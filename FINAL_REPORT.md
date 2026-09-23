# Multi-Agent Coder — Current Verification Report

## Status

Phase 1–3 are implemented. Phase 3.5 hardening is substantially implemented and the previously identified code-level gaps have been remediated in this snapshot.

## Remediations completed

- Generic command execution uses argv-based subprocess execution; no `shell=True` remains in project code.
- Path containment and symlink escape checks are present.
- Original-repository protection now compares against a recorded baseline, so unrelated pre-existing local changes do not falsely fail a task if they remain unchanged.
- Codex implementation provider exists.
- Codex reviewer provider is now wired into the real review path through `ProviderReviewer` instead of existing only as unused code.
- Provider-backed reviewer output is schema-validated.
- Deterministic verification failures override any AI `PASS` verdict.
- Critical-risk tasks cannot be finalized by an AI `PASS`; they require human review.
- Reviewer prompt/output artifacts are no longer written into task worktrees.
- Fallback scope checking now records its baseline before fallback execution.
- Provider failure classification now uses the provider's classified error and preserves `WAITING_CODEX` / `WAITING_PROVIDER` correctly when no fallback exists.
- Repair success no longer references a non-existent `VerificationReport.diff` field.
- A real repair-loop end-to-end test now exercises: failing implementation -> provider repair -> verification rerun -> reviewer -> READY_FOR_REVIEW.
- Production provider config no longer silently selects mock providers before real providers; mocks are disabled by default outside tests.

## Automated verification

Local test command:

```bash
python3 -m pytest -q
```

Result for this snapshot:

**25 passed**

Additional checks:
- Python compile check passes.
- No `shell=True` found under `agent_system`, `tests`, or `config`.

## Remaining limitation

A real **free** provider is still not validated. The current real provider integration is Codex, configured as premium/experimental. A free provider should only be marked `VALIDATED` after it is actually callable on the target machine and passes real implementation, review, failure, and repair acceptance tests.

## Verdict

**PHASE 3.5 CODE REMEDIATION VERIFIED — REAL FREE PROVIDER VALIDATION STILL REQUIRED**

The project is ready to be stored in a private GitHub repository and continued from this point. Do not claim full Phase 3.5 closure until a real free provider has passed the required live acceptance tests.

## Current continuation audit — 2026-09-17

This continuation did not restart the prior audit/E2E run and did not discard `runs/`, `.agent-system/`, or completed results.

### Ollama probe

- `ollama ps`: model loaded during probe.
- `ollama list`: `qwen2.5-coder:7b` is installed.
- `GET http://127.0.0.1:11434/api/version`: HTTP 200, `{"version":"0.34.0"}`.
- Direct E2E production provider probe executed (`runs/OLLAMA-PROBE/implementer-result.json`):
  - provider = ollama-free
  - model = qwen2.5-coder:7b
  - timeout_seconds = 120
  - elapsed_seconds = 11.996
  - result.success = true
  - result.modified_files = ["README.md"]
  - diff_check_exit = 0
  - fallback_used = false
  - providence = "Direct production provider execute() on isolated git worktree"
- **REAL OLLAMA IMPLEMENTER = PASS**
- **REAL OLLAMA REVIEWER = NOT RE-PROVEN / VALIDATED FROM PRIOR EVIDENCE** (prior persisted benchmark records remain unchanged: `ollama-reviewer` is `VALIDATED` from prior evidence).
- **BLOCKER = NONE**

### Non-Ollama checks

- PR #14 is `MERGED` at merge SHA `35159b7b9cb2cfc1b033aa7cf6ef0d890bf255b8`.
- PR #15 is `MERGED` at merge SHA `200d9a46bbdc0ff8adad7ca04c81c1e9ffc8f59b`; both GitHub test checks succeeded.
- PR #16 is `MERGED` at merge SHA `a50a016a7fc8a70d46477959645aa6f5dba53cc8`; both GitHub test checks succeeded.
- Local ancestry checks for all merge commits passed.
- 75 pytest tests passed across watchdog, phase closeout, repair, DAG, parallel execution, leases, recovery, negative controls, integration enforcement, GitHub PR handling, benchmark registry, reviewer, worktree, state, risk, project, and Codex configuration.
- Watchdog: 0 active alerts, 0 blocking alerts, 0 high alerts; phase closeout `PASS`; doctor checks all `PASS`.
- The `TASK-0007` integration receipt and merge SHA were preserved; the merge SHA is an ancestor of the local E2E repository `main`.
- Main worktree is clean, `git fsck` passes, and zero stale detached worktrees exist. E2E worktrees and run artifacts were archived and preserved.

### Validation

**VALIDATION = PASS**

**BLOCKER = NONE**

## Final V1 release closeout — 2026-09-23

Scope was limited to the three V1 closeout items. No features were added and no completed phases were reopened.

### 1. Watchdog read-only mode

- `IntegrationWatchdog.run(persist=...)` now controls whether governance registries are written. `persist=False` evaluates everything in memory only.
- `agent-doctor`, `agent phase-closeout`, and `agent dashboard` run the watchdog with `persist=False` and no longer modify tracked files (previously every run rewrote `docs/project/PROJECT_HEALTH.json`).
- An explicit `agent-watchdog` / `agent watchdog` run still persists `ALERTS.json`, `PROMOTION_QUEUE.json`, and `PROJECT_HEALTH.json`.
- CI now runs `agent phase-closeout` after `agent-doctor` and fails on `git diff --exit-code`.
- Regression tests show `git status --porcelain` stays empty after doctor, phase-closeout, and dashboard, that read-only runs still report failures, and that explicit runs persist.

### 2. Watchdog fails closed on state DB errors

- The watchdog reads `.agent-system/state.db` through a SQLite read-only (`mode=ro`) connection. It never creates or migrates the DB.
- A missing DB still means no runtime tasks (fresh checkout / CI). If the DB exists but cannot be opened or read (corrupt file, directory, missing `tasks` table), the watchdog emits one deterministic `BLOCKING` alert `ALERT-STATE-DB-UNREADABLE` (`STATE_DB_UNREADABLE`) with the exception as evidence. This makes phase-closeout fail and `agent-watchdog` exit 1.
- Repeated runs update the same alert rather than adding duplicates. The alert resolves once the DB is readable again or removed, and a corrupt DB is left byte-for-byte untouched.

### Verification

- `python -m pytest -q`: **92 passed**
- `python -m compileall -q agent_system`: PASS
- `agent-doctor` on a clean checkout: exit 0 (only WARN: state database not created yet)
- `agent phase-closeout`: PASS; `git diff --exit-code` is clean afterwards
- `agent-watchdog`: 0 active alerts (BLOCKING=0, HIGH=0, WARNING=0), 0 validated orphans
- `shell=True` guard: none under `agent_system`, `tests`, or `config`

### Verdict

**V1 CLOSEOUT VERIFIED** — release is tagged `v1.0.0` on the merge commit once GitHub CI passes and the merge SHA is confirmed on `main`.

## Post-V1 hardening — 2026-09-23

Scope was limited to three items. The `v1.0.0` tag is unchanged.

### 1. Watchdog inspects external project worktrees

- `IntegrationWatchdog._check_worktrees` now scans the orchestrator root **and** every repository configured in `projects/*.yaml`. Configs whose repo is missing or invalid are skipped, and a repo listed more than once is scanned once.
- Both the root and project repos use the same `git worktree list --porcelain` parser. Findings (`STALE_EXECUTION_ASSET`, WARNING):
  - detached worktrees are reported as "Detached worktree";
  - registered worktrees whose directory is gone (`prunable`) are reported as "Orphaned worktree (directory missing)".
- Each repo's main worktree, and the scanned repo path itself, are never flagged.
- Worktrees are deduplicated by resolved path, so one worktree reachable through several configs produces one finding.
- Alert IDs are project-scoped (`ALERT-STALE-WT-<project>-<name>`), so same-named worktrees in different repos don't collide. Root alert IDs are unchanged.

### 2. Doctor fails closed on project load errors

- `Doctor._integration_checks` no longer falls back to the orchestrator root when a task's project config fails to load.
- Instead it emits `integration:project_load` FAIL with the project name, the reason, and the affected task IDs. It skips project-specific merge verification for those tasks, which are also listed as unverified under `integration:completed_merged`.

### 3. Live Ollama reviewer re-validation

- The configured `ollama-reviewer` (`OllamaProvider`, model `qwen2.5-coder:7b` via `OLLAMA_REVIEW_MODEL`, Ollama 0.34.0) was loaded through `ProviderRegistry(config/models.yaml)` and `ProviderReviewer`. No mock or local-command provider was used.
- One bounded live review of a known diff (add `multiply()` plus a test) returned valid JSON with verdict `PASS` (allowed). The provider took 132.6 s against a 180 s timeout, and the server logged `POST /api/chat` 200.
- Worktree snapshots (git status, HEAD, and per-file SHA-256) were identical before and after, and `modified_files` was empty.
- Evidence: `runs/OLLAMA-REVIEWER-LIVE/reviewer-live-result.json`, `runs/OLLAMA-REVIEWER-LIVE/review.json`, probe `runs/ollama_reviewer_live_probe.py`.
- **REAL OLLAMA REVIEWER = PASS (re-proven live)**

### Verification

- `python -m pytest -q`: **101 passed** (9 new regression tests)
- compileall: PASS; `shell=True` guard: PASS
- Clean checkout: `agent-doctor` exit 0, `agent phase-closeout` PASS with a clean tree, `agent-watchdog` 0 active alerts
