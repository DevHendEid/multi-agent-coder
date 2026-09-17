from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ALLOWED_SEVERITIES = {"INFO", "WARNING", "HIGH", "BLOCKING"}
ALLOWED_DISPOSITIONS = {"INTEGRATE", "KEEP_SHADOW", "DEFER_WITH_BLOCKER", "ARCHIVE"}


class GovernanceValidationError(ValueError):
    pass


@dataclass(slots=True)
class AssetRecord:
    asset_id: str
    name: str
    purpose: str
    component: str
    asset_type: str
    implementation_path: str
    version: str
    owner: str
    engineering_status: str
    scientific_status: str
    validation_status: str
    integration_status: str
    consumer: str
    gateway_reachability: str
    production_reachable: bool
    validation_receipts: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    next_action: str = ""
    promotion_criteria: str = ""
    disposition: str = "INTEGRATE"
    supersedes: str = "NONE"
    superseded_by: str = "NONE"
    created_by_phase: str = ""
    last_updated_by_phase: str = ""

    def validate(self) -> None:
        if not self.asset_id or not self.name or not self.component:
            raise GovernanceValidationError(f"Asset missing core identifiers: {self}")
        if self.disposition not in ALLOWED_DISPOSITIONS:
            raise GovernanceValidationError(f"Asset {self.asset_id} has invalid disposition: {self.disposition}")


@dataclass(slots=True)
class ValidationRecord:
    validation_id: str
    target_asset_id: str
    suite_name: str
    validation_type: str
    evidence_path: str
    status: str
    executed_at: str
    receipt_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AlertRecord:
    alert_id: str
    finding_code: str
    severity: str
    asset_id: str | None
    task_id: str | None
    title: str
    message: str
    evidence: str
    detected_at: str
    current_state: str
    required_action: str
    suggested_consumer: str | None = None
    blocker: str | None = None
    allowed_dispositions: list[str] = field(default_factory=list)
    source_paths: list[str] = field(default_factory=list)
    resolved: bool = False
    resolution: str | None = None
    resolved_at: str | None = None

    def validate(self) -> None:
        if self.severity not in ALLOWED_SEVERITIES:
            raise GovernanceValidationError(f"Alert {self.alert_id} has invalid severity: {self.severity}")


@dataclass(slots=True)
class PromotionQueueItem:
    queue_id: str
    asset_id: str
    task_id: str | None
    issue: str
    severity: str
    required_action: str
    suggested_consumer: str | None = None
    blocker: str | None = None
    allowed_dispositions: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    status: str = "ACTIVE"
    source_alert_id: str | None = None

    def validate(self) -> None:
        if self.severity not in ALLOWED_SEVERITIES:
            raise GovernanceValidationError(f"Queue item {self.queue_id} has invalid severity: {self.severity}")


@dataclass(slots=True)
class ProjectHealthMetrics:
    total_assets: int
    production_connected_assets: int
    shadow_connected_assets: int
    validated_assets: int
    promotion_pending_assets: int
    deferred_assets: int
    archived_assets: int
    orphan_count: int
    validated_orphan_count: int
    stale_superseded_references: int
    assets_missing_consumers: int
    assets_missing_validation_receipts: int
    unresolved_blocking_alerts: int
    unresolved_high_alerts: int
    calculated_at: str = ""


class GovernanceRegistry:
    def __init__(self, root: Path | str):
        self.root = Path(root).resolve()
        self.gov_dir = self.root / "docs" / "project"
        self.assets: dict[str, AssetRecord] = {}
        self.validations: dict[str, ValidationRecord] = {}
        self.alerts: dict[str, AlertRecord] = {}
        self.promotion_queue: dict[str, PromotionQueueItem] = {}
        self.health_metrics: ProjectHealthMetrics | None = None
        self.source_of_truth: dict[str, Any] = {}

    def load(self) -> None:
        self.gov_dir.mkdir(parents=True, exist_ok=True)
        sot_path = self.gov_dir / "SOURCE_OF_TRUTH.json"
        if sot_path.exists():
            self.source_of_truth = json.loads(sot_path.read_text())

        asset_path = self.gov_dir / "ASSET_REGISTRY.json"
        if asset_path.exists():
            raw_assets = json.loads(asset_path.read_text())
            for item in raw_assets:
                rec = AssetRecord(**item)
                rec.validate()
                self.assets[rec.asset_id] = rec

        val_path = self.gov_dir / "VALIDATION_REGISTRY.json"
        if val_path.exists():
            raw_vals = json.loads(val_path.read_text())
            for item in raw_vals:
                rec = ValidationRecord(**item)
                self.validations[rec.validation_id] = rec

        alerts_path = self.gov_dir / "ALERTS.json"
        if alerts_path.exists():
            raw_alerts = json.loads(alerts_path.read_text())
            for item in raw_alerts:
                rec = AlertRecord(**item)
                rec.validate()
                self.alerts[rec.alert_id] = rec

        queue_path = self.gov_dir / "PROMOTION_QUEUE.json"
        if queue_path.exists():
            raw_queue = json.loads(queue_path.read_text())
            for item in raw_queue:
                rec = PromotionQueueItem(**item)
                rec.validate()
                self.promotion_queue[rec.queue_id] = rec

        health_path = self.gov_dir / "PROJECT_HEALTH.json"
        if health_path.exists():
            data = json.loads(health_path.read_text())
            self.health_metrics = ProjectHealthMetrics(**data)

    def save(self) -> None:
        self.gov_dir.mkdir(parents=True, exist_ok=True)

        (self.gov_dir / "SOURCE_OF_TRUTH.json").write_text(
            json.dumps(self.source_of_truth, indent=2) + "\n"
        )
        (self.gov_dir / "ASSET_REGISTRY.json").write_text(
            json.dumps([asdict(a) for a in self.assets.values()], indent=2) + "\n"
        )
        (self.gov_dir / "VALIDATION_REGISTRY.json").write_text(
            json.dumps([asdict(v) for v in self.validations.values()], indent=2) + "\n"
        )
        (self.gov_dir / "ALERTS.json").write_text(
            json.dumps([asdict(a) for a in self.alerts.values()], indent=2) + "\n"
        )
        (self.gov_dir / "PROMOTION_QUEUE.json").write_text(
            json.dumps([asdict(q) for q in self.promotion_queue.values()], indent=2) + "\n"
        )
        if self.health_metrics:
            (self.gov_dir / "PROJECT_HEALTH.json").write_text(
                json.dumps(asdict(self.health_metrics), indent=2) + "\n"
            )
