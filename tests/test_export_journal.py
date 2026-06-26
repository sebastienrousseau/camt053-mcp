"""Tests for the export_journal converter + MCP tool (D5 in #17)."""

from __future__ import annotations

from camt053_mcp import export_journal
from camt053_mcp.export_journal import OPERATOR_FILL
from camt053_mcp.server import (
    export_journal as export_journal_tool,
)
from camt053_mcp.server import (
    list_export_journal_targets,
)

SAMPLE_ENTRIES_CRDT = [
    {
        "reference": "NTRY-0001",
        "amount": "1500.00",
        "currency": "EUR",
        "credit_debit_indicator": "CRDT",
        "booking_date": "2026-06-15",
        "value_date": "2026-06-15",
        "counterparty_name": "Globex SA",
        "counterparty_account": "DE89370400440532013000",
        "remittance_information_unstructured": "Invoice 2026-Q2-417",
    }
]

SAMPLE_ENTRIES_DBIT = [
    {
        "reference": "NTRY-0002",
        "amount": "42.00",
        "currency": "EUR",
        "credit_debit_indicator": "DBIT",
        "booking_date": "2026-06-15",
        "value_date": "2026-06-15",
    }
]


# ---------------------------------------------------------------------------
# Pure converters
# ---------------------------------------------------------------------------


class TestXeroConverter:
    """The Xero BankTransactions converter."""

    def test_credit_entry_becomes_receive(self) -> None:
        out = export_journal.export_for_xero(SAMPLE_ENTRIES_CRDT)
        assert len(out) == 1
        assert out[0]["Type"] == "RECEIVE"
        assert out[0]["Reference"] == "NTRY-0001"
        assert out[0]["Date"] == "2026-06-15"
        assert out[0]["CurrencyCode"] == "EUR"
        assert out[0]["Contact"]["Name"] == "Globex SA"
        assert out[0]["LineItems"][0]["UnitAmount"] == "1500.00"

    def test_debit_entry_becomes_spend(self) -> None:
        out = export_journal.export_for_xero(SAMPLE_ENTRIES_DBIT)
        assert out[0]["Type"] == "SPEND"

    def test_missing_fields_become_operator_fill(self) -> None:
        out = export_journal.export_for_xero([{"reference": "X"}])
        # bank account, account code, contact, currency all unspecified
        assert out[0]["BankAccount"]["Code"] == OPERATOR_FILL
        assert out[0]["LineItems"][0]["AccountCode"] == OPERATOR_FILL
        assert out[0]["Contact"]["Name"] == OPERATOR_FILL
        assert out[0]["CurrencyCode"] == OPERATOR_FILL

    def test_uses_value_date_when_booking_date_missing(self) -> None:
        out = export_journal.export_for_xero([{"value_date": "2026-06-15"}])
        assert out[0]["Date"] == "2026-06-15"

    def test_description_falls_back_through_chain(self) -> None:
        entry = {
            "additional_entry_information": "additional info text",
        }
        out = export_journal.export_for_xero([entry])
        assert out[0]["LineItems"][0]["Description"] == "additional info text"


class TestQBOConverter:
    """The QuickBooks Online JournalEntry converter."""

    def test_credit_entry_debits_bank(self) -> None:
        out = export_journal.export_for_qbo(SAMPLE_ENTRIES_CRDT)
        assert len(out) == 1
        bank_line = out[0]["Line"][0]
        offset_line = out[0]["Line"][1]
        assert bank_line["JournalEntryLineDetail"]["PostingType"] == "Debit"
        assert offset_line["JournalEntryLineDetail"]["PostingType"] == "Credit"
        assert bank_line["Amount"] == "1500.00"
        assert offset_line["Amount"] == "1500.00"

    def test_debit_entry_credits_bank(self) -> None:
        out = export_journal.export_for_qbo(SAMPLE_ENTRIES_DBIT)
        assert (
            out[0]["Line"][0]["JournalEntryLineDetail"]["PostingType"]
            == "Credit"
        )
        assert (
            out[0]["Line"][1]["JournalEntryLineDetail"]["PostingType"]
            == "Debit"
        )

    def test_balanced_lines(self) -> None:
        """Every QBO journal must have balanced debit + credit lines."""
        out = export_journal.export_for_qbo(SAMPLE_ENTRIES_CRDT)
        lines = out[0]["Line"]
        debits = sum(
            float(line["Amount"])
            for line in lines
            if line["JournalEntryLineDetail"]["PostingType"] == "Debit"
        )
        credits = sum(
            float(line["Amount"])
            for line in lines
            if line["JournalEntryLineDetail"]["PostingType"] == "Credit"
        )
        assert debits == credits

    def test_private_note_carries_metadata(self) -> None:
        out = export_journal.export_for_qbo(SAMPLE_ENTRIES_CRDT)
        note = out[0]["PrivateNote"]
        assert "NTRY-0001" in note
        assert "1500.00" in note
        assert "EUR" in note

    def test_missing_amount_defaults(self) -> None:
        out = export_journal.export_for_qbo([{"reference": "X"}])
        assert out[0]["Line"][0]["Amount"] == "0.00"


# ---------------------------------------------------------------------------
# Top-level export() service entry point
# ---------------------------------------------------------------------------


class TestExport:
    """The top-level export() function used by the MCP tool."""

    def test_unsupported_target_returns_error(self) -> None:
        result = export_journal.export("<doc/>", "netsuite")
        assert "error" in result
        assert "netsuite" in result["error"]

    def test_malformed_xml_returns_error(self) -> None:
        result = export_journal.export("not xml", "xero")
        assert "error" in result

    def test_entry_level_error_is_propagated(self, monkeypatch) -> None:
        """An error envelope from ``list_entries`` is surfaced verbatim."""
        monkeypatch.setattr(
            export_journal.services,
            "list_entries",
            lambda xml: [{"error": "boom"}],
        )
        result = export_journal.export("<doc/>", "xero")
        assert result == {"error": "boom"}

    def test_placeholder_count_reflects_real_count(
        self, statement_xml
    ) -> None:
        result = export_journal.export(statement_xml, "xero")
        assert "error" not in result
        assert result["target"] == "xero"
        assert result["placeholder_field"] == OPERATOR_FILL
        assert isinstance(result["placeholder_count"], int)
        assert result["placeholder_count"] >= 0

    def test_qbo_target_returns_journal_entries(self, statement_xml) -> None:
        result = export_journal.export(statement_xml, "qbo")
        assert "error" not in result
        assert result["target"] == "qbo"
        for entry in result["entries"]:
            assert "TxnDate" in entry
            assert "Line" in entry
            assert len(entry["Line"]) == 2


class TestPlaceholderCounter:
    """The recursive placeholder counter."""

    def test_counts_scalar(self) -> None:
        assert export_journal._count_placeholders(OPERATOR_FILL) == 1
        assert export_journal._count_placeholders("other") == 0

    def test_counts_nested(self) -> None:
        payload = {
            "a": OPERATOR_FILL,
            "b": [OPERATOR_FILL, "x"],
            "c": {"d": OPERATOR_FILL, "e": 5},
        }
        assert export_journal._count_placeholders(payload) == 3

    def test_counts_zero(self) -> None:
        assert (
            export_journal._count_placeholders({"a": 1, "b": [None, "x"]}) == 0
        )


# ---------------------------------------------------------------------------
# MCP tool integration
# ---------------------------------------------------------------------------


class TestExportJournalTool:
    """The @server.tool wrapper."""

    def test_default_target_is_xero(self, statement_xml) -> None:
        result = export_journal_tool(statement_xml)
        assert result["target"] == "xero"

    def test_explicit_qbo_target(self, statement_xml) -> None:
        result = export_journal_tool(statement_xml, target="qbo")
        assert result["target"] == "qbo"

    def test_unsupported_target_returns_error_dict(self) -> None:
        result = export_journal_tool("<doc/>", target="sap")
        assert "error" in result


class TestListTargetsTool:
    """The companion list-targets tool."""

    def test_returns_sorted_targets(self) -> None:
        assert list_export_journal_targets() == ["qbo", "xero"]

    def test_matches_module_set(self) -> None:
        assert (
            set(list_export_journal_targets())
            == export_journal.SUPPORTED_TARGETS
        )
