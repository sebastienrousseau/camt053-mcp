#!/usr/bin/env python3
"""Example: ``get_input_schema``.

Returns the JSON Schema describing the flat input record for a
message type. Useful for client-side form generation.

Usage::

    python examples/04_get_input_schema.py
"""

from camt053_mcp.server import get_input_schema

MESSAGE_TYPE = "camt.053.001.14"


def main() -> None:
    schema = get_input_schema(MESSAGE_TYPE)
    print(f"Input schema for {MESSAGE_TYPE}:")
    print(f"  $id        : {schema.get('$id')}")
    print(f"  type       : {schema.get('type')}")
    print(f"  required   : {len(schema.get('required', []))} fields")
    print(f"  properties : {len(schema.get('properties', {}))} fields total")


if __name__ == "__main__":
    main()
