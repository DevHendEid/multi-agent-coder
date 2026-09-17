# Multi-Agent Coder — Project Roadmap

Current development status and priority sequence following the **Integration First + Continuous Main Integration** operating strategy.

---

## 1. Completed Phases (Authoritatively on `main`)

- [x] **Phase 1–3**: Core orchestrator, state management, git worktree isolation, scope gates, deterministic verifier, AI reviewer, and repair loop.
- [x] **Phase 3.5**: Security hardening: removal of `shell=True`, workspace path containment, symlink escape checks, baseline repository protection, real Codex reviewer wiring.
- [x] **Phase 4**: Independent review panel with multi-reviewer quorum and human override boundary for critical risks.
- [x] **Phase 5**: Parallel worktree task execution (`ParallelExecutor`).
- [x] **Phase 6**: Task DAG dependencies, cycle detection, and topological execution staging (`TaskDAG`).
- [x] **Phase 7**: Model benchmark registry with empirical admission states (`BenchmarkRegistry`).
- [x] **Phase 8**: GitHub PR CLI integration and guarded auto-merge automation (`GitHubPRManager`).
- [x] **Phase 9**: Production hardening: system diagnostics (`Doctor`), crash recovery (`SystemRecovery`), and runtime locking (`RuntimeLock`).
- [x] **Repository & Worktree Hygiene**: Pruned stale detached worktrees; verified 10 phase branches merged to `main`; preserved `chore/github-toolkit`.

---

## 2. Active Priorities & Next Tasks

### Priority 1: Final End-to-End Production Integration Test
- **Goal**: Execute a complete, realistic lifecycle run of `agent` on a local project contract: Task Contract -> Worktree Creation -> Mock/Ollama Implementation -> Deterministic Verification -> Independent Review -> PR Packaging.
- **Consumer**: Production Orchestration Pipeline
- **Target**: `main`

### Priority 2: Real Free Provider Live Validation
- **Goal**: Validate `ollama-free` on the local machine with live acceptance tests (implementation, review, failure handling, and bounded repair).
- **Target State**: Transition from `EXPERIMENTAL` to `VALIDATED` in `BenchmarkRegistry`.
- **Blocker**: Host Ollama service running with target models pulled (`OLLAMA_MODEL`, `OLLAMA_REVIEW_MODEL`).

### Priority 3: GitHub Toolkit Reconciliation (`chore/github-toolkit`)
- **Goal**: Integrate unmerged security workflows, CodeQL, Dependabot, and issue templates from `origin/chore/github-toolkit` (commit `77c5b53`).
- **Pipeline**: Rebase on `main` -> PR -> CI Pass -> Guarded Auto-Merge -> Confirm on `main`.
- **Consumer**: GitHub repository maintenance and continuous integration.
