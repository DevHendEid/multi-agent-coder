from __future__ import annotations

import json
from pathlib import Path
import pytest

from dataclasses import asdict
from agent_system.governance import (
    AssetRecord,
    GovernanceRegistry,
    ValidationRecord,
)
from agent_system.watchdog import IntegrationWatchdog
from agent_system.doctor import Doctor
from agent_system.cli import cmd_watchdog, cmd_phase_closeout
import argparse


def init_test_governance(tmp_path: Path) -> GovernanceRegistry:
    gov_dir = tmp_path / "docs" / "project"
    gov_dir.mkdir(parents=True)
    
    sot = {
        "repository": "multi-agent-coder",
        "authoritative_branch": "main",
        "active_commit_sha": "abc1234",
        "schema_version": "1.0.0"
    }
    (gov_dir / "SOURCE_OF_TRUTH.json").write_text(json.dumps(sot, indent=2))
    
    asset = AssetRecord(
        asset_id="ASSET-001",
        name="Test Core",
        purpose="Testing core infrastructure",
        component="core",
        asset_type="CORE_ENGINE",
        implementation_path="agent_system/core.py",
        version="1.0.0",
        owner="test",
        engineering_status="STABLE",
        scientific_status="VALIDATED",
        validation_status="VALIDATED",
        integration_status="PRODUCTION_ACTIVE",
        consumer="agent_system.cli",
        gateway_reachability="DIRECT",
        production_reachable=True,
        validation_receipts=["VAL-001"],
        blockers=[],
        next_action="MAINTAIN",
        promotion_criteria="N/A",
        disposition="INTEGRATE",
        supersedes="NONE",
        superseded_by="NONE",
        created_by_phase="phase-1",
        last_updated_by_phase="phase-1",
    )
    (gov_dir / "ASSET_REGISTRY.json").write_text(json.dumps([asdict(asset)], indent=2))
    
    val = ValidationRecord(
        validation_id="VAL-001",
        target_asset_id="ASSET-001",
        suite_name="test_core",
        validation_type="UNIT_TEST",
        evidence_path="tests/test_core.py",
        status="PASS",
        executed_at="2026-09-17T00:00:00Z",
    )
    (gov_dir / "VALIDATION_REGISTRY.json").write_text(json.dumps([asdict(val)], indent=2))
    (gov_dir / "ALERTS.json").write_text("[]")
    (gov_dir / "PROMOTION_QUEUE.json").write_text("[]")
    (gov_dir / "PROJECT_HEALTH.json").write_text(json.dumps({
        "total_assets": 1,
        "production_connected_assets": 1,
        "shadow_connected_assets": 0,
        "validated_assets": 1,
        "promotion_pending_assets": 0,
        "deferred_assets": 0,
        "archived_assets": 0,
        "orphan_count": 0,
        "validated_orphan_count": 0,
        "stale_superseded_references": 0,
        "assets_missing_consumers": 0,
        "assets_missing_validation_receipts": 0,
        "unresolved_blocking_alerts": 0,
        "unresolved_high_alerts": 0,
        "calculated_at": "2026-09-17T00:00:00Z"
    }, indent=2))

    registry = GovernanceRegistry(tmp_path)
    registry.load()
    return registry


def test_clean_registry_passes(tmp_path: Path):
    init_test_governance(tmp_path)
    watchdog = IntegrationWatchdog(tmp_path)
    active = watchdog.run()
    assert len(active) == 0
    ok, reasons = watchdog.check_phase_closeout()
    assert ok is True
    assert reasons == []


def test_validated_orphan_detection_and_resolution(tmp_path: Path):
    reg = init_test_governance(tmp_path)
    # Add a validated asset with NO consumer
    orphan = AssetRecord(
        asset_id="ASSET-ORPHAN",
        name="Orphan Tool",
        purpose="Development utility for orphan test",
        component="tools",
        asset_type="UTILITY",
        implementation_path="agent_system/tools/orphan.py",
        version="1.0.0",
        owner="test",
        engineering_status="TESTED",
        scientific_status="VALIDATED",
        validation_status="VALIDATED",
        integration_status="DISCONNECTED",
        consumer="",
        gateway_reachability="STANDALONE",
        production_reachable=False,
        validation_receipts=["VAL-002"],
        blockers=[],
        disposition="INTEGRATE",
    )
    reg.assets["ASSET-ORPHAN"] = orphan
    reg.validations["VAL-002"] = ValidationRecord(
        validation_id="VAL-002",
        target_asset_id="ASSET-ORPHAN",
        suite_name="test_orphan",
        validation_type="BENCHMARK",
        evidence_path="tests/test_orphan.py",
        status="PASS",
        executed_at="2026-09-17T00:00:00Z"
    )
    reg.save()

    watchdog = IntegrationWatchdog(tmp_path)
    active = watchdog.run()
    orphan_alerts = [a for a in active if a.finding_code == "VALIDATED_ORPHAN"]
    assert len(orphan_alerts) == 1
    assert orphan_alerts[0].severity == "BLOCKING"
    assert watchdog.registry.health_metrics.validated_orphan_count == 1

    # Phase closeout must fail
    ok, reasons = watchdog.check_phase_closeout()
    assert ok is False
    assert any("validated_orphan_count" in r for r in reasons)

    # Resolution 1: Mark as KEEP_SHADOW
    reg = GovernanceRegistry(tmp_path)
    reg.load()
    reg.assets["ASSET-ORPHAN"].disposition = "KEEP_SHADOW"
    reg.assets["ASSET-ORPHAN"].promotion_criteria = "Promote when consumer agent is ready"
    reg.save()

    watchdog2 = IntegrationWatchdog(tmp_path)
    active2 = watchdog2.run()
    orphan_alerts2 = [a for a in active2 if a.finding_code == "VALIDATED_ORPHAN"]
    assert len(orphan_alerts2) == 0
    assert watchdog2.registry.alerts["ALERT-ORPHAN-ASSET-ORPHAN"].resolved is True
    assert watchdog2.registry.health_metrics.validated_orphan_count == 0

    ok2, reasons2 = watchdog2.check_phase_closeout()
    assert ok2 is True


def test_disconnected_reusable_code(tmp_path: Path):
    reg = init_test_governance(tmp_path)
    disc = AssetRecord(
        asset_id="ASSET-DISC",
        name="Disconnected Lib",
        purpose="Helper library without caller",
        component="lib",
        asset_type="LIBRARY",
        implementation_path="agent_system/lib.py",
        version="1.0.0",
        owner="test",
        engineering_status="IMPLEMENTED",
        scientific_status="NOT_APPLICABLE",
        validation_status="UNVALIDATED",
        integration_status="DISCONNECTED",
        consumer="None",
        gateway_reachability="INTERNAL_ONLY",
        production_reachable=False,
        disposition="INTEGRATE",
    )
    reg.assets["ASSET-DISC"] = disc
    reg.save()

    watchdog = IntegrationWatchdog(tmp_path)
    active = watchdog.run()
    disc_alerts = [a for a in active if a.finding_code == "DISCONNECTED_REUSABLE"]
    assert len(disc_alerts) == 1
    assert disc_alerts[0].severity == "HIGH"


def test_shadow_without_promotion_path(tmp_path: Path):
    reg = init_test_governance(tmp_path)
    shadow = AssetRecord(
        asset_id="ASSET-SHADOW",
        name="Shadow Module",
        purpose="Shadow model run",
        component="models",
        asset_type="MODEL_ADAPTER",
        implementation_path="agent_system/shadow.py",
        version="1.0.0",
        owner="test",
        engineering_status="IMPLEMENTED",
        scientific_status="VALIDATED",
        validation_status="VALIDATED",
        integration_status="SHADOW_RUNNING",
        consumer="shadow_runner",
        gateway_reachability="INTERNAL_ONLY",
        production_reachable=False,
        validation_receipts=["VAL-003"],
        promotion_criteria="", # MISSING!
        disposition="KEEP_SHADOW",
    )
    reg.assets["ASSET-SHADOW"] = shadow
    reg.validations["VAL-003"] = ValidationRecord(
        validation_id="VAL-003",
        target_asset_id="ASSET-SHADOW",
        suite_name="test_shadow",
        validation_type="BENCHMARK",
        evidence_path="tests/test_shadow.py",
        status="PASS",
        executed_at="2026-09-17T00:00:00Z"
    )
    reg.save()

    watchdog = IntegrationWatchdog(tmp_path)
    active = watchdog.run()
    shadow_alerts = [a for a in active if a.finding_code == "SHADOW_NO_PROMOTION_PATH"]
    assert len(shadow_alerts) == 1
    assert shadow_alerts[0].severity in {"HIGH", "WARNING"}


def test_research_code_production_reachable(tmp_path: Path):
    reg = init_test_governance(tmp_path)
    research = AssetRecord(
        asset_id="ASSET-RESEARCH",
        name="Research Exploratory Script",
        purpose="Testing hypotheses",
        component="research",
        asset_type="RESEARCH_NOTEBOOK",
        implementation_path="research/exp.py",
        version="0.1.0",
        owner="researcher",
        engineering_status="PROTOTYPE",
        scientific_status="EXPLORATORY",
        validation_status="UNVALIDATED",
        integration_status="DISCONNECTED",
        consumer="cli",
        gateway_reachability="DIRECT",
        production_reachable=True, # ILLEGAL for exploratory/research
        disposition="INTEGRATE",
    )
    reg.assets["ASSET-RESEARCH"] = research
    reg.save()

    watchdog = IntegrationWatchdog(tmp_path)
    active = watchdog.run()
    res_alerts = [a for a in active if a.finding_code == "RESEARCH_CODE_PRODUCTION_REACHABLE"]
    assert len(res_alerts) == 1
    assert res_alerts[0].severity == "BLOCKING"


def test_missing_validation_receipt(tmp_path: Path):
    reg = init_test_governance(tmp_path)
    unverified = AssetRecord(
        asset_id="ASSET-NORECEIPT",
        name="Unverified Claim",
        purpose="Claims validated without receipts",
        component="core",
        asset_type="CORE_ENGINE",
        implementation_path="agent_system/unverified.py",
        version="1.0.0",
        owner="test",
        engineering_status="IMPLEMENTED",
        scientific_status="VALIDATED",
        validation_status="VALIDATED",
        integration_status="PRODUCTION_ACTIVE",
        consumer="cli",
        gateway_reachability="DIRECT",
        production_reachable=True,
        validation_receipts=[], # EMPTY!
        disposition="INTEGRATE",
    )
    reg.assets["ASSET-NORECEIPT"] = unverified
    reg.save()

    watchdog = IntegrationWatchdog(tmp_path)
    active = watchdog.run()
    receipt_alerts = [a for a in active if a.finding_code == "MISSING_VALIDATION_RECEIPT"]
    assert len(receipt_alerts) == 1
    assert receipt_alerts[0].severity == "BLOCKING"


def test_poc_without_disposition(tmp_path: Path):
    reg = init_test_governance(tmp_path)
    poc = AssetRecord(
        asset_id="ASSET-POC",
        name="POC Experiment",
        purpose="Quick POC",
        component="poc",
        asset_type="PROOF_OF_CONCEPT",
        implementation_path="poc/demo.py",
        version="0.1.0",
        owner="test",
        engineering_status="PROTOTYPE",
        scientific_status="EXPLORATORY",
        validation_status="UNVALIDATED",
        integration_status="DISCONNECTED",
        consumer="None",
        gateway_reachability="STANDALONE",
        production_reachable=False,
        blockers=[],
        disposition="INTEGRATE", # Invalid for POC with no consumer/blocker
    )
    reg.assets["ASSET-POC"] = poc
    reg.save()

    watchdog = IntegrationWatchdog(tmp_path)
    active = watchdog.run()
    poc_alerts = [a for a in active if a.finding_code == "POC_NO_DISPOSITION"]
    assert len(poc_alerts) == 1
    assert poc_alerts[0].severity == "HIGH"


def test_alert_idempotence_and_resolution(tmp_path: Path):
    reg = init_test_governance(tmp_path)
    # Introduce an orphan
    reg.assets["ASSET-ORPHAN"] = AssetRecord(
        asset_id="ASSET-ORPHAN",
        name="Orphan Tool",
        purpose="Development utility for idempotence test",
        component="tools",
        asset_type="UTILITY",
        implementation_path="agent_system/tools/orphan.py",
        version="1.0.0",
        owner="test",
        engineering_status="TESTED",
        scientific_status="VALIDATED",
        validation_status="VALIDATED",
        integration_status="DISCONNECTED",
        consumer="",
        gateway_reachability="STANDALONE",
        production_reachable=False,
        validation_receipts=["VAL-002"],
        disposition="INTEGRATE",
    )
    reg.validations["VAL-002"] = ValidationRecord(
        validation_id="VAL-002",
        target_asset_id="ASSET-ORPHAN",
        suite_name="test_orphan",
        validation_type="BENCHMARK",
        evidence_path="tests/test_orphan.py",
        status="PASS",
        executed_at="2026-09-17T00:00:00Z"
    )
    reg.save()

    watchdog = IntegrationWatchdog(tmp_path)
    run1 = watchdog.run()
    assert len(run1) == 1
    alert_id = run1[0].alert_id

    # Second run without changes -> exact same alert, no duplication
    watchdog2 = IntegrationWatchdog(tmp_path)
    run2 = watchdog2.run()
    assert len(run2) == 1
    assert run2[0].alert_id == alert_id
    assert len(watchdog2.registry.alerts) == 1

    # Check promotion queue item was created
    assert len(watchdog2.registry.promotion_queue) == 1
    queue_item = list(watchdog2.registry.promotion_queue.values())[0]
    assert queue_item.asset_id == "ASSET-ORPHAN"
    assert queue_item.status == "ACTIVE"

    # Fix condition: archive the orphan
    reg = GovernanceRegistry(tmp_path)
    reg.load()
    reg.assets["ASSET-ORPHAN"].disposition = "ARCHIVE"
    reg.save()

    # Run 3: Condition resolved
    watchdog3 = IntegrationWatchdog(tmp_path)
    run3 = watchdog3.run()
    assert len(run3) == 0
    assert watchdog3.registry.alerts[alert_id].resolved is True
    assert watchdog3.registry.alerts[alert_id].resolved_at is not None

    # Queue item should be marked RESOLVED
    queue_item_resolved = list(watchdog3.registry.promotion_queue.values())[0]
    assert queue_item_resolved.status == "RESOLVED"


def test_dashboard_generation(tmp_path: Path):
    init_test_governance(tmp_path)
    watchdog = IntegrationWatchdog(tmp_path)
    watchdog.run()
    dashboard = watchdog.generate_dashboard()
    assert "# Multi-Agent Coder — Integration Dashboard" in dashboard
    assert "## Asset Lifecycle Matrix" in dashboard
    assert "ASSET-001" in dashboard
    assert "**Validated Orphan Count**: `0`" in dashboard


def test_doctor_governance_integration(tmp_path: Path):
    init_test_governance(tmp_path)
    doc = Doctor(tmp_path)
    gov_results = doc._governance_checks()
    assert all(r.status == "PASS" for r in gov_results)

    # Introduce a blocking orphan
    reg = GovernanceRegistry(tmp_path)
    reg.load()
    reg.assets["ASSET-ORPHAN"] = AssetRecord(
        asset_id="ASSET-ORPHAN",
        name="Orphan Tool",
        purpose="Development utility for doctor test",
        component="tools",
        asset_type="UTILITY",
        implementation_path="agent_system/tools/orphan.py",
        version="1.0.0",
        owner="test",
        engineering_status="TESTED",
        scientific_status="VALIDATED",
        validation_status="VALIDATED",
        integration_status="DISCONNECTED",
        consumer="",
        gateway_reachability="STANDALONE",
        production_reachable=False,
        validation_receipts=["VAL-002"],
        disposition="INTEGRATE",
    )
    reg.validations["VAL-002"] = ValidationRecord(
        validation_id="VAL-002",
        target_asset_id="ASSET-ORPHAN",
        suite_name="test_orphan",
        validation_type="BENCHMARK",
        evidence_path="tests/test_orphan.py",
        status="PASS",
        executed_at="2026-09-17T00:00:00Z"
    )
    reg.save()

    gov_results2 = doc._governance_checks()
    orphan_res = [r for r in gov_results2 if r.name == "governance:validated_orphans"]
    assert orphan_res[0].status == "FAIL"
    alert_res = [r for r in gov_results2 if r.name == "governance:alerts"]
    assert alert_res[0].status == "FAIL"


def test_cmd_watchdog_blocking_exit_code(tmp_path: Path, monkeypatch):
    init_test_governance(tmp_path)
    # Monkeypatch cwd to tmp_path
    monkeypatch.chdir(tmp_path)
    
    # Clean passes with return 0
    cmd_watchdog(argparse.Namespace())

    # Add blocking asset
    reg = GovernanceRegistry(tmp_path)
    reg.load()
    reg.assets["ASSET-BLOCKING"] = AssetRecord(
        asset_id="ASSET-BLOCKING",
        name="Blocking Asset",
        purpose="Development utility for exit test",
        component="tools",
        asset_type="UTILITY",
        implementation_path="agent_system/tools/b.py",
        version="1.0.0",
        owner="test",
        engineering_status="TESTED",
        scientific_status="VALIDATED",
        validation_status="VALIDATED",
        integration_status="DISCONNECTED",
        consumer="",
        gateway_reachability="STANDALONE",
        production_reachable=False,
        validation_receipts=["VAL-B"],
        disposition="INTEGRATE",
    )
    reg.validations["VAL-B"] = ValidationRecord(
        validation_id="VAL-B",
        target_asset_id="ASSET-BLOCKING",
        suite_name="test_b",
        validation_type="BENCHMARK",
        evidence_path="tests/test_b.py",
        status="PASS",
        executed_at="2026-09-17T00:00:00Z"
    )
    reg.save()

    with pytest.raises(SystemExit) as exc:
        cmd_watchdog(argparse.Namespace())
    assert exc.value.code == 1


def _git(root: Path, *args: str) -> str:
    import subprocess
    return subprocess.run(
        ["git", *args], cwd=str(root), check=True, capture_output=True, text=True
    ).stdout


def _init_committed_governance(tmp_path: Path) -> None:
    init_test_governance(tmp_path)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")


def test_doctor_and_phase_closeout_leave_git_clean(tmp_path: Path, monkeypatch):
    _init_committed_governance(tmp_path)
    monkeypatch.chdir(tmp_path)

    Doctor(tmp_path)._governance_checks()
    IntegrationWatchdog(tmp_path).check_phase_closeout()
    cmd_phase_closeout(argparse.Namespace())
    IntegrationWatchdog(tmp_path).generate_dashboard()

    assert _git(tmp_path, "status", "--porcelain") == ""


def test_read_only_run_still_reports_findings_without_writing(tmp_path: Path, monkeypatch):
    _init_committed_governance(tmp_path)
    reg = GovernanceRegistry(tmp_path)
    reg.load()
    reg.assets["ASSET-001"].consumer = ""
    reg.save()
    _git(tmp_path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "orphan")

    ok, failures = IntegrationWatchdog(tmp_path).check_phase_closeout()
    assert ok is False
    assert any("VALIDATED_ORPHAN" in f for f in failures)
    checks = {r.name: r.status for r in Doctor(tmp_path)._governance_checks()}
    assert checks["governance:validated_orphans"] == "FAIL"
    assert _git(tmp_path, "status", "--porcelain") == ""


def test_explicit_watchdog_run_persists(tmp_path: Path, monkeypatch):
    _init_committed_governance(tmp_path)
    monkeypatch.chdir(tmp_path)

    cmd_watchdog(argparse.Namespace())

    health = json.loads((tmp_path / "docs" / "project" / "PROJECT_HEALTH.json").read_text())
    assert health["calculated_at"] != "2026-09-17T00:00:00Z"
    assert "PROJECT_HEALTH.json" in _git(tmp_path, "status", "--porcelain")


@pytest.mark.parametrize("corrupt", ["garbage", "directory", "no_tasks_table"])
def test_unreadable_state_db_fails_closed(tmp_path: Path, corrupt: str):
    import sqlite3
    init_test_governance(tmp_path)
    db = tmp_path / ".agent-system" / "state.db"
    db.parent.mkdir(parents=True)
    if corrupt == "garbage":
        db.write_bytes(b"this is not a sqlite database" * 10)
    elif corrupt == "directory":
        db.mkdir()
    else:
        sqlite3.connect(db).close()

    watchdog = IntegrationWatchdog(tmp_path)
    active = watchdog.run()

    db_alerts = [a for a in active if a.finding_code == "STATE_DB_UNREADABLE"]
    assert len(db_alerts) == 1
    assert db_alerts[0].severity == "BLOCKING"
    assert db_alerts[0].alert_id == "ALERT-STATE-DB-UNREADABLE"
    assert "could not open or read" in db_alerts[0].message
    ok, failures = IntegrationWatchdog(tmp_path).check_phase_closeout()
    assert ok is False
    assert any("STATE_DB_UNREADABLE" in f for f in failures)


def test_unreadable_state_db_alert_not_duplicated(tmp_path: Path, monkeypatch):
    init_test_governance(tmp_path)
    db = tmp_path / ".agent-system" / "state.db"
    db.parent.mkdir(parents=True)
    db.write_bytes(b"corrupt" * 100)
    monkeypatch.chdir(tmp_path)

    for _ in range(3):
        with pytest.raises(SystemExit) as exc:
            cmd_watchdog(argparse.Namespace())
        assert exc.value.code == 1

    alerts = json.loads((tmp_path / "docs" / "project" / "ALERTS.json").read_text())
    db_alerts = [a for a in alerts if a["finding_code"] == "STATE_DB_UNREADABLE"]
    assert len(db_alerts) == 1
    assert db_alerts[0]["resolved"] is False
    # Watchdog must not overwrite or repair the corrupt DB.
    assert db.read_bytes() == b"corrupt" * 100

    # Once the DB is removed the alert resolves instead of lingering.
    db.unlink()
    cmd_watchdog(argparse.Namespace())
    alerts = json.loads((tmp_path / "docs" / "project" / "ALERTS.json").read_text())
    assert [a["resolved"] for a in alerts if a["finding_code"] == "STATE_DB_UNREADABLE"] == [True]


def test_readable_state_db_is_not_modified(tmp_path: Path):
    from agent_system.state import StateStore
    init_test_governance(tmp_path)
    db = tmp_path / ".agent-system" / "state.db"
    StateStore(db)
    before = db.read_bytes()

    active = IntegrationWatchdog(tmp_path).run(persist=False)

    assert not [a for a in active if a.finding_code == "STATE_DB_UNREADABLE"]
    assert db.read_bytes() == before
