#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Fail when the release version is restated differently anywhere.

Single source of truth: ``version`` in ``pyproject.toml``. The same string
must appear in the package's ``__version__`` (when the package defines
one), as a ``## [X.Y.Z]`` heading in ``CHANGELOG.md``, in ``glama.json``
(``version`` and the Docker tag of ``installation.docker`` when present),
and in ``server.json`` (``version`` and every ``packages[].version``).

The Glama directory and the MCP registry read those two manifests; before
this check they sat several releases behind the package on every server
in the suite. Standard library only, so the workflow needs no install.

Usage: python3 scripts/verify_versions.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _pyproject_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        raise SystemExit("pyproject.toml has no version field")
    return match.group(1)


def _package_version() -> str | None:
    for init in ROOT.glob("*/__init__.py"):
        if init.parent.name.startswith((".", "_", "tests", "docs", "examples")):
            continue
        match = re.search(r'^__version__\s*=\s*"([^"]+)"', init.read_text(encoding="utf-8"), re.MULTILINE)
        if match:
            return match.group(1)
    return None


def _changelog_versions() -> set[str]:
    path = ROOT / "CHANGELOG.md"
    if not path.exists():
        return set()
    return set(re.findall(r"^## \[(\d+\.\d+\.\d+)\]", path.read_text(encoding="utf-8"), re.MULTILINE))


def _json(name: str) -> dict | None:
    path = ROOT / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def main() -> int:
    """Compare every restatement of the version and report."""
    expected = _pyproject_version()
    problems: list[str] = []
    print(f"  pyproject.toml version = {expected}")
    pkg = _package_version()
    if pkg is not None:
        print(f"  __version__            = {pkg}")
        if pkg != expected:
            problems.append("__version__ differs from pyproject.toml")
    changelog = _changelog_versions()
    if changelog and expected not in changelog:
        problems.append(f"CHANGELOG.md has no [{expected}] heading")
    glama = _json("glama.json")
    if glama is not None:
        docker = str((glama.get("installation") or {}).get("docker", ""))
        tag = docker.rsplit(":", 1)[-1] if ":" in docker else expected
        print(f"  glama.json             = {glama.get('version')} (docker tag {tag})")
        if glama.get("version") != expected or tag != expected:
            problems.append("glama.json version or Docker tag differs")
    server = _json("server.json")
    if server is not None:
        pkg_versions = [p.get("version") for p in server.get("packages", [])]
        print(f"  server.json            = {server.get('version')} (packages {pkg_versions})")
        if server.get("version") != expected or any(v != expected for v in pkg_versions):
            problems.append("server.json version differs")
    for problem in problems:
        print(f"ERROR: {problem}")
    if problems:
        return 1
    print("OK: every source agrees on", expected)
    return 0


if __name__ == "__main__":
    sys.exit(main())
