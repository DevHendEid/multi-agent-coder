from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_system.benchmark import BenchmarkRegistry, BenchmarkStatus
from agent_system.integration import IntegrationError, IntegrationService
from agent_system.models import RiskLevel, TaskStatus
from agent_system.providers.base import (
    ExecutionResult,
    ModelProvider,
    ProviderAvailability,
    ProviderCapabilities,
    ProviderMetadata,
    ProviderTier,
)
from agent_system.providers.ollama_provider import OllamaProvider
from agent_system.providers.registry import ProviderRegistry
from agent_system.router import ModelRouter
from agent_system.scope import ScopeGate
from agent_system.state import StateStore


class DummyProvider(ModelProvider):
    def __init__(self, provider_id: str, avail: ProviderAvailability):
        super().__init__(provider_id, provider_id, ProviderTier.FREE)
        self._avail = avail

    def availability(self) -> ProviderAvailability:
        return self._avail

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(True, False, False, 4096)

    def classify_error(self, error: Exception) -> ProviderAvailability:
        return ProviderAvailability.PROVIDER_ERROR

    def execute(self, task: dict, context: dict, worktree: Path) -> ExecutionResult:
        return ExecutionResult(True, "", None, [])


def test_check_ci_non_zero_without_failure_is_pending(tmp_path: Path):
    root = tmp_path / "sys"
    root.mkdir()
    state = StateStore(root / ".agent-system" / "state.db")
    state.create_task("T-1", "demo", "goal", "low")

    # Mock gh runner returning exit code 1 with "no checks reported"
    def mock_gh(argv: list[str], cwd: Path):
        return subprocess.CompletedProcess(argv, 1, "", "no checks reported on the 'test' branch\n")

    service = IntegrationService(root, gh_runner=mock_gh)
    res = service.check_ci("T-1")
    assert res["status"] == "PENDING"
    assert state.get_task("T-1")["status"] == TaskStatus.CI_PENDING.value


def test_check_ci_explicit_failure(tmp_path: Path):
    root = tmp_path / "sys"
    root.mkdir()
    state = StateStore(root / ".agent-system" / "state.db")
    state.create_task("T-1", "demo", "goal", "low")

    def mock_gh(argv: list[str], cwd: Path):
        return subprocess.CompletedProcess(argv, 1, "", "Check failed: tests\n")

    service = IntegrationService(root, gh_runner=mock_gh)
    res = service.check_ci("T-1")
    assert res["status"] == "FAIL"
    assert state.get_task("T-1")["status"] == TaskStatus.CI_FAILED.value


def test_check_ci_confirmed_pass(tmp_path: Path):
    root = tmp_path / "sys"
    root.mkdir()
    state = StateStore(root / ".agent-system" / "state.db")
    state.create_task("T-1", "demo", "goal", "low")

    def mock_gh(argv: list[str], cwd: Path):
        return subprocess.CompletedProcess(argv, 0, "All checks passed\n", "")

    service = IntegrationService(root, gh_runner=mock_gh)
    res = service.check_ci("T-1")
    assert res["status"] == "PASS"


def test_merge_pr_blocks_when_ci_is_pending(tmp_path: Path):
    root = tmp_path / "sys"
    root.mkdir()
    state = StateStore(root / ".agent-system" / "state.db")
    state.create_task("T-1", "demo", "goal", "low")
    state.update_task("T-1", branch="agent/t-1")

    def mock_gh(argv: list[str], cwd: Path):
        if argv[:3] == ["gh", "pr", "checks"]:
            return subprocess.CompletedProcess(argv, 1, "", "no checks reported on the 'agent/t-1' branch")
        return subprocess.CompletedProcess(argv, 0, "Merged", "")

    service = IntegrationService(root, gh_runner=mock_gh)
    with pytest.raises(IntegrationError, match="Merge blocked: CI checks are pending"):
        service.merge_pr("T-1")


def test_finalize_reports_and_persists_worktree_cleanup_failure(tmp_path: Path):
    root = tmp_path / "sys"
    root.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    state = StateStore(root / ".agent-system" / "state.db")
    state.create_task("T-1", "demo", "goal", "low")
    fake_wt = tmp_path / "fake_wt"
    fake_wt.mkdir()
    state.update_task("T-1", branch="agent/t-1", worktree=str(fake_wt))

    # Setup gates
    run_dir = root / "runs" / "T-1"
    run_dir.mkdir(parents=True)
    (run_dir / "verification.json").write_text(json.dumps({"overall_status": "PASS"}))
    (run_dir / "review.json").write_text(json.dumps({"verdict": "PASS"}))

    service = IntegrationService(root)
    # Stub confirm_merge_on_main and post_merge_verify
    service.confirm_merge_on_main = MagicMock(return_value="merge123")
    service.post_merge_verify = MagicMock()

    # Mock WorktreeManager.remove to fail
    with patch("agent_system.integration.WorktreeManager.remove", side_effect=OSError("permission denied")):
        with pytest.raises(IntegrationError, match="Worktree cleanup failed"):
            service.finalize("T-1", merge_sha="merge123", clean_resources=True)

    task = state.get_task("T-1")
    assert "Worktree cleanup failed" in (task.get("blocker") or "")
    events = state.get_events("T-1")
    assert any(e["action"] == "worktree_cleanup_failed" for e in events)


def test_router_excludes_unavailable_providers_from_fallbacks(tmp_path: Path):
    p_good1 = DummyProvider("p1", ProviderAvailability.AVAILABLE)
    p_good2 = DummyProvider("p2", ProviderAvailability.AVAILABLE)
    p_bad = DummyProvider("p3", ProviderAvailability.UNAVAILABLE)
    p_quota = DummyProvider("p4", ProviderAvailability.QUOTA_EXHAUSTED)

    registry = MagicMock(spec=ProviderRegistry)
    providers = {"p1": p_good1, "p2": p_good2, "p3": p_bad, "p4": p_quota}
    registry.get = lambda pid: providers.get(pid)

    cfg = tmp_path / "routing.yaml"
    cfg.write_text("low:\n  - p1\n  - p3\n  - p4\n  - p2\n")

    router = ModelRouter(registry, routing_config_path=cfg)
    decision = router.route("low")
    assert decision.provider == p_good1
    # Only p2 is AVAILABLE, p3 and p4 must NOT be in fallbacks
    assert decision.fallback_providers == [p_good2]


def test_router_benchmark_lookup_failure_yields_benchmark_error(tmp_path: Path):
    p = DummyProvider("p1", ProviderAvailability.AVAILABLE)
    registry = MagicMock(spec=ProviderRegistry)
    registry.get = lambda pid: p if pid == "p1" else None

    # Benchmarks lookup raises Exception
    bench = MagicMock(spec=BenchmarkRegistry)
    bench.status.side_effect = sqlite3.OperationalError("database disk image is malformed")

    cfg = tmp_path / "routing.yaml"
    cfg.write_text("low:\n  - p1\n")

    router = ModelRouter(registry, routing_config_path=cfg, benchmark_registry=bench)
    decision = router.route("low")
    assert decision.provider is None
    assert "BENCHMARK_ERROR" in decision.reason


def test_ollama_provider_patch_uses_fuzz_zero():
    import inspect
    src = inspect.getsource(OllamaProvider._apply_patch)
    assert "--fuzz=0" in src


def test_scope_original_repo_failure_returns_violation(tmp_path: Path):
    repo = tmp_path / "nonexistent_repo"
    enforcer = ScopeGate(worktree=tmp_path, allowed_scope=[], forbidden_scope=[], original_repo=repo)
    violations = enforcer._check_original_repo()
    assert len(violations) >= 1
    assert "unable to inspect original repository" in violations[0].reason
