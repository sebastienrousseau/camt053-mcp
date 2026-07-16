# ISO 20022 MCP Servers Compared (2026)

Choosing an ISO 20022 MCP server means deciding what your AI agent
actually needs to do with financial messages: read a bank statement,
generate a payment file, reconcile the two, or just look up what a
`pacs.008` is. This page compares the open-source options that speak
real ISO 20022 XML — and explains where the popular SaaS MCP servers
(Stripe, Plaid, QuickBooks) fit, because they solve a different
problem. All third-party facts below were checked against the cited
projects' public repositories and documentation as of July 2026.

## What is an ISO 20022 MCP server?

The [Model Context Protocol](https://modelcontextprotocol.io) (MCP) is
an open standard that lets AI agents and assistants discover and call
external tools in a uniform way. An **ISO 20022 MCP server** exposes
operations over [ISO 20022](https://www.iso20022.org/) financial
messages — the XML standard behind SEPA, CBPR+, and most modern
interbank and bank-to-customer traffic — as MCP tools: parsing a
`camt.053` statement, validating a `pain.001` payment initiation
against its XSD, generating a reversing entry, or checking a message
for the November 2026 CBPR+ cutover.

That is different from a *financial-data* MCP server (Stripe, Plaid,
QuickBooks), which exposes a SaaS platform's API — useful for billing
and aggregated account data, but it never touches ISO 20022 XML.

## The ISO 20022 MCP Suite

Four coordinated, Apache-2.0 licensed, Python-native servers by
[Sebastien Rousseau](https://github.com/sebastienrousseau). Dependency
ranges are kept aligned so they co-install in one environment.

| Server | Scope | Surface |
|------|------|------|
| [`camt053-mcp`](https://github.com/sebastienrousseau/camt053-mcp) | `camt.053`/`camt.052` bank statements: parse, validate, filter, reverse; MT940/MT942 migration; CBPR+ readiness; Xero/QBO journal export | 22 MCP tools · 4 prompts · 3 resources |
| [`iso20022-mcp`](https://github.com/sebastienrousseau/iso20022-mcp) | Unified gateway routing `search` / `describe` / `validate` / `generate` / `parse` across the `pain` · `pacs` · `camt` · `acmt` families | 7 meta-tools |
| [`reconcile-mcp`](https://github.com/sebastienrousseau/reconcile-mcp) | Explainable matching of expected `pain.001` payments against observed `camt.053` entries (exact, partial, one-to-many, many-to-one, scored) | 7 MCP tools |
| [`bankstatementparser-mcp`](https://github.com/sebastienrousseau/bankstatementparser-mcp) | Multi-format statement ingestion: CAMT.053, pain.001, MT940, OFX/QFX, CSV | 5 MCP tools · 1 prompt · 1 resource |

The suite also includes per-family servers for outbound messages —
[`pain001-mcp`](https://github.com/sebastienrousseau/pain001-mcp),
[`pacs008-mcp`](https://github.com/sebastienrousseau/pacs008-mcp), and
[`acmt001-mcp`](https://github.com/sebastienrousseau/acmt001-mcp) —
reachable through the `iso20022-mcp` gateway.

## Feature comparison

The closest independent alternative that actually speaks ISO 20022 XML
is **Pactus** ([`deniskarlinsky/iso20022-mcp`](https://github.com/deniskarlinsky/iso20022-mcp),
published on PyPI as
[`pactus-mcp`](https://pypi.org/project/pactus-mcp/)). As of July 2026
its README documents nine tools — a `ping` health check plus
parse/validate pairs for one pinned version each of `pacs.008.001.08`,
`pacs.002.001.10`, `pain.001.001.09`, and `camt.053.001.08` — over
stdio, with no prompts, resources, HTTP transport, or authentication
documented.

| Axis | ISO 20022 MCP Suite (4 servers) | Pactus (`pactus-mcp`) |
|------|------|------|
| camt coverage | Parse, validate, filter, reverse `camt.053`/`camt.052`; MT940/MT942 migration; CBPR+ readiness checks; `camt.056`/`camt.029` E&I via the gateway | Parse + XSD-validate `camt.053.001.08` only |
| pain coverage | Generate and validate `pain.001` (v03–v12) and `pain.008` via `pain001-mcp`; reconcile against statements via `reconcile-mcp` | Parse + XSD-validate `pain.001.001.09` only |
| pacs coverage | Generate, validate, parse, scheme-check `pacs.008` via `pacs008-mcp` | Parse + XSD-validate `pacs.008.001.08` and `pacs.002.001.10` |
| Tools / prompts / resources | 41 MCP tools, 5 prompts, 4 resources across the four core servers | 9 tools; no prompts or resources documented |
| MCP sampling | Yes — `camt053-mcp`'s `classify_entry` classifies statement entries using the *client's* LLM | Not documented |
| Transports | stdio on all four; `camt053-mcp` adds streamable HTTP | stdio |
| Auth | `camt053-mcp` HTTP transport: OAuth 2.1 resource-server auth (RFC 9728) with a static-bearer dev fallback, plus per-request tenant scoping | None documented |
| Audit | `camt053-mcp` emits a structured JSONL audit stream (`camt053_mcp.audit` logger) with tenant scoping | Not documented |
| Release provenance | `camt053-mcp`: SLSA Build L3 attestations + PEP 740 sigstore attestations on PyPI uploads | Not documented |
| Test coverage | 100% line + branch coverage enforced in CI in all four repos | CI badge present; no coverage gate documented |
| License | Apache-2.0 | MIT |

Claims about Pactus are from its public README and repository metadata,
checked 2026-07-16; the project is active (last push July 2026).

### pycamt — a parser library, not an MCP server

[`pycamt`](https://github.com/ODAncona/pycamt) is a GPL-3.0 Python
*library* for parsing `camt.053` XML across several standard versions.
It has no MCP server, CLI validation, or generation capability. As of
July 2026 its last PyPI release is 1.0.1 (March 2024), with occasional
repository commits since (most recently January 2026). If you only
need to read camt.053 in a Python script and GPL-3.0 fits your project,
it is a reasonable minimal choice; it does not expose anything to an
AI agent.

### Stripe, Plaid, and QuickBooks MCP servers — a different category

These are **SaaS / aggregator** MCP servers: they give agents access to
a platform's own API, not to ISO 20022 XML. None of them (as of July
2026) parse, validate, or generate camt/pain/pacs messages.

- **Stripe MCP** (`https://mcp.stripe.com`, hosted, OAuth or restricted
  API keys): tools over the Stripe API — customers, charges, refunds,
  invoices, subscriptions — plus documentation search. Billing and
  payments data inside Stripe, not bank statement files.
- **Plaid MCP** (`https://api.dashboard.plaid.com/mcp/`, streamable
  HTTP): as of July 2026 this is a **Dashboard diagnostics server
  only** — read-only tools for debugging Items, Link conversion
  analytics, and usage metrics. Plaid's docs are explicit that it is
  *not* a gateway to end-user account or transaction data.
- **QuickBooks MCP** (Intuit's
  [`quickbooks-online-mcp-server`](https://github.com/intuit/quickbooks-online-mcp-server),
  local Node.js or hosted remote endpoint, OAuth): roughly twenty CRUD
  tools over QuickBooks Online entities — customers, vendors, invoices,
  bills, payments. Accounting-platform data, no ISO 20022.

They are complements, not competitors: `camt053-mcp`'s
`export_journal` tool, for example, emits Xero `BankTransactions` and
QuickBooks `JournalEntry` payloads from a parsed statement, bridging
the two worlds.

## When NOT to use this suite

An honest fit check — reach for something else when:

- **Your data lives in a SaaS platform, not in bank files.** If the
  question is "what did we bill in Stripe last month?", the Stripe MCP
  server answers it directly; no ISO 20022 XML is involved.
- **You need US bank-account aggregation.** Plaid-style aggregated
  balances and transactions are not ISO 20022 messages, and as of July
  2026 Plaid's MCP surface is dashboard diagnostics only — watch that
  space rather than forcing this suite to do aggregation.
- **You need bookkeeping CRUD.** Creating invoices and bills belongs to
  the QuickBooks/Xero MCP surfaces; this suite only exports journal
  payloads *from* statements.
- **You only need a one-off camt.053 parse in a Python script.** A
  plain parser library (the suite's own cores, or `pycamt` if GPL-3.0
  suits you) is less machinery than an MCP server.
- **You have no MCP client.** The suite's underlying libraries ship
  CLIs and, for `camt053`, a REST API — use those for scripted or CI
  pipelines.
- **You need message families the suite does not cover yet.** Coverage
  is deepest on `camt.05x`, `pain.001`/`pain.008`, `pacs.008`, and
  `acmt.001`; if you need, say, `semt` securities messages, none of the
  servers compared here handle them.

---

*All third-party claims on this page were verified against the cited
repositories, package indexes, and vendor documentation on 16 July
2026. Corrections welcome via
[camt053-mcp issues](https://github.com/sebastienrousseau/camt053-mcp/issues).*
