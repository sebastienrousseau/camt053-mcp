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
    "validate_statement",
    "check_cbpr_readiness",
    "get_cbpr_cutover_date",
    "cite_rulebook",
    "list_rulebook_clauses",
    "export_journal",
    "list_export_journal_targets",
    "parse_statement",
    "list_entries",
    "filter_entries",
    "generate_reversal",
}


def _registered_prompt_names() -> set[str]:
    """Return the names of every prompt registered on the FastMCP server."""
    return {
        prompt.name for prompt in server.server._prompt_manager.list_prompts()
    }


def _registered_resource_uris() -> set[str]:
    """Return the URIs of every resource registered on the FastMCP server."""
    return {
        str(resource.uri)
        for resource in server.server._resource_manager.list_resources()
    }


def _read_resource(uri: str):
    """Read a resource through FastMCP and return its decoded JSON payload."""
    contents = asyncio.run(server.server.read_resource(uri))
    block = contents[0] if isinstance(contents, list | tuple) else contents
    return json.loads(block.content)


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
    """Every tool is registered on the server."""
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


def test_validate_statement_detects_message_type(statement_xml):
    """Validation detects the document's camt message type."""
    result = server.validate_statement(statement_xml)
    assert isinstance(result, dict)
    assert result["message_type"] == "camt.053.001.14"
    assert "valid" in result
    assert isinstance(result["errors"], list)


def test_validate_statement_invalid_reports_errors(statement_xml):
    """A schema-invalid document yields ``valid=False`` with errors."""
    result = server.validate_statement(statement_xml)
    assert result["valid"] is False
    assert result["errors"]


def test_validate_statement_valid_document(statement_xml):
    """A schema-valid reversal validates as ``valid=True`` with no errors."""
    reversal = server.generate_reversal(statement_xml, "AC04")
    result = server.validate_statement(reversal)
    assert result["valid"] is True
    assert result["message_type"] == "camt.053.001.14"
    assert result["errors"] == []


def test_validate_statement_malformed_xml_returns_error():
    """Malformed XML yields an ``{"error": ...}`` dict, not an exception."""
    result = server.validate_statement("<nope/>")
    assert isinstance(result, dict)
    assert "error" in result


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


def test_list_entries_returns_all_by_default(statement_xml):
    """``list_entries`` returns every entry as a plain list by default."""
    result = server.list_entries(statement_xml)
    assert isinstance(result, list)
    assert len(result) == 3


def test_list_entries_malformed_xml_returns_error():
    """Malformed XML yields an ``{"error": ...}`` list, not an exception."""
    result = server.list_entries("<nope/>")
    assert isinstance(result, list)
    assert result and "error" in result[0]


def test_list_entries_paginated_envelope(statement_xml):
    """A ``limit`` returns a paginated envelope with a sliced page."""
    result = server.list_entries(statement_xml, offset=1, limit=1)
    assert result["total"] == 3
    assert result["offset"] == 1
    assert result["limit"] == 1
    assert len(result["entries"]) == 1


def test_filter_entries_default_unchanged(statement_xml):
    """Without a ``limit``, ``filter_entries`` returns the full list."""
    result = server.filter_entries(statement_xml, "AC04")
    assert isinstance(result, list)
    assert len(result) == 1


def test_filter_entries_paginated_envelope(statement_xml):
    """A ``limit`` returns a paginated envelope around the matches."""
    result = server.filter_entries(statement_xml, "AC04", offset=0, limit=5)
    assert result == {
        "total": 1,
        "offset": 0,
        "limit": 5,
        "entries": result["entries"],
    }
    assert len(result["entries"]) == 1


def test_filter_entries_offset_past_end_returns_empty_page(statement_xml):
    """An offset beyond the result count yields an empty page (total kept)."""
    result = server.filter_entries(statement_xml, "AC04", offset=10, limit=5)
    assert result["total"] == 1
    assert result["entries"] == []


def test_pagination_negative_offset_returns_error(statement_xml):
    """A negative offset yields an ``{"error": ...}`` payload."""
    result = server.filter_entries(statement_xml, "AC04", offset=-1, limit=1)
    assert isinstance(result, dict)
    assert "error" in result


def test_pagination_negative_limit_returns_error(statement_xml):
    """A negative limit yields an ``{"error": ...}`` payload."""
    result = server.list_entries(statement_xml, offset=0, limit=-1)
    assert isinstance(result, dict)
    assert "error" in result


def test_reversal_preview_prompt_registered():
    """The ``reversal_preview`` prompt is registered on the server."""
    assert "reversal_preview" in _registered_prompt_names()


def test_reversal_preview_prompt_renders_default_reason(statement_xml):
    """The prompt renders a guided template defaulting to AC04."""
    result = asyncio.run(server.server.get_prompt("reversal_preview", {}))
    texts = [m.content.text for m in result.messages]
    assert len(result.messages) == 2
    assert any("AC04" in text for text in texts)
    assert any("filter_entries" in text for text in texts)
    assert any("generate_reversal" in text for text in texts)


def test_reversal_preview_prompt_parameterised_reason():
    """The prompt threads the supplied reason code into its guidance."""
    result = asyncio.run(
        server.server.get_prompt("reversal_preview", {"reason_code": "AC06"})
    )
    texts = " ".join(m.content.text for m in result.messages)
    assert "AC06" in texts
    assert "AC04" not in texts


def test_reconcile_against_pain001_prompt_registered():
    """The ``reconcile_against_pain001`` prompt is registered."""
    assert "reconcile_against_pain001" in _registered_prompt_names()


def test_reconcile_against_pain001_prompt_renders():
    """The reconcile prompt produces a 5-step user+assistant template."""
    result = asyncio.run(
        server.server.get_prompt("reconcile_against_pain001", {})
    )
    texts = " ".join(m.content.text for m in result.messages)
    assert len(result.messages) == 2
    assert "pain.001" in texts
    assert "EndToEndId" in texts
    assert "parse_statement" in texts


def test_find_duplicate_entries_prompt_registered():
    """The ``find_duplicate_entries`` prompt is registered."""
    assert "find_duplicate_entries" in _registered_prompt_names()


def test_find_duplicate_entries_prompt_renders():
    """The duplicate-finder prompt produces a 4-step template."""
    result = asyncio.run(
        server.server.get_prompt("find_duplicate_entries", {})
    )
    texts = " ".join(m.content.text for m in result.messages)
    assert len(result.messages) == 2
    assert "duplicate" in texts.lower()
    assert "parse_statement" in texts


def test_match_to_invoice_set_prompt_registered():
    """The ``match_to_invoice_set`` prompt is registered."""
    assert "match_to_invoice_set" in _registered_prompt_names()


def test_match_to_invoice_set_prompt_renders():
    """The invoice-matcher prompt produces a 5-step template."""
    result = asyncio.run(server.server.get_prompt("match_to_invoice_set", {}))
    texts = " ".join(m.content.text for m in result.messages)
    assert len(result.messages) == 2
    assert "invoice" in texts.lower()
    assert "remittance" in texts.lower()
    assert "parse_statement" in texts


def test_generate_reversal_no_matching_reason_returns_error(statement_xml):
    """A reason code matching no entries yields a serialized error string."""
    out = server.generate_reversal(statement_xml, "ZZ99")
    payload = json.loads(out)
    assert "error" in payload


def test_resources_registered():
    """Both reference resources are registered on the server."""
    assert _registered_resource_uris() == {
        "camt053://return-reasons",
        "camt053://message-types",
    }


def test_return_reason_resource_lists_ac04():
    """The return-reason resource returns the catalog including AC04."""
    payload = _read_resource("camt053://return-reasons")
    assert isinstance(payload, list)
    assert any(row.get("code") == "AC04" for row in payload)


def test_message_type_resource_lists_three():
    """The message-type resource returns the 3 supported types."""
    payload = _read_resource("camt053://message-types")
    assert isinstance(payload, list)
    assert len(payload) == 3
    assert all("message_type" in row and "name" in row for row in payload)


def test_return_reason_resource_error_is_serializable(monkeypatch):
    """A failing return-reason resource yields a serialized error payload."""

    def boom():
        raise Camt053Error("boom")

    monkeypatch.setattr(server.services, "list_return_reasons", boom)
    payload = _read_resource("camt053://return-reasons")
    assert payload == {"error": "boom"}


def test_message_type_resource_error_is_serializable(monkeypatch):
    """A failing message-type resource yields a serialized error payload."""

    def boom():
        raise ValueError("boom")

    monkeypatch.setattr(server.services, "list_message_types", boom)
    payload = _read_resource("camt053://message-types")
    assert payload == {"error": "boom"}


# ---------------------------------------------------------------------------
# bank_session_context templated resource (D1 in #17)
# ---------------------------------------------------------------------------


def test_bank_session_payload_eu_uk_bic():
    """An EU/UK BIC yields SEPA + CBPR+ + HVPS+ recommended clauses."""
    payload = server._bank_session_payload("chat-42", "NWBKGB2LXXX")
    assert payload["session_id"] == "chat-42"
    assert payload["bic"] == "NWBKGB2LXXX"
    assert payload["bic_country"] == "GB"
    assert payload["bic_kind"] == "BIC11"
    clauses = payload["recommended_rulebook_clauses"]
    assert "SEPA/2025/iban-only" in clauses
    assert "CBPR+/2026/structured-address-mandate-nov-2026" in clauses
    assert "HVPS+/2026/t2-rtgs-uplift-mr2026" in clauses


def test_bank_session_payload_non_eu_bic_skips_sepa():
    """A non-EU/UK BIC omits the SEPA clauses but keeps CBPR+ + HVPS+."""
    payload = server._bank_session_payload("chat-1", "BOFAUS3NXXX")
    assert payload["bic_country"] == "US"
    clauses = payload["recommended_rulebook_clauses"]
    assert not any(c.startswith("SEPA/") for c in clauses)
    assert any(c.startswith("CBPR+/") for c in clauses)


def test_bank_session_payload_bic8_kind():
    """An 8-character BIC is classified as BIC8."""
    payload = server._bank_session_payload("s", "NWBKGB2L")
    assert payload["bic_kind"] == "BIC8"


def test_bank_session_payload_malformed_bic():
    """A malformed BIC yields None for country + kind, no crash."""
    payload = server._bank_session_payload("s", "TOO-SHORT")
    assert payload["bic_kind"] is None
    assert payload["bic_country"] is None


def test_bank_session_payload_lowercases_normalised():
    """The payload normalises BIC to uppercase."""
    payload = server._bank_session_payload("s", "nwbkgb2lxxx")
    assert payload["bic"] == "NWBKGB2LXXX"


def test_bank_session_payload_cutover_date_included():
    """Every payload carries the well-known Nov 2026 cutover date."""
    payload = server._bank_session_payload("s", "NWBKGB2L")
    assert payload["cbpr_cutover_date"]
    assert payload["cbpr_cutover_date"].startswith("2026-")


def test_bank_session_resource_via_fastmcp():
    """The templated resource resolves through FastMCP's URI router."""
    payload = _read_resource("camt053://session/chat-42/bank/NWBKGB2LXXX")
    assert payload["session_id"] == "chat-42"
    assert payload["bic"] == "NWBKGB2LXXX"


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


# ─── CBPR+ readiness tool (Nov 14-16 2026 cliff) ────────────────────────────

_V08_CLEAN = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.08">'
    "<BkToCstmrStmt><GrpHdr><MsgId>M</MsgId>"
    "<CreDtTm>2026-06-21T10:00:00</CreDtTm></GrpHdr>"
    "<Stmt><Id>S</Id>"
    "<Acct><Id><IBAN>DE89370400440532013000</IBAN></Id></Acct>"
    "</Stmt></BkToCstmrStmt></Document>"
)

_V08_UNSTRUCTURED_ADDRESS = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.08">'
    "<BkToCstmrStmt><GrpHdr><MsgId>M</MsgId>"
    "<CreDtTm>2026-06-21T10:00:00</CreDtTm></GrpHdr>"
    "<Stmt><Id>S</Id>"
    "<Acct><Id><IBAN>DE89370400440532013000</IBAN></Id></Acct>"
    "<Ntry><NtryDtls><TxDtls><RltdPties><Cdtr>"
    "<PstlAdr><AdrLine>Line only</AdrLine></PstlAdr>"
    "</Cdtr></RltdPties></TxDtls></NtryDtls></Ntry>"
    "</Stmt></BkToCstmrStmt></Document>"
)


def test_check_cbpr_readiness_clean_v08_returns_ready_report():
    """A clean v08 payload returns cbpr_ready=True with no error issues."""
    result = server.check_cbpr_readiness(_V08_CLEAN)
    assert result["cbpr_ready"] is True
    assert result["schema_version"] == "camt.053.001.08"
    assert result["cutover_date"] == "2026-11-16"
    assert all(issue["severity"] != "error" for issue in result["issues"])


def test_check_cbpr_readiness_unstructured_address_fails():
    """An AdrLine without TwnNm + Ctry trips cbpr_ready=False."""
    result = server.check_cbpr_readiness(_V08_UNSTRUCTURED_ADDRESS)
    assert result["cbpr_ready"] is False
    assert result["summary"]["unstructured_only"] == 1
    codes = [issue["code"] for issue in result["issues"]]
    assert "UNSTRUCTURED_ONLY_ADDRESS" in codes


def test_check_cbpr_readiness_malformed_xml_returns_error_envelope():
    """Malformed XML surfaces as an {"error": ...} dict, not an exception."""
    result = server.check_cbpr_readiness("<Document>unclosed")
    assert "error" in result
    assert "well-formed" in result["error"].lower()


def test_get_cbpr_cutover_date_returns_iso_date():
    """The cutover-date tool returns the canonical Nov 2026 date."""
    result = server.get_cbpr_cutover_date()
    assert result == {"cutover_date": "2026-11-16"}
