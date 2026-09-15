from __future__ import annotations

import json
import shlex
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .project import ProjectConfig
from .scope import ScopeGate


class VerificationStatus(str):
    PASS = "PASS"
    FAIL = "FAIL"
    SKIPPED = "SKIPPED"
    TIMEOUT = "TIMEOUT"
    ERROR = "ERROR"


@dataclass(slots=True)
class VerificationResult:
    command: str
    cwd: str
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int
    timeout: bool
    status: str


@dataclass(slots=True)
class VerificationReport:
    task_id: str
    risk: str
    scope_gate: VerificationResult | None
    diff_check: VerificationResult | None
    command_results: list[VerificationResult]
    overall_status: str
    created_at: str


class Verifier:
    def __init__(self, project: ProjectConfig, worktree: Path, original_repo: Path):
        self.project = project
        self.worktree = worktree
        self.original_repo = original_repo
        self.scope_gate = ScopeGate(worktree=worktree, allowed_scope=[], forbidden_scope=project.protected_paths, original_repo=original_repo)

    def run(self, task_id: str, risk: str, allowed_scope: list[str]) -> VerificationReport:
        self.scope_gate.allowed_scope = allowed_scope
        self.scope_gate.forbidden_scope = self.project.protected_paths
        results = []
        scope_result = self._run_scope_gate()
        results.append(scope_result)
        diff_result = self._run_diff_check()
        results.append(diff_result)
        for cmd_name, cmd in self.project.get_verification_commands(risk):
            results.append(self._run_command(cmd_name, cmd))
        overall_status = self._compute_overall_status(results)
        return VerificationReport(task_id, risk, scope_result, diff_result, [r for r in results if r not in (scope_result, diff_result)], overall_status, datetime.now(timezone.utc).isoformat())

    def _run_scope_gate(self) -> VerificationResult:
        start = time.perf_counter()
        scope_result = self.scope_gate.check()
        duration_ms = int((time.perf_counter() - start) * 1000)
        status = VerificationStatus.PASS if scope_result.passed else VerificationStatus.FAIL
        details = "; ".join(f"{v.path}: {v.reason}" for v in scope_result.violations) if scope_result.violations else "All files within scope"
        return VerificationResult("scope_gate", str(self.worktree), 0 if scope_result.passed else 1, details, "", duration_ms, False, status)

    def _run_diff_check(self) -> VerificationResult:
        start = time.perf_counter()
        passed, output = self.scope_gate.diff_check()
        duration_ms = int((time.perf_counter() - start) * 1000)
        return VerificationResult("git diff --check", str(self.worktree), 0 if passed else 1, output if output else "No whitespace errors", "", duration_ms, False, VerificationStatus.PASS if passed else VerificationStatus.FAIL)

    def _run_command(self, name: str, command: str) -> VerificationResult:
        timeout_seconds = self.project.get_timeout(name)
        start = time.perf_counter()
        timed_out = False
        exit_code = None
        stdout = ""
        stderr = ""
        try:
            argv = shlex.split(command)
            result = subprocess.run(argv, cwd=str(self.worktree), capture_output=True, text=True, timeout=timeout_seconds)
            exit_code = result.returncode
            stdout = result.stdout
            stderr = result.stderr
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            exit_code = -1
            stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = f"Command timed out after {timeout_seconds}s"
        except Exception as exc:
            exit_code = -1
            stderr = str(exc)
        duration_ms = int((time.perf_counter() - start) * 1000)
        if timed_out:
            status = VerificationStatus.TIMEOUT
        elif exit_code == 0:
            status = VerificationStatus.PASS
        elif exit_code is None:
            status = VerificationStatus.ERROR
        else:
            status = VerificationStatus.FAIL
        return VerificationResult(command, str(self.worktree), exit_code, stdout, stderr, duration_ms, timed_out, status)

    def _compute_overall_status(self, results: list[VerificationResult]) -> str:
        return VerificationStatus.FAIL if any(r.status in (VerificationStatus.FAIL, VerificationStatus.TIMEOUT, VerificationStatus.ERROR) for r in results) else VerificationStatus.PASS

    def persist(self, report: VerificationReport, run_dir: Path) -> Path:
        run_dir.mkdir(parents=True, exist_ok=True)
        out_path = run_dir / "verification.json"
        data = {
            "task_id": report.task_id,
            "risk": report.risk,
            "scope_gate": asdict(report.scope_gate) if report.scope_gate else None,
            "diff_check": asdict(report.diff_check) if report.diff_check else None,
            "command_results": [asdict(r) for r in report.command_results],
            "overall_status": report.overall_status,
            "created_at": report.created_at,
        }
        out_path.write_text(json.dumps(data, indent=2))
        return out_path
