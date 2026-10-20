# SPDX-FileCopyrightText: 2026 Sebastien Rousseau <sebastian.rousseau@gmail.com>
# SPDX-License-Identifier: Apache-2.0 OR MIT
"""The scheduled suite check holds the camt053 suite to one version.

Every member ships the core's number, in the same release, so a server
published ahead of the core fails exactly like one left behind. The tree
is compared with ``camt053-mcp``'s own release, not the core's. PyPI is
never reached; the published versions are substituted.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parent.parent
    / "scripts"
    / "check_suite_consistency.py"
)

MEMBERS = (
    "camt053",
    "camt053-lsp",
    "camt053-mcp",
    "camt053-loader-mt940",
    "camt053-loader-mt942",
    "camt053-writer-xlsx",
)


@pytest.fixture
def script(monkeypatch):
    """The script as a module, with PyPI and the tree substituted."""
    spec = importlib.util.spec_from_file_location("suite_check", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def use(tree: str, published: dict[str, str | None]):
        monkeypatch.setattr(module, "tree_version", lambda: tree)
        monkeypatch.setattr(module, "published_version", published.get)
        return module.check()[0]

    return use


def _suite(version: str, **members: str) -> dict[str, str | None]:
    versions: dict[str, str | None] = dict.fromkeys(MEMBERS, version)
    versions.update({k.replace("_", "-"): v for k, v in members.items()})
    return versions


def test_a_suite_on_one_version_is_clean(script):
    assert script("0.0.23", _suite("0.0.23")) == []


def test_a_server_ahead_of_the_core_fails(script):
    """The state main was in: camt053-mcp 0.0.22, every other member 0.0.20."""
    problems = script("0.0.22", _suite("0.0.20", camt053_mcp="0.0.22"))
    assert problems == [
        "camt053-mcp: published 0.0.22, but the suite is at 0.0.20"
    ]


def test_a_member_left_behind_fails(script):
    problems = script("0.0.23", _suite("0.0.23", camt053_lsp="0.0.20"))
    assert problems == [
        "camt053-lsp: published 0.0.20, but the suite is at 0.0.23"
    ]


def test_unreleased_bump_names_this_distribution(script):
    """A bump that never reached PyPI is reported against camt053-mcp."""
    problems = script("0.0.23", _suite("0.0.22"))
    assert problems == [
        "camt053-mcp: tree is 0.0.23 but PyPI has 0.0.22. Either the "
        "release was never tagged, or the tree is behind."
    ]


def test_unreadable_index_is_reported(script):
    """A member PyPI cannot answer for is a problem, never a pass."""
    versions = _suite("0.0.23")
    del versions["camt053-writer-xlsx"]
    assert script("0.0.23", versions) == [
        "camt053-writer-xlsx: could not read PyPI"
    ]
