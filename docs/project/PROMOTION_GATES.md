# Multi-Agent Coder — Promotion Gates & Release Policy

This document establishes the dual-gate architecture distinguishing **Engineering Integration** from **Production / Scientific Promotion**.

---

## 1. Dual-Gate Architecture

```
[Worktree Implementation]
          │
          ▼
┌─────────────────────────────────┐
│     GATE 1: ENGINEERING MERGE   │
│  - Deterministic Verifier PASS  │
│  - Independent Review QUORUM    │
│  - Scope & Containment Checks   │
│  - GitHub CI Checks Green       │
│  - Guarded Auto-Merge by Risk   │
└────────────────┬────────────────┘
                 │
                 ▼
          Merged into `main`
                 │
                 ▼
┌─────────────────────────────────┐
│   GATE 2: PRODUCTION PROMOTION  │
│  - Live Empirical Benchmarks    │
│  - Model Admission State        │
│  - Safety & Security Audit      │
│  - Explicit Human Sign-off      │
│  - Feature Flag Activation      │
└────────────────┬────────────────┘
                 │
                 ▼
      PRODUCTION_ACTIVE
```

### Engineering Integration (Gate 1)
Merges validated code into the authoritative `main` branch. This guarantees that code is syntactically sound, verified by deterministic tests, formatted cleanly, contained within allowed scopes, and passing CI.

### Production / Scientific Promotion (Gate 2)
Controls whether integrated code is actively routed to real users or production traffic. Scientific models, new LLM providers, and safety-critical features may be merged into `main` behind feature flags or marked as `EXPERIMENTAL`/`SHADOW` while awaiting live empirical qualification.

---

## 2. Gate 1: Engineering Auto-Merge & Risk Policy

Configured in `agent_system/risk.py` and `agent_system/github_pr.py`:

| Risk Tier | Risk Definition | Verification Requirements | Review Quorum | Auto-Merge Eligibility |
|---|---|---|---|---|
| **LOW** | Documentation, comments, test additions | Deterministic verifier PASS | Reviewer PASS or PASS_WITH_NOTES | **Eligible** after CI green |
| **MEDIUM** | Standard component features, internal helpers | Full test suite PASS | Reviewer PASS or PASS_WITH_NOTES | **Eligible** after CI green |
| **HIGH** | Architectural refactors, provider routing changes | Full test suite + Regression tests PASS | Multi-Reviewer Quorum PASS | **Requires Human Approval** (Auto-merge blocked by default) |
| **CRITICAL** | Security, scope boundaries, credential handling | Full test suite + Security assertions PASS | Multi-Reviewer Quorum PASS + Security Sign-off | **NEVER Auto-Merge** (Explicit Human Approval Mandatory) |

### Strict Rule on Critical Risk
An AI `PASS` verdict can NEVER finalize or auto-merge a `CRITICAL` risk task. The task state machine will transition to `WAITING_HUMAN`.

---

## 3. Gate 2: Model & Provider Admission Gates

Configured in `agent_system/benchmark.py` and `config/benchmarks.yaml`:

```
   [NEW PROVIDER]
          │
          ▼
    UNBENCHMARKED
          │  (1st perfect run: 100% pass)
          ▼
      CANDIDATE
          │  (2nd consecutive perfect run)
          ▼
      VALIDATED  ─────────► Eligible for Production Routing
          │
          ▼ (< 50% pass rate)
      REJECTED   ─────────► Strictly Excluded by ModelRouter
```

1. **UNBENCHMARKED**: Newly declared provider. Prohibited from production high/critical routing.
2. **CANDIDATE**: Passed 1 standard benchmark suite without failures.
3. **VALIDATED**: Passed 2 consecutive benchmark runs at 100% pass rate. Eligible for promotion into production routing.
4. **REJECTED**: Fell below 50% pass rate. Automatically dropped by `ModelRouter` even if declared in `config/routing.yaml`.

---

## 4. Worktree and Branch Hygiene Gate

Before closing any engineering phase or task:
- All disposable worktrees must be pruned.
- The merge commit must be proven reachable from `main`.
- Temporary task branches must be deleted once merged.
- Integration receipts must be appended to `INTEGRATION_RECEIPTS.md`.
