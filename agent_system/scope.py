from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class ScopeViolation:
    path: str
    reason: str


@dataclass(slots=True)
class ScopeCheckResult:
    passed: bool
    violations: list[ScopeViolation]
    modified_files: list[str]


class ScopeGate:
    def __init__(self, worktree: Path, allowed_scope: list[str], forbidden_scope: list[str], original_repo: Path | None = None):
        self.worktree = worktree.resolve()
        self.allowed_scope = allowed_scope
        self.forbidden_scope = forbidden_scope
        self.original_repo = original_repo.resolve() if original_repo else None
        self._baseline_head: str | None = None
        self._baseline_status: str | None = None
        self._baseline_original_status: str | None = None

    def validate_path_containment(self, target_path: Path) -> ScopeViolation | None:
        try:
            resolved_target = target_path.resolve()
            resolved_worktree = self.worktree.resolve()
            if not resolved_target.is_relative_to(resolved_worktree):
                return ScopeViolation(str(target_path), f"Path escapes worktree: {resolved_target} not in {resolved_worktree}")
            if self.original_repo:
                resolved_original = self.original_repo.resolve()
                if resolved_target.is_relative_to(resolved_original) and resolved_target != resolved_original:
                    return ScopeViolation(str(target_path), f"Path points into original repository: {resolved_target}")
        except Exception as exc:
            return ScopeViolation(str(target_path), f"Path validation failed: {exc}")
        return None

    def record_baseline(self) -> None:
        try:
            head_result = subprocess.run(["git", "-C", str(self.worktree), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
            self._baseline_head = head_result.stdout.strip() if head_result.returncode == 0 else None
            status_result = subprocess.run(["git", "-C", str(self.worktree), "status", "--porcelain"], capture_output=True, text=True, timeout=10)
            self._baseline_status = status_result.stdout.strip() if status_result.returncode == 0 else ""
            if self.original_repo:
                original_status = subprocess.run(["git", "-C", str(self.original_repo), "status", "--porcelain"], capture_output=True, text=True, timeout=10)
                self._baseline_original_status = original_status.stdout.strip() if original_status.returncode == 0 else None
        except Exception:
            self._baseline_head = None
            self._baseline_status = ""
            self._baseline_original_status = None

    def check(self) -> ScopeCheckResult:
        try:
            status_result = subprocess.run(["git", "-C", str(self.worktree), "status", "--porcelain"], capture_output=True, text=True, timeout=10)
            if status_result.returncode != 0:
                return ScopeCheckResult(False, [ScopeViolation("git", f"git status failed: {status_result.stderr}")], [])
            modified_files = self._parse_status(status_result.stdout.strip())
            violations = []
            for file_path in modified_files:
                violation = self._check_file(file_path)
                if violation:
                    violations.append(violation)
            if self.original_repo:
                violations.extend(self._check_original_repo())
            return ScopeCheckResult(len(violations) == 0, violations, modified_files)
        except Exception as exc:
            return ScopeCheckResult(False, [ScopeViolation("exception", str(exc))], [])

    def _parse_status(self, status_output: str) -> list[str]:
        files = []
        for line in status_output.strip().split("\n"):
            if not line:
                continue
            parts = line.split(maxsplit=1)
            if len(parts) == 2:
                files.append(parts[1])
        return files

    def _check_file(self, file_path: str) -> ScopeViolation | None:
        containment_check = self.validate_path_containment(self.worktree / file_path)
        if containment_check:
            return containment_check
        for pattern in self.forbidden_scope or []:
            if self._match_pattern(file_path, pattern):
                return ScopeViolation(file_path, f"matches forbidden pattern: {pattern}")
        if self.allowed_scope:
            if not any(self._match_pattern(file_path, p) for p in self.allowed_scope):
                return ScopeViolation(file_path, "not in allowed scope")
        return None

    def _check_original_repo(self) -> list[ScopeViolation]:
        violations = []
        try:
            status_result = subprocess.run(["git", "-C", str(self.original_repo), "status", "--porcelain"], capture_output=True, text=True, timeout=10)
            if status_result.returncode == 0:
                current_status = status_result.stdout.strip()
                baseline_status = self._baseline_original_status
                if baseline_status is None:
                    if current_status:
                        violations.append(ScopeViolation("original_repo", f"original repository state cannot be compared to baseline: {current_status}"))
                elif current_status != baseline_status:
                    violations.append(ScopeViolation("original_repo", "original repository changed after task execution"))
            else:
                violations.append(ScopeViolation("original_repo", f"unable to inspect original repository (exit {status_result.returncode}): {status_result.stderr.strip()}"))
        except Exception as exc:
            violations.append(ScopeViolation("original_repo", f"unable to inspect original repository: {exc}"))
        return violations

    def _match_pattern(self, path: str, pattern: str) -> bool:
        if pattern.endswith("/"):
            return path.startswith(pattern)
        if pattern.startswith("*"):
            return path.endswith(pattern[1:])
        if pattern.endswith("*"):
            return path.startswith(pattern[:-1])
        return path == pattern

    def diff_check(self) -> tuple[bool, str]:
        try:
            result = subprocess.run(["git", "-C", str(self.worktree), "diff", "--check"], capture_output=True, text=True, timeout=10)
            return result.returncode == 0, result.stdout + result.stderr
        except Exception as exc:
            return False, str(exc)

    def get_diff(self) -> str:
        try:
            result = subprocess.run(["git", "-C", str(self.worktree), "diff", "HEAD"], capture_output=True, text=True, timeout=10)
            return result.stdout
        except Exception:
            return ""

    def get_diff_stat(self) -> str:
        try:
            result = subprocess.run(["git", "-C", str(self.worktree), "diff", "--stat", "HEAD"], capture_output=True, text=True, timeout=10)
            return result.stdout
        except Exception:
            return ""
