#!/usr/bin/env python3
"""Example: ``validate_identifier``.

Validates a single financial identifier — IBAN, BIC, or LEI — using
the dedicated checkers.

Usage::

    python examples/06_validate_identifier.py
"""

from camt053_mcp.server import validate_identifier

CASES = [
    ("iban", "GB29NWBK60161331926819"),  # valid
    ("iban", "GB00BAD0000000000000000"),  # invalid checksum
    ("bic", "NWBKGB2LXXX"),               # valid 11-char
    ("lei", "529900T8BM49AURSDO55"),      # valid (DTCC)
]


def main() -> None:
    for kind, value in CASES:
        result = validate_identifier(kind, value)
        status = "ok" if result.get("ok") else f"bad ({result.get('error')})"
        print(f"  {kind:<5} {value:<25} -> {status}")


if __name__ == "__main__":
    main()
