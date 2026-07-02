# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.0.10] - 2026-07-02

The **discoverability** cut. Registers `camt053-mcp` with the official
Model Context Protocol Registry, adds MCP-spec conformance CI, and
positions the server as part of the ISO 20022 MCP Suite. No functional
or API changes.

### Added

- **Official MCP Registry integration.** `camt053-mcp` is now
  registered with the official Model Context Protocol Registry
  (`registry.modelcontextprotocol.io`) as
  `io.github.sebastienrousseau/camt053-mcp`. A new `server.json` at
  the repo root provides the registry metadata (PyPI package
  identifier, stdio transport), and the README carries an
  `mcp-name: io.github.sebastienrousseau/camt053-mcp` marker that the
  registry uses to verify PyPI package ownership.
- **Auto-publish workflow** (`.github/workflows/publish-mcp.yml`).
  Authenticates to the MCP Registry via GitHub OIDC (no secrets
  required) on every `v*.*.*` tag push, syncs the tag version into
  `server.json`, and runs `mcp-publisher publish`. Registry metadata
  now stays in lockstep with each PyPI release automatically.
- **Protocol conformance CI** (`.github/workflows/mcp-inspect.yml`).
  Runs `@modelcontextprotocol/inspector --cli` against `tools/list`,
  `resources/list`, and `prompts/list` on every push and PR.
  Continuous validation of MCP protocol conformance across all 19
  tools, 3 resources, and 4 prompts.
- **Suite discoverability.** The README now cross-links the sibling
  banking MCP servers under a "Related MCP Servers" section,
  positioning `camt053-mcp` as part of the ISO 20022 MCP Suite
  alongside `pain001-mcp`, `bankstatementparser-mcp`, `acmt001-mcp`,
  and `noyalib-mcp`.

### Changed

- GitHub repository description and topics refreshed: description now
  positions the server as part of the ISO 20022 MCP Suite (with
  CBPR+/HVPS+ readiness noted); topics extended with `mcp-server`,
  `financial-services`, `iso-20022`, `stdio`, `claude-desktop`, and
  `cbpr-plus`.
- **Glama manifest version resynced** — `glama.json` was stale at
  `0.0.6`; now bumped to `0.0.10` to match the release.

### No functional / API changes

- Same 19 MCP tools, 3 resources, and 4 prompts as v0.0.9. This
  release is metadata, CI, and discoverability only.

## [0.0.9] - 2026-06-27

### Changed

- **Version** — suite-wide lockstep bump to `0.0.9`. No functional changes.

## [0.0.8] - 2026-06-26

### Changed

- **Version** — suite-wide lockstep bump to `0.0.8`. No functional changes.

## [0.0.7] - 2026-06-26

### Changed

- **Version** — suite-wide lockstep bump to `0.0.7` to keep all `camt053`
  packages on the same version. No functional changes to the MCP server.

### Fixed

- **Type annotation** — annotated the `ctx` parameter of `classify_entry`
  (mypy `no-untyped-def`).
- **Test coverage** — restored 100% coverage with a test for the
  entry-level error path in `export_journal.export`.

## [0.0.6] - 2026-06-22

### Added

- **`check_cbpr_readiness` MCP tool** that wraps
  `camt053.compliance.check_cbpr_readiness` and reports whether an
  incoming camt.05x statement will pass the coordinated CBPR+ /
  Fedwire / CHAPS / T2 cutover on **14-16 November 2026**. Detects
  unstructured-only postal addresses (`AdrLine` without `TwnNm` +
  `Ctry`, the Nov 2026 reject case) and schema-version drift (.02-.07
  deprecated; .08 / .13 current). Returns a structured report with
  `cbpr_ready: bool`, per-issue XPath-style paths, severities, stable
  codes, and an address-classification summary.
- **`get_cbpr_cutover_date` MCP tool** returning the canonical
  cutover date (`2026-11-16`) so agents can quote it directly without
  having to call a readiness check first.

Total tools: **13** (up from 11). Part of the v0.0.6 batch tracked in
[#17](https://github.com/sebastienrousseau/camt053-mcp/issues/17).

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

[0.0.9]: https://github.com/sebastienrousseau/camt053-mcp/releases/tag/v0.0.9
[0.0.8]: https://github.com/sebastienrousseau/camt053-mcp/releases/tag/v0.0.8
[0.0.7]: https://github.com/sebastienrousseau/camt053-mcp/releases/tag/v0.0.7
[0.0.6]: https://github.com/sebastienrousseau/camt053-mcp/releases/tag/v0.0.6
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
