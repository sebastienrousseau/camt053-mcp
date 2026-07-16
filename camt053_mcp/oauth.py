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

"""OAuth 2.1 resource-server auth for the HTTP transport (RFC 9728).

The HTTP transport's original credential -- a single static bearer
token compared byte-for-byte (``CAMT053_MCP_TOKEN``) -- remains
available as an explicit **dev-mode fallback**, but production
deployments should point the server at an OAuth 2.1 authorization
server instead::

    CAMT053_MCP_OAUTH_ISSUER=https://auth.example.com \\
    CAMT053_MCP_OAUTH_AUDIENCE=https://mcp.example.com/mcp \\
    camt053-mcp --transport=http --bind=0.0.0.0:8080

Three pieces live here:

* :class:`OAuthConfig` -- the resource-server configuration, read from
  the ``CAMT053_MCP_OAUTH_*`` environment variables. ``ISSUER`` and
  ``AUDIENCE`` (the canonical resource URI per RFC 8707) are required
  together; ``JWKS_URL`` defaults to the issuer's
  ``/.well-known/jwks.json``; ``SCOPES`` optionally lists scopes every
  token must carry (e.g. ``camt053:read``).
* :class:`JWTVerifier` -- validates ``Authorization: Bearer`` JWTs:
  signature via the (cached) JWKS, ``iss``, ``aud``, ``exp`` / ``nbf``,
  and the optional required scopes. It implements the MCP SDK's
  :class:`mcp.server.auth.provider.TokenVerifier` protocol and returns
  the SDK's :class:`~mcp.server.auth.provider.AccessToken` model, so it
  can be reused anywhere the SDK expects a verifier.
* :class:`OAuthResourceMiddleware` -- the ASGI wrapper enforcing the
  above on every HTTP request, rejecting failures ``401`` (``403`` for
  ``insufficient_scope``, per RFC 6750 §3.1) with a
  ``WWW-Authenticate`` challenge carrying the ``resource_metadata``
  URL per RFC 9728 §5.1, and serving the RFC 9728 protected-resource
  metadata document itself on ``GET /.well-known/oauth-protected-\\
resource`` (both the bare path and the audience-derived variant).

Why the middleware is hand-wired rather than FastMCP's constructor
auth: the server instance is a module-level singleton created long
before the transport is chosen (stdio never needs auth), and the
transport layer needs per-path exemptions (metadata, metrics) plus
tenant/audit integration. The SDK's *primitives* -- the
``TokenVerifier`` protocol and ``AccessToken`` model -- are reused;
only the wiring and the JWT/JWKS validation (which the SDK does not
provide) are implemented here. The RFC 9728 metadata document is
built by hand instead of via the SDK's ``ProtectedResourceMetadata``
model because that model round-trips URLs through pydantic's
``AnyHttpUrl``, which appends a trailing slash to bare-origin URLs --
and RFC 8707 resource identifiers are compared as exact strings, so
the operator-supplied ``resource`` / ``issuer`` values must be echoed
verbatim.
"""

from __future__ import annotations

import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx
import jwt
from mcp.server.auth.provider import AccessToken
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from camt053_mcp import observability
from camt053_mcp.auditing import TENANT_HEADER, _tenant_var, audit_event

__all__ = [
    "OAUTH_AUDIENCE_ENV",
    "OAUTH_ISSUER_ENV",
    "OAUTH_JWKS_URL_ENV",
    "OAUTH_SCOPES_ENV",
    "WELL_KNOWN_PATH",
    "JWKSCache",
    "JWTVerifier",
    "OAuthConfig",
    "OAuthResourceMiddleware",
    "TokenValidationError",
    "protected_resource_metadata",
    "resource_metadata_url",
]

#: Environment variable naming the OAuth 2.1 authorization server
#: (the JWT ``iss`` claim must match it exactly).
OAUTH_ISSUER_ENV = "CAMT053_MCP_OAUTH_ISSUER"

#: Environment variable naming this resource server's canonical
#: resource URI (RFC 8707); the JWT ``aud`` claim must contain it.
OAUTH_AUDIENCE_ENV = "CAMT053_MCP_OAUTH_AUDIENCE"

#: Environment variable overriding the JWKS document URL. When unset,
#: ``<issuer>/.well-known/jwks.json`` is used.
OAUTH_JWKS_URL_ENV = "CAMT053_MCP_OAUTH_JWKS_URL"

#: Environment variable listing space-separated scopes every token
#: must carry (e.g. ``"camt053:read"``). Unset / empty: no scope gate.
OAUTH_SCOPES_ENV = "CAMT053_MCP_OAUTH_SCOPES"

#: The RFC 9728 §3 well-known path for protected-resource metadata.
WELL_KNOWN_PATH = "/.well-known/oauth-protected-resource"

#: Clock-skew tolerance (seconds) applied to ``exp`` / ``nbf`` checks.
CLOCK_SKEW_LEEWAY_S = 30

#: How long (seconds) fetched JWKS keys are served from cache before
#: the JWKS URL is consulted again. An unknown ``kid`` always triggers
#: one refresh (key rotation) regardless of age.
JWKS_CACHE_TTL_S = 300.0


class TokenValidationError(Exception):
    """A bearer JWT failed validation.

    Attributes:
        reason: A short stable failure code (e.g. ``"token_expired"``,
            ``"issuer_mismatch"``, ``"insufficient_scope"``) used for
            audit records, metrics labels, and the RFC 6750 challenge.
        description: A human-readable detail string, safe to return to
            the caller (never echoes the token).
    """

    def __init__(self, reason: str, description: str) -> None:
        """Record the failure ``reason`` code and ``description``.

        Args:
            reason: Short stable failure code.
            description: Human-readable detail for the challenge.
        """
        super().__init__(f"{reason}: {description}")
        self.reason = reason
        self.description = description


@dataclass(frozen=True)
class OAuthConfig:
    """Resource-server configuration for OAuth 2.1 JWT validation.

    Attributes:
        issuer: The authorization server's issuer identifier; the JWT
            ``iss`` claim must match it exactly.
        audience: This server's canonical resource URI (RFC 8707); the
            JWT ``aud`` claim must contain it, and it is echoed as
            ``resource`` in the RFC 9728 metadata.
        jwks_url: Where to fetch the JSON Web Key Set used to check
            token signatures.
        required_scopes: Scopes every token must carry; empty means no
            scope gating.
    """

    issuer: str
    audience: str
    jwks_url: str
    required_scopes: tuple[str, ...] = ()

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] | None = None
    ) -> OAuthConfig | None:
        """Read the OAuth configuration from the environment.

        Args:
            environ: The environment mapping to read; ``None`` uses
                ``os.environ``.

        Returns:
            The configuration, or ``None`` when no ``CAMT053_MCP_OAUTH_*``
            variable is set (the caller then falls back to the static
            dev-mode token).

        Raises:
            SystemExit: If the OAuth configuration is partial -- some
                variables set but ``ISSUER`` or ``AUDIENCE`` missing --
                so a typo'd deployment fails loudly instead of silently
                serving with weaker auth.
        """
        env = os.environ if environ is None else environ
        issuer = env.get(OAUTH_ISSUER_ENV, "").strip()
        audience = env.get(OAUTH_AUDIENCE_ENV, "").strip()
        jwks_url = env.get(OAUTH_JWKS_URL_ENV, "").strip()
        scopes = tuple(env.get(OAUTH_SCOPES_ENV, "").split())
        if not (issuer or audience or jwks_url or scopes):
            return None
        if not issuer or not audience:
            raise SystemExit(
                "Partial OAuth configuration: set both "
                f"{OAUTH_ISSUER_ENV} and {OAUTH_AUDIENCE_ENV} (with "
                f"optional {OAUTH_JWKS_URL_ENV} / {OAUTH_SCOPES_ENV}), "
                "or unset all CAMT053_MCP_OAUTH_* variables to use the "
                "static dev-mode token."
            )
        if not jwks_url:
            jwks_url = issuer.rstrip("/") + "/.well-known/jwks.json"
        return cls(
            issuer=issuer,
            audience=audience,
            jwks_url=jwks_url,
            required_scopes=scopes,
        )


def resource_metadata_url(audience: str) -> str:
    """Build the RFC 9728 §3.1 metadata URL for a resource identifier.

    Inserts ``/.well-known/oauth-protected-resource`` between host and
    resource path, e.g. ``https://x.example/mcp`` becomes
    ``https://x.example/.well-known/oauth-protected-resource/mcp``.
    (Same derivation as the MCP SDK's ``build_resource_metadata_url``,
    re-implemented to keep the operator's string un-normalised.)

    Args:
        audience: The canonical resource URI (RFC 8707).

    Returns:
        The absolute metadata URL advertised in ``WWW-Authenticate``.
    """
    parsed = urlparse(audience)
    path = "" if parsed.path in ("", "/") else parsed.path
    return f"{parsed.scheme}://{parsed.netloc}{WELL_KNOWN_PATH}{path}"


def protected_resource_metadata(config: OAuthConfig) -> dict[str, Any]:
    """Build the RFC 9728 §2 protected-resource metadata document.

    Args:
        config: The resource-server configuration.

    Returns:
        The metadata as a JSON-serialisable dict: ``resource``,
        ``authorization_servers``, ``bearer_methods_supported`` and --
        when scope gating is configured -- ``scopes_supported``.
    """
    metadata: dict[str, Any] = {
        "resource": config.audience,
        "authorization_servers": [config.issuer],
        "bearer_methods_supported": ["header"],
    }
    if config.required_scopes:
        metadata["scopes_supported"] = list(config.required_scopes)
    return metadata


class JWKSCache:
    """A TTL cache of JWKS signing keys, fetched with ``httpx``.

    Keys are indexed by ``kid`` and refreshed from the JWKS URL when
    the cache is older than the TTL or when an unknown ``kid`` shows up
    (key rotation). Fetching is async (never blocks the event loop);
    concurrent refreshes are benign -- the fetch is idempotent -- so no
    lock is taken.
    """

    def __init__(
        self, url: str, ttl_seconds: float = JWKS_CACHE_TTL_S
    ) -> None:
        """Create a cache reading from ``url``.

        Args:
            url: The JWKS document URL.
            ttl_seconds: Maximum key age before a routine refresh.
        """
        self._url = url
        self._ttl = ttl_seconds
        self._keys: dict[str, jwt.PyJWK] = {}
        self._fetched_at = float("-inf")

    def _stale(self) -> bool:
        """Return whether the cached keys are older than the TTL."""
        return time.monotonic() - self._fetched_at >= self._ttl

    async def _refresh(self) -> None:
        """Re-fetch the JWKS document and rebuild the key index.

        Raises:
            TokenValidationError: ``jwks_unavailable`` when the JWKS
                URL cannot be fetched or does not parse as a key set.
        """
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(self._url)
                response.raise_for_status()
                document = response.json()
            entries = document["keys"]
            if not isinstance(entries, list):
                raise TypeError("JWKS 'keys' member is not a list")
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise TokenValidationError(
                "jwks_unavailable",
                f"could not fetch JWKS from {self._url}: "
                f"{exc.__class__.__name__}",
            ) from exc
        keys: dict[str, jwt.PyJWK] = {}
        for entry in entries:
            kid = entry.get("kid") if isinstance(entry, dict) else None
            if not kid:
                continue
            try:
                keys[kid] = jwt.PyJWK(entry)
            except (
                jwt.exceptions.PyJWKError,
                jwt.exceptions.InvalidKeyError,
            ):
                continue  # skip unusable entries, keep the good ones
        self._keys = keys
        self._fetched_at = time.monotonic()

    async def get_key(self, kid: str | None) -> jwt.PyJWK:
        """Resolve the signing key for ``kid``.

        Args:
            kid: The JWT header's key id. ``None`` is tolerated only
                when the key set holds exactly one key (common with
                single-key identity providers).

        Returns:
            The matching :class:`jwt.PyJWK`.

        Raises:
            TokenValidationError: ``jwks_unavailable`` on fetch
                failure, ``unknown_kid`` when the key set has no
                matching key, ``missing_kid`` when the token names no
                key and the set is ambiguous.
        """
        if self._stale() or (kid is not None and kid not in self._keys):
            await self._refresh()
        if kid is None:
            if len(self._keys) == 1:
                return next(iter(self._keys.values()))
            raise TokenValidationError(
                "missing_kid",
                "token header has no 'kid' and the JWKS is ambiguous",
            )
        try:
            return self._keys[kid]
        except KeyError:
            raise TokenValidationError(
                "unknown_kid", f"no JWKS key matches kid {kid!r}"
            ) from None


#: Maps PyJWT validation exceptions to stable failure-reason codes.
#: Order matters: subclasses must precede their bases
#: (``InvalidTokenError`` is the catch-all handled separately).
_JWT_ERROR_REASONS: tuple[tuple[type[Exception], str], ...] = (
    (jwt.exceptions.ExpiredSignatureError, "token_expired"),
    (jwt.exceptions.ImmatureSignatureError, "token_not_yet_valid"),
    (jwt.exceptions.InvalidIssuerError, "issuer_mismatch"),
    (jwt.exceptions.InvalidAudienceError, "audience_mismatch"),
    (jwt.exceptions.InvalidSignatureError, "signature_invalid"),
    (jwt.exceptions.MissingRequiredClaimError, "missing_required_claim"),
)


class JWTVerifier:
    """Validates OAuth 2.1 bearer JWTs against the resource config.

    Implements the MCP SDK's ``TokenVerifier`` protocol
    (:meth:`verify_token`) on top of :meth:`verify`, which raises a
    reason-coded :class:`TokenValidationError` so the transport can
    audit *why* a token was rejected.

    Algorithm confusion is prevented structurally: the verification
    algorithm is always taken from the JWKS key itself (``PyJWK.
    algorithm_name``), never from the attacker-controlled token
    header, so ``none`` / HMAC downgrades are impossible with an
    asymmetric key set.
    """

    def __init__(
        self, config: OAuthConfig, jwks: JWKSCache | None = None
    ) -> None:
        """Create a verifier for ``config``.

        Args:
            config: The resource-server configuration.
            jwks: The JWKS cache to resolve signing keys from; ``None``
                builds one from ``config.jwks_url``.
        """
        self._config = config
        self._jwks = jwks if jwks is not None else JWKSCache(config.jwks_url)

    async def verify(self, token: str) -> AccessToken:
        """Validate ``token`` fully, raising on any failure.

        Checks, in order: token structure, signing key resolution
        (JWKS), signature, ``exp`` / ``nbf`` (with
        :data:`CLOCK_SKEW_LEEWAY_S`), ``iss``, ``aud``, and the
        configured required scopes.

        Args:
            token: The raw compact-serialised JWT.

        Returns:
            The SDK :class:`AccessToken` carrying the caller's
            ``client_id``, scopes, expiry, and full claim set.

        Raises:
            TokenValidationError: With a stable ``reason`` code on any
                validation failure.
        """
        try:
            header = jwt.get_unverified_header(token)
        except jwt.exceptions.InvalidTokenError as exc:
            raise TokenValidationError(
                "malformed_token", f"not a decodable JWT: {exc}"
            ) from exc
        key = await self._jwks.get_key(header.get("kid"))
        try:
            claims = jwt.decode(
                token,
                key=key.key,
                algorithms=[key.algorithm_name],
                audience=self._config.audience,
                issuer=self._config.issuer,
                leeway=CLOCK_SKEW_LEEWAY_S,
                options={"require": ["exp", "iss", "aud"]},
            )
        except jwt.exceptions.InvalidTokenError as exc:
            for exc_type, reason in _JWT_ERROR_REASONS:
                if isinstance(exc, exc_type):
                    raise TokenValidationError(reason, str(exc)) from exc
            raise TokenValidationError("invalid_token", str(exc)) from exc
        scopes = str(claims.get("scope", "")).split()
        missing = [
            scope
            for scope in self._config.required_scopes
            if scope not in scopes
        ]
        if missing:
            raise TokenValidationError(
                "insufficient_scope",
                "token lacks required scope(s): " + " ".join(missing),
            )
        client_id = str(
            claims.get("client_id")
            or claims.get("azp")
            or claims.get("sub")
            or ""
        )
        return AccessToken(
            token=token,
            client_id=client_id,
            scopes=scopes,
            expires_at=claims.get("exp"),
            resource=self._config.audience,
            subject=claims.get("sub"),
            claims=claims,
        )

    async def verify_token(self, token: str) -> AccessToken | None:
        """SDK ``TokenVerifier`` protocol adapter over :meth:`verify`.

        Args:
            token: The raw compact-serialised JWT.

        Returns:
            The :class:`AccessToken` when valid, ``None`` otherwise
            (the reason is discarded; use :meth:`verify` when the
            caller needs it).
        """
        try:
            return await self.verify(token)
        except TokenValidationError:
            return None


class OAuthResourceMiddleware:
    """ASGI middleware enforcing OAuth 2.1 JWT auth per RFC 9728.

    The OAuth sibling of the transport's ``BearerTokenMiddleware``:
    every HTTP request must carry ``Authorization: Bearer <jwt>``
    passing :class:`JWTVerifier`; failures are rejected ``401``
    (``403`` for ``insufficient_scope``) with a ``WWW-Authenticate``
    challenge carrying ``error``, ``error_description``, and the
    ``resource_metadata`` URL (RFC 9728 §5.1). The RFC 9728 metadata
    document itself is served unauthenticated on ``GET`` to
    :data:`WELL_KNOWN_PATH` and its audience-derived variant --
    clients need it precisely when they do not yet hold a token.
    The ``/metrics`` endpoint is handled (and exempted) one layer
    further out by the observability middleware; see
    ``docs/quickstart.md`` for the access-policy trade-off.

    Authorized requests forward the optional ``Camt053-Account``
    tenant header into the tenant context variable (same contract as
    the static-token middleware) and are audited with the token's
    ``client_id`` and scopes.
    """

    def __init__(
        self,
        app: ASGIApp,
        verifier: JWTVerifier,
        config: OAuthConfig,
    ) -> None:
        """Wrap ``app`` behind OAuth 2.1 auth.

        Args:
            app: The downstream ASGI application.
            verifier: The JWT verifier to authenticate requests with.
            config: The resource-server configuration (for metadata).
        """
        self._app = app
        self._verifier = verifier
        self._config = config
        self._metadata = protected_resource_metadata(config)
        self._resource_metadata_url = resource_metadata_url(config.audience)
        self._well_known_paths = {
            WELL_KNOWN_PATH,
            urlparse(self._resource_metadata_url).path,
        }

    def _challenge(self, error: str, description: str) -> str:
        """Build the RFC 6750 / RFC 9728 ``WWW-Authenticate`` value.

        Args:
            error: The RFC 6750 error code (``invalid_token`` or
                ``insufficient_scope``).
            description: The human-readable failure detail.

        Returns:
            The full ``Bearer ...`` challenge string.
        """
        return (
            f'Bearer error="{error}", '
            f'error_description="{description}", '
            f'resource_metadata="{self._resource_metadata_url}"'
        )

    async def _reject(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        failure: TokenValidationError,
        tenant: str | None,
    ) -> None:
        """Send the 401/403 rejection for ``failure`` and audit it.

        Args:
            scope: The ASGI connection scope.
            receive: The ASGI receive callable.
            send: The ASGI send callable.
            failure: The validation failure being reported.
            tenant: The (unauthenticated) tenant header, for the audit
                record's scope attribution.
        """
        insufficient = failure.reason == "insufficient_scope"
        status = 403 if insufficient else 401
        error = "insufficient_scope" if insufficient else "invalid_token"
        audit_event(
            "http.request.rejected",
            tenant,
            path=scope.get("path", ""),
            reason=failure.reason,
            auth="oauth",
        )
        observability.AUTH_FAILURES.labels(reason=failure.reason).inc()
        response = JSONResponse(
            {"error": error, "error_description": failure.description},
            status_code=status,
            headers={
                "WWW-Authenticate": self._challenge(error, failure.description)
            },
        )
        await response(scope, receive, send)

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
        path = scope.get("path", "")
        if path in self._well_known_paths and scope.get("method") in (
            "GET",
            "HEAD",
        ):
            await JSONResponse(self._metadata)(scope, receive, send)
            return
        headers = Headers(scope=scope)
        tenant = headers.get(TENANT_HEADER)
        supplied = headers.get("Authorization", "")
        scheme, _, credential = supplied.partition(" ")
        if scheme.lower() != "bearer" or not credential.strip():
            await self._reject(
                scope,
                receive,
                send,
                TokenValidationError(
                    "missing_bearer",
                    "expected 'Authorization: Bearer <token>'",
                ),
                tenant,
            )
            return
        try:
            access = await self._verifier.verify(credential.strip())
        except TokenValidationError as failure:
            await self._reject(scope, receive, send, failure, tenant)
            return
        audit_event(
            "http.request.authorized",
            tenant,
            path=path,
            auth="oauth",
            client_id=access.client_id,
            token_scopes=access.scopes,
        )
        reset_token = _tenant_var.set(tenant)
        try:
            await self._app(scope, receive, send)
        finally:
            _tenant_var.reset(reset_token)
