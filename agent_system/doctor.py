from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

from .orchestrator import Orchestrator
from .providers import ProviderRegistry


@dataclass(slots=True)
class CheckResult:
    name: str
    status: str
    detail: str


class Doctor:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def run(self) -> list[CheckResult]:
        checks: list[CheckResult] = []
        checks.append(self._binary("git", required=True))
        checks.append(self._binary("gh", required=False))
        checks.extend(self._config_checks())
        checks.append(self._state_db_check())
        checks.extend(self._task_checks())
        return checks

    @staticmethod
    def _binary(name: str, *, required: bool) -> CheckResult:
        path = shutil.which(name)
        if path:
            return CheckResult(f"binary:{name}", "PASS", path)
        return CheckResult(f"binary:{name}", "FAIL" if required else "WARN", "not found")

    def _config_checks(self) -> list[CheckResult]:
        out: list[CheckResult] = []
        models = self.root / "config" / "models.yaml"
        routing = self.root / "config" / "routing.yaml"
        try:
            registry = ProviderRegistry(models)
            provider_ids = {p.provider_id for p in registry.list_all()}
            out.append(CheckResult("config:models", "PASS", f"{len(provider_ids)} providers loaded"))
        except Exception as exc:
            return [CheckResult("config:models", "FAIL", str(exc))]

        try:
            data = yaml.safe_load(routing.read_text()) or {}
            unknown: list[str] = []
            for risk, ids in data.items():
                if not isinstance(ids, list):
                    out.append(CheckResult("config:routing", "FAIL", f"{risk} must be a list"))
                    return out
                unknown.extend(str(pid) for pid in ids if str(pid) not in provider_ids)
            if unknown:
                out.append(CheckResult("config:routing", "FAIL", "unknown providers: " + ", ".join(sorted(set(unknown)))))
            else:
                out.append(CheckResult("config:routing", "PASS", "all routing providers are configured"))
        except Exception as exc:
            out.append(CheckResult("config:routing", "FAIL", str(exc)))

        benchmarks = self.root / "config" / "benchmarks.yaml"
        try:
            if benchmarks.exists():
                value = yaml.safe_load(benchmarks.read_text()) or {}
                if not isinstance(value, dict):
                    raise ValueError("benchmark config must be a mapping")
            out.append(CheckResult("config:benchmarks", "PASS", "valid YAML"))
        except Exception as exc:
            out.append(CheckResult("config:benchmarks", "FAIL", str(exc)))
        return out

    def _state_db_check(self) -> CheckResult:
        path = self.root / ".agent-system" / "state.db"
        if not path.exists():
            return CheckResult("state:integrity", "WARN", "state database not created yet")
        try:
            con = sqlite3.connect(path, timeout=5.0)
            try:
                result = con.execute("PRAGMA integrity_check").fetchone()
            finally:
                con.close()
            detail = str(result[0]) if result else "no result"
            return CheckResult("state:integrity", "PASS" if detail.lower() == "ok" else "FAIL", detail)
        except Exception as exc:
            return CheckResult("state:integrity", "FAIL", str(exc))

    def _task_checks(self) -> list[CheckResult]:
        path = self.root / ".agent-system" / "state.db"
        if not path.exists():
            return []
        try:
            orch = Orchestrator(self.root)
            rows = orch.state.list_tasks()
        except Exception as exc:
            return [CheckResult("tasks:load", "FAIL", str(exc))]
        missing = []
        transient_without_lock = []
        transient = {"CONTEXT_READY", "IMPLEMENTING", "IMPLEMENTED", "VERIFYING", "REVIEWING", "REPAIRING"}
        for row in rows:
            worktree = row.get("worktree")
            if worktree and row.get("status") not in {"COMPLETED", "FAILED"} and not Path(worktree).is_dir():
                missing.append(row["id"])
            if row.get("status") in transient:
                lock = self.root / ".agent-system" / "locks" / f"{row['id']}.lock"
                if not lock.exists():
                    transient_without_lock.append(row["id"])
        out = [
            CheckResult("tasks:worktrees", "FAIL" if missing else "PASS", ", ".join(missing) if missing else "all active worktrees exist"),
        ]
        if transient_without_lock:
            out.append(CheckResult("tasks:orphaned", "WARN", "possible interrupted tasks: " + ", ".join(transient_without_lock)))
        else:
            out.append(CheckResult("tasks:orphaned", "PASS", "no interrupted transient tasks detected"))
        return out


def main() -> None:
    parser = argparse.ArgumentParser(prog="agent-doctor", description="Check Multi-Agent Coder production readiness")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    checks = Doctor(Path.cwd()).run()
    if args.as_json:
        print(json.dumps([asdict(item) for item in checks], indent=2))
    else:
        for item in checks:
            print(f"{item.status:<4} {item.name}: {item.detail}")
    if any(item.status == "FAIL" for item in checks):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
