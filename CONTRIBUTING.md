# Contributing

1. Create a focused branch from `main`.
2. Keep changes within the requested scope.
3. Install development tooling with `pip install -e '.[dev]'` and `pre-commit install --hook-type pre-commit --hook-type pre-push`.
4. Run `ruff check .`, `mypy agent_system`, and `pytest` before opening a PR.
5. Add or update tests for behavior changes.
6. Never commit secrets, generated credentials, private user data, or licensed datasets.
7. Use the PR template and document risk, verification, and rollback.
8. High/critical-risk changes require explicit human review before merge.

CI is the source of truth for automated checks. Security workflows run CodeQL, Semgrep, Gitleaks, and Trivy in addition to dependency updates from Dependabot.
