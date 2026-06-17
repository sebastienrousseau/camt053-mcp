# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
