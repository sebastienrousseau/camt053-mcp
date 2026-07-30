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

"""Optional OpenTelemetry distributed tracing for the MCP layer.

Tracing is an **opt-in** feature gated behind the ``[otel]`` extra. The
base install pulls in *no* OpenTelemetry dependency, so importing this
module always succeeds; the heavy ``opentelemetry`` packages are imported
lazily, only when :func:`init_tracing` is called. When the extra is not
installed, :func:`init_tracing` returns ``False`` (graceful, no crash) and
every tracing primitive degrades to a zero-overhead no-op.

Usage::

    from camt053_mcp import tracing

    # Enable at startup (endpoint may also come from the standard
    # OTEL_EXPORTER_OTLP_ENDPOINT environment variable):
    if tracing.init_tracing(endpoint="http://collector:4318/v1/traces"):
        tracing.instrument_tracing(server)

Three primitives are exposed:

* :func:`init_tracing` -- one-time setup of a global ``TracerProvider``
  carrying a ``service.name`` resource, plus an OTLP/HTTP span exporter
  when an endpoint is configured. Returns ``True`` when tracing is now
  active, ``False`` when the ``[otel]`` extra is not installed.
* :func:`trace_span` -- a context manager that opens one span, records
  any exception raised in the block, and sets the span status. A no-op
  (yielding ``None``) until :func:`init_tracing` has succeeded.
* :func:`traced_tool` -- a decorator built on :func:`trace_span` that
  wraps a sync or async callable in a named span.
* :func:`instrument_tracing` -- wires the FastMCP tool dispatcher so
  every tool invocation is traced (idempotent).
"""

from __future__ import annotations

import functools
import inspect
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, TypeVar, cast

if TYPE_CHECKING:  # pragma: no cover - typing-only imports
    from opentelemetry.trace import Tracer

__all__ = [
    "OTEL_ENDPOINT_ENV",
    "init_tracing",
    "is_active",
    "trace_span",
    "traced_tool",
    "instrument_tracing",
]

#: The standard OpenTelemetry environment variable naming the OTLP
#: endpoint. Read by :func:`init_tracing` when no explicit ``endpoint``
#: argument is supplied.
OTEL_ENDPOINT_ENV = "OTEL_EXPORTER_OTLP_ENDPOINT"

#: The active tracer, or ``None`` until :func:`init_tracing` succeeds.
_TRACER: Tracer | None = None

#: The active provider, retained so tests (and shutdown paths) can attach
#: extra span processors after initialisation.
_PROVIDER: Any = None

F = TypeVar("F", bound=Callable[..., Any])


def is_active() -> bool:
    """Return ``True`` when tracing has been initialised and is emitting.

    A cheap predicate mirroring the internal tracer state, so callers can
    branch on tracing being live without importing OpenTelemetry.
    """
    return _TRACER is not None


def init_tracing(
    endpoint: str | None = None,
    service_name: str = "camt053-mcp",
) -> bool:
    """Initialise global OpenTelemetry tracing (opt-in, idempotent-safe).

    Lazily imports the OpenTelemetry API + SDK. When the ``[otel]`` extra
    is not installed the import fails and this returns ``False`` without
    raising, leaving every tracing primitive a no-op. When it is
    installed, a global ``TracerProvider`` is created with a
    ``service.name`` resource and registered as the process tracer
    provider; if ``endpoint`` (or the ``OTEL_EXPORTER_OTLP_ENDPOINT``
    environment variable) is set, an OTLP/HTTP span exporter is attached
    via a ``BatchSpanProcessor``.

    Args:
        endpoint: The OTLP/HTTP traces endpoint (e.g.
            ``"http://collector:4318/v1/traces"``). When ``None`` the
            ``OTEL_EXPORTER_OTLP_ENDPOINT`` environment variable is used;
            when neither is set, spans are still recorded on the provider
            but no exporter is wired (useful for in-process inspection).
        service_name: The ``service.name`` resource attribute stamped on
            every span (default ``"camt053-mcp"``).

    Returns:
        ``True`` when tracing is now active, ``False`` when the ``[otel]``
        extra is not installed.
    """
    global _TRACER, _PROVIDER
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        return False

    provider = TracerProvider(
        resource=Resource.create({"service.name": service_name})
    )

    endpoint = endpoint or os.environ.get(OTEL_ENDPOINT_ENV)
    if endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )

        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint))
        )

    trace.set_tracer_provider(provider)
    _PROVIDER = provider
    # Obtain the tracer from *our* provider rather than the global, so our
    # spans are unaffected by OpenTelemetry's set-once global-provider rule
    # (a second init_tracing in the same process still traces correctly).
    _TRACER = provider.get_tracer("camt053-mcp")
    return True


def provider() -> Any:
    """Return the active ``TracerProvider``, or ``None`` before init.

    Exposed so callers (and tests) can attach extra span processors or drive a
    clean shutdown/flush without reaching into module state directly.
    """
    return _PROVIDER


@contextmanager
def trace_span(name: str) -> Iterator[Any]:
    """Open a span named ``name`` for the duration of the ``with`` block.

    A zero-overhead no-op until :func:`init_tracing` has succeeded: when
    tracing is inactive it simply yields ``None`` and returns. When active
    it starts a span, yields it, records any exception raised in the block
    (marking the span status ``ERROR`` and re-raising), and otherwise marks
    the status ``OK``.

    Args:
        name: The span name (e.g. ``"mcp.tool.parse_statement"``).

    Yields:
        The active span, or ``None`` when tracing is inactive.
    """
    tracer = _TRACER
    if tracer is None:
        yield None
        return

    from opentelemetry.trace import Status, StatusCode

    with tracer.start_as_current_span(name) as span:
        try:
            yield span
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            raise
        else:
            span.set_status(Status(StatusCode.OK))


def traced_tool(name: str) -> Callable[[F], F]:
    """Decorate a sync or async callable to run inside a :func:`trace_span`.

    The wrapped callable executes inside a span named ``name``; exception
    recording and status are handled by :func:`trace_span`, so the
    decorator is a zero-overhead no-op until tracing is initialised.
    Coroutine functions are awaited inside the span; plain functions run
    synchronously inside it.

    Args:
        name: The span name to open around each invocation.

    Returns:
        A decorator preserving the wrapped callable's signature.
    """

    def decorator(func: F) -> F:
        """Wrap ``func`` so each call runs inside a named span."""
        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                """Await ``func`` inside a tracing span."""
                with trace_span(name):
                    return await func(*args, **kwargs)

            return cast(F, async_wrapper)

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            """Call ``func`` inside a tracing span."""
            with trace_span(name):
                return func(*args, **kwargs)

        return cast(F, wrapper)

    return decorator


def instrument_tracing(mcp_server: Any) -> bool:
    """Wrap ``mcp_server``'s tool dispatcher with a span (idempotent).

    Wraps the FastMCP ``ToolManager.call_tool`` entry point -- the single
    funnel every tool invocation passes through -- so each dispatch opens a
    span named ``mcp.tool.<name>``. When tracing is inactive the span is a
    no-op, so the wrapper adds negligible overhead. A server is only ever
    wrapped once; repeated calls are no-ops.

    Args:
        mcp_server: The FastMCP server whose dispatcher to wrap. An object
            without a ``_tool_manager`` (e.g. a test fake) is left
            untouched.

    Returns:
        ``True`` when the dispatcher is instrumented (now or already),
        ``False`` when the server exposes no tool manager.
    """
    manager = getattr(mcp_server, "_tool_manager", None)
    if manager is None:
        return False
    if getattr(manager, "_camt053_tracing_instrumented", False):
        return True
    original = manager.call_tool

    async def call_tool(
        name: str,
        arguments: dict[str, Any],
        context: Any = None,
        convert_result: bool = False,
    ) -> Any:
        """Dispatch one tool call inside a span named for the tool."""
        with trace_span(f"mcp.tool.{name}"):
            return await original(
                name,
                arguments,
                context=context,
                convert_result=convert_result,
            )

    manager.call_tool = call_tool
    manager._camt053_tracing_instrumented = True
    return True
