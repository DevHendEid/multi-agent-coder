from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from agent_system.doctor import Doctor
from agent_system.models import TaskStatus
from agent_system.state import StateStore
from agent_system.watchdog import IntegrationWatchdog

from test_watchdog import init_test_governance


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=str(cwd), check=True, capture_output=True, text=True,
    ).stdout.strip()


def make_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", "main")
    (path / "f.txt").write_text(f"{path.name}\n")
    git(path, "add", "-A")
    git(path, "commit", "-qm", "init")
    return path


def write_project(root: Path, name: str, repo: Path | str) -> None:
    projects = root / "projects"
    projects.mkdir(exist_ok=True)
    (projects / f"{name}.yaml").write_text(
        f"name: {name}\nrepository:\n  path: {repo}\ncommands:\n  test: 'true'\n"
    )


def stale_wt_alerts(alerts):
    return sorted(
        (a for a in alerts if a.finding_code == "STALE_EXECUTION_ASSET"), key=lambda a: a.alert_id
    )


# --- 1. Watchdog external project worktrees ---------------------------------


def test_watchdog_reports_detached_and_orphaned_worktrees_in_project_repo(tmp_path: Path):
    root = tmp_path / "system"
    init_test_governance(root)
    repo = make_repo(tmp_path / "ext")
    git(repo, "worktree", "add", "-q", "--detach", str(tmp_path / "wt-detached"))
    git(repo, "worktree", "add", "-q", "-b", "agent/task-9", str(tmp_path / "wt-orphan"))
    shutil.rmtree(tmp_path / "wt-orphan")
    write_project(root, "ext", repo)

    alerts = stale_wt_alerts(IntegrationWatchdog(root).run(persist=False))

    assert [a.alert_id for a in alerts] == ["ALERT-STALE-WT-ext-wt-detached", "ALERT-STALE-WT-ext-wt-orphan"]
    by_id = {a.alert_id: a for a in alerts}
    assert by_id["ALERT-STALE-WT-ext-wt-detached"].current_state == "Detached worktree"
    assert by_id["ALERT-STALE-WT-ext-wt-orphan"].current_state == "Orphaned worktree (directory missing)"
    assert all(f"repository={repo}" in a.evidence for a in alerts)


def test_watchdog_ignores_active_branch_worktrees_and_main_worktree(tmp_path: Path):
    root = tmp_path / "system"
    init_test_governance(root)
    repo = make_repo(tmp_path / "ext")
    git(repo, "checkout", "-q", "--detach")  # main worktree itself detached
    git(repo, "worktree", "add", "-q", "-b", "agent/task-1", str(tmp_path / "wt-active"))
    write_project(root, "ext", repo)

    assert stale_wt_alerts(IntegrationWatchdog(root).run(persist=False)) == []


def test_watchdog_deduplicates_worktrees_shared_by_multiple_project_configs(tmp_path: Path):
    root = tmp_path / "system"
    init_test_governance(root)
    repo = make_repo(tmp_path / "ext")
    git(repo, "worktree", "add", "-q", "--detach", str(tmp_path / "wt-detached"))
    write_project(root, "alpha", repo)
    write_project(root, "beta", repo)
    # A config pointing at a linked worktree of the same repo sees the same worktree list.
    write_project(root, "gamma", tmp_path / "wt-detached")

    watchdog = IntegrationWatchdog(root)
    for _ in range(3):
        alerts = stale_wt_alerts(watchdog.run())

    assert len(alerts) == 1
    assert alerts[0].alert_id == "ALERT-STALE-WT-alpha-wt-detached"
    persisted = json.loads((root / "docs" / "project" / "ALERTS.json").read_text())
    assert [a["alert_id"] for a in persisted if a["finding_code"] == "STALE_EXECUTION_ASSET"] == [
        "ALERT-STALE-WT-alpha-wt-detached"
    ]


def test_watchdog_same_worktree_name_in_different_repos_gets_distinct_alerts(tmp_path: Path):
    root = tmp_path / "system"
    init_test_governance(root)
    for name in ("one", "two"):
        repo = make_repo(tmp_path / name)
        git(repo, "worktree", "add", "-q", "--detach", str(tmp_path / name / ".wts" / "task-0001"))
        write_project(root, name, repo)

    ids = [a.alert_id for a in stale_wt_alerts(IntegrationWatchdog(root).run(persist=False))]

    assert ids == ["ALERT-STALE-WT-one-task-0001", "ALERT-STALE-WT-two-task-0001"]


def test_watchdog_skips_missing_or_invalid_project_configs(tmp_path: Path):
    root = tmp_path / "system"
    init_test_governance(root)
    write_project(root, "missing", tmp_path / "does-not-exist")
    (root / "projects" / "broken.yaml").write_text("name: broken\n")
    (root / "projects" / "garbage.yaml").write_text(": : :\n")

    assert stale_wt_alerts(IntegrationWatchdog(root).run(persist=False)) == []


def test_watchdog_stale_project_worktree_resolves_after_prune(tmp_path: Path):
    root = tmp_path / "system"
    init_test_governance(root)
    repo = make_repo(tmp_path / "ext")
    git(repo, "worktree", "add", "-q", "--detach", str(tmp_path / "wt"))
    write_project(root, "ext", repo)
    watchdog = IntegrationWatchdog(root)
    assert len(stale_wt_alerts(watchdog.run())) == 1

    git(repo, "worktree", "remove", str(tmp_path / "wt"))

    assert stale_wt_alerts(watchdog.run()) == []
    persisted = json.loads((root / "docs" / "project" / "ALERTS.json").read_text())
    assert [a["resolved"] for a in persisted if a["finding_code"] == "STALE_EXECUTION_ASSET"] == [True]


# --- 2. Doctor project load failure -----------------------------------------


def completed_task_root(tmp_path: Path, project: str) -> tuple[Path, str]:
    """Orchestrator root whose own main contains merge_sha, so a silent root fallback would PASS."""
    root = make_repo(tmp_path / "system")
    merge_sha = git(root, "rev-parse", "HEAD")
    state = StateStore(root / ".agent-system" / "state.db")
    state.create_task("TASK-X", project, "goal", "low", production_target=True)
    state.update_task("TASK-X", status=TaskStatus.COMPLETED.value, merge_sha=merge_sha, force_completed=True)
    (root / "runs" / "TASK-X").mkdir(parents=True)
    (root / "runs" / "TASK-X" / "integration_receipt.json").write_text("{}")
    return root, merge_sha


def checks_by_name(root: Path) -> dict[str, list]:
    out: dict[str, list] = {}
    for c in Doctor(root)._integration_checks():
        out.setdefault(c.name, []).append(c)
    return out


def test_doctor_fails_when_project_config_missing_instead_of_using_root(tmp_path: Path):
    root, _ = completed_task_root(tmp_path, "ghost")

    checks = checks_by_name(root)

    [load] = checks["integration:project_load"]
    assert load.status == "FAIL"
    assert "project 'ghost'" in load.detail
    assert "Project config not found" in load.detail
    assert "TASK-X" in load.detail
    [merged] = checks["integration:completed_merged"]
    assert merged.status == "FAIL"
    assert "TASK-X (project 'ghost' failed to load)" in merged.detail


def test_doctor_fails_when_project_repository_missing(tmp_path: Path):
    root, _ = completed_task_root(tmp_path, "demo")
    write_project(root, "demo", tmp_path / "gone")

    [load] = checks_by_name(root)["integration:project_load"]

    assert load.status == "FAIL"
    assert "project 'demo'" in load.detail
    assert "Repository path does not exist" in load.detail


def test_doctor_verifies_merge_sha_in_project_repo_when_project_loads(tmp_path: Path):
    root, root_sha = completed_task_root(tmp_path, "demo")
    repo = make_repo(tmp_path / "repo")
    write_project(root, "demo", repo)

    checks = checks_by_name(root)

    assert "integration:project_load" not in checks
    [merged] = checks["integration:completed_merged"]
    # root_sha exists only in the orchestrator repo, so checking the project repo must fail.
    assert merged.status == "FAIL"
    assert f"TASK-X (SHA {root_sha[:7]} unreachable)" in merged.detail

    StateStore(root / ".agent-system" / "state.db").update_task(
        "TASK-X", merge_sha=git(repo, "rev-parse", "HEAD"), force_completed=True
    )
    [merged] = checks_by_name(root)["integration:completed_merged"]
    assert merged.status == "PASS"
