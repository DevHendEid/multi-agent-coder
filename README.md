# Multi-Agent Coder — Foundation V1

This is Phase 1 of a generic multi-agent coding system. It intentionally does **not** call any AI model yet.

Implemented now:
- CLI
- Task contract creation from a user goal
- Project adapter loading from YAML
- Persistent task state in SQLite
- Git worktree creation in isolated branches
- Status/list/resume foundation
- Protected-path and repository validation hooks

Not implemented yet:
- Model providers
- Free-model router
- Codex provider/fallback
- Verifier/reviewer/repair agents
- Parallel DAG execution

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .

agent init-project --name demo --repo /path/to/repo
agent task --project projects/demo.yaml "Fix logout persistence bug"
agent status TASK-0001
```

The system defaults to `auto_merge: false` and creates a dedicated Git worktree for each task.
