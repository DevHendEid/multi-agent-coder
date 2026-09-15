from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .orchestrator import Orchestrator


@dataclass(slots=True)
class ParallelTaskResult:
    task_id: str
    returncode: int
    stdout: str
    stderr: str

    @property
    def success(self) -> bool:
        return self.returncode == 0


Worker = Callable[[str], ParallelTaskResult]


class ParallelTaskRunner:
    """Run already-created tasks concurrently in isolated worktrees.

    Phase 5 deliberately parallelizes execution only after task/worktree creation.
    Preflight rejects duplicate task IDs, branches, or worktree paths before any
    worker starts. Each default worker runs in a separate Python process, which
    keeps provider/runtime state isolated while SQLite coordinates through WAL.
    """

    def __init__(self, root: Path, worker: Worker | None = None):
        self.root = Path(root).resolve()
        self.orchestrator = Orchestrator(self.root)
        self.worker = worker or self._run_subprocess

    def run(self, task_ids: list[str], max_workers: int = 2) -> list[ParallelTaskResult]:
        if not task_ids:
            raise ValueError("At least one task ID is required")
        if len(set(task_ids)) != len(task_ids):
            raise ValueError("Duplicate task IDs are not allowed")
        if max_workers < 1:
            raise ValueError("max_workers must be >= 1")

        self._preflight(task_ids)
        worker_count = min(max_workers, len(task_ids))
        results_by_id: dict[str, ParallelTaskResult] = {}
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="agent-task") as pool:
            futures = {pool.submit(self.worker, task_id): task_id for task_id in task_ids}
            for future in as_completed(futures):
                task_id = futures[future]
                try:
                    result = future.result()
                except Exception as exc:  # keep sibling tasks running and report failure
                    result = ParallelTaskResult(task_id, 1, "", f"worker exception: {exc}")
                results_by_id[task_id] = result
        return [results_by_id[task_id] for task_id in task_ids]

    def _preflight(self, task_ids: list[str]) -> None:
        worktrees: dict[str, str] = {}
        branches: dict[str, str] = {}
        for task_id in task_ids:
            state = self.orchestrator.state.get_task(task_id)
            if not state:
                raise ValueError(f"Unknown task: {task_id}")
            worktree_raw = state.get("worktree")
            branch = state.get("branch")
            if not worktree_raw:
                raise ValueError(f"Task {task_id} has no worktree")
            worktree = str(Path(worktree_raw).resolve())
            if not Path(worktree).is_dir():
                raise ValueError(f"Task {task_id} worktree does not exist: {worktree}")
            if worktree in worktrees:
                raise ValueError(
                    f"Tasks {worktrees[worktree]} and {task_id} share worktree {worktree}"
                )
            worktrees[worktree] = task_id
            if branch:
                if branch in branches:
                    raise ValueError(
                        f"Tasks {branches[branch]} and {task_id} share branch {branch}"
                    )
                branches[branch] = task_id

    def _run_subprocess(self, task_id: str) -> ParallelTaskResult:
        env = os.environ.copy()
        proc = subprocess.run(
            [sys.executable, "-m", "agent_system.cli", "run", task_id],
            cwd=str(self.root),
            env=env,
            capture_output=True,
            text=True,
        )
        return ParallelTaskResult(task_id, proc.returncode, proc.stdout, proc.stderr)
