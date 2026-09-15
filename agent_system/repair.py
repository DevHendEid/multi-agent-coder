from __future__ import annotations

import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .context import ContextBuilder
from .models import TaskStatus
from .orchestrator import Orchestrator
from .providers import ProviderAvailability
from .router import ModelRouter
from .scope import ScopeGate
from .verifier import VerificationReport, Verifier
from .reviewer import Reviewer, ReviewResult, ReviewVerdict


@dataclass(slots=True)
class RepairResult:
    success: bool
    rounds_completed: int
    final_verification: VerificationReport | None
    final_review: ReviewResult | None
    error: str | None
    escalated: bool


class RepairLoop:
    def __init__(self, orchestrator: Orchestrator, router: ModelRouter, context_builder: ContextBuilder, verifier: Verifier, reviewer: Reviewer, max_free_attempts: int = 2, max_total_repair_rounds: int = 3):
        self.orchestrator = orchestrator
        self.router = router
        self.context_builder = context_builder
        self.verifier = verifier
        self.reviewer = reviewer
        self.max_free_attempts = max_free_attempts
        self.max_total_repair_rounds = max_total_repair_rounds

    def run(self, task_id: str, implementation_diff: str, verification_report: VerificationReport, review_result: ReviewResult) -> RepairResult:
        state = self.orchestrator.state.get_task(task_id)
        if not state:
            raise ValueError(f"Task not found: {task_id}")
        project = self.orchestrator._load_project(state["project"])
        worktree_path = Path(state["worktree"])
        original_repo = project.repo_path
        if not worktree_path.exists():
            raise ValueError(f"Worktree not found: {worktree_path}")
        checkpoint_dir = self._create_checkpoint(worktree_path)
        free_attempts_used = state.get("repair_attempts", 0)
        total_rounds = 0
        current_diff = implementation_diff
        current_verification = verification_report
        current_review = review_result

        while total_rounds < self.max_total_repair_rounds:
            if not self._should_repair(current_verification, current_review, state["risk"]):
                break
            if free_attempts_used >= self.max_free_attempts:
                self.orchestrator.state.update_task(task_id, status=TaskStatus.WAITING_HUMAN.value)
                return RepairResult(False, total_rounds, current_verification, current_review, "Max free repair attempts reached", True)

            self.orchestrator.state.update_task(task_id, status=TaskStatus.REPAIRING.value)
            repair_result = self._run_repair_round(task_id, state, project, worktree_path, original_repo, current_diff, current_verification, current_review)
            total_rounds += 1
            free_attempts_used = self.orchestrator.state.increment_repair_attempts(task_id)

            if not repair_result.success:
                self._restore_checkpoint(worktree_path, checkpoint_dir)
                self.orchestrator.state.update_task(task_id, status=TaskStatus.FAILED.value)
                return RepairResult(False, total_rounds, current_verification, current_review, repair_result.error, False)

            current_diff = repair_result.new_diff
            current_verification = self.verifier.run(task_id, state["risk"], [])
            self.verifier.persist(current_verification, self.orchestrator.root / "runs" / task_id)
            if current_verification.overall_status != "PASS":
                continue

            current_review = self.reviewer.review(task_id=task_id, goal=state["goal"], risk=state["risk"], diff=current_diff, verification_report=current_verification)
            self.reviewer.persist(current_review, self.orchestrator.root / "runs" / task_id)
            if current_review.verdict in (ReviewVerdict.FAIL_SCOPE, ReviewVerdict.FAIL_SECURITY):
                self._restore_checkpoint(worktree_path, checkpoint_dir)
                self.orchestrator.state.update_task(task_id, status=TaskStatus.FAILED.value)
                return RepairResult(False, total_rounds, current_verification, current_review, f"Review rejected: {current_review.verdict}", False)
            if current_review.verdict == ReviewVerdict.PASS:
                break

        if current_verification.overall_status == "PASS" and current_review.verdict in (ReviewVerdict.PASS, ReviewVerdict.PASS_WITH_NOTES):
            self.orchestrator.state.update_task(task_id, status=TaskStatus.READY_FOR_REVIEW.value)
            return RepairResult(True, total_rounds, current_verification, current_review, None, False)

        self.orchestrator.state.update_task(task_id, status=TaskStatus.WAITING_HUMAN.value)
        return RepairResult(False, total_rounds, current_verification, current_review, "Repair loop exhausted without resolution", True)

    def _should_repair(self, verification: VerificationReport, review: ReviewResult, risk: str) -> bool:
        if verification.overall_status != "PASS":
            return True
        if review.verdict in (ReviewVerdict.FAIL_SCOPE, ReviewVerdict.FAIL_CORRECTNESS, ReviewVerdict.FAIL_TESTS, ReviewVerdict.FAIL_ARCHITECTURE):
            return True
        if risk == "critical" and review.verdict == ReviewVerdict.NEEDS_HUMAN_DECISION:
            return False
        return False

    def _run_repair_round(self, task_id: str, state: dict, project, worktree_path: Path, original_repo: Path, current_diff: str, verification: VerificationReport, review: ReviewResult) -> "RepairRoundResult":
        provider_id = state.get("provider_id")
        if not provider_id:
            return RepairRoundResult(False, error="No provider ID in task state")
        provider = self.router.registry.get(provider_id)
        if not provider:
            return RepairRoundResult(False, error=f"Provider {provider_id} not found")
        if provider.availability() != ProviderAvailability.AVAILABLE:
            fallback = self.router.select_fallback(provider_id, state["risk"], Exception("Provider unavailable"))
            if fallback.provider:
                provider = fallback.provider
            else:
                return RepairRoundResult(False, error=f"Provider {provider_id} unavailable and no fallback")

        context = self.context_builder.build(task_id, state["goal"], state["risk"], [], project.protected_paths, worktree_path, original_repo)
        task_data = {
            "id": task_id,
            "goal": state["goal"],
            "risk": state["risk"],
            "allowed_scope": [],
            "forbidden_scope": project.protected_paths,
            "repair_context": {
                "previous_diff": current_diff,
                "verification_failures": self._extract_failures(verification),
                "review_verdict": review.verdict,
                "review_notes": review.notes,
            },
        }
        context_data = {"selected_files": [asdict(f) for f in context.selected_files], "repo": asdict(context.repo), "task": asdict(context.task)}
        scope_gate = ScopeGate(worktree_path, [], project.protected_paths, original_repo)
        scope_gate.record_baseline()
        result = provider.execute(task_data, context_data, worktree_path)
        if not result.success:
            return RepairRoundResult(False, error=result.error or "Repair execution failed")
        scope_check = scope_gate.check()
        if not scope_check.passed:
            return RepairRoundResult(False, error=f"Scope violation during repair: {scope_check.violations}")
        new_diff = scope_gate.get_diff()
        if self._repair_made_things_worse(current_diff, new_diff, scope_check.modified_files, project.protected_paths):
            return RepairRoundResult(False, error="Repair made things worse")
        return RepairRoundResult(True, new_diff=new_diff)

    def _extract_failures(self, verification: VerificationReport) -> list[dict[str, Any]]:
        failures = []
        for r in [verification.scope_gate, verification.diff_check] + verification.command_results:
            if r and r.status != "PASS":
                failures.append({"command": r.command, "status": r.status, "exit_code": r.exit_code, "stderr": r.stderr})
        return failures

    def _repair_made_things_worse(self, old_diff: str, new_diff: str, modified_files: list[str], protected_paths: list[str]) -> bool:
        new_files = set()
        for line in new_diff.split("\n"):
            if line.startswith("+++") and not line.startswith("++++"):
                new_files.add(line[4:].strip())
            elif line.startswith("---") and not line.startswith("----"):
                new_files.add(line[4:].strip())
        for f in new_files:
            for prot in protected_paths:
                if f.startswith(prot.rstrip("/")):
                    return True
        old_added = old_diff.count("\n+") - old_diff.count("\n+++")
        new_added = new_diff.count("\n+") - new_diff.count("\n+++")
        return new_added > old_added * 3 if old_added else False

    def _create_checkpoint(self, worktree_path: Path) -> Path:
        checkpoint_dir = worktree_path.parent / f".checkpoint-{worktree_path.name}"
        if checkpoint_dir.exists():
            shutil.rmtree(checkpoint_dir)
        shutil.copytree(worktree_path, checkpoint_dir)
        return checkpoint_dir

    def _restore_checkpoint(self, worktree_path: Path, checkpoint_dir: Path) -> None:
        if checkpoint_dir.exists():
            shutil.rmtree(worktree_path)
            shutil.copytree(checkpoint_dir, worktree_path)


@dataclass(slots=True)
class RepairRoundResult:
    success: bool
    new_diff: str = ""
    error: str | None = None
