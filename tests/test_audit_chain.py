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

"""Tests for the unified, tamper-evident audit chain.

Covers, per the workstream's acceptance criteria:

* the chain verifies end-to-end with the core library's
  ``verify_chain`` under the ``CAMT053_AUDIT_HMAC_KEY`` secret;
* a tampered record breaks verification;
* the core library's redaction is applied to tool arguments (lists
  and nested mappings included) and long payloads are truncated;
* disabled mode (key unset) logs plain attribution records exactly
  as before;
* every ``tool.invoked`` record links session id, request id, tool,
  tenant, redacted arguments, and outcome.
"""

import asyncio
import json
import logging
from types import SimpleNamespace

import pytest

pytest.importorskip("mcp")

from camt053.audit import AuditEvent, verify_chain  # noqa: E402

from camt053_mcp import auditing, observability, transport  # noqa: E402

SECRET = "audit-chain-secret"


@pytest.fixture()
def chain_key(monkeypatch):
    """Enable audit chaining with a fresh chain for this test."""
    monkeypatch.setenv(transport.AUDIT_HMAC_KEY_ENV, SECRET)
    monkeypatch.setattr(auditing._chain_state, "chain", None)
    monkeypatch.setattr(auditing._chain_state, "key", None)
    yield SECRET


@pytest.fixture()
def no_chain_key(monkeypatch):
    """Ensure audit chaining is disabled for this test."""
    monkeypatch.delenv(transport.AUDIT_HMAC_KEY_ENV, raising=False)
    monkeypatch.setattr(auditing._chain_state, "chain", None)
    monkeypatch.setattr(auditing._chain_state, "key", None)


def _events_from(caplog):
    """Parse each audit log line back into a dict."""
    return [json.loads(record.getMessage()) for record in caplog.records]


def _as_chain(events):
    """Rebuild core AuditEvent instances from logged chain dicts."""
    return [AuditEvent(**event) for event in events]


# ─── chaining on/off ─────────────────────────────────────────────────────────


def test_disabled_mode_logs_plain_records(no_chain_key, caplog):
    """Without the key, audit_event behaves exactly as before."""
    with caplog.at_level(logging.INFO, logger="camt053_mcp.audit"):
        record = transport.audit_event("http.request.authorized", "t-1")
    assert record["service"] == "camt053-mcp"
    assert "hmac" not in record
    assert "sequence" not in record
    assert _events_from(caplog) == [record]


def test_chain_verifies_end_to_end(chain_key, caplog):
    """Chained records reconstruct into a chain verify_chain accepts."""
    with caplog.at_level(logging.INFO, logger="camt053_mcp.audit"):
        transport.audit_event("http.request.authorized", "t-1", path="/mcp")
        transport.audit_event("tool.invoked", "t-1", tool="list_entries")
        transport.audit_event("http.request.rejected", None, reason="x")
    events = _events_from(caplog)
    assert [event["sequence"] for event in events] == [0, 1, 2]
    assert events[0]["prev_hash"] == ""
    assert events[1]["prev_hash"] == events[0]["hmac"]
    assert events[0]["payload"]["scope"] == "t-1"
    verdict = verify_chain(_as_chain(events), SECRET.encode("utf-8"))
    assert verdict.valid is True


def test_tampered_record_breaks_verification(chain_key, caplog):
    """Editing any logged field is detected by verify_chain."""
    with caplog.at_level(logging.INFO, logger="camt053_mcp.audit"):
        transport.audit_event("tool.invoked", "t-1", tool="parse_statement")
        transport.audit_event("tool.invoked", "t-2", tool="list_entries")
    events = _events_from(caplog)
    events[0]["payload"]["scope"] = "attacker"  # rewrite history
    verdict = verify_chain(_as_chain(events), SECRET.encode("utf-8"))
    assert verdict.valid is False
    assert verdict.broken_at == 0
    assert verdict.reason == "HMAC_MISMATCH"


def test_wrong_secret_breaks_verification(chain_key, caplog):
    """The chain only verifies under the producing secret."""
    with caplog.at_level(logging.INFO, logger="camt053_mcp.audit"):
        transport.audit_event("tool.invoked", "t-1", tool="list_entries")
    verdict = verify_chain(_as_chain(_events_from(caplog)), b"not-the-secret")
    assert verdict.valid is False


def test_key_rotation_starts_a_fresh_chain(chain_key, monkeypatch):
    """Changing the key abandons the old chain at sequence zero."""
    first = transport.audit_event("http.server.starting", None)
    assert first["sequence"] == 0
    second = transport.audit_event("http.server.starting", None)
    assert second["sequence"] == 1
    monkeypatch.setenv(transport.AUDIT_HMAC_KEY_ENV, "rotated-secret")
    third = transport.audit_event("http.server.starting", None)
    assert third["sequence"] == 0
    assert third["prev_hash"] == ""


def test_unsetting_key_disables_chaining_again(chain_key, monkeypatch):
    """Removing the key mid-process returns to plain records."""
    chained = transport.audit_event("http.server.starting", None)
    assert "hmac" in chained
    monkeypatch.delenv(transport.AUDIT_HMAC_KEY_ENV)
    plain = transport.audit_event("http.server.starting", None)
    assert "hmac" not in plain
    assert auditing._chain_state.chain is None


# ─── redaction and truncation ────────────────────────────────────────────────


def test_redacted_arguments_apply_core_rules_recursively():
    """IBANs/names are masked at every nesting level, lists included."""
    prepared = transport.redacted_arguments(
        {
            "xml": "<Document/>",
            "records": [
                {
                    "iban": "GB29NWBK60161331926819",
                    "name": "Acme Treasury Ltd",
                    "reference": "REF-1",
                }
            ],
            "creditor": {"iban": "DE89370400440532013000"},
        }
    )
    record = prepared["records"][0]
    assert record["iban"].endswith("6819")
    assert "GB29NWBK" not in record["iban"]
    assert record["name"] != "Acme Treasury Ltd"
    assert record["reference"] == "REF-1"  # non-sensitive: untouched
    assert "DE8937" not in prepared["creditor"]["iban"]
    assert prepared["xml"] == "<Document/>"


def test_redacted_arguments_truncate_long_payloads():
    """Statement-sized strings are bounded with an explicit marker."""
    xml = "<Document>" + "x" * 5000 + "</Document>"
    prepared = transport.redacted_arguments({"xml": xml})
    assert len(prepared["xml"]) < 320
    assert "...[truncated" in prepared["xml"]


def test_redacted_arguments_are_json_safe():
    """Non-serialisable leaves are coerced so chaining never fails."""
    prepared = transport.redacted_arguments({"weird": object()})
    assert isinstance(prepared["weird"], str)
    json.dumps(prepared)  # must not raise


# ─── audit_tool_invocation ───────────────────────────────────────────────────


def _ctx(headers=None, request_id="req-9"):
    """A fake FastMCP Context carrying an HTTP request."""
    request = SimpleNamespace(headers=headers) if headers is not None else None
    return SimpleNamespace(
        request_context=SimpleNamespace(request=request, request_id=request_id)
    )


def test_tool_invocation_links_session_tool_args_and_outcome(
    no_chain_key, caplog
):
    """The tool.invoked record carries the full linkage fields."""
    ctx = _ctx(
        headers={
            "mcp-session-id": "sess-1234",
            transport.TENANT_HEADER: "acme-treasury",
        }
    )
    with caplog.at_level(logging.INFO, logger="camt053_mcp.audit"):
        record = transport.audit_tool_invocation(
            "validate_identifier",
            {"kind": "iban", "value": "GB29NWBK60161331926819"},
            ctx,
            "success",
        )
    assert record["event"] == "tool.invoked"
    assert record["tool"] == "validate_identifier"
    assert record["session"] == "sess-1234"
    assert record["request_id"] == "req-9"
    assert record["scope"] == "acme-treasury"
    assert record["outcome"] == "success"
    # 'value' is not a sensitive key name; 'kind' is untouched too.
    assert record["arguments"]["kind"] == "iban"
    assert _events_from(caplog)[-1] == record


def test_tool_invocation_redacts_sensitive_arguments(no_chain_key):
    """Sensitive argument names are redacted in the audit record."""
    record = transport.audit_tool_invocation(
        "validate_records",
        {"records": [{"iban": "GB29NWBK60161331926819"}]},
        None,
        "success",
    )
    assert "GB29NWBK" not in json.dumps(record)


def test_tool_invocation_without_context_is_unscoped(no_chain_key):
    """ctx=None (in-process use) logs '-' placeholders, no crash."""
    record = transport.audit_tool_invocation("list_entries", {}, None, "error")
    assert record["session"] == "-"
    assert record["request_id"] == "-"
    assert record["scope"] == "-"


def test_tool_invocation_outside_request_context(no_chain_key):
    """A Context that raises outside a request degrades gracefully."""

    class _Detached:
        """Mimics the SDK Context outside a request."""

        @property
        def request_context(self):
            """Raise like the SDK does outside a request."""
            raise ValueError("Context is not available outside of a request")

    record = transport.audit_tool_invocation(
        "list_entries", {}, _Detached(), "success"
    )
    assert record["session"] == "-"


def test_tool_invocation_with_request_context_none(no_chain_key):
    """A context whose request_context is None degrades gracefully."""
    ctx = SimpleNamespace(request_context=None)
    record = transport.audit_tool_invocation("x", {}, ctx, "success")
    assert record["session"] == "-"


def test_tool_invocation_http_request_without_session_header(no_chain_key):
    """An HTTP request lacking mcp-session-id yields the placeholder."""
    record = transport.audit_tool_invocation(
        "x", {}, _ctx(headers={}), "success"
    )
    assert record["session"] == "-"
    assert record["request_id"] == "req-9"


def test_tool_invocation_context_without_request(no_chain_key):
    """A stdio-shaped context (no HTTP request) still logs request_id."""
    ctx = _ctx(headers=None, request_id=42)
    record = transport.audit_tool_invocation("x", {}, ctx, "success")
    assert record["session"] == "-"
    assert record["request_id"] == "42"


def test_tool_invocation_is_chained_when_key_set(chain_key, caplog):
    """tool.invoked records participate in the HMAC chain."""
    with caplog.at_level(logging.INFO, logger="camt053_mcp.audit"):
        transport.audit_tool_invocation(
            "list_entries", {"xml": "<Document/>"}, None, "success"
        )
    events = _events_from(caplog)
    assert events[0]["event_type"] == "tool.invoked"
    assert events[0]["payload"]["tool"] == "list_entries"
    verdict = verify_chain(_as_chain(events), SECRET.encode("utf-8"))
    assert verdict.valid is True


# ─── dispatcher integration ──────────────────────────────────────────────────


def test_instrumented_dispatch_emits_tool_invoked_audit(no_chain_key, caplog):
    """The metrics wrapper also writes the tool.invoked audit line."""

    class _Manager:
        """Fake ToolManager returning an error envelope."""

        async def call_tool(
            self, name, arguments, context=None, convert_result=False
        ):
            """Return a scripted error envelope."""
            return {"error": "nope"}

    server = SimpleNamespace(_tool_manager=_Manager())
    assert observability.instrument_tools(server) is True
    with caplog.at_level(logging.INFO, logger="camt053_mcp.audit"):
        asyncio.run(
            server._tool_manager.call_tool("probe_audit", {"kind": "bic"})
        )
    events = _events_from(caplog)
    assert events[-1]["event"] == "tool.invoked"
    assert events[-1]["tool"] == "probe_audit"
    assert events[-1]["outcome"] == "error"
    assert events[-1]["arguments"] == {"kind": "bic"}
