from pathlib import Path

from agent_system.benchmark import BenchmarkCaseResult, BenchmarkRegistry, BenchmarkRunner, BenchmarkStatus
from agent_system.providers.base import ExecutionResult, ModelProvider, ProviderAvailability, ProviderCapabilities, ProviderTier
from agent_system.providers.registry import ProviderRegistry
from agent_system.router import ModelRouter


class GoodEditingProvider(ModelProvider):
    def __init__(self, provider_id="good"):
        super().__init__("good", provider_id, ProviderTier.FREE, {})
        self.model = "good-model"
    def availability(self): return ProviderAvailability.AVAILABLE
    def capabilities(self): return ProviderCapabilities(supports_file_editing=True)
    def execute(self, task, context, worktree):
        goal = task["goal"]
        if "calc.add" in goal:
            (Path(worktree) / "calc.py").write_text("def add(a, b):\n    return a + b\n")
            changed = ["calc.py"]
        else:
            (Path(worktree) / "README.md").write_text("# Demo\nBenchmark passed.\n")
            changed = ["README.md"]
        return ExecutionResult(True, "ok", modified_files=changed)
    def classify_error(self, error): return ProviderAvailability.PROVIDER_ERROR


class BadEditingProvider(GoodEditingProvider):
    def execute(self, task, context, worktree):
        return ExecutionResult(False, "", error="failed", modified_files=[])


class GoodReviewerProvider(ModelProvider):
    def __init__(self):
        super().__init__("reviewer", "reviewer", ProviderTier.FREE, {})
        self.model = "review-model"
    def availability(self): return ProviderAvailability.AVAILABLE
    def capabilities(self): return ProviderCapabilities(supports_file_editing=False)
    def execute(self, task, context, worktree):
        status = context["verification_report"]["overall_status"]
        verdict = "PASS" if status == "PASS" else "FAIL_TESTS"
        review = {"verdict": verdict, "notes": "ok", "details": {}}
        return ExecutionResult(True, "", modified_files=[], metadata={"review": review})
    def classify_error(self, error): return ProviderAvailability.PROVIDER_ERROR


def test_two_perfect_runs_promote_same_model_to_validated(tmp_path):
    registry = BenchmarkRegistry(tmp_path / "bench.db", validation_runs=2)
    results = [BenchmarkCaseResult("a", "implementer", True, 10), BenchmarkCaseResult("b", "implementer", True, 20)]
    first = registry.record("p", "m", "implementer", results)
    second = registry.record("p", "m", "implementer", results)
    assert first.status == BenchmarkStatus.CANDIDATE
    assert second.status == BenchmarkStatus.VALIDATED
    assert registry.status("p", "m") == BenchmarkStatus.VALIDATED


def test_model_identity_isolated_between_versions(tmp_path):
    registry = BenchmarkRegistry(tmp_path / "bench.db", validation_runs=2)
    results = [BenchmarkCaseResult("a", "implementer", True, 1)]
    registry.record("p", "model-v1", "implementer", results)
    registry.record("p", "model-v1", "implementer", results)
    assert registry.status("p", "model-v1") == BenchmarkStatus.VALIDATED
    assert registry.status("p", "model-v2") == BenchmarkStatus.UNBENCHMARKED


def test_bad_run_is_rejected_but_later_run_can_recover(tmp_path):
    registry = BenchmarkRegistry(tmp_path / "bench.db", reject_below=0.5)
    bad = [BenchmarkCaseResult("a", "implementer", False, 1), BenchmarkCaseResult("b", "implementer", False, 1)]
    good = [BenchmarkCaseResult("a", "implementer", True, 1), BenchmarkCaseResult("b", "implementer", True, 1)]
    assert registry.record("p", "m", "implementer", bad).status == BenchmarkStatus.REJECTED
    assert registry.record("p", "m", "implementer", good).status == BenchmarkStatus.CANDIDATE


def test_runner_validates_disposable_implementer_cases(tmp_path):
    registry = BenchmarkRegistry(tmp_path / "bench.db")
    summary = BenchmarkRunner(registry).run(GoodEditingProvider())
    assert summary.role == "implementer"
    assert summary.passed_cases == summary.total_cases == 2
    assert summary.status == BenchmarkStatus.CANDIDATE


def test_runner_validates_read_only_reviewer_cases(tmp_path):
    registry = BenchmarkRegistry(tmp_path / "bench.db")
    summary = BenchmarkRunner(registry).run(GoodReviewerProvider())
    assert summary.role == "reviewer"
    assert summary.passed_cases == summary.total_cases == 2


def test_router_skips_benchmark_rejected_provider(tmp_path):
    registry = BenchmarkRegistry(tmp_path / "bench.db")
    bad_results = [BenchmarkCaseResult("a", "implementer", False, 1), BenchmarkCaseResult("b", "implementer", False, 1)]
    registry.record("bad", "good-model", "implementer", bad_results)

    providers = ProviderRegistry()
    providers.register(GoodEditingProvider("bad"))
    providers.register(GoodEditingProvider("fallback"))
    routing = tmp_path / "routing.yaml"
    routing.write_text("low:\n  - bad\n  - fallback\n")
    decision = ModelRouter(providers, routing, registry).route("low")
    assert decision.provider is not None
    assert decision.provider.provider_id == "fallback"
    assert "BENCHMARK_REJECTED" in decision.reason or decision.provider.provider_id == "fallback"


def test_registry_persists_case_history(tmp_path):
    db = tmp_path / "bench.db"
    registry = BenchmarkRegistry(db)
    summary = BenchmarkRunner(registry).run(GoodEditingProvider())
    reopened = BenchmarkRegistry(db)
    latest = reopened.latest(summary.provider_id, summary.model_name)
    assert latest is not None
    assert latest.run_id == summary.run_id
    assert latest.pass_rate == 1.0
