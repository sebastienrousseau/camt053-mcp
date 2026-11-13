# SPDX-FileCopyrightText: 2026 Sebastien Rousseau <sebastian.rousseau@gmail.com>
# SPDX-License-Identifier: Apache-2.0 OR MIT

"""Catalogue descriptions must govern only the entry they describe."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from camt053_mcp import server

_SHADOW_CUES = (
    "whenever you call",
    "whenever you use",
    "whenever calling",
    "whenever using",
    "prior to calling",
    "prior to invoking",
    "before calling",
    "before invoking",
    "before you call",
    "before you use",
    "before using",
    "after calling",
    "after invoking",
    "after you call",
    "after using",
    "when calling",
    "when invoking",
    "when you call",
    "when using",
    "do not call",
    "do not use",
    "do not invoke",
    "don't call",
    "don't use",
    "don't invoke",
    "never call",
    "never use",
    "never invoke",
    "always call",
    "always use",
    "always invoke",
    "must call",
    "must use",
    "must invoke",
    "must first call",
    "must always call",
    "instead of calling",
    "in place of calling",
    "ignore the",
    "disregard the",
    "override the",
)

_SHADOW_WINDOW = 120
_TOOL_REF = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")


def _walk_dict(
    node: dict[str, Any], path: str, out: list[tuple[str, str]]
) -> None:
    """Append description and title strings from a mapping node."""
    for key in sorted(node):
        val = node[key]
        if isinstance(val, str) and key in ("description", "title"):
            out.append((f"{path}.{key}", val))
        else:
            _walk_schema(val, f"{path}.{key}", out)


def _walk_schema(node: Any, path: str, out: list[tuple[str, str]]) -> None:
    """Append every description and title inside a JSON Schema."""
    if isinstance(node, dict):
        _walk_dict(node, path, out)
    elif isinstance(node, list):
        for idx, item in enumerate(node):
            _walk_schema(item, f"{path}[{idx}]", out)


def _schema_attr(tool: Any, camel: str, snake: str) -> Any:
    """Fetch a JSON schema attribute across MCP SDK naming conventions."""
    val = getattr(tool, camel, None)
    return val if val is not None else (getattr(tool, snake, None) or {})


def _tool_fields(tool: Any) -> list[tuple[str, str, str]]:
    """Collect catalogue text fields owned by one MCP tool."""
    owner = tool.name
    fields = [
        (f'tool "{owner}" description', tool.description or "", owner),
        (f'tool "{owner}" title', getattr(tool, "title", "") or "", owner),
    ]
    nested: list[tuple[str, str]] = []
    _walk_schema(
        _schema_attr(tool, "inputSchema", "input_schema"),
        f'tool "{owner}" inputSchema',
        nested,
    )
    _walk_schema(
        _schema_attr(tool, "outputSchema", "output_schema"),
        f'tool "{owner}" outputSchema',
        nested,
    )
    fields.extend((where, text, owner) for where, text in nested)
    return fields


def _resource_field(res: Any) -> tuple[str, str, str]:
    """Collect the description field owned by one MCP resource."""
    rname = getattr(res, "name", "") or ""
    desc = getattr(res, "description", "") or ""
    return (f'resource "{rname}" description', desc, rname)


def _prompt_fields(prompt: Any) -> list[tuple[str, str, str]]:
    """Collect description fields owned by one MCP prompt and its args."""
    pname = prompt.name
    pdesc = getattr(prompt, "description", "") or ""
    fields = [(f'prompt "{pname}" description', pdesc, pname)]
    for arg in getattr(prompt, "arguments", None) or []:
        adesc = getattr(arg, "description", "") or ""
        fields.append(
            (
                f'prompt "{pname}" argument "{arg.name}" description',
                adesc,
                pname,
            )
        )
    return fields


def _catalog_fields() -> tuple[list[tuple[str, str, str]], set[str]]:
    """Return every catalogue text field and the set of tool names."""
    tools = list(asyncio.run(server.server.list_tools()))
    resources = list(asyncio.run(server.server.list_resources()))
    prompts = list(asyncio.run(server.server.list_prompts()))
    catalogue = {t.name.lower() for t in tools if t.name}
    fields: list[tuple[str, str, str]] = []
    for tool in tools:
        fields.extend(_tool_fields(tool))
    for res in resources:
        fields.append(_resource_field(res))
    for prompt in prompts:
        fields.extend(_prompt_fields(prompt))
    return fields, catalogue


def _refs_in(window: str, catalogue: set[str]) -> list[str]:
    """Return tool references appearing inside a post-cue text window."""
    refs = set(_TOOL_REF.findall(window))
    for name in catalogue:
        if "_" not in name and re.search(
            rf"(?<![a-z0-9_]){re.escape(name)}(?![a-z0-9_])", window
        ):
            refs.add(name)
    return sorted(refs)


def _cue_hits(
    where: str, lower: str, cue: str, owner: str, catalogue: set[str]
) -> list[tuple[str, str, str]]:
    """Find cross-tool references following one cue in lowercased text."""
    hits: list[tuple[str, str, str]] = []
    pos = 0
    while True:
        idx = lower.find(cue, pos)
        if idx < 0:
            return hits
        pos = idx + len(cue)
        window = lower[pos : pos + _SHADOW_WINDOW]
        hits.extend(
            (where, ref, cue)
            for ref in _refs_in(window, catalogue)
            if ref != owner
        )


def _scan_variant(
    where: str, text: str, owner: str, catalogue: set[str]
) -> list[tuple[str, str, str]]:
    """Find cross-tool shadowing cues inside one text variant."""
    lower = text.lower()
    owner_lower = owner.lower()
    hits: list[tuple[str, str, str]] = []
    for cue in _SHADOW_CUES:
        hits.extend(_cue_hits(where, lower, cue, owner_lower, catalogue))
    return hits


def test_tool_descriptions_govern_only_their_own_tool() -> None:
    """No catalogue description attaches an imperative rule to another tool."""
    fields, catalogue = _catalog_fields()
    offenders: list[tuple[str, str, str]] = []
    for where, text, owner in fields:
        for candidate in (text, re.sub(r"\s+", " ", text)):
            offenders.extend(_scan_variant(where, candidate, owner, catalogue))
    assert sorted(set(offenders)) == []


def test_shadow_scanner_flags_cross_tool_imperative() -> None:
    """Verify the scanner flags cross-tool imperatives and ignores self-refs."""
    catalogue = {"alpha", "beta_tool", "gamma"}
    assert (
        _scan_variant(
            'tool "alpha" description',
            "Do not use alpha malformedly.",
            "alpha",
            catalogue,
        )
        == []
    )
    hits = _scan_variant(
        'tool "alpha" description',
        "Do not use beta_tool or gamma here.",
        "alpha",
        catalogue,
    )
    assert hits == [
        ('tool "alpha" description', "beta_tool", "do not use"),
        ('tool "alpha" description', "gamma", "do not use"),
    ]
