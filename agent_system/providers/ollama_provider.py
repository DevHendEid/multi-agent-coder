from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .base import (
    ExecutionResult,
    ModelProvider,
    ProviderAvailability,
    ProviderCapabilities,
    ProviderMetadata,
    ProviderTier,
    ProviderTrustStatus,
)


class OllamaProvider(ModelProvider):
    """Local Ollama provider.

    Implementer mode asks the model for a unified diff, validates every path,
    then applies it with ``git apply``. Reviewer mode is read-only and returns
    structured JSON for ProviderReviewer.
    """

    def __init__(self, name: str, provider_id: str, tier: ProviderTier, config: dict[str, Any] | None = None):
        super().__init__(name, provider_id, tier, config)
        cfg = config or {}
        self.base_url = str(cfg.get("base_url", "http://127.0.0.1:11434")).rstrip("/")
        model_env = str(cfg.get("model_env", "OLLAMA_MODEL"))
        self.model = os.environ.get(model_env, str(cfg.get("model", ""))).strip()
        self.role = str(cfg.get("role", "implementer"))
        self.timeout_seconds = int(cfg.get("timeout_seconds", 300))
        self.context_limit = int(cfg.get("context_limit", 32768))
        trust_str = str(cfg.get("trust_status", "EXPERIMENTAL"))
        try:
            self.trust_status = ProviderTrustStatus(trust_str)
        except ValueError:
            self.trust_status = ProviderTrustStatus.EXPERIMENTAL

    def availability(self) -> ProviderAvailability:
        if not self.model:
            return ProviderAvailability.UNAVAILABLE
        try:
            data = self._request_json("/api/tags", None, timeout=5)
        except Exception as exc:
            return self.classify_error(exc)
        names = {
            str(item.get("name", ""))
            for item in data.get("models", [])
            if isinstance(item, dict)
        }
        return ProviderAvailability.AVAILABLE if self.model in names else ProviderAvailability.UNAVAILABLE

    def capabilities(self) -> ProviderCapabilities:
        reviewer = self.role == "reviewer"
        return ProviderCapabilities(
            supports_tools=False,
            supports_file_editing=not reviewer,
            supports_long_context=True,
            max_context_tokens=self.context_limit,
        )

    def get_metadata(self) -> ProviderMetadata:
        caps = self.capabilities()
        return ProviderMetadata(
            provider_id=self.provider_id,
            model_name=self.model or "unconfigured",
            tier=self.tier,
            supports_code=True,
            supports_tools=caps.supports_tools,
            supports_file_editing=caps.supports_file_editing,
            supports_long_context=caps.supports_long_context,
            context_limit=self.context_limit,
            availability=self.availability(),
            timeout_seconds=self.timeout_seconds,
            trust_status=self.trust_status,
        )

    def execute(self, task: dict[str, Any], context: dict[str, Any], worktree: Path) -> ExecutionResult:
        availability = self.availability()
        if availability != ProviderAvailability.AVAILABLE:
            return ExecutionResult(
                False,
                "",
                f"Ollama provider unavailable: {availability.value}",
                [],
                {"availability": availability.value, "model": self.model},
            )

        worktree = Path(worktree).resolve()
        if not worktree.is_dir():
            return ExecutionResult(False, "", f"Worktree does not exist: {worktree}", [])

        prompt = self._build_review_prompt(task, context) if self.role == "reviewer" else self._build_patch_prompt(task, context)
        payload: dict[str, Any] = {
            "model": self.model,
            "stream": False,
            "messages": [{"role": "user", "content": prompt}],
        }
        if self.role == "reviewer":
            payload["format"] = "json"

        start = time.perf_counter()
        try:
            data = self._request_json("/api/chat", payload, timeout=self.timeout_seconds)
        except Exception as exc:
            availability = self.classify_error(exc)
            return ExecutionResult(
                False,
                "",
                f"Ollama execution failed: {exc}",
                [],
                {"availability": availability.value, "model": self.model},
            )

        duration_ms = int((time.perf_counter() - start) * 1000)
        output = str((data.get("message") or {}).get("content", ""))
        metadata: dict[str, Any] = {
            "model": self.model,
            "role": self.role,
            "duration_ms": duration_ms,
            "trust_status": self.trust_status.value,
        }

        if self.role == "reviewer":
            try:
                review = json.loads(output)
                if isinstance(review, dict):
                    metadata["review"] = review
            except json.JSONDecodeError:
                pass
            return ExecutionResult(True, output, None, [], metadata)

        patch = self._extract_patch(output)
        if not patch:
            return ExecutionResult(False, output, "Model did not return a unified diff", [], metadata)

        try:
            self._validate_patch_paths(patch, worktree)
            self._apply_patch(patch, worktree)
        except Exception as exc:
            return ExecutionResult(False, output, f"Patch rejected: {exc}", [], metadata)

        modified = self._detect_modified_files(worktree)
        metadata["patch_bytes"] = len(patch.encode("utf-8"))
        return ExecutionResult(True, output, None, modified, metadata)

    def classify_error(self, error: Exception) -> ProviderAvailability:
        if isinstance(error, urllib.error.HTTPError):
            if error.code in (401, 403):
                return ProviderAvailability.AUTH_ERROR
            if error.code == 429:
                return ProviderAvailability.RATE_LIMITED
            if error.code in (404, 502, 503, 504):
                return ProviderAvailability.UNAVAILABLE
            return ProviderAvailability.PROVIDER_ERROR
        if isinstance(error, urllib.error.URLError):
            return ProviderAvailability.UNAVAILABLE
        text = str(error).lower()
        if "timed out" in text or "timeout" in text:
            return ProviderAvailability.RATE_LIMITED
        if "connection" in text or "refused" in text or "not found" in text:
            return ProviderAvailability.UNAVAILABLE
        return ProviderAvailability.PROVIDER_ERROR

    def _request_json(self, path: str, payload: dict[str, Any] | None, timeout: int) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        if payload is None:
            req = urllib.request.Request(url, method="GET")
        else:
            body = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Ollama response must be a JSON object")
        return data

    def _build_patch_prompt(self, task: dict[str, Any], context: dict[str, Any]) -> str:
        parts = [
            f"Task: {task.get('id', 'unknown')}",
            f"Goal: {task.get('goal', '')}",
            f"Risk: {task.get('risk', 'medium')}",
            f"Allowed scope: {task.get('allowed_scope', [])}",
            f"Forbidden scope: {task.get('forbidden_scope', [])}",
            "",
            "Repository context:",
        ]
        for item in context.get("selected_files", []):
            parts.extend([
                f"--- {item.get('path', '')} ({item.get('size', 0)} bytes) ---",
                str(item.get("content", "")),
            ])
        parts.extend([
            "",
            "Return ONLY a valid unified git diff. Do not include prose.",
            "Do not use absolute paths, ../ paths, or modify .git.",
            "Only change files necessary for the task.",
        ])
        return "\n".join(parts)

    def _build_review_prompt(self, task: dict[str, Any], context: dict[str, Any]) -> str:
        return "\n".join([
            f"Task: {task.get('id', 'unknown')}",
            f"Goal: {task.get('goal', '')}",
            f"Risk: {task.get('risk', 'medium')}",
            "You are a read-only independent code reviewer.",
            "Git diff:",
            str(task.get("diff", "")),
            "Verification results:",
            json.dumps(context.get("verification_report", {}), indent=2),
            "Return JSON only with keys: verdict, notes, details.",
            "Allowed verdicts: PASS, PASS_WITH_NOTES, FAIL_SCOPE, FAIL_CORRECTNESS, FAIL_TESTS, FAIL_ARCHITECTURE, FAIL_SECURITY, NEEDS_HUMAN_DECISION.",
        ])

    @staticmethod
    def _extract_patch(output: str) -> str:
        text = (output or "").strip()
        if "```diff" in text:
            text = text.split("```diff", 1)[1].split("```", 1)[0].strip()
        elif "```patch" in text:
            text = text.split("```patch", 1)[1].split("```", 1)[0].strip()
        if text.startswith("diff --git") or text.startswith("--- "):
            return text + ("\n" if not text.endswith("\n") else "")
        pos = text.find("diff --git")
        if pos >= 0:
            patch = text[pos:].strip()
            return patch + ("\n" if not patch.endswith("\n") else "")
        return ""

    @classmethod
    def _validate_patch_paths(cls, patch: str, worktree: Path) -> None:
        paths: list[str] = []
        for line in patch.splitlines():
            if line.startswith("diff --git "):
                try:
                    parts = shlex.split(line)
                except ValueError as exc:
                    raise ValueError(f"invalid diff header: {exc}") from exc
                if len(parts) >= 4:
                    paths.extend(parts[2:4])
            elif line.startswith(("--- ", "+++ ")):
                raw = line[4:].split("\t", 1)[0].strip()
                if raw != "/dev/null":
                    paths.append(raw)
            elif line.startswith("rename from "):
                paths.append(line[len("rename from "):].strip())
            elif line.startswith("rename to "):
                paths.append(line[len("rename to "):].strip())

        if not paths:
            raise ValueError("patch contains no file paths")

        root = worktree.resolve()
        for raw in paths:
            path = raw
            if path.startswith("a/") or path.startswith("b/"):
                path = path[2:]
            candidate = Path(path)
            if candidate.is_absolute() or ".." in candidate.parts:
                raise ValueError(f"unsafe patch path: {raw}")
            if candidate.parts and candidate.parts[0] == ".git":
                raise ValueError("patch may not modify .git")
            resolved = (root / candidate).resolve(strict=False)
            try:
                resolved.relative_to(root)
            except ValueError as exc:
                raise ValueError(f"patch escapes worktree: {raw}") from exc

    @staticmethod
    def _apply_patch(patch: str, worktree: Path) -> None:
        check = subprocess.run(
            ["git", "apply", "--check", "-"],
            cwd=str(worktree),
            input=patch,
            text=True,
            capture_output=True,
            timeout=30,
        )
        if check.returncode != 0:
            raise ValueError(check.stderr.strip() or "git apply --check failed")
        applied = subprocess.run(
            ["git", "apply", "-"],
            cwd=str(worktree),
            input=patch,
            text=True,
            capture_output=True,
            timeout=30,
        )
        if applied.returncode != 0:
            raise ValueError(applied.stderr.strip() or "git apply failed")

    @staticmethod
    def _detect_modified_files(worktree: Path) -> list[str]:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(worktree),
            capture_output=True,
            text=True,
            timeout=10,
        )
        files: list[str] = []
        for line in result.stdout.splitlines():
            if len(line) < 4:
                continue
            path = line[3:]
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            files.append(path)
        return files
