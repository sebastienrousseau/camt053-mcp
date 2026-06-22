#!/usr/bin/env python3
"""Example: ``list_entries``.

Lists every entry across all of a statement's statements. Optional
``offset`` + ``limit`` for pagination — the call below shows both
modes (full list, then a single-page envelope).

Usage::

    python examples/11_list_entries.py
"""

from pathlib import Path

from camt053_mcp.server import list_entries


def main() -> None:
    xml = (Path(__file__).parent / "_data" / "sample_statement.xml").read_text()

    full = list_entries(xml)
    print(f"Full list: {len(full)} entries")
    for e in full[:3]:
        print(
            f"  {(e.get('reference') or '-'):<12} "
            f"{e.get('amount')} {e.get('currency')}"
        )

    page = list_entries(xml, offset=0, limit=1)
    print(
        f"\nPaginated: total={page.get('total')} "
        f"offset={page.get('offset')} limit={page.get('limit')} "
        f"returned={len(page.get('entries', []))}"
    )


if __name__ == "__main__":
    main()
