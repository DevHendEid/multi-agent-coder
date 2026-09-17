from __future__ import annotations

import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from agent_system.models import TaskStatus
from agent_system.providers.base import ProviderTier
from agent_system.providers.command_provider import CommandProvider
from agent_system.recovery import RecoveryError, RecoveryManager
from agent_system.runtime_lock import TaskLease, TaskLeaseError
from agent_system.state import StateStore


def test_task_lease_blocks_duplicate_execution_and_releases(tmp_path: Path):
    first = TaskLease(tmp_path, "TASK-0001")
    first.acquire()
    try:
        with pytest.raises(TaskLeaseError, match="already running"):
            TaskLease(tmp_path, "TASK-0001").acquire()
    finally:
        first.release()
    second = TaskLease(tmp_path, "TASK-0001")
    second.acquire()
    second.release()


def test_task_lease_reclaims_dead_same_host_owner(tmp_path: Path):
    lock = tmp_path / ".agent-system" / "locks" / "TASK-0001.lock"
    lock.mkdir(parents=True)
    (lock / "lease.json").write_text(json.dumps({
        "task_id": "TASK-0001",
        "pid": 99999999,
        "host": socket.gethostname(),
        "token": "old",
        "acquired_at": "old",
    }))
    lease = TaskLease(tmp_path, "TASK-0001")
    lease.acquire()
    assert json.loads((lock / "lease.json").read_text())["token"] == lease.token
    lease.release()


def prepare_recovery(tmp_path: Path, status: str) -> tuple[Path, Path]:
    root = tmp_path / "system"; root.mkdir()
    repo = tmp_path / "repo"; repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "T"], check=True)
    (repo / "a.txt").write_text("base\n")
    subprocess.run(["git", "-C", str(repo), "add", "a.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"], check=True, capture_output=True)
    state = StateStore(root / ".agent-system" / "state.db")
    state.create_task("TASK-0001", "demo", "goal", "low")
    state.update_task("TASK-0001", status=status, branch="agent/task-0001", worktree=str(repo))
    return root, repo


def test_recovery_promotes_only_from_persisted_pass_evidence(tmp_path: Path):
    root, _ = prepare_recovery(tmp_path, TaskStatus.REVIEWING.value)
    run = root / "runs" / "TASK-0001"; run.mkdir(parents=True)
    (run / "verification.json").write_text(json.dumps({"overall_status": "PASS"}))
    (run / "review.json").write_text(json.dumps({"verdict": "PASS"}))
    manager = RecoveryManager(root)
    plan = manager.plan("TASK-0001")
    assert plan.target_status == TaskStatus.READY_FOR_REVIEW.value
    manager.apply("TASK-0001")
    assert StateStore(root / ".agent-system" / "state.db").get_task("TASK-0001")["status"] == TaskStatus.READY_FOR_REVIEW.value


def test_recovery_marks_unverified_partial_changes_for_manual_inspection(tmp_path: Path):
    root, repo = prepare_recovery(tmp_path, TaskStatus.IMPLEMENTING.value)
    (repo / "a.txt").write_text("partial\n")
    plan = RecoveryManager(root).plan("TASK-0001")
    assert plan.target_status == TaskStatus.FAILED.value
    assert "manual inspection" in plan.reason


def test_recovery_refuses_when_runtime_lease_exists(tmp_path: Path):
    root, _ = prepare_recovery(tmp_path, TaskStatus.IMPLEMENTING.value)
    lease = TaskLease(root, "TASK-0001")
    lease.acquire()
    try:
        with pytest.raises(RecoveryError, match="runtime lease"):
            RecoveryManager(root).plan("TASK-0001")
    finally:
        lease.release()


def test_command_provider_does_not_persist_full_argv(tmp_path: Path):
    provider = CommandProvider(
        "safe-command",
        "safe-command",
        ProviderTier.FREE,
        {"command": [sys.executable, "-c", "print('secret-looking-prompt')"]},
    )
    result = provider.execute({"id": "T", "goal": "g"}, {"selected_files": []}, tmp_path)
    assert result.success
    assert "command" not in (result.metadata or {})
    assert result.metadata["command_redacted"] is True
    assert result.metadata["executable"]


def test_production_routing_excludes_disposable_implementer():
    import yaml

    from agent_system.providers.registry import ProviderRegistry

    root = Path(__file__).resolve().parents[1]
    registry = ProviderRegistry(root / "config" / "models.yaml")
    routing = yaml.safe_load((root / "config" / "routing.yaml").read_text())
    assert registry.get("local-command") is None
    for providers in routing.values():
        assert "local-command" not in providers
        assert all(registry.get(provider_id) is not None for provider_id in providers)
