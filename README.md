# Multi-Agent Coder

Generic multi-agent coding orchestrator with isolated Git worktrees, free-first provider routing, deterministic verification, independent review, bounded repair, parallel execution, DAG dependencies, model benchmarking, guarded PR publication, and production hardening.

## Implemented architecture

- Project adapters and task contracts
- Persistent SQLite task state and event history
- Isolated Git worktree per task
- Free-first provider routing with fallback/escalation
- Local Ollama implementer and read-only reviewer support
- Codex implementer/reviewer adapters
- Deterministic scope, diff, test, lint, build, and integration gates when configured
- Independent review panel with deterministic failure dominance
- Bounded repair and full re-verification
- Parallel execution across distinct task worktrees
- Persistent DAG/task dependencies with cycle prevention
- Model/version-specific benchmark registry and admission status
- Explicit guarded GitHub PR publication after persisted verification/review gates
- Per-task runtime leases to prevent duplicate concurrent execution
- Conservative interrupted-task recovery tooling
- Production diagnostics and SQLite integrity checks
- CI on Python 3.11 and 3.12 with read-only permissions and SHA-pinned actions

## Safety boundaries

Model output is never proof of correctness. Deterministic verification remains authoritative. A reviewer cannot vote away a failed deterministic gate. Critical/human-gated work remains subject to explicit human review. The system does **not** automatically merge or deploy.

PR publication is an explicit user action through `agent-pr`; it commits and pushes the already-verified task branch, then creates or reuses a GitHub pull request. Human-gated tasks may only be published as draft PRs.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e .

agent init-project --name demo --repo /path/to/repo
agent task --project demo "Fix logout persistence bug"
agent run TASK-0001
agent status TASK-0001
```

For local Ollama routing, set exact installed model names before running:

```bash
export OLLAMA_MODEL=qwen2.5-coder:7b
export OLLAMA_REVIEW_MODEL=qwen2.5-coder:7b
agent models
```

## Parallel work and dependencies

```bash
agent run-many TASK-0001 TASK-0002 --workers 2
agent task --project demo --depends-on TASK-0001 "Add follow-up behavior"
agent depends TASK-0003 --on TASK-0002
agent dag
agent run-ready --workers 2
```

## Model benchmark registry

```bash
agent benchmark ollama-free --repeat 2
agent benchmark ollama-reviewer --repeat 2
agent benchmarks
agent models
```

Benchmark admission is tied to the exact provider/model identity. A new model version does not inherit an older model's benchmark status.

## Model control UI

```bash
pip install -e '.[ui]'
agent-ui                           # http://127.0.0.1:8765 (run from the repo root)
```

A local admin page for `config/models.yaml` and `config/routing.yaml`:

- **Providers**: id, model, role, tier, trust, benchmark status, timeout and enabled flag. Test availability and run `agent benchmark <id>` from the page. Secrets (keys, tokens, `env` blocks, URL credentials) are redacted.
- **Routing**: set the provider order for each risk level. The first available provider is primary and the rest are fallbacks, as in `ModelRouter`. Reviewers can be enabled or disabled.

Saves are validated with the same rules as `agent-doctor`. The UI also rejects unknown, disabled or reviewer-only providers in implementer routing, and any risk level left empty. Each save is atomic, backs up the previous files to `.agent-system/config-backups/`, and is refused if the files changed on disk since they were loaded.

Formatting: toggling a reviewer rewrites only its `enabled:` line in `models.yaml`, so comments stay. `routing.yaml` is regenerated, keeping its leading comment block but not comments elsewhere in the file.

V1 has no authentication. It binds only to loopback addresses, and it rejects requests with non-loopback `Host` headers and POSTs without the `X-Agent-UI` header.

## Pull request publication

Requires GitHub CLI (`gh`) to be installed and authenticated for the target repository.

```bash
agent-pr TASK-0001                 # draft PR by default
agent-pr TASK-0001 --ready         # non-draft only when no human gate is active
```

No merge or deployment occurs automatically.

## Production diagnostics and recovery

```bash
agent-doctor
agent-doctor --json
agent-recover TASK-0001            # dry-run recovery plan
agent-recover TASK-0001 --apply    # apply only the conservative state transition
```

Interrupted-task recovery never edits or discards worktree changes. Partial unverified changes without a persisted implementation artifact are marked for manual inspection rather than assumed correct.

## Tests

```bash
python -m pytest -q
python -m compileall -q agent_system
```

GitHub Actions runs the suite on Python 3.11 and 3.12, performs CLI smoke tests, package consistency checks, compile checks, a `shell=True` guard, runtime-state tracking guard, and SHA-pin guard for actions.
