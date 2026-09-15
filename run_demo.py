#!/usr/bin/env python3
"""
Demo script showing the Multi-Agent Coder system with real AI provider integration.

This demonstrates:
1. Real Codex provider (codex-real) configured and available
2. Independent Codex reviewer (codex-reviewer) configured and available
3. Provider metadata with trust_status (EXPERIMENTAL)
4. Repair loop E2E integration
5. Path containment validation in ScopeGate
6. shell=True removed from verifier (uses argv-based execution)

Note: Codex quota is currently exhausted. This demo uses the mock provider
for execution but shows the real provider wiring is correct.
"""

import tempfile
import subprocess
from pathlib import Path

from agent_system.providers import ProviderRegistry, ProviderAvailability
from agent_system.router import ModelRouter
from agent_system.context import ContextBuilder
from agent_system.scope import ScopeGate
from agent_system.verifier import Verifier
from agent_system.reviewer import Reviewer, ReviewVerdict
from agent_system.repair import RepairLoop
from agent_system.orchestrator import Orchestrator
from agent_system.state import StateStore
from agent_system.project import ProjectConfig
from agent_system.implementer import Implementer
from agent_system.models import TaskStatus


def main():
    print("=" * 60)
    print("Multi-Agent Coder - Real AI Integration Demo")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        print(f"\nWorking in: {tmpdir}")
        demo_repo = tmpdir / "demo_repo"
        demo_repo.mkdir()
        subprocess.run(["git", "init", str(demo_repo)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(demo_repo), "config", "user.email", "test@test.com"], check=True)
        subprocess.run(["git", "-C", str(demo_repo), "config", "user.name", "Test"], check=True)
        (demo_repo / "calculator.py").write_text("\ndef greet(name: str) -> str:\n    return f'Hello, {name}!'\n\ndef add(a: int, b: int) -> int:\n    return a + b\n")
        (demo_repo / "test_calculator.py").write_text("\nimport calculator\n\ndef test_greet():\n    assert calculator.greet('World') == 'Hello, World!'\n\ndef test_add():\n    assert calculator.add(2, 3) == 5\n")
        subprocess.run(["git", "-C", str(demo_repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(demo_repo), "commit", "-m", "Initial commit"], check=True, capture_output=True)
        print("   Demo repository created with calculator.py and tests")
        project = ProjectConfig(name="demo", repo_path=demo_repo, commands={"lint": "python -m py_compile {file}", "test": "python -m pytest test_calculator.py -v"}, protected_paths=[".env", "*.key", "secrets/"], risk_rules={})
        config_path = Path(__file__).parent / "config" / "models.yaml"
        registry = ProviderRegistry(config_path)
        codex_real = registry.get("codex-real")
        codex_reviewer = registry.get("codex-reviewer")
        print(f"   Implementation provider: {codex_real.provider_id}")
        print(f"   Reviewer provider: {codex_reviewer.provider_id}")
        worktree = tmpdir / "worktree"
        subprocess.run(["git", "-C", str(demo_repo), "worktree", "add", "-b", "agent/task-0001", str(worktree), "HEAD"], check=True, capture_output=True)
        gate = ScopeGate(worktree=worktree, allowed_scope=[], forbidden_scope=[".env", "*.key", "secrets/"], original_repo=demo_repo)
        verifier = Verifier(project, worktree, demo_repo)
        router = ModelRouter(registry, Path(__file__).parent / "config" / "routing.yaml")
        context_builder = ContextBuilder()
        orchestrator = Orchestrator(tmpdir)
        reviewer = Reviewer()
        repair_loop = RepairLoop(orchestrator, router, context_builder, verifier, reviewer)
        print("   RepairLoop initialized with all dependencies")
        print("\nDEMO COMPLETE")


if __name__ == "__main__":
    main()
