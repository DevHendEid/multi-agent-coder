from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from agent_system.models import TaskStatus
from agent_system.parallel import ParallelTaskResult, ParallelTaskRunner
from agent_system.state import StateStore


def prepare_task(root: Path, task_id: str, worktree: Path, branch: str) -> None:
    worktree.mkdir(parents=True)
    state = StateStore(root / ".agent" / "state.db")
    state.create_task(task_id, "demo", f"goal-{task_id}", "low")
    state.update_task(
        task_id,
        status=TaskStatus.PLANNED.value,
        branch=branch,
        worktree=str(worktree),
    )


def test_parallel_runner_executes_distinct_tasks_concurrently(tmp_path: Path):
    prepare_task(tmp_path, "TASK-0001", tmp_path / "wt1", "agent/task-0001")
    prepare_task(tmp_path, "TASK-0002", tmp_path / "wt2", "agent/task-0002")

    lock = threading.Lock()
    active = 0
    peak = 0

    def worker(task_id: str) -> ParallelTaskResult:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.08)
        with lock:
            active -= 1
        return ParallelTaskResult(task_id, 0, f"done {task_id}", "")

    results = ParallelTaskRunner(tmp_path, worker=worker).run(
        ["TASK-0001", "TASK-0002"], max_workers=2
    )

    assert peak == 2
    assert [r.task_id for r in results] == ["TASK-0001", "TASK-0002"]
    assert all(r.success for r in results)


def test_parallel_runner_rejects_shared_worktree_before_start(tmp_path: Path):
    shared = tmp_path / "shared"
    prepare_task(tmp_path, "TASK-0001", shared, "agent/task-0001")
    state = StateStore(tmp_path / ".agent" / "state.db")
    state.create_task("TASK-0002", "demo", "goal-2", "low")
    state.update_task(
        "TASK-0002",
        status=TaskStatus.PLANNED.value,
        branch="agent/task-0002",
        worktree=str(shared),
    )

    calls = []
    runner = ParallelTaskRunner(
        tmp_path,
        worker=lambda task_id: calls.append(task_id) or ParallelTaskResult(task_id, 0, "", ""),
    )
    with pytest.raises(ValueError, match="share worktree"):
        runner.run(["TASK-0001", "TASK-0002"], max_workers=2)
    assert calls == []


def test_parallel_runner_rejects_shared_branch(tmp_path: Path):
    prepare_task(tmp_path, "TASK-0001", tmp_path / "wt1", "agent/shared")
    prepare_task(tmp_path, "TASK-0002", tmp_path / "wt2", "agent/shared")

    with pytest.raises(ValueError, match="share branch"):
        ParallelTaskRunner(tmp_path, worker=lambda _: None).run(
            ["TASK-0001", "TASK-0002"], max_workers=2
        )


def test_parallel_runner_rejects_duplicate_task_ids(tmp_path: Path):
    prepare_task(tmp_path, "TASK-0001", tmp_path / "wt1", "agent/task-0001")
    with pytest.raises(ValueError, match="Duplicate task IDs"):
        ParallelTaskRunner(tmp_path, worker=lambda _: None).run(
            ["TASK-0001", "TASK-0001"], max_workers=2
        )


def test_state_store_uses_wal_for_parallel_workers(tmp_path: Path):
    state = StateStore(tmp_path / "state.db")
    with state._connect() as con:
        mode = con.execute("PRAGMA journal_mode").fetchone()[0]
        timeout = con.execute("PRAGMA busy_timeout").fetchone()[0]
    assert mode.lower() == "wal"
    assert timeout >= 30000
