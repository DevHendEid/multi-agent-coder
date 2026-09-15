from __future__ import annotations

import json
import os
import subprocess
import time
import tempfile
from pathlib import Path
from typing import Any

from .base import ExecutionResult, ModelProvider, ProviderAvailability, ProviderCapabilities, ProviderMetadata, ProviderTier, ProviderTrustStatus


class CodexProvider(ModelProvider):
    def __init__(self, name: str, provider_id: str, tier: ProviderTier, config: dict[str, Any] | None = None):
        super().__init__(name, provider_id, tier, config)
        self.executable = config.get("executable", "codex") if config else "codex"
        self.model = config.get("model", "gpt-5.6-sol") if config else "gpt-5.6-sol"
        self.timeout_seconds = config.get("timeout_seconds", 600) if config else 600
        self.sandbox_mode = config.get("sandbox_mode", "workspace-write") if config else "workspace-write"
        self.approve_for_me = config.get("approve_for_me", True) if config else True
        self.skip_git_repo_check = config.get("skip_git_repo_check", True) if config else True
        trust_str = config.get("trust_status", "EXPERIMENTAL") if config else "EXPERIMENTAL"
        try:
            self.trust_status = ProviderTrustStatus(trust_str)
        except ValueError:
            self.trust_status = ProviderTrustStatus.EXPERIMENTAL
        self._execution_count = 0
        self._max_executions = config.get("max_executions", -1) if config else -1

    def availability(self) -> ProviderAvailability:
        if self._max_executions > 0 and self._execution_count >= self._max_executions:
            return ProviderAvailability.QUOTA_EXHAUSTED
        try:
            result = subprocess.run([self.executable, "--version"], capture_output=True, text=True, timeout=10)
            if result.returncode != 0:
                return ProviderAvailability.UNAVAILABLE
        except Exception:
            return ProviderAvailability.UNAVAILABLE
        return ProviderAvailability.AVAILABLE

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(True, True, True, 128000)

    def get_metadata(self) -> ProviderMetadata:
        return ProviderMetadata(self.provider_id, self.model, self.tier, True, True, True, True, 128000, self.availability(), self.timeout_seconds, self.trust_status)

    def execute(self, task: dict[str, Any], context: dict[str, Any], worktree: Path) -> ExecutionResult:
        if self.availability() != ProviderAvailability.AVAILABLE:
            return ExecutionResult(False, "", f"Codex not available: {self.availability().value}", [])
        self._execution_count += 1
        prompt = self._build_prompt(task, context)
        output_file = Path(tempfile.gettempdir()) / f"multi-agent-codex-{self.provider_id}-{os.getpid()}.txt"
        cmd = self._build_command(prompt, worktree, output_file)
        start = time.perf_counter()
        try:
            result = subprocess.run(cmd, cwd=str(worktree), env=os.environ.copy(), capture_output=True, text=True, timeout=self.timeout_seconds)
            duration_ms = int((time.perf_counter() - start) * 1000)
        except subprocess.TimeoutExpired:
            return ExecutionResult(False, "", f"Codex timed out after {self.timeout_seconds}s", [], {"duration_ms": int((time.perf_counter()-start)*1000)})
        except FileNotFoundError:
            return ExecutionResult(False, "", f"Codex executable not found: {self.executable}", [])
        except Exception as exc:
            return ExecutionResult(False, "", f"Codex execution failed: {exc}", [], {"duration_ms": int((time.perf_counter()-start)*1000)})
        modified_files = self._detect_modified_files(worktree)
        success = result.returncode == 0
        error = result.stderr if result.returncode != 0 else None
        combined = (result.stdout + result.stderr).lower()
        if "usage limit" in combined or "quota" in combined:
            error = "Quota exhausted"
            success = False
        return ExecutionResult(
            success,
            result.stdout,
            error,
            modified_files,
            {
                "returncode": result.returncode,
                "command": self._redact_command(cmd),
                "duration_ms": duration_ms,
                "model": self.model,
                "trust_status": self.trust_status,
            },
        )

    def _build_command(self, prompt_content: str, worktree: Path, output_file: Path) -> list[str]:
        cmd = [self.executable, "exec"]
        if self.skip_git_repo_check:
            cmd.append("--skip-git-repo-check")
        cmd.extend(["-C", str(worktree)])
        # Current Codex CLI rejects --approve-for-me together with --sandbox.
        # Prefer the explicit sandbox policy when one is configured.
        if self.approve_for_me and not self.sandbox_mode:
            cmd.append("--approve-for-me")
        cmd.extend(["-m", self.model])
        if self.sandbox_mode:
            cmd.extend(["-s", self.sandbox_mode])
        cmd.extend(["--output-last-message", str(output_file), "--json", prompt_content])
        return cmd

    @staticmethod
    def _redact_command(cmd: list[str]) -> list[str]:
        """Return command metadata without persisting the full prompt/context."""
        redacted = list(cmd)
        if redacted:
            redacted[-1] = "<PROMPT_REDACTED>"
        return redacted

    def _build_prompt(self, task: dict[str, Any], context: dict[str, Any]) -> str:
        parts = [
            f"Task: {task.get('id','unknown')}",
            f"Goal: {task.get('goal','')}",
            f"Risk: {task.get('risk','medium')}",
            "",
            "Context:",
            f"Allowed scope: {task.get('allowed_scope', [])}",
            f"Forbidden scope: {task.get('forbidden_scope', [])}",
            "",
            "Repository context:",
        ]
        for f in context.get("selected_files", []):
            parts.extend([f"--- {f['path']} ({f['size']} bytes) ---", f.get("content", "")])
        parts.extend([
            "",
            "Instructions:",
            "1. Implement the requested changes in the repository.",
            "2. Only modify files within the allowed scope.",
            "3. Do not modify protected paths.",
            "4. Write clean, tested code following project conventions.",
        ])
        return "\n".join(parts)

    def _detect_modified_files(self, worktree: Path) -> list[str]:
        try:
            result = subprocess.run(["git", "-C", str(worktree), "status", "--porcelain"], capture_output=True, text=True, timeout=10)
            return [line.split(maxsplit=1)[1] for line in result.stdout.strip().split("\n") if line and len(line.split(maxsplit=1)) == 2]
        except Exception:
            return []

    def classify_error(self, error: Exception) -> ProviderAvailability:
        s = str(error).lower()
        if "quota" in s or "usage limit" in s or "credit" in s:
            return ProviderAvailability.QUOTA_EXHAUSTED
        if "rate" in s or "limit" in s or "timeout" in s:
            return ProviderAvailability.RATE_LIMITED
        if "auth" in s or "unauthorized" in s or "401" in s or "403" in s:
            return ProviderAvailability.AUTH_ERROR
        if "not found" in s or "no such file" in s or "unavailable" in s or "connection" in s:
            return ProviderAvailability.UNAVAILABLE
        return ProviderAvailability.PROVIDER_ERROR


class CodexReviewerProvider(ModelProvider):
    def __init__(self, name: str, provider_id: str, tier: ProviderTier, config: dict[str, Any] | None = None):
        super().__init__(name, provider_id, tier, config)
        self.executable = config.get("executable", "codex") if config else "codex"
        self.model = config.get("model", "gpt-5.6-sol") if config else "gpt-5.6-sol"
        self.timeout_seconds = config.get("timeout_seconds", 300) if config else 300
        self.sandbox_mode = config.get("sandbox_mode", "read-only") if config else "read-only"
        self.skip_git_repo_check = config.get("skip_git_repo_check", True) if config else True
        try:
            self.trust_status = ProviderTrustStatus(config.get("trust_status", "EXPERIMENTAL") if config else "EXPERIMENTAL")
        except ValueError:
            self.trust_status = ProviderTrustStatus.EXPERIMENTAL

    def availability(self) -> ProviderAvailability:
        try:
            r = subprocess.run([self.executable, "--version"], capture_output=True, text=True, timeout=10)
            return ProviderAvailability.AVAILABLE if r.returncode == 0 else ProviderAvailability.UNAVAILABLE
        except Exception:
            return ProviderAvailability.UNAVAILABLE

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(False, False, True, 128000)

    def get_metadata(self) -> ProviderMetadata:
        return ProviderMetadata(self.provider_id, self.model, self.tier, True, False, False, True, 128000, self.availability(), self.timeout_seconds, self.trust_status)

    def execute(self, task: dict[str, Any], context: dict[str, Any], worktree: Path) -> ExecutionResult:
        if self.availability() != ProviderAvailability.AVAILABLE:
            return ExecutionResult(False, "", f"Codex reviewer not available: {self.availability().value}", [])
        prompt = self._build_review_prompt(task, context)
        output_file = Path(tempfile.gettempdir()) / f"multi-agent-review-{self.provider_id}-{os.getpid()}.txt"
        cmd = self._build_review_command(prompt, worktree, output_file)
        start = time.perf_counter()
        try:
            result = subprocess.run(cmd, cwd=str(worktree), env=os.environ.copy(), capture_output=True, text=True, timeout=self.timeout_seconds)
            duration_ms = int((time.perf_counter() - start) * 1000)
        except subprocess.TimeoutExpired:
            return ExecutionResult(False, "", f"Codex review timed out after {self.timeout_seconds}s", [], {"duration_ms": int((time.perf_counter()-start)*1000)})
        except FileNotFoundError:
            return ExecutionResult(False, "", f"Codex executable not found: {self.executable}", [])
        except Exception as exc:
            return ExecutionResult(False, "", f"Codex review execution failed: {exc}", [], {"duration_ms": int((time.perf_counter()-start)*1000)})
        review_data = self._parse_review_output(result.stdout)
        return ExecutionResult(
            result.returncode == 0,
            result.stdout,
            result.stderr if result.returncode != 0 else None,
            [],
            {
                "returncode": result.returncode,
                "command": CodexProvider._redact_command(cmd),
                "duration_ms": duration_ms,
                "model": self.model,
                "trust_status": self.trust_status,
                "review": review_data,
            },
        )

    def _build_review_command(self, prompt_content: str, worktree: Path, output_file: Path) -> list[str]:
        cmd = [self.executable, "exec"]
        if self.skip_git_repo_check:
            cmd.append("--skip-git-repo-check")
        cmd.extend(["-C", str(worktree), "-s", self.sandbox_mode, "-m", self.model, "--output-last-message", str(output_file), "--json", prompt_content])
        return cmd

    def _build_review_prompt(self, task: dict[str, Any], context: dict[str, Any]) -> str:
        return "\n".join([
            f"Task: {task.get('id','unknown')}",
            f"Goal: {task.get('goal','')}",
            f"Risk: {task.get('risk','medium')}",
            "",
            "You are an independent code reviewer. Analyze the changes and provide a structured review.",
            "",
            "Git diff to review:",
            task.get("diff", ""),
            "",
            "Verification results:",
            json.dumps(context.get("verification_report", {}), indent=2),
            "",
            "Output JSON exactly with verdict, notes, details. Allowed verdicts: PASS, PASS_WITH_NOTES, FAIL_SCOPE, FAIL_CORRECTNESS, FAIL_TESTS, FAIL_ARCHITECTURE, FAIL_SECURITY, NEEDS_HUMAN_DECISION. Deterministic failures cannot be overridden.",
        ])

    def _parse_review_output(self, output: str) -> dict[str, Any]:
        for line in output.strip().split("\n"):
            if line.strip().startswith("{"):
                try:
                    return json.loads(line.strip())
                except json.JSONDecodeError:
                    pass
        return {"verdict": "NEEDS_HUMAN_DECISION", "notes": "Could not parse review output", "details": {}}

    def classify_error(self, error: Exception) -> ProviderAvailability:
        return CodexProvider.classify_error(self, error)
