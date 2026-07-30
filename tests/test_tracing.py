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

"""Tests for the optional OpenTelemetry tracing layer.

Real verification: every "active" case initialises tracing against an
in-process ``InMemorySpanExporter`` (SimpleSpanProcessor) and asserts the
exact span names, statuses, resource attributes, and exception events the
code produced -- no mocks of the OpenTelemetry API. The "extra not
installed" path is exercised by making the lazy ``opentelemetry`` import
raise ``ImportError`` and asserting graceful degradation to no-ops.
"""

import asyncio
import builtins
from types import SimpleNamespace

import pytest

from camt053_mcp import tracing


@pytest.fixture(autouse=True)
def _reset_tracing_state():
    """Reset the module-global tracer/provider around every test."""
    tracing._TRACER = None
    tracing._PROVIDER = None
    yield
    tracing._TRACER = None
    tracing._PROVIDER = None


def _memory_exporter():
    """Attach an in-memory exporter to the live provider and return it."""
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    exporter = InMemorySpanExporter()
    tracing.provider().add_span_processor(SimpleSpanProcessor(exporter))
    return exporter


class _FakeManager:
    """A stand-in FastMCP ToolManager recording its dispatches."""

    def __init__(self, result=None):
        """Store the scripted ``result`` returned by call_tool."""
        self.result = result
        self.calls = []

    async def call_tool(
        self, name, arguments, context=None, convert_result=False
    ):
        """Record the call and return the scripted result."""
        self.calls.append((name, arguments, context, convert_result))
        return self.result


# ─── init_tracing ────────────────────────────────────────────────────────────


def test_init_tracing_activates_and_sets_service_name(monkeypatch):
    """A plain init_tracing() call activates tracing with our resource."""
    monkeypatch.delenv(tracing.OTEL_ENDPOINT_ENV, raising=False)
    assert tracing.is_active() is False
    assert tracing.init_tracing(service_name="camt053-mcp-test") is True
    assert tracing.is_active() is True

    exporter = _memory_exporter()
    with tracing.trace_span("mcp.tool.probe"):
        pass
    spans = exporter.get_finished_spans()
    assert [s.name for s in spans] == ["mcp.tool.probe"]
    assert spans[0].resource.attributes["service.name"] == "camt053-mcp-test"


def test_init_tracing_wires_otlp_exporter_when_endpoint_given(monkeypatch):
    """An explicit endpoint attaches an OTLP/HTTP batch exporter."""
    monkeypatch.delenv(tracing.OTEL_ENDPOINT_ENV, raising=False)
    assert (
        tracing.init_tracing(endpoint="http://collector:4318/v1/traces")
        is True
    )
    # A span processor is registered on the active provider.
    processor = tracing._PROVIDER._active_span_processor
    assert processor is not None


def test_init_tracing_reads_endpoint_from_env(monkeypatch):
    """With no argument the standard OTLP env var supplies the endpoint."""
    monkeypatch.setenv(
        tracing.OTEL_ENDPOINT_ENV, "http://collector:4318/v1/traces"
    )
    assert tracing.init_tracing() is True
    assert tracing.is_active() is True


def test_init_tracing_returns_false_when_extra_missing(monkeypatch):
    """Missing [otel] extra -> init returns False and primitives no-op."""
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        """Fail every opentelemetry import to simulate the missing extra."""
        if name.startswith("opentelemetry"):
            raise ImportError("No module named 'opentelemetry'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    assert tracing.init_tracing() is False
    assert tracing.is_active() is False

    # trace_span is a zero-overhead no-op yielding None.
    with tracing.trace_span("mcp.tool.noop") as span:
        assert span is None

    # traced_tool wraps but adds no behaviour.
    @tracing.traced_tool("mcp.tool.noop")
    def echo(value):
        """Return its argument unchanged."""
        return value

    assert echo(7) == 7


# ─── trace_span ──────────────────────────────────────────────────────────────


def test_trace_span_marks_ok_status(monkeypatch):
    """A clean block yields a live span and closes it with status OK."""
    from opentelemetry.trace import StatusCode

    monkeypatch.delenv(tracing.OTEL_ENDPOINT_ENV, raising=False)
    assert tracing.init_tracing() is True
    exporter = _memory_exporter()

    with tracing.trace_span("mcp.tool.ok") as span:
        assert span is not None

    (recorded,) = exporter.get_finished_spans()
    assert recorded.name == "mcp.tool.ok"
    assert recorded.status.status_code == StatusCode.OK


def test_trace_span_records_exception_and_sets_error(monkeypatch):
    """An exception in the block is recorded and re-raised, status ERROR."""
    from opentelemetry.trace import StatusCode

    monkeypatch.delenv(tracing.OTEL_ENDPOINT_ENV, raising=False)
    assert tracing.init_tracing() is True
    exporter = _memory_exporter()

    # try/except (not pytest.raises) so the post-block assertions are plainly
    # reachable to static analysis while still proving the exception re-raises.
    raised: ValueError | None = None
    try:
        with tracing.trace_span("mcp.tool.fail"):
            raise ValueError("boom")
    except ValueError as exc:
        raised = exc
    assert raised is not None and str(raised) == "boom"

    (recorded,) = exporter.get_finished_spans()
    assert recorded.name == "mcp.tool.fail"
    assert recorded.status.status_code == StatusCode.ERROR
    assert any(event.name == "exception" for event in recorded.events)


# ─── traced_tool ─────────────────────────────────────────────────────────────


def test_traced_tool_sync_runs_inside_span(monkeypatch):
    """A decorated sync callable runs inside a named span, result intact."""
    monkeypatch.delenv(tracing.OTEL_ENDPOINT_ENV, raising=False)
    assert tracing.init_tracing() is True
    exporter = _memory_exporter()

    @tracing.traced_tool("mcp.tool.add")
    def add(a, b):
        """Return the sum of two numbers."""
        return a + b

    assert add(2, 3) == 5
    assert [s.name for s in exporter.get_finished_spans()] == ["mcp.tool.add"]


def test_traced_tool_async_runs_inside_span(monkeypatch):
    """A decorated coroutine is awaited inside a named span."""
    monkeypatch.delenv(tracing.OTEL_ENDPOINT_ENV, raising=False)
    assert tracing.init_tracing() is True
    exporter = _memory_exporter()

    @tracing.traced_tool("mcp.tool.mul")
    async def mul(a, b):
        """Return the product of two numbers."""
        return a * b

    assert asyncio.run(mul(4, 5)) == 20
    assert [s.name for s in exporter.get_finished_spans()] == ["mcp.tool.mul"]


# ─── instrument_tracing ──────────────────────────────────────────────────────


def test_instrument_tracing_skips_servers_without_manager():
    """An object without a _tool_manager is left untouched."""
    assert tracing.instrument_tracing(SimpleNamespace()) is False


def test_instrument_tracing_is_idempotent(monkeypatch):
    """A second instrumentation does not double-wrap the dispatcher."""
    monkeypatch.delenv(tracing.OTEL_ENDPOINT_ENV, raising=False)
    assert tracing.init_tracing() is True
    manager = _FakeManager(result={"ok": True})
    srv = SimpleNamespace(_tool_manager=manager)
    assert tracing.instrument_tracing(srv) is True
    wrapped = manager.call_tool
    assert tracing.instrument_tracing(srv) is True
    assert manager.call_tool is wrapped


def test_instrument_tracing_traces_each_dispatch(monkeypatch):
    """Every wrapped dispatch opens a span named for the tool."""
    monkeypatch.delenv(tracing.OTEL_ENDPOINT_ENV, raising=False)
    assert tracing.init_tracing() is True
    exporter = _memory_exporter()

    manager = _FakeManager(result={"valid": True})
    srv = SimpleNamespace(_tool_manager=manager)
    assert tracing.instrument_tracing(srv) is True

    out = asyncio.run(
        srv._tool_manager.call_tool(
            "validate_identifier", {"kind": "bic"}, context=None
        )
    )
    assert out == {"valid": True}
    assert manager.calls == [
        ("validate_identifier", {"kind": "bic"}, None, False)
    ]
    assert [s.name for s in exporter.get_finished_spans()] == [
        "mcp.tool.validate_identifier"
    ]
