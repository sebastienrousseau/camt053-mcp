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

"""Prometheus observability for the MCP layer.

Four metrics cover the HTTP transport and the tool dispatcher:

* ``mcp_http_requests_total{path, status}`` -- every HTTP request that
  reaches the transport, labelled with its (normalised) path and
  response status.
* ``mcp_tool_invocations_total{tool, outcome}`` -- every MCP tool
  dispatch, labelled ``success`` / ``error`` (the server's
  ``{"error": ...}`` envelope convention) / ``exception`` (raised).
* ``mcp_tool_latency_seconds{tool}`` -- tool dispatch latency
  histogram.
* ``mcp_auth_failures_total{reason}`` -- rejected requests by stable
  failure reason (``invalid_static_token``, ``token_expired``,
  ``issuer_mismatch``, ...), incremented by the auth middlewares.

Instrumentation points:

* :class:`MetricsMiddleware` wraps the (already auth-wrapped) ASGI app
  as the **outermost** layer, so rejected requests are counted too,
  and serves ``GET /metrics`` itself.
* :func:`instrument_tools` wraps the FastMCP tool manager's
  ``call_tool`` dispatcher once (idempotent), timing every invocation
  regardless of transport.

**Access policy**: ``/metrics`` and ``/.well-known/*`` are exempt from
bearer/OAuth auth. Metadata *must* be anonymous (RFC 9728 clients read
it before they hold a token); ``/metrics`` is exempt for operational
symmetry -- Prometheus scrapers rarely speak OAuth -- on the grounds
that it exposes only aggregate counters (tool names, status codes,
failure reasons; never arguments, tenants, or statement data). If that
is still too much surface for your deployment, block ``/metrics`` at
the reverse proxy or scrape over the loopback interface only. The
trade-off is documented in ``docs/quickstart.md``.

All metrics live in the module-level :data:`REGISTRY` (a dedicated
``CollectorRegistry``, not the process-global default) so the exposure
endpoint serves exactly these series and tests can read them without
cross-suite interference.
"""

from __future__ import annotations

import json
import time
from typing import Any

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
)
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

__all__ = [
    "METRICS_PATH",
    "REGISTRY",
    "AUTH_FAILURES",
    "HTTP_REQUESTS",
    "TOOL_INVOCATIONS",
    "TOOL_LATENCY",
    "MetricsMiddleware",
    "classify_outcome",
    "instrument_tools",
]

#: The Prometheus exposition endpoint served by the middleware.
METRICS_PATH = "/metrics"

#: Dedicated registry holding exactly this module's metrics.
REGISTRY = CollectorRegistry()

#: HTTP requests by (normalised) path and response status.
HTTP_REQUESTS = Counter(
    "mcp_http_requests_total",
    "HTTP requests handled by the MCP transport.",
    ("path", "status"),
    registry=REGISTRY,
)

#: Tool dispatches by tool name and outcome (success/error/exception).
TOOL_INVOCATIONS = Counter(
    "mcp_tool_invocations_total",
    "MCP tool invocations by outcome.",
    ("tool", "outcome"),
    registry=REGISTRY,
)

#: Tool dispatch latency, per tool.
TOOL_LATENCY = Histogram(
    "mcp_tool_latency_seconds",
    "MCP tool dispatch latency in seconds.",
    ("tool",),
    registry=REGISTRY,
)

#: Rejected requests by stable failure reason.
AUTH_FAILURES = Counter(
    "mcp_auth_failures_total",
    "Authentication failures by reason.",
    ("reason",),
    registry=REGISTRY,
)

#: Paths reported verbatim as the ``path`` label; anything else is
#: folded into ``"other"`` so hostile or misdirected traffic cannot
#: explode the label cardinality.
_KNOWN_PATHS = frozenset({"/mcp", METRICS_PATH})

#: Prefix under which RFC 9728 metadata paths are reported.
_WELL_KNOWN_PREFIX = "/.well-known/oauth-protected-resource"


def _normalise_path(path: str) -> str:
    """Fold ``path`` into a bounded label set.

    Args:
        path: The raw ASGI request path.

    Returns:
        The path itself for known endpoints, the well-known stem for
        RFC 9728 metadata variants, or ``"other"``.
    """
    if path in _KNOWN_PATHS:
        return path
    if path.startswith(_WELL_KNOWN_PREFIX):
        return _WELL_KNOWN_PREFIX
    return "other"


def classify_outcome(result: Any) -> str:
    """Classify a tool result as ``"success"`` or ``"error"``.

    Mirrors the server's error conventions: tools never raise on
    domain failures, they return an ``{"error": ...}`` dict, a list
    containing one, an ``"error: ..."`` string row, or (for XML
    tools) a serialised ``{"error": ...}`` JSON string. Converted
    results (``convert_result=True`` tuples, ``{"result": ...}``
    structured-output wrappers) are unwrapped recursively.

    Args:
        result: The value returned by the tool dispatcher.

    Returns:
        ``"error"`` when an error envelope is recognised anywhere in
        the result, ``"success"`` otherwise.
    """
    if isinstance(result, tuple):
        for element in result:
            if classify_outcome(element) == "error":
                return "error"
        return "success"
    if isinstance(result, dict):
        if "error" in result:
            return "error"
        if set(result) == {"result"}:
            return classify_outcome(result["result"])
        return "success"
    if isinstance(result, list):
        for item in result:
            if classify_outcome(item) == "error":
                return "error"
        return "success"
    if isinstance(result, str):
        stripped = result.lstrip()
        if stripped.startswith("error:"):
            return "error"
        if stripped.startswith("{"):
            try:
                payload = json.loads(stripped)
            except ValueError:
                return "success"
            return classify_outcome(payload)
        return "success"
    return "success"


def instrument_tools(mcp_server: Any) -> bool:
    """Wrap ``mcp_server``'s tool dispatcher with metrics (idempotent).

    Wraps the FastMCP ``ToolManager.call_tool`` entry point -- the
    single funnel every tool invocation passes through -- recording
    :data:`TOOL_INVOCATIONS` and :data:`TOOL_LATENCY` per call. A
    server is only ever wrapped once; repeated calls are no-ops.

    Args:
        mcp_server: The FastMCP server whose dispatcher to wrap. An
            object without a ``_tool_manager`` (e.g. the transport
            test fakes, or a future SDK that renames the attribute)
            is left untouched.

    Returns:
        ``True`` when the dispatcher is instrumented (now or already),
        ``False`` when the server exposes no tool manager.
    """
    manager = getattr(mcp_server, "_tool_manager", None)
    if manager is None:
        return False
    if getattr(manager, "_camt053_metrics_instrumented", False):
        return True
    original = manager.call_tool

    async def call_tool(
        name: str,
        arguments: dict[str, Any],
        context: Any = None,
        convert_result: bool = False,
    ) -> Any:
        """Dispatch one tool call, recording latency and outcome."""
        started = time.perf_counter()
        outcome = "exception"
        try:
            result = await original(
                name,
                arguments,
                context=context,
                convert_result=convert_result,
            )
            outcome = classify_outcome(result)
            return result
        finally:
            TOOL_INVOCATIONS.labels(tool=name, outcome=outcome).inc()
            TOOL_LATENCY.labels(tool=name).observe(
                time.perf_counter() - started
            )

    manager.call_tool = call_tool
    manager._camt053_metrics_instrumented = True
    return True


class MetricsMiddleware:
    """Outermost ASGI middleware: request counting + ``GET /metrics``.

    Wraps the auth middleware (so 401/403 rejections are counted) and
    serves the Prometheus exposition format on :data:`METRICS_PATH`
    without authentication -- see the module docstring for the access
    policy and its trade-off. Non-HTTP scopes pass through untouched.
    """

    def __init__(self, app: ASGIApp) -> None:
        """Wrap ``app`` with request metrics.

        Args:
            app: The downstream ASGI application (the auth-wrapped
                MCP app).
        """
        self._app = app

    async def __call__(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        """Serve /metrics or count one downstream request.

        Args:
            scope: The ASGI connection scope.
            receive: The ASGI receive callable.
            send: The ASGI send callable.
        """
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        path = scope.get("path", "")
        if path == METRICS_PATH and scope.get("method") in ("GET", "HEAD"):
            response = Response(
                generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST
            )
            await response(scope, receive, send)
            HTTP_REQUESTS.labels(path=METRICS_PATH, status="200").inc()
            return
        observed = {"status": "500"}  # downstream crash before start

        async def counting_send(message: Any) -> None:
            """Record the response status, then forward ``message``."""
            if message["type"] == "http.response.start":
                observed["status"] = str(message["status"])
            await send(message)

        try:
            await self._app(scope, receive, counting_send)
        finally:
            HTTP_REQUESTS.labels(
                path=_normalise_path(path), status=observed["status"]
            ).inc()
