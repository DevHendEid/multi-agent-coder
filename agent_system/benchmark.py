from __future__ import annotations

import json
import sqlite3
import subprocess
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .providers import ModelProvider, ProviderAvailability


class BenchmarkStatus(str):
    UNBENCHMARKED = "UNBENCHMARKED"
    CANDIDATE = "CANDIDATE"
    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"


@dataclass(slots=True)
class BenchmarkCaseResult:
    case_id: str
    role: str
    passed: bool
    duration_ms: int
    error: str | None = None
    details: dict[str, Any] | None = None


@dataclass(slots=True)
class BenchmarkSummary:
    run_id: str
    provider_id: str
    model_name: str
    role: str
    total_cases: int
    passed_cases: int
    pass_rate: float
    average_duration_ms: float
    status: str
    created_at: str


class BenchmarkRegistry:
    """Persistent, model-specific benchmark history and admission state."""

    def __init__(self, db_path: str | Path, *, validation_runs: int = 2, required_pass_rate: float = 1.0, reject_below: float = 0.5):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.validation_runs = max(1, int(validation_runs))
        self.required_pass_rate = float(required_pass_rate)
        self.reject_below = float(reject_below)
        self._init_db()

    def _connect(self):
        con = sqlite3.connect(self.db_path, timeout=30.0)
        con.execute("PRAGMA busy_timeout=30000")
        return con

    def _init_db(self) -> None:
        with self._connect() as con:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("""
                CREATE TABLE IF NOT EXISTS benchmark_runs (
                    run_id TEXT PRIMARY KEY,
                    provider_id TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    total_cases INTEGER NOT NULL,
                    passed_cases INTEGER NOT NULL,
                    pass_rate REAL NOT NULL,
                    average_duration_ms REAL NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            con.execute("""
                CREATE TABLE IF NOT EXISTS benchmark_cases (
                    run_id TEXT NOT NULL,
                    case_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    passed INTEGER NOT NULL,
                    duration_ms INTEGER NOT NULL,
                    error TEXT,
                    details_json TEXT NOT NULL,
                    PRIMARY KEY(run_id, case_id),
                    FOREIGN KEY(run_id) REFERENCES benchmark_runs(run_id) ON DELETE CASCADE
                )
            """)
            con.execute("CREATE INDEX IF NOT EXISTS idx_benchmark_model ON benchmark_runs(provider_id, model_name, created_at DESC)")

    def record(self, provider_id: str, model_name: str, role: str, results: list[BenchmarkCaseResult]) -> BenchmarkSummary:
        if not results:
            raise ValueError("Benchmark run must contain at least one case")
        run_id = f"BENCH-{uuid.uuid4().hex[:12]}"
        passed = sum(1 for result in results if result.passed)
        total = len(results)
        pass_rate = passed / total
        average_duration = sum(result.duration_ms for result in results) / total
        created_at = datetime.now(timezone.utc).isoformat()
        status = self._status_for(provider_id, model_name, pass_rate)
        summary = BenchmarkSummary(run_id, provider_id, model_name, role, total, passed, pass_rate, average_duration, status, created_at)
        with self._connect() as con:
            con.execute(
                "INSERT INTO benchmark_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, provider_id, model_name, role, total, passed, pass_rate, average_duration, status, created_at),
            )
            for result in results:
                con.execute(
                    "INSERT INTO benchmark_cases VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (run_id, result.case_id, result.role, int(result.passed), result.duration_ms, result.error, json.dumps(result.details or {}, sort_keys=True)),
                )
        return summary

    def _status_for(self, provider_id: str, model_name: str, pass_rate: float) -> str:
        if pass_rate < self.reject_below:
            return BenchmarkStatus.REJECTED
        if pass_rate < self.required_pass_rate:
            return BenchmarkStatus.CANDIDATE
        previous = self.history(provider_id, model_name, limit=max(0, self.validation_runs - 1))
        consecutive_passes = 1
        for run in previous:
            if run.pass_rate >= self.required_pass_rate:
                consecutive_passes += 1
            else:
                break
        return BenchmarkStatus.VALIDATED if consecutive_passes >= self.validation_runs else BenchmarkStatus.CANDIDATE

    def latest(self, provider_id: str, model_name: str | None = None) -> BenchmarkSummary | None:
        query = "SELECT * FROM benchmark_runs WHERE provider_id = ?"
        params: list[Any] = [provider_id]
        if model_name is not None:
            query += " AND model_name = ?"
            params.append(model_name)
        query += " ORDER BY created_at DESC LIMIT 1"
        with self._connect() as con:
            con.row_factory = sqlite3.Row
            row = con.execute(query, params).fetchone()
        return self._row_to_summary(row) if row else None

    def history(self, provider_id: str, model_name: str | None = None, limit: int = 20) -> list[BenchmarkSummary]:
        if limit <= 0:
            return []
        query = "SELECT * FROM benchmark_runs WHERE provider_id = ?"
        params: list[Any] = [provider_id]
        if model_name is not None:
            query += " AND model_name = ?"
            params.append(model_name)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(query, params).fetchall()
        return [self._row_to_summary(row) for row in rows]

    def status(self, provider_id: str, model_name: str) -> str:
        latest = self.latest(provider_id, model_name)
        return latest.status if latest else BenchmarkStatus.UNBENCHMARKED

    def latest_all(self) -> list[BenchmarkSummary]:
        with self._connect() as con:
            con.row_factory = sqlite3.Row
            rows = con.execute("""
                SELECT r.* FROM benchmark_runs r
                JOIN (
                    SELECT provider_id, model_name, MAX(created_at) AS max_created
                    FROM benchmark_runs GROUP BY provider_id, model_name
                ) x ON r.provider_id=x.provider_id AND r.model_name=x.model_name AND r.created_at=x.max_created
                ORDER BY r.provider_id, r.model_name
            """).fetchall()
        return [self._row_to_summary(row) for row in rows]

    @staticmethod
    def _row_to_summary(row: sqlite3.Row) -> BenchmarkSummary:
        return BenchmarkSummary(
            run_id=row["run_id"], provider_id=row["provider_id"], model_name=row["model_name"], role=row["role"],
            total_cases=row["total_cases"], passed_cases=row["passed_cases"], pass_rate=row["pass_rate"],
            average_duration_ms=row["average_duration_ms"], status=row["status"], created_at=row["created_at"],
        )


class BenchmarkRunner:
    """Run deterministic acceptance cases against an available provider.

    Editing providers are tested in disposable git repositories. Read-only
    providers are tested for structured review judgments. No benchmark touches
    the user's project repository.
    """

    def __init__(self, registry: BenchmarkRegistry):
        self.registry = registry

    def run(self, provider: ModelProvider) -> BenchmarkSummary:
        if provider.availability() != ProviderAvailability.AVAILABLE:
            raise RuntimeError(f"Provider {provider.provider_id} is not available")
        metadata = provider.get_metadata()
        role = "implementer" if provider.capabilities().supports_file_editing else "reviewer"
        results = self._run_implementer(provider) if role == "implementer" else self._run_reviewer(provider)
        return self.registry.record(provider.provider_id, metadata.model_name, role, results)

    def _run_implementer(self, provider: ModelProvider) -> list[BenchmarkCaseResult]:
        cases = [
            ("python-add", "Fix calc.add so add(2, 3) returns 5. Modify only calc.py.", {"calc.py": "def add(a, b):\n    return a - b\n"}, {"calc.py": "def add(a, b):\n    return a + b\n"}),
            ("docs-line", "Append exactly one line 'Benchmark passed.' to README.md. Modify only README.md.", {"README.md": "# Demo\n"}, {"README.md": "# Demo\nBenchmark passed.\n"}),
        ]
        return [self._editing_case(provider, *case) for case in cases]

    def _editing_case(self, provider: ModelProvider, case_id: str, goal: str, initial: dict[str, str], expected: dict[str, str]) -> BenchmarkCaseResult:
        start = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix="agent-benchmark-") as tmp:
            repo = Path(tmp)
            self._init_repo(repo, initial)
            selected_files = [{"path": path, "size": len(content.encode()), "content": content} for path, content in initial.items()]
            task = {"id": f"BENCH-{case_id}", "goal": goal, "risk": "low", "allowed_scope": list(initial), "forbidden_scope": [".git"]}
            context = {"selected_files": selected_files, "repo": {"root": str(repo)}, "task": task}
            result = provider.execute(task, context, repo)
            passed = bool(result.success) and all((repo / path).read_text() == content for path, content in expected.items())
            tracked = subprocess.run(["git", "status", "--short"], cwd=repo, capture_output=True, text=True).stdout
            changed = [line[3:] for line in tracked.splitlines() if len(line) >= 4]
            allowed = set(expected)
            passed = passed and all(path in allowed for path in changed)
            error = result.error if not result.success else (None if passed else "Output did not match deterministic expectation")
            duration = int((time.perf_counter() - start) * 1000)
            return BenchmarkCaseResult(case_id, "implementer", passed, duration, error, {"modified_files": result.modified_files or [], "changed": changed})

    def _run_reviewer(self, provider: ModelProvider) -> list[BenchmarkCaseResult]:
        clean = self._review_case(provider, "clean-pass", "PASS", {"overall_status": "PASS", "command_results": []})
        failing = self._review_case(provider, "test-failure", "FAIL", {"overall_status": "FAIL", "command_results": [{"command": "pytest", "status": "FAIL", "stderr": "AssertionError"}]})
        return [clean, failing]

    def _review_case(self, provider: ModelProvider, case_id: str, expectation: str, verification: dict[str, Any]) -> BenchmarkCaseResult:
        start = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix="agent-review-benchmark-") as tmp:
            repo = Path(tmp); self._init_repo(repo, {"demo.py": "VALUE = 1\n"})
            task = {"id": f"BENCH-{case_id}", "goal": "Review the supplied change", "risk": "low", "diff": "diff --git a/demo.py b/demo.py\n+VALUE = 2\n"}
            result = provider.execute(task, {"verification_report": verification, "context_summary": {}}, repo)
            review = (result.metadata or {}).get("review", {}) if result.success else {}
            verdict = review.get("verdict") if isinstance(review, dict) else None
            if expectation == "PASS":
                passed = verdict in {"PASS", "PASS_WITH_NOTES"}
            else:
                passed = isinstance(verdict, str) and verdict.startswith("FAIL_")
            duration = int((time.perf_counter() - start) * 1000)
            return BenchmarkCaseResult(case_id, "reviewer", passed, duration, result.error if not result.success else (None if passed else f"Unexpected verdict: {verdict}"), {"verdict": verdict})

    @staticmethod
    def _init_repo(repo: Path, files: dict[str, str]) -> None:
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "benchmark@example.invalid"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Agent Benchmark"], cwd=repo, check=True)
        for path, content in files.items():
            target = repo / path; target.parent.mkdir(parents=True, exist_ok=True); target.write_text(content)
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "benchmark baseline"], cwd=repo, check=True)
