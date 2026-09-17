# Multi-Agent Coder — Component Status Registry

This registry tracks every component in the repository.

*Note on Statuses:*
- **Engineering Integration**: Merged to `main`, passing tests and CI (`MERGED_TO_MAIN`).
- **Production Status**: Reachable from the production gateway with validated runtime readiness (`PRODUCTION_ACTIVE`, `COMPLETE_REUSABLE`, `SHADOW`, or `EXPERIMENTAL`).

---

## 1. Core Architecture & Orchestration

### Orchestrator
- **Purpose**: Central state machine coordinating task lifecycle from PLANNED to READY_FOR_REVIEW.
- **Implementation Path**: `agent_system/orchestrator.py`
- **Consumer**: `agent_system/cli.py`, `agent_system/parallel.py`
- **Lifecycle Disposition**: `INTEGRATE`
- **Production Reachability**: YES
- **Validation Status**: VALIDATED (unit + integration tests passing)
- **Integration Status**: PRODUCTION_ACTIVE
- **Blocker**: NONE

### StateStore
- **Purpose**: Persistent SQLite backing store for tasks, repair attempts, and status transitions.
- **Implementation Path**: `agent_system/state.py`
- **Consumer**: `Orchestrator`, `ParallelExecutor`, `GitHubPRManager`, `SystemRecovery`
- **Lifecycle Disposition**: `INTEGRATE`
- **Production Reachability**: YES
- **Validation Status**: VALIDATED (unit + integration tests passing)
- **Integration Status**: PRODUCTION_ACTIVE
- **Blocker**: NONE

### TaskDAG
- **Purpose**: Directed acyclic graph builder, cycle detector, and topological scheduler.
- **Implementation Path**: `agent_system/dag.py`
- **Consumer**: `agent_system/orchestrator.py`, `agent_system/parallel.py`
- **Lifecycle Disposition**: `INTEGRATE`
- **Production Reachability**: YES
- **Validation Status**: VALIDATED (`tests/test_dag.py` passing)
- **Integration Status**: PRODUCTION_ACTIVE
- **Blocker**: NONE

### ParallelExecutor
- **Purpose**: Concurrent execution of independent task stages across isolated git worktrees.
- **Implementation Path**: `agent_system/parallel.py`
- **Consumer**: `agent_system/cli.py`
- **Lifecycle Disposition**: `INTEGRATE`
- **Production Reachability**: YES
- **Validation Status**: VALIDATED (`tests/test_parallel.py` passing)
- **Integration Status**: PRODUCTION_ACTIVE
- **Blocker**: NONE

### RuntimeLock
- **Purpose**: Process-level locking to prevent concurrent state corruption across task runners.
- **Implementation Path**: `agent_system/runtime_lock.py`
- **Consumer**: `agent_system/orchestrator.py`
- **Lifecycle Disposition**: `INTEGRATE`
- **Production Reachability**: YES
- **Validation Status**: VALIDATED (`tests/test_production_hardening.py` passing)
- **Integration Status**: PRODUCTION_ACTIVE
- **Blocker**: NONE

---

## 2. Isolation, Verification & Quality Control

### WorktreeManager
- **Purpose**: Lifecycle management of temporary git worktrees (`.agent-worktrees/`).
- **Implementation Path**: `agent_system/worktree.py`
- **Consumer**: `Orchestrator`, `ParallelExecutor`, `SystemRecovery`
- **Lifecycle Disposition**: `INTEGRATE`
- **Production Reachability**: YES
- **Validation Status**: VALIDATED (`tests/test_worktree.py` passing)
- **Integration Status**: PRODUCTION_ACTIVE
- **Blocker**: NONE

### ScopeGate
- **Purpose**: Enforces workspace path containment, blocks symlink escapes, verifies whitespace, protects baseline repo.
- **Implementation Path**: `agent_system/scope.py`
- **Consumer**: `Implementer`, `Verifier`, `RepairLoop`
- **Lifecycle Disposition**: `INTEGRATE`
- **Production Reachability**: YES
- **Validation Status**: VALIDATED (`tests/test_phase35_remediation.py` passing)
- **Integration Status**: PRODUCTION_ACTIVE
- **Blocker**: NONE

### Verifier
- **Purpose**: Deterministic test and check runner; argv subprocess execution with zero `shell=True`.
- **Implementation Path**: `agent_system/verifier.py`
- **Consumer**: `Orchestrator`, `RepairLoop`
- **Lifecycle Disposition**: `INTEGRATE`
- **Production Reachability**: YES
- **Validation Status**: VALIDATED (`tests/test_phase35_remediation.py` passing)
- **Integration Status**: PRODUCTION_ACTIVE
- **Blocker**: NONE

### Reviewer & IndependentReviewPanel
- **Purpose**: Multi-agent review panel with quorum logic, schema validation, and human override enforcement.
- **Implementation Path**: `agent_system/reviewer.py`, `agent_system/multi_reviewer.py`
- **Consumer**: `Orchestrator`, `RepairLoop`
- **Lifecycle Disposition**: `INTEGRATE`
- **Production Reachability**: YES
- **Validation Status**: VALIDATED (`tests/test_multi_reviewer.py` passing)
- **Integration Status**: PRODUCTION_ACTIVE
- **Blocker**: NONE

### RepairLoop
- **Purpose**: Bounded iterative repair with filesystem checkpoints, failure context injection, and max-attempt halts.
- **Implementation Path**: `agent_system/repair.py`
- **Consumer**: `Orchestrator`
- **Lifecycle Disposition**: `INTEGRATE`
- **Production Reachability**: YES
- **Validation Status**: VALIDATED (`tests/test_repair_context.py`, `tests/test_phase35_remediation.py` passing)
- **Integration Status**: PRODUCTION_ACTIVE
- **Blocker**: NONE

---

## 3. Provider Subsystem & Model Routing

### ProviderRegistry & Base
- **Purpose**: Provider abstraction, capability discovery, and dynamic config instantiation.
- **Implementation Path**: `agent_system/providers/base.py`, `agent_system/providers/registry.py`
- **Consumer**: `ModelRouter`, `Implementer`, `Reviewer`, `Doctor`
- **Lifecycle Disposition**: `INTEGRATE`
- **Production Reachability**: YES
- **Validation Status**: VALIDATED
- **Integration Status**: PRODUCTION_ACTIVE
- **Blocker**: NONE

### ModelRouter
- **Purpose**: Selects providers by task risk tier and filters out benchmark-REJECTED models.
- **Implementation Path**: `agent_system/router.py`
- **Consumer**: `Implementer`, `RepairLoop`
- **Lifecycle Disposition**: `INTEGRATE`
- **Production Reachability**: YES
- **Validation Status**: VALIDATED
- **Integration Status**: PRODUCTION_ACTIVE
- **Blocker**: NONE

### BenchmarkRegistry
- **Purpose**: SQLite benchmark suite (`benchmarks.db`) evaluating models across deterministic runs.
- **Implementation Path**: `agent_system/benchmark.py`
- **Consumer**: `ModelRouter`, `agent_system/cli.py`
- **Lifecycle Disposition**: `INTEGRATE`
- **Production Reachability**: YES
- **Validation Status**: VALIDATED (`tests/test_benchmark_registry.py` passing)
- **Integration Status**: PRODUCTION_ACTIVE
- **Blocker**: NONE

### MockProvider (`mock-free`, `mock-premium`)
- **Purpose**: Deterministic provider for automated unit/integration tests.
- **Implementation Path**: `agent_system/providers/mock.py`
- **Consumer**: Pytest test suite
- **Lifecycle Disposition**: `KEEP_SHADOW`
- **Production Reachability**: NO (explicitly disabled in production config outside tests)
- **Validation Status**: VALIDATED
- **Integration Status**: SHADOW
- **Blocker**: NONE

### Codex Providers (`codex-real`, `codex-reviewer`)
- **Purpose**: Premium model provider integrating the Codex CLI tool for implementation and review.
- **Implementation Path**: `agent_system/providers/codex.py`
- **Consumer**: `ModelRouter` (high/critical tiers)
- **Lifecycle Disposition**: `INTEGRATE`
- **Production Reachability**: YES
- **Validation Status**: EXPERIMENTAL (code-level wiring verified in tests; requires live API quota)
- **Integration Status**: EXPERIMENTAL
- **Blocker**: External API quota / credential availability

### Ollama Providers (`ollama-free`, `ollama-reviewer`)
- **Purpose**: Free, local model provider integrating the Ollama HTTP API.
- **Implementation Path**: `agent_system/providers/ollama.py`
- **Consumer**: `ModelRouter` (low/medium tiers)
- **Lifecycle Disposition**: `DEFER_WITH_BLOCKER`
- **Production Reachability**: YES (configured in routing)
- **Validation Status**: EXPERIMENTAL (mock tests pass in `tests/test_ollama_provider.py`)
- **Integration Status**: EXPERIMENTAL
- **Blocker**: Live daemon verification and full acceptance test run pending on host

---

## 4. Diagnostics, Recovery & Pull Request Automation

### GitHubPRManager (`agent-pr`)
- **Purpose**: CLI for idempotent PR creation, CI status checks, and guarded auto-merge.
- **Implementation Path**: `agent_system/github_pr.py`
- **Consumer**: `agent-pr` entry point, CI automation
- **Lifecycle Disposition**: `INTEGRATE`
- **Production Reachability**: YES
- **Validation Status**: VALIDATED (`tests/test_github_pr.py` passing)
- **Integration Status**: PRODUCTION_ACTIVE
- **Blocker**: NONE

### Doctor (`agent-doctor`)
- **Purpose**: Diagnostic health and configuration drift assertions.
- **Implementation Path**: `agent_system/doctor.py`
- **Consumer**: `agent-doctor` entry point, CI workflow
- **Lifecycle Disposition**: `INTEGRATE`
- **Production Reachability**: YES
- **Validation Status**: VALIDATED (runs clean in local and CI checks)
- **Integration Status**: PRODUCTION_ACTIVE
- **Blocker**: NONE

### SystemRecovery (`agent-recover`)
- **Purpose**: Reconciles interrupted executions, prunes dead worktrees, and verifies database integrity.
- **Implementation Path**: `agent_system/recovery.py`
- **Consumer**: `agent-recover` entry point
- **Lifecycle Disposition**: `INTEGRATE`
- **Production Reachability**: YES
- **Validation Status**: VALIDATED (`tests/test_production_hardening.py` passing)
- **Integration Status**: PRODUCTION_ACTIVE
- **Blocker**: NONE
