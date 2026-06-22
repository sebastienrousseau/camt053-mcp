#!/usr/bin/env python3
"""Example: ``list_message_types``.

Lists every camt.05x message type the server supports. No input needed.

Usage::

    python examples/01_list_message_types.py
"""

from camt053_mcp.server import list_message_types


def main() -> None:
    types = list_message_types()
    print(f"Supported camt.05x message types ({len(types)}):")
    for t in types:
        print(f"  {t['message_type']:<22}  {t['name']}")


if __name__ == "__main__":
    main()
