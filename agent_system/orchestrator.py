from __future__ import annotations

from pathlib import Path
from .contracts import write_contract
from .dag import DagScheduler
from .models import TaskContract, TaskStatus
from .project import ProjectConfig
from .risk import classify_risk
from .state import StateStore
from .worktree import WorktreeManager


class Orchestrator:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.state = StateStore(self.root / ".agent-system" / "state.db")

    def _load_project(self, project_name: str) -> ProjectConfig:
        project_path = self.root / "projects" / f"{project_name}.yaml"
        return ProjectConfig.load(project_path)

    def create_task(self, project_file: str | Path, goal: str, dependencies: list[str] | None = None) -> dict:
        project = ProjectConfig.load(project_file)
        dependencies = dependencies or []
        for dep in dependencies:
            if not self.state.get_task(dep):
                raise ValueError(f"Unknown dependency task: {dep}")

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
        self.state.create_task(
            task_id,
            project.name,
            goal,
            risk,
            max_free_attempts=contract.max_free_attempts,
            max_total_repair_rounds=contract.max_free_attempts + 1,
        )
        DagScheduler(self.state).add_dependencies(task_id, dependencies)
        contract_path = write_contract(contract, self.root / "tasks")

        manager = WorktreeManager(project.repo_path)
        branch, worktree = manager.create(task_id)
        status = TaskStatus.WAITING_DEPENDENCY.value if dependencies else TaskStatus.PLANNED.value
        self.state.update_task(
            task_id,
            status=status,
            branch=branch,
            worktree=str(worktree),
        )

        run_dir = self.root / "runs" / task_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "README.txt").write_text(
            f"Task {task_id}\nGoal: {goal}\nRisk: {risk}\nBranch: {branch}\nWorktree: {worktree}\nDependencies: {', '.join(dependencies) or 'none'}\n"
        )

        return {
            "task_id": task_id,
            "risk": risk,
            "contract": str(contract_path),
            "branch": branch,
            "worktree": str(worktree),
            "dependencies": dependencies,
            "status": status,
        }

    def get_task_state(self, task_id: str) -> dict | None:
        return self.state.get_task(task_id)

    def list_tasks(self) -> list[dict]:
        return self.state.list_tasks()
