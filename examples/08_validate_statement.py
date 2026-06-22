#!/usr/bin/env python3
"""Example: ``validate_statement``.

Validates an incoming camt.05x statement against its XSD schema.

Usage::

    python examples/08_validate_statement.py
"""

from pathlib import Path

from camt053_mcp.server import validate_statement


def main() -> None:
    xml = (Path(__file__).parent / "_data" / "sample_statement.xml").read_text()
    result = validate_statement(xml)
    print(f"ok           : {result.get('ok')}")
    print(f"message_type : {result.get('message_type')}")
    for err in result.get("errors", []):
        print(f"  - {err}")


if __name__ == "__main__":
    main()
