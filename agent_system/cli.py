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
from .benchmark import BenchmarkRegistry, BenchmarkRunner


def find_root() -> Path:
    return Path.cwd()


def build_benchmark_registry(root: Path) -> BenchmarkRegistry:
    policy = {}
    config_path = root / "config" / "benchmarks.yaml"
    if config_path.exists():
        policy = (yaml.safe_load(config_path.read_text()) or {}).get("policy", {}) or {}
    return BenchmarkRegistry(
        root / ".agent-system" / "benchmarks.db",
        validation_runs=int(policy.get("validation_runs", 2)),
        required_pass_rate=float(policy.get("required_pass_rate", 1.0)),
        reject_below=float(policy.get("reject_below_pass_rate", 0.5)),
    )


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
    projects = find_root() / "projects"; projects.mkdir(parents=True, exist_ok=True)
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
    out.write_text(yaml.safe_dump(payload, sort_keys=False)); print(out)


def cmd_task(args):
    orch = Orchestrator(find_root())
    try:
        result = orch.create_task(find_root() / "projects" / f"{args.project}.yaml", args.goal, dependencies=args.depends_on)
    except (ProjectConfigError, WorktreeError, ValueError) as exc:
        raise SystemExit(str(exc))
    for k, v in result.items(): print(f"{k}: {v}")


def cmd_status(args):
    orch = Orchestrator(find_root()); row = orch.state.get_task(args.task_id)
    if not row: raise SystemExit(f"Unknown task: {args.task_id}")
    for k, v in row.items(): print(f"{k}: {v}")
    deps = orch.state.get_dependencies(args.task_id)
    if deps: print(f"dependencies: {', '.join(deps)}")


def cmd_list(args):
    orch = Orchestrator(find_root())
    for row in orch.state.list_tasks(): print(f"{row['id']}  {row['status']:<18}  {row['risk']:<8}  {row['project']}  {row['goal']}")


def cmd_models(args):
    root = find_root(); orch = Orchestrator(root)
    registry = ProviderRegistry(orch.root / "config" / "models.yaml")
    benchmarks = build_benchmark_registry(root)
    for p in registry.list_all():
        metadata = p.get_metadata(); caps = p.capabilities()
        benchmark_status = benchmarks.status(p.provider_id, metadata.model_name)
        print(f"{p.provider_id} ({p.tier.value}) - {metadata.availability.value} - config={metadata.trust_status.value} - benchmark={benchmark_status}")
        print(f"  Model: {metadata.model_name}")
        print(f"  Tools: {caps.supports_tools}, File Editing: {caps.supports_file_editing}, Long Context: {caps.supports_long_context}")


def cmd_benchmark(args):
    root = find_root(); provider_registry = ProviderRegistry(root / "config" / "models.yaml")
    provider = provider_registry.get(args.provider_id)
    if provider is None: raise SystemExit(f"Unknown provider: {args.provider_id}")
    registry = build_benchmark_registry(root); runner = BenchmarkRunner(registry)
    for index in range(args.repeat):
        try:
            summary = runner.run(provider)
        except Exception as exc:
            raise SystemExit(f"Benchmark failed: {exc}")
        print(f"Run {index + 1}/{args.repeat}: {summary.provider_id} {summary.model_name} role={summary.role} pass={summary.passed_cases}/{summary.total_cases} rate={summary.pass_rate:.2f} avg={summary.average_duration_ms:.0f}ms status={summary.status}")


def cmd_benchmarks(args):
    rows = build_benchmark_registry(find_root()).latest_all()
    if not rows:
        print("No benchmark history")
        return
    for row in rows:
        print(f"{row.provider_id}  {row.model_name}  {row.role}  {row.status}  {row.passed_cases}/{row.total_cases}  {row.pass_rate:.2f}  {row.average_duration_ms:.0f}ms")


def cmd_run(args):
    root = find_root(); orch = Orchestrator(root); state = orch.state.get_task(args.task_id)
    if not state: raise SystemExit(f"Unknown task: {args.task_id}")
    try: DagScheduler(orch.state).assert_runnable(args.task_id)
    except DagError as exc:
        orch.state.update_task(args.task_id, status=TaskStatus.WAITING_DEPENDENCY.value); raise SystemExit(str(exc))
    if state.get("status") == TaskStatus.WAITING_DEPENDENCY.value:
        orch.state.update_task(args.task_id, status=TaskStatus.PLANNED.value); state = orch.state.get_task(args.task_id)
    project = orch._load_project(state["project"])
    registry = ProviderRegistry(orch.root / "config" / "models.yaml")
    router = ModelRouter(registry, orch.root / "config" / "routing.yaml", build_benchmark_registry(root))
    context_builder = ContextBuilder(); verifier = Verifier(project, Path(state["worktree"]), project.repo_path)
    reviewer = build_reviewer(registry, Path(state["worktree"]))
    repair_loop = RepairLoop(orch, router, context_builder, verifier, reviewer, max_free_attempts=state.get("max_free_attempts", 2), max_total_repair_rounds=state.get("max_total_repair_rounds", 3))
    artifacts = RunArtifacts(orch.root / "runs" / args.task_id)
    result = Implementer(orch, router, context_builder, verifier, reviewer, repair_loop, artifacts).run(args.task_id)
    if result.success:
        print(f"IMPLEMENTED: {result.provider_id}"); print(f"Modified files: {', '.join(result.modified_files) if result.modified_files else 'none'}"); print(f"Duration: {result.duration_ms}ms"); print(f"Status: {orch.state.get_task(args.task_id).get('status')}")
    else:
        print(f"FAILED: {result.error}"); print(f"Status: {orch.state.get_task(args.task_id).get('status')}"); sys.exit(1)


def cmd_run_many(args):
    try: results = ParallelTaskRunner(find_root()).run(args.task_ids, max_workers=args.workers)
    except ValueError as exc: raise SystemExit(str(exc))
    any_failed = False
    for result in results:
        print(f"=== {result.task_id}: {'PASS' if result.success else 'FAIL'} ===")
        if result.stdout.strip(): print(result.stdout.rstrip())
        if result.stderr.strip(): print(result.stderr.rstrip(), file=sys.stderr)
        any_failed = any_failed or not result.success
    if any_failed: sys.exit(1)


def cmd_run_ready(args):
    orch = Orchestrator(find_root()); ready = DagScheduler(orch.state).ready_tasks(args.task_ids or None)
    if not ready: print("No runnable tasks"); return
    print("Runnable: " + ", ".join(ready)); cmd_run_many(argparse.Namespace(task_ids=ready, workers=args.workers))


def cmd_depends(args):
    orch = Orchestrator(find_root()); scheduler = DagScheduler(orch.state)
    try: scheduler.add_dependencies(args.task_id, args.on)
    except DagError as exc: raise SystemExit(str(exc))
    state = scheduler.dependency_state(args.task_id)
    if state.dependencies and not state.ready: orch.state.update_task(args.task_id, status=TaskStatus.WAITING_DEPENDENCY.value)
    print(f"{args.task_id} depends on: {', '.join(state.dependencies) or 'none'}")


def cmd_dag(args):
    orch = Orchestrator(find_root()); scheduler = DagScheduler(orch.state)
    for task_id, deps in sorted(scheduler.graph().items()):
        state = scheduler.dependency_state(task_id); marker = "READY" if state.ready else ("BLOCKED_FAILED" if state.failed else "WAITING")
        print(f"{task_id}: {marker} <- {', '.join(deps) or 'none'}")


def cmd_verify(args):
    orch = Orchestrator(find_root()); state = orch.state.get_task(args.task_id)
    if not state: raise SystemExit(f"Unknown task: {args.task_id}")
    project = orch._load_project(state["project"]); verifier = Verifier(project, Path(state["worktree"]), project.repo_path)
    report = verifier.run(args.task_id, state["risk"], []); run_dir = orch.root / "runs" / args.task_id; verifier.persist(report, run_dir)
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
    diff = scope_gate.get_diff(); run_dir = orch.root / "runs" / args.task_id; verification_path = run_dir / "verification.json"
    if not verification_path.exists(): raise SystemExit("Verification report not found. Run 'agent verify' first.")
    verification_data = json.loads(verification_path.read_text())
    from .verifier import VerificationReport, VerificationResult
    scope_gate_res = VerificationResult(**verification_data["scope_gate"]) if verification_data.get("scope_gate") else None
    diff_check_res = VerificationResult(**verification_data["diff_check"]) if verification_data.get("diff_check") else None
    command_results = [VerificationResult(**r) for r in verification_data.get("command_results", [])]
    verification_report = VerificationReport(task_id=verification_data["task_id"], risk=verification_data["risk"], scope_gate=scope_gate_res, diff_check=diff_check_res, command_results=command_results, overall_status=verification_data["overall_status"], created_at=verification_data["created_at"])
    registry = ProviderRegistry(orch.root / "config" / "models.yaml"); reviewer = build_reviewer(registry, worktree)
    review_result = reviewer.review(task_id=args.task_id, goal=state["goal"], risk=state["risk"], diff=diff, verification_report=verification_report); reviewer.persist(review_result, run_dir)
    print(f"Review: {review_result.verdict}"); print(f"Notes: {review_result.notes}")
    if review_result.verdict in ("FAIL_SCOPE", "FAIL_CORRECTNESS", "FAIL_TESTS", "FAIL_ARCHITECTURE", "FAIL_SECURITY"): sys.exit(1)


def cmd_resume(args):
    orch = Orchestrator(find_root()); state = orch.state.get_task(args.task_id)
    if not state: raise SystemExit(f"Unknown task: {args.task_id}")
    current_status = state.get("status", ""); print(f"Current status: {current_status}")
    if current_status in (TaskStatus.PLANNED.value, TaskStatus.CONTEXT_READY.value, TaskStatus.IMPLEMENTING.value, TaskStatus.REPAIRING.value, TaskStatus.WAITING_PROVIDER.value, TaskStatus.WAITING_CODEX.value, TaskStatus.WAITING_DEPENDENCY.value): cmd_run(args)
    elif current_status in (TaskStatus.IMPLEMENTED.value, TaskStatus.VERIFYING.value): cmd_verify(args); cmd_review(args)
    elif current_status == TaskStatus.REVIEWING.value: cmd_review(args)
    elif current_status == TaskStatus.READY_FOR_REVIEW.value: print("Task is READY_FOR_REVIEW - no action needed")
    elif current_status in (TaskStatus.COMPLETED.value, TaskStatus.FAILED.value): print(f"Task is in terminal state: {current_status}")
    else: print(f"Unknown state: {current_status}")


def cmd_watchdog(args):
    root = find_root()
    from .watchdog import IntegrationWatchdog
    watchdog = IntegrationWatchdog(root)
    unresolved = watchdog.run()
    blocking = [a for a in unresolved if a.severity == "BLOCKING"]
    high = [a for a in unresolved if a.severity == "HIGH"]
    warnings = [a for a in unresolved if a.severity == "WARNING"]
    metrics = watchdog.registry.health_metrics
    print(f"Watchdog scan complete: {len(unresolved)} active alerts (BLOCKING={len(blocking)}, HIGH={len(high)}, WARNING={len(warnings)})")
    if metrics:
        print(f"Validated orphans: {metrics.validated_orphan_count}")
    if unresolved:
        for a in unresolved:
            print(f"  [{a.severity}] {a.finding_code}: {a.title}")
    if blocking:
        sys.exit(1)


def cmd_alerts(args):
    root = find_root()
    from .watchdog import IntegrationWatchdog
    watchdog = IntegrationWatchdog(root)
    watchdog.registry.load()
    alerts = list(watchdog.registry.alerts.values())
    if not getattr(args, "all", False):
        alerts = [a for a in alerts if not a.resolved]
    if getattr(args, "severity", None):
        alerts = [a for a in alerts if a.severity == args.severity.upper()]
    if not alerts:
        print("No matching alerts.")
        return
    for a in alerts:
        status_str = "RESOLVED" if a.resolved else "ACTIVE"
        print(f"[{status_str}] [{a.severity}] {a.finding_code} ({a.alert_id}): {a.title}")
        print(f"  Message: {a.message}")
        print(f"  Required Action: {a.required_action}")


def cmd_queue(args):
    root = find_root()
    from .watchdog import IntegrationWatchdog
    watchdog = IntegrationWatchdog(root)
    watchdog.registry.load()
    items = [q for q in watchdog.registry.promotion_queue.values() if q.status == "ACTIVE"]
    if not items:
        print("Promotion/Integration queue is empty.")
        return
    for q in items:
        print(f"[{q.severity}] {q.queue_id} (Asset: {q.asset_id}): {q.issue}")
        print(f"  Required Action: {q.required_action}")
        print(f"  Allowed Dispositions: {', '.join(q.allowed_dispositions)}")


def cmd_dashboard(args):
    root = find_root()
    from .watchdog import IntegrationWatchdog
    watchdog = IntegrationWatchdog(root)
    dashboard_text = watchdog.generate_dashboard()
    print(dashboard_text)
    if getattr(args, "save", False):
        out_path = root / "docs" / "project" / "INTEGRATION_DASHBOARD.md"
        out_path.write_text(dashboard_text)
        print(f"\nDashboard saved to {out_path}")


def cmd_phase_closeout(args):
    root = find_root()
    from .watchdog import IntegrationWatchdog
    watchdog = IntegrationWatchdog(root)
    passed, failures = watchdog.check_phase_closeout()
    if passed:
        print("PASS: Repository satisfies all phase closeout invariants.")
    else:
        print("FAIL: Phase closeout rejected:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)


def main_watchdog():
    p = argparse.ArgumentParser(prog="agent-watchdog", description="Run integration watchdog scan")
    cmd_watchdog(p.parse_args())


def main_alerts():
    p = argparse.ArgumentParser(prog="agent-alerts", description="Inspect governance lifecycle alerts")
    p.add_argument("--severity", choices=["INFO", "WARNING", "HIGH", "BLOCKING"])
    p.add_argument("--all", action="store_true")
    cmd_alerts(p.parse_args())


def main_queue():
    p = argparse.ArgumentParser(prog="agent-promotion-queue", description="Display promotion and integration queue")
    cmd_queue(p.parse_args())


def main_dashboard():
    p = argparse.ArgumentParser(prog="agent-dashboard")
    p.add_argument("--save", action="store_true")
    cmd_dashboard(p.parse_args())


def build_parser():
    p = argparse.ArgumentParser(prog="agent", description="Multi-Agent Coder Foundation"); sub = p.add_subparsers(dest="command", required=True)
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
    benchmark = sub.add_parser("benchmark"); benchmark.add_argument("provider_id"); benchmark.add_argument("--repeat", type=int, default=1, choices=range(1, 11)); benchmark.set_defaults(func=cmd_benchmark)
    benchmarks = sub.add_parser("benchmarks"); benchmarks.set_defaults(func=cmd_benchmarks)
    watchdog = sub.add_parser("watchdog"); watchdog.set_defaults(func=cmd_watchdog)
    alerts = sub.add_parser("alerts"); alerts.add_argument("--severity", choices=["INFO", "WARNING", "HIGH", "BLOCKING"]); alerts.add_argument("--all", action="store_true"); alerts.set_defaults(func=cmd_alerts)
    queue = sub.add_parser("queue"); queue.set_defaults(func=cmd_queue)
    dashboard = sub.add_parser("dashboard"); dashboard.add_argument("--save", action="store_true"); dashboard.set_defaults(func=cmd_dashboard)
    closeout = sub.add_parser("phase-closeout"); closeout.set_defaults(func=cmd_phase_closeout)
    return p


def main():
    args = build_parser().parse_args(); args.func(args)


if __name__ == "__main__":
    main()

