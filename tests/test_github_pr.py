from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agent_system.github_pr import GitHubPullRequestPublisher, PullRequestError
from agent_system.models import TaskStatus
from agent_system.state import StateStore


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def prepare(tmp_path: Path, *, verification: str = "PASS", review: str = "PASS"):
    root = tmp_path / "system"
    root.mkdir()
    repo = tmp_path / "repo"
    remote = tmp_path / "remote.git"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")
    (repo / "a.txt").write_text("base\n")
    git(repo, "add", "a.txt")
    git(repo, "commit", "-m", "initial")
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True, text=True)
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "checkout", "-b", "agent/task-0001")
    (repo / "a.txt").write_text("changed\n")

    state = StateStore(root / ".agent-system" / "state.db")
    state.create_task("TASK-0001", "demo", "change a", "low")
    state.update_task(
        "TASK-0001",
        status=TaskStatus.READY_FOR_REVIEW.value,
        branch="agent/task-0001",
        worktree=str(repo),
        provider_id="ollama-free",
    )
    run_dir = root / "runs" / "TASK-0001"
    run_dir.mkdir(parents=True)
    (run_dir / "verification.json").write_text(json.dumps({"overall_status": verification}))
    (run_dir / "review.json").write_text(json.dumps({"verdict": review}))
    return root, repo, remote


class FakeGh:
    def __init__(self, *, existing: bool = False):
        self.existing = existing
        self.created = False
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], cwd: Path):
        self.calls.append(argv)
        if argv[:3] == ["gh", "pr", "view"]:
            if self.existing or self.created:
                payload = {"number": 7, "url": "https://github.com/o/r/pull/7", "state": "OPEN", "isDraft": True}
                return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")
            return subprocess.CompletedProcess(argv, 1, "", "not found")
        if argv[:3] == ["gh", "pr", "create"]:
            self.created = True
            return subprocess.CompletedProcess(argv, 0, "https://github.com/o/r/pull/7\n", "")
        return subprocess.CompletedProcess(argv, 1, "", "unexpected command")


def test_publish_commits_pushes_and_records_pr(tmp_path: Path):
    root, repo, remote = prepare(tmp_path)
    gh = FakeGh()
    result = GitHubPullRequestPublisher(root, gh_runner=gh).publish("TASK-0001")

    assert result.created is True
    assert result.url.endswith("/pull/7")
    assert git(repo, "status", "--porcelain").stdout == ""
    assert git(repo, "show", "HEAD:a.txt").stdout == "changed\n"
    refs = subprocess.run(
        ["git", "--git-dir", str(remote), "show-ref", "--verify", "refs/heads/agent/task-0001"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.commit_sha in refs.stdout
    state = StateStore(root / ".agent-system" / "state.db").get_task("TASK-0001")
    assert state["status"] == TaskStatus.PR_READY.value
    artifact = json.loads((root / "runs" / "TASK-0001" / "pr.json").read_text())
    assert artifact["number"] == 7
    assert any(call[:3] == ["gh", "pr", "create"] and "--draft" in call for call in gh.calls)


def test_failed_verification_refuses_publication(tmp_path: Path):
    root, repo, _ = prepare(tmp_path, verification="FAIL")
    gh = FakeGh()
    with pytest.raises(PullRequestError, match="Verification is not PASS"):
        GitHubPullRequestPublisher(root, gh_runner=gh).publish("TASK-0001")
    assert gh.calls == []
    assert git(repo, "status", "--porcelain").stdout.strip()


def test_human_gate_requires_draft_and_stays_waiting_human(tmp_path: Path):
    root, _, _ = prepare(tmp_path, review="NEEDS_HUMAN_DECISION")
    gh = FakeGh()
    with pytest.raises(PullRequestError, match="only be published as draft"):
        GitHubPullRequestPublisher(root, gh_runner=gh).publish("TASK-0001", draft=False)

    result = GitHubPullRequestPublisher(root, gh_runner=gh).publish("TASK-0001", draft=True)
    assert result.draft is True
    state = StateStore(root / ".agent-system" / "state.db").get_task("TASK-0001")
    assert state["status"] == TaskStatus.WAITING_HUMAN.value


def test_existing_pr_is_idempotent(tmp_path: Path):
    root, repo, _ = prepare(tmp_path)
    first = FakeGh()
    GitHubPullRequestPublisher(root, gh_runner=first).publish("TASK-0001")

    existing = FakeGh(existing=True)
    result = GitHubPullRequestPublisher(root, gh_runner=existing).publish("TASK-0001")
    assert result.created is False
    assert not any(call[:3] == ["gh", "pr", "create"] for call in existing.calls)
    assert git(repo, "status", "--porcelain").stdout == ""
