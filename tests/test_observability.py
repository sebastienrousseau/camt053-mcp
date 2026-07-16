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

"""Tests for the Prometheus observability layer.

Covers every metric path:

* ``mcp_http_requests_total`` -- authorized, rejected, exempt, and
  crash-before-response requests, plus path-label normalisation;
* ``mcp_tool_invocations_total`` / ``mcp_tool_latency_seconds`` --
  success, error-envelope, and raising dispatches through the
  instrumented tool manager (idempotence included);
* ``mcp_auth_failures_total`` -- static-token and OAuth rejections;
* the unauthenticated ``GET /metrics`` endpoint, in-process and over
  the real HTTP stack.
"""

import asyncio
from types import SimpleNamespace

import httpx
import pytest

pytest.importorskip("mcp")

from camt053_mcp import oauth, observability, transport  # noqa: E402


def _sample(name, labels):
    """Read one sample from the module registry (0.0 when unset)."""
    return observability.REGISTRY.get_sample_value(name, labels) or 0.0


def _http_scope(path="/mcp", method="POST", headers=()):
    """Build a minimal ASGI HTTP scope."""
    return {
        "type": "http",
        "method": method,
        "path": path,
        "headers": list(headers),
    }


def _drive(app, scope):
    """Dispatch one ASGI event through ``app``; return sent messages."""
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    return sent


async def _ok_downstream(scope, receive, send):
    """A downstream app answering 200 'ok'."""
    await send({"type": "http.response.start", "status": 200})
    await send({"type": "http.response.body", "body": b"ok"})


# ─── path normalisation ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("path", "label"),
    [
        ("/mcp", "/mcp"),
        ("/metrics", "/metrics"),
        (
            "/.well-known/oauth-protected-resource",
            "/.well-known/oauth-protected-resource",
        ),
        (
            "/.well-known/oauth-protected-resource/mcp",
            "/.well-known/oauth-protected-resource",
        ),
        ("/etc/passwd", "other"),
        ("", "other"),
    ],
)
def test_normalise_path_bounds_label_cardinality(path, label):
    """Known endpoints keep their label; junk folds into 'other'."""
    assert observability._normalise_path(path) == label


# ─── classify_outcome ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("result", "outcome"),
    [
        ({"valid": True}, "success"),
        ({"error": "boom"}, "error"),
        ({"result": {"error": "boom"}}, "error"),
        ({"result": [1, 2]}, "success"),
        ([{"code": "AC04"}], "success"),
        ([{"error": "boom"}], "error"),
        (["error: bad message type"], "error"),
        (["fine", "rows"], "success"),
        ("<Document/>", "success"),
        ('{"error": "generation failed"}', "error"),
        ('{"fine": 1}', "success"),
        ("{not json", "success"),
        ((["content"], {"error": "boom"}), "error"),
        ((["content"], {"result": 3}), "success"),
        (42, "success"),
        (None, "success"),
    ],
)
def test_classify_outcome_mirrors_error_envelopes(result, outcome):
    """The outcome heuristic recognises every server error envelope."""
    assert observability.classify_outcome(result) == outcome


# ─── instrument_tools ────────────────────────────────────────────────────────


class _FakeManager:
    """A stand-in ToolManager whose call_tool returns / raises on cue."""

    def __init__(self, result=None, exc=None):
        """Store the scripted ``result`` or ``exc`` for call_tool."""
        self.result = result
        self.exc = exc
        self.calls = []

    async def call_tool(
        self, name, arguments, context=None, convert_result=False
    ):
        """Record the call, then return / raise as scripted."""
        self.calls.append((name, arguments, context, convert_result))
        if self.exc is not None:
            raise self.exc
        return self.result


def _instrumented(result=None, exc=None):
    """Build an instrumented fake server around a scripted manager."""
    manager = _FakeManager(result=result, exc=exc)
    server = SimpleNamespace(_tool_manager=manager)
    assert observability.instrument_tools(server) is True
    return server, manager


def test_instrument_tools_skips_servers_without_manager():
    """Objects without a _tool_manager are left untouched."""
    assert observability.instrument_tools(SimpleNamespace()) is False


def test_instrument_tools_is_idempotent():
    """A second instrumentation does not double-wrap the dispatcher."""
    server, manager = _instrumented(result={"ok": True})
    wrapped = manager.call_tool
    assert observability.instrument_tools(server) is True
    assert manager.call_tool is wrapped


def test_instrumented_dispatch_counts_success_and_latency():
    """A clean dispatch increments success and observes latency."""
    server, manager = _instrumented(result={"valid": True})
    labels = {"tool": "probe_success", "outcome": "success"}
    before = _sample("mcp_tool_invocations_total", labels)
    count_before = _sample(
        "mcp_tool_latency_seconds_count", {"tool": "probe_success"}
    )
    out = asyncio.run(
        server._tool_manager.call_tool("probe_success", {"a": 1})
    )
    assert out == {"valid": True}
    assert manager.calls == [("probe_success", {"a": 1}, None, False)]
    assert _sample("mcp_tool_invocations_total", labels) == before + 1
    assert (
        _sample("mcp_tool_latency_seconds_count", {"tool": "probe_success"})
        == count_before + 1
    )


def test_instrumented_dispatch_counts_error_envelopes():
    """An {"error": ...} result is counted with outcome=error."""
    server, _ = _instrumented(result={"error": "no such thing"})
    labels = {"tool": "probe_error", "outcome": "error"}
    before = _sample("mcp_tool_invocations_total", labels)
    asyncio.run(server._tool_manager.call_tool("probe_error", {}))
    assert _sample("mcp_tool_invocations_total", labels) == before + 1


def test_instrumented_dispatch_counts_raised_exceptions():
    """A raising dispatch is counted outcome=exception and re-raises."""
    server, _ = _instrumented(exc=RuntimeError("boom"))
    labels = {"tool": "probe_raise", "outcome": "exception"}
    before = _sample("mcp_tool_invocations_total", labels)
    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(server._tool_manager.call_tool("probe_raise", {}))
    assert _sample("mcp_tool_invocations_total", labels) == before + 1


def test_instrumented_dispatch_forwards_context_and_convert_result():
    """The wrapper passes context / convert_result through verbatim."""
    server, manager = _instrumented(result="fine")
    ctx = object()
    asyncio.run(
        server._tool_manager.call_tool(
            "probe_fwd", {"x": 1}, context=ctx, convert_result=True
        )
    )
    assert manager.calls == [("probe_fwd", {"x": 1}, ctx, True)]


# ─── MetricsMiddleware ───────────────────────────────────────────────────────


def test_metrics_middleware_serves_exposition_unauthenticated():
    """GET /metrics answers the Prometheus text format, no auth."""
    app = observability.MetricsMiddleware(_ok_downstream)
    before = _sample(
        "mcp_http_requests_total", {"path": "/metrics", "status": "200"}
    )
    sent = _drive(app, _http_scope(path="/metrics", method="GET"))
    assert sent[0]["status"] == 200
    headers = dict(sent[0]["headers"])
    assert b"text/plain" in headers[b"content-type"]
    body = sent[1]["body"].decode()
    assert "mcp_http_requests_total" in body
    assert "mcp_tool_latency_seconds" in body
    assert "mcp_auth_failures_total" in body
    assert (
        _sample(
            "mcp_http_requests_total", {"path": "/metrics", "status": "200"}
        )
        == before + 1
    )


def test_metrics_middleware_counts_downstream_requests():
    """A forwarded request is counted with its path and status."""
    app = observability.MetricsMiddleware(_ok_downstream)
    labels = {"path": "/mcp", "status": "200"}
    before = _sample("mcp_http_requests_total", labels)
    sent = _drive(app, _http_scope(path="/mcp"))
    assert sent[0]["status"] == 200
    assert _sample("mcp_http_requests_total", labels) == before + 1


def test_metrics_middleware_normalises_unknown_paths():
    """Junk paths are folded into the 'other' label."""
    app = observability.MetricsMiddleware(_ok_downstream)
    labels = {"path": "other", "status": "200"}
    before = _sample("mcp_http_requests_total", labels)
    _drive(app, _http_scope(path="/definitely/not/a/route"))
    assert _sample("mcp_http_requests_total", labels) == before + 1


def test_metrics_middleware_post_to_metrics_goes_downstream():
    """Only GET/HEAD are the exposition; POST /metrics is forwarded."""
    app = observability.MetricsMiddleware(_ok_downstream)
    labels = {"path": "/metrics", "status": "200"}
    before = _sample("mcp_http_requests_total", labels)
    sent = _drive(app, _http_scope(path="/metrics", method="POST"))
    assert sent[1]["body"] == b"ok"  # downstream, not the exposition
    assert _sample("mcp_http_requests_total", labels) == before + 1


def test_metrics_middleware_counts_downstream_crash_as_500():
    """A crash before any response start is recorded as status 500."""

    async def crashing(scope, receive, send):
        raise RuntimeError("downstream blew up")

    app = observability.MetricsMiddleware(crashing)
    labels = {"path": "/mcp", "status": "500"}
    before = _sample("mcp_http_requests_total", labels)
    with pytest.raises(RuntimeError, match="blew up"):
        _drive(app, _http_scope(path="/mcp"))
    assert _sample("mcp_http_requests_total", labels) == before + 1


def test_metrics_middleware_forwards_non_http_scopes():
    """Lifespan events bypass the metrics layer untouched."""
    seen = []

    async def downstream(scope, receive, send):
        seen.append(scope["type"])

    app = observability.MetricsMiddleware(downstream)

    async def go():
        await app({"type": "lifespan"}, None, None)

    asyncio.run(go())
    assert seen == ["lifespan"]


# ─── mcp_auth_failures_total ─────────────────────────────────────────────────


def test_static_token_rejection_increments_auth_failures():
    """The dev-mode bearer middleware counts its 401s."""
    middleware = transport.BearerTokenMiddleware(_ok_downstream, "right")
    labels = {"reason": "invalid_static_token"}
    before = _sample("mcp_auth_failures_total", labels)
    sent = _drive(
        middleware,
        _http_scope(headers=[(b"authorization", b"Bearer wrong")]),
    )
    assert sent[0]["status"] == 401
    assert _sample("mcp_auth_failures_total", labels) == before + 1


def test_oauth_rejection_increments_auth_failures_by_reason():
    """The OAuth middleware counts failures under their reason code."""
    config = oauth.OAuthConfig(
        issuer="https://auth.example.test",
        audience="https://mcp.example.test/mcp",
        jwks_url="http://127.0.0.1:9/jwks.json",
    )
    middleware = oauth.OAuthResourceMiddleware(
        _ok_downstream, oauth.JWTVerifier(config), config
    )
    labels = {"reason": "missing_bearer"}
    before = _sample("mcp_auth_failures_total", labels)
    sent = _drive(middleware, _http_scope())
    assert sent[0]["status"] == 401
    assert _sample("mcp_auth_failures_total", labels) == before + 1


# ─── Integration: the real HTTP stack ────────────────────────────────────────


def test_metrics_endpoint_is_reachable_without_bearer(http_server):
    """/metrics needs no Authorization header on the real stack."""
    base = http_server.url.rsplit("/mcp", 1)[0]
    response = httpx.get(f"{base}/metrics")
    assert response.status_code == 200
    assert "mcp_http_requests_total" in response.text


def test_rejected_http_request_is_counted(http_server):
    """A 401 on the real stack lands in mcp_http_requests_total."""
    labels = {"path": "/mcp", "status": "401"}
    before = _sample("mcp_http_requests_total", labels)
    response = httpx.get(http_server.url)  # no Authorization
    assert response.status_code == 401
    assert _sample("mcp_http_requests_total", labels) == before + 1


def test_full_tool_call_over_http_records_tool_metrics(http_server):
    """An authed MCP tool call shows up in the tool metrics."""
    import json

    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    labels = {"tool": "validate_identifier", "outcome": "success"}
    before = _sample("mcp_tool_invocations_total", labels)
    latency_before = _sample(
        "mcp_tool_latency_seconds_count", {"tool": "validate_identifier"}
    )

    async def call():
        headers = {"Authorization": f"Bearer {http_server.token}"}
        async with httpx.AsyncClient(headers=headers, timeout=30) as client:
            async with streamable_http_client(
                http_server.url, http_client=client
            ) as (read_stream, write_stream, _):
                async with ClientSession(read_stream, write_stream) as sess:
                    await sess.initialize()
                    result = await sess.call_tool(
                        "validate_identifier",
                        {"kind": "bic", "value": "NWBKGB2LXXX"},
                    )
        assert not result.isError
        return json.loads(result.content[0].text)

    payload = asyncio.run(call())
    assert payload["valid"] is True
    assert _sample("mcp_tool_invocations_total", labels) == before + 1
    assert (
        _sample(
            "mcp_tool_latency_seconds_count", {"tool": "validate_identifier"}
        )
        == latency_before + 1
    )
