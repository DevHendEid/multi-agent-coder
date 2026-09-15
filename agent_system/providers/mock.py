from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import ExecutionResult, ModelProvider, ProviderAvailability, ProviderCapabilities, ProviderTier


class MockProvider(ModelProvider):
    def __init__(
        self,
        name: str = "mock",
        provider_id: str = "mock-free",
        tier: ProviderTier = ProviderTier.FREE,
        config: dict[str, Any] | None = None,
    ):
        super().__init__(name, provider_id, tier, config)
        self._availability = ProviderAvailability.AVAILABLE
        self._should_fail = config.get("should_fail", False) if config else False
        self._fail_with = config.get("fail_with", ProviderAvailability.PROVIDER_ERROR) if config else ProviderAvailability.PROVIDER_ERROR
        self._execution_count = 0
        self._max_executions = config.get("max_executions", -1) if config else -1
        self._implementation_template = config.get("implementation_template") if config else None

    def availability(self) -> ProviderAvailability:
        if self._max_executions > 0 and self._execution_count >= self._max_executions:
            return ProviderAvailability.QUOTA_EXHAUSTED
        return self._availability

    def set_availability(self, availability: ProviderAvailability) -> None:
        self._availability = availability

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supports_tools=True,
            supports_file_editing=True,
            supports_long_context=True,
            max_context_tokens=8192,
        )

    def execute(self, task: dict[str, Any], context: dict[str, Any], worktree: Path) -> ExecutionResult:
        if self.availability() != ProviderAvailability.AVAILABLE:
            return ExecutionResult(
                success=False,
                output="",
                error=f"Provider not available: {self.availability().value}",
                modified_files=[],
            )

        self._execution_count += 1

        if self._should_fail:
            return ExecutionResult(
                success=False,
                output="",
                error="Mock provider configured to fail",
                modified_files=[],
            )

        goal = task.get("goal", "")
        modified_files = []

        if self._implementation_template:
            for file_path, content in self._implementation_template.items():
                full_path = worktree / file_path
                full_path.parent.mkdir(parents=True, exist_ok=True)
                full_path.write_text(content)
                modified_files.append(file_path)
        elif "README" in goal.upper() or "readme" in goal.lower():
            readme_path = worktree / "README.md"
            content = readme_path.read_text() if readme_path.exists() else "# Project\n"
            new_content = content.rstrip() + "\n\n## Project Description\n\nThis is a demo project for the Multi-Agent Coder system.\n"
            readme_path.write_text(new_content)
            modified_files.append("README.md")
        else:
            modified_files.append("mock_output.txt")
            (worktree / "mock_output.txt").write_text(f"Mock implementation for: {goal}")

        return ExecutionResult(
            success=True,
            output=f"Implemented: {goal}",
            modified_files=modified_files,
            metadata={"execution_count": self._execution_count},
        )

    def classify_error(self, error: Exception) -> ProviderAvailability:
        error_str = str(error).lower()
        if "quota" in error_str or "credit" in error_str:
            return ProviderAvailability.QUOTA_EXHAUSTED
        if "rate" in error_str or "limit" in error_str:
            return ProviderAvailability.RATE_LIMITED
        if "auth" in error_str or "unauthorized" in error_str or "401" in error_str or "403" in error_str:
            return ProviderAvailability.AUTH_ERROR
        if "unavailable" in error_str or "timeout" in error_str or "connection" in error_str:
            return ProviderAvailability.UNAVAILABLE
        return ProviderAvailability.PROVIDER_ERROR
