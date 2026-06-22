#!/usr/bin/env python3
"""Example: ``get_cbpr_cutover_date``.

Returns the official CBPR+ / Nov 2026 cutover date as ISO 8601.

Usage::

    python examples/10_get_cbpr_cutover_date.py
"""

from camt053_mcp.server import get_cbpr_cutover_date


def main() -> None:
    result = get_cbpr_cutover_date()
    print(f"CBPR+ cutover : {result}")


if __name__ == "__main__":
    main()
