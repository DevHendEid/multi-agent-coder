from __future__ import annotations

import argparse
import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .models import TaskStatus
from .orchestrator import Orchestrator
from .integration import IntegrationService, IntegrationReceipt, IntegrationError


_SAFE_REF = re.compile(r"^[A-Za-z0-9._/-]+$")
_SAFE_REMOTE = re.compile(r"^[A-Za-z0-9._-]+$")
_ALLOWED_REVIEW = {"PASS", "PASS_WITH_NOTES", "NEEDS_HUMAN_DECISION"}


class PullRequestError(RuntimeError):
    pass


@dataclass(slots=True)
class PullRequestResult:
    task_id: str
    number: int | None
    url: str
    state: str
    draft: bool
    created: bool
    commit_sha: str


GhRunner = Callable[[list[str], Path], subprocess.CompletedProcess[str]]


def _default_gh_runner(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True)


class GitHubPullRequestPublisher:
    """Publish a verified task worktree as a GitHub pull request.

    This is intentionally an explicit human-invoked boundary. It may commit and
    push the already-verified task branch, but it never merges or deploys. A
    critical/human-gated review may only be published as a draft PR and remains
    in WAITING_HUMAN after publication.
    """

    def __init__(self, root: str | Path, gh_runner: GhRunner | None = None):
        self.root = Path(root).resolve()
        self.orchestrator = Orchestrator(self.root)
        self.gh_runner = gh_runner or _default_gh_runner

    def publish(
        self,
        task_id: str,
        *,
        remote: str = "origin",
        base: str = "main",
        draft: bool = True,
    ) -> PullRequestResult:
        if not _SAFE_REMOTE.fullmatch(remote):
            raise PullRequestError(f"Unsafe remote name: {remote}")
        if not _SAFE_REF.fullmatch(base) or base.startswith("-") or ".." in base:
            raise PullRequestError(f"Unsafe base ref: {base}")

        state = self.orchestrator.state.get_task(task_id)
        if not state:
            raise PullRequestError(f"Unknown task: {task_id}")
        if state["status"] not in {
            TaskStatus.READY_FOR_REVIEW.value,
            TaskStatus.WAITING_HUMAN.value,
            TaskStatus.PR_READY.value,
        }:
            raise PullRequestError(
                f"Task {task_id} is {state['status']}; PR publication requires READY_FOR_REVIEW/WAITING_HUMAN/PR_READY"
            )

        worktree = Path(state.get("worktree") or "").resolve()
        if not worktree.is_dir():
            raise PullRequestError(f"Task worktree is missing: {worktree}")
        branch = str(state.get("branch") or "")
        if not branch or not _SAFE_REF.fullmatch(branch) or branch.startswith("-") or ".." in branch:
            raise PullRequestError(f"Unsafe or missing task branch: {branch}")

        verification, review = self._load_gates(task_id)
        if verification.get("overall_status") != "PASS":
            raise PullRequestError("Verification is not PASS; refusing to publish PR")
        verdict = str(review.get("verdict", ""))
        if verdict not in _ALLOWED_REVIEW:
            raise PullRequestError(f"Review verdict {verdict or '<missing>'} is not publishable")
        human_gate = verdict == "NEEDS_HUMAN_DECISION"
        if human_gate and not draft:
            raise PullRequestError("Human-gated tasks may only be published as draft PRs")

        current_branch = self._git(worktree, ["branch", "--show-current"]).stdout.strip()
        if current_branch != branch:
            raise PullRequestError(f"Worktree branch mismatch: expected {branch}, got {current_branch or '<detached>'}")
        self._git(worktree, ["remote", "get-url", remote])

        changed = bool(self._git(worktree, ["status", "--porcelain"]).stdout.strip())
        if changed:
            self._git(worktree, ["add", "--all"])
            staged = self._git(worktree, ["diff", "--cached", "--name-only"]).stdout.strip()
            if not staged:
                raise PullRequestError("No staged task changes to commit")
            message = f"{task_id}: {str(state['goal']).strip()}"[:200]
            self._git(worktree, ["commit", "-m", message])

        commit_sha = self._git(worktree, ["rev-parse", "HEAD"]).stdout.strip()
        self._git(worktree, ["push", "--set-upstream", remote, branch])

        existing = self._gh_json(
            ["gh", "pr", "view", branch, "--json", "number,url,state,isDraft"],
            worktree,
            allow_failure=True,
        )
        created = False
        if existing is None:
            title = f"[{task_id}] {str(state['goal']).strip()}"[:240]
            body = self._body(task_id, state, verification, review, commit_sha)
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", prefix="agent-pr-", suffix=".md", delete=True) as handle:
                handle.write(body)
                handle.flush()
                argv = ["gh", "pr", "create", "--head", branch, "--base", base, "--title", title, "--body-file", handle.name]
                if draft:
                    argv.append("--draft")
                proc = self.gh_runner(argv, worktree)
            if proc.returncode != 0:
                raise PullRequestError((proc.stderr or proc.stdout or "gh pr create failed").strip())
            url = self._extract_url(proc.stdout)
            existing = self._gh_json(
                ["gh", "pr", "view", branch, "--json", "number,url,state,isDraft"],
                worktree,
                allow_failure=True,
            ) or {"number": None, "url": url, "state": "OPEN", "isDraft": draft}
            created = True

        result = PullRequestResult(
            task_id=task_id,
            number=int(existing["number"]) if existing.get("number") is not None else None,
            url=str(existing.get("url") or ""),
            state=str(existing.get("state") or "OPEN"),
            draft=bool(existing.get("isDraft", draft)),
            created=created,
            commit_sha=commit_sha,
        )
        next_status = TaskStatus.WAITING_HUMAN.value if human_gate else TaskStatus.PR_READY.value
        before = state["status"]
        self.orchestrator.state.update_task(task_id, status=next_status)
        self.orchestrator.state.record_event(
            task_id,
            before,
            next_status,
            state.get("provider_id"),
            "publish_pr",
            result.url,
        )
        self._persist(task_id, result, remote, base, verdict)
        return result

    def _load_gates(self, task_id: str) -> tuple[dict, dict]:
        run_dir = self.root / "runs" / task_id
        verification_path = run_dir / "verification.json"
        review_path = run_dir / "review.json"
        if not verification_path.exists():
            raise PullRequestError("Verification artifact missing")
        if not review_path.exists():
            raise PullRequestError("Review artifact missing")
        try:
            verification = json.loads(verification_path.read_text())
            review = json.loads(review_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise PullRequestError(f"Invalid gate artifact: {exc}") from exc
        if not isinstance(verification, dict) or not isinstance(review, dict):
            raise PullRequestError("Gate artifacts must be JSON objects")
        return verification, review

    def _git(self, cwd: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
        proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
        if proc.returncode != 0:
            raise PullRequestError((proc.stderr or proc.stdout or f"git {' '.join(args)} failed").strip())
        return proc

    def _gh_json(self, argv: list[str], cwd: Path, *, allow_failure: bool = False) -> dict | None:
        proc = self.gh_runner(argv, cwd)
        if proc.returncode != 0:
            if allow_failure:
                return None
            raise PullRequestError((proc.stderr or proc.stdout or "GitHub CLI command failed").strip())
        try:
            value = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            if allow_failure:
                return None
            raise PullRequestError("GitHub CLI returned malformed JSON") from exc
        return value if isinstance(value, dict) else None

    @staticmethod
    def _extract_url(output: str) -> str:
        for token in (output or "").split():
            if token.startswith("https://github.com/"):
                return token.strip()
        return ""

    @staticmethod
    def _body(task_id: str, state: dict, verification: dict, review: dict, commit_sha: str) -> str:
        return "\n".join([
            "## Multi-Agent Coder task",
            "",
            f"- Task: `{task_id}`",
            f"- Risk: `{state.get('risk', 'unknown')}`",
            f"- Provider: `{state.get('provider_id') or 'unknown'}`",
            f"- Verification: `{verification.get('overall_status', 'UNKNOWN')}`",
            f"- Review: `{review.get('verdict', 'UNKNOWN')}`",
            f"- Commit: `{commit_sha}`",
            "",
            "### Goal",
            str(state.get("goal", "")),
            "",
            "### Safety boundary",
            "This PR was created only after persisted verification and review gates. No automatic merge or deployment is performed.",
            "",
        ])

    def _persist(self, task_id: str, result: PullRequestResult, remote: str, base: str, verdict: str) -> None:
        path = self.root / "runs" / task_id / "pr.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "task_id": task_id,
            "number": result.number,
            "url": result.url,
            "state": result.state,
            "draft": result.draft,
            "created": result.created,
            "commit_sha": result.commit_sha,
            "remote": remote,
            "base": base,
            "review_verdict": verdict,
        }, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-pr", description="Publish a verified task branch as a GitHub PR")
    parser.add_argument("task_id")
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--base", default="main")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--draft", action="store_true", default=True)
    mode.add_argument("--ready", action="store_true", help="Create a non-draft PR (not permitted for human-gated tasks)")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        result = GitHubPullRequestPublisher(Path.cwd()).publish(
            args.task_id,
            remote=args.remote,
            base=args.base,
            draft=not args.ready,
        )
    except PullRequestError as exc:
        raise SystemExit(str(exc))
    action = "created" if result.created else "existing"
    print(f"PR {action}: {result.url or '<url unavailable>'}")
    print(f"Commit: {result.commit_sha}")
    print(f"Draft: {result.draft}")


if __name__ == "__main__":
    main()
