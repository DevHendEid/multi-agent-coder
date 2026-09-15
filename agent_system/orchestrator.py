from __future__ import annotations

from pathlib import Path
from .contracts import write_contract
from .models import TaskContract, TaskStatus
from .project import ProjectConfig
from .risk import classify_risk
from .state import StateStore
from .worktree import WorktreeManager


class Orchestrator:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.state = StateStore(self.root / ".agent-system" / "state.db")

    def create_task(self, project_file: str | Path, goal: str) -> dict:
        project = ProjectConfig.load(project_file)
        task_id = self.state.next_task_id()
        risk = classify_risk(goal)

        contract = TaskContract(
            id=task_id,
            goal=goal,
            project=project.name,
            risk=risk,
            allowed_scope=[],
            forbidden_scope=project.protected_paths,
            acceptance=[
                "Project-defined static analysis passes if configured",
                "Project-defined tests pass if configured",
                "No unrelated file changes",
                "Human approval required before merge",
            ],
        )
        self.state.create_task(task_id, project.name, goal, risk)
        contract_path = write_contract(contract, self.root / "tasks")

        manager = WorktreeManager(project.repo_path)
        branch, worktree = manager.create(task_id)
        self.state.update_task(
            task_id,
            status=TaskStatus.PLANNED.value,
            branch=branch,
            worktree=str(worktree),
        )

        run_dir = self.root / "runs" / task_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "README.txt").write_text(
            f"Task {task_id}\nGoal: {goal}\nRisk: {risk}\nBranch: {branch}\nWorktree: {worktree}\n"
        )

        return {
            "task_id": task_id,
            "risk": risk,
            "contract": str(contract_path),
            "branch": branch,
            "worktree": str(worktree),
            "status": TaskStatus.PLANNED.value,
        }
