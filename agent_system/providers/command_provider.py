from __future__ import annotations

import os
import subprocess
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .base import ExecutionResult, ModelProvider, ProviderAvailability, ProviderCapabilities, ProviderTier


class CommandProvider(ModelProvider):
    def __init__(self, name: str, provider_id: str, tier: ProviderTier, config: dict[str, Any] | None = None):
        super().__init__(name, provider_id, tier, config)
        self.command_template = config.get("command") if config else []
        self.timeout_seconds = config.get("timeout_seconds", 300) if config else 300
        self.workdir = config.get("workdir") if config else None
        self.env = config.get("env") if config else {}

    def availability(self) -> ProviderAvailability:
        if not self.command_template:
            return ProviderAvailability.UNAVAILABLE
        cmd = self.command_template[0]
        if not (Path(cmd).is_absolute() and Path(cmd).exists()) and shutil.which(cmd) is None:
            return ProviderAvailability.UNAVAILABLE
        return ProviderAvailability.AVAILABLE

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(False, True, False, 4096)

    def _render_command(self, worktree: Path, prompt_file: Path) -> list[str]:
        return [part.replace("{worktree}", str(worktree)).replace("{prompt_file}", str(prompt_file)) for part in self.command_template]

    def execute(self, task: dict[str, Any], context: dict[str, Any], worktree: Path) -> ExecutionResult:
        if self.availability() != ProviderAvailability.AVAILABLE:
            return ExecutionResult(False, "", f"Command not available: {self.command_template[0] if self.command_template else 'none'}", [])
        fd, prompt_name = tempfile.mkstemp(prefix=f"multi-agent-{self.provider_id}-", suffix=".txt")
        os.close(fd)
        prompt_file = Path(prompt_name)
        prompt_file.write_text(self._build_prompt(task, context))
        cmd = self._render_command(worktree, prompt_file)
        env = os.environ.copy(); env.update(self.env)
        cwd = Path(self.workdir).expanduser().resolve() if self.workdir else worktree
        try:
            result = subprocess.run(cmd, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=self.timeout_seconds)
        except subprocess.TimeoutExpired:
            return ExecutionResult(False, "", f"Command timed out after {self.timeout_seconds}s", [])
        except FileNotFoundError:
            return ExecutionResult(False, "", f"Command not found: {cmd[0]}", [])
        except Exception as exc:
            return ExecutionResult(False, "", f"Command execution failed: {exc}", [])
        finally:
            prompt_file.unlink(missing_ok=True)
        return ExecutionResult(result.returncode == 0, result.stdout, result.stderr if result.returncode != 0 else None, self._detect_modified_files(worktree), {"returncode": result.returncode, "command": cmd})

    def _build_prompt(self, task: dict[str, Any], context: dict[str, Any]) -> str:
        parts = [f"Task: {task.get('id','unknown')}", f"Goal: {task.get('goal','')}", f"Risk: {task.get('risk','medium')}", "", f"Allowed scope: {task.get('allowed_scope', [])}", f"Forbidden scope: {task.get('forbidden_scope', [])}", "", "Repository context:"]
        for f in context.get("selected_files", []):
            parts.extend([f"--- {f['path']} ({f['size']} bytes) ---", f.get("content", "")])
        return "\n".join(parts)

    def _detect_modified_files(self, worktree: Path) -> list[str]:
        try:
            result = subprocess.run(["git", "-C", str(worktree), "status", "--porcelain"], capture_output=True, text=True, timeout=10)
            return [line.split(maxsplit=1)[1] for line in result.stdout.strip().split("\n") if line and len(line.split(maxsplit=1)) == 2]
        except Exception:
            return []

    def classify_error(self, error: Exception) -> ProviderAvailability:
        s = str(error).lower()
        if "timeout" in s: return ProviderAvailability.RATE_LIMITED
        if "not found" in s or "no such file" in s: return ProviderAvailability.UNAVAILABLE
        if "permission" in s or "auth" in s: return ProviderAvailability.AUTH_ERROR
        return ProviderAvailability.PROVIDER_ERROR
