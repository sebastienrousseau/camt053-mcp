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

"""Tests for the D7 HTTP transport, bearer auth, and tenant scoping (#42).

Covers, per the issue's acceptance criteria:

* stdio still works without any auth (the default, unchanged);
* HTTP requires a bearer token (server refuses to start without one);
* HTTP rejects requests with a missing or wrong bearer;
* the optional ``Camt053-Account`` tenant header round-trips into the
  tool-visible context (``get_tenant_context``);
* audit records carry service name + tenant scope.

The unit tests exercise every branch deterministically in-process; the
integration tests drive the real stack (uvicorn + middleware + FastMCP
streamable HTTP + MCP client SDK) via the session-scoped
``http_server`` fixture.
"""

import asyncio
import json
import logging
from types import SimpleNamespace

import httpx
import pytest

pytest.importorskip("mcp")

from mcp import ClientSession  # noqa: E402
from mcp.client.streamable_http import streamable_http_client  # noqa: E402

import camt053_mcp.server as server  # noqa: E402
from camt053_mcp import transport  # noqa: E402


def _fake_ctx(request):
    """Build a minimal stand-in for a FastMCP Context wrapping ``request``."""
    return SimpleNamespace(request_context=SimpleNamespace(request=request))


def _fake_http_request(headers):
    """Build a minimal stand-in for a Starlette request with ``headers``."""
    return SimpleNamespace(headers=headers)


# ─── parse_bind ──────────────────────────────────────────────────────────────


def test_parse_bind_splits_host_and_port():
    """A well-formed HOST:PORT string parses into its two parts."""
    assert transport.parse_bind("0.0.0.0:8080") == ("0.0.0.0", 8080)
    assert transport.parse_bind("localhost:1") == ("localhost", 1)


def test_parse_bind_rejects_missing_colon():
    """A bind string without a HOST:PORT separator is refused."""
    with pytest.raises(ValueError, match="HOST:PORT"):
        transport.parse_bind("8080")


def test_parse_bind_rejects_empty_host():
    """A bind string with an empty host is refused."""
    with pytest.raises(ValueError, match="HOST:PORT"):
        transport.parse_bind(":8080")


def test_parse_bind_rejects_non_integer_port():
    """A non-numeric port is refused with a clear message."""
    with pytest.raises(ValueError, match="integer"):
        transport.parse_bind("127.0.0.1:http")


def test_parse_bind_rejects_out_of_range_port():
    """A port outside 0..65535 is refused."""
    with pytest.raises(ValueError, match="0..65535"):
        transport.parse_bind("127.0.0.1:65536")


# ─── audit_event ─────────────────────────────────────────────────────────────


def test_audit_event_carries_service_name_and_scope(caplog):
    """Every audit record is attributable: service name + tenant scope."""
    with caplog.at_level(logging.INFO, logger="camt053_mcp.audit"):
        record = transport.audit_event(
            "http.request.authorized", "acme-treasury", path="/mcp"
        )
    assert record["service"] == "camt053-mcp"
    assert record["scope"] == "acme-treasury"
    assert record["event"] == "http.request.authorized"
    assert record["path"] == "/mcp"
    assert record["timestamp_utc"].endswith("Z")
    # The same record went to the audit logger as one JSON line.
    assert len(caplog.records) == 1
    assert json.loads(caplog.records[0].getMessage()) == record


def test_audit_event_unscoped_calls_get_dash_scope():
    """A call without a tenant is logged with the '-' placeholder scope."""
    record = transport.audit_event("http.server.starting", None)
    assert record["scope"] == "-"


# ─── current_tenant ──────────────────────────────────────────────────────────


def test_current_tenant_is_none_by_default():
    """Outside any request (e.g. plain stdio) there is no tenant."""
    assert transport.current_tenant() is None
    assert transport.current_tenant(None) is None


def test_current_tenant_reads_header_from_request_context():
    """The Camt053-Account header on the bound HTTP request wins."""
    ctx = _fake_ctx(
        _fake_http_request({transport.TENANT_HEADER: "acme-treasury"})
    )
    assert transport.current_tenant(ctx) == "acme-treasury"


def test_current_tenant_falls_back_to_contextvar():
    """With no header on the request, the middleware's contextvar is used."""
    token = transport._tenant_var.set("var-tenant")
    try:
        # No request at all (stdio-shaped context).
        assert transport.current_tenant(_fake_ctx(None)) == "var-tenant"
        # A request whose header is absent/empty.
        ctx = _fake_ctx(_fake_http_request({}))
        assert transport.current_tenant(ctx) == "var-tenant"
    finally:
        transport._tenant_var.reset(token)


# ─── BearerTokenMiddleware (in-process ASGI) ─────────────────────────────────


def _http_scope(headers):
    """Build an ASGI HTTP scope with the given header dict."""
    return {
        "type": "http",
        "method": "POST",
        "path": "/mcp",
        "headers": [
            (k.lower().encode(), v.encode()) for k, v in headers.items()
        ],
    }


def _run_middleware(headers, token="good-token"):
    """Drive the middleware once; return (sent messages, downstream calls)."""
    sent = []
    downstream_calls = []

    async def downstream(scope, receive, send):
        downstream_calls.append(transport.current_tenant())
        await send({"type": "http.response.start", "status": 200})
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = transport.BearerTokenMiddleware(downstream, token)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    asyncio.run(_run_middleware_async(middleware, headers, receive, send))
    return sent, downstream_calls


async def _run_middleware_async(middleware, headers, receive, send):
    """Await one middleware dispatch for ``headers``."""
    await middleware(_http_scope(headers), receive, send)


def test_middleware_rejects_missing_bearer():
    """A request with no Authorization header is rejected 401."""
    sent, downstream = _run_middleware({})
    assert downstream == []
    start = sent[0]
    assert start["status"] == 401
    headers = dict(start["headers"])
    assert headers[b"www-authenticate"] == b"Bearer"
    body = json.loads(sent[1]["body"])
    assert "Unauthorized" in body["error"]


def test_middleware_rejects_wrong_bearer():
    """A request with the wrong bearer token is rejected 401."""
    sent, downstream = _run_middleware({"Authorization": "Bearer wrong-token"})
    assert downstream == []
    assert sent[0]["status"] == 401


def test_middleware_passes_correct_bearer_and_sets_tenant():
    """A correct bearer reaches the app with the tenant contextvar set."""
    sent, downstream = _run_middleware(
        {
            "Authorization": "Bearer good-token",
            "Camt053-Account": "acme-treasury",
        }
    )
    assert downstream == ["acme-treasury"]
    assert sent[0]["status"] == 200
    # The tenant is scoped to the request: reset afterwards.
    assert transport.current_tenant() is None


def test_middleware_correct_bearer_without_tenant_header():
    """A correct bearer with no tenant header yields an unscoped call."""
    sent, downstream = _run_middleware({"Authorization": "Bearer good-token"})
    assert downstream == [None]
    assert sent[0]["status"] == 200


def test_middleware_forwards_non_http_scopes():
    """Lifespan (non-HTTP) events bypass auth and reach the app."""
    seen = []

    async def downstream(scope, receive, send):
        seen.append(scope["type"])

    middleware = transport.BearerTokenMiddleware(downstream, "good-token")

    async def go():
        await middleware({"type": "lifespan"}, None, None)

    asyncio.run(go())
    assert seen == ["lifespan"]


def test_middleware_audits_rejects_and_accepts(caplog):
    """Both auth outcomes land in the audit log with service + scope."""
    with caplog.at_level(logging.INFO, logger="camt053_mcp.audit"):
        _run_middleware({"Camt053-Account": "acme-treasury"})
        _run_middleware(
            {
                "Authorization": "Bearer good-token",
                "Camt053-Account": "acme-treasury",
            }
        )
    events = [json.loads(r.getMessage()) for r in caplog.records]
    kinds = {e["event"] for e in events}
    assert "http.request.rejected" in kinds
    assert "http.request.authorized" in kinds
    assert all(e["service"] == "camt053-mcp" for e in events)
    assert all(e["scope"] == "acme-treasury" for e in events)


# ─── build_http_app / run_http ───────────────────────────────────────────────


def test_build_http_app_wraps_streamable_http_in_auth(monkeypatch):
    """The HTTP app is the streamable app behind auth behind metrics."""
    from camt053_mcp import observability

    sentinel = object()
    fake_server = SimpleNamespace(streamable_http_app=lambda: sentinel)
    app = transport.build_http_app(fake_server, "tok")
    assert isinstance(app, observability.MetricsMiddleware)
    assert isinstance(app._app, transport.BearerTokenMiddleware)
    assert app._app._app is sentinel
    assert app._app._token == "tok"


def test_run_http_refuses_to_start_without_token(monkeypatch):
    """Without CAMT053_MCP_TOKEN the HTTP transport refuses to start."""
    monkeypatch.delenv(transport.TOKEN_ENV, raising=False)
    with pytest.raises(SystemExit, match=transport.TOKEN_ENV):
        transport.run_http(server.server, "127.0.0.1:0")


def test_run_http_serves_wrapped_app_via_uvicorn(monkeypatch):
    """With a token, run_http hands the authed app to uvicorn on the bind."""
    calls = {}

    def fake_run(app, host, port, log_level):
        calls.update(app=app, host=host, port=port, log_level=log_level)

    monkeypatch.setattr(transport.uvicorn, "run", fake_run)
    monkeypatch.setenv(transport.TOKEN_ENV, "env-token")
    fake_server = SimpleNamespace(streamable_http_app=lambda: object())
    transport.run_http(fake_server, "127.0.0.1:8123")
    assert isinstance(calls["app"]._app, transport.BearerTokenMiddleware)
    assert calls["app"]._app._token == "env-token"
    assert (calls["host"], calls["port"]) == ("127.0.0.1", 8123)


def test_run_http_explicit_token_overrides_env(monkeypatch):
    """An explicitly passed token wins over the environment variable."""
    calls = {}
    monkeypatch.setattr(
        transport.uvicorn,
        "run",
        lambda app, **kw: calls.update(app=app),
    )
    monkeypatch.setenv(transport.TOKEN_ENV, "env-token")
    fake_server = SimpleNamespace(streamable_http_app=lambda: object())
    transport.run_http(fake_server, "127.0.0.1:8123", token="explicit")
    assert calls["app"]._app._token == "explicit"


# ─── CLI flag plumbing ───────────────────────────────────────────────────────


def test_cli_defaults_to_stdio_without_auth(monkeypatch):
    """`camt053-mcp` with no flags serves stdio and needs no token."""
    monkeypatch.delenv(transport.TOKEN_ENV, raising=False)
    calls = []
    monkeypatch.setattr(
        server.server, "run", lambda *a, **k: calls.append(True)
    )
    assert server.main([]) is None
    assert calls == [True]


def test_cli_explicit_stdio_matches_default(monkeypatch):
    """`--transport=stdio` behaves exactly like the default."""
    calls = []
    monkeypatch.setattr(
        server.server, "run", lambda *a, **k: calls.append(True)
    )
    assert server.main(["--transport=stdio"]) is None
    assert calls == [True]


def test_cli_http_dispatches_to_run_http(monkeypatch):
    """`--transport=http --bind=...` hands off to transport.run_http."""
    calls = {}
    monkeypatch.setattr(
        server._transport,
        "run_http",
        lambda mcp_server, bind: calls.update(server=mcp_server, bind=bind),
    )
    server.main(["--transport=http", "--bind=0.0.0.0:8080"])
    assert calls["server"] is server.server
    assert calls["bind"] == "0.0.0.0:8080"


def test_cli_http_default_bind_is_loopback(monkeypatch):
    """`--transport=http` without --bind defaults to loopback:8080."""
    calls = {}
    monkeypatch.setattr(
        server._transport,
        "run_http",
        lambda mcp_server, bind: calls.update(bind=bind),
    )
    server.main(["--transport=http"])
    assert calls["bind"] == transport.DEFAULT_BIND == "127.0.0.1:8080"


def test_cli_rejects_unknown_transport():
    """An unsupported --transport value is refused by argparse."""
    with pytest.raises(SystemExit):
        server.main(["--transport=carrier-pigeon"])


# ─── get_tenant_context tool (stdio-shaped, in-process) ──────────────────────


def test_get_tenant_context_over_stdio_is_unscoped():
    """Over stdio (no HTTP request) the tool reports tenant None."""
    result = server.get_tenant_context(_fake_ctx(None))
    assert result == {"service": "camt053-mcp", "tenant": None}


def test_get_tenant_context_reads_tenant_header():
    """The tool surfaces the Camt053-Account header as the tenant."""
    ctx = _fake_ctx(
        _fake_http_request({transport.TENANT_HEADER: "acme-treasury"})
    )
    result = server.get_tenant_context(ctx)
    assert result == {"service": "camt053-mcp", "tenant": "acme-treasury"}


def test_get_tenant_context_audits_service_and_scope(caplog):
    """Each tenant-context call is written to the audit log."""
    ctx = _fake_ctx(_fake_http_request({transport.TENANT_HEADER: "t-1"}))
    with caplog.at_level(logging.INFO, logger="camt053_mcp.audit"):
        server.get_tenant_context(ctx)
    events = [json.loads(r.getMessage()) for r in caplog.records]
    assert events[-1]["event"] == "tool.get_tenant_context"
    assert events[-1]["service"] == "camt053-mcp"
    assert events[-1]["scope"] == "t-1"


# ─── Integration: the real HTTP stack ────────────────────────────────────────


def test_http_missing_bearer_is_401(http_server):
    """An HTTP request without Authorization is rejected 401."""
    response = httpx.post(
        http_server.url,
        json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
    )
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert "Unauthorized" in response.json()["error"]


def test_http_wrong_bearer_is_401(http_server):
    """An HTTP request with the wrong bearer token is rejected 401."""
    response = httpx.get(
        http_server.url,
        headers={"Authorization": "Bearer not-the-token"},
    )
    assert response.status_code == 401


async def _call_tool_over_http(url, headers, tool_name, arguments):
    """Initialize an MCP session over HTTP and call one tool."""
    async with httpx.AsyncClient(headers=headers, timeout=30) as client:
        async with streamable_http_client(url, http_client=client) as (
            read_stream,
            write_stream,
            _,
        ):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
    assert not result.isError
    return json.loads(result.content[0].text)


async def _call_get_tenant_context(url, headers):
    """Initialize an MCP session over HTTP and call get_tenant_context."""
    return await _call_tool_over_http(url, headers, "get_tenant_context", {})


def test_http_correct_bearer_passes_and_tenant_round_trips(http_server):
    """The Camt053-Account header round-trips into the tool context."""
    payload = asyncio.run(
        _call_get_tenant_context(
            http_server.url,
            {
                "Authorization": f"Bearer {http_server.token}",
                "Camt053-Account": "acme-treasury",
            },
        )
    )
    assert payload == {"service": "camt053-mcp", "tenant": "acme-treasury"}


def test_http_correct_bearer_without_tenant_is_unscoped(http_server):
    """Authorized calls without the tenant header report tenant None."""
    payload = asyncio.run(
        _call_get_tenant_context(
            http_server.url,
            {"Authorization": f"Bearer {http_server.token}"},
        )
    )
    assert payload == {"service": "camt053-mcp", "tenant": None}


def test_http_full_tool_call_works_when_authorized(http_server):
    """A regular domain tool works end-to-end over authed HTTP."""
    payload = asyncio.run(
        _call_tool_over_http(
            http_server.url,
            {"Authorization": f"Bearer {http_server.token}"},
            "validate_identifier",
            {"kind": "bic", "value": "NWBKGB2LXXX"},
        )
    )
    assert payload["valid"] is True
