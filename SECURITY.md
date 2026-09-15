# Security Policy

## Reporting a vulnerability

Do not open a public issue for credentials, authentication bypasses, remote-code-execution paths, or other exploitable security findings. Report them privately to the repository owner through GitHub's private security reporting channel when available.

Include reproduction steps, affected version/commit, impact, and a minimal proof of concept. Do not include real secrets, customer data, or third-party licensed data.

## Supported branch

Security fixes target `main` unless a release branch is explicitly documented.

## Secret handling

Never commit API keys, tokens, private keys, `.env` files, production credentials, or proprietary datasets. CI runs Gitleaks, Trivy, Semgrep, and CodeQL as defense-in-depth checks.
