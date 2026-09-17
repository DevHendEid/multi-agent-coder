from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import subprocess
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
        checks.extend(self._integration_checks())
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

    def _integration_checks(self) -> list[CheckResult]:
        out: list[CheckResult] = []
        receipts_doc = self.root / "docs" / "project" / "INTEGRATION_RECEIPTS.md"
        receipts_text = receipts_doc.read_text() if receipts_doc.exists() else ""

        # Direct-main commit check
        try:
            target_ref = "main"
            chk = subprocess.run(["git", "rev-parse", "--verify", "main"], cwd=str(self.root), capture_output=True)
            if chk.returncode != 0:
                chk_origin = subprocess.run(["git", "rev-parse", "--verify", "origin/main"], cwd=str(self.root), capture_output=True)
                if chk_origin.returncode == 0:
                    target_ref = "origin/main"
                else:
                    target_ref = "HEAD"

            log_proc = subprocess.run(
                ["git", "log", "--first-parent", "-n", "20", "--format=%H %h %p", target_ref],
                cwd=str(self.root),
                capture_output=True,
                text=True,
            )
            if log_proc.returncode == 0:
                unrecorded_direct_commits = []
                for line in log_proc.stdout.strip().splitlines():
                    if not line:
                        continue
                    parts = line.split()
                    full_sha = parts[0]
                    short_sha = parts[1]
                    parents = parts[2:]
                    # Direct non-merge commit on main has <= 1 parent
                    if len(parents) <= 1:
                        if short_sha not in receipts_text and full_sha not in receipts_text:
                            unrecorded_direct_commits.append(short_sha)
                if unrecorded_direct_commits:
                    out.append(CheckResult("integration:direct_main_commits", "FAIL", f"unrecorded direct-main commits: {', '.join(unrecorded_direct_commits)}"))
                else:
                    out.append(CheckResult("integration:direct_main_commits", "PASS", "all direct-main commits have receipts or historical exceptions"))
            else:
                out.append(CheckResult("integration:direct_main_commits", "WARN", f"git log failed: {log_proc.stderr.strip()}"))
        except Exception as exc:
            out.append(CheckResult("integration:direct_main_commits", "WARN", f"unable to inspect git log: {exc}"))

        # Stale detached worktree check
        try:
            wt_proc = subprocess.run(["git", "worktree", "list", "--porcelain"], cwd=str(self.root), capture_output=True, text=True)
            if wt_proc.returncode == 0:
                stale_wts = []
                current_wt = None
                for line in wt_proc.stdout.splitlines():
                    if line.startswith("worktree "):
                        current_wt = line.split(" ", 1)[1]
                    elif line == "detached":
                        if current_wt and current_wt != str(self.root):
                            stale_wts.append(current_wt)
                if stale_wts:
                    out.append(CheckResult("integration:stale_worktrees", "FAIL", f"stale detached worktrees: {', '.join(stale_wts)}"))
                else:
                    out.append(CheckResult("integration:stale_worktrees", "PASS", "zero stale detached worktrees"))
        except Exception as exc:
            out.append(CheckResult("integration:stale_worktrees", "WARN", f"unable to inspect worktrees: {exc}"))

        path = self.root / ".agent-system" / "state.db"
        if not path.exists():
            return out

        try:
            orch = Orchestrator(self.root)
            rows = orch.state.list_tasks()
        except Exception as exc:
            out.append(CheckResult("integration:tasks", "FAIL", str(exc)))
            return out

        missing_receipts = []
        unmerged_completed = []
        unresolved_ready_for_pr = []
        for r in rows:
            task_id = r["id"]
            status = r.get("status")
            prod_target = r.get("production_target", 1)
            if status == "COMPLETED" and prod_target:
                receipt_file = self.root / "runs" / task_id / "integration_receipt.json"
                if not receipt_file.exists():
                    missing_receipts.append(task_id)
                merge_sha = r.get("merge_sha")
                if not merge_sha:
                    unmerged_completed.append(task_id)
                else:
                    chk = subprocess.run(
                        ["git", "merge-base", "--is-ancestor", merge_sha, "main"],
                        cwd=str(self.root),
                        capture_output=True,
                        text=True,
                    )
                    if chk.returncode != 0:
                        unmerged_completed.append(f"{task_id} (SHA {merge_sha[:7]} unreachable)")
            elif status == "READY_FOR_PR":
                if not r.get("pr_number") and not r.get("pr_url") and not r.get("blocker"):
                    unresolved_ready_for_pr.append(task_id)

        if missing_receipts:
            out.append(CheckResult("integration:completed_receipts", "FAIL", f"tasks missing integration receipts: {', '.join(missing_receipts)}"))
        else:
            out.append(CheckResult("integration:completed_receipts", "PASS", "all completed production tasks have receipts"))

        if unmerged_completed:
            out.append(CheckResult("integration:completed_merged", "FAIL", f"completed tasks not verified on main: {', '.join(unmerged_completed)}"))
        else:
            out.append(CheckResult("integration:completed_merged", "PASS", "all completed tasks confirmed on main"))

        if unresolved_ready_for_pr:
            out.append(CheckResult("integration:ready_for_pr", "WARN", f"READY_FOR_PR tasks without PR or blocker: {', '.join(unresolved_ready_for_pr)}"))
        else:
            out.append(CheckResult("integration:ready_for_pr", "PASS", "no unhandled READY_FOR_PR tasks"))

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
