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

"""HTTP transport, bearer-token auth, and tenant scoping (D7, #42 / #17).

The Camt053 MCP server speaks stdio by default -- launched by a local
MCP client, one process per operator, no authentication surface. D7
adds an opt-in **streamable-HTTP** transport for shared, multi-tenant
deployments::

    CAMT053_MCP_TOKEN=s3cret camt053-mcp --transport=http --bind=0.0.0.0:8080

Three concerns live here, deliberately outside :mod:`camt053_mcp.server`
so the stdio path never imports or executes any of it:

* **Bearer-token middleware.** :class:`BearerTokenMiddleware` is a pure
  ASGI wrapper around FastMCP's streamable-HTTP Starlette app. Every
  HTTP request must carry ``Authorization: Bearer <token>`` matching
  the ``CAMT053_MCP_TOKEN`` environment variable (compared with
  :func:`hmac.compare_digest` to avoid timing leaks); anything else is
  rejected ``401`` before it reaches the MCP session manager. stdio is
  intentionally untouched -- no token is required there. The static
  token is **dev-mode auth**: when any ``CAMT053_MCP_OAUTH_*``
  variable is set, :mod:`camt053_mcp.oauth` supersedes it with OAuth
  2.1 resource-server JWT validation (RFC 9728), and starting with
  only the static token logs an explicit dev-mode warning.
* **Tenant scoping.** HTTP callers may send an optional
  ``Camt053-Account`` header naming the tenant/account scope of the
  call. The middleware forwards it into a :class:`~contextvars.\
ContextVar`, and :func:`current_tenant` resolves it for tools --
  preferring the live HTTP request bound to the FastMCP
  :class:`~mcp.server.fastmcp.Context` (which survives task hops inside
  the MCP session manager), falling back to the context variable, and
  yielding ``None`` on stdio.
* **Audit attribution.** :func:`audit_event` emits one structured JSON
  record per transport event on the ``camt053_mcp.audit`` logger,
  always carrying the **service name** (``camt053-mcp``) and the
  tenant **scope**, so multi-tenant calls are attributable in the
  operator's log pipeline (the same append-only sink the wider camt053
  suite's hash-chain audit log feeds).

Two audit extensions unify this log with the core library's
tamper-evident HMAC chain:

* **Hash-chaining.** When ``CAMT053_AUDIT_HMAC_KEY`` is set, every
  audit record is appended to a :class:`camt053.audit.HashChain`
  keyed from that secret and the *chain event* (sequence, prev_hash,
  hmac, payload) is logged instead of the bare record, so the log can
  be verified end-to-end with :func:`camt053.audit.verify_chain` and
  any tampered line breaks verification. Unset key: chaining is
  disabled and the plain attribution records are logged exactly as
  before.
* **Tool-invocation linkage.** :func:`audit_tool_invocation` (called
  from the instrumented tool dispatcher) logs one ``tool.invoked``
  record per MCP tool call carrying the streamable-HTTP session id,
  the JSON-RPC request id, the tool name, the tenant scope, the
  outcome, and the call arguments **redacted** with the core
  library's :func:`camt053.logging.redact_value` rules (recursively,
  lists included) and truncated to a bounded preview -- linking every
  session to the exact (redacted) arguments it sent.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import threading
from collections.abc import Mapping
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import uvicorn
from camt053.audit import HashChain
from camt053.logging import redact_value
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from camt053_mcp import observability

if TYPE_CHECKING:  # pragma: no cover
    from mcp.server.fastmcp import Context, FastMCP

__all__ = [
    "AUDIT_HMAC_KEY_ENV",
    "DEFAULT_BIND",
    "SERVICE_NAME",
    "TENANT_HEADER",
    "TOKEN_ENV",
    "BearerTokenMiddleware",
    "audit_event",
    "audit_tool_invocation",
    "build_http_app",
    "current_tenant",
    "parse_bind",
    "redacted_arguments",
    "run_http",
]

#: The service name stamped on every audit record so multi-tenant calls
#: are attributable to this server among the suite's other services.
SERVICE_NAME = "camt053-mcp"

#: The optional HTTP request header naming the tenant/account scope.
TENANT_HEADER = "Camt053-Account"

#: The environment variable the HTTP transport reads its bearer token
#: from. The string is the variable's *name*, not a credential, hence
#: the targeted B105 suppression.
TOKEN_ENV = "CAMT053_MCP_TOKEN"  # nosec B105

#: The default ``--bind`` for ``--transport=http``: loopback-only, so an
#: operator must opt in explicitly (e.g. ``0.0.0.0:8080``) to expose the
#: server beyond the local host.
DEFAULT_BIND = "127.0.0.1:8080"

#: Structured audit records (one JSON object per line) are emitted here;
#: operators route the ``camt053_mcp.audit`` logger to their append-only
#: audit sink.
_audit_logger = logging.getLogger("camt053_mcp.audit")

#: Operational (non-audit) transport diagnostics -- e.g. the dev-mode
#: auth warning -- go to the module's own logger.
_logger = logging.getLogger(__name__)

#: The tenant scope of the HTTP request currently being served, set by
#: :class:`BearerTokenMiddleware` for the duration of each request.
#: ``None`` outside a request and always ``None`` on stdio.
_tenant_var: ContextVar[str | None] = ContextVar(
    "camt053_mcp_tenant", default=None
)

#: The environment variable holding the HMAC secret that keys the
#: tamper-evident audit chain. Unset / empty: chaining is disabled and
#: plain attribution records are logged. The string is the variable's
#: *name*, not a credential, hence the targeted B105 suppression.
AUDIT_HMAC_KEY_ENV = "CAMT053_AUDIT_HMAC_KEY"  # nosec B105

#: Redacted tool arguments are truncated to this many characters per
#: string value, keeping audit lines bounded even for full-statement
#: XML payloads.
_ARG_PREVIEW_MAX = 256

#: The process-wide audit hash-chain (lazily created from
#: :data:`AUDIT_HMAC_KEY_ENV`) and the key it was built with, so a
#: rotated key starts a fresh chain.
_chain: HashChain | None = None
_chain_key: str | None = None

#: Serialises chain appends: sequence numbers and prev-hash linkage
#: must never interleave across threads.
_chain_lock = threading.Lock()


def _active_chain() -> HashChain | None:
    """Return the audit hash-chain, or ``None`` when chaining is off.

    Reads :data:`AUDIT_HMAC_KEY_ENV` on every call so operators (and
    tests) can enable, rotate, or disable the key at runtime; a
    changed key abandons the old chain and starts a new one at
    sequence zero.
    """
    global _chain, _chain_key
    key = os.environ.get(AUDIT_HMAC_KEY_ENV)
    if not key:
        _chain = None
        _chain_key = None
        return None
    if _chain is None or _chain_key != key:
        _chain = HashChain(secret=key.encode("utf-8"))
        _chain_key = key
    return _chain


def audit_event(
    event_type: str, scope: str | None, **fields: Any
) -> dict[str, Any]:
    """Emit one structured audit record attributing a call to a tenant.

    Every record carries the service name (:data:`SERVICE_NAME`) and the
    tenant ``scope`` (the ``Camt053-Account`` header value, or ``"-"``
    when the caller did not scope itself), so multi-tenant calls are
    attributable in the audit log. The record is logged as a single
    sorted-key JSON line on the ``camt053_mcp.audit`` logger and also
    returned so callers (and tests) can inspect it.

    When :data:`AUDIT_HMAC_KEY_ENV` is set, the record is additionally
    appended to the core library's tamper-evident HMAC hash-chain
    (:class:`camt053.audit.HashChain`) and the logged/returned value
    is the **chain event** -- ``{"sequence", "timestamp_utc",
    "event_type", "payload", "prev_hash", "hmac"}`` with the record as
    ``payload`` -- so the whole log verifies end-to-end with
    :func:`camt053.audit.verify_chain` under the same secret.

    Args:
        event_type: A stable event label, e.g. ``"http.request.rejected"``
            or ``"http.request.authorized"``.
        scope: The tenant scope of the call, or ``None`` for unscoped.
        **fields: Extra JSON-serialisable attributes (path, bind, ...).

    Returns:
        The record that was logged (the chain event when chaining is
        enabled).
    """
    record: dict[str, Any] = {
        "service": SERVICE_NAME,
        "scope": scope or "-",
        "event": event_type,
        "timestamp_utc": datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        **fields,
    }
    chain = _active_chain()
    if chain is not None:
        with _chain_lock:
            chained = chain.append(event_type, payload=record).to_dict()
        _audit_logger.info(json.dumps(chained, sort_keys=True))
        return chained
    _audit_logger.info(json.dumps(record, sort_keys=True))
    return record


def _redact_item(key: str, value: Any) -> Any:
    """Redact one argument value under the core library's rules.

    Delegates leaf values to :func:`camt053.logging.redact_value`
    (exactly the rules ``camt053.logging.redact_context`` applies) and
    extends its mapping recursion to lists/tuples, so IBANs or names
    inside e.g. a ``records`` list are redacted too. List items
    inherit the parent key for rule matching.

    Args:
        key: The argument name the value sits under.
        value: The value to redact.

    Returns:
        The redacted value; containers are rebuilt, never mutated.
    """
    if isinstance(value, Mapping):
        return {k: _redact_item(k, v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_item(key, item) for item in value]
    return redact_value(key, value)


def _truncate_previews(value: Any) -> Any:
    """Bound every string in ``value`` to :data:`_ARG_PREVIEW_MAX`.

    Args:
        value: An already-redacted argument value.

    Returns:
        The value with long strings replaced by a truncated preview
        annotated with the number of characters dropped.
    """
    if isinstance(value, str) and len(value) > _ARG_PREVIEW_MAX:
        dropped = len(value) - _ARG_PREVIEW_MAX
        return value[:_ARG_PREVIEW_MAX] + f"...[truncated {dropped} chars]"
    if isinstance(value, Mapping):
        return {k: _truncate_previews(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_truncate_previews(item) for item in value]
    return value


def redacted_arguments(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Prepare tool-call arguments for the audit log.

    Applies the core library's PII redaction (see :func:`_redact_item`),
    truncates long strings to a bounded preview, and coerces the result
    to plain JSON types (non-serialisable leaves become their ``repr``)
    so the record can always be logged and HMAC-chained.

    Args:
        arguments: The raw JSON-RPC tool arguments.

    Returns:
        A JSON-safe, redacted, size-bounded copy of ``arguments``.
    """
    prepared = {
        key: _truncate_previews(_redact_item(key, value))
        for key, value in dict(arguments).items()
    }
    result: dict[str, Any] = json.loads(
        json.dumps(prepared, sort_keys=True, default=repr)
    )
    return result


def _session_info(ctx: Any) -> tuple[str | None, str | None]:
    """Extract (session id, request id) from a FastMCP context.

    Best effort: over streamable HTTP the MCP session id is the
    ``mcp-session-id`` request header; the request id is the JSON-RPC
    id of the tool call. Over stdio (or outside a request, where the
    SDK raises ``ValueError``) both are ``None``.

    Args:
        ctx: The FastMCP ``Context`` of the running tool, or ``None``.

    Returns:
        The ``(session_id, request_id)`` pair, each ``None`` when
        unavailable.
    """
    if ctx is None:
        return None, None
    try:
        request_context = ctx.request_context
    except (AttributeError, ValueError):
        return None, None
    if request_context is None:
        return None, None
    session_id: str | None = None
    request = getattr(request_context, "request", None)
    if request is not None:
        session_id = request.headers.get("mcp-session-id")
    request_id = getattr(request_context, "request_id", None)
    return session_id, str(request_id) if request_id is not None else None


def audit_tool_invocation(
    tool: str, arguments: Mapping[str, Any], ctx: Any, outcome: str
) -> dict[str, Any]:
    """Audit one MCP tool invocation with session-to-args linkage.

    Emits a ``tool.invoked`` record carrying the MCP session id and
    JSON-RPC request id (from the request context), the tool name, the
    tenant scope, the outcome (``success`` / ``error`` /
    ``exception``), and the call arguments redacted via the core
    library's rules and truncated to a bounded preview. When the audit
    chain is enabled the record is HMAC-chained like every other audit
    event (see :func:`audit_event`).

    Args:
        tool: The invoked tool's name.
        arguments: The raw JSON-RPC tool arguments.
        ctx: The FastMCP ``Context`` of the call, or ``None``.
        outcome: The dispatch outcome label.

    Returns:
        The record that was logged.
    """
    try:
        tenant = current_tenant(ctx)
    except (AttributeError, ValueError):
        tenant = _tenant_var.get()
    session_id, request_id = _session_info(ctx)
    return audit_event(
        "tool.invoked",
        tenant,
        tool=tool,
        session=session_id or "-",
        request_id=request_id or "-",
        arguments=redacted_arguments(arguments),
        outcome=outcome,
    )


def current_tenant(ctx: Context | None = None) -> str | None:
    """Return the tenant scope of the current call, if any.

    Resolution order:

    1. The ``Camt053-Account`` header of the HTTP request bound to the
       FastMCP ``ctx`` (``ctx.request_context.request``), when the call
       arrived over the streamable-HTTP transport. This is the reliable
       path: the MCP session manager may execute a tool in a different
       task than the request handler, so the request object -- not the
       context variable -- is authoritative.
    2. The context variable populated by :class:`BearerTokenMiddleware`.
    3. ``None`` -- e.g. on stdio, where no HTTP headers exist.

    Args:
        ctx: The FastMCP request context of the running tool, when the
            tool has one; ``None`` falls straight to the context variable.
    """
    if ctx is not None:
        request = ctx.request_context.request
        if request is not None:
            tenant = request.headers.get(TENANT_HEADER)
            if tenant:
                return tenant
    return _tenant_var.get()


def parse_bind(bind: str) -> tuple[str, int]:
    """Parse a ``HOST:PORT`` bind string into a ``(host, port)`` pair.

    Args:
        bind: The bind address, e.g. ``"0.0.0.0:8080"``.

    Returns:
        The ``(host, port)`` tuple.

    Raises:
        ValueError: If ``bind`` is not ``HOST:PORT`` with a non-empty
            host and a port in ``0..65535``.
    """
    host, sep, port_text = bind.rpartition(":")
    if not sep or not host:
        raise ValueError(
            f"--bind must be HOST:PORT (e.g. '0.0.0.0:8080'), got {bind!r}"
        )
    try:
        port = int(port_text)
    except ValueError:
        raise ValueError(
            f"--bind port must be an integer, got {port_text!r}"
        ) from None
    if not 0 <= port <= 65535:
        raise ValueError(f"--bind port must be in 0..65535, got {port}")
    return host, port


class BearerTokenMiddleware:
    """Pure ASGI middleware enforcing ``Authorization: Bearer`` auth.

    Wraps FastMCP's streamable-HTTP Starlette app. Every HTTP request
    must present exactly ``Authorization: Bearer <token>``; a missing or
    wrong credential is rejected ``401`` (with ``WWW-Authenticate:
    Bearer``) before reaching the MCP session manager. Authorized
    requests have their optional ``Camt053-Account`` tenant header
    forwarded into the tenant context variable for the duration of the
    request, and every decision -- rejected or authorized -- is written
    to the audit log with service name + tenant scope.

    Implemented as raw ASGI (not Starlette's ``BaseHTTPMiddleware``) so
    the transport's streaming SSE responses pass through untouched.
    Non-HTTP scopes (``lifespan``, ``websocket``) are forwarded as-is.
    """

    def __init__(self, app: ASGIApp, token: str) -> None:
        """Wrap ``app``, requiring ``token`` on every HTTP request.

        Args:
            app: The downstream ASGI application (FastMCP's
                streamable-HTTP Starlette app).
            token: The bearer token every request must present.
        """
        self._app = app
        self._token = token

    async def __call__(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        """Authenticate one ASGI event and dispatch it downstream.

        Args:
            scope: The ASGI connection scope.
            receive: The ASGI receive callable.
            send: The ASGI send callable.
        """
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        tenant = headers.get(TENANT_HEADER)
        supplied = headers.get("Authorization", "")
        expected = f"Bearer {self._token}"
        if not hmac.compare_digest(
            supplied.encode("utf-8"), expected.encode("utf-8")
        ):
            audit_event(
                "http.request.rejected",
                tenant,
                path=scope.get("path", ""),
                reason="missing or invalid bearer token",
            )
            observability.AUTH_FAILURES.labels(
                reason="invalid_static_token"
            ).inc()
            response = JSONResponse(
                {"error": "Unauthorized: missing or invalid bearer token"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return
        audit_event(
            "http.request.authorized", tenant, path=scope.get("path", "")
        )
        reset_token = _tenant_var.set(tenant)
        try:
            await self._app(scope, receive, send)
        finally:
            _tenant_var.reset(reset_token)


def build_http_app(
    mcp_server: FastMCP,
    token: str | None = None,
    oauth_config: Any = None,
) -> ASGIApp:
    """Build the authenticated streamable-HTTP ASGI app.

    Exactly one auth mode applies: when ``oauth_config`` is given the
    app enforces OAuth 2.1 resource-server JWT validation
    (:class:`camt053_mcp.oauth.OAuthResourceMiddleware`, RFC 9728);
    otherwise the static dev-mode ``token`` is required via
    :class:`BearerTokenMiddleware`.

    Args:
        mcp_server: The FastMCP server to expose over HTTP.
        token: The static dev-mode bearer token, when OAuth is not
            configured.
        oauth_config: A :class:`camt053_mcp.oauth.OAuthConfig`; takes
            precedence over ``token``.

    Returns:
        FastMCP's streamable-HTTP Starlette app (MCP endpoint at
        ``/mcp``) wrapped in the selected auth middleware, itself
        wrapped in the observability layer
        (:class:`camt053_mcp.observability.MetricsMiddleware`, which
        also serves ``GET /metrics``); the server's tool dispatcher is
        instrumented for the tool metrics as a side effect.

    Raises:
        ValueError: If neither ``token`` nor ``oauth_config`` is given.
    """
    # Imported here, not at module top: oauth.py imports this module's
    # audit/tenant primitives, so the top-level import would be a cycle.
    from camt053_mcp import oauth as _oauth

    observability.instrument_tools(mcp_server)
    inner = mcp_server.streamable_http_app()
    authed: ASGIApp
    if oauth_config is not None:
        authed = _oauth.OAuthResourceMiddleware(
            inner, _oauth.JWTVerifier(oauth_config), oauth_config
        )
    elif token:
        authed = BearerTokenMiddleware(inner, token)
    else:
        raise ValueError(
            "build_http_app requires a static token or an OAuth config"
        )
    return observability.MetricsMiddleware(authed)


def run_http(mcp_server: FastMCP, bind: str, token: str | None = None) -> None:
    """Serve the MCP server over authenticated streamable HTTP.

    Blocks until the process is stopped. Auth is resolved from the
    environment, strongest first:

    1. **OAuth 2.1 resource server** -- when the
       ``CAMT053_MCP_OAUTH_*`` variables are set (see
       :mod:`camt053_mcp.oauth`), bearer JWTs are validated against
       the configured issuer / audience / JWKS. A static token set
       alongside is ignored (with a warning): the weaker credential
       never widens access.
    2. **Static dev-mode token** -- the :data:`TOKEN_ENV` environment
       variable (``CAMT053_MCP_TOKEN``) unless supplied explicitly.
       An explicit warning marks this as dev-mode auth.

    Starting with neither is refused rather than silently serving an
    unauthenticated multi-tenant endpoint.

    Args:
        mcp_server: The FastMCP server to expose.
        bind: The ``HOST:PORT`` to listen on (see :func:`parse_bind`).
        token: The static bearer token; ``None`` reads
            :data:`TOKEN_ENV`.

    Raises:
        SystemExit: If neither OAuth nor a static token is configured,
            or the OAuth configuration is partial.
        ValueError: If ``bind`` is malformed.
    """
    from camt053_mcp import oauth as _oauth

    host, port = parse_bind(bind)
    oauth_config = _oauth.OAuthConfig.from_env()
    if token is None:
        token = os.environ.get(TOKEN_ENV)
    if oauth_config is not None:
        if token:
            _logger.warning(
                "Both OAuth (%s) and the static token (%s) are set; "
                "OAuth wins and the static token is IGNORED.",
                _oauth.OAUTH_ISSUER_ENV,
                TOKEN_ENV,
            )
        app = build_http_app(mcp_server, oauth_config=oauth_config)
        auth_mode = "oauth"
    elif token:
        _logger.warning(
            "HTTP transport is using the static %s bearer token -- "
            "DEV-MODE auth (single shared secret, no expiry, no "
            "scopes). Configure %s / %s for OAuth 2.1 in production.",
            TOKEN_ENV,
            _oauth.OAUTH_ISSUER_ENV,
            _oauth.OAUTH_AUDIENCE_ENV,
        )
        app = build_http_app(mcp_server, token=token)
        auth_mode = "static-token"
    else:
        raise SystemExit(
            f"--transport=http requires auth: set {TOKEN_ENV} to a "
            "non-empty secret (dev mode; every HTTP request must then "
            "send 'Authorization: Bearer <secret>'), or configure "
            f"OAuth 2.1 via {_oauth.OAUTH_ISSUER_ENV} and "
            f"{_oauth.OAUTH_AUDIENCE_ENV}."
        )
    audit_event(
        "http.server.starting", None, bind=f"{host}:{port}", auth=auth_mode
    )
    uvicorn.run(app, host=host, port=port, log_level="info")
