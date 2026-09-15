from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from .reviewer import ReviewResult, ReviewVerdict, Reviewer
from .verifier import VerificationReport


_FAILURE_PRECEDENCE = (
    ReviewVerdict.FAIL_SECURITY,
    ReviewVerdict.FAIL_SCOPE,
    ReviewVerdict.FAIL_TESTS,
    ReviewVerdict.FAIL_CORRECTNESS,
    ReviewVerdict.FAIL_ARCHITECTURE,
)


class IndependentReviewPanel(Reviewer):
    """Aggregate independent reviewers without allowing model consensus to override gates.

    Free/local reviewers run for every task. Premium reviewers are reserved for
    high/critical risk tasks so the default path remains free-first. A
    deterministic reviewer is always included and remains authoritative when
    verification fails. Any independent failure is treated conservatively.
    Critical tasks always end at the human gate even when every reviewer passes.
    """

    def __init__(
        self,
        free_reviewers: list[Reviewer] | None = None,
        premium_reviewers: list[Reviewer] | None = None,
        deterministic_reviewer: Reviewer | None = None,
    ):
        super().__init__("independent-review-panel")
        self.free_reviewers = list(free_reviewers or [])
        self.premium_reviewers = list(premium_reviewers or [])
        self.deterministic_reviewer = deterministic_reviewer or Reviewer(
            reviewer_id="deterministic-reviewer"
        )

    def review(
        self,
        task_id: str,
        goal: str,
        risk: str,
        diff: str,
        verification_report: VerificationReport,
        context_summary: dict[str, Any] | None = None,
    ) -> ReviewResult:
        deterministic = self.deterministic_reviewer.review(
            task_id=task_id,
            goal=goal,
            risk=risk,
            diff=diff,
            verification_report=verification_report,
            context_summary=context_summary,
        )

        selected = list(self.free_reviewers)
        premium_used = risk in {"high", "critical"}
        if premium_used:
            selected.extend(self.premium_reviewers)

        independent_results: list[ReviewResult] = []
        for reviewer in selected:
            independent_results.append(
                reviewer.review(
                    task_id=task_id,
                    goal=goal,
                    risk=risk,
                    diff=diff,
                    verification_report=verification_report,
                    context_summary=context_summary,
                )
            )

        verdict, notes = self._aggregate(
            risk=risk,
            verification_report=verification_report,
            deterministic=deterministic,
            independent=independent_results,
        )
        details = {
            "policy": "independent-conservative-v1",
            "premium_reviewers_used": premium_used,
            "deterministic_review": asdict(deterministic),
            "independent_reviews": [asdict(result) for result in independent_results],
            "reviewer_count": 1 + len(independent_results),
        }
        return ReviewResult(
            verdict=verdict,
            notes=notes,
            details=details,
            reviewer_id=self.reviewer_id,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

    @staticmethod
    def _aggregate(
        risk: str,
        verification_report: VerificationReport,
        deterministic: ReviewResult,
        independent: list[ReviewResult],
    ) -> tuple[str, str]:
        # Deterministic verification is authoritative and cannot be voted away.
        if verification_report.overall_status != "PASS":
            return deterministic.verdict, (
                "Deterministic verification failed; independent model reviews cannot override it. "
                + deterministic.notes
            )

        verdicts = [result.verdict for result in independent]
        for failure in _FAILURE_PRECEDENCE:
            if failure in verdicts:
                reviewers = [r.reviewer_id for r in independent if r.verdict == failure]
                return failure, f"Independent review failure ({failure}) from: {', '.join(reviewers)}"

        if risk == "critical":
            return ReviewVerdict.NEEDS_HUMAN_DECISION, (
                "Critical risk task requires human review after independent review."
            )

        if ReviewVerdict.NEEDS_HUMAN_DECISION in verdicts:
            reviewers = [
                r.reviewer_id
                for r in independent
                if r.verdict == ReviewVerdict.NEEDS_HUMAN_DECISION
            ]
            return ReviewVerdict.NEEDS_HUMAN_DECISION, (
                "At least one independent reviewer requested human adjudication: "
                + ", ".join(reviewers)
            )

        if risk == "high":
            return ReviewVerdict.PASS_WITH_NOTES, (
                "Independent review passed; high-risk task remains flagged for human attention."
            )

        if ReviewVerdict.PASS_WITH_NOTES in verdicts:
            return ReviewVerdict.PASS_WITH_NOTES, "Independent review passed with notes."

        if independent:
            return ReviewVerdict.PASS, "Independent reviewers and deterministic gates passed."

        return deterministic.verdict, (
            "No AI reviewer was available; deterministic review result used. "
            + deterministic.notes
        )
