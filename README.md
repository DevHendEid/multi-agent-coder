# Multi-Agent Coder

Generic multi-agent coding orchestration foundation with isolated Git worktrees, provider routing, deterministic verification, structured review, bounded repair, and resumable task state.

## Current status

Implemented and tested:
- Project adapters and task contracts
- SQLite task state and event history
- Isolated Git worktrees per task
- Provider registry and risk-based routing
- Mock and command providers
- Codex implementation provider
- Codex read-only reviewer provider adapter
- Context builder with size limits
- Scope/path containment checks
- Deterministic verification gates
- Provider-backed structured review with deterministic override rules
- Bounded repair loop with end-to-end repair/re-verify coverage
- Resume support and run artifacts

Current limitation:
- A real **free** provider has not yet been validated on the user's machine. Codex integration exists, but it is a premium provider and can be quota-limited.
- No automatic merge, push, deploy, SAFA integration, parallel agent DAG, or production release automation is enabled.

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

## Safety model

The orchestrator never treats model output as proof of correctness. Deterministic gates remain authoritative. AI providers operate against task worktrees, and the system does not auto-merge or auto-push.

## Tests

```bash
python -m pytest -q
```

Current local result in this snapshot: **25 passed**.
