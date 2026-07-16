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

"""Tests for the OAuth 2.1 resource-server auth (RFC 9728).

Covers, per the workstream's acceptance criteria:

* configuration from ``CAMT053_MCP_OAUTH_*`` env vars (full, partial,
  absent, derived JWKS URL, scope list);
* JWT validation: a valid token passes; wrong issuer / audience /
  expiry / nbf / signature / algorithm / structure are all rejected
  with stable reason codes and HTTP 401;
* scope-claim gating (403 ``insufficient_scope``);
* the RFC 9728 protected-resource metadata endpoint (bare and
  audience-derived paths) and the ``WWW-Authenticate`` challenge
  carrying ``resource_metadata``;
* JWKS fetching against a real local HTTP server (cache, rotation
  refresh, unusable-entry skipping, fetch failures);
* the static-token dev-mode fallback warning and OAuth-over-static
  precedence in ``run_http``.
"""

import asyncio
import http.server
import json
import logging
import threading
import time
from types import SimpleNamespace

import pytest

pytest.importorskip("mcp")

import jwt  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402

from camt053_mcp import oauth, transport  # noqa: E402

ISSUER = "https://auth.example.test"
AUDIENCE = "https://mcp.example.test/mcp"
KID = "test-key-1"


# ─── Key material and token helpers ──────────────────────────────────────────


@pytest.fixture(scope="module")
def rsa_key():
    """A module-scoped RSA-2048 signing key (key generation is slow)."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def other_rsa_key():
    """A second RSA key, NOT in the JWKS, for bad-signature tokens."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwk(key, kid=KID):
    """Serialise ``key``'s public half as one JWKS entry dict."""
    entry = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    entry.update({"kid": kid, "alg": "RS256", "use": "sig"})
    return entry


@pytest.fixture(scope="module")
def jwks_document(rsa_key):
    """A JWKS document holding the test key plus skippable junk."""
    return {
        "keys": [
            _jwk(rsa_key),
            {"kid": "unusable", "kty": "weird"},  # skipped: bad kty
            {"kty": "RSA"},  # skipped: no kid
        ]
    }


def _make_token(
    key,
    *,
    kid=KID,
    algorithm="RS256",
    drop=(),
    **overrides,
):
    """Mint a JWT for the test issuer/audience with ``overrides``."""
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "exp": now + 600,
        "iat": now,
        "sub": "user-1",
        "scope": "camt053:read",
    }
    claims.update(overrides)
    for name in drop:
        claims.pop(name, None)
    return jwt.encode(claims, key, algorithm=algorithm, headers={"kid": kid})


# ─── A real local JWKS endpoint (drives the httpx fetch path) ────────────────


class _JWKSHandler(http.server.BaseHTTPRequestHandler):
    """Serves the module's JWKS document plus deliberately broken URLs."""

    document = None  # set by the fixture

    def do_GET(self):  # noqa: N802 (http.server API)
        """Answer /jwks.json, /not-json, /no-keys, /keys-not-list, /404."""
        payloads = {
            "/jwks.json": (200, json.dumps(self.document)),
            "/not-json": (200, "this is not JSON {"),
            "/no-keys": (200, "{}"),
            "/keys-not-list": (200, '{"keys": 42}'),
        }
        status, body = payloads.get(self.path, (404, "not found"))
        raw = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        """Silence per-request stderr noise."""


@pytest.fixture(scope="module")
def jwks_url(jwks_document):
    """Serve the JWKS over real HTTP; yield the /jwks.json URL."""
    _JWKSHandler.document = jwks_document
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _JWKSHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    yield f"http://127.0.0.1:{port}/jwks.json"
    server.shutdown()
    thread.join(timeout=5)


@pytest.fixture()
def config(jwks_url):
    """An OAuthConfig pointing at the local JWKS, with a scope gate."""
    return oauth.OAuthConfig(
        issuer=ISSUER,
        audience=AUDIENCE,
        jwks_url=jwks_url,
        required_scopes=("camt053:read",),
    )


@pytest.fixture()
def verifier(config):
    """A JWTVerifier over the local JWKS."""
    return oauth.JWTVerifier(config)


def _verify(verifier, token):
    """Run the async verify() to completion."""
    return asyncio.run(verifier.verify(token))


def _reason(verifier, token):
    """Verify ``token`` expecting failure; return the reason code."""
    with pytest.raises(oauth.TokenValidationError) as excinfo:
        _verify(verifier, token)
    return excinfo.value.reason


# ─── OAuthConfig.from_env ────────────────────────────────────────────────────


def test_from_env_absent_returns_none():
    """With no CAMT053_MCP_OAUTH_* variable set, OAuth is off."""
    assert oauth.OAuthConfig.from_env({}) is None


def test_from_env_reads_full_configuration():
    """All four variables round-trip into the config."""
    cfg = oauth.OAuthConfig.from_env(
        {
            oauth.OAUTH_ISSUER_ENV: ISSUER,
            oauth.OAUTH_AUDIENCE_ENV: AUDIENCE,
            oauth.OAUTH_JWKS_URL_ENV: "https://auth.example.test/keys",
            oauth.OAUTH_SCOPES_ENV: "camt053:read camt053:write",
        }
    )
    assert cfg == oauth.OAuthConfig(
        issuer=ISSUER,
        audience=AUDIENCE,
        jwks_url="https://auth.example.test/keys",
        required_scopes=("camt053:read", "camt053:write"),
    )


def test_from_env_derives_jwks_url_from_issuer():
    """JWKS URL defaults to <issuer>/.well-known/jwks.json."""
    cfg = oauth.OAuthConfig.from_env(
        {
            oauth.OAUTH_ISSUER_ENV: ISSUER + "/",
            oauth.OAUTH_AUDIENCE_ENV: AUDIENCE,
        }
    )
    assert cfg.jwks_url == f"{ISSUER}/.well-known/jwks.json"
    assert cfg.required_scopes == ()


@pytest.mark.parametrize(
    "partial",
    [
        {oauth.OAUTH_ISSUER_ENV: ISSUER},
        {oauth.OAUTH_AUDIENCE_ENV: AUDIENCE},
        {oauth.OAUTH_JWKS_URL_ENV: "https://x/keys"},
        {oauth.OAUTH_SCOPES_ENV: "camt053:read"},
    ],
)
def test_from_env_partial_configuration_refuses_to_start(partial):
    """Some-but-not-enough OAuth variables fail loudly (no silent auth)."""
    with pytest.raises(SystemExit, match="Partial OAuth configuration"):
        oauth.OAuthConfig.from_env(partial)


def test_from_env_reads_process_environment(monkeypatch):
    """Passing environ=None (the default) reads os.environ."""
    monkeypatch.setenv(oauth.OAUTH_ISSUER_ENV, ISSUER)
    monkeypatch.setenv(oauth.OAUTH_AUDIENCE_ENV, AUDIENCE)
    cfg = oauth.OAuthConfig.from_env()
    assert cfg.issuer == ISSUER


# ─── RFC 9728 metadata helpers ───────────────────────────────────────────────


def test_resource_metadata_url_inserts_well_known_before_path():
    """A resource URI with a path keeps it after the well-known stem."""
    assert oauth.resource_metadata_url("https://x.test/mcp") == (
        "https://x.test/.well-known/oauth-protected-resource/mcp"
    )


@pytest.mark.parametrize("audience", ["https://x.test", "https://x.test/"])
def test_resource_metadata_url_bare_origin(audience):
    """A bare-origin resource URI yields the bare well-known path."""
    assert oauth.resource_metadata_url(audience) == (
        "https://x.test/.well-known/oauth-protected-resource"
    )


def test_protected_resource_metadata_document(config):
    """The metadata document carries the RFC 9728 required members."""
    assert oauth.protected_resource_metadata(config) == {
        "resource": AUDIENCE,
        "authorization_servers": [ISSUER],
        "bearer_methods_supported": ["header"],
        "scopes_supported": ["camt053:read"],
    }


def test_protected_resource_metadata_omits_empty_scopes(jwks_url):
    """Without a scope gate, scopes_supported is omitted entirely."""
    cfg = oauth.OAuthConfig(
        issuer=ISSUER, audience=AUDIENCE, jwks_url=jwks_url
    )
    assert "scopes_supported" not in oauth.protected_resource_metadata(cfg)


# ─── JWKSCache ───────────────────────────────────────────────────────────────


def test_jwks_cache_fetches_and_skips_unusable_entries(jwks_url):
    """The cache loads the good key and skips junk JWKS entries."""
    cache = oauth.JWKSCache(jwks_url)
    key = asyncio.run(cache.get_key(KID))
    assert key.algorithm_name == "RS256"
    assert set(cache._keys) == {KID}


def test_jwks_cache_serves_fresh_keys_without_refetching(jwks_url):
    """A fresh cached kid is answered without touching the network."""
    cache = oauth.JWKSCache(jwks_url)
    asyncio.run(cache.get_key(KID))

    async def boom():
        raise AssertionError("unexpected JWKS refetch")

    cache._refresh = boom
    assert asyncio.run(cache.get_key(KID)).algorithm_name == "RS256"


def test_jwks_cache_ttl_expiry_triggers_refetch(jwks_url):
    """Once older than the TTL, the cache refetches even for known kids."""
    cache = oauth.JWKSCache(jwks_url, ttl_seconds=0.0)
    asyncio.run(cache.get_key(KID))
    calls = []
    original = oauth.JWKSCache._refresh

    async def counting(self=cache):
        calls.append(1)
        await original(self)

    cache._refresh = counting
    asyncio.run(cache.get_key(KID))
    assert calls == [1]


def test_jwks_cache_unknown_kid_refreshes_once_then_fails(jwks_url):
    """An unknown kid triggers a rotation refresh, then a stable error."""
    cache = oauth.JWKSCache(jwks_url)
    asyncio.run(cache.get_key(KID))  # warm
    with pytest.raises(oauth.TokenValidationError) as excinfo:
        asyncio.run(cache.get_key("rotated-away"))
    assert excinfo.value.reason == "unknown_kid"


def test_jwks_cache_no_kid_single_key_is_tolerated(jwks_url):
    """kid-less tokens are accepted when the key set is unambiguous."""
    cache = oauth.JWKSCache(jwks_url)
    key = asyncio.run(cache.get_key(None))
    assert key.algorithm_name == "RS256"


def test_jwks_cache_no_kid_ambiguous_key_set_is_refused(jwks_url, rsa_key):
    """kid-less tokens are refused when several keys could match."""
    cache = oauth.JWKSCache(jwks_url)
    asyncio.run(cache.get_key(KID))
    cache._keys["second"] = cache._keys[KID]
    with pytest.raises(oauth.TokenValidationError) as excinfo:
        asyncio.run(cache.get_key(None))
    assert excinfo.value.reason == "missing_kid"


@pytest.mark.parametrize(
    "suffix", ["not-json", "no-keys", "keys-not-list", "missing"]
)
def test_jwks_cache_bad_documents_are_jwks_unavailable(jwks_url, suffix):
    """Broken JWKS payloads map to the jwks_unavailable reason."""
    base = jwks_url.rsplit("/", 1)[0]
    cache = oauth.JWKSCache(f"{base}/{suffix}")
    with pytest.raises(oauth.TokenValidationError) as excinfo:
        asyncio.run(cache.get_key(KID))
    assert excinfo.value.reason == "jwks_unavailable"


def test_jwks_cache_unreachable_host_is_jwks_unavailable():
    """A connection failure maps to the jwks_unavailable reason."""
    cache = oauth.JWKSCache("http://127.0.0.1:9/jwks.json")  # discard port
    with pytest.raises(oauth.TokenValidationError) as excinfo:
        asyncio.run(cache.get_key(KID))
    assert excinfo.value.reason == "jwks_unavailable"


# ─── JWTVerifier ─────────────────────────────────────────────────────────────


def test_valid_token_yields_access_token(verifier, rsa_key):
    """A well-formed, in-policy JWT verifies into an SDK AccessToken."""
    token = _make_token(rsa_key, client_id="agent-42")
    access = _verify(verifier, token)
    assert access.client_id == "agent-42"
    assert access.scopes == ["camt053:read"]
    assert access.subject == "user-1"
    assert access.resource == AUDIENCE
    assert access.claims["iss"] == ISSUER
    assert access.expires_at == access.claims["exp"]


def test_client_id_falls_back_to_azp_then_sub(verifier, rsa_key):
    """client_id resolution: client_id > azp > sub > empty string."""
    assert (
        _verify(verifier, _make_token(rsa_key, azp="azp-1")).client_id
        == "azp-1"
    )
    assert _verify(verifier, _make_token(rsa_key)).client_id == "user-1"
    token = _make_token(rsa_key, drop=("sub",))
    assert _verify(verifier, token).client_id == ""


def test_wrong_issuer_is_rejected(verifier, rsa_key):
    """A token from another issuer fails with issuer_mismatch."""
    token = _make_token(rsa_key, iss="https://evil.example.test")
    assert _reason(verifier, token) == "issuer_mismatch"


def test_wrong_audience_is_rejected(verifier, rsa_key):
    """A token minted for another resource fails audience_mismatch."""
    token = _make_token(rsa_key, aud="https://other.example.test/mcp")
    assert _reason(verifier, token) == "audience_mismatch"


def test_expired_token_is_rejected(verifier, rsa_key):
    """exp in the past (beyond leeway) fails token_expired."""
    token = _make_token(rsa_key, exp=int(time.time()) - 3600)
    assert _reason(verifier, token) == "token_expired"


def test_not_yet_valid_token_is_rejected(verifier, rsa_key):
    """nbf in the future (beyond leeway) fails token_not_yet_valid."""
    token = _make_token(rsa_key, nbf=int(time.time()) + 3600)
    assert _reason(verifier, token) == "token_not_yet_valid"


def test_missing_exp_claim_is_rejected(verifier, rsa_key):
    """exp/iss/aud are required claims; dropping exp is refused."""
    token = _make_token(rsa_key, drop=("exp",))
    assert _reason(verifier, token) == "missing_required_claim"


def test_bad_signature_is_rejected(verifier, other_rsa_key):
    """A token signed by a key outside the JWKS fails signature_invalid."""
    token = _make_token(other_rsa_key)  # same kid, wrong key
    assert _reason(verifier, token) == "signature_invalid"


def test_algorithm_confusion_is_rejected(verifier):
    """An HS256 token cannot sneak past the JWKS RS256 key."""
    token = _make_token("shared-secret", algorithm="HS256")
    assert _reason(verifier, token) == "invalid_token"


def test_malformed_token_is_rejected(verifier):
    """Garbage that is not a JWT at all fails malformed_token."""
    assert _reason(verifier, "not-a-jwt") == "malformed_token"


def test_unknown_kid_is_rejected(verifier, rsa_key):
    """A token naming an unknown signing key fails unknown_kid."""
    token = _make_token(rsa_key, kid="rotated-away")
    assert _reason(verifier, token) == "unknown_kid"


def test_missing_scope_is_rejected(verifier, rsa_key):
    """The configured scope gate refuses tokens without the scope."""
    token = _make_token(rsa_key, scope="other:scope")
    assert _reason(verifier, token) == "insufficient_scope"


def test_scope_gate_absent_accepts_scopeless_token(jwks_url, rsa_key):
    """Without required scopes, a token with no scope claim passes."""
    cfg = oauth.OAuthConfig(
        issuer=ISSUER, audience=AUDIENCE, jwks_url=jwks_url
    )
    access = _verify(
        oauth.JWTVerifier(cfg), _make_token(rsa_key, drop=("scope",))
    )
    assert access.scopes == []


def test_verify_token_protocol_adapter(verifier, rsa_key):
    """verify_token (SDK protocol) maps failure to None, success to token."""
    assert asyncio.run(verifier.verify_token("junk")) is None
    access = asyncio.run(verifier.verify_token(_make_token(rsa_key)))
    assert access is not None and access.subject == "user-1"


# ─── OAuthResourceMiddleware (in-process ASGI) ───────────────────────────────


def _http_scope(headers, path="/mcp", method="POST"):
    """Build an ASGI HTTP scope with the given header dict."""
    return {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [
            (k.lower().encode(), v.encode()) for k, v in headers.items()
        ],
    }


def _drive(middleware, headers, path="/mcp", method="POST"):
    """Dispatch one request through ``middleware``; return sent messages."""
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    asyncio.run(middleware(_http_scope(headers, path, method), receive, send))
    return sent


@pytest.fixture()
def mw(config):
    """The OAuth middleware over a downstream that records tenants."""
    downstream_calls = []

    async def downstream(scope, receive, send):
        downstream_calls.append(transport.current_tenant())
        await send({"type": "http.response.start", "status": 200})
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = oauth.OAuthResourceMiddleware(
        downstream, oauth.JWTVerifier(config), config
    )
    middleware.downstream_calls = downstream_calls
    return middleware


def _status_and_headers(sent):
    """Extract (status, headers-dict) from an ASGI response start."""
    start = sent[0]
    return start["status"], dict(start.get("headers", []))


def test_middleware_valid_token_reaches_app_with_tenant(mw, rsa_key):
    """A valid JWT passes; the tenant header lands in the contextvar."""
    sent = _drive(
        mw,
        {
            "Authorization": f"Bearer {_make_token(rsa_key)}",
            "Camt053-Account": "acme-treasury",
        },
    )
    assert sent[0]["status"] == 200
    assert mw.downstream_calls == ["acme-treasury"]
    assert transport.current_tenant() is None  # reset after the request


def test_middleware_missing_bearer_is_401_with_metadata(mw):
    """No Authorization header: 401 + resource_metadata challenge."""
    sent = _drive(mw, {})
    status, headers = _status_and_headers(sent)
    assert status == 401
    challenge = headers[b"www-authenticate"].decode()
    assert challenge.startswith('Bearer error="invalid_token"')
    assert (
        "resource_metadata="
        '"https://mcp.example.test'
        '/.well-known/oauth-protected-resource/mcp"' in challenge
    )
    assert mw.downstream_calls == []


@pytest.mark.parametrize(
    "authorization", ["Basic dXNlcjpwdw==", "Bearer", "Bearer   "]
)
def test_middleware_non_bearer_credentials_are_401(mw, authorization):
    """Wrong scheme or empty credential is refused up front."""
    sent = _drive(mw, {"Authorization": authorization})
    assert sent[0]["status"] == 401


def test_middleware_invalid_token_is_401_and_audited(mw, rsa_key, caplog):
    """A failing JWT yields 401 and an audit record with the reason."""
    token = _make_token(rsa_key, iss="https://evil.example.test")
    with caplog.at_level(logging.INFO, logger="camt053_mcp.audit"):
        sent = _drive(
            mw,
            {
                "Authorization": f"Bearer {token}",
                "Camt053-Account": "acme-treasury",
            },
        )
    status, headers = _status_and_headers(sent)
    assert status == 401
    events = [json.loads(r.getMessage()) for r in caplog.records]
    assert events[-1]["event"] == "http.request.rejected"
    assert events[-1]["reason"] == "issuer_mismatch"
    assert events[-1]["scope"] == "acme-treasury"
    assert events[-1]["auth"] == "oauth"


def test_middleware_insufficient_scope_is_403(mw, rsa_key):
    """Scope-gate failures use 403 insufficient_scope per RFC 6750."""
    token = _make_token(rsa_key, scope="other:scope")
    sent = _drive(mw, {"Authorization": f"Bearer {token}"})
    status, headers = _status_and_headers(sent)
    assert status == 403
    challenge = headers[b"www-authenticate"].decode()
    assert 'error="insufficient_scope"' in challenge


def test_middleware_authorized_audit_carries_client_and_scopes(
    mw, rsa_key, caplog
):
    """Authorized requests are audited with client_id + token scopes."""
    token = _make_token(rsa_key, client_id="agent-42")
    with caplog.at_level(logging.INFO, logger="camt053_mcp.audit"):
        _drive(mw, {"Authorization": f"Bearer {token}"})
    events = [json.loads(r.getMessage()) for r in caplog.records]
    assert events[-1]["event"] == "http.request.authorized"
    assert events[-1]["client_id"] == "agent-42"
    assert events[-1]["token_scopes"] == ["camt053:read"]


@pytest.mark.parametrize(
    "path",
    [
        "/.well-known/oauth-protected-resource",
        "/.well-known/oauth-protected-resource/mcp",
    ],
)
def test_middleware_serves_rfc9728_metadata_unauthenticated(mw, path):
    """GET on both well-known paths returns the metadata, no token."""
    sent = _drive(mw, {}, path=path, method="GET")
    assert sent[0]["status"] == 200
    body = json.loads(sent[1]["body"])
    assert body == {
        "resource": AUDIENCE,
        "authorization_servers": [ISSUER],
        "bearer_methods_supported": ["header"],
        "scopes_supported": ["camt053:read"],
    }
    assert mw.downstream_calls == []


def test_middleware_non_get_on_well_known_requires_auth(mw):
    """POST to the well-known path is NOT exempt from auth."""
    sent = _drive(
        mw, {}, path="/.well-known/oauth-protected-resource", method="POST"
    )
    assert sent[0]["status"] == 401


def test_middleware_forwards_non_http_scopes(mw):
    """Lifespan (non-HTTP) events bypass auth entirely."""
    seen = []

    async def downstream(scope, receive, send):
        seen.append(scope["type"])

    mw._app = downstream

    async def go():
        await mw({"type": "lifespan"}, None, None)

    asyncio.run(go())
    assert seen == ["lifespan"]


# ─── build_http_app / run_http wiring ────────────────────────────────────────


def test_build_http_app_oauth_takes_precedence(config):
    """With an OAuth config, the OAuth middleware wraps the app."""
    sentinel = object()
    fake_server = SimpleNamespace(streamable_http_app=lambda: sentinel)
    app = transport.build_http_app(
        fake_server, token="ignored", oauth_config=config
    )
    assert isinstance(app, oauth.OAuthResourceMiddleware)
    assert app._app is sentinel


def test_build_http_app_without_any_auth_is_refused():
    """Neither token nor OAuth config: hard error, never open access."""
    fake_server = SimpleNamespace(streamable_http_app=lambda: object())
    with pytest.raises(ValueError, match="static token or an OAuth config"):
        transport.build_http_app(fake_server)


def _run_http_with(monkeypatch, env):
    """Run run_http with ``env`` vars; capture the uvicorn app + logs."""
    calls = {}
    for name in (
        transport.TOKEN_ENV,
        oauth.OAUTH_ISSUER_ENV,
        oauth.OAUTH_AUDIENCE_ENV,
        oauth.OAUTH_JWKS_URL_ENV,
        oauth.OAUTH_SCOPES_ENV,
    ):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(
        transport.uvicorn, "run", lambda app, **kw: calls.update(app=app)
    )
    fake_server = SimpleNamespace(streamable_http_app=lambda: object())
    transport.run_http(fake_server, "127.0.0.1:8123")
    return calls


def test_run_http_oauth_env_selects_oauth_middleware(monkeypatch):
    """OAuth env vars alone put the OAuth middleware in front."""
    calls = _run_http_with(
        monkeypatch,
        {
            oauth.OAUTH_ISSUER_ENV: ISSUER,
            oauth.OAUTH_AUDIENCE_ENV: AUDIENCE,
        },
    )
    assert isinstance(calls["app"], oauth.OAuthResourceMiddleware)


def test_run_http_static_token_logs_dev_mode_warning(monkeypatch, caplog):
    """The static token still works but is flagged as dev-mode auth."""
    with caplog.at_level(logging.WARNING, logger="camt053_mcp.transport"):
        calls = _run_http_with(monkeypatch, {transport.TOKEN_ENV: "s3cret"})
    assert isinstance(calls["app"], transport.BearerTokenMiddleware)
    assert any("DEV-MODE" in r.getMessage() for r in caplog.records)


def test_run_http_oauth_beats_static_token(monkeypatch, caplog):
    """When both are configured, OAuth wins and the token is ignored."""
    with caplog.at_level(logging.WARNING, logger="camt053_mcp.transport"):
        calls = _run_http_with(
            monkeypatch,
            {
                transport.TOKEN_ENV: "s3cret",
                oauth.OAUTH_ISSUER_ENV: ISSUER,
                oauth.OAUTH_AUDIENCE_ENV: AUDIENCE,
            },
        )
    assert isinstance(calls["app"], oauth.OAuthResourceMiddleware)
    assert any("IGNORED" in r.getMessage() for r in caplog.records)


def test_run_http_partial_oauth_env_refuses_to_start(monkeypatch):
    """A partial OAuth env (issuer only) refuses to start."""
    monkeypatch.delenv(oauth.OAUTH_AUDIENCE_ENV, raising=False)
    monkeypatch.delenv(oauth.OAUTH_JWKS_URL_ENV, raising=False)
    monkeypatch.delenv(oauth.OAUTH_SCOPES_ENV, raising=False)
    monkeypatch.setenv(oauth.OAUTH_ISSUER_ENV, ISSUER)
    fake_server = SimpleNamespace(streamable_http_app=lambda: object())
    with pytest.raises(SystemExit, match="Partial OAuth configuration"):
        transport.run_http(fake_server, "127.0.0.1:8123")


# ─── Integration: real HTTP stack with OAuth ─────────────────────────────────


def test_oauth_end_to_end_over_real_http(config, rsa_key):
    """A dedicated FastMCP instance serves OAuth-authed streamable HTTP."""
    import httpx
    import uvicorn
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from mcp.server.fastmcp import FastMCP

    tiny = FastMCP("oauth-e2e")

    @tiny.tool()
    def echo(text: str) -> str:
        """Echo ``text`` back (integration probe tool)."""
        return text

    app = transport.build_http_app(tiny, oauth_config=config)
    uv_config = uvicorn.Config(
        app, host="127.0.0.1", port=0, log_level="warning", lifespan="on"
    )
    uv_server = uvicorn.Server(uv_config)
    thread = threading.Thread(target=uv_server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if uv_server.started:
            break
        time.sleep(0.05)
    assert uv_server.started, "OAuth test HTTP server failed to start"
    port = uv_server.servers[0].sockets[0].getsockname()[1]
    base = f"http://127.0.0.1:{port}"

    try:
        # Unauthenticated: 401 with the RFC 9728 challenge.
        response = httpx.get(f"{base}/mcp")
        assert response.status_code == 401
        assert "resource_metadata" in response.headers["www-authenticate"]

        # Metadata endpoint needs no token.
        response = httpx.get(f"{base}/.well-known/oauth-protected-resource")
        assert response.status_code == 200
        assert response.json()["resource"] == AUDIENCE

        # A valid JWT drives a full MCP session end-to-end.
        token = _make_token(rsa_key)

        async def call():
            headers = {"Authorization": f"Bearer {token}"}
            async with httpx.AsyncClient(
                headers=headers, timeout=30
            ) as client:
                async with streamable_http_client(
                    f"{base}/mcp", http_client=client
                ) as (read_stream, write_stream, _):
                    async with ClientSession(
                        read_stream, write_stream
                    ) as session:
                        await session.initialize()
                        result = await session.call_tool(
                            "echo", {"text": "hello"}
                        )
            assert not result.isError
            return result.content[0].text

        assert asyncio.run(call()) == "hello"
    finally:
        uv_server.should_exit = True
        thread.join(timeout=10)
