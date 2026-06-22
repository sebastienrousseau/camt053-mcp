#!/usr/bin/env python3
"""Example: ``cite_rulebook`` + ``list_rulebook_clauses``.

Quote a payments-rulebook rule by id. The registry covers SEPA, CBPR+,
and HVPS+ at a curated-summary level; ``source_url`` always points at
the authoritative scheme document for verification.

Usage::

    python examples/14_cite_rulebook.py
"""

from camt053_mcp.server import cite_rulebook, list_rulebook_clauses


def main() -> None:
    print("== Available rulebook citations ==")
    for entry in list_rulebook_clauses():
        print(
            f"  {entry['scheme']:<6} {entry['version']:<6} "
            f"{entry['clause']:<38} {entry['title']}"
        )

    print()
    print("== Cite the structured-address mandate ==")
    citation = cite_rulebook(
        "CBPR+", "2026", "structured-address-mandate-nov-2026"
    )
    print(f"  title    : {citation['title']}")
    print(f"  summary  : {citation['summary'][:160]}...")
    print(f"  source   : {citation['source_url']}")
    print(f"  as_of    : {citation['as_of']}")

    print()
    print("== Unknown clause yields an error envelope ==")
    print(f"  {cite_rulebook('SEPA', '2025', 'no-such-clause')['error']}")


if __name__ == "__main__":
    main()
