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

"""Tests for the Camt053 MCP server."""

import asyncio
import json

import pytest

pytest.importorskip("mcp")

from camt053.exceptions import Camt053Error  # noqa: E402
from mcp.server.fastmcp import FastMCP  # noqa: E402

import camt053_mcp.server as server  # noqa: E402

EXPECTED_TOOLS = {
    "list_message_types",
    "list_return_reasons",
    "get_required_fields",
    "get_input_schema",
    "validate_records",
    "validate_identifier",
    "parse_statement",
    "filter_entries",
    "generate_reversal",
}


def _registered_tool_names() -> set[str]:
    """Return the names of every tool registered on the FastMCP server.

    Prefers the synchronous ``_tool_manager.list_tools()`` introspection;
    falls back to the async ``list_tools()`` API if unavailable.
    """
    manager = getattr(server.server, "_tool_manager", None)
    if manager is not None and hasattr(manager, "list_tools"):
        return {tool.name for tool in manager.list_tools()}
    tools = asyncio.run(server.server.list_tools())
    return {tool.name for tool in tools}


def test_server_and_main_are_well_formed():
    """The module exposes a FastMCP server and a callable ``main``."""
    assert isinstance(server.server, FastMCP)
    assert callable(server.main)


def test_all_tools_registered():
    """All nine tools are registered on the server."""
    assert _registered_tool_names() == EXPECTED_TOOLS


def test_list_message_types_returns_3():
    """The list tool reports every supported message type (3)."""
    result = server.list_message_types()
    assert isinstance(result, list)
    assert len(result) == 3
    assert all("message_type" in row and "name" in row for row in result)


def test_list_return_reasons_includes_ac04():
    """The return-reason tool lists ISO codes including AC04."""
    result = server.list_return_reasons()
    assert isinstance(result, list)
    assert any(row.get("code") == "AC04" for row in result)


def test_validate_identifier_valid_and_invalid():
    """A known-good and known-bad BIC are classified correctly."""
    good = server.validate_identifier("bic", "NWBKGB2LXXX")
    assert good == {"kind": "bic", "value": "NWBKGB2LXXX", "valid": True}

    bad = server.validate_identifier("bic", "NOTABIC")
    assert bad["valid"] is False


def test_validate_identifier_unsupported_kind_returns_error():
    """An unsupported identifier kind yields an error dict, not an exception."""
    result = server.validate_identifier("ssn", "123-45-6789")
    assert "error" in result


def test_parse_statement_returns_data(statement_xml):
    """Parsing a valid statement yields a dict with statement data."""
    result = server.parse_statement(statement_xml)
    assert isinstance(result, dict)
    assert "error" not in result


def test_filter_entries_finds_ac04(statement_xml):
    """Filtering on AC04 returns the single matching entry."""
    result = server.filter_entries(statement_xml, "AC04")
    assert isinstance(result, list)
    assert len(result) == 1


def test_generate_reversal_returns_xml(statement_xml):
    """Generating a reversal yields a validated XML reversing entry."""
    xml = server.generate_reversal(statement_xml, "AC04")
    assert isinstance(xml, str)
    assert xml.lstrip().startswith("<?xml")
    assert "<RvslInd>true</RvslInd>" in xml


def test_invalid_message_type_returns_error_dict():
    """An unsupported message type returns an ``{"error": ...}`` dict."""
    result = server.get_required_fields("camt.999.999.99")
    # get_required_fields returns a list; the error is surfaced as a string
    # entry. The schema-bearing tools return an error dict directly.
    schema_result = server.get_input_schema("camt.999.999.99")
    assert isinstance(schema_result, dict)
    assert "error" in schema_result
    assert any("error" in str(item) for item in result)


def test_generate_reversal_error_is_serializable():
    """A failed reversal returns a JSON-serializable error string."""
    out = server.generate_reversal("<not-a-statement/>", "AC04")
    payload = json.loads(out)
    assert "error" in payload


def test_list_message_types_error_returns_error_list(monkeypatch):
    """A failing ``list_message_types`` yields an ``{"error": ...}`` list."""

    def boom():
        raise ValueError("boom")

    monkeypatch.setattr(server.services, "list_message_types", boom)
    result = server.list_message_types()
    assert result == [{"error": "boom"}]


def test_list_return_reasons_error_returns_error_list(monkeypatch):
    """A failing ``list_return_reasons`` yields an ``{"error": ...}`` list."""

    def boom():
        raise Camt053Error("boom")

    monkeypatch.setattr(server.services, "list_return_reasons", boom)
    result = server.list_return_reasons()
    assert result == [{"error": "boom"}]


def test_get_required_fields_unsupported_type_returns_error():
    """An unsupported message type yields a string ``error:`` entry."""
    result = server.get_required_fields("camt.999.001.99")
    assert any("error" in str(item) for item in result)


def test_validate_records_unsupported_type_returns_error():
    """An unsupported message type yields an ``{"error": ...}`` dict."""
    result = server.validate_records("camt.999.001.99", [{}])
    assert isinstance(result, dict)
    assert "error" in result


def test_parse_statement_malformed_xml_returns_error():
    """Malformed XML yields an ``{"error": ...}`` dict, not an exception."""
    result = server.parse_statement("<nope/>")
    assert isinstance(result, dict)
    assert "error" in result


def test_filter_entries_malformed_xml_returns_error():
    """Malformed XML yields an ``{"error": ...}`` list, not an exception."""
    result = server.filter_entries("<nope/>", "AC04")
    assert isinstance(result, list)
    assert result and "error" in result[0]


def test_generate_reversal_no_matching_reason_returns_error(statement_xml):
    """A reason code matching no entries yields a serialized error string."""
    out = server.generate_reversal(statement_xml, "ZZ99")
    payload = json.loads(out)
    assert "error" in payload


def test_main_runs_server(monkeypatch):
    """``main`` invokes ``server.run`` and returns without hanging."""
    calls = []
    monkeypatch.setattr(
        server.server, "run", lambda *a, **k: calls.append(True)
    )
    assert server.main() is None
    assert calls == [True]


def test_call_tool_through_fastmcp():
    """Tools are invocable through the FastMCP dispatch layer."""

    async def go():
        result = await server.server.call_tool(
            "validate_identifier", {"kind": "bic", "value": "NWBKGB2LXXX"}
        )
        # call_tool returns a sequence of content blocks; extract the text.
        block = result[0] if isinstance(result, list | tuple) else result
        text = getattr(block, "text", None)
        if text is None and isinstance(result, tuple):
            # Newer FastMCP returns (content, structured) tuples.
            text = json.dumps(result[1])
        return json.loads(text)

    payload = asyncio.run(go())
    assert payload["valid"] is True
