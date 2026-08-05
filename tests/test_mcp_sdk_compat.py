"""Regression tests for the ``mcp`` SDK compatibility layer.

``mcp`` 2.0 removed ``mcp.server.fastmcp`` and renamed ``FastMCP`` to
``MCPServer``, which broke every import site in this package. These tests
pin the behaviour that has to hold on *whichever* major is installed, so
the next SDK move fails here rather than at runtime in a client.

The suite runs against one installed major at a time; CI's pinned
requirements resolve mcp 2.x, and the 1.x branch is exercised by
installing the floor of the declared range.
"""

from __future__ import annotations

import asyncio

import camt053_mcp._mcp_compat as compat
from camt053_mcp import __version__
from camt053_mcp.server import server


def test_supported_major() -> None:
    """The layer must recognise the installed SDK, not silently guess."""
    assert compat.MCP_MAJOR in (1, 2)


def test_server_class_matches_major() -> None:
    """2.x must resolve MCPServer, 1.x must resolve FastMCP."""
    expected = "MCPServer" if compat.MCP_MAJOR >= 2 else "FastMCP"
    assert compat.MCPServer.__name__ == expected
    assert type(server).__name__ == expected


def test_context_is_importable() -> None:
    """``Context`` moved modules in 2.0; the layer must still expose it."""
    assert compat.Context is not None


def test_context_is_the_injectable_one() -> None:
    """The exported ``Context`` must be the class the SDK injects.

    mcp 2.x ships *two* different classes called ``Context``:
    ``mcp.server.context.Context`` and
    ``mcp.server.mcpserver.context.Context``. Only the latter is matched
    when the SDK decides which tool argument receives the context.
    Exporting the wrong one makes the parameter look like ordinary tool
    input and blows up schema generation with "Cannot generate a
    JsonSchema for core_schema.IsInstanceSchema".
    """
    if compat.MCP_MAJOR < 2:
        return

    from mcp.server.mcpserver.tools.base import find_context_parameter

    def probe(ctx: compat.Context, value: int) -> int:  # pragma: no cover
        return value

    assert find_context_parameter(probe) == "ctx"


def test_server_reports_package_version() -> None:
    """serverInfo.version must equal the package version on both majors."""
    assert compat.server_version(server) == __version__


def test_build_server_sets_version() -> None:
    """The version must survive construction, whichever mechanism is used."""
    built = compat.build_server("compat-probe", "9.9.9")
    assert compat.server_version(built) == "9.9.9"


def test_tools_are_registered() -> None:
    """A rename that silently dropped the decorators would show up here."""
    tools = asyncio.run(server.list_tools())
    assert tools, "no tools registered on the MCP server"
    assert all(t.name for t in tools)


def test_result_is_error_handles_both_spellings() -> None:
    """The helper must not depend on which spelling the SDK exposes."""

    class OnlySnake:
        is_error = True

    class OnlyCamel:
        isError = True  # noqa: N815 - mirrors the mcp 1.x field name

    assert compat.result_is_error(OnlySnake()) is True
    assert compat.result_is_error(OnlyCamel()) is True
    assert compat.result_is_error(object()) is False


def test_result_content_handles_every_shape() -> None:
    """2.x wraps content in an object; 1.x returned it bare or in a tuple."""

    class Wrapped:
        content = ["block"]

    assert compat.result_content(Wrapped()) == ["block"]
    assert compat.result_content((["block"], {"meta": 1})) == ["block"]
    assert compat.result_content(["block"]) == ["block"]


def test_result_structured_handles_both_shapes() -> None:
    """2.x exposes structured_content; 1.x used the tuple's second slot."""

    class Wrapped:
        structured_content = {"result": [1, 2]}

    assert compat.result_structured(Wrapped()) == {"result": [1, 2]}
    assert compat.result_structured((["block"], {"result": []})) == {
        "result": []
    }
    assert compat.result_structured(["block"]) is None
