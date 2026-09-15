from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .verifier import VerificationReport
from .providers.base import ModelProvider, ProviderAvailability


class ReviewVerdict(str):
    PASS = "PASS"
    PASS_WITH_NOTES = "PASS_WITH_NOTES"
    FAIL_SCOPE = "FAIL_SCOPE"
    FAIL_CORRECTNESS = "FAIL_CORRECTNESS"
    FAIL_TESTS = "FAIL_TESTS"
    FAIL_ARCHITECTURE = "FAIL_ARCHITECTURE"
    FAIL_SECURITY = "FAIL_SECURITY"
    NEEDS_HUMAN_DECISION = "NEEDS_HUMAN_DECISION"


@dataclass(slots=True)
class ReviewResult:
    verdict: str
    notes: str
    details: dict[str, Any]
    reviewer_id: str
    created_at: str


class Reviewer:
    def __init__(self, reviewer_id: str = "mock-reviewer"):
        self.reviewer_id = reviewer_id

    def review(
        self,
        task_id: str,
        goal: str,
        risk: str,
        diff: str,
        verification_report: VerificationReport,
        context_summary: dict[str, Any] | None = None,
    ) -> ReviewResult:
        details = {
            "task_id": task_id,
            "risk": risk,
            "verification_status": verification_report.overall_status,
            "diff_stats": self._analyze_diff(diff),
        }

        if verification_report.overall_status != "PASS":
            if verification_report.scope_gate and verification_report.scope_gate.status != "PASS":
                verdict = ReviewVerdict.FAIL_SCOPE
                notes = "Scope gate failed: changes outside allowed scope or protected paths modified"
            elif verification_report.diff_check and verification_report.diff_check.status != "PASS":
                verdict = ReviewVerdict.FAIL_CORRECTNESS
                notes = "Diff check failed: whitespace or formatting issues"
            else:
                verdict = ReviewVerdict.FAIL_TESTS
                notes = "Verification commands failed"
        else:
            if risk == "critical":
                verdict = ReviewVerdict.NEEDS_HUMAN_DECISION
                notes = "Critical risk task requires human review"
            elif risk == "high":
                verdict = ReviewVerdict.PASS_WITH_NOTES
                notes = "High risk task passed verification, recommend human review"
            else:
                verdict = ReviewVerdict.PASS
                notes = "All verification gates passed"

        if diff and self._has_security_concerns(diff):
            if verdict in (ReviewVerdict.PASS, ReviewVerdict.PASS_WITH_NOTES):
                verdict = ReviewVerdict.FAIL_SECURITY
                notes = "Potential security issues detected in diff"

        return ReviewResult(
            verdict=verdict,
            notes=notes,
            details=details,
            reviewer_id=self.reviewer_id,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

    def _analyze_diff(self, diff: str) -> dict[str, Any]:
        if not diff:
            return {"files_changed": 0, "lines_added": 0, "lines_removed": 0}

        files_changed = 0
        lines_added = 0
        lines_removed = 0

        for line in diff.split("\n"):
            if line.startswith("+++") or line.startswith("---"):
                files_changed += 1
            elif line.startswith("+") and not line.startswith("+++"):
                lines_added += 1
            elif line.startswith("-") and not line.startswith("---"):
                lines_removed += 1

        return {
            "files_changed": files_changed // 2,
            "lines_added": lines_added,
            "lines_removed": lines_removed,
        }

    def _has_security_concerns(self, diff: str) -> bool:
        security_patterns = [
            "password",
            "secret",
            "token",
            "api_key",
            "private_key",
            "credential",
            "authorization",
        ]
        diff_lower = diff.lower()
        return any(pattern in diff_lower for pattern in security_patterns)

    def persist(self, result: ReviewResult, run_dir: Path) -> Path:
        run_dir.mkdir(parents=True, exist_ok=True)
        out_path = run_dir / "review.json"
        data = asdict(result)
        out_path.write_text(json.dumps(data, indent=2))
        return out_path


class ProviderReviewer(Reviewer):
    """Adapter that turns a read-only ModelProvider into the Reviewer interface.

    Deterministic verification remains authoritative: a provider can add review
    findings, but it cannot turn failed verification into PASS.
    """

    VALID_VERDICTS = {
        ReviewVerdict.PASS,
        ReviewVerdict.PASS_WITH_NOTES,
        ReviewVerdict.FAIL_SCOPE,
        ReviewVerdict.FAIL_CORRECTNESS,
        ReviewVerdict.FAIL_TESTS,
        ReviewVerdict.FAIL_ARCHITECTURE,
        ReviewVerdict.FAIL_SECURITY,
        ReviewVerdict.NEEDS_HUMAN_DECISION,
    }

    def __init__(self, provider: ModelProvider, worktree: Path):
        super().__init__(provider.provider_id)
        self.provider = provider
        self.worktree = Path(worktree).resolve()

    def review(
        self,
        task_id: str,
        goal: str,
        risk: str,
        diff: str,
        verification_report: VerificationReport,
        context_summary: dict[str, Any] | None = None,
    ) -> ReviewResult:
        deterministic = Reviewer(reviewer_id="deterministic-reviewer").review(
            task_id=task_id,
            goal=goal,
            risk=risk,
            diff=diff,
            verification_report=verification_report,
            context_summary=context_summary,
        )

        if self.provider.availability() != ProviderAvailability.AVAILABLE:
            return ReviewResult(
                verdict=deterministic.verdict if verification_report.overall_status != "PASS" else ReviewVerdict.NEEDS_HUMAN_DECISION,
                notes=f"AI reviewer unavailable ({self.provider.availability().value}); {deterministic.notes}",
                details={"review_error": "provider_unavailable", "deterministic": deterministic.details},
                reviewer_id=self.reviewer_id,
                created_at=datetime.now(timezone.utc).isoformat(),
            )

        task = {
            "id": task_id,
            "goal": goal,
            "risk": risk,
            "diff": diff,
        }
        context = {
            "verification_report": asdict(verification_report),
            "context_summary": context_summary or {},
        }
        result = self.provider.execute(task, context, self.worktree)

        if not result.success:
            return ReviewResult(
                verdict=deterministic.verdict if verification_report.overall_status != "PASS" else ReviewVerdict.NEEDS_HUMAN_DECISION,
                notes=f"AI reviewer failed: {result.error or 'unknown provider error'}; {deterministic.notes}",
                details={"review_error": "provider_execution_failed", "provider_metadata": result.metadata or {}},
                reviewer_id=self.reviewer_id,
                created_at=datetime.now(timezone.utc).isoformat(),
            )

        review_data = (result.metadata or {}).get("review")
        if not isinstance(review_data, dict):
            review_data = self._parse_json_output(result.output)

        verdict = review_data.get("verdict") if isinstance(review_data, dict) else None
        notes = review_data.get("notes") if isinstance(review_data, dict) else None
        details = review_data.get("details", {}) if isinstance(review_data, dict) else {}

        if verdict not in self.VALID_VERDICTS or not isinstance(notes, str):
            return ReviewResult(
                verdict=deterministic.verdict if verification_report.overall_status != "PASS" else ReviewVerdict.NEEDS_HUMAN_DECISION,
                notes=f"REVIEW_ERROR: malformed structured reviewer output; {deterministic.notes}",
                details={"review_error": "malformed_output", "raw_output": result.output[:2000]},
                reviewer_id=self.reviewer_id,
                created_at=datetime.now(timezone.utc).isoformat(),
            )

        if verification_report.overall_status != "PASS" and verdict in (ReviewVerdict.PASS, ReviewVerdict.PASS_WITH_NOTES):
            verdict = deterministic.verdict
            notes = f"AI verdict overridden by deterministic verification failure. {deterministic.notes}"
            details = {**details, "ai_verdict_overridden": True}
        if risk == "critical" and verdict in (ReviewVerdict.PASS, ReviewVerdict.PASS_WITH_NOTES):
            verdict = ReviewVerdict.NEEDS_HUMAN_DECISION
            notes = "Critical risk task requires human review"
            details = {**details, "critical_human_gate": True}

        return ReviewResult(
            verdict=verdict,
            notes=notes,
            details=details if isinstance(details, dict) else {},
            reviewer_id=self.reviewer_id,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

    @staticmethod
    def _parse_json_output(output: str) -> dict[str, Any] | None:
        text = (output or "").strip()
        if not text:
            return None
        try:
            data = json.loads(text)
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            pass
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                data = json.loads(line)
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                continue
        return None


class MockReviewer(Reviewer):
    def __init__(self, verdict: str = ReviewVerdict.PASS, notes: str = "Mock review passed"):
        super().__init__("mock-reviewer")
        self._forced_verdict = verdict
        self._forced_notes = notes

    def review(
        self,
        task_id: str,
        goal: str,
        risk: str,
        diff: str,
        verification_report: VerificationReport,
        context_summary: dict[str, Any] | None = None,
    ) -> ReviewResult:
        return ReviewResult(
            verdict=self._forced_verdict,
            notes=self._forced_notes,
            details={"forced": True, "task_id": task_id, "risk": risk},
            reviewer_id=self.reviewer_id,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
