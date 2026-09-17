# Multi-Agent Coder — Validation Registry

This registry tracks all automated, deterministic, and empirical validation suites.

---

## 1. Automated Test Suite

- **Framework**: `pytest`
- **Configuration**: `pyproject.toml`
- **Total Tests**: 53
- **Latest Result**: 53 PASS, 0 FAIL (100% pass rate)

### Test Modules & Coverage Breakdown

| Test File | Test Count | Focus Area | Status |
|---|---|---|---|
| `tests/test_dag.py` | 5 | DAG construction, cycle detection, topological staging | PASS |
| `tests/test_parallel.py` | 4 | Parallel task execution across isolated worktrees | PASS |
| `tests/test_github_pr.py` | 7 | GitHub PR lifecycle, idempotent creation, auto-merge guards | PASS |
| `tests/test_benchmark_registry.py` | 6 | SQLite benchmark store, admission state machine, router integration | PASS |
| `tests/test_production_hardening.py` | 8 | Doctor diagnostics, system crash recovery, runtime locking | PASS |
| `tests/test_multi_reviewer.py` | 4 | Multi-reviewer panel, quorum logic, security/scope vetoes | PASS |
| `tests/test_phase35_remediation.py` | 6 | argv subprocess runner, path containment, symlinks, repo baseline protection, repair loop | PASS |
| `tests/test_repair_context.py` | 4 | Failure context serialization, error injection, checkpoint restoration | PASS |
| `tests/test_ollama_provider.py` | 4 | Ollama HTTP client, error handling, mock implementer/reviewer | PASS |
| `tests/test_codex_provider.py` | 2 | Codex CLI provider configuration and execution arguments | PASS |
| `tests/test_project.py` | 1 | Project config parsing and validation timeouts | PASS |
| `tests/test_risk.py` | 1 | Path-based risk evaluation rules | PASS |
| `tests/test_state.py` | 1 | SQLite StateStore task CRUD and atomic transitions | PASS |

---

## 2. Deterministic Static & Security Checks

### 2.1 Python Compilation Check
- **Command**: `python -m compileall -q agent_system`
- **Requirement**: Zero syntax or byte-compilation errors.
- **Status**: PASS

### 2.2 Shell Injection Prohibition Check
- **Command**: `grep -R "shell=True" agent_system tests config`
- **Requirement**: No occurrences of `shell=True` across the entire codebase. Subprocesses must use argv-based execution.
- **Status**: PASS (0 occurrences found)

### 2.3 Runtime State Non-Tracking Check
- **Command**: `git ls-files | grep -E '(^|/)(state|benchmarks)\.db$|(^|/)runs/|(^|/)\.agent-system/'`
- **Requirement**: SQLite databases, runtime worktrees, and log runs must be gitignored and never committed.
- **Status**: PASS (Clean)

### 2.4 GitHub Actions Security Check
- **Command**: Inspection for unpinned third-party actions (`uses: ...@vX` vs full SHA)
- **Requirement**: All GitHub Actions must be SHA-pinned in workflows.
- **Status**: PASS

---

## 3. Pre-Flight Diagnostics (`agent-doctor`)

Command: `agent-doctor`

| Check Category | Target | Result | Note |
|---|---|---|---|
| `binary:git` | `/usr/bin/git` | PASS | Installed and executable |
| `binary:gh` | `/usr/bin/gh` | PASS | GitHub CLI installed |
| `config:models` | `config/models.yaml` | PASS | 4 providers configured and valid |
| `config:routing` | `config/routing.yaml` | PASS | Zero routing drift; all referenced providers exist |
| `config:benchmarks` | `config/benchmarks.yaml` | PASS | Valid YAML policy |
| `state:integrity` | `.agent-system/state.db` | PASS / WARN | Clean schema initialized upon first task run |

---

## 4. Empirical Benchmark Validation Policy

Configured in `config/benchmarks.yaml`:

- **Runs Required**: 2 consecutive runs
- **Required Pass Rate**: 1.0 (100%) for `VALIDATED` status
- **Rejection Threshold**: < 0.5 (50%) drops model to `REJECTED`
- **Router Integration**: Router excludes any model in `REJECTED` state regardless of configuration tier.
- **Current Model Benchmark States**:
  - `mock-free`: VALIDATED (in test harness)
  - `codex-real`: UNBENCHMARKED / EXPERIMENTAL
  - `ollama-free`: UNBENCHMARKED / EXPERIMENTAL

