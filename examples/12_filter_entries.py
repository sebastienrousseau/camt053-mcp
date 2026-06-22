#!/usr/bin/env python3
"""Example: ``filter_entries``.

Returns the statement entries carrying a given return reason code.
Useful as the preview step before generating a reversal.

Usage::

    python examples/12_filter_entries.py
"""

from pathlib import Path

from camt053_mcp.server import filter_entries


def main() -> None:
    xml = (Path(__file__).parent / "_data" / "sample_statement.xml").read_text()
    matches = filter_entries(xml, reason_code="AC04")
    print(f"Entries returned AC04: {len(matches)}")
    for e in matches:
        print(
            f"  {(e.get('reference') or '-'):<12} "
            f"{e.get('amount')} {e.get('currency')}  "
            f"reason={e.get('return_reason_code')}"
        )


if __name__ == "__main__":
    main()
