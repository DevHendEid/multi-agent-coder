from __future__ import annotations

from pathlib import Path
import subprocess


class WorktreeError(RuntimeError):
    pass


class WorktreeManager:
    def __init__(self, repo_path: Path, worktree_root_name: str = ".agent-worktrees"):
        self.repo_path = repo_path
        self.root = repo_path.parent / f"{repo_path.name}-{worktree_root_name.lstrip('.')}"
        self.root.mkdir(parents=True, exist_ok=True)

    def create(self, task_id: str) -> tuple[str, Path]:
        branch = f"agent/{task_id.lower()}"
        path = self.root / task_id.lower()
        if path.exists():
            raise WorktreeError(f"Worktree already exists: {path}")

        result = subprocess.run(
            ["git", "-C", str(self.repo_path), "worktree", "add", "-b", branch, str(path), "HEAD"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise WorktreeError(result.stderr.strip() or result.stdout.strip())
        return branch, path

    def remove(self, path: Path, force: bool = False) -> None:
        cmd = ["git", "-C", str(self.repo_path), "worktree", "remove", str(path)]
        if force:
            cmd.insert(4, "--force")
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise WorktreeError(result.stderr.strip() or result.stdout.strip())
