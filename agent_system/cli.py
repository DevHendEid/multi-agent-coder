from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import yaml

from .orchestrator import Orchestrator
from .project import ProjectConfigError
from .worktree import WorktreeError
from .providers import ProviderRegistry
from .router import ModelRouter
from .context import ContextBuilder
from .implementer import Implementer
from .verifier import Verifier
from .reviewer import Reviewer, ProviderReviewer
from .repair import RepairLoop
from .artifacts import RunArtifacts
from .models import TaskStatus


def find_root() -> Path:
    return Path.cwd()


def build_reviewer(registry: ProviderRegistry, worktree: Path) -> Reviewer:
    """Prefer the configured AI reviewer; fall back safely when unavailable."""
    provider = registry.get("codex-reviewer")
    if provider is not None:
        return ProviderReviewer(provider, worktree)
    return Reviewer(reviewer_id="deterministic-reviewer")


def cmd_init_project(args):
    repo = Path(args.repo).expanduser().resolve()
    projects = find_root() / "projects"
    projects.mkdir(parents=True, exist_ok=True)
    out = projects / f"{args.name}.yaml"
    if out.exists() and not args.force:
        raise SystemExit(f"Project config already exists: {out} (use --force to overwrite)")
    payload = {
        "name": args.name,
        "repository": {"path": str(repo)},
        "commands": {
            "install": "",
            "analyze": "",
            "lint": "",
            "test": "",
            "build": "",
            "integration_test": "",
        },
        "protected_paths": [".env", "secrets/"],
        "allowed_generated_paths": [],
        "timeouts": {
            "analyze": 60,
            "lint": 60,
            "test": 120,
            "build": 180,
            "integration_test": 300,
        },
        "environment": {},
        "risk_rules": {"docs": "low", "tests": "low", "architecture": "high", "security": "critical"},
    }
    out.write_text(yaml.safe_dump(payload, sort_keys=False))
    print(out)


def cmd_task(args):
    orch = Orchestrator(find_root())
    try:
        project_path = find_root() / "projects" / f"{args.project}.yaml"
        result = orch.create_task(project_path, args.goal)
    except (ProjectConfigError, WorktreeError) as exc:
        raise SystemExit(str(exc))
    for k, v in result.items():
        print(f"{k}: {v}")


def cmd_status(args):
    orch = Orchestrator(find_root())
    row = orch.state.get_task(args.task_id)
    if not row:
        raise SystemExit(f"Unknown task: {args.task_id}")
    for k, v in row.items():
        print(f"{k}: {v}")


def cmd_list(args):
    orch = Orchestrator(find_root())
    for row in orch.state.list_tasks():
        print(f"{row['id']}  {row['status']:<14}  {row['risk']:<8}  {row['project']}  {row['goal']}")


def cmd_models(args):
    orch = Orchestrator(find_root())
    registry = ProviderRegistry(orch.root / "config" / "models.yaml")
    providers = registry.list_all()
    for p in providers:
        avail = p.availability()
        caps = p.capabilities()
        print(f"{p.provider_id} ({p.tier.value}) - {avail.value}")
        print(f"  Tools: {caps.supports_tools}, File Editing: {caps.supports_file_editing}, Long Context: {caps.supports_long_context}")


def cmd_run(args):
    orch = Orchestrator(find_root())
    state = orch.state.get_task(args.task_id)
    if not state:
        raise SystemExit(f"Unknown task: {args.task_id}")

    project = orch._load_project(state["project"])
    registry = ProviderRegistry(orch.root / "config" / "models.yaml")
    router = ModelRouter(registry, orch.root / "config" / "routing.yaml")
    context_builder = ContextBuilder()
    verifier = Verifier(project, Path(state["worktree"]), project.repo_path)
    reviewer = build_reviewer(registry, Path(state["worktree"]))
    repair_loop = RepairLoop(
        orch, router, context_builder, verifier, reviewer,
        max_free_attempts=state.get("max_free_attempts", 2),
        max_total_repair_rounds=state.get("max_total_repair_rounds", 3),
    )
    artifacts = RunArtifacts(orch.root / "runs" / args.task_id)

    implementer = Implementer(orch, router, context_builder, verifier, reviewer, repair_loop, artifacts)
    result = implementer.run(args.task_id)

    if result.success:
        print(f"IMPLEMENTED: {result.provider_id}")
        print(f"Modified files: {', '.join(result.modified_files) if result.modified_files else 'none'}")
        print(f"Duration: {result.duration_ms}ms")
        state_after = orch.state.get_task(args.task_id)
        print(f"Status: {state_after.get('status')}")
    else:
        print(f"FAILED: {result.error}")
        state_after = orch.state.get_task(args.task_id)
        print(f"Status: {state_after.get('status')}")
        sys.exit(1)


def cmd_verify(args):
    orch = Orchestrator(find_root())
    state = orch.state.get_task(args.task_id)
    if not state:
        raise SystemExit(f"Unknown task: {args.task_id}")

    project = orch._load_project(state["project"])
    verifier = Verifier(project, Path(state["worktree"]), project.repo_path)
    report = verifier.run(args.task_id, state["risk"], [])
    run_dir = orch.root / "runs" / args.task_id
    verifier.persist(report, run_dir)

    print(f"Verification: {report.overall_status}")
    if report.scope_gate:
        print(f"  Scope Gate: {report.scope_gate.status}")
    if report.diff_check:
        print(f"  Diff Check: {report.diff_check.status}")
    for r in report.command_results:
        print(f"  {r.command}: {r.status} ({r.duration_ms}ms)")

    if report.overall_status != "PASS":
        sys.exit(1)


def cmd_review(args):
    orch = Orchestrator(find_root())
    state = orch.state.get_task(args.task_id)
    if not state:
        raise SystemExit(f"Unknown task: {args.task_id}")

    project = orch._load_project(state["project"])
    worktree = Path(state["worktree"])
    scope_gate = __import__("agent_system.scope", fromlist=["ScopeGate"]).ScopeGate(
        worktree=worktree,
        allowed_scope=[],
        forbidden_scope=project.protected_paths,
        original_repo=project.repo_path,
    )
    diff = scope_gate.get_diff()

    run_dir = orch.root / "runs" / args.task_id
    verification_path = run_dir / "verification.json"
    if not verification_path.exists():
        raise SystemExit("Verification report not found. Run 'agent verify' first.")

    import json
    verification_data = json.loads(verification_path.read_text())
    from .verifier import VerificationReport, VerificationResult
    scope_gate_res = None
    diff_check_res = None
    if verification_data.get("scope_gate"):
        scope_gate_res = VerificationResult(**verification_data["scope_gate"])
    if verification_data.get("diff_check"):
        diff_check_res = VerificationResult(**verification_data["diff_check"])
    command_results = [VerificationResult(**r) for r in verification_data.get("command_results", [])]
    verification_report = VerificationReport(
        task_id=verification_data["task_id"],
        risk=verification_data["risk"],
        scope_gate=scope_gate_res,
        diff_check=diff_check_res,
        command_results=command_results,
        overall_status=verification_data["overall_status"],
        created_at=verification_data["created_at"],
    )

    registry = ProviderRegistry(orch.root / "config" / "models.yaml")
    reviewer = build_reviewer(registry, worktree)
    review_result = reviewer.review(
        task_id=args.task_id,
        goal=state["goal"],
        risk=state["risk"],
        diff=diff,
        verification_report=verification_report,
    )
    reviewer.persist(review_result, run_dir)

    print(f"Review: {review_result.verdict}")
    print(f"Notes: {review_result.notes}")

    if review_result.verdict in ("FAIL_SCOPE", "FAIL_CORRECTNESS", "FAIL_TESTS", "FAIL_ARCHITECTURE", "FAIL_SECURITY"):
        sys.exit(1)


def cmd_resume(args):
    orch = Orchestrator(find_root())
    state = orch.state.get_task(args.task_id)
    if not state:
        raise SystemExit(f"Unknown task: {args.task_id}")

    current_status = state.get("status", "")
    print(f"Current status: {current_status}")

    if current_status == TaskStatus.PLANNED.value:
        print("Resuming from PLANNED -> running implementation")
        cmd_run(args)
    elif current_status == TaskStatus.CONTEXT_READY.value:
        print("Resuming from CONTEXT_READY -> running implementation")
        cmd_run(args)
    elif current_status == TaskStatus.IMPLEMENTING.value:
        print("Resuming from IMPLEMENTING -> running implementation")
        cmd_run(args)
    elif current_status == TaskStatus.IMPLEMENTED.value:
        print("Resuming from IMPLEMENTED -> running verification")
        cmd_verify(args)
        cmd_review(args)
    elif current_status == TaskStatus.VERIFYING.value:
        print("Resuming from VERIFYING -> running verification")
        cmd_verify(args)
        cmd_review(args)
    elif current_status == TaskStatus.REVIEWING.value:
        print("Resuming from REVIEWING -> running review")
        cmd_review(args)
    elif current_status == TaskStatus.REPAIRING.value:
        print("Resuming from REPAIRING -> running full cycle")
        cmd_run(args)
    elif current_status == TaskStatus.WAITING_PROVIDER.value:
        print("Resuming from WAITING_PROVIDER -> running implementation")
        cmd_run(args)
    elif current_status == TaskStatus.WAITING_CODEX.value:
        print("Resuming from WAITING_CODEX -> running implementation")
        cmd_run(args)
    elif current_status == TaskStatus.READY_FOR_REVIEW.value:
        print("Task is READY_FOR_REVIEW - no action needed")
    elif current_status in (TaskStatus.COMPLETED.value, TaskStatus.FAILED.value):
        print(f"Task is in terminal state: {current_status}")
    else:
        print(f"Unknown state: {current_status}")


def build_parser():
    p = argparse.ArgumentParser(prog="agent", description="Multi-Agent Coder Foundation")
    sub = p.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init-project", help="Create a generic project adapter YAML")
    init.add_argument("--name", required=True)
    init.add_argument("--repo", required=True)
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=cmd_init_project)

    task = sub.add_parser("task", help="Create a task contract and isolated worktree")
    task.add_argument("--project", required=True)
    task.add_argument("goal")
    task.set_defaults(func=cmd_task)

    status = sub.add_parser("status", help="Show persisted task state")
    status.add_argument("task_id")
    status.set_defaults(func=cmd_status)

    ls = sub.add_parser("list", help="List tasks")
    ls.set_defaults(func=cmd_list)

    run = sub.add_parser("run", help="Run implementation for a task")
    run.add_argument("task_id")
    run.set_defaults(func=cmd_run)

    verify = sub.add_parser("verify", help="Run verification for a task")
    verify.add_argument("task_id")
    verify.set_defaults(func=cmd_verify)

    review = sub.add_parser("review", help="Run review for a task")
    review.add_argument("task_id")
    review.set_defaults(func=cmd_review)

    resume = sub.add_parser("resume", help="Resume a task from its current state")
    resume.add_argument("task_id")
    resume.set_defaults(func=cmd_resume)

    models = sub.add_parser("models", help="List available model providers")
    models.set_defaults(func=cmd_models)

    return p


def main():
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
