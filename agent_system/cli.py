from __future__ import annotations

import argparse
from pathlib import Path
import sys
import yaml
from .orchestrator import Orchestrator
from .project import ProjectConfigError
from .worktree import WorktreeError


def find_root() -> Path:
    return Path.cwd()


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
        "commands": {"analyze": "", "test": "", "build": ""},
        "protected_paths": [".env", "secrets/"],
        "risk_rules": {"docs": "low", "tests": "low", "architecture": "high", "security": "critical"},
    }
    out.write_text(yaml.safe_dump(payload, sort_keys=False))
    print(out)


def cmd_task(args):
    orch = Orchestrator(find_root())
    try:
        result = orch.create_task(args.project, args.goal)
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

    return p


def main():
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
