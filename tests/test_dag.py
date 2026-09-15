from pathlib import Path

import pytest

from agent_system.dag import DagError, DagScheduler
from agent_system.models import TaskStatus
from agent_system.state import StateStore


def make_state(tmp_path: Path) -> StateStore:
    return StateStore(tmp_path / "state.db")


def add_task(state: StateStore, task_id: str, status: str = TaskStatus.PLANNED.value) -> None:
    state.create_task(task_id, "demo", task_id, "low")
    state.update_task(task_id, status=status, branch=f"agent/{task_id.lower()}", worktree=str(state.db_path.parent / task_id))


def test_dependency_waits_until_parent_is_ready(tmp_path):
    state = make_state(tmp_path)
    add_task(state, "TASK-0001")
    add_task(state, "TASK-0002")
    dag = DagScheduler(state)
    dag.add_dependency("TASK-0002", "TASK-0001")

    dep = dag.dependency_state("TASK-0002")
    assert dep.ready is False
    assert dep.pending == ["TASK-0001"]
    with pytest.raises(DagError, match="waiting for dependencies"):
        dag.assert_runnable("TASK-0002")

    state.update_task("TASK-0001", status=TaskStatus.READY_FOR_REVIEW.value)
    assert dag.dependency_state("TASK-0002").ready is True
    dag.assert_runnable("TASK-0002")


def test_failed_dependency_blocks_child(tmp_path):
    state = make_state(tmp_path)
    add_task(state, "TASK-0001", TaskStatus.FAILED.value)
    add_task(state, "TASK-0002")
    dag = DagScheduler(state)
    dag.add_dependency("TASK-0002", "TASK-0001")
    assert dag.dependency_state("TASK-0002").failed == ["TASK-0001"]
    with pytest.raises(DagError, match="failed dependencies"):
        dag.assert_runnable("TASK-0002")


def test_cycle_is_rejected_without_partial_edge(tmp_path):
    state = make_state(tmp_path)
    for task_id in ("TASK-0001", "TASK-0002", "TASK-0003"):
        add_task(state, task_id)
    dag = DagScheduler(state)
    dag.add_dependency("TASK-0002", "TASK-0001")
    dag.add_dependency("TASK-0003", "TASK-0002")
    with pytest.raises(DagError, match="cycle"):
        dag.add_dependency("TASK-0001", "TASK-0003")
    assert state.get_dependencies("TASK-0001") == []


def test_self_dependency_and_unknown_dependency_rejected(tmp_path):
    state = make_state(tmp_path)
    add_task(state, "TASK-0001")
    dag = DagScheduler(state)
    with pytest.raises(DagError, match="itself"):
        dag.add_dependency("TASK-0001", "TASK-0001")
    with pytest.raises(DagError, match="Unknown dependency"):
        dag.add_dependency("TASK-0001", "TASK-9999")


def test_ready_tasks_respects_graph(tmp_path):
    state = make_state(tmp_path)
    add_task(state, "TASK-0001", TaskStatus.READY_FOR_REVIEW.value)
    add_task(state, "TASK-0002", TaskStatus.WAITING_DEPENDENCY.value)
    add_task(state, "TASK-0003", TaskStatus.PLANNED.value)
    dag = DagScheduler(state)
    dag.add_dependency("TASK-0002", "TASK-0001")
    dag.add_dependency("TASK-0003", "TASK-0002")
    assert dag.ready_tasks() == ["TASK-0002"]


def test_dependency_storage_is_persistent(tmp_path):
    db = tmp_path / "state.db"
    state = StateStore(db)
    add_task(state, "TASK-0001")
    add_task(state, "TASK-0002")
    DagScheduler(state).add_dependency("TASK-0002", "TASK-0001")
    reopened = StateStore(db)
    assert reopened.get_dependencies("TASK-0002") == ["TASK-0001"]
    assert reopened.get_dependents("TASK-0001") == ["TASK-0002"]
