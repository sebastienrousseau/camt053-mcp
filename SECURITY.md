# Security Policy

The camt053-mcp maintainers take the security of this project seriously. This
document explains which versions receive security updates and how to report a
vulnerability responsibly.

camt053-mcp is the Model Context Protocol (MCP) server for camt053: it exposes
the core [`camt053`](https://github.com/sebastienrousseau/camt053) ISO 20022
Bank Statement library as tools, resources, and prompts for AI agents and
assistants.

## Supported Versions

Security fixes are applied to the latest released minor version. While the
project is in its `0.0.x` series, only the most recent release line receives
security updates.

| Version | Supported          |
| ------- | ------------------ |
| 0.0.x   | :white_check_mark: |
| < 0.0.1 | :x:                |

## Reporting a Vulnerability

**Please do not report security vulnerabilities through public GitHub issues,
discussions, or pull requests.**

We support coordinated disclosure. To report a vulnerability, use either of the
following private channels:

- **GitHub Security Advisories** (preferred): open a private report via the
  repository's
  [Security tab → "Report a vulnerability"](https://github.com/sebastienrousseau/camt053-mcp/security/advisories/new).
- **Email**: contact the maintainer at
  [sebastian.rousseau@gmail.com](mailto:sebastian.rousseau@gmail.com).

When reporting, please include as much of the following as possible:

- A description of the vulnerability and its potential impact.
- Steps to reproduce, or a proof-of-concept.
- The affected version(s) and environment (Python version, OS).
- Any known mitigations or workarounds.

## Response Timeline

We aim to meet the following targets, on a best-effort basis:

| Stage                     | Target                          |
| ------------------------- | ------------------------------- |
| Acknowledge receipt       | Within 3 business days          |
| Initial assessment        | Within 7 business days          |
| Fix or mitigation plan    | Within 30 days of confirmation  |
| Public disclosure         | Coordinated, after a fix ships  |

We will keep you informed of progress throughout the process and will credit
reporters in the advisory unless anonymity is requested.

## Scope

The following are in scope:

- The `camt053-mcp` MCP server as published in this repository, including the
  FastMCP tools, resources, and prompts it exposes over stdio, and the way it
  surfaces the underlying `camt053` library to MCP agents.
- Handling of agent-supplied tool arguments and the payloads returned to
  agents, including error envelopes.
- XML parsing and validation paths reached through the server, including
  XXE / path-traversal handling.
- Input validation for IBAN, BIC, LEI, currency, and reason-code data.

The following are generally out of scope:

- Vulnerabilities in the underlying `camt053` library itself; please report
  those against the
  [camt053](https://github.com/sebastienrousseau/camt053) repository.
- Vulnerabilities in third-party dependencies (please report those upstream;
  we will track and update affected dependencies via Dependabot).
- Issues requiring a compromised host, malicious local configuration, or
  physical access.
- Denial of service caused by intentionally malformed, multi-gigabyte inputs
  beyond documented usage.

Thank you for helping keep camt053-mcp and its users safe.

## NIST SSDF practice mapping

This repository follows the practices of the **NIST Secure Software
Development Framework (SP 800-218 Rev 1.1)**. The table below maps
each SSDF practice that applies to an open-source Python library to
the concrete control(s) that implement it in this repo.

| SSDF practice | How this repo addresses it |
| :--- | :--- |
| **PO.1** Define security requirements | This `SECURITY.md`, plus the in-scope/out-of-scope sections above. |
| **PO.3** Implement supporting toolchains | `pyproject.toml`; `.github/workflows/ci.yml` (test + lint + security scan); `.github/workflows/scorecard.yml`. |
| **PO.4** Define and use criteria for software security checks | CI enforces tests on Python 3.10/3.11/3.12, ruff lint, black formatting, mypy, bandit security scan, interrogate docstring coverage; Scorecard runs weekly. |
| **PO.5** Implement and maintain secure environments | PyPI Trusted Publishing (OIDC, no long-lived tokens); branch protection + signed commits on `main`; per-workflow `permissions:` minimisation. |
| **PS.1** Protect all forms of code from unauthorized access and tampering | Signed commits (SSH ed25519); branch protection; required PR reviews; `persist-credentials: false` on Scorecard checkout. |
| **PS.2** Provide a mechanism for verifying software release integrity | Signed git tags; `actions/attest-build-provenance@v3` SLSA L3 provenance attestations; PEP 740 sigstore attestations on PyPI uploads (`pypa/gh-action-pypi-publish` with `attestations: true`). |
| **PS.3** Archive and protect each software release | GitHub Releases pin the exact `dist/*` artifacts; CycloneDX 1.6 + SPDX 2.3 SBOMs and a pip-licenses manifest attached to every release; PyPI is the immutable archive. |
| **PW.1** Design software to mitigate security risks | XML inputs are delegated to the core `camt053` library which uses `defusedxml` (no XXE / billion-laughs); the MCP server returns serialised `{"error": ...}` payloads rather than raising into client transports. |
| **PW.4** Reuse well-secured software when feasible | Dependencies pinned via pyproject; Dependabot grouped weekly + separate security-update group; updates reviewed before merge. |
| **PW.5** Adhere to secure coding practices | `ruff`, `bandit -ll`, strict `mypy`, code review on every PR. |
| **PW.6** Configure build processes to improve security | Reproducible builds via `python -m build` / `poetry build` with locked dependencies; CI uses pinned action versions; minimum-required GH Actions permissions. |
| **PW.7** Review and analyze human-readable code | All changes go through PRs with required review; CodeQL static analysis runs on push/PR; ruff + mypy + bandit on every change. |
| **PW.8** Test executable code | pytest across 3 Python versions; per-tool runnable examples auto-exercised in CI via `tests/test_examples.py`. |
| **PW.9** Configure software with secure defaults | Stdio transport binds to the local process owner only (no network listener); tools return errors as data instead of raising into the client. |
| **RV.1** Identify and confirm vulnerabilities on an ongoing basis | Dependabot daily; `bandit` in CI; OpenSSF Scorecard weekly; GitHub Security Advisories accept reports. |
| **RV.2** Assess, prioritise, and remediate vulnerabilities | Coordinated-disclosure timeline above (3-day ack / 7-day assessment / 30-day fix); CHANGELOG + advisory at fix publication. |
| **RV.3** Analyze root causes | Each security advisory captures root cause + remediation in the GitHub Security Advisory body; lessons feed back into added regression tests. |

Cross-suite practices (organisation roles, multi-package release governance) are owned by the upstream [`camt053`](https://github.com/sebastienrousseau/camt053) repository's `SECURITY.md`.
