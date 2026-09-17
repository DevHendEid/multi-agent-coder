# Multi-Agent Coder — Authoritative Source of Truth

## 1. Authority Hierarchy

The authoritative source of truth for all active production behavior, code, and operational policy is the **`main`** branch of this repository.

### Rule of Precedence
1. **Authoritative Active Code**: The tree on `main` defines currently active capabilities. No feature, provider, model integration, adapter, runtime behavior, validation gate, or production policy is considered active or integrated unless it exists on `main`.
2. **Authoritative Governance**: The markdown documents under `docs/project/` define project architecture, component status, validation state, lifecycle dispositions, and promotion gates.
3. **Historical / Evidence Documents**: Files such as `FINAL_REPORT.md` represent point-in-time snapshot assessments and are marked as `HISTORICAL` or `PARTIALLY_SUPERSEDED`.
4. **Temporary Environments**: Feature branches, task worktrees, and unmerged pull requests are transient implementation workspaces and NEVER act as authoritative project state.

---

## 2. Document Status Taxonomy

All documentation within the repository must carry an explicit status tag:

| Tag | Meaning | Authority Level |
|---|---|---|
| `STILL_AUTHORITATIVE` | Current, binding operational or architectural standard | High (Binding) |
| `PARTIALLY_SUPERSEDED` | Contains historically valid context, but parts are superseded by later phases | Medium (Contextual) |
| `SUPERSEDED` | Deprecated, superseded by a versioned successor | Low (Historical record only) |
| `HISTORICAL` | Point-in-time milestone report, immutable audit trail | Audit / Verification |

---

## 3. Core Operating Strategy & Lifecycle States

Every asset, component, task, and branch must resolve into exactly one of four binding lifecycle dispositions:

1. **`INTEGRATE`**: Merged into `main`, verified via CI and post-merge checks, reachable from the production gateway.
2. **`KEEP_SHADOW`**: Validated and kept for comparison, shadow-execution, or non-production reference; not routable in active production paths.
3. **`DEFER_WITH_BLOCKER`**: Implementation exists, but cannot merge to `main` due to an explicit, documented technical or policy blocker.
4. **`ARCHIVE`**: Obsolete, replaced, or failed candidate preserved strictly for historical reproducibility.

### Authoritative Completion Equation
```text
WORKTREE_COMPLETE != PROJECT_COMPLETE
BRANCH_COMPLETE   != PROJECT_COMPLETE
PR_OPEN           != PROJECT_COMPLETE
CI_PASS           != PROJECT_COMPLETE
```
A production-targeted task reaches `PROJECT_COMPLETE` if and only if:
1. Deterministic verification passes.
2. Independent review passes or passes with notes.
3. PR is created and verified by CI.
4. Merge into `main` is executed under risk policy.
5. Merge SHA is verified reachable from `main`.
6. Post-merge verification checks pass.
7. Temporary worktrees and branches are pruned.
8. An integration receipt is persisted.

---

## 4. Current Repository Baseline

- **Repository**: `multi-agent-coder`
- **Authoritative Branch**: `main`
- **Active Commit**: `4c032a5` (*Fix doctor routing config drift*)
- **Phases Merged to Main**:
  - Phase 1–3: Core orchestrator, state store, worktrees, scope gate, verifier, reviewer, repair loop.
  - Phase 3.5: argv subprocess refactoring (removal of `shell=True`), path containment, symlink escape checks, baseline repo protection, provider classification.
  - Phase 4: Independent review panel with multi-reviewer quorum (`IndependentReviewPanel`).
  - Phase 5: Parallel task execution across isolated worktrees (`ParallelExecutor`).
  - Phase 6: DAG task dependencies and topological scheduling (`TaskDAG`).
  - Phase 7: Model benchmark registry and admission classification (`BenchmarkRegistry`).
  - Phase 8: GitHub PR integration CLI and guarded auto-merge automation (`GitHubPRManager`).
  - Phase 9: Production hardening, doctor diagnostics (`Doctor`), crash recovery (`SystemRecovery`), and runtime lock management (`RuntimeLock`).
