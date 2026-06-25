<!-- SPDX-License-Identifier: Apache-2.0 OR MIT -->

# `camt053-mcp` roadmap

## Mission

The Model Context Protocol (MCP) server for the
[`camt053`](https://github.com/sebastienrousseau/camt053) ISO 20022
bank-statement library — agent-first surface, no other ISO 20022 MCP
server in the ecosystem matches its tool coverage.

## Where we are (v0.0.7, shipped 2026-06-22)

- **19 tools** (up from 11 in v0.0.6):
  - Message-type discovery: `list_message_types`, `list_return_reasons`,
    `get_required_fields`, `get_input_schema`
  - Validation: `validate_records`, `validate_identifier`,
    `validate_statement`
  - Parsing: `parse_statement`, `list_entries`, `filter_entries`
  - Reversing-entry generation: `generate_reversal`
  - CBPR+ Nov 2026 cliff: `check_cbpr_readiness`,
    `get_cbpr_cutover_date`
  - Curated rulebook lookup (D4 in #17): `cite_rulebook`,
    `list_rulebook_clauses` — 9 SEPA/CBPR+/HVPS+ clauses
  - Accounting-platform export (D5 partial in #17): `export_journal`
    for Xero + QBO + `list_export_journal_targets`
  - LLM-driven classification via MCP Sampling (D3 in #17):
    `classify_entry`, `list_classify_entry_categories`
- **3 resources** including the templated
  `camt053://session/{session_id}/bank/{bic}` (D1 in #17) that
  returns parsed BIC country + recommended rulebook clauses.
- **4 guided prompts** (D2 in #17): `reversal_preview`,
  `reconcile_against_pain001`, `find_duplicate_entries`,
  `match_to_invoice_set`.
- **Supply chain**: 100% line + branch coverage, OpenSSF Scorecard,
  SLSA Build L3 + PEP 740 sigstore attestations on every release,
  CycloneDX 1.6 + SPDX 2.3 + pip-licenses SBOMs on every GitHub
  release, NIST SP 800-218 SSDF practice mapping in `SECURITY.md`.

## v0.0.8 — Q3 2026

Goal: HTTP transport, multi-tenant deployments.

- **HTTP/SSE transport variant** (#42, D7 in #17):
  `camt053-mcp --transport=http --bind=…` + bearer-token auth +
  optional `Camt053-Account` tenant header → `Context` for
  multi-tenant scoping.
- **`export_journal` NetSuite + SAP S/4HANA targets**: complete D5
  in #17 (Xero + QBO shipped in v0.0.7).
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
- **OAuth provider integration**: HTTP transport authenticates by
  bearer token; integrating with corporate OAuth (Okta, Auth0, etc.)
  is the operator's job at the reverse-proxy layer.

## How to influence the roadmap

- Open an issue with the proposed tool / resource / prompt + the
  use case it unblocks.
- For larger items, sketch a design in the issue body.
- See [`GOVERNANCE.md`](GOVERNANCE.md) for the decision-making
  process.
