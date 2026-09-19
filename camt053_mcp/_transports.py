# Copyright (C) 2023-2026 Sebastien Rousseau.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""One command line for the three MCP transports.

Every server in the suite is started the same way::

    <server>                                   # stdio, for a client that spawns it
    <server> --transport streamable-http       # HTTP on 127.0.0.1:8000, path /mcp
    <server> --transport sse --port 8001       # the older HTTP+SSE transport

Over streamable HTTP the SDK speaks both current protocol revisions on
one endpoint: ``2026-07-28`` (stateless, ``server/discover``, per-request
``_meta``) and ``2025-11-25`` (``initialize`` handshake, ``Mcp-Session-Id``).
Responses stream as server-sent events; a ``GET`` on the same path opens
the server-to-client event stream. ``--transport sse`` serves the
2024-11-05 HTTP+SSE transport for clients that still expect it.

The listener binds the loopback interface unless told otherwise. There
is no authentication here: put the server behind a gateway you trust
before binding a routable address.

This module holds the transport dispatch; the flags that drive it live
in :mod:`_cli`. Both files are copied verbatim into every server of the
suite and work on mcp 1.x and 2.x.
"""

from __future__ import annotations

from typing import Any

TRANSPORTS: tuple[str, ...] = ("stdio", "streamable-http", "sse")
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
STREAMABLE_HTTP_PATH = "/mcp"
SSE_PATH = "/sse"
MESSAGE_PATH = "/messages/"


def run(
    server: Any,
    transport: str = "stdio",
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
) -> None:
    """Serve ``server`` over ``transport``; returns when the server stops.

    Raises:
        ValueError: If ``transport`` is not one of :data:`TRANSPORTS`, or
            ``port`` is outside 1-65535.
    """
    if transport not in TRANSPORTS:
        raise ValueError(
            f"unknown transport {transport!r}; choose from {', '.join(TRANSPORTS)}"
        )
    if not 1 <= port <= 65535:
        raise ValueError(f"port must be between 1 and 65535, got {port}")
    if transport == "stdio":
        server.run()
        return
    settings = getattr(server, "settings", None)
    if settings is not None and hasattr(settings, "host"):
        # mcp 1.x: the listener reads host and port from the settings
        # object and run() takes no keyword arguments.
        settings.host = host
        settings.port = port
        server.run(transport)
        return
    if transport == "streamable-http":
        server.run(
            "streamable-http",
            host=host,
            port=port,
            streamable_http_path=STREAMABLE_HTTP_PATH,
        )
        return
    server.run(
        "sse",
        host=host,
        port=port,
        sse_path=SSE_PATH,
        message_path=MESSAGE_PATH,
    )
