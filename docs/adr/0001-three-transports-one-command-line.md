<!-- SPDX-License-Identifier: Apache-2.0 OR MIT -->

# 0001. Serve stdio, streamable HTTP and SSE from one command line

- **Status:** Accepted
- **Date:** 2026-09-19
- **Deciders:** maintainer

## Context

The server spoke stdio (a client spawns the process, nothing listens)
and, since 0.0.13, an authenticated streamable HTTP transport of its own
(`--transport http`, `transport.py`: bearer token or OAuth 2.1, tenant
scoping, audit chain) for shared multi-tenant deployments. That transport
carries this server's auth model and flags; the rest of the suite had no
HTTP listener at all. A gateway that fans one server out to many agents,
or an auditor such as scout that speaks HTTP, needs the same command line
on every server. The MCP specification now has two current revisions on
the HTTP binding, `2025-11-25` (an `initialize` handshake and a session
header) and `2026-07-28` (stateless, per-request `_meta`,
`server/discover`), and clients on either must be served. The older
HTTP+SSE transport (`2024-11-05`) is still what some hosts expect.

## Options considered

1. Stay on stdio plus the authenticated `http` transport and leave the
   suite-wide command line to a wrapper process.
2. Add HTTP behind a bespoke module per server, each with its own flags.
3. One shared module, copied verbatim into every server of the suite,
   that maps the same three flags onto the SDK's transports.

## Decision

Option 3. `camt053-mcp` runs stdio; `camt053-mcp --transport
streamable-http` listens on `--host`/`--port` at `/mcp` and speaks both
current protocol revisions on that one endpoint, streaming responses as
server-sent events and offering the server-to-client stream on `GET`;
`camt053-mcp --transport sse` serves the older HTTP+SSE transport at
`/sse` and `/messages/`. The module binds loopback unless told otherwise
and adds no authentication of its own: a routable deployment sits behind
a gateway the operator trusts. The authenticated `--transport http` with
`--bind` stays as it was, an additional choice on the same command line.

## Consequences

`main()` keeps its argparse parser and takes `--host`/`--port` from
`_cli.add_arguments`, dispatching the two new choices through
`_transports.run`; both files are the same in every server, so the suite
is started, documented and tested the same way. Each server is verified
over streamable HTTP with scout in both protocol eras and over SSE with
the SDK client before release; the conformance workflow lists tools over
every transport. The stdio and `http` tests stay as they were.
