# Multi-Agent Coder — Superseded Index

Historical log of architectural patterns, modules, configurations, and artifacts that have been superseded by versioned successors. Preserved for reproducibility and audit provenance.

---

## 1. Superseded Implementations & Patterns

| Predecessor Pattern / Component | Superseding Implementation | Phase | Rationale |
|---|---|---|---|
| Subprocess execution with `shell=True` | Argv list execution via `shlex.split` / direct list | Phase 3.5 | Security: Eliminates arbitrary shell injection risks. |
| Unbounded repair attempts | Bounded `RepairLoop` with filesystem checkpoints & rollback | Phase 3.5 | Prevents runaway API cost, negative code progress, and worktree corruption. |
| Review prompt written to worktree | Review artifacts stored outside worktree in `runs/<task_id>/` | Phase 3.5 | Prevents reviewer metadata from polluting git diffs and task scope. |
| In-memory task dicts | SQLite persistent `StateStore` (`.agent-system/state.db`) | Phase 5 | Crash resilience, transactional status updates, multi-process support. |
| Serial task queue | `TaskDAG` dependency resolver + `ParallelExecutor` | Phases 5 & 6 | Enables concurrent worktree execution for independent tasks. |
| Single AI reviewer verdict | `IndependentReviewPanel` with quorum and security veto | Phase 4 | Prevents AI hallucination from overriding security policies. |
| Static YAML trust status | Empirical `BenchmarkRegistry` admission states | Phase 7 | Ensures models earn admission through reproducible benchmark runs. |
| Manual git branch/push/PR | `GitHubPRManager` (`agent-pr`) CLI | Phase 8 | Idempotent PR lifecycle and guarded auto-merge automation. |
| Unchecked configuration drift | `Doctor` (`agent-doctor`) diagnostic pre-flight checks | Phase 9 | Fails early in CI/CD when providers or routing configurations drift. |

---

## 2. Superseded Artifacts & Worktrees

- **Worktree `dull-candytuft`**: Stale worktree at commit `8fc4086` left over from Phase 9. Pruned on 2026-09-17 after verifying zero unmerged work.
- **`FINAL_REPORT.md`**: Marked `HISTORICAL`. Captures Phase 3.5 audit snapshot; superseded for ongoing architecture by `docs/project/`.
