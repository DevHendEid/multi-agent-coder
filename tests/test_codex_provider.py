from pathlib import Path

from agent_system.providers.base import ProviderTier
from agent_system.providers.codex_provider import CodexProvider


def make_provider(**overrides):
    config = {
        "executable": "codex",
        "model": "gpt-5.6-sol",
        "sandbox_mode": "workspace-write",
        "approve_for_me": True,
        "skip_git_repo_check": True,
    }
    config.update(overrides)
    return CodexProvider("Codex", "codex-real", ProviderTier.PREMIUM, config)


def test_build_command_does_not_mix_approve_for_me_with_sandbox(tmp_path: Path):
    provider = make_provider()
    cmd = provider._build_command("secret prompt", tmp_path, tmp_path / "out.txt")
    assert "-s" in cmd
    assert "workspace-write" in cmd
    assert "--approve-for-me" not in cmd


def test_build_command_can_use_approve_for_me_without_sandbox(tmp_path: Path):
    provider = make_provider(sandbox_mode="")
    cmd = provider._build_command("prompt", tmp_path, tmp_path / "out.txt")
    assert "--approve-for-me" in cmd
    assert "-s" not in cmd


def test_redacted_command_metadata_never_contains_prompt(tmp_path: Path):
    provider = make_provider()
    cmd = provider._build_command("TOP SECRET PROMPT", tmp_path, tmp_path / "out.txt")
    redacted = provider._redact_command(cmd)
    assert "TOP SECRET PROMPT" not in redacted
    assert "<PROMPT_REDACTED>" in redacted
