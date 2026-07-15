# Quickstart

A 10-minute install → MCP client config → first conversation tutorial
for `camt053-mcp`.

## 1. Install

`camt053-mcp` runs on macOS, Linux, and Windows and requires Python
3.10+. It pulls in the core `camt053` library and the MCP SDK
automatically.

```sh
python -m pip install camt053-mcp
```

Verify:

```sh
python -c "import camt053_mcp; print(camt053_mcp.__version__)"
```

## 2. Launch the server

The package installs a `camt053-mcp` console entry point that starts
the server over stdio (FastMCP's default transport):

```sh
camt053-mcp
```

The command speaks MCP on stdin/stdout — it is meant to be launched by
an MCP client, not used interactively.

## 3. Register it with your MCP client

### Claude Desktop

Add an entry to `~/Library/Application Support/Claude/claude_desktop_config.json`
(macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "camt053": { "command": "camt053-mcp" }
  }
}
```

Restart Claude Desktop. The 22 camt053 tools, three resources, and the
`reversal_preview` prompt are now available in any chat.

### Other clients (Continue, Cursor, generic stdio MCP clients)

Point the client at the `camt053-mcp` command. The server speaks
standard MCP — no custom transport, no auth.

## 4. First conversation

Drop a camt.053 statement into a chat and ask the agent to find any
returned entries and generate a reversal:

> Here is a camt.053 statement. Find every entry returned with code
> AC04, show me the totals, and if I confirm, emit a validated
> reversing-entry document.

The `reversal_preview` prompt guides the agent through the four-step
safe pattern (`parse_statement` → `filter_entries` → confirm with
operator → `generate_reversal`).

## 5. Use in-process (no MCP client needed)

To prototype or write integration tests, call the tools through the
FastMCP instance directly. Every example in `examples/` follows this
pattern. The shortest one:

```python
import asyncio

from camt053_mcp.server import server


async def main() -> None:
    result = await server.call_tool("list_message_types", {})
    content = result[0] if isinstance(result, tuple) else result
    print(content[0].text)


asyncio.run(main())
```

A focused example exists for every tool — `examples/01_list_message_types.py`,
`examples/02_list_return_reasons.py`, …, `examples/13_generate_reversal.py`.

## 6. Multi-tenant HTTP deployment

Everything above runs the server over **stdio**: one process per
operator, launched by the MCP client, no network surface, no
authentication needed. For a *shared* deployment — one server instance
serving several agents, teams, or tenant accounts — switch to the
streamable-HTTP transport:

```sh
export CAMT053_MCP_TOKEN="$(openssl rand -hex 32)"
camt053-mcp --transport=http --bind=0.0.0.0:8080
```

- `--transport=http` serves MCP streamable HTTP at `http://HOST:PORT/mcp`.
  The default remains `--transport=stdio`; existing stdio behaviour is
  unchanged.
- `--bind HOST:PORT` picks the listen address (default `127.0.0.1:8080`,
  loopback-only — exposing the server beyond the host is an explicit
  opt-in).
- **Bearer auth is mandatory over HTTP.** The server refuses to start
  unless `CAMT053_MCP_TOKEN` is set, and every HTTP request must carry
  `Authorization: Bearer <token>`; a missing or wrong credential is
  rejected `401` before it reaches the MCP layer. stdio needs no token.

### Tenant scoping with the `Camt053-Account` header

HTTP callers may add an optional `Camt053-Account` header naming the
tenant/account the session acts for. The value is forwarded into the
tool-visible context — any tool can resolve it via
`camt053_mcp.transport.current_tenant(ctx)`, and the
`get_tenant_context` tool reports it directly:

```sh
curl -s http://localhost:8080/mcp \
  -H "Authorization: Bearer $CAMT053_MCP_TOKEN" \
  -H "Camt053-Account: acme-treasury" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "curl", "version": "0"}}}'
```

An MCP client config for the HTTP transport (clients that support
streamable HTTP, e.g. via `mcp-remote` or native HTTP support):

```json
{
  "mcpServers": {
    "camt053": {
      "url": "https://camt053.internal.example.com/mcp",
      "headers": {
        "Authorization": "Bearer <token>",
        "Camt053-Account": "acme-treasury"
      }
    }
  }
}
```

### Audit attribution

Every HTTP request — rejected or authorized — and every
`get_tenant_context` call is written to the `camt053_mcp.audit` logger
as one JSON line carrying the **service name** (`camt053-mcp`) and the
tenant **scope**, so multi-tenant calls stay attributable in your log
pipeline:

```json
{"event": "http.request.authorized", "path": "/mcp", "scope": "acme-treasury", "service": "camt053-mcp", "timestamp_utc": "2026-07-15T10:00:00.000000Z"}
```

Route that logger to your append-only audit sink (file, journald,
object storage) alongside the wider camt053 suite's hash-chain audit
log. For a production-shaped recipe (TLS proxy, systemd, containers),
see the [deployment cookbook](deployment-cookbook.md).

## 7. Next steps

- Browse the full [tool catalog](../README.md#tools) (22 tools across
  parsing, validation, identifier checking, return-reason filtering,
  reversal generation, CBPR+ readiness, MT94x migration, journal
  export, LLM classification, and tenant scoping).
- Try the [resources](../README.md#resources) — `camt053://return-reasons`
  and `camt053://message-types` give read-only catalog context the
  agent can load without a tool call.
- Read the suite's deeper docs at
  <https://sebastienrousseau.github.io/camt053/>.

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `command not found: camt053-mcp` | Install went to a venv that isn't on PATH | Re-install in your active env, or invoke `python -m camt053_mcp.server` |
| MCP client doesn't see the tools | Wrong path in client config | Use absolute path: `which camt053-mcp` → paste into client `command` |
| `Camt053Error: schema X.Y.Z not supported` | Statement is on a major version this release doesn't cover | Check `list_message_types`; pin core `camt053` to a release that covers it |
