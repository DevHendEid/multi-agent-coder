from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agent_system.doctor import Doctor
from agent_system.integration import IntegrationError, IntegrationReceipt, IntegrationService
from agent_system.models import RiskLevel, TaskStatus
from agent_system.state import IntegrationPolicyError, StateStore


def run_git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def setup_env(tmp_path: Path, risk: str = "medium", verification: str = "PASS", review: str = "PASS"):
    root = tmp_path / "system"
    root.mkdir()
    repo = tmp_path / "repo"
    remote = tmp_path / "remote.git"
    repo.mkdir()
    run_git(repo, "init")
    run_git(repo, "config", "user.email", "test@example.com")
    run_git(repo, "config", "user.name", "Test")
    (repo / "calc.py").write_text("def add(a, b): return a + b\n")
    run_git(repo, "add", "calc.py")
    run_git(repo, "commit", "-m", "initial")
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    run_git(repo, "remote", "add", "origin", str(remote))
    run_git(repo, "checkout", "-b", "agent/task-test")
    (repo / "calc.py").write_text("def add(a, b): return a + b + 0\n")

    projects = root / "projects"
    projects.mkdir(parents=True)
    (projects / "demo.yaml").write_text(
        f"name: demo\nrepository:\n  path: {repo}\ncommands:\n  test: 'true'\nprotected_paths: []\nrisk_rules: {{}}\n"
    )

    state = StateStore(root / ".agent-system" / "state.db")
    state.create_task("TASK-TEST", "demo", "fix calc", risk, production_target=True)
    state.update_task(
        "TASK-TEST",
        status=TaskStatus.READY_FOR_REVIEW.value,
        branch="agent/task-test",
        worktree=str(repo),
        provider_id="mock-provider",
    )

    run_dir = root / "runs" / "TASK-TEST"
    run_dir.mkdir(parents=True)
    (run_dir / "verification.json").write_text(json.dumps({"overall_status": verification}))
    (run_dir / "review.json").write_text(json.dumps({"verdict": review}))

    docs_dir = root / "docs" / "project"
    docs_dir.mkdir(parents=True)
    (docs_dir / "INTEGRATION_RECEIPTS.md").write_text("# Receipts\n")

    return root, repo, remote


class MockGh:
    def __init__(self, ci_status: str = "pass", pr_state: str = "OPEN"):
        self.ci_status = ci_status
        self.pr_state = pr_state
        self.created = False
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], cwd: Path):
        self.calls.append(argv)
        if argv[:3] == ["gh", "pr", "view"]:
            if self.created or self.pr_state == "MERGED":
                payload = {
                    "number": 42,
                    "url": "https://github.com/org/repo/pull/42",
                    "state": self.pr_state,
                    "isDraft": False,
                    "headRefOid": "abc1234",
                    "mergeCommit": {"oid": "merge9999"} if self.pr_state == "MERGED" else None,
                }
                return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")
            return subprocess.CompletedProcess(argv, 1, "", "not found")

        if argv[:3] == ["gh", "pr", "create"]:
            self.created = True
            return subprocess.CompletedProcess(argv, 0, "https://github.com/org/repo/pull/42\n", "")

        if argv[:3] == ["gh", "pr", "checks"]:
            if self.ci_status == "fail":
                return subprocess.CompletedProcess(argv, 1, "", "Check failed: tests\n")
            if self.ci_status == "pending":
                return subprocess.CompletedProcess(argv, 0, "Check in_progress: tests\n", "")
            return subprocess.CompletedProcess(argv, 0, "All checks passed\n", "")

        if argv[:3] == ["gh", "pr", "merge"]:
            self.pr_state = "MERGED"
            return subprocess.CompletedProcess(argv, 0, "Merged", "")

        return subprocess.CompletedProcess(argv, 1, "", "unexpected gh call")


def test_no_direct_completion_from_ready_for_review_for_production_target(tmp_path: Path):
    root, repo, _ = setup_env(tmp_path)
    state = StateStore(root / ".agent-system" / "state.db")

    with pytest.raises(IntegrationPolicyError, match="cannot transition directly from READY_FOR_REVIEW to COMPLETED"):
        state.update_task("TASK-TEST", status=TaskStatus.COMPLETED.value)


def test_completed_requires_verified_integration_receipt(tmp_path: Path):
    root, repo, _ = setup_env(tmp_path)
    state = StateStore(root / ".agent-system" / "state.db")

    state.update_task("TASK-TEST", status=TaskStatus.POST_MERGE_VERIFYING.value)
    with pytest.raises(IntegrationPolicyError, match="cannot be marked COMPLETED without confirmed merge SHA and integration receipt"):
        state.update_task("TASK-TEST", status=TaskStatus.COMPLETED.value)


def test_pr_idempotence(tmp_path: Path):
    root, repo, _ = setup_env(tmp_path)
    mock_gh = MockGh()
    service = IntegrationService(root, gh_runner=mock_gh)

    res1 = service.publish_or_reuse_pr("TASK-TEST")
    assert res1["created"] is True
    assert res1["number"] == 42

    res2 = service.publish_or_reuse_pr("TASK-TEST")
    assert res2["created"] is False
    assert res2["number"] == 42


def test_ci_failure_blocks_merge(tmp_path: Path):
    root, repo, _ = setup_env(tmp_path)
    mock_gh = MockGh(ci_status="fail")
    service = IntegrationService(root, gh_runner=mock_gh)
    service.publish_or_reuse_pr("TASK-TEST")

    with pytest.raises(IntegrationError, match="CI checks failed"):
        service.merge_pr("TASK-TEST")

    state = StateStore(root / ".agent-system" / "state.db").get_task("TASK-TEST")
    assert state["status"] == TaskStatus.CI_FAILED.value


def test_high_and_critical_human_gate(tmp_path: Path):
    root, repo, _ = setup_env(tmp_path, risk="critical")
    mock_gh = MockGh(ci_status="pass")
    service = IntegrationService(root, gh_runner=mock_gh)
    service.publish_or_reuse_pr("TASK-TEST")

    with pytest.raises(IntegrationError, match="requires explicit human approval"):
        service.merge_pr("TASK-TEST", human_approved=False)

    state = StateStore(root / ".agent-system" / "state.db").get_task("TASK-TEST")
    assert state["status"] == TaskStatus.WAITING_HUMAN.value

    # Approved succeeds
    merged = service.merge_pr("TASK-TEST", human_approved=True)
    assert merged["status"] == "MERGED"


def test_confirmed_merged_sha_required_and_verified(tmp_path: Path):
    root, repo, _ = setup_env(tmp_path)
    mock_gh = MockGh(pr_state="MERGED")
    service = IntegrationService(root, gh_runner=mock_gh)

    with pytest.raises(IntegrationError, match="is not reachable"):
        service.confirm_merge_on_main("TASK-TEST", expected_merge_sha="nonexistent_sha_0000")


def test_post_merge_failure_does_not_mark_completed(tmp_path: Path):
    root, repo, _ = setup_env(tmp_path)
    # Configure failing command
    (root / "projects" / "demo.yaml").write_text(
        f"name: demo\nrepository:\n  path: {repo}\ncommands:\n  test: 'exit 1'\nprotected_paths: []\nrisk_rules: {{}}\n"
    )
    service = IntegrationService(root)
    with pytest.raises(IntegrationError, match="Post-merge verification failed"):
        service.post_merge_verify("TASK-TEST")

    state = StateStore(root / ".agent-system" / "state.db").get_task("TASK-TEST")
    assert state["status"] != TaskStatus.COMPLETED.value


def test_stale_and_orphan_detection_in_doctor(tmp_path: Path):
    root = tmp_path / "system"
    root.mkdir()
    state = StateStore(root / ".agent-system" / "state.db")
    state.create_task("TASK-ORPHAN", "demo", "orphan test", "low", production_target=True)
    state.update_task("TASK-ORPHAN", status=TaskStatus.COMPLETED.value, force_completed=True)

    doctor = Doctor(root)
    checks = doctor.run()
    receipt_check = next(c for c in checks if c.name == "integration:completed_receipts")
    assert receipt_check.status == "FAIL"
    assert "TASK-ORPHAN" in receipt_check.detail


def test_historical_exception_recognized_but_unrecorded_direct_commit_fails(tmp_path: Path):
    doctor = Doctor(Path.cwd())
    checks = doctor.run()
    direct_check = next(c for c in checks if c.name == "integration:direct_main_commits")
    assert direct_check.status == "PASS"
