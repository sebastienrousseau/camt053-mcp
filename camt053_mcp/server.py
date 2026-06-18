# Copyright (C) 2023-2026 Sebastien Rousseau.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Model Context Protocol (MCP) server for Camt053.

This server exposes the Camt053 library's ISO 20022 ``camt.05x`` capabilities
as MCP tools so that any MCP-compatible client (Claude Desktop, IDEs, agents)
can discover message types and return reasons, inspect input schemas, validate
records and financial identifiers, parse incoming bank statements, and generate
validated reversing-entry XML.

The headline workflow is one-shot reversing-entry generation: read an incoming
camt.053 statement, find the entries carrying a return reason code (e.g. AC04
Closed Account), and emit a validated camt.053.001.14 reversal document.

Every tool is a thin, typed wrapper over :mod:`camt053.services` -- the single
shared facade also used by the CLI, REST API, and LSP server -- so all
interfaces behave identically. Tools return JSON-serializable data (dicts,
lists, or strings); on a :class:`ValueError` or :class:`camt053.exceptions.\
Camt053Error` they return an ``{"error": ...}`` payload rather than raising.

Launching the server:
    * As a console script (installed with the ``servers`` extra)::

        camt053-mcp

    * Programmatically::

        from camt053_mcp.server import main
        main()

    * In an MCP client config (e.g. Claude Desktop ``claude_desktop_config.json``)::

        {
          "mcpServers": {
            "camt053": {
              "command": "camt053-mcp"
            }
          }
        }

The server communicates over stdio (FastMCP's default transport).
"""

import json
from typing import Any

from camt053 import services
from camt053.exceptions import Camt053Error
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.prompts.base import AssistantMessage, UserMessage

server = FastMCP("camt053")


def _paginate(
    entries: list[dict],
    offset: int,
    limit: int | None,
) -> list[dict] | dict[str, Any]:
    """Apply optional pagination to a list of entry dicts.

    When ``limit`` is ``None`` the full ``entries`` list is returned unchanged,
    preserving the behaviour expected by existing callers. Otherwise a
    paginated envelope ``{"total", "offset", "limit", "entries"}`` is returned,
    where ``entries`` is the ``offset:offset + limit`` slice.

    A negative ``offset`` or a negative ``limit`` yields an ``{"error": ...}``
    payload, consistent with the module's error convention.

    Args:
        entries: The full list of entry dicts to paginate.
        offset: The zero-based index of the first entry to return.
        limit: The maximum number of entries to return, or ``None`` for all.
    """
    if offset < 0:
        return {"error": "offset must be non-negative"}
    if limit is not None and limit < 0:
        return {"error": "limit must be non-negative"}
    if limit is None:
        return entries
    return {
        "total": len(entries),
        "offset": offset,
        "limit": limit,
        "entries": entries[offset : offset + limit],
    }


@server.tool()
def list_message_types() -> list[dict]:
    """List every supported ISO 20022 camt.05x message type.

    Returns a list of ``{"message_type": ..., "name": ...}`` dictionaries, one
    per supported message type (e.g. ``camt.053.001.14``).
    """
    try:
        return services.list_message_types()
    except (ValueError, Camt053Error) as exc:
        return [{"error": str(exc)}]


@server.tool()
def list_return_reasons() -> list[dict]:
    """List every known ISO external return reason code with its name.

    Returns a list of ``{"code": ..., "name": ...}`` dictionaries (e.g.
    ``{"code": "AC04", "name": "Closed Account Number"}``).
    """
    try:
        return services.list_return_reasons()
    except (ValueError, Camt053Error) as exc:
        return [{"error": str(exc)}]


@server.tool()
def get_required_fields(message_type: str) -> list[str]:
    """List the required input field names for a given camt message type.

    Args:
        message_type: A supported ISO 20022 camt.05x message type.
    """
    try:
        return services.get_required_fields(message_type)
    except (ValueError, Camt053Error) as exc:
        return [f"error: {exc}"]


@server.tool()
def get_input_schema(message_type: str) -> dict:
    """Return the JSON Schema describing the flat input record for a type.

    Args:
        message_type: A supported ISO 20022 camt.05x message type.
    """
    try:
        return services.get_input_schema(message_type)
    except (ValueError, Camt053Error) as exc:
        return {"error": str(exc)}


@server.tool()
def validate_records(message_type: str, records: list[dict]) -> dict:
    """Validate flat records against a message type's input JSON Schema.

    Returns a report ``{"valid": bool, "total": int, "valid_count": int,
    "errors": [...]}``.

    Args:
        message_type: A supported ISO 20022 camt.05x message type.
        records: One or more flat reversing-entry records to validate.
    """
    try:
        return services.validate_records(message_type, records)
    except (ValueError, Camt053Error) as exc:
        return {"error": str(exc)}


@server.tool()
def validate_identifier(kind: str, value: str) -> dict:
    """Validate a financial identifier (IBAN, BIC, or LEI).

    Returns ``{"kind": str, "value": str, "valid": bool}``.

    Args:
        kind: One of ``"iban"``, ``"bic"``, or ``"lei"`` (case-insensitive).
        value: The identifier value to check.
    """
    try:
        return services.validate_identifier(kind, value)
    except (ValueError, Camt053Error) as exc:
        return {"error": str(exc)}


@server.tool()
def parse_statement(xml: str) -> dict:
    """Parse an incoming camt.05x statement into plain data.

    Returns the parsed document as a JSON-serialisable dict (group header plus
    statements, each with its account, balances, and entries), or an
    ``{"error": ...}`` payload if the XML cannot be parsed.

    Args:
        xml: The raw statement XML as a string.
    """
    try:
        return services.parse_statement(xml)
    except (ValueError, Camt053Error) as exc:
        return {"error": str(exc)}


@server.tool()
def list_entries(
    xml: str,
    offset: int = 0,
    limit: int | None = None,
) -> list[dict] | dict[str, Any]:
    """Return every statement entry across all of a statement's statements.

    When ``limit`` is ``None`` (the default) the full list of entries is
    returned. When ``limit`` is given, a paginated envelope ``{"total",
    "offset", "limit", "entries"}`` is returned instead, exposing the
    ``offset:offset + limit`` slice. A negative ``offset`` or ``limit`` yields
    an ``{"error": ...}`` payload.

    Args:
        xml: The raw statement XML as a string.
        offset: The zero-based index of the first entry to return (paginated
            mode only; default ``0``).
        limit: The maximum number of entries to return, or ``None`` for the
            full list (default ``None``).
    """
    try:
        entries = services.list_entries(xml)
    except (ValueError, Camt053Error) as exc:
        return [{"error": str(exc)}]
    return _paginate(entries, offset, limit)


@server.tool()
def filter_entries(
    xml: str,
    reason_code: str = "AC04",
    offset: int = 0,
    limit: int | None = None,
) -> list[dict] | dict[str, Any]:
    """Return the statement entries carrying a given return reason code.

    When ``limit`` is ``None`` (the default) the full list of matching entries
    is returned, preserving the behaviour expected by existing callers. When
    ``limit`` is given, a paginated envelope ``{"total", "offset", "limit",
    "entries"}`` is returned instead, exposing the ``offset:offset + limit``
    slice. A negative ``offset`` or ``limit`` yields an ``{"error": ...}``
    payload.

    Args:
        xml: The raw statement XML as a string.
        reason_code: The ISO external return reason to match (default
            ``"AC04"`` Closed Account).
        offset: The zero-based index of the first entry to return (paginated
            mode only; default ``0``).
        limit: The maximum number of entries to return, or ``None`` for the
            full list (default ``None``).
    """
    try:
        entries = services.filter_entries(xml, reason_code)
    except (ValueError, Camt053Error) as exc:
        return [{"error": str(exc)}]
    return _paginate(entries, offset, limit)


@server.tool()
def generate_reversal(xml: str, reason_code: str = "AC04") -> str:
    """Read a statement and generate a validated reversing-entry document.

    This is the headline one-shot workflow: parse the incoming camt.053, pick
    the entries with the requested return reason (e.g. AC04 Closed Account),
    and emit a validated camt.053.001.14 reversal statement.

    Returns the validated XML document as a string, or an ``{"error": ...}``
    payload (serialized) if generation fails.

    Args:
        xml: The raw incoming statement XML as a string.
        reason_code: The ISO external return reason to reverse (default
            ``"AC04"``).
    """
    try:
        return services.generate_reversal(xml, reason_code)
    except (ValueError, Camt053Error) as exc:
        return json.dumps({"error": str(exc)})


@server.resource("camt053://return-reasons")
def return_reason_catalog() -> str:
    """Expose the ISO external return-reason catalog as a JSON resource.

    Returns the full list of ``{"code", "name"}`` return-reason dictionaries
    (from :func:`camt053.services.list_return_reasons`) serialised as JSON, so
    an agent can load the catalog as reference context without calling a tool.
    On a :class:`ValueError` or
    :class:`camt053.exceptions.Camt053Error` an ``{"error": ...}`` payload is
    returned instead (serialised), consistent with the server's tools.
    """
    try:
        return json.dumps(services.list_return_reasons())
    except (ValueError, Camt053Error) as exc:
        return json.dumps({"error": str(exc)})


@server.resource("camt053://message-types")
def message_type_catalog() -> str:
    """Expose the supported camt.05x message types as a JSON resource.

    Returns the list of ``{"message_type", "name"}`` dictionaries (from
    :func:`camt053.services.list_message_types`) serialised as JSON, so an agent
    can load the supported message types as reference context without calling a
    tool. On a :class:`ValueError` or
    :class:`camt053.exceptions.Camt053Error` an ``{"error": ...}`` payload is
    returned instead (serialised), consistent with the server's tools.
    """
    try:
        return json.dumps(services.list_message_types())
    except (ValueError, Camt053Error) as exc:
        return json.dumps({"error": str(exc)})


@server.prompt()
def reversal_preview(
    reason_code: str = "AC04",
) -> list[UserMessage | AssistantMessage]:
    """Guide an agent through previewing and confirming a reversal.

    Returns a multi-step message template that walks an agent through the
    headline reversal workflow for a given return reason code: parse the
    statement, preview the entries that would be reversed via
    ``filter_entries``, confirm with the operator, then call
    ``generate_reversal``. The flow is parameterised by ``reason_code`` so the
    same guidance can target any ISO external return reason.

    Args:
        reason_code: The ISO external return reason to preview (default
            ``"AC04"`` Closed Account).
    """
    return [
        UserMessage(
            "I want to reverse the entries in a camt.053 statement that "
            f"carry the return reason code {reason_code}. Walk me through it "
            "safely, one step at a time, and wait for my confirmation before "
            "generating anything."
        ),
        AssistantMessage(
            "We'll do this in four steps:\n"
            "1. Parse the statement: call `parse_statement` with the raw "
            "statement XML so we can see its structure.\n"
            f"2. Preview the reversals: call `filter_entries` with that XML "
            f'and reason_code="{reason_code}" to list exactly which entries '
            "would be reversed. For a large statement, pass `limit` (and "
            "`offset`) to page through the matches.\n"
            "3. Confirm: review the previewed entries together and explicitly "
            "confirm the reversal is correct before proceeding.\n"
            f"4. Generate: once confirmed, call `generate_reversal` with the "
            f'same XML and reason_code="{reason_code}" to emit the validated '
            "camt.053.001.14 reversal document.\n"
            "Please share the statement XML and we'll start at step 1."
        ),
    ]


def main() -> None:
    """Run the Camt053 MCP server over stdio (the ``camt053-mcp`` entry point)."""
    server.run()


if __name__ == "__main__":  # pragma: no cover
    main()
