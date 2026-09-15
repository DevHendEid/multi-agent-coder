from __future__ import annotations

from dataclasses import dataclass

from .models import TaskStatus
from .state import StateStore


SATISFIED_STATUSES = {
    TaskStatus.READY_FOR_REVIEW.value,
    TaskStatus.PR_READY.value,
    TaskStatus.COMPLETED.value,
}
FAILED_STATUSES = {TaskStatus.FAILED.value}


@dataclass(slots=True)
class DependencyState:
    task_id: str
    dependencies: list[str]
    pending: list[str]
    failed: list[str]

    @property
    def ready(self) -> bool:
        return not self.pending and not self.failed


class DagError(ValueError):
    pass


class DagScheduler:
    """Persistent task dependency graph with cycle and readiness checks."""

    def __init__(self, state: StateStore):
        self.state = state

    def add_dependency(self, task_id: str, depends_on: str) -> None:
        if task_id == depends_on:
            raise DagError("A task cannot depend on itself")
        if not self.state.get_task(task_id):
            raise DagError(f"Unknown task: {task_id}")
        if not self.state.get_task(depends_on):
            raise DagError(f"Unknown dependency task: {depends_on}")
        if self._reachable(depends_on, task_id):
            raise DagError(f"Dependency would create a cycle: {task_id} -> {depends_on}")
        self.state.add_dependency(task_id, depends_on)

    def add_dependencies(self, task_id: str, dependencies: list[str]) -> None:
        added: list[str] = []
        try:
            for dep in dependencies:
                if dep in added or dep in self.state.get_dependencies(task_id):
                    continue
                self.add_dependency(task_id, dep)
                added.append(dep)
        except Exception:
            for dep in added:
                self.state.remove_dependency(task_id, dep)
            raise

    def dependency_state(self, task_id: str) -> DependencyState:
        if not self.state.get_task(task_id):
            raise DagError(f"Unknown task: {task_id}")
        deps = self.state.get_dependencies(task_id)
        pending: list[str] = []
        failed: list[str] = []
        for dep in deps:
            row = self.state.get_task(dep)
            if not row or row["status"] in FAILED_STATUSES:
                failed.append(dep)
            elif row["status"] not in SATISFIED_STATUSES:
                pending.append(dep)
        return DependencyState(task_id, deps, pending, failed)

    def assert_runnable(self, task_id: str) -> None:
        dep_state = self.dependency_state(task_id)
        if dep_state.failed:
            raise DagError(
                f"Task {task_id} has failed dependencies: {', '.join(dep_state.failed)}"
            )
        if dep_state.pending:
            raise DagError(
                f"Task {task_id} is waiting for dependencies: {', '.join(dep_state.pending)}"
            )

    def ready_tasks(self, task_ids: list[str] | None = None) -> list[str]:
        rows = self.state.list_tasks()
        allowed = set(task_ids) if task_ids is not None else None
        ready: list[str] = []
        for row in reversed(rows):
            task_id = row["id"]
            if allowed is not None and task_id not in allowed:
                continue
            if row["status"] not in {
                TaskStatus.NEW.value,
                TaskStatus.PLANNED.value,
                TaskStatus.CONTEXT_READY.value,
                TaskStatus.WAITING_DEPENDENCY.value,
            }:
                continue
            if self.dependency_state(task_id).ready:
                ready.append(task_id)
        return ready

    def graph(self) -> dict[str, list[str]]:
        return {row["id"]: self.state.get_dependencies(row["id"]) for row in self.state.list_tasks()}

    def _reachable(self, start: str, target: str) -> bool:
        stack = [start]
        seen: set[str] = set()
        while stack:
            node = stack.pop()
            if node == target:
                return True
            if node in seen:
                continue
            seen.add(node)
            stack.extend(self.state.get_dependencies(node))
        return False
