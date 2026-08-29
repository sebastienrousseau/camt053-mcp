# SPDX-FileCopyrightText: 2026 Sebastien Rousseau <sebastian.rousseau@gmail.com>
# SPDX-License-Identifier: Apache-2.0 OR MIT
"""The README's tool list must match what the server actually registers.

A tool that is registered but undocumented is invisible: nobody calls it,
nobody reviews it, and it drifts. That is not hypothetical here --
`search_rulebook_vector` and `detect_statement_anomalies` were both
registered and absent from the README, and the stated tool count had been
wrong by two for long enough that nobody noticed.

Documentation drift is silent by nature: nothing fails, the tests pass,
and the only symptom is a feature nobody knows exists. This makes it
fail.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from camt053_mcp import server

ROOT = Path(__file__).resolve().parent.parent


def _registered_tools() -> set[str]:
    """Every tool name the MCP server actually exposes."""
    tools = asyncio.run(server.server.list_tools())
    return {tool.name for tool in tools}


def _readme_tool_section() -> str:
    """The bullet list under `## Tools`, up to the next subsection."""
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    after = text.split("## Tools", 1)[1]
    return after.split("### Pagination", 1)[0]


def _documented_tools() -> set[str]:
    """Tool names listed in the README's Tools section.

    The character class includes digits: `convert_mt940_to_camt053` does
    not match a letters-only pattern, and a checker that silently skips
    the names it cannot parse reports success while missing exactly the
    entries most likely to be wrong.
    """
    return set(re.findall(r"^- `([a-z0-9_]+)`", _readme_tool_section(), re.M))


def test_every_registered_tool_is_documented() -> None:
    """A registered tool absent from the README is a tool nobody calls."""
    missing = sorted(_registered_tools() - _documented_tools())
    assert not missing, (
        f"registered but undocumented: {missing}. Add each to the Tools "
        f"list in README.md."
    )


def test_no_documented_tool_has_been_removed() -> None:
    """The mirror failure: a tool deleted in code, still advertised."""
    stale = sorted(_documented_tools() - _registered_tools())
    assert not stale, (
        f"documented but not registered: {stale}. Remove each from the "
        f"Tools list in README.md."
    )


def test_the_stated_tool_count_is_right() -> None:
    """The suite table quotes a number, and it had drifted by two."""
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    stated = re.search(r"\| (\d+) MCP tools", text)
    assert stated, "the suite table no longer states a tool count"
    assert int(stated.group(1)) == len(_registered_tools()), (
        f"README says {stated.group(1)} MCP tools; the server registers "
        f"{len(_registered_tools())}"
    )
