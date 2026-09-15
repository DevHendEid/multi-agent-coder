from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agent_system.providers.base import ProviderAvailability, ProviderTier
from agent_system.providers.ollama_provider import OllamaProvider
from agent_system.providers.registry import ProviderRegistry


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "README.md").write_text("# Demo\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    return repo


def provider(role: str = "implementer") -> OllamaProvider:
    return OllamaProvider(
        "Ollama",
        "ollama-test",
        ProviderTier.FREE,
        {
            "model": "test-model",
            "role": role,
            "trust_status": "EXPERIMENTAL",
            "timeout_seconds": 30,
        },
    )


def test_availability_requires_configured_model(monkeypatch):
    p = OllamaProvider("Ollama", "ollama", ProviderTier.FREE, {"model": ""})
    assert p.availability() == ProviderAvailability.UNAVAILABLE


def test_availability_detects_local_model(monkeypatch):
    p = provider()
    monkeypatch.setattr(p, "_request_json", lambda path, payload, timeout: {"models": [{"name": "test-model"}]})
    assert p.availability() == ProviderAvailability.AVAILABLE


def test_implementer_applies_valid_patch_only_inside_worktree(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    p = provider()
    patch = """diff --git a/README.md b/README.md
index 8b13789..e6fbd24 100644
--- a/README.md
+++ b/README.md
@@ -1 +1,3 @@
 # Demo
+
+Local free model edit.
"""

    def fake_request(path, payload, timeout):
        if path == "/api/tags":
            return {"models": [{"name": "test-model"}]}
        return {"message": {"content": patch}}

    monkeypatch.setattr(p, "_request_json", fake_request)
    result = p.execute({"id": "TASK-1", "goal": "Update readme", "risk": "low"}, {"selected_files": []}, repo)
    assert result.success is True
    assert "README.md" in (result.modified_files or [])
    assert "Local free model edit." in (repo / "README.md").read_text()


def test_patch_path_traversal_is_rejected(tmp_path):
    repo = make_repo(tmp_path)
    bad = """diff --git a/../outside.txt b/../outside.txt
--- a/../outside.txt
+++ b/../outside.txt
@@ -0,0 +1 @@
+bad
"""
    with pytest.raises(ValueError, match="unsafe patch path"):
        OllamaProvider._validate_patch_paths(bad, repo)


def test_patch_symlink_escape_is_rejected(tmp_path):
    repo = make_repo(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (repo / "escape").symlink_to(outside, target_is_directory=True)
    bad = """diff --git a/escape/pwn.txt b/escape/pwn.txt
--- /dev/null
+++ b/escape/pwn.txt
@@ -0,0 +1 @@
+bad
"""
    with pytest.raises(ValueError, match="escapes worktree"):
        OllamaProvider._validate_patch_paths(bad, repo)


def test_reviewer_returns_structured_json_without_writes(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    p = provider(role="reviewer")
    review = {"verdict": "PASS", "notes": "Looks good", "details": {"checked": True}}

    def fake_request(path, payload, timeout):
        if path == "/api/tags":
            return {"models": [{"name": "test-model"}]}
        return {"message": {"content": json.dumps(review)}}

    monkeypatch.setattr(p, "_request_json", fake_request)
    before = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True, check=True).stdout
    result = p.execute(
        {"id": "TASK-1", "goal": "Review", "risk": "low", "diff": ""},
        {"verification_report": {"overall_status": "PASS"}},
        repo,
    )
    after = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True, check=True).stdout
    assert result.success is True
    assert result.metadata["review"]["verdict"] == "PASS"
    assert before == after == ""


def test_registry_builds_ollama_provider(tmp_path, monkeypatch):
    config = tmp_path / "models.yaml"
    config.write_text(
        """providers:
  free-local:
    type: ollama
    name: Local Free
    provider_id: free-local
    tier: free
    enabled: true
    model: test-model
    role: implementer
"""
    )
    registry = ProviderRegistry(config)
    p = registry.get("free-local")
    assert isinstance(p, OllamaProvider)
    assert p.tier == ProviderTier.FREE
