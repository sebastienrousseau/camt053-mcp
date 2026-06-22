#!/usr/bin/env python3
"""Example: ``validate_records``.

Validates a list of flat records against a message type's input JSON
Schema. Returns ``{"ok": bool, "errors": [...]}``.

Usage::

    python examples/05_validate_records.py
"""

from camt053_mcp.server import validate_records

MESSAGE_TYPE = "camt.053.001.14"

# Two records: one valid, one missing required fields. Shape is the flat
# "input record" form documented by get_input_schema (example 04).
RECORDS = [
    {
        "statement_msg_id": "STMT-MSG-0001",
        "statement_id": "STMT-0001",
        "account_id": "GB29NWBK60161331926819",
        "entry_ref": "NTRY-0001",
        "amount": "1500.00",
        "currency": "EUR",
        "credit_debit": "CRDT",
        "booking_date": "2026-06-15",
        "value_date": "2026-06-15",
    },
    {"entry_ref": "NTRY-BROKEN"},  # missing the rest
]


def main() -> None:
    result = validate_records(MESSAGE_TYPE, RECORDS)
    print(f"ok       : {result.get('ok')}")
    print(f"errors   : {len(result.get('errors', []))}")
    for err in result.get("errors", [])[:5]:
        print(f"  - {err}")


if __name__ == "__main__":
    main()
