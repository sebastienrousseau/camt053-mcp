#!/usr/bin/env python3
"""Example: ``generate_reversal``.

Reads a statement and emits a validated reversing-entry document for
the entries carrying the given reason code (defaults to AC04 — Closed
Account). The output is XSD-valid camt.053.001.14 XML.

Usage::

    python examples/13_generate_reversal.py
"""

from pathlib import Path

from camt053_mcp.server import generate_reversal


def main() -> None:
    xml = (Path(__file__).parent / "_data" / "sample_statement.xml").read_text()
    reversal_xml = generate_reversal(xml, reason_code="AC04")
    print(f"Reversing-entry XML ({len(reversal_xml)} chars):")
    head = "\n".join(reversal_xml.splitlines()[:8])
    print(head)
    if len(reversal_xml.splitlines()) > 8:
        print("...")


if __name__ == "__main__":
    main()
