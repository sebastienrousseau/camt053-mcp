<!-- SPDX-License-Identifier: Apache-2.0 OR MIT -->

# camt053-mcp Architecture

A map of the codebase for new contributors and maintainers. The goal is
that anyone can navigate, extend, and reason about camt053-mcp without
prior context.

## The pipeline

```
MCP client (Claude Desktop, IDE, agent, gateway)
        |  stdio (JSON-RPC), streamable HTTP, SSE,
        |  or authenticated streamable HTTP (transport.py)
        v
camt053_mcp/server.py        (MCPServer: tools, resources, prompts)
        |  thin typed wrappers
        v
camt053.services             (parse, validate, filter, reverse,
        |                     CBPR+ checks; MT940/MT942 via the loaders)
        v
ISO 20022 camt.053 / camt.052 XML / structured data
```

Tools are deliberately thin: every one is a small adapter that
delegates to the
[`camt053`](https://github.com/sebastienrousseau/camt053) library's
`services` layer and returns a JSON-serialisable result. The agent
surface is the public API of that library, exposed in a way an MCP
client can call.

## Module map

| Area | Module | Responsibility |
| :--- | :--- | :--- |
| **Server** | `camt053_mcp/server.py` | The MCPServer, all tool / resource / prompt registrations, the argparse `main()` |
| **Entry point** | `camt053_mcp.server:main` (console script: `camt053-mcp`) | Launches the server over stdio, over streamable HTTP / SSE with `--transport` (`_cli.py`, `_transports.py`, ADR 0001), or over authenticated streamable HTTP with `--transport http --bind` (`transport.py`) |
| **Suite command line** | `camt053_mcp/_cli.py`, `camt053_mcp/_transports.py` | `--host`/`--port` and the transport dispatch, copied verbatim into every server of the suite; work on mcp 1.x and 2.x |
| **Authenticated HTTP** | `camt053_mcp/transport.py` | Streamable HTTP with mandatory bearer auth, `Camt053-Account` tenant scoping and audit attribution |
| **OAuth** | `camt053_mcp/oauth.py` | OAuth 2.1 resource-server JWT validation (RFC 9728); the static `CAMT053_MCP_TOKEN` stays as dev-mode fallback |
| **Audit** | `camt053_mcp/auditing.py` | Audit attribution, tenant context and the HMAC tamper-evident chain; imports nothing else from the package |
| **Metrics** | `camt053_mcp/observability.py` | Prometheus metrics for the HTTP transport and the tool dispatcher |
| **Tracing** | `camt053_mcp/tracing.py` | Opt-in OpenTelemetry tracing behind the `[otel]` extra (`--otel-endpoint`) |
| **SDK shim** | `camt053_mcp/_mcp_compat.py` | One import surface over mcp 1.x (`FastMCP`) and 2.x (`MCPServer`) |
| **Rulebook** | `camt053_mcp/rulebook.py`, `camt053_mcp/vector_search.py` | Curated payments-rulebook clauses and the sqlite-vec lexical-vector search over them (`[vector]` extra) |
| **Classification** | `camt053_mcp/classify.py` | `classify_entry` through MCP Sampling: the client's model does the inference |
| **Journal export** | `camt053_mcp/export_journal.py` | Parsed entries to Xero and QuickBooks Online journal payloads |
| **Version** | `camt053_mcp/__init__.py` | Single source of truth (`__version__`) |
| **Tests** | `tests/` | In-process regressions per module, the suite conformance invariants, the stress suite (`stress` marker) |
| **Benchmarks** | `benches/bench_tool_dispatch.py`, `bench/load_test.py` | MCP-layer overhead over the core; asyncio + httpx load against the HTTP transport |
| **Fuzzing** | `fuzz/`, `.clusterfuzzlite/` | Atheris harness, run by ClusterFuzzLite on PRs and on a schedule |
| **Examples** | `examples/` | One runnable script per usage shape, executed by a test |
| **Release helpers** | `scripts/verify_versions.py`, `scripts/check_suite_consistency.py` | Version sources agree; the six `camt053` packages agree |

## Tools, resources, prompts

The current MCP surface:

- **Tools** (24) - discovery `list_message_types`, `list_return_reasons`,
  `get_required_fields`, `get_input_schema`; validation
  `validate_records`, `validate_identifier`, `validate_statement`,
  `check_cbpr_readiness`, `get_cbpr_cutover_date`; parsing and analysis
  `parse_statement`, `list_entries`, `filter_entries`,
  `detect_statement_anomalies`; migration `convert_mt940_to_camt053`,
  `convert_mt942`; generation `generate_reversal`; rulebook
  `cite_rulebook`, `list_rulebook_clauses`, `search_rulebook_vector`;
  export `export_journal`, `list_export_journal_targets`;
  classification `classify_entry`, `list_classify_entry_categories`;
  deployment `get_tenant_context`.
- **Resources** (3) - `camt053://return-reasons`,
  `camt053://message-types` and the templated
  `camt053://session/{session_id}/bank/{bic}`.
- **Prompts** (4) - `reversal_preview`, `reconcile_against_pain001`,
  `find_duplicate_entries`, `match_to_invoice_set`.

## Key design decisions

- **Delegation, not duplication.** Every tool is a thin wrapper over
  `camt053.services`. If you want a new tool, port the matching helper
  from `camt053` rather than re-implementing it here.
- **Errors as data.** Tools do not raise on bad input. A `Camt053Error`
  or a bad argument becomes an `{"error": ...}` payload so the agent can
  reason about failure without parsing tracebacks.
- **Four transports, one command line.** stdio for a client that spawns
  the process; streamable HTTP and SSE from the suite's shared
  `_cli`/`_transports` pair, unauthenticated and bound to loopback by
  default; authenticated streamable HTTP in `transport.py` for shared
  multi-tenant deployments (ADR 0001).
- **Auth layered below the transport.** `auditing.py` has no imports
  from the rest of the package, so transport, OAuth and observability
  can all build on it without cycles.
- **Optional extras stay optional.** OpenTelemetry (`[otel]`) and
  sqlite-vec (`[vector]`) are imported lazily; the base install pulls
  in neither.
- **Coverage enforced at 100%** line+branch and docstring
  (`interrogate`); the suite-conformance test pins the invariants every
  `camt053` package shares.

## Extension points

- **Add a tool:** a `@server.tool(...)` function in
  `camt053_mcp/server.py`; pair it with tests in
  `tests/test_mcp_server.py`, document it in the README (a test fails
  when a registered tool is undocumented) and update the tool count in
  the suite table, `glama.json` and `server.json`.
- **Add a resource:** `@server.resource("camt053://...")`.
- **Add a prompt:** `@server.prompt()`.
- **Match a new `camt053` feature:** when a new `services` helper lands
  upstream, port it as a tool here in the same release window.

## Where to look first

- Runnable examples: [`examples/`](examples/)
- Decisions: [`docs/adr/`](docs/adr/index.md)
- Roadmap: [`ROADMAP.md`](ROADMAP.md)
- Release process: [`RELEASING.md`](RELEASING.md)
- Parent library: [`camt053`](https://github.com/sebastienrousseau/camt053)
