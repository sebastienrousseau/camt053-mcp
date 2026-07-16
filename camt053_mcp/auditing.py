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

"""Audit attribution, tenant context, and the tamper-evident chain.

The base layer of the HTTP-transport stack: this module has **no**
imports from the rest of :mod:`camt053_mcp`, so the transport
(:mod:`camt053_mcp.transport`), auth (:mod:`camt053_mcp.oauth`), and
observability (:mod:`camt053_mcp.observability`) modules can all
depend on it without import cycles::

    auditing  <-  observability  <-  oauth  <-  transport

Three concerns live here:

* **Audit attribution.** :func:`audit_event` emits one structured JSON
  record per event on the ``camt053_mcp.audit`` logger, always
  carrying the **service name** (``camt053-mcp``) and the tenant
  **scope**, so multi-tenant calls are attributable in the operator's
  log pipeline.
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

Tenant context also lives here (:data:`_tenant_var`,
:func:`current_tenant`): the auth middlewares set the context
variable per request, and tools resolve it -- preferring the live
HTTP request bound to the FastMCP ``Context``, which survives task
hops inside the MCP session manager.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Mapping
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from camt053.audit import HashChain
from camt053.logging import redact_value

if TYPE_CHECKING:  # pragma: no cover
    from mcp.server.fastmcp import Context

__all__ = [
    "AUDIT_HMAC_KEY_ENV",
    "SERVICE_NAME",
    "TENANT_HEADER",
    "audit_event",
    "audit_tool_invocation",
    "current_tenant",
    "redacted_arguments",
]

#: The service name stamped on every audit record so multi-tenant calls
#: are attributable to this server among the suite's other services.
SERVICE_NAME = "camt053-mcp"

#: The optional HTTP request header naming the tenant/account scope.
TENANT_HEADER = "Camt053-Account"

#: The environment variable holding the HMAC secret that keys the
#: tamper-evident audit chain. Unset / empty: chaining is disabled and
#: plain attribution records are logged. The string is the variable's
#: *name*, not a credential, hence the targeted B105 suppression.
AUDIT_HMAC_KEY_ENV = "CAMT053_AUDIT_HMAC_KEY"  # nosec B105

#: Redacted tool arguments are truncated to this many characters per
#: string value, keeping audit lines bounded even for full-statement
#: XML payloads.
_ARG_PREVIEW_MAX = 256

#: Structured audit records (one JSON object per line) are emitted here;
#: operators route the ``camt053_mcp.audit`` logger to their append-only
#: audit sink.
_audit_logger = logging.getLogger("camt053_mcp.audit")

#: The tenant scope of the HTTP request currently being served, set by
#: the auth middlewares for the duration of each request. ``None``
#: outside a request and always ``None`` on stdio.
_tenant_var: ContextVar[str | None] = ContextVar(
    "camt053_mcp_tenant", default=None
)


class _ChainState:
    """Holds the lazily-created audit hash-chain and its key.

    A small mutable holder (rather than module globals) so
    :func:`_active_chain` can rebuild or drop the chain on key
    rotation without ``global`` statements. The lock serialises
    chain appends: sequence numbers and prev-hash linkage must never
    interleave across threads.
    """

    def __init__(self) -> None:
        """Start with chaining disabled."""
        self.chain: HashChain | None = None
        self.key: str | None = None
        self.lock = threading.Lock()


#: The process-wide chain state, keyed from :data:`AUDIT_HMAC_KEY_ENV`.
_chain_state = _ChainState()


def _active_chain() -> HashChain | None:
    """Return the audit hash-chain, or ``None`` when chaining is off.

    Reads :data:`AUDIT_HMAC_KEY_ENV` on every call so operators (and
    tests) can enable, rotate, or disable the key at runtime; a
    changed key abandons the old chain and starts a new one at
    sequence zero.
    """
    key = os.environ.get(AUDIT_HMAC_KEY_ENV)
    if not key:
        _chain_state.chain = None
        _chain_state.key = None
        return None
    if _chain_state.chain is None or _chain_state.key != key:
        _chain_state.chain = HashChain(secret=key.encode("utf-8"))
        _chain_state.key = key
    return _chain_state.chain


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
        with _chain_state.lock:
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
    2. The context variable populated by the auth middlewares.
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
