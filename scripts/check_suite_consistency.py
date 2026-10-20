#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Sebastien Rousseau <sebastian.rousseau@gmail.com>
# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Check this package against what PyPI says about it and its suite.

Nothing breaks when a version slips: a package left a release behind
still installs, still imports, still passes its own tests. The only way
to notice is to go and look, so a scheduled job runs this and fails.

What it holds ``camt053-mcp`` to:

* **The tree is what was released.** A version bumped in the tree and
  never tagged looks exactly like a finished release and nothing else
  notices. Three repositories in the wider suite have done this, each
  time stranding a ``cryptography`` advisory floor that reached nobody.
* **The server never trails the core.** ``camt053-mcp`` may ship ahead of
  ``camt053`` -- a security floor on the MCP SDK cannot wait for five
  other repositories to release -- but a server older than the core it
  wraps is a release that was skipped.
* **The declared ``camt053`` floor exists.** A floor above the newest
  published core makes the package uninstallable.

How far the other members sit from the core is printed as a note and
does not fail this job: that is the core repository's policy to enforce,
in its own Suite Consistency workflow.

Usage:
    python3 scripts/check_suite_consistency.py
    python3 scripts/check_suite_consistency.py --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised only on the 3.10 floor
    import tomli as tomllib

ROOT = Path(__file__).resolve().parent.parent

#: The core library every member wraps.
CORE = "camt053"

#: This repository's distribution.
SELF = "camt053-mcp"

#: Every published member of the camt053 suite. The core is first.
MEMBERS = (
    CORE,
    "camt053-lsp",
    SELF,
    "camt053-loader-mt940",
    "camt053-loader-mt942",
    "camt053-writer-xlsx",
)

TIMEOUT = 30

Published = dict[str, "str | None"]


def published_version(distribution: str) -> str | None:
    """The newest version of ``distribution`` on PyPI, or None."""
    url = f"https://pypi.org/pypi/{distribution}/json"
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as response:
            return str(json.load(response)["info"]["version"])
    except (urllib.error.URLError, KeyError, ValueError, TimeoutError):
        return None


def _pyproject() -> dict[str, Any]:
    """This checkout's ``pyproject.toml``."""
    with (ROOT / "pyproject.toml").open("rb") as handle:
        data: dict[str, Any] = tomllib.load(handle)
    return data


def tree_version(data: dict[str, Any]) -> str:
    """The version this checkout declares."""
    poetry = data.get("tool", {}).get("poetry", {})
    return str(poetry.get("version") or data["project"]["version"])


def core_floor(data: dict[str, Any]) -> str | None:
    """The lowest ``camt053`` this checkout accepts, if it declares one."""
    deps = data.get("tool", {}).get("poetry", {}).get("dependencies", {})
    spec = deps.get(CORE)
    if isinstance(spec, dict):
        spec = spec.get("version")
    match = re.search(r">=\s*([0-9][0-9.]*)", str(spec or ""))
    return match.group(1) if match else None


def as_tuple(version: str) -> tuple[int, ...]:
    """``0.0.20`` as ``(0, 0, 20)``; stops at the first non-digit part."""
    parts: list[int] = []
    for chunk in version.split("."):
        if not chunk.isdigit():
            break
        parts.append(int(chunk))
    return tuple(parts)


def _unreadable(published: Published) -> list[str]:
    """One problem per member PyPI did not answer for."""
    return [
        f"{name}: could not read PyPI"
        for name in (CORE, SELF)
        if published.get(name) is None
    ]


def _against_core(own: str, core: str, floor: str | None) -> list[str]:
    """Problems with where this package sits relative to the core."""
    problems: list[str] = []
    if as_tuple(own) < as_tuple(core):
        problems.append(
            f"{SELF}: published {own} trails the core {CORE} {core}. "
            f"Release it."
        )
    if floor and as_tuple(floor) > as_tuple(core):
        problems.append(
            f"{SELF} requires {CORE}>={floor}, but the newest published "
            f"{CORE} is {core}. Nobody can install this combination."
        )
    return problems


def _notes(core: str, published: Published) -> list[str]:
    """Members whose published version differs from the core's."""
    return [
        f"{name}: published {version}, the core is at {core}"
        for name, version in published.items()
        if version and name != CORE and version != core
    ]


def check(
    tree: str, floor: str | None, published: Published
) -> tuple[list[str], list[str]]:
    """Return ``(problems, notes)`` for this tree against ``published``."""
    problems = _unreadable(published)
    own, core = published.get(SELF), published.get(CORE)
    if own and own != tree:
        problems.append(
            f"{SELF}: tree is {tree} but PyPI has {own}. Either the "
            f"release was never tagged, or the tree is behind."
        )
    if not (own and core):
        return problems, []
    return problems + _against_core(own, core, floor), _notes(core, published)


def _print_text(report: dict[str, Any]) -> None:
    """Render ``report`` for a person reading the job log."""
    print(f"tree version: {report['tree']}")
    for name, version in report["published"].items():
        print(f"  {name:34} {version}")
    if report["notes"]:
        print("\nnotes (not failures):")
        print("\n".join(f"  - {item}" for item in report["notes"]))
    if report["problems"]:
        print("\nproblems:")
        print("\n".join(f"  - {item}" for item in report["problems"]))
    else:
        print(f"\n{SELF} agrees with what is published")


def main(argv: list[str] | None = None) -> int:
    """Run the check and report; non-zero when there is a problem."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(argv)

    data = _pyproject()
    tree = tree_version(data)
    published: Published = {name: published_version(name) for name in MEMBERS}
    problems, notes = check(tree, core_floor(data), published)
    report: dict[str, Any] = {
        "tree": tree,
        "published": published,
        "problems": problems,
        "notes": notes,
    }
    if args.json:
        json.dump(report, sys.stdout, indent=1)
        print()
    else:
        _print_text(report)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
