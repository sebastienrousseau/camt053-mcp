<!-- SPDX-License-Identifier: Apache-2.0 OR MIT -->

# `camt053-mcp` governance

This document describes how `camt053-mcp` is run, how decisions are made, and
how to take on responsibility for it. `camt053-mcp` is part of the
[`camt053` suite](https://github.com/sebastienrousseau/camt053); the
suite-wide governance lives in the
[core repo's GOVERNANCE.md](https://github.com/sebastienrousseau/camt053/blob/main/GOVERNANCE.md).
This document covers the mcp-specific bits.

## Mission and scope

`camt053-mcp` is the Model Context Protocol (MCP) server exposing the
[`camt053`](https://github.com/sebastienrousseau/camt053) ISO 20022
bank-statement library as agent tools, resources, and prompts. Changes are
weighed against the same criterion as the core library: **correctness,
security, and clarity over feature breadth**.

A change is in-scope if it adds a tool / resource / prompt that exposes
existing `camt053.services` functionality to MCP clients, hardens the
transport, or improves the agent-driven workflow shape. A change is
out-of-scope if it duplicates logic that belongs in the core library, or
ships features that depend on a particular client (e.g. Claude-specific
extensions).

## Roles + decision making

Inherited from the
[suite governance](https://github.com/sebastienrousseau/camt053/blob/main/GOVERNANCE.md).
Briefly:

| Role | Who | Can |
| :--- | :--- | :--- |
| **Maintainer** | Listed in [`MAINTAINERS.md`](MAINTAINERS.md) | Merge PRs, cut releases, triage, set direction |
| **Contributor** | Anyone with a merged PR | Propose changes, review, discuss |
| **User** | Everyone | File issues, ask questions, request features |

- Day-to-day changes land via PR with maintainer approval (conventional
  commits + signed commits + branch policy from the suite STYLEGUIDE).
- Larger changes (new tool surface, new transport, dependency additions)
  require a tracking GitHub Issue + 72-hour comment window + maintainer
  agreement.
- Releases are cut against a v0.0.X milestone; signed tag + OIDC publish
  to PyPI with PEP 740 attestations.
- Security disclosures: 3-day ack / 7-day assessment / 30-day fix per
  [`SECURITY.md`](SECURITY.md).

## Cross-suite consistency

All packages in the suite share the same CI floor, release pipeline, and
governance documents. Cross-suite policy changes land in the core repo
first, then mirror to the sibling packages.

## Becoming a maintainer

See the path in [`MAINTAINERS.md`](MAINTAINERS.md) — same shape as the
core repo's policy.

## Updating this document

PR with the 72-hour comment window for anything material. The lead
maintainer has final say but engages with substantive feedback before
merging.
