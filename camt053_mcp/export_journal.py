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

"""Convert parsed camt.053 entries to accounting-platform journal entries.

Backs the ``export_journal`` MCP tool. Currently supports two targets:

* **Xero** — produces a list of ``BankTransactions`` payloads ready to
  POST to ``https://api.xero.com/api.xro/2.0/BankTransactions``.
* **QBO** (QuickBooks Online) — produces a list of ``JournalEntry``
  payloads ready to POST to ``/v3/company/{realmId}/journalentry``.

For each target the schema follows the public REST API documentation.
Operator-specific values (account codes, contact identifiers,
realm IDs) appear as ``OPERATOR_FILL`` placeholders so the agent can
report what the operator still needs to wire up.

NetSuite + SAP S/4HANA targets are tracked in #17 (D5 stretch); this
module's :data:`SUPPORTED_TARGETS` set lists what ships today.
"""

from __future__ import annotations

from typing import Any

from camt053 import services
from camt053.exceptions import Camt053Error

#: The accounting targets this module knows how to export to.
SUPPORTED_TARGETS = frozenset({"xero", "qbo"})

#: Sentinel placeholder for operator-specific values the converter
#: cannot derive from the statement payload alone (account codes,
#: contact identifiers, realm IDs). The MCP tool's response includes
#: the count of placeholders so the operator knows how much wiring
#: remains.
OPERATOR_FILL = "OPERATOR_FILL"


# ---------------------------------------------------------------------------
# Xero
# ---------------------------------------------------------------------------


def _xero_bank_transaction(entry: dict[str, Any]) -> dict[str, Any]:
    """Map one statement entry to a Xero ``BankTransactions`` payload."""
    cd = entry.get("credit_debit_indicator") or "CRDT"
    xero_type = "RECEIVE" if cd == "CRDT" else "SPEND"

    amount = entry.get("amount") or "0.00"
    currency = entry.get("currency") or OPERATOR_FILL

    contact_name = (
        entry.get("counterparty_name")
        or entry.get("counterparty_account")
        or OPERATOR_FILL
    )

    description = (
        entry.get("remittance_information_unstructured")
        or entry.get("additional_entry_information")
        or entry.get("reference")
        or "camt.053 booked entry"
    )

    return {
        "Type": xero_type,
        "Reference": entry.get("reference") or OPERATOR_FILL,
        "Date": entry.get("booking_date")
        or entry.get("value_date")
        or OPERATOR_FILL,
        "BankAccount": {"Code": OPERATOR_FILL},  # operator's Xero bank code
        "Contact": {"Name": contact_name},
        "LineAmountTypes": "NoTax",
        "CurrencyCode": currency,
        "LineItems": [
            {
                "Description": description,
                "Quantity": "1",
                "UnitAmount": str(amount),
                "AccountCode": OPERATOR_FILL,  # operator's Xero P&L account
            }
        ],
    }


def export_for_xero(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return a list of Xero ``BankTransactions`` payloads.

    Each payload is ready to wrap in
    ``{"BankTransactions": [...]}`` and POST to the Xero REST API.
    """
    return [_xero_bank_transaction(e) for e in entries]


# ---------------------------------------------------------------------------
# QuickBooks Online
# ---------------------------------------------------------------------------


def _qbo_journal_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Map one statement entry to a QBO ``JournalEntry`` payload.

    Produces a balanced two-line journal: one line to the bank account
    (debit on credit-entries, credit on debit-entries) and a mirrored
    line to a clearing / income account. Both ``AccountRef.value``
    fields are ``OPERATOR_FILL`` for the operator to substitute.
    """
    cd = entry.get("credit_debit_indicator") or "CRDT"
    amount = entry.get("amount") or "0.00"

    if cd == "CRDT":
        bank_posting = "Debit"
        offset_posting = "Credit"
    else:
        bank_posting = "Credit"
        offset_posting = "Debit"

    description = (
        entry.get("remittance_information_unstructured")
        or entry.get("additional_entry_information")
        or "camt.053 booked entry"
    )

    line_bank = {
        "DetailType": "JournalEntryLineDetail",
        "Amount": str(amount),
        "Description": description,
        "JournalEntryLineDetail": {
            "PostingType": bank_posting,
            "AccountRef": {"value": OPERATOR_FILL, "name": "Bank"},
        },
    }
    line_offset = {
        "DetailType": "JournalEntryLineDetail",
        "Amount": str(amount),
        "Description": description,
        "JournalEntryLineDetail": {
            "PostingType": offset_posting,
            "AccountRef": {"value": OPERATOR_FILL, "name": "Offset"},
        },
    }

    return {
        "TxnDate": entry.get("booking_date")
        or entry.get("value_date")
        or OPERATOR_FILL,
        "DocNumber": entry.get("reference") or OPERATOR_FILL,
        "Line": [line_bank, line_offset],
        "PrivateNote": (
            f"camt.053 entry {entry.get('reference') or '(no ref)'}; "
            f"amount {amount} {entry.get('currency') or '?'}; "
            f"value date {entry.get('value_date') or '?'}"
        ),
    }


def export_for_qbo(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return a list of QBO ``JournalEntry`` payloads."""
    return [_qbo_journal_entry(e) for e in entries]


# ---------------------------------------------------------------------------
# Public entry point used by the MCP tool
# ---------------------------------------------------------------------------


_EXPORTERS = {
    "xero": export_for_xero,
    "qbo": export_for_qbo,
}


def export(xml: str, target: str) -> dict[str, Any]:
    """Parse ``xml`` and return target-shaped journal entries.

    Returns ``{"target": str, "entries": [...], "placeholder_count": int,
    "placeholder_field": str}`` on success or ``{"error": ...}``.

    The ``placeholder_count`` field counts every ``OPERATOR_FILL`` token
    in the returned payload so the agent can tell the operator exactly
    how many wiring decisions remain (account codes, contact IDs).
    """
    if target not in SUPPORTED_TARGETS:
        return {
            "error": (
                f"unsupported target {target!r}. "
                f"Supported targets: {sorted(SUPPORTED_TARGETS)}. "
                "NetSuite + SAP S/4HANA are tracked in #17."
            )
        }

    try:
        entries = services.list_entries(xml)
    except (ValueError, Camt053Error) as exc:
        return {"error": str(exc)}

    if entries and "error" in entries[0]:
        return {"error": entries[0]["error"]}

    exported = _EXPORTERS[target](entries)
    placeholder_count = _count_placeholders(exported)

    return {
        "target": target,
        "entries": exported,
        "placeholder_count": placeholder_count,
        "placeholder_field": OPERATOR_FILL,
    }


def _count_placeholders(payload: Any) -> int:
    """Recursively count ``OPERATOR_FILL`` tokens in a JSON-like structure."""
    if isinstance(payload, str):
        return 1 if payload == OPERATOR_FILL else 0
    if isinstance(payload, dict):
        return sum(_count_placeholders(v) for v in payload.values())
    if isinstance(payload, list):
        return sum(_count_placeholders(v) for v in payload)
    return 0
