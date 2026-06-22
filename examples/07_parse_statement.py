#!/usr/bin/env python3
"""Example: ``parse_statement``.

Parses a camt.05x XML statement into plain Python data.

Usage::

    python examples/07_parse_statement.py
"""

from pathlib import Path

from camt053_mcp.server import parse_statement


def main() -> None:
    xml = (Path(__file__).parent / "_data" / "sample_statement.xml").read_text()
    parsed = parse_statement(xml)
    print(f"message_type : {parsed.get('message_type')}")
    print(f"statements   : {len(parsed.get('statements', []))}")
    for s in parsed.get("statements", []):
        print(
            f"  Stmt {s.get('id')} "
            f"({s.get('account_iban')}) "
            f"{len(s.get('entries', []))} entries"
        )


if __name__ == "__main__":
    main()
