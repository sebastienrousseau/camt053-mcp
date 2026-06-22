#!/usr/bin/env python3
"""Example: ``list_return_reasons``.

Lists every ISO external return-reason code the server knows about.

Usage::

    python examples/02_list_return_reasons.py
"""

from camt053_mcp.server import list_return_reasons


def main() -> None:
    reasons = list_return_reasons()
    print(f"ISO external return reasons ({len(reasons)}):")
    for r in reasons[:10]:  # first ten only for terminal sanity
        print(f"  {r['code']:<6}  {r['name']}")
    if len(reasons) > 10:
        print(f"  ... and {len(reasons) - 10} more")


if __name__ == "__main__":
    main()
