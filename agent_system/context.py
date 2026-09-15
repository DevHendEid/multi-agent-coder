from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class FileInfo:
    path: str
    size: int
    selected: bool
    reason: str
    content: str | None = None


@dataclass(slots=True)
class RepoMetadata:
    root: str
    branch: str
    commit: str
    status: str
    file_count: int
    total_size: int


@dataclass(slots=True)
class TaskMetadata:
    id: str
    goal: str
    risk: str
    allowed_scope: list[str]
    forbidden_scope: list[str]


@dataclass(slots=True)
class Context:
    task: TaskMetadata
    repo: RepoMetadata
    selected_files: list[FileInfo]
    limits: dict[str, int]
    created_at: str


class ContextBuilder:
    def __init__(
        self,
        max_files: int = 20,
        max_file_bytes: int = 50000,
        max_total_context_bytes: int = 200000,
    ):
        self.max_files = max_files
        self.max_file_bytes = max_file_bytes
        self.max_total_context_bytes = max_total_context_bytes

    def build(
        self,
        task_id: str,
        goal: str,
        risk: str,
        allowed_scope: list[str],
        forbidden_scope: list[str],
        worktree: Path,
        project_root: Path,
    ) -> Context:
        repo_metadata = self._get_repo_metadata(worktree)
        candidate_files = self._find_candidate_files(worktree, allowed_scope, forbidden_scope)
        selected_files = self._select_files(candidate_files, worktree)
        task_metadata = TaskMetadata(
            id=task_id,
            goal=goal,
            risk=risk,
            allowed_scope=allowed_scope,
            forbidden_scope=forbidden_scope,
        )
        context = Context(
            task=task_metadata,
            repo=repo_metadata,
            selected_files=selected_files,
            limits={
                "max_files": self.max_files,
                "max_file_bytes": self.max_file_bytes,
                "max_total_context_bytes": self.max_total_context_bytes,
            },
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        return context

    def _get_repo_metadata(self, worktree: Path) -> RepoMetadata:
        try:
            branch_result = subprocess.run(
                ["git", "-C", str(worktree), "branch", "--show-current"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            branch = branch_result.stdout.strip() if branch_result.returncode == 0 else "unknown"

            commit_result = subprocess.run(
                ["git", "-C", str(worktree), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            commit = commit_result.stdout.strip() if commit_result.returncode == 0 else "unknown"

            status_result = subprocess.run(
                ["git", "-C", str(worktree), "status", "--porcelain"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            status = status_result.stdout.strip() if status_result.returncode == 0 else "unknown"
        except Exception:
            branch = "unknown"
            commit = "unknown"
            status = "error"

        file_count = 0
        total_size = 0
        for f in worktree.rglob("*"):
            if f.is_file() and not f.name.startswith("."):
                file_count += 1
                total_size += f.stat().st_size

        return RepoMetadata(
            root=str(worktree),
            branch=branch,
            commit=commit,
            status=status,
            file_count=file_count,
            total_size=total_size,
        )

    def _find_candidate_files(
        self,
        worktree: Path,
        allowed_scope: list[str],
        forbidden_scope: list[str],
    ) -> list[Path]:
        candidates = []
        for f in worktree.rglob("*"):
            if not f.is_file() or f.name.startswith("."):
                continue
            rel = f.relative_to(worktree)
            rel_str = str(rel)

            if forbidden_scope:
                forbidden = False
                for pattern in forbidden_scope:
                    if self._match_pattern(rel_str, pattern):
                        forbidden = True
                        break
                if forbidden:
                    continue

            if allowed_scope:
                allowed = False
                for pattern in allowed_scope:
                    if self._match_pattern(rel_str, pattern):
                        allowed = True
                        break
                if not allowed:
                    continue

            candidates.append(f)
        return candidates

    def _match_pattern(self, path: str, pattern: str) -> bool:
        if pattern.endswith("/"):
            return path.startswith(pattern)
        if pattern.startswith("*"):
            return path.endswith(pattern[1:])
        if pattern.endswith("*"):
            return path.startswith(pattern[:-1])
        return path == pattern

    def _select_files(self, candidates: list[Path], worktree: Path) -> list[FileInfo]:
        scored = []
        for f in candidates:
            rel = f.relative_to(worktree)
            rel_str = str(rel)
            size = f.stat().st_size

            score = 0
            reason = ""

            if size <= self.max_file_bytes:
                score += 10
            else:
                score -= 5
                reason = "large file"

            if rel_str.lower().endswith((".md", ".txt", ".py", ".js", ".ts", ".rs", ".java", ".go")):
                score += 5
                if not reason:
                    reason = "source/documentation file"

            if "readme" in rel_str.lower():
                score += 20
                reason = "README file"
            elif "test" in rel_str.lower() or "spec" in rel_str.lower():
                score += 10
                reason = "test file"
            elif rel_str.startswith("src/") or rel_str.startswith("lib/"):
                score += 8
                reason = "source directory"

            scored.append((score, f, rel_str, size, reason))

        scored.sort(key=lambda x: -x[0])

        selected = []
        total_bytes = 0
        for score, f, rel_str, size, reason in scored[:self.max_files]:
            if total_bytes + size > self.max_total_context_bytes:
                break
            content = None
            if size <= self.max_file_bytes:
                try:
                    content = f.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    content = f"<binary or unreadable: {size} bytes>"
            else:
                content = f"<file too large: {size} bytes, max {self.max_file_bytes}>"

            selected.append(FileInfo(
                path=rel_str,
                size=size,
                selected=True,
                reason=reason or "selected by scoring",
                content=content,
            ))
            total_bytes += size

        return selected

    def persist(self, context: Context, run_dir: Path) -> Path:
        run_dir.mkdir(parents=True, exist_ok=True)
        out_path = run_dir / "context.json"
        data = {
            "task": asdict(context.task),
            "repo": asdict(context.repo),
            "selected_files": [asdict(f) for f in context.selected_files],
            "limits": context.limits,
            "created_at": context.created_at,
        }
        out_path.write_text(json.dumps(data, indent=2))
        return out_path