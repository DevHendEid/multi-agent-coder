from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class RunArtifacts:
    def __init__(self, run_dir: Path):
        self.run_dir = run_dir
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.events_path = self.run_dir / "events.jsonl"

    def record_event(
        self,
        task_id: str,
        state_before: str | None,
        state_after: str,
        provider_id: str | None,
        action: str,
        result: str | None = None,
        duration_ms: int | None = None,
    ) -> None:
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "task_id": task_id,
            "state_before": state_before,
            "state_after": state_after,
            "provider_id": provider_id,
            "action": action,
            "result": result,
            "duration_ms": duration_ms,
        }
        with self.events_path.open("a") as f:
            f.write(json.dumps(event) + "\n")

    def get_events(self) -> list[dict[str, Any]]:
        if not self.events_path.exists():
            return []
        events = []
        with self.events_path.open() as f:
            for line in f:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
        return events

    def generate_final_report(
        self,
        task_id: str,
        goal: str,
        risk: str,
        status: str,
        provider_id: str | None,
        implementation_result: dict[str, Any] | None,
        verification_report: dict[str, Any] | None,
        review_result: dict[str, Any] | None,
        context_summary: dict[str, Any] | None,
    ) -> Path:
        lines = [
            f"# Task Report: {task_id}",
            "",
            f"**Goal:** {goal}",
            f"**Risk:** {risk}",
            f"**Final Status:** {status}",
            f"**Provider:** {provider_id or 'N/A'}",
            f"**Generated:** {datetime.now(timezone.utc).isoformat()}",
            "",
            "## Summary",
            "",
        ]

        if implementation_result:
            lines.extend([
                "### Implementation",
                f"- Success: {implementation_result.get('success', False)}",
                f"- Provider: {implementation_result.get('provider_id', 'N/A')}",
                f"- Modified Files: {', '.join(implementation_result.get('modified_files', [])) or 'none'}",
                f"- Duration: {implementation_result.get('duration_ms', 0)}ms",
                "",
            ])

        if verification_report:
            lines.extend([
                "### Verification",
                f"- Overall: {verification_report.get('overall_status', 'UNKNOWN')}",
            ])
            if verification_report.get("scope_gate"):
                lines.append(f"- Scope Gate: {verification_report['scope_gate']['status']}")
            if verification_report.get("diff_check"):
                lines.append(f"- Diff Check: {verification_report['diff_check']['status']}")
            for cmd_res in verification_report.get("command_results", []):
                lines.append(f"- {cmd_res['command']}: {cmd_res['status']} ({cmd_res.get('duration_ms', 0)}ms)")
            lines.append("")

        if review_result:
            lines.extend([
                "### Review",
                f"- Verdict: {review_result.get('verdict', 'UNKNOWN')}",
                f"- Notes: {review_result.get('notes', 'N/A')}",
                f"- Reviewer: {review_result.get('reviewer_id', 'N/A')}",
                "",
            ])

        if context_summary:
            lines.extend([
                "### Context",
                f"- Files Selected: {len(context_summary.get('selected_files', []))}",
                f"- Total Context Size: {context_summary.get('total_bytes', 0)} bytes",
                "",
            ])

        lines.extend([
            "## Events Timeline",
            "",
            "| Time | State Before | State After | Provider | Action | Result | Duration |",
            "|------|--------------|-------------|----------|--------|--------|----------|",
        ])

        for event in self.get_events():
            lines.append(
                f"| {event['timestamp']} | {event['state_before'] or '-'} | "
                f"{event['state_after']} | {event['provider_id'] or '-'} | "
                f"{event['action']} | {event['result'] or '-'} | "
                f"{event['duration_ms'] or '-'}ms |"
            )

        report_path = self.run_dir / "final_report.md"
        report_path.write_text("\n".join(lines))
        return report_path