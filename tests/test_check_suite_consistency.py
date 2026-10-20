# SPDX-FileCopyrightText: 2026 Sebastien Rousseau <sebastian.rousseau@gmail.com>
# SPDX-License-Identifier: Apache-2.0 OR MIT
"""scripts/check_suite_consistency.py, without touching the network."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "check_suite_consistency", ROOT / "scripts" / "check_suite_consistency.py"
)
assert _SPEC and _SPEC.loader
suite = importlib.util.module_from_spec(_SPEC)
sys.modules["check_suite_consistency"] = suite
_SPEC.loader.exec_module(suite)


def _published(own: str | None, core: str | None, others: str = "0.0.20"):
    versions = dict.fromkeys(suite.MEMBERS, others)
    versions[suite.SELF] = own
    versions[suite.CORE] = core
    return versions


def test_server_ahead_of_the_core_is_a_note_not_a_failure() -> None:
    """The state main was in: 0.0.22 released, the core at 0.0.20.

    The old check read this tree as the core's and failed every night.
    """
    problems, notes = suite.check(
        "0.0.22", "0.0.17", _published("0.0.22", "0.0.20")
    )
    assert problems == []
    assert notes == ["camt053-mcp: published 0.0.22, the core is at 0.0.20"]


def test_a_bump_that_was_never_released_fails() -> None:
    problems, _ = suite.check(
        "0.0.23", "0.0.17", _published("0.0.22", "0.0.20")
    )
    assert problems == [
        "camt053-mcp: tree is 0.0.23 but PyPI has 0.0.22. Either the "
        "release was never tagged, or the tree is behind."
    ]


def test_a_server_behind_the_core_fails() -> None:
    problems, _ = suite.check(
        "0.0.19", "0.0.17", _published("0.0.19", "0.0.20")
    )
    assert problems == [
        "camt053-mcp: published 0.0.19 trails the core camt053 0.0.20. "
        "Release it."
    ]


def test_an_unpublished_core_floor_fails() -> None:
    problems, _ = suite.check(
        "0.0.22", "0.0.21", _published("0.0.22", "0.0.20")
    )
    assert problems == [
        "camt053-mcp requires camt053>=0.0.21, but the newest published "
        "camt053 is 0.0.20. Nobody can install this combination."
    ]


def test_pypi_not_answering_fails() -> None:
    problems, _ = suite.check("0.0.22", "0.0.17", _published(None, None))
    assert problems == [
        "camt053: could not read PyPI",
        "camt053-mcp: could not read PyPI",
    ]


def test_other_members_drifting_are_notes() -> None:
    _, notes = suite.check(
        "0.0.20", None, _published("0.0.20", "0.0.20", others="0.0.19")
    )
    assert len(notes) == len(suite.MEMBERS) - 2


def test_core_floor_reads_this_pyproject() -> None:
    data = suite._pyproject()
    assert suite.core_floor(data) == "0.0.17"
    assert suite.tree_version(data) == data["tool"]["poetry"]["version"]
    table = {
        "tool": {"poetry": {"dependencies": {"camt053": {"version": ">=1.2"}}}}
    }
    assert suite.core_floor(table) == "1.2"
    assert suite.core_floor({}) is None


def test_as_tuple_stops_at_a_suffix() -> None:
    assert suite.as_tuple("0.0.20") == (0, 0, 20)
    assert suite.as_tuple("0.1.0rc1") == (0, 1)
