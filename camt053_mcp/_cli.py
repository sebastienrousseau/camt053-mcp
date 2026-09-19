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
"""The command line every server in the suite shares.

``serve`` is the body of each ``main()``: it parses ``--transport``,
``--host``, ``--port`` and ``--version`` and hands the result to
:func:`_transports.run`. Flag names, defaults and help text live here and
nowhere else, so the suite is started and documented the same way.

Copied verbatim into every server of the suite.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from typing import Any

from camt053_mcp._transports import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    TRANSPORTS,
    run,
)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Add ``--transport``, ``--host`` and ``--port`` to ``parser``."""
    group = parser.add_argument_group("transport")
    group.add_argument(
        "--transport",
        choices=TRANSPORTS,
        default="stdio",
        help=(
            "how to talk to the client: stdio (default; the client spawns "
            "this process), streamable-http (HTTP at --host:--port/mcp, "
            "protocol 2026-07-28 and 2025-11-25) or sse (the older HTTP+SSE "
            "transport at /sse and /messages/)"
        ),
    )
    group.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help="interface to bind for the HTTP transports (default: 127.0.0.1)",
    )
    group.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help="port to bind for the HTTP transports (default: 8000)",
    )


def serve(
    server: Any, argv: Sequence[str] | None, prog: str, version: str
) -> None:
    """Parse ``argv`` and run ``server``: the body of every ``main()``."""
    parser = argparse.ArgumentParser(
        prog=prog,
        description=f"{prog} {version}: an MCP server. Speaks stdio by default.",
    )
    parser.add_argument(
        "--version", action="version", version=f"{prog} {version}"
    )
    add_arguments(parser)
    args = parser.parse_args(argv)
    run(server, args.transport, args.host, args.port)
