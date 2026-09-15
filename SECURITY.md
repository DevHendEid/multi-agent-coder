# Security and operational boundaries

## Core guarantees

- Model output is untrusted input.
- Deterministic verification is authoritative.
- Task work executes in isolated Git worktrees.
- Protected paths and path-containment checks must remain enabled.
- Reviewer disagreement cannot override failed deterministic gates.
- Critical/human-gated work requires explicit human decision.
- Pull-request publication is explicit; merge and deployment are never automatic.
- Runtime databases, run artifacts, task contracts generated at runtime, and local environment files must not be committed.

## Provider safety

- Provider commands must use argv execution; `shell=True` is forbidden.
- Full prompts/context must not be persisted in command metadata.
- New provider/model identities begin unbenchmarked and should be benchmarked before they are trusted for important work.
- Quota/auth/provider failures must degrade safely rather than bypassing verification.

## Concurrency and recovery

- A per-task runtime lease prevents two local processes from implementing the same task concurrently.
- Distinct tasks may run concurrently in distinct worktrees.
- Recovery is conservative and explicit. It must not discard or silently accept partial worktree changes.

## CI and dependencies

- GitHub Actions run with read-only repository permissions for tests.
- Third-party GitHub Actions are pinned to immutable commit SHAs.
- CI rejects `shell=True` and tracked runtime state.

## Reporting a security issue

Do not put secrets, credentials, tokens, private datasets, or exploit details in a public issue. Report sensitive findings through a private channel controlled by the repository owner.
