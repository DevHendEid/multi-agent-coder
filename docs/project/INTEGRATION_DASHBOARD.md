# Multi-Agent Coder — Integration Dashboard

## Health Metrics
- **Total Tracked Assets**: 19
- **Production Connected**: 19
- **Validated Orphan Count**: `0` (Target: 0)
- **Active Blocking Alerts**: `0`
- **Active High Alerts**: `0`
- **Promotion Queue Pending**: 0
- **Calculated At**: 2026-09-17T10:52:40.467611+00:00

## Asset Lifecycle Matrix

| Badge | Asset | Component | Engineering | Validation | Integration | Consumer | Blocker | Next Action |
|---|---|---|---|---|---|---|---|---|
| ✅ Production Connected | `ASSET-001` CLI Gateway | Core Orchestrator | MERGED_TO_MAIN | VALIDATED | PRODUCTION_ACTIVE | End users / automation scripts | NONE | Maintain CLI subcommands |
| ✅ Production Connected | `ASSET-002` Orchestrator Engine | Core Orchestrator | MERGED_TO_MAIN | VALIDATED | PRODUCTION_ACTIVE | agent_system/cli.py, agent_system/parallel.py | NONE | Support dynamic provider benchmark fallback |
| ✅ Production Connected | `ASSET-003` SQLite State Store | State Management | MERGED_TO_MAIN | VALIDATED | PRODUCTION_ACTIVE | Orchestrator, ParallelExecutor, GitHubPRManager, SystemRecovery | NONE | Schema migration versioning |
| ✅ Production Connected | `ASSET-004` Scope Gate & Sandboxing | Security & Scope | MERGED_TO_MAIN | VALIDATED | PRODUCTION_ACTIVE | Implementer, Verifier, RepairLoop | NONE | Monitor edge cases in symlink traversal |
| ✅ Production Connected | `ASSET-005` Verifier Engine (Zero Shell) | Deterministic Verification | MERGED_TO_MAIN | VALIDATED | PRODUCTION_ACTIVE | Orchestrator, RepairLoop | NONE | Maintain timeout handlers |
| ✅ Production Connected | `ASSET-006` Independent Multi-Reviewer Panel | Quality & Review | MERGED_TO_MAIN | VALIDATED | PRODUCTION_ACTIVE | Orchestrator, RepairLoop | NONE | Add configurable weights to quorum |
| ✅ Production Connected | `ASSET-007` Bounded Repair Loop | Self-Healing & Repair | MERGED_TO_MAIN | VALIDATED | PRODUCTION_ACTIVE | Orchestrator | NONE | Expand failure context summarization |
| ✅ Production Connected | `ASSET-008` DAG Dependency Scheduler | Scheduling & Concurrency | MERGED_TO_MAIN | VALIDATED | PRODUCTION_ACTIVE | Orchestrator, ParallelExecutor | NONE | Support conditional dynamic branch tasks |
| ✅ Production Connected | `ASSET-009` Parallel Worktree Executor | Scheduling & Concurrency | MERGED_TO_MAIN | VALIDATED | PRODUCTION_ACTIVE | agent_system/cli.py | NONE | Dynamic worker pool scaling |
| ✅ Production Connected | `ASSET-010` Model Benchmark Registry | Model Evaluation & Governance | MERGED_TO_MAIN | VALIDATED | PRODUCTION_ACTIVE | ModelRouter, agent benchmark CLI | NONE | Execute live runs on target free providers |
| ✅ Production Connected | `ASSET-011` GitHub PR Integration Tooling | GitHub Integration | MERGED_TO_MAIN | VALIDATED | PRODUCTION_ACTIVE | agent-pr entry point, CI automation | NONE | Add auto-merge webhook triggers |
| ✅ Production Connected | `ASSET-012` Doctor Pre-Flight Diagnostics | Diagnostics & Hardening | MERGED_TO_MAIN | VALIDATED | PRODUCTION_ACTIVE | agent-doctor entry point, CI workflow | NONE | Integrate watchdog diagnostics |
| ✅ Production Connected | `ASSET-013` System Crash Recovery | Recovery & Resilience | MERGED_TO_MAIN | VALIDATED | PRODUCTION_ACTIVE | agent-recover entry point | NONE | Periodic background garbage collection |
| 🔵 Shadow Connected | `ASSET-014` Ollama Local Provider (Free Tier) | Model Providers | MERGED_TO_MAIN | VALIDATED | PRODUCTION_ACTIVE | ModelRouter (low/medium tiers), IndependentReviewPanel | Implementer patch formatting tuning | Refine unified diff generation prompt |
| 🔵 Shadow Connected | `ASSET-015` Codex CLI Provider (Premium Tier) | Model Providers | MERGED_TO_MAIN | VALIDATED | PRODUCTION_ACTIVE | ModelRouter (high/critical tiers) | External API quota exhausted | Replenish tokens / credentials for live production runs |
| ✅ Production Connected | `ASSET-016` GitHub Quality & Security Toolkit | CI/CD & Governance | MERGED_TO_MAIN | VALIDATED | PRODUCTION_ACTIVE | GitHub Actions, developers, open-source contributors | NONE | Enforce in CI pull request builds |
| ✅ Production Connected | `ASSET-017` Continuous Main Integration Service | Production Integration | MERGED_TO_MAIN | VALIDATED | PRODUCTION_ACTIVE | agent-pr, agent, CI automation | NONE | Connect to automated release tagging |
| ✅ Production Connected | `ASSET-018` Integration Watchdog & Lifecycle Alert System | Governance & Watchdog | IMPLEMENTED | VALIDATED | PRODUCTION_ACTIVE | agent-watchdog, agent-doctor, CI workflow | NONE | Run continuously in CI |
| ✅ Production Connected | `ASSET-019` Governance Machine-Readable Registries | Governance & Watchdog | IMPLEMENTED | VALIDATED | PRODUCTION_ACTIVE | agent_system/watchdog.py, agent_system/doctor.py | NONE | Keep in sync with docs/project/ markdown files |

## Active Alerts
Zero active alerts. All lifecycle invariants upheld.
