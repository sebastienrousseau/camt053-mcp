#!/usr/bin/env python3
"""Example: ``check_cbpr_readiness``.

Checks a camt.053 statement against the CBPR+ Nov 14-16 2026 cliff
rules. Flags content that will be rejected after cutover (unstructured-
only postal addresses, MT-style FIN fields, etc.).

Usage::

    python examples/09_check_cbpr_readiness.py
"""

from pathlib import Path

from camt053_mcp.server import check_cbpr_readiness


def main() -> None:
    xml = (Path(__file__).parent / "_data" / "sample_statement.xml").read_text()
    report = check_cbpr_readiness(xml)
    print(f"ready    : {report.get('ready')}")
    print(f"findings : {len(report.get('findings', []))}")
    for f in report.get("findings", [])[:5]:
        print(f"  - [{f.get('severity', 'info')}] {f.get('message', f)}")


if __name__ == "__main__":
    main()
