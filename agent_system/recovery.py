from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .models import TaskStatus
from .orchestrator import Orchestrator


class RecoveryError(RuntimeError):
    pass


@dataclass(slots=True)
class RecoveryPlan:
    task_id: str
    current_status: str
    target_status: str
    reason: str


_TRANSIENT = {
    TaskStatus.CONTEXT_READY.value,
    TaskStatus.IMPLEMENTING.value,
    TaskStatus.IMPLEMENTED.value,
    TaskStatus.VERIFYING.value,
    TaskStatus.REVIEWING.value,
    TaskStatus.REPAIRING.value,
}


class RecoveryManager:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.orchestrator = Orchestrator(self.root)

    def plan(self, task_id: str) -> RecoveryPlan:
        state = self.orchestrator.state.get_task(task_id)
        if not state:
            raise RecoveryError(f"Unknown task: {task_id}")
        current = state["status"]
        if current not in _TRANSIENT:
            return RecoveryPlan(task_id, current, current, "task is not in an interrupted transient state")
        lock = self.root / ".agent-system" / "locks" / f"{task_id}.lock"
        if lock.exists():
            raise RecoveryError(f"Task {task_id} still has an active/unknown runtime lease; refusing recovery")

        run_dir = self.root / "runs" / task_id
        verification = self._read_json(run_dir / "verification.json")
        review = self._read_json(run_dir / "review.json")
        implementation = self._read_json(run_dir / "implementation.json")

        if verification.get("overall_status") == "PASS":
            verdict = review.get("verdict")
            if verdict in {"PASS", "PASS_WITH_NOTES"}:
                return RecoveryPlan(task_id, current, TaskStatus.READY_FOR_REVIEW.value, "persisted verification and review both passed")
            if verdict == "NEEDS_HUMAN_DECISION":
                return RecoveryPlan(task_id, current, TaskStatus.WAITING_HUMAN.value, "persisted review requires human decision")
            return RecoveryPlan(task_id, current, TaskStatus.REVIEWING.value, "verification passed; review must be rerun")
        if implementation:
            return RecoveryPlan(task_id, current, TaskStatus.IMPLEMENTED.value, "implementation artifact exists; verification must be rerun")

        worktree = Path(state.get("worktree") or "")
        if worktree.is_dir():
            proc = subprocess.run(["git", "-C", str(worktree), "status", "--porcelain"], capture_output=True, text=True)
            if proc.returncode == 0 and proc.stdout.strip():
                return RecoveryPlan(task_id, current, TaskStatus.FAILED.value, "partial unverified worktree changes require manual inspection")
        return RecoveryPlan(task_id, current, TaskStatus.PLANNED.value, "no persisted implementation; safe to restart from planning state")

    def apply(self, task_id: str) -> RecoveryPlan:
        plan = self.plan(task_id)
        if plan.target_status == plan.current_status:
            return plan
        self.orchestrator.state.update_task(task_id, status=plan.target_status)
        state = self.orchestrator.state.get_task(task_id)
        self.orchestrator.state.record_event(
            task_id,
            plan.current_status,
            plan.target_status,
            state.get("provider_id") if state else None,
            "recovery",
            plan.reason,
        )
        return plan

    @staticmethod
    def _read_json(path: Path) -> dict:
        try:
            value = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}


def main() -> None:
    parser = argparse.ArgumentParser(prog="agent-recover", description="Plan or apply conservative recovery for an interrupted task")
    parser.add_argument("task_id")
    parser.add_argument("--apply", action="store_true", help="Apply the proposed state transition; default is dry-run")
    args = parser.parse_args()
    manager = RecoveryManager(Path.cwd())
    try:
        plan = manager.apply(args.task_id) if args.apply else manager.plan(args.task_id)
    except RecoveryError as exc:
        raise SystemExit(str(exc))
    print(f"{plan.task_id}: {plan.current_status} -> {plan.target_status}")
    print(plan.reason)
    if not args.apply and plan.target_status != plan.current_status:
        print("Dry run only. Re-run with --apply to persist this recovery transition.")


if __name__ == "__main__":
    main()
