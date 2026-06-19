# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.0.5] - 2026-06-19

### Added

- `validate_statement` MCP tool that validates an incoming camt.05x statement
  against its XSD schema and reports `{"valid", "message_type", "errors"}`,
  delegating to `camt053.services.validate_statement` (#3)

### Fixed

- Repointed the broken `https://camt053.com` links (website link and docs
  badge/URL) and the `pyproject.toml` `homepage` to the GitHub Pages site
  `https://sebastienrousseau.github.io/camt053/` (#4)

## [0.0.4] - 2026-06-19

### Added

- Security policy (`SECURITY.md`) documenting supported versions, private
  vulnerability reporting via GitHub Security Advisories, the response timeline,
  and the scope of the camt053-mcp server
- Dependabot configuration covering the `pip` and `github-actions` ecosystems,
  with weekly grouped updates
- Weekly CodeQL scanning workflow (`security-and-quality` queries) running on
  push, pull request, and a Monday schedule
- Bandit security-scan CI job (`Security Scan`) running `bandit -r camt053_mcp/`
- GitHub issue templates (bug report and feature request) and a pull-request
  template
- A `CODEOWNERS` file assigning default review ownership

## [0.0.3] - 2026-06-19

### Fixed

- `camt053_mcp.__version__` now matches the published package version (it was
  still `0.0.1` while `pyproject.toml` declared `0.0.2`). Both now read `0.0.3`.
- Added a version-consistency test that parses `version` out of `pyproject.toml`
  and asserts it equals `camt053_mcp.__version__`, guarding against future drift.

[0.0.5]: https://github.com/sebastienrousseau/camt053-mcp/releases/tag/v0.0.5
[0.0.4]: https://github.com/sebastienrousseau/camt053-mcp/releases/tag/v0.0.4
[0.0.3]: https://github.com/sebastienrousseau/camt053-mcp/releases/tag/v0.0.3

## [0.0.2] - 2026-06-18

### Added

- `list_entries` MCP tool — list every entry across all of a statement's
  statements (#4)
- Optional `offset` / `limit` pagination on `list_entries` and `filter_entries`:
  when `limit` is given the tools return a `{"total", "offset", "limit",
  "entries"}` envelope; when omitted they return the full list unchanged, so
  existing callers are unaffected. A negative `offset` or `limit` returns an
  `{"error": ...}` payload (#4)
- `reversal_preview` FastMCP prompt — a parameterised (`reason_code`, default
  `AC04`) four-step template guiding an agent to parse the statement, preview
  the matching entries via `filter_entries`, confirm, then call
  `generate_reversal` (#4)
- MCP resources giving agents read-only reference context (#3):
  - `camt053://return-reasons` — the ISO external return-reason catalog
    (`{"code", "name"}` list, from `services.list_return_reasons`)
  - `camt053://message-types` — the supported camt.05x message types
    (`{"message_type", "name"}` list, from `services.list_message_types`)

### Deferred

- `validate_statement` MCP tool (#3) — deferred to a later release because it
  depends on a `camt053.services.validate_statement` core API that ships with
  `camt053` 0.0.2 (not yet released)

[0.0.2]: https://github.com/sebastienrousseau/camt053-mcp/releases/tag/v0.0.2

## [0.0.1] - 2026-06-16

### Added

- Initial release of `camt053-mcp`, a Model Context Protocol (MCP) server that
  exposes the [`camt053`](https://github.com/sebastienrousseau/camt053) ISO
  20022 camt.05x Bank Statement library as tools for AI agents and assistants
- `camt053-mcp` console script that runs the FastMCP server over stdio
- Nine MCP tools, all delegating to the shared `camt053.services` facade so they
  behave identically to the CLI and REST API:
  - `list_message_types` — list the 3 supported camt.05x message types
  - `list_return_reasons` — list the ISO external return reason codes
  - `get_required_fields` — required input fields for a message type
  - `get_input_schema` — full input JSON Schema for a message type
  - `validate_records` — validate flat records against a message type
  - `validate_identifier` — validate an IBAN, BIC, or LEI
  - `parse_statement` — parse an incoming camt.05x statement into data
  - `filter_entries` — return entries carrying a given return reason code
  - `generate_reversal` — generate a validated reversing-entry XML document
- Graceful error handling: tools return an `{"error": ...}` payload on a
  `ValueError` or `Camt053Error` rather than raising
- Python 3.10+ support; depends on `camt053` (>=0.0.1) and `mcp` (>=1.2)
- Runnable example (`examples/mcp_tools.py`) invoking the tools in-process

[0.0.1]: https://github.com/sebastienrousseau/camt053-mcp/releases/tag/v0.0.1
