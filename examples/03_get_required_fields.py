#!/usr/bin/env python3
"""Example: ``get_required_fields``.

Returns the required input field names for a given camt message type.

Usage::

    python examples/03_get_required_fields.py
"""

from camt053_mcp.server import get_required_fields

MESSAGE_TYPE = "camt.053.001.14"


def main() -> None:
    fields = get_required_fields(MESSAGE_TYPE)
    print(f"Required fields for {MESSAGE_TYPE}:")
    for f in fields:
        print(f"  - {f}")


if __name__ == "__main__":
    main()
