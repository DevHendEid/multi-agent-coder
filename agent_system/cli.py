from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import yaml

from .orchestrator import Orchestrator
from .project import ProjectConfigError
from .worktree import WorktreeError
from .providers import ProviderRegistry, ProviderAvailability
from .router import ModelRouter
from .context import ContextBuilder
from .implementer import Implementer
from .verifier import Verifier
from .reviewer import Reviewer, ProviderReviewer
from .multi_reviewer import IndependentReviewPanel
from .repair import RepairLoop
from .artifacts import RunArtifacts
from .models import TaskStatus
from .parallel import ParallelTaskRunner
from .dag import DagError, DagScheduler


def find_root() -> Path:
    return Path.cwd()


def build_reviewer(registry: ProviderRegistry, worktree: Path) -> Reviewer:
    free_reviewers: list[Reviewer] = []
    premium_reviewers: list[Reviewer] = []
    for provider in registry.list_all():
        if provider.availability() != ProviderAvailability.AVAILABLE:
            continue
        caps = provider.capabilities()
        if caps.supports_file_editing:
            continue
        reviewer = ProviderReviewer(provider, worktree)
        if provider.tier.value == "premium":
            premium_reviewers.append(reviewer)
        else:
            free_reviewers.append(reviewer)
    return IndependentReviewPanel(
        free_reviewers=free_reviewers,
        premium_reviewers=premium_reviewers,
        deterministic_reviewer=Reviewer(reviewer_id="deterministic-reviewer"),
    )


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
        "commands": {"install": "", "analyze": "", "lint": "", "test": "", "build": "", "integration_test": ""},
        "protected_paths": [".env", "secrets/"],
        "allowed_generated_paths": [],
        "timeouts": {"analyze": 60, "lint": 60, "test": 120, "build": 180, "integration_test": 300},
        "environment": {},
        "risk_rules": {"docs": "low", "tests": "low", "architecture": "high", "security": "critical"},
    }
    out.write_text(yaml.safe_dump(payload, sort_keys=False))
    print(out)


def cmd_task(args):
    orch = Orchestrator(find_root())
    try:
        project_path = find_root() / "projects" / f"{args.project}.yaml"
        result = orch.create_task(project_path, args.goal, dependencies=args.depends_on)
    except (ProjectConfigError, WorktreeError, ValueError) as exc:
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
    deps = orch.state.get_dependencies(args.task_id)
    if deps:
        print(f"dependencies: {', '.join(deps)}")


def cmd_list(args):
    orch = Orchestrator(find_root())
    for row in orch.state.list_tasks():
        print(f"{row['id']}  {row['status']:<18}  {row['risk']:<8}  {row['project']}  {row['goal']}")


def cmd_models(args):
    orch = Orchestrator(find_root())
    registry = ProviderRegistry(orch.root / "config" / "models.yaml")
    for p in registry.list_all():
        metadata = p.get_metadata(); caps = p.capabilities()
        print(f"{p.provider_id} ({p.tier.value}) - {metadata.availability.value} - {metadata.trust_status.value}")
        print(f"  Model: {metadata.model_name}")
        print(f"  Tools: {caps.supports_tools}, File Editing: {caps.supports_file_editing}, Long Context: {caps.supports_long_context}")


def cmd_run(args):
    orch = Orchestrator(find_root())
    state = orch.state.get_task(args.task_id)
    if not state:
        raise SystemExit(f"Unknown task: {args.task_id}")
    try:
        DagScheduler(orch.state).assert_runnable(args.task_id)
    except DagError as exc:
        orch.state.update_task(args.task_id, status=TaskStatus.WAITING_DEPENDENCY.value)
        raise SystemExit(str(exc))
    if state.get("status") == TaskStatus.WAITING_DEPENDENCY.value:
        orch.state.update_task(args.task_id, status=TaskStatus.PLANNED.value)
        state = orch.state.get_task(args.task_id)

    project = orch._load_project(state["project"])
    registry = ProviderRegistry(orch.root / "config" / "models.yaml")
    router = ModelRouter(registry, orch.root / "config" / "routing.yaml")
    context_builder = ContextBuilder()
    verifier = Verifier(project, Path(state["worktree"]), project.repo_path)
    reviewer = build_reviewer(registry, Path(state["worktree"]))
    repair_loop = RepairLoop(orch, router, context_builder, verifier, reviewer,
        max_free_attempts=state.get("max_free_attempts", 2),
        max_total_repair_rounds=state.get("max_total_repair_rounds", 3))
    artifacts = RunArtifacts(orch.root / "runs" / args.task_id)
    result = Implementer(orch, router, context_builder, verifier, reviewer, repair_loop, artifacts).run(args.task_id)
    if result.success:
        print(f"IMPLEMENTED: {result.provider_id}")
        print(f"Modified files: {', '.join(result.modified_files) if result.modified_files else 'none'}")
        print(f"Duration: {result.duration_ms}ms")
        print(f"Status: {orch.state.get_task(args.task_id).get('status')}")
    else:
        print(f"FAILED: {result.error}")
        print(f"Status: {orch.state.get_task(args.task_id).get('status')}")
        sys.exit(1)


def cmd_run_many(args):
    try:
        results = ParallelTaskRunner(find_root()).run(args.task_ids, max_workers=args.workers)
    except ValueError as exc:
        raise SystemExit(str(exc))
    any_failed = False
    for result in results:
        status = "PASS" if result.success else "FAIL"
        print(f"=== {result.task_id}: {status} ===")
        if result.stdout.strip(): print(result.stdout.rstrip())
        if result.stderr.strip(): print(result.stderr.rstrip(), file=sys.stderr)
        any_failed = any_failed or not result.success
    if any_failed: sys.exit(1)


def cmd_run_ready(args):
    orch = Orchestrator(find_root())
    ready = DagScheduler(orch.state).ready_tasks(args.task_ids or None)
    if not ready:
        print("No runnable tasks")
        return
    print("Runnable: " + ", ".join(ready))
    ns = argparse.Namespace(task_ids=ready, workers=args.workers)
    cmd_run_many(ns)


def cmd_depends(args):
    orch = Orchestrator(find_root())
    scheduler = DagScheduler(orch.state)
    try:
        scheduler.add_dependencies(args.task_id, args.on)
    except DagError as exc:
        raise SystemExit(str(exc))
    state = scheduler.dependency_state(args.task_id)
    if state.dependencies and not state.ready:
        orch.state.update_task(args.task_id, status=TaskStatus.WAITING_DEPENDENCY.value)
    print(f"{args.task_id} depends on: {', '.join(state.dependencies) or 'none'}")


def cmd_dag(args):
    orch = Orchestrator(find_root())
    scheduler = DagScheduler(orch.state)
    for task_id, deps in sorted(scheduler.graph().items()):
        state = scheduler.dependency_state(task_id)
        marker = "READY" if state.ready else ("BLOCKED_FAILED" if state.failed else "WAITING")
        print(f"{task_id}: {marker} <- {', '.join(deps) or 'none'}")


def cmd_verify(args):
    orch = Orchestrator(find_root()); state = orch.state.get_task(args.task_id)
    if not state: raise SystemExit(f"Unknown task: {args.task_id}")
    project = orch._load_project(state["project"])
    verifier = Verifier(project, Path(state["worktree"]), project.repo_path)
    report = verifier.run(args.task_id, state["risk"], [])
    run_dir = orch.root / "runs" / args.task_id; verifier.persist(report, run_dir)
    print(f"Verification: {report.overall_status}")
    if report.scope_gate: print(f"  Scope Gate: {report.scope_gate.status}")
    if report.diff_check: print(f"  Diff Check: {report.diff_check.status}")
    for r in report.command_results: print(f"  {r.command}: {r.status} ({r.duration_ms}ms)")
    if report.overall_status != "PASS": sys.exit(1)


def cmd_review(args):
    orch = Orchestrator(find_root()); state = orch.state.get_task(args.task_id)
    if not state: raise SystemExit(f"Unknown task: {args.task_id}")
    project = orch._load_project(state["project"]); worktree = Path(state["worktree"])
    scope_gate = __import__("agent_system.scope", fromlist=["ScopeGate"]).ScopeGate(worktree=worktree, allowed_scope=[], forbidden_scope=project.protected_paths, original_repo=project.repo_path)
    diff = scope_gate.get_diff(); run_dir = orch.root / "runs" / args.task_id
    verification_path = run_dir / "verification.json"
    if not verification_path.exists(): raise SystemExit("Verification report not found. Run 'agent verify' first.")
    verification_data = json.loads(verification_path.read_text())
    from .verifier import VerificationReport, VerificationResult
    scope_gate_res = VerificationResult(**verification_data["scope_gate"]) if verification_data.get("scope_gate") else None
    diff_check_res = VerificationResult(**verification_data["diff_check"]) if verification_data.get("diff_check") else None
    command_results = [VerificationResult(**r) for r in verification_data.get("command_results", [])]
    verification_report = VerificationReport(task_id=verification_data["task_id"], risk=verification_data["risk"], scope_gate=scope_gate_res, diff_check=diff_check_res, command_results=command_results, overall_status=verification_data["overall_status"], created_at=verification_data["created_at"])
    registry = ProviderRegistry(orch.root / "config" / "models.yaml")
    review_result = build_reviewer(registry, worktree).review(task_id=args.task_id, goal=state["goal"], risk=state["risk"], diff=diff, verification_report=verification_report)
    build_reviewer(registry, worktree).persist(review_result, run_dir)
    print(f"Review: {review_result.verdict}"); print(f"Notes: {review_result.notes}")
    if review_result.verdict in ("FAIL_SCOPE", "FAIL_CORRECTNESS", "FAIL_TESTS", "FAIL_ARCHITECTURE", "FAIL_SECURITY"): sys.exit(1)


def cmd_resume(args):
    orch = Orchestrator(find_root()); state = orch.state.get_task(args.task_id)
    if not state: raise SystemExit(f"Unknown task: {args.task_id}")
    current_status = state.get("status", ""); print(f"Current status: {current_status}")
    if current_status in (TaskStatus.PLANNED.value, TaskStatus.CONTEXT_READY.value, TaskStatus.IMPLEMENTING.value, TaskStatus.REPAIRING.value, TaskStatus.WAITING_PROVIDER.value, TaskStatus.WAITING_CODEX.value, TaskStatus.WAITING_DEPENDENCY.value):
        cmd_run(args)
    elif current_status in (TaskStatus.IMPLEMENTED.value, TaskStatus.VERIFYING.value):
        cmd_verify(args); cmd_review(args)
    elif current_status == TaskStatus.REVIEWING.value: cmd_review(args)
    elif current_status == TaskStatus.READY_FOR_REVIEW.value: print("Task is READY_FOR_REVIEW - no action needed")
    elif current_status in (TaskStatus.COMPLETED.value, TaskStatus.FAILED.value): print(f"Task is in terminal state: {current_status}")
    else: print(f"Unknown state: {current_status}")


def build_parser():
    p = argparse.ArgumentParser(prog="agent", description="Multi-Agent Coder Foundation")
    sub = p.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init-project"); init.add_argument("--name", required=True); init.add_argument("--repo", required=True); init.add_argument("--force", action="store_true"); init.set_defaults(func=cmd_init_project)
    task = sub.add_parser("task"); task.add_argument("--project", required=True); task.add_argument("--depends-on", action="append", default=[]); task.add_argument("goal"); task.set_defaults(func=cmd_task)
    status = sub.add_parser("status"); status.add_argument("task_id"); status.set_defaults(func=cmd_status)
    ls = sub.add_parser("list"); ls.set_defaults(func=cmd_list)
    run = sub.add_parser("run"); run.add_argument("task_id"); run.set_defaults(func=cmd_run)
    run_many = sub.add_parser("run-many"); run_many.add_argument("task_ids", nargs="+"); run_many.add_argument("--workers", type=int, default=2); run_many.set_defaults(func=cmd_run_many)
    run_ready = sub.add_parser("run-ready"); run_ready.add_argument("task_ids", nargs="*"); run_ready.add_argument("--workers", type=int, default=2); run_ready.set_defaults(func=cmd_run_ready)
    depends = sub.add_parser("depends"); depends.add_argument("task_id"); depends.add_argument("--on", action="append", required=True); depends.set_defaults(func=cmd_depends)
    dag = sub.add_parser("dag"); dag.set_defaults(func=cmd_dag)
    verify = sub.add_parser("verify"); verify.add_argument("task_id"); verify.set_defaults(func=cmd_verify)
    review = sub.add_parser("review"); review.add_argument("task_id"); review.set_defaults(func=cmd_review)
    resume = sub.add_parser("resume"); resume.add_argument("task_id"); resume.set_defaults(func=cmd_resume)
    models = sub.add_parser("models"); models.set_defaults(func=cmd_models)
    return p


def main():
    args = build_parser().parse_args(); args.func(args)


if __name__ == "__main__":
    main()
