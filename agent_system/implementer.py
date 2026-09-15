from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .artifacts import RunArtifacts
from .context import ContextBuilder
from .models import TaskStatus
from .orchestrator import Orchestrator
from .providers import ProviderAvailability
from .repair import RepairLoop
from .reviewer import Reviewer, ReviewResult, ReviewVerdict
from .router import ModelRouter, RoutingDecision
from .runtime_lock import TaskLease, TaskLeaseError
from .scope import ScopeGate
from .verifier import VerificationReport, Verifier


@dataclass(slots=True)
class ImplementationResult:
    success: bool
    provider_id: str | None
    modified_files: list[str]
    diff: str
    diff_stat: str
    error: str | None
    metadata: dict[str, Any]
    duration_ms: int


class Implementer:
    def __init__(self, orchestrator: Orchestrator, router: ModelRouter, context_builder: ContextBuilder,
                 verifier: Verifier | None = None, reviewer: Reviewer | None = None,
                 repair_loop: RepairLoop | None = None, artifacts: RunArtifacts | None = None):
        self.orchestrator = orchestrator
        self.router = router
        self.context_builder = context_builder
        self.verifier = verifier
        self.reviewer = reviewer
        self.repair_loop = repair_loop
        self.artifacts = artifacts

    def run(self, task_id: str) -> ImplementationResult:
        try:
            with TaskLease(self.orchestrator.root, task_id):
                return self._run_locked(task_id)
        except TaskLeaseError as exc:
            return ImplementationResult(False, None, [], "", "", str(exc), {"runtime_lock": "busy"}, 0)

    def _run_locked(self, task_id: str) -> ImplementationResult:
        state = self.orchestrator.state.get_task(task_id)
        if not state:
            raise ValueError(f"Task not found: {task_id}")
        if state["status"] not in {TaskStatus.PLANNED.value, TaskStatus.CONTEXT_READY.value,
                                   TaskStatus.WAITING_PROVIDER.value, TaskStatus.WAITING_CODEX.value}:
            raise ValueError(f"Task {task_id} is in invalid state for implementation: {state['status']}")
        project = self.orchestrator._load_project(state["project"])
        worktree = Path(state["worktree"])
        if not worktree.exists():
            raise ValueError(f"Worktree not found: {worktree}")
        run_dir = self.orchestrator.root / "runs" / task_id
        self._transition(task_id, state["status"], TaskStatus.IMPLEMENTING.value, None, "start_implementation")
        context = self.context_builder.build(task_id, state["goal"], state["risk"], [], project.protected_paths, worktree, project.repo_path)
        self.context_builder.persist(context, run_dir)
        self._transition(task_id, TaskStatus.IMPLEMENTING.value, TaskStatus.CONTEXT_READY.value, None, "context_ready")
        decision = self.router.route(state["risk"])
        if not decision.provider:
            wait = decision.wait_state or TaskStatus.WAITING_PROVIDER.value
            self._transition(task_id, TaskStatus.CONTEXT_READY.value, wait, None, "routing_failed", decision.reason)
            return ImplementationResult(False, None, [], "", "", decision.reason, {"routing_decision": decision.reason}, 0)
        result = self._execute_provider(task_id, state, project, worktree, context, decision)
        if not result.success or not self.verifier:
            return result
        verification = self._run_verification(task_id, state["risk"], run_dir)
        review: ReviewResult | None = None
        if self.reviewer:
            review = self._run_review(task_id, state["goal"], state["risk"], result.diff, verification, run_dir)
        needs_repair = verification.overall_status != "PASS" or (review is not None and review.verdict not in {ReviewVerdict.PASS, ReviewVerdict.PASS_WITH_NOTES, ReviewVerdict.NEEDS_HUMAN_DECISION})
        if needs_repair and self.repair_loop and review is not None:
            repaired = self.repair_loop.run(task_id, result.diff, verification, review)
            if not repaired.success:
                return ImplementationResult(False, result.provider_id, [], "", "", repaired.error, {"repair_rounds": repaired.rounds_completed}, result.duration_ms)
            gate = ScopeGate(worktree, [], project.protected_paths, project.repo_path)
            result.diff = gate.get_diff()
            result.diff_stat = gate.get_diff_stat()
            result.metadata = {**result.metadata, "repair_rounds": repaired.rounds_completed}
        elif verification.overall_status != "PASS":
            return result
        elif review is not None:
            if review.verdict in {ReviewVerdict.PASS, ReviewVerdict.PASS_WITH_NOTES}:
                self._transition(task_id, TaskStatus.REVIEWING.value, TaskStatus.READY_FOR_REVIEW.value, result.provider_id, "review_passed", review.verdict)
            elif review.verdict == ReviewVerdict.NEEDS_HUMAN_DECISION:
                self._transition(task_id, TaskStatus.REVIEWING.value, TaskStatus.WAITING_HUMAN.value, result.provider_id, "needs_human_decision", review.notes)
        if self.artifacts:
            final_state = self.orchestrator.state.get_task(task_id)["status"]
            self.artifacts.generate_final_report(task_id, state["goal"], state["risk"], final_state, result.provider_id, asdict(result), asdict(verification), asdict(review) if review else None, {"selected_files": [asdict(f) for f in context.selected_files], "total_bytes": sum(f.size for f in context.selected_files)})
        return result

    def _execute_provider(self, task_id: str, state: dict, project, worktree: Path, context, decision: RoutingDecision) -> ImplementationResult:
        provider = decision.provider
        assert provider is not None
        self._transition(task_id, TaskStatus.CONTEXT_READY.value, TaskStatus.IMPLEMENTING.value, provider.provider_id, "provider_selected", decision.reason, update_status=False)
        self.orchestrator.state.update_task(task_id, status=TaskStatus.IMPLEMENTING.value, provider_id=provider.provider_id)
        gate = ScopeGate(worktree, [], project.protected_paths, project.repo_path)
        gate.record_baseline()
        task_data = {"id": task_id, "goal": state["goal"], "risk": state["risk"], "allowed_scope": [], "forbidden_scope": project.protected_paths}
        context_data = {"selected_files": [asdict(f) for f in context.selected_files], "repo": asdict(context.repo), "task": asdict(context.task)}
        started = time.perf_counter()
        execution = provider.execute(task_data, context_data, worktree)
        elapsed = int((time.perf_counter() - started) * 1000)
        if not execution.success:
            failure = Exception(execution.error or "Unknown provider failure")
            availability = provider.classify_error(failure)
            wait = TaskStatus.WAITING_CODEX.value if availability == ProviderAvailability.QUOTA_EXHAUSTED and "codex" in provider.provider_id.lower() else TaskStatus.WAITING_PROVIDER.value
            self._transition(task_id, TaskStatus.IMPLEMENTING.value, wait, provider.provider_id, "implementation_failed", execution.error, elapsed)
            fallback = self.router.select_fallback(provider.provider_id, state["risk"], failure)
            if fallback.provider:
                return self._execute_fallback(task_id, state, project, worktree, context, fallback)
            self.orchestrator.state.update_task(task_id, status=fallback.wait_state or wait)
            return ImplementationResult(False, provider.provider_id, [], "", "", execution.error or "Implementation failed", execution.metadata or {}, elapsed)
        scope = gate.check()
        if not scope.passed:
            self._transition(task_id, TaskStatus.IMPLEMENTING.value, TaskStatus.FAILED.value, provider.provider_id, "scope_violation", str(scope.violations), elapsed)
            return ImplementationResult(False, provider.provider_id, [], "", "", f"Scope violation: {scope.violations}", {"violations": [asdict(v) for v in scope.violations]}, elapsed)
        diff = gate.get_diff()
        if not diff and not execution.modified_files:
            self._transition(task_id, TaskStatus.IMPLEMENTING.value, TaskStatus.FAILED.value, provider.provider_id, "no_changes", "No changes made by provider", elapsed)
            return ImplementationResult(False, provider.provider_id, [], "", "", "No changes made", {}, elapsed)
        result = ImplementationResult(True, provider.provider_id, scope.modified_files, diff, gate.get_diff_stat(), None, execution.metadata or {}, elapsed)
        self._transition(task_id, TaskStatus.IMPLEMENTING.value, TaskStatus.IMPLEMENTED.value, provider.provider_id, "implementation_complete", f"Modified {len(scope.modified_files)} files", elapsed)
        self._persist_implementation(result, task_id, self.orchestrator.root / "runs" / task_id)
        return result

    def _execute_fallback(self, task_id: str, state: dict, project, worktree: Path, context, decision: RoutingDecision) -> ImplementationResult:
        provider = decision.provider
        assert provider is not None
        gate = ScopeGate(worktree, [], project.protected_paths, project.repo_path)
        gate.record_baseline()
        self.orchestrator.state.update_task(task_id, status=TaskStatus.IMPLEMENTING.value, provider_id=provider.provider_id)
        task_data = {"id": task_id, "goal": state["goal"], "risk": state["risk"], "allowed_scope": [], "forbidden_scope": project.protected_paths}
        context_data = {"selected_files": [asdict(f) for f in context.selected_files], "repo": asdict(context.repo), "task": asdict(context.task)}
        started = time.perf_counter()
        execution = provider.execute(task_data, context_data, worktree)
        elapsed = int((time.perf_counter() - started) * 1000)
        if not execution.success:
            self._transition(task_id, TaskStatus.IMPLEMENTING.value, TaskStatus.FAILED.value, provider.provider_id, "fallback_failed", execution.error, elapsed)
            return ImplementationResult(False, provider.provider_id, [], "", "", execution.error or "Fallback failed", execution.metadata or {}, elapsed)
        scope = gate.check()
        if not scope.passed:
            self._transition(task_id, TaskStatus.IMPLEMENTING.value, TaskStatus.FAILED.value, provider.provider_id, "fallback_scope_violation", str(scope.violations), elapsed)
            return ImplementationResult(False, provider.provider_id, [], "", "", f"Scope violation: {scope.violations}", {"violations": [asdict(v) for v in scope.violations]}, elapsed)
        result = ImplementationResult(True, provider.provider_id, scope.modified_files, gate.get_diff(), gate.get_diff_stat(), None, execution.metadata or {}, elapsed)
        self._transition(task_id, TaskStatus.IMPLEMENTING.value, TaskStatus.IMPLEMENTED.value, provider.provider_id, "fallback_implementation_complete", f"Modified {len(scope.modified_files)} files", elapsed)
        self._persist_implementation(result, task_id, self.orchestrator.root / "runs" / task_id)
        return result

    def _run_verification(self, task_id: str, risk: str, run_dir: Path) -> VerificationReport:
        self._transition(task_id, TaskStatus.IMPLEMENTED.value, TaskStatus.VERIFYING.value, None, "start_verification")
        report = self.verifier.run(task_id, risk, [])
        self.verifier.persist(report, run_dir)
        self._transition(task_id, TaskStatus.VERIFYING.value, TaskStatus.REVIEWING.value, None, "verification_complete", report.overall_status)
        return report

    def _run_review(self, task_id: str, goal: str, risk: str, diff: str, verification: VerificationReport, run_dir: Path) -> ReviewResult:
        result = self.reviewer.review(task_id, goal, risk, diff, verification)
        self.reviewer.persist(result, run_dir)
        return result

    def _persist_implementation(self, result: ImplementationResult, task_id: str, run_dir: Path) -> None:
        run_dir.mkdir(parents=True, exist_ok=True)
        data = {"task_id": task_id, **asdict(result), "created_at": datetime.now(timezone.utc).isoformat()}
        (run_dir / "implementation.json").write_text(json.dumps(data, indent=2, default=str))

    def _transition(self, task_id: str, before: str | None, after: str, provider_id: str | None, action: str, result: str | None = None, duration_ms: int | None = None, update_status: bool = True) -> None:
        self.orchestrator.state.record_event(task_id, before, after, provider_id, action, result, duration_ms)
        if update_status:
            self.orchestrator.state.update_task(task_id, status=after)
        if self.artifacts:
            self.artifacts.record_event(task_id, before, after, provider_id, action, result, duration_ms)
