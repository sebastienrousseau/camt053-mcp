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

Restart Claude Desktop. The 13 camt053 tools, two resources, and the
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

## 6. Next steps

- Browse the full [tool catalog](../README.md#tools) (13 tools across
  parsing, validation, identifier checking, return-reason filtering,
  reversal generation, and CBPR+ readiness).
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
