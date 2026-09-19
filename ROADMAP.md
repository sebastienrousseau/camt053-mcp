<!-- SPDX-License-Identifier: Apache-2.0 OR MIT -->

# `camt053-mcp` roadmap

## Mission

The Model Context Protocol (MCP) server for the
[`camt053`](https://github.com/sebastienrousseau/camt053) ISO 20022
bank-statement library — agent-first surface, no other ISO 20022 MCP
server in the ecosystem matches its tool coverage.

## Where we are (v0.0.20, shipped 2026-08-29)

- **24 tools**:
  - Message-type discovery: `list_message_types`, `list_return_reasons`,
    `get_required_fields`, `get_input_schema`
  - Validation: `validate_records`, `validate_identifier`,
    `validate_statement`, `check_cbpr_readiness`, `get_cbpr_cutover_date`
  - Parsing and analysis: `parse_statement`, `list_entries`,
    `filter_entries`, `detect_statement_anomalies`
  - Legacy SWIFT migration: `convert_mt940_to_camt053`, `convert_mt942`
  - Reversing-entry generation: `generate_reversal`
  - Curated rulebook lookup: `cite_rulebook`, `list_rulebook_clauses`,
    `search_rulebook_vector` (`[vector]` extra)
  - Accounting-platform export: `export_journal` for Xero + QBO,
    `list_export_journal_targets`
  - LLM-driven classification via MCP Sampling: `classify_entry`,
    `list_classify_entry_categories`
  - Deployment: `get_tenant_context`
- **3 resources** including the templated
  `camt053://session/{session_id}/bank/{bic}` that returns parsed BIC
  country + recommended rulebook clauses.
- **4 guided prompts**: `reversal_preview`,
  `reconcile_against_pain001`, `find_duplicate_entries`,
  `match_to_invoice_set`.
- **Transports**: stdio; streamable HTTP and SSE from the suite's shared
  command line (`--transport streamable-http|sse`, `--host`, `--port`);
  authenticated streamable HTTP (`--transport http --bind`) with a
  static bearer token in dev mode or OAuth 2.1 resource-server auth
  (RFC 9728), `Camt053-Account` tenant scoping, Prometheus metrics, a
  tamper-evident audit chain and opt-in OpenTelemetry tracing.
- **Supply chain**: 100% line + branch coverage, OpenSSF Scorecard,
  SLSA Build L3 + PEP 740 sigstore attestations on every release,
  CycloneDX 1.6 + SPDX 2.3 + pip-licenses SBOMs on every GitHub
  release, NIST SP 800-218 SSDF practice mapping in `SECURITY.md`,
  a suite-consistency gate across the six `camt053` packages, CI on
  Python 3.10 to 3.14.

## Next

- **`export_journal` NetSuite + SAP S/4HANA targets** (Xero + QBO
  shipped in v0.0.7).
- **OpenSSF Best Practices Silver** badge live.
- **Second maintainer** named (recruiting per
  [`MAINTAINERS.md`](MAINTAINERS.md)).

## v0.0.9 — Q4 2026

Goal: post-Nov-2026-cliff field-tested behaviour.

- **`camt.110` / `camt.111`** exception/investigation tools (matched
  to the core library's parsing support).
- **More guided prompts** (escrow workflows, FX settlement flows).

## v0.1.0 — Q1 2027

Goal: first stable minor.

- **MCP API surface frozen**: any future tool name change becomes a
  minor-bump event per SemVer.
- **OpenSSF Best Practices Gold**.

## Out of scope (until a contributor steps up)

- **Embedded LLM**: the server uses MCP Sampling (`classify_entry`)
  to let the *client's* model do inference. No bundled LLM weights;
  no hosted inference endpoint.
- **Bundled authorization server**: the HTTP transport validates
  OAuth 2.1 tokens as a resource server; issuing them (Okta, Auth0,
  Keycloak, etc.) is the operator's job.

## How to influence the roadmap

- Open an issue with the proposed tool / resource / prompt + the
  use case it unblocks.
- For larger items, sketch a design in the issue body.
- See [`GOVERNANCE.md`](GOVERNANCE.md) for the decision-making
  process.
