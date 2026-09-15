from __future__ import annotations

import json
import subprocess
from pathlib import Path

from agent_system.context import ContextBuilder
from agent_system.models import TaskStatus
from agent_system.orchestrator import Orchestrator
from agent_system.providers.base import ExecutionResult, ModelProvider, ProviderAvailability, ProviderCapabilities, ProviderTier
from agent_system.providers.registry import ProviderRegistry
from agent_system.repair import RepairLoop
from agent_system.reviewer import ReviewResult, ReviewVerdict
from agent_system.router import ModelRouter
from agent_system.verifier import VerificationReport, VerificationResult


class CapturingRepairProvider(ModelProvider):
    def __init__(self):
        super().__init__("repair", "repair", ProviderTier.FREE, {})
        self.context = None

    def availability(self):
        return ProviderAvailability.AVAILABLE

    def capabilities(self):
        return ProviderCapabilities(supports_file_editing=True)

    def execute(self, task, context, worktree):
        self.context = context
        (Path(worktree) / "calc.py").write_text("def add(a, b):\n    return a + b\n")
        return ExecutionResult(True, "fixed", modified_files=["calc.py"])

    def classify_error(self, error):
        return ProviderAvailability.PROVIDER_ERROR


def test_repair_round_exposes_verification_failure_to_provider(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

    worktree = tmp_path / "worktree"
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-b", "agent/repair-context", str(worktree), "HEAD"], check=True, capture_output=True)

    projects = tmp_path / "projects"
    projects.mkdir()
    (projects / "demo.yaml").write_text(
        f"name: demo\nrepository:\n  path: {repo}\ncommands: {{}}\nprotected_paths: []\nrisk_rules: {{}}\n"
    )

    orch = Orchestrator(tmp_path)
    orch.state.create_task("TASK-R", "demo", "Fix add", "low")
    orch.state.update_task(
        "TASK-R",
        status=TaskStatus.REVIEWING.value,
        branch="agent/repair-context",
        worktree=str(worktree),
        provider_id="repair",
    )

    provider = CapturingRepairProvider()
    registry = ProviderRegistry()
    registry.register(provider)
    routing = tmp_path / "routing.yaml"
    routing.write_text("low:\n  - repair\n")

    loop = RepairLoop(orch, ModelRouter(registry, routing), ContextBuilder(), None, None)
    failed_command = VerificationResult(
        command="python -c test",
        cwd=str(worktree),
        exit_code=1,
        stdout="",
        stderr="AssertionError: expected 5",
        duration_ms=1,
        timed_out=False,
        status="FAIL",
    )
    verification = VerificationReport("TASK-R", "low", None, None, [failed_command], "FAIL", "now")
    review = ReviewResult(ReviewVerdict.FAIL_TESTS, "tests failed", {}, "deterministic", "now")

    result = loop._run_repair_round(
        "TASK-R",
        orch.state.get_task("TASK-R"),
        orch._load_project("demo"),
        worktree,
        repo,
        "diff --git a/calc.py b/calc.py\n",
        verification,
        review,
    )

    assert result.success is True
    assert provider.context is not None
    repair_items = [item for item in provider.context["selected_files"] if item["path"] == "__repair_context__.json"]
    assert len(repair_items) == 1
    payload = json.loads(repair_items[0]["content"])
    assert payload["review_verdict"] == ReviewVerdict.FAIL_TESTS
    assert payload["verification_failures"][0]["stderr"] == "AssertionError: expected 5"
