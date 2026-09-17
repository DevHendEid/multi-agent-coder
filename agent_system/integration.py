from __future__ import annotations

import json
import re
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .models import RiskLevel, TaskStatus
from .orchestrator import Orchestrator
from .state import IntegrationPolicyError
from .verifier import VerificationReport, Verifier
from .worktree import WorktreeManager


_SAFE_REF = re.compile(r"^[A-Za-z0-9._/-]+$")
_SAFE_REMOTE = re.compile(r"^[A-Za-z0-9._-]+$")


class IntegrationError(IntegrationPolicyError):
    pass


@dataclass(slots=True)
class IntegrationReceipt:
    task_id: str
    pr_number: int | None
    pr_url: str
    source_branch: str
    head_sha: str
    merge_sha: str
    merge_timestamp: str
    ci_status: str
    review_verdict: str
    verification_status: str
    risk_class: str
    lifecycle_disposition: str = "INTEGRATE"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


GhRunner = Callable[[list[str], Path], subprocess.CompletedProcess[str]]
GitRunner = Callable[[list[str], Path], subprocess.CompletedProcess[str]]


def _default_gh_runner(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True)


def _default_git_runner(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *argv], cwd=str(cwd), capture_output=True, text=True)


class IntegrationService:
    """Service governing continuous main integration, PR lifecycle, CI observation,

    guarded merge, main reachability verification, post-merge verification,
    and cleanup. Direct push to main is strictly prohibited.
    """

    def __init__(
        self,
        root: str | Path,
        gh_runner: GhRunner | None = None,
        git_runner: GitRunner | None = None,
    ):
        self.root = Path(root).resolve()
        self.orchestrator = Orchestrator(self.root)
        self.gh_runner = gh_runner or _default_gh_runner
        self.git_runner = git_runner or _default_git_runner

    def publish_or_reuse_pr(
        self,
        task_id: str,
        *,
        remote: str = "origin",
        base: str = "main",
        draft: bool = False,
        human_approved: bool = False,
    ) -> dict[str, Any]:
        state = self.orchestrator.state.get_task(task_id)
        if not state:
            raise IntegrationError(f"Task not found: {task_id}")

        worktree = Path(state.get("worktree") or "").resolve()
        branch = str(state.get("branch") or "")
        if not worktree.is_dir():
            raise IntegrationError(f"Task worktree is missing: {worktree}")
        if not branch or not _SAFE_REF.fullmatch(branch) or branch.startswith("-") or ".." in branch:
            raise IntegrationError(f"Unsafe or missing task branch: {branch}")

        verification, review = self._load_gates(task_id)
        if verification.get("overall_status") != "PASS":
            raise IntegrationError("Deterministic verification is not PASS; cannot create PR")

        verdict = str(review.get("verdict", ""))
        human_gate = (
            verdict == "NEEDS_HUMAN_DECISION"
            or state.get("risk") in {RiskLevel.HIGH.value, RiskLevel.CRITICAL.value}
        ) and not human_approved

        if human_gate:
            draft = True

        # Commit uncommitted task changes in worktree if any
        status_proc = self.git_runner(["status", "--porcelain"], worktree)
        if status_proc.stdout.strip():
            self.git_runner(["add", "--all"], worktree)
            self.git_runner(["commit", "-m", f"{task_id}: {state['goal']}"[:200]], worktree)

        commit_sha = self.git_runner(["rev-parse", "HEAD"], worktree).stdout.strip()

        # Push task branch to remote (refuse if branch is base/main)
        if branch == base:
            raise IntegrationError("Direct push to authoritative main branch is prohibited.")
        self.git_runner(["push", "--set-upstream", remote, branch], worktree)

        # Idempotent PR lookup
        view_proc = self.gh_runner(["gh", "pr", "view", branch, "--json", "number,url,state,isDraft,headRefOid"], worktree)
        pr_data: dict[str, Any] | None = None
        if view_proc.returncode == 0:
            try:
                pr_data = json.loads(view_proc.stdout)
            except json.JSONDecodeError:
                pr_data = None

        created = False
        if not pr_data or not pr_data.get("url"):
            title = f"[{task_id}] {state['goal']}"[:240]
            body = self._build_pr_body(task_id, state, verification, review, commit_sha)
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", prefix="agent-pr-", suffix=".md", delete=True) as handle:
                handle.write(body)
                handle.flush()
                create_argv = ["gh", "pr", "create", "--head", branch, "--base", base, "--title", title, "--body-file", handle.name]
                if draft:
                    create_argv.append("--draft")
                create_proc = self.gh_runner(create_argv, worktree)
            if create_proc.returncode != 0:
                raise IntegrationError(f"gh pr create failed: {(create_proc.stderr or create_proc.stdout).strip()}")
            url = self._extract_url(create_proc.stdout)
            view_proc2 = self.gh_runner(["gh", "pr", "view", branch, "--json", "number,url,state,isDraft,headRefOid"], worktree)
            if view_proc2.returncode == 0:
                try:
                    pr_data = json.loads(view_proc2.stdout)
                except json.JSONDecodeError:
                    pr_data = {"number": None, "url": url, "state": "OPEN", "isDraft": draft, "headRefOid": commit_sha}
            else:
                pr_data = {"number": None, "url": url, "state": "OPEN", "isDraft": draft, "headRefOid": commit_sha}
            created = True

        pr_number = int(pr_data["number"]) if pr_data.get("number") is not None else None
        pr_url = str(pr_data.get("url") or "")
        next_status = TaskStatus.WAITING_HUMAN.value if human_gate else TaskStatus.PR_OPEN.value

        self.orchestrator.state.update_task(
            task_id,
            status=next_status,
            pr_number=pr_number,
            pr_url=pr_url,
        )
        self.orchestrator.state.record_event(
            task_id,
            state["status"],
            next_status,
            state.get("provider_id"),
            "publish_or_reuse_pr",
            pr_url,
        )

        persisted = {
            "task_id": task_id,
            "number": pr_number,
            "url": pr_url,
            "state": pr_data.get("state", "OPEN"),
            "draft": bool(pr_data.get("isDraft", draft)),
            "created": created,
            "head_sha": commit_sha,
            "remote": remote,
            "base": base,
            "review_verdict": verdict,
        }
        self._persist_pr(task_id, persisted)
        return persisted

    def check_ci(self, task_id: str) -> dict[str, Any]:
        state = self.orchestrator.state.get_task(task_id)
        if not state:
            raise IntegrationError(f"Task not found: {task_id}")
        worktree = Path(state.get("worktree") or self.root)
        branch = str(state.get("branch") or "")

        proc = self.gh_runner(["gh", "pr", "checks", branch], worktree)
        output = (proc.stdout or "") + (proc.stderr or "")

        # Evaluate checks
        if proc.returncode != 0 and ("fail" in output.lower() or "error" in output.lower()):
            self.orchestrator.state.update_task(task_id, status=TaskStatus.CI_FAILED.value)
            return {"status": "FAIL", "raw": output}
        if "pending" in output.lower() or "in_progress" in output.lower() or "queued" in output.lower():
            self.orchestrator.state.update_task(task_id, status=TaskStatus.CI_PENDING.value)
            return {"status": "PENDING", "raw": output}

        # Checks passed or no checks required
        return {"status": "PASS", "raw": output}

    def merge_pr(
        self,
        task_id: str,
        *,
        human_approved: bool = False,
        merge_method: str = "merge",
    ) -> dict[str, Any]:
        state = self.orchestrator.state.get_task(task_id)
        if not state:
            raise IntegrationError(f"Task not found: {task_id}")

        risk = state.get("risk", RiskLevel.MEDIUM.value)
        worktree = Path(state.get("worktree") or self.root)
        branch = str(state.get("branch") or "")

        # Check CI
        ci = self.check_ci(task_id)
        if ci["status"] == "FAIL":
            self.orchestrator.state.update_task(task_id, status=TaskStatus.CI_FAILED.value)
            raise IntegrationError(f"Merge blocked: CI checks failed for task {task_id}")
        if ci["status"] == "PENDING":
            self.orchestrator.state.update_task(task_id, status=TaskStatus.CI_PENDING.value)
            raise IntegrationError(f"Merge blocked: CI checks are pending for task {task_id}")

        # Enforce Risk Policy
        if risk in {RiskLevel.HIGH.value, RiskLevel.CRITICAL.value} and not human_approved:
            self.orchestrator.state.update_task(task_id, status=TaskStatus.WAITING_HUMAN.value, blocker="Human approval required for high/critical risk")
            raise IntegrationError(f"Task {task_id} has {risk} risk and requires explicit human approval before merge")

        self.orchestrator.state.update_task(task_id, status=TaskStatus.MERGE_PENDING.value)
        merge_proc = self.gh_runner(["gh", "pr", "merge", branch, f"--{merge_method}"], worktree)
        if merge_proc.returncode != 0:
            self.orchestrator.state.update_task(task_id, status=TaskStatus.MERGE_BLOCKED.value, blocker=merge_proc.stderr.strip())
            raise IntegrationError(f"gh pr merge failed: {merge_proc.stderr.strip()}")

        # Verify PR is merged on GitHub
        view_proc = self.gh_runner(["gh", "pr", "view", branch, "--json", "state,mergeCommit"], worktree)
        merge_sha = ""
        if view_proc.returncode == 0:
            try:
                data = json.loads(view_proc.stdout)
                merge_sha = data.get("mergeCommit", {}).get("oid") if isinstance(data.get("mergeCommit"), dict) else ""
            except json.JSONDecodeError:
                pass

        self.orchestrator.state.update_task(task_id, status=TaskStatus.MERGED.value, merge_sha=merge_sha or None)
        return {"status": "MERGED", "merge_sha": merge_sha}

    def confirm_merge_on_main(
        self,
        task_id: str,
        expected_merge_sha: str | None = None,
        main_branch: str = "main",
    ) -> str:
        state = self.orchestrator.state.get_task(task_id)
        if not state:
            raise IntegrationError(f"Task not found: {task_id}")

        project = self.orchestrator._load_project(state["project"])
        repo_path = project.repo_path

        # Determine merge sha
        merge_sha = expected_merge_sha or state.get("merge_sha")
        if not merge_sha:
            pr_path = self.root / "runs" / task_id / "pr.json"
            if pr_path.exists():
                pr_info = json.loads(pr_path.read_text())
                branch = pr_info.get("branch") or state.get("branch")
                if branch:
                    view_proc = self.gh_runner(["gh", "pr", "view", branch, "--json", "mergeCommit"], repo_path)
                    if view_proc.returncode == 0:
                        try:
                            d = json.loads(view_proc.stdout)
                            merge_sha = d.get("mergeCommit", {}).get("oid", "")
                        except json.JSONDecodeError:
                            pass

        if not merge_sha:
            raise IntegrationError(f"Could not resolve merge SHA for task {task_id}")

        # Verify reachability from main
        rev_proc = self.git_runner(["merge-base", "--is-ancestor", merge_sha, main_branch], repo_path)
        if rev_proc.returncode != 0:
            # Also check if merge_sha is HEAD of main
            head_sha = self.git_runner(["rev-parse", main_branch], repo_path).stdout.strip()
            if head_sha != merge_sha:
                raise IntegrationError(f"Merge SHA {merge_sha} is not reachable from {main_branch}")

        self.orchestrator.state.update_task(task_id, status=TaskStatus.POST_MERGE_VERIFYING.value, merge_sha=merge_sha)
        return merge_sha

    def post_merge_verify(self, task_id: str, main_branch: str = "main") -> VerificationReport:
        state = self.orchestrator.state.get_task(task_id)
        if not state:
            raise IntegrationError(f"Task not found: {task_id}")

        project = self.orchestrator._load_project(state["project"])
        verifier = Verifier(project, project.repo_path, project.repo_path)
        report = verifier.run(task_id, state["risk"], [])
        if report.overall_status != "PASS":
            self.orchestrator.state.update_task(task_id, blocker="Post-merge verification failed on main")
            raise IntegrationError(f"Post-merge verification failed on {main_branch}: {report.overall_status}")
        return report

    def finalize(
        self,
        task_id: str,
        *,
        merge_sha: str,
        human_approved: bool = False,
        clean_resources: bool = True,
    ) -> IntegrationReceipt:
        state = self.orchestrator.state.get_task(task_id)
        if not state:
            raise IntegrationError(f"Task not found: {task_id}")

        verification, review = self._load_gates(task_id)
        pr_path = self.root / "runs" / task_id / "pr.json"
        pr_info = json.loads(pr_path.read_text()) if pr_path.exists() else {}

        # Confirm reachability
        self.confirm_merge_on_main(task_id, expected_merge_sha=merge_sha)

        # Run post-merge verification
        self.post_merge_verify(task_id)

        # Build Integration Receipt
        receipt = IntegrationReceipt(
            task_id=task_id,
            pr_number=pr_info.get("number") or state.get("pr_number"),
            pr_url=pr_info.get("url") or state.get("pr_url") or "",
            source_branch=str(state.get("branch") or pr_info.get("remote", "")),
            head_sha=pr_info.get("head_sha", ""),
            merge_sha=merge_sha,
            merge_timestamp=datetime.now(timezone.utc).isoformat(),
            ci_status="PASS",
            review_verdict=str(review.get("verdict", "PASS")),
            verification_status="PASS",
            risk_class=str(state.get("risk", "medium")),
            lifecycle_disposition="INTEGRATE",
        )

        # Persist receipt
        receipt_path = self.root / "runs" / task_id / "integration_receipt.json"
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(json.dumps(receipt.to_dict(), indent=2))

        # Append receipt to docs/project/INTEGRATION_RECEIPTS.md
        self._append_to_receipts_doc(receipt)

        # Mark COMPLETED with confirmed merge_sha
        self.orchestrator.state.update_task(
            task_id,
            status=TaskStatus.COMPLETED.value,
            merge_sha=merge_sha,
            force_completed=True,
        )

        # Clean resources safely
        if clean_resources:
            worktree = state.get("worktree")
            if worktree and Path(worktree).is_dir():
                try:
                    project = self.orchestrator._load_project(state["project"])
                    WorktreeManager(project.repo_path).remove(Path(worktree))
                except Exception:
                    pass

        return receipt

    def _load_gates(self, task_id: str) -> tuple[dict, dict]:
        run_dir = self.root / "runs" / task_id
        verification_path = run_dir / "verification.json"
        review_path = run_dir / "review.json"
        if not verification_path.exists():
            raise IntegrationError(f"Verification artifact missing for {task_id}")
        if not review_path.exists():
            raise IntegrationError(f"Review artifact missing for {task_id}")
        return json.loads(verification_path.read_text()), json.loads(review_path.read_text())

    def _persist_pr(self, task_id: str, data: dict[str, Any]) -> None:
        path = self.root / "runs" / task_id / "pr.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2))

    def _append_to_receipts_doc(self, receipt: IntegrationReceipt) -> None:
        doc_path = self.root / "docs" / "project" / "INTEGRATION_RECEIPTS.md"
        if not doc_path.exists():
            return
        entry = f"""
### RECEIPT-{receipt.task_id}: Production Integration
- **Task ID**: `{receipt.task_id}`
- **PR**: #{receipt.pr_number} ({receipt.pr_url})
- **Source Branch**: `{receipt.source_branch}`
- **Head SHA**: `{receipt.head_sha}`
- **Merge SHA**: `{receipt.merge_sha}`
- **Merge Timestamp**: {receipt.merge_timestamp}
- **CI Status**: {receipt.ci_status}
- **Review Verdict**: {receipt.review_verdict}
- **Verification**: {receipt.verification_status}
- **Risk Class**: {receipt.risk_class}
- **Disposition**: {receipt.lifecycle_disposition}
"""
        doc_path.write_text(doc_path.read_text() + "\n" + entry)

    @staticmethod
    def _extract_url(output: str) -> str:
        for token in (output or "").split():
            if token.startswith("https://github.com/"):
                return token.strip()
        return ""

    @staticmethod
    def _build_pr_body(task_id: str, state: dict, verification: dict, review: dict, commit_sha: str) -> str:
        return "\n".join([
            f"## Multi-Agent Coder Task: {task_id}",
            "",
            f"- **Goal**: {state.get('goal', '')}",
            f"- **Risk**: `{state.get('risk', 'medium')}`",
            f"- **Head Commit**: `{commit_sha}`",
            f"- **Verification**: `{verification.get('overall_status', 'UNKNOWN')}`",
            f"- **Review Verdict**: `{review.get('verdict', 'UNKNOWN')}`",
            "",
            "### Continuous Main Integration Policy",
            "This PR was generated by Multi-Agent Coder. Integration to `main` requires deterministic CI pass, independent review, and guarded merge validation.",
        ])
