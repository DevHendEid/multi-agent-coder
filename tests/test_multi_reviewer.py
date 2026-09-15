from __future__ import annotations

from datetime import datetime, timezone

from agent_system.multi_reviewer import IndependentReviewPanel
from agent_system.reviewer import ReviewResult, ReviewVerdict, Reviewer
from agent_system.verifier import VerificationReport, VerificationResult


def report(status: str = "PASS") -> VerificationReport:
    scope = VerificationResult("scope_gate", ".", 0 if status == "PASS" else 1, "", "", 1, False, status)
    diff = VerificationResult("git diff --check", ".", 0, "", "", 1, False, "PASS")
    return VerificationReport("TASK-1", "medium", scope, diff, [], status, datetime.now(timezone.utc).isoformat())


class StubReviewer(Reviewer):
    def __init__(self, reviewer_id: str, verdict: str):
        super().__init__(reviewer_id)
        self.verdict = verdict
        self.calls = 0

    def review(self, task_id, goal, risk, diff, verification_report, context_summary=None):
        self.calls += 1
        return ReviewResult(
            verdict=self.verdict,
            notes=f"{self.reviewer_id}:{self.verdict}",
            details={},
            reviewer_id=self.reviewer_id,
            created_at=datetime.now(timezone.utc).isoformat(),
        )


def test_low_risk_runs_free_reviewers_but_skips_premium():
    free = StubReviewer("free-a", ReviewVerdict.PASS)
    premium = StubReviewer("premium-a", ReviewVerdict.FAIL_CORRECTNESS)
    panel = IndependentReviewPanel([free], [premium])

    result = panel.review("TASK-1", "goal", "low", "diff", report())

    assert result.verdict == ReviewVerdict.PASS
    assert free.calls == 1
    assert premium.calls == 0
    assert result.details["premium_reviewers_used"] is False


def test_high_risk_uses_premium_reviewer_and_failure_wins():
    free = StubReviewer("free-a", ReviewVerdict.PASS)
    premium = StubReviewer("premium-a", ReviewVerdict.FAIL_ARCHITECTURE)
    panel = IndependentReviewPanel([free], [premium])

    result = panel.review("TASK-1", "goal", "high", "diff", report())

    assert premium.calls == 1
    assert result.verdict == ReviewVerdict.FAIL_ARCHITECTURE
    assert result.details["premium_reviewers_used"] is True


def test_deterministic_failure_cannot_be_overridden_by_reviewers():
    free = StubReviewer("free-a", ReviewVerdict.PASS)
    premium = StubReviewer("premium-a", ReviewVerdict.PASS)
    panel = IndependentReviewPanel([free], [premium])

    failed = report("FAIL")
    result = panel.review("TASK-1", "goal", "high", "diff", failed)

    assert result.verdict == ReviewVerdict.FAIL_SCOPE
    assert "cannot override" in result.notes


def test_critical_always_requires_human_after_all_reviews_pass():
    free = StubReviewer("free-a", ReviewVerdict.PASS)
    premium = StubReviewer("premium-a", ReviewVerdict.PASS)
    panel = IndependentReviewPanel([free], [premium])

    result = panel.review("TASK-1", "goal", "critical", "diff", report())

    assert free.calls == 1
    assert premium.calls == 1
    assert result.verdict == ReviewVerdict.NEEDS_HUMAN_DECISION


def test_security_failure_has_highest_failure_precedence():
    a = StubReviewer("a", ReviewVerdict.FAIL_CORRECTNESS)
    b = StubReviewer("b", ReviewVerdict.FAIL_SECURITY)
    panel = IndependentReviewPanel([a, b], [])

    result = panel.review("TASK-1", "goal", "medium", "diff", report())

    assert result.verdict == ReviewVerdict.FAIL_SECURITY
    assert result.details["reviewer_count"] == 3
