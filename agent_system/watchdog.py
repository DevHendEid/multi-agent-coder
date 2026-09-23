from __future__ import annotations

import json
import sqlite3
import subprocess
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .governance import (
    ALLOWED_DISPOSITIONS,
    AlertRecord,
    GovernanceRegistry,
    ProjectHealthMetrics,
    PromotionQueueItem,
)

class IntegrationWatchdog:
    """Deterministic governance watchdog enforcing continuous lifecycle rules."""

    def __init__(self, root: Path | str):
        self.root = Path(root).resolve()
        self.registry = GovernanceRegistry(self.root)
        self.state_db_path = self.root / ".agent-system" / "state.db"

    def run(self, persist: bool = True) -> list[AlertRecord]:
        """Scan governance state. With persist=False, registries are only updated in memory."""
        self.registry.load()
        active_findings: list[AlertRecord] = []

        # 1. Inspect Governance Assets
        active_findings.extend(self._check_assets())

        # 2. Inspect Validations & Evidence
        active_findings.extend(self._check_validations())

        # 3. Inspect Runtime Tasks & State
        active_findings.extend(self._check_runtime_tasks())

        # 4. Inspect Stale Worktrees
        active_findings.extend(self._check_worktrees())

        # 5. Idempotently update alerts registry
        now_iso = datetime.now(timezone.utc).isoformat()
        active_ids = {a.alert_id for a in active_findings}

        # Keep existing alerts, update active ones, resolve cleared ones
        for finding in active_findings:
            if finding.alert_id in self.registry.alerts:
                existing = self.registry.alerts[finding.alert_id]
                existing.current_state = finding.current_state
                existing.evidence = finding.evidence
                existing.message = finding.message
                existing.resolved = False
                existing.resolution = None
                existing.resolved_at = None
            else:
                self.registry.alerts[finding.alert_id] = finding

        for alert_id, alert in self.registry.alerts.items():
            if alert_id not in active_ids and not alert.resolved:
                alert.resolved = True
                alert.resolved_at = now_iso
                alert.resolution = "Condition resolved; underlying defect cleared"

        # 6. Update Promotion Queue
        self._sync_promotion_queue(now_iso)

        # 7. Update Project Health Metrics
        self._calculate_health_metrics(now_iso)

        # 8. Persist registries (explicit watchdog runs only)
        if persist:
            self.registry.save()

        return [a for a in self.registry.alerts.values() if not a.resolved]

    def _check_assets(self) -> list[AlertRecord]:
        findings: list[AlertRecord] = []
        now_iso = datetime.now(timezone.utc).isoformat()

        for asset in self.registry.assets.values():
            has_consumer = bool(asset.consumer and asset.consumer.strip() and asset.consumer.lower() not in {"none", "no consumer", "unspecified"})
            is_validated = asset.validation_status.upper() == "VALIDATED"
            is_production_candidate = asset.disposition not in {"KEEP_SHADOW", "DEFER_WITH_BLOCKER", "ARCHIVE"}

            # A. VALIDATED_ORPHAN (BLOCKING)
            if is_validated and not has_consumer and is_production_candidate:
                findings.append(AlertRecord(
                    alert_id=f"ALERT-ORPHAN-{asset.asset_id}",
                    finding_code="VALIDATED_ORPHAN",
                    severity="BLOCKING",
                    asset_id=asset.asset_id,
                    task_id=None,
                    title=f"Validated asset {asset.asset_id} is orphaned without consumer",
                    message=f"Asset '{asset.name}' is marked VALIDATED but has no consumer and no explicit non-production disposition.",
                    evidence=f"validation_status={asset.validation_status}, consumer='{asset.consumer}', disposition='{asset.disposition}'",
                    detected_at=now_iso,
                    current_state=f"VALIDATED with disposition {asset.disposition}",
                    required_action="Assign a valid consumer or set disposition to KEEP_SHADOW, DEFER_WITH_BLOCKER, or ARCHIVE.",
                    suggested_consumer=None,
                    blocker=asset.blockers[0] if asset.blockers else None,
                    allowed_dispositions=["KEEP_SHADOW", "DEFER_WITH_BLOCKER", "ARCHIVE"],
                    source_paths=[asset.implementation_path],
                ))

            # B. DISCONNECTED_REUSABLE (HIGH)
            elif asset.engineering_status in {"IMPLEMENTED", "TESTED", "STABLE"} and not has_consumer and is_production_candidate:
                findings.append(AlertRecord(
                    alert_id=f"ALERT-DISCONNECTED-{asset.asset_id}",
                    finding_code="DISCONNECTED_REUSABLE",
                    severity="HIGH",
                    asset_id=asset.asset_id,
                    task_id=None,
                    title=f"Reusable code {asset.asset_id} is disconnected",
                    message=f"Asset '{asset.name}' is {asset.engineering_status} but has no registered caller/consumer.",
                    evidence=f"engineering_status={asset.engineering_status}, consumer='{asset.consumer}'",
                    detected_at=now_iso,
                    current_state=f"engineering_status={asset.engineering_status}",
                    required_action="Connect to a consumer or classify under explicit shadow/deferred disposition.",
                    suggested_consumer=None,
                    blocker=asset.blockers[0] if asset.blockers else None,
                    allowed_dispositions=["INTEGRATE", "KEEP_SHADOW", "DEFER_WITH_BLOCKER", "ARCHIVE"],
                    source_paths=[asset.implementation_path],
                ))

            # C. SHADOW_NO_PROMOTION_PATH (WARNING or HIGH)
            if (asset.integration_status == "SHADOW_CONNECTED" or asset.asset_type == "SHADOW" or asset.disposition == "KEEP_SHADOW"):
                if not asset.promotion_criteria and not asset.blockers:
                    findings.append(AlertRecord(
                        alert_id=f"ALERT-SHADOW-PROMOTION-{asset.asset_id}",
                        finding_code="SHADOW_NO_PROMOTION_PATH",
                        severity="HIGH",
                        asset_id=asset.asset_id,
                        task_id=None,
                        title=f"Shadow asset {asset.asset_id} has no promotion path",
                        message=f"Asset '{asset.name}' is kept in shadow mode without defined promotion criteria or blockers.",
                        evidence=f"disposition={asset.disposition}, promotion_criteria='{asset.promotion_criteria}', blockers={asset.blockers}",
                        detected_at=now_iso,
                        current_state=f"disposition={asset.disposition}",
                        required_action="Define explicit promotion criteria or documented blocker.",
                        allowed_dispositions=["KEEP_SHADOW", "DEFER_WITH_BLOCKER", "ARCHIVE"],
                        source_paths=[asset.implementation_path],
                    ))

            # E. UNCLASSIFIED_TOOL (WARNING)
            if asset.asset_type in {"TOOL", "STANDALONE_TOOL", "UTILITY"}:
                classified = any(c in asset.purpose.lower() or c in asset.component.lower() for c in [
                    "recurring ci", "verification", "research tool", "development utility", "archived", "diagnostic", "recovery"
                ])
                if not classified:
                    findings.append(AlertRecord(
                        alert_id=f"ALERT-TOOL-{asset.asset_id}",
                        finding_code="UNCLASSIFIED_TOOL",
                        severity="WARNING",
                        asset_id=asset.asset_id,
                        task_id=None,
                        title=f"Standalone tool {asset.asset_id} is unclassified",
                        message=f"Tool '{asset.name}' is not classified under recognized operational roles.",
                        evidence=f"asset_type={asset.asset_type}, purpose='{asset.purpose}'",
                        detected_at=now_iso,
                        current_state=f"asset_type={asset.asset_type}",
                        required_action="Classify as CI/verification tool, reusable research tool, dev utility, or archive.",
                        allowed_dispositions=["INTEGRATE", "ARCHIVE"],
                        source_paths=[asset.implementation_path],
                    ))

            # F. POC_NO_DISPOSITION (HIGH)
            if asset.asset_type in {"POC", "PROOF_OF_CONCEPT"} or "poc" in asset.name.lower():
                if not asset.next_action or asset.disposition not in ALLOWED_DISPOSITIONS:
                    findings.append(AlertRecord(
                        alert_id=f"ALERT-POC-{asset.asset_id}",
                        finding_code="POC_NO_DISPOSITION",
                        severity="HIGH",
                        asset_id=asset.asset_id,
                        task_id=None,
                        title=f"Proof of concept {asset.asset_id} has no disposition",
                        message=f"POC '{asset.name}' lacks an explicit promote/replace/defer/archive decision.",
                        evidence=f"asset_type={asset.asset_type}, next_action='{asset.next_action}', disposition='{asset.disposition}'",
                        detected_at=now_iso,
                        current_state="POC without disposition",
                        required_action="Assign explicit next action and lifecycle disposition.",
                        allowed_dispositions=list(ALLOWED_DISPOSITIONS),
                        source_paths=[asset.implementation_path],
                    ))

            # G. STALE_SUPERSEDED_REFERENCE (WARNING / HIGH)
            if asset.superseded_by != "NONE":
                if asset.engineering_status in {"PRODUCTION_ACTIVE", "MERGED_TO_MAIN"} and asset.production_reachable:
                    findings.append(AlertRecord(
                        alert_id=f"ALERT-SUPERSEDED-{asset.asset_id}",
                        finding_code="STALE_SUPERSEDED_REFERENCE",
                        severity="HIGH",
                        asset_id=asset.asset_id,
                        task_id=None,
                        title=f"Superseded asset {asset.asset_id} remains active in production",
                        message=f"Asset '{asset.name}' is superseded by {asset.superseded_by} but is still marked production reachable.",
                        evidence=f"superseded_by={asset.superseded_by}, production_reachable={asset.production_reachable}",
                        detected_at=now_iso,
                        current_state="SUPERSEDED but PRODUCTION_REACHABLE",
                        required_action="Archive asset or replace production imports with successor.",
                        allowed_dispositions=["ARCHIVE"],
                        source_paths=[asset.implementation_path],
                    ))

            # H. RESEARCH_CODE_PRODUCTION_REACHABLE (BLOCKING)
            is_research = (
                asset.scientific_status.upper() == "RESEARCH_ONLY"
                or "RESEARCH" in asset.asset_type.upper()
            )
            has_gateway = bool(
                asset.gateway_reachability
                and any(w in asset.gateway_reachability.lower() for w in ["gateway", "adapter", "interface", "mediat"])
            )
            if is_research and asset.production_reachable and not has_gateway:
                findings.append(AlertRecord(
                    alert_id=f"ALERT-RESEARCH-PROD-{asset.asset_id}",
                    finding_code="RESEARCH_CODE_PRODUCTION_REACHABLE",
                    severity="BLOCKING",
                    asset_id=asset.asset_id,
                    task_id=None,
                    title=f"Research asset {asset.asset_id} is production reachable without gateway",
                    message=f"Asset '{asset.name}' is designated research but marked production reachable without an explicit gateway/interface.",
                    evidence=f"scientific_status={asset.scientific_status}, asset_type={asset.asset_type}, gateway_reachability={asset.gateway_reachability}",
                    detected_at=now_iso,
                    current_state="Research code marked production_reachable without gateway",
                    required_action="Disconnect asset from production import paths or mediate behind an explicit gateway/interface.",
                    allowed_dispositions=["KEEP_SHADOW", "ARCHIVE"],
                    source_paths=[asset.implementation_path],
                ))

            # I. MISSING_VALIDATION_RECEIPT (BLOCKING)
            if (asset.production_reachable or asset.validation_status.upper() == "VALIDATED") and asset.engineering_status in {"PRODUCTION_ACTIVE", "MERGED_TO_MAIN", "IMPLEMENTED", "TESTED", "STABLE"}:
                if not asset.validation_receipts:
                    findings.append(AlertRecord(
                        alert_id=f"ALERT-RECEIPT-{asset.asset_id}",
                        finding_code="MISSING_VALIDATION_RECEIPT",
                        severity="BLOCKING",
                        asset_id=asset.asset_id,
                        task_id=None,
                        title=f"Validated/Production asset {asset.asset_id} has no validation receipt",
                        message=f"Asset '{asset.name}' is marked validated or production reachable but lists no validation receipts.",
                        evidence=f"validation_status={asset.validation_status}, production_reachable={asset.production_reachable}, validation_receipts={asset.validation_receipts}",
                        detected_at=now_iso,
                        current_state="Validated or production asset without receipt",
                        required_action="Link verified validation receipt in ASSET_REGISTRY.json.",
                        allowed_dispositions=["INTEGRATE"],
                        source_paths=[asset.implementation_path],
                    ))

        return findings

    def _check_validations(self) -> list[AlertRecord]:
        findings: list[AlertRecord] = []
        now_iso = datetime.now(timezone.utc).isoformat()
        known_asset_ids = set(self.registry.assets.keys())

        # D. UNLINKED_EVIDENCE (WARNING)
        for val in self.registry.validations.values():
            if val.target_asset_id not in known_asset_ids:
                findings.append(AlertRecord(
                    alert_id=f"ALERT-UNLINKED-VAL-{val.validation_id}",
                    finding_code="UNLINKED_EVIDENCE",
                    severity="WARNING",
                    asset_id=val.target_asset_id,
                    task_id=None,
                    title=f"Validation evidence {val.validation_id} targets unknown asset {val.target_asset_id}",
                    message=f"Validation record '{val.suite_name}' references asset {val.target_asset_id} which does not exist in ASSET_REGISTRY.",
                    evidence=f"target_asset_id={val.target_asset_id}",
                    detected_at=now_iso,
                    current_state="Unlinked validation",
                    required_action="Register target asset or reconcile validation record.",
                    source_paths=[val.evidence_path],
                ))

        return findings

    def _check_runtime_tasks(self) -> list[AlertRecord]:
        findings: list[AlertRecord] = []
        now_iso = datetime.now(timezone.utc).isoformat()
        if not self.state_db_path.exists():
            return findings

        try:
            tasks = self._read_tasks_readonly()
        except Exception as exc:
            # Fail closed: an unreadable state DB hides task lifecycle defects.
            return [AlertRecord(
                alert_id="ALERT-STATE-DB-UNREADABLE",
                finding_code="STATE_DB_UNREADABLE",
                severity="BLOCKING",
                asset_id=None,
                task_id=None,
                title="Runtime state database cannot be read",
                message=f"Watchdog could not open or read {self.state_db_path}; runtime task invariants cannot be verified.",
                evidence=f"{type(exc).__name__}: {exc}",
                detected_at=now_iso,
                current_state="State DB unreadable",
                required_action="Restore or repair .agent-system/state.db (or remove it if no tasks exist), then re-run the watchdog.",
                source_paths=[str(self.state_db_path)],
            )]

        for task in tasks:
            task_id = task["id"]
            status = task.get("status")
            prod_target = bool(task.get("production_target", 1))

            # J. FALSE_COMPLETION (BLOCKING)
            if status == "COMPLETED" and prod_target:
                receipt_file = self.root / "runs" / task_id / "integration_receipt.json"
                merge_sha = task.get("merge_sha")
                if not receipt_file.exists() or not merge_sha:
                    findings.append(AlertRecord(
                        alert_id=f"ALERT-FALSE-COMPLETION-{task_id}",
                        finding_code="FALSE_COMPLETION",
                        severity="BLOCKING",
                        asset_id=None,
                        task_id=task_id,
                        title=f"Task {task_id} marked COMPLETED without verified integration",
                        message=f"Production-targeted task {task_id} is COMPLETED but missing integration receipt or merge SHA.",
                        evidence=f"receipt_exists={receipt_file.exists()}, merge_sha={merge_sha}",
                        detected_at=now_iso,
                        current_state="COMPLETED without verified receipt",
                        required_action="Generate integration receipt and confirm merge reachability on main.",
                        source_paths=[str(receipt_file)],
                    ))

            # K. READY_FOR_PR_WITHOUT_PR (HIGH)
            if status == "READY_FOR_PR" and prod_target:
                if not task.get("pr_number") and not task.get("pr_url") and not task.get("blocker"):
                    findings.append(AlertRecord(
                        alert_id=f"ALERT-PR-ORPHAN-{task_id}",
                        finding_code="READY_FOR_PR_WITHOUT_PR",
                        severity="HIGH",
                        asset_id=None,
                        task_id=task_id,
                        title=f"Task {task_id} in READY_FOR_PR has no pull request",
                        message=f"Task {task_id} is ready for integration but has no opened PR and no explicit blocker.",
                        evidence=f"status=READY_FOR_PR, pr_number={task.get('pr_number')}",
                        detected_at=now_iso,
                        current_state="READY_FOR_PR without PR",
                        required_action="Open pull request via agent-pr or record technical blocker.",
                        source_paths=[],
                    ))

        return findings

    def _read_tasks_readonly(self) -> list[dict[str, Any]]:
        # Open read-only so the watchdog never creates or migrates the state DB.
        con = sqlite3.connect(f"{self.state_db_path.as_uri()}?mode=ro", uri=True, timeout=30.0)
        try:
            con.row_factory = sqlite3.Row
            rows = con.execute("SELECT * FROM tasks ORDER BY created_at DESC").fetchall()
        finally:
            con.close()
        return [dict(r) for r in rows]

    def _check_worktrees(self) -> list[AlertRecord]:
        findings: list[AlertRecord] = []
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            wt_proc = subprocess.run(["git", "worktree", "list", "--porcelain"], cwd=str(self.root), capture_output=True, text=True)
            if wt_proc.returncode != 0:
                return findings

            current_wt: str | None = None
            for line in wt_proc.stdout.splitlines():
                if line.startswith("worktree "):
                    current_wt = line.split(" ", 1)[1]
                elif line == "detached":
                    if current_wt and current_wt != str(self.root):
                        findings.append(AlertRecord(
                            alert_id=f"ALERT-STALE-WT-{Path(current_wt).name}",
                            finding_code="STALE_EXECUTION_ASSET",
                            severity="WARNING",
                            asset_id=None,
                            task_id=Path(current_wt).name,
                            title=f"Stale detached worktree {Path(current_wt).name}",
                            message=f"Detached worktree exists at {current_wt} without active execution.",
                            evidence=f"worktree_path={current_wt}",
                            detected_at=now_iso,
                            current_state="Detached worktree",
                            required_action="Prune worktree after confirming no uncommitted changes.",
                            source_paths=[current_wt],
                        ))
        except Exception:
            pass

        return findings

    def _sync_promotion_queue(self, now_iso: str) -> None:
        active_queue_asset_ids = set()
        for alert in self.registry.alerts.values():
            if alert.resolved:
                continue
            if alert.finding_code in {"VALIDATED_ORPHAN", "DISCONNECTED_REUSABLE", "SHADOW_NO_PROMOTION_PATH", "POC_NO_DISPOSITION", "READY_FOR_PR_WITHOUT_PR"}:
                queue_id = f"QUEUE-{alert.asset_id or alert.task_id}"
                active_queue_asset_ids.add(queue_id)
                if queue_id in self.registry.promotion_queue:
                    item = self.registry.promotion_queue[queue_id]
                    item.severity = alert.severity
                    item.issue = alert.title
                    item.required_action = alert.required_action
                    item.updated_at = now_iso
                    item.status = "ACTIVE"
                else:
                    self.registry.promotion_queue[queue_id] = PromotionQueueItem(
                        queue_id=queue_id,
                        asset_id=alert.asset_id or "UNASSIGNED",
                        task_id=alert.task_id,
                        issue=alert.title,
                        severity=alert.severity,
                        required_action=alert.required_action,
                        suggested_consumer=alert.suggested_consumer,
                        blocker=alert.blocker,
                        allowed_dispositions=alert.allowed_dispositions,
                        created_at=now_iso,
                        updated_at=now_iso,
                        status="ACTIVE",
                        source_alert_id=alert.alert_id,
                    )

        # Mark resolved queue items
        for q_id, q_item in self.registry.promotion_queue.items():
            if q_id not in active_queue_asset_ids and q_item.status == "ACTIVE":
                q_item.status = "RESOLVED"
                q_item.updated_at = now_iso

    def _calculate_health_metrics(self, now_iso: str) -> None:
        assets = list(self.registry.assets.values())
        active_alerts = [a for a in self.registry.alerts.values() if not a.resolved]

        total = len(assets)
        prod_connected = sum(1 for a in assets if a.integration_status == "PRODUCTION_ACTIVE" and a.production_reachable)
        shadow_connected = sum(1 for a in assets if a.disposition == "KEEP_SHADOW")
        validated = sum(1 for a in assets if a.validation_status.upper() == "VALIDATED")
        promotion_pending = sum(1 for a in self.registry.promotion_queue.values() if a.status == "ACTIVE")
        deferred = sum(1 for a in assets if a.disposition == "DEFER_WITH_BLOCKER")
        archived = sum(1 for a in assets if a.disposition == "ARCHIVE")
        orphan_count = sum(1 for a in active_alerts if a.finding_code in {"VALIDATED_ORPHAN", "DISCONNECTED_REUSABLE"})
        validated_orphan_count = sum(1 for a in active_alerts if a.finding_code == "VALIDATED_ORPHAN")
        stale_superseded = sum(1 for a in active_alerts if a.finding_code == "STALE_SUPERSEDED_REFERENCE")
        missing_consumers = sum(1 for a in assets if not a.consumer or a.consumer.strip().lower() in {"none", "no consumer"})
        missing_receipts = sum(1 for a in active_alerts if a.finding_code == "MISSING_VALIDATION_RECEIPT")
        blocking_alerts = sum(1 for a in active_alerts if a.severity == "BLOCKING")
        high_alerts = sum(1 for a in active_alerts if a.severity == "HIGH")

        self.registry.health_metrics = ProjectHealthMetrics(
            total_assets=total,
            production_connected_assets=prod_connected,
            shadow_connected_assets=shadow_connected,
            validated_assets=validated,
            promotion_pending_assets=promotion_pending,
            deferred_assets=deferred,
            archived_assets=archived,
            orphan_count=orphan_count,
            validated_orphan_count=validated_orphan_count,
            stale_superseded_references=stale_superseded,
            assets_missing_consumers=missing_consumers,
            assets_missing_validation_receipts=missing_receipts,
            unresolved_blocking_alerts=blocking_alerts,
            unresolved_high_alerts=high_alerts,
            calculated_at=now_iso,
        )

    def check_phase_closeout(self, persist: bool = False) -> tuple[bool, list[str]]:
        """Verify that the repository state satisfies phase closeout invariants."""
        unresolved = self.run(persist=persist)
        failures: list[str] = []

        metrics = self.registry.health_metrics
        if metrics and metrics.validated_orphan_count > 0:
            failures.append(f"validated_orphan_count is {metrics.validated_orphan_count} (target: 0)")

        blocking_alerts = [a for a in unresolved if a.severity == "BLOCKING"]
        if blocking_alerts:
            for b in blocking_alerts:
                failures.append(f"Unresolved BLOCKING alert: [{b.finding_code}] {b.title}")

        return (len(failures) == 0, failures)

    def generate_dashboard(self, persist: bool = False) -> str:
        """Generate human-readable Markdown integration dashboard from registries."""
        self.run(persist=persist)
        lines: list[str] = []
        lines.append("# Multi-Agent Coder — Integration Dashboard")
        lines.append("")
        metrics = self.registry.health_metrics
        if metrics:
            lines.append("## Health Metrics")
            lines.append(f"- **Total Tracked Assets**: {metrics.total_assets}")
            lines.append(f"- **Production Connected**: {metrics.production_connected_assets}")
            lines.append(f"- **Validated Orphan Count**: `{metrics.validated_orphan_count}` (Target: 0)")
            lines.append(f"- **Active Blocking Alerts**: `{metrics.unresolved_blocking_alerts}`")
            lines.append(f"- **Active High Alerts**: `{metrics.unresolved_high_alerts}`")
            lines.append(f"- **Promotion Queue Pending**: {metrics.promotion_pending_assets}")
            lines.append(f"- **Calculated At**: {metrics.calculated_at}")
            lines.append("")

        lines.append("## Asset Lifecycle Matrix")
        lines.append("")
        lines.append("| Badge | Asset | Component | Engineering | Validation | Integration | Consumer | Blocker | Next Action |")
        lines.append("|---|---|---|---|---|---|---|---|---|")

        for a in sorted(self.registry.assets.values(), key=lambda x: x.asset_id):
            if a.disposition == "ARCHIVE":
                badge = "⚪ Archived"
            elif a.scientific_status == "RESEARCH_ONLY":
                badge = "🟣 Research Only"
            elif a.asset_type in {"TOOL", "STANDALONE_TOOL"}:
                badge = "🛠 Standalone Tool"
            elif a.disposition == "KEEP_SHADOW":
                badge = "🔵 Shadow Connected"
            elif a.disposition == "DEFER_WITH_BLOCKER" or a.blockers:
                badge = "🔴 Orphan / Blocked"
            elif a.validation_status.upper() != "VALIDATED":
                badge = "🟡 Validation Pending"
            elif a.integration_status == "PRODUCTION_ACTIVE":
                badge = "✅ Production Connected"
            else:
                badge = "🟠 Validated / Promotion Pending"

            blocker_str = a.blockers[0] if a.blockers else "NONE"
            consumer_str = a.consumer if a.consumer else "NONE"
            action_str = a.next_action if a.next_action else "NONE"
            lines.append(f"| {badge} | `{a.asset_id}` {a.name} | {a.component} | {a.engineering_status} | {a.validation_status} | {a.integration_status} | {consumer_str} | {blocker_str} | {action_str} |")

        lines.append("")
        lines.append("## Active Alerts")
        active_alerts = [a for a in self.registry.alerts.values() if not a.resolved]
        if not active_alerts:
            lines.append("Zero active alerts. All lifecycle invariants upheld.")
        else:
            for alert in sorted(active_alerts, key=lambda x: (0 if x.severity == 'BLOCKING' else 1 if x.severity == 'HIGH' else 2)):
                lines.append(f"- **[{alert.severity}] {alert.finding_code}**: {alert.title}")
                lines.append(f"  - *Required Action*: {alert.required_action}")
                if alert.blocker:
                    lines.append(f"  - *Blocker*: {alert.blocker}")

        lines.append("")
        return "\n".join(lines)
