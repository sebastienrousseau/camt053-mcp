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
