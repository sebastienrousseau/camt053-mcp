"""Compatibility layer over the ``mcp`` SDK's two supported majors.

``mcp`` 2.0 removed the ``mcp.server.fastmcp`` module. ``FastMCP`` became
``mcp.server.MCPServer``, ``Context`` moved to
``mcp.server.mcpserver.context``, and the prompt message classes moved to
``mcp.server.mcpserver.prompts.base``. Server construction, the
``.tool()`` / ``.resource()`` decorators and ``.run()`` are otherwise
identical, so a single indirection covers both.

Several result attributes were also renamed camelCase -> snake_case
(``isError`` -> ``is_error``); :func:`result_content` and
:func:`result_is_error` paper over those.

Import the server class, :data:`Context` and :func:`build_server` from
here rather than from ``mcp`` directly, so there is exactly one place to
update when the SDK moves again.

The two majors also differ in how ``serverInfo.version`` is set: 2.x
takes ``version=`` on the constructor and exposes ``.version`` read-only,
while 1.x has neither and needs the value poked onto the underlying
low-level server. :func:`build_server` and :func:`server_version` hide
that difference.

Only one major can be installed at a time, so whichever branch is not
taken is unreachable and carries ``pragma: no cover``. Both branches are
exercised in CI by running the suite twice — once against the pinned
requirements (2.x) and once against the floor of the declared range.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "MCP_MAJOR",
    "AssistantMessage",
    "Context",
    "MCPServer",
    "UserMessage",
    "build_server",
    "result_content",
    "result_is_error",
    "result_structured",
    "server_version",
]


def _resolve() -> tuple[Any, Any, int]:
    """Return ``(server_class, context_class, major)`` for the installed SDK."""
    import mcp.server as _mcp_server

    server_cls = getattr(_mcp_server, "MCPServer", None)
    if server_cls is not None:  # mcp >= 2
        # Must be ``mcp.server.mcpserver.context``, NOT
        # ``mcp.server.context``. Both exist in 2.x and they are
        # *different classes*; only this one is what
        # ``find_context_parameter`` matches when deciding which tool
        # argument to inject. Annotating a tool with the other one makes
        # the parameter look like ordinary input, and building the tool
        # schema then dies with "Cannot generate a JsonSchema for
        # core_schema.IsInstanceSchema".
        from mcp.server.mcpserver.context import Context

        return server_cls, Context, 2

    # mcp 1.x. Imported dynamically on purpose: a static
    # ``from mcp.server import fastmcp`` cannot type-check while 2.x is
    # installed, because the module no longer exists there. Going
    # through the module object also avoids rebinding ``Context``, which
    # a second ``from … import Context`` would do.
    import importlib  # pragma: no cover

    fastmcp = importlib.import_module("mcp.server.fastmcp")  # pragma: no cover
    return fastmcp.FastMCP, fastmcp.Context, 1  # pragma: no cover


MCPServer, Context, MCP_MAJOR = _resolve()


def _resolve_prompts() -> tuple[Any, Any]:
    """Return ``(UserMessage, AssistantMessage)`` for the installed SDK.

    The prompt message classes moved from
    ``mcp.server.fastmcp.prompts.base`` to
    ``mcp.server.mcpserver.prompts.base`` in 2.0. Imported dynamically
    for the same reason as the server class: neither path type-checks
    while the other major is installed.
    """
    import importlib

    path = (
        "mcp.server.mcpserver.prompts.base"
        if MCP_MAJOR >= 2
        else "mcp.server.fastmcp.prompts.base"
    )
    base = importlib.import_module(path)
    return base.UserMessage, base.AssistantMessage


UserMessage, AssistantMessage = _resolve_prompts()


def build_server(name: str, version: str) -> Any:
    """Construct the MCP server, reporting ``version`` in ``serverInfo``."""
    if MCP_MAJOR >= 2:
        return MCPServer(name, version=version)

    server = MCPServer(name)  # pragma: no cover  (FastMCP on 1.x)
    server._mcp_server.version = version  # pragma: no cover
    return server  # pragma: no cover


def server_version(server: Any) -> str:
    """Read back the version a server reports, across both majors."""
    version = getattr(server, "version", None)
    if version:
        return str(version)
    return str(server._mcp_server.version)  # pragma: no cover


def result_content(result: Any) -> Any:
    """The content list from a ``call_tool`` result, across both majors.

    2.x returns a ``CallToolResult`` (read ``.content``); 1.x returned
    the content list itself, or a ``(content, meta)`` tuple. Subscripting
    a 2.x result raises ``TypeError``.
    """
    content = getattr(result, "content", None)
    if content is not None:
        return content
    return result[0] if isinstance(result, tuple) else result


def result_structured(result: Any) -> Any:
    """The structured payload of a ``call_tool`` result, or ``None``.

    2.x exposes it as ``CallToolResult.structured_content``; 1.x put it
    in the second slot of a ``(content, structured)`` tuple. Prefer this
    over parsing the text blocks: 2.x emits *one block per item* when a
    tool returns a list, where 1.x emitted a single JSON array, so block
    parsing gives different answers on the two majors.
    """
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        return structured
    if isinstance(result, tuple) and len(result) > 1:
        return result[1]
    return None


def result_is_error(result: Any) -> bool:
    """Whether a ``call_tool`` result is an error, across both majors.

    The attribute was renamed ``isError`` -> ``is_error`` in 2.0. The
    pydantic alias only covers construction kwargs, not attribute reads,
    so both spellings have to be probed.
    """
    if hasattr(result, "is_error"):  # mcp >= 2
        return bool(result.is_error)
    return bool(getattr(result, "isError", False))
