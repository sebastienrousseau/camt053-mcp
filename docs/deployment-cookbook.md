# Deployment cookbook

End-to-end recipes for deploying `camt053-mcp` beyond the local
stdio default, with the surrounding pieces you'll typically want
next to it: TLS termination, secret management, process
supervision, and audit-log routing.

The recipes are **opinionated minimums**: enough to be
production-shaped, small enough to read in one sitting. Adapt
network policies, secret management, and resource limits to your
environment. For the REST-API recipes of the wider suite (Docker
Compose, Kubernetes, Prometheus/Grafana), see the core library's
[deployment cookbook](https://sebastienrousseau.github.io/camt053/deployment-cookbook.html).

## Contents

- [1. Multi-tenant HTTP service](#1-multi-tenant-http-service)
- [2. Single-host systemd unit behind nginx](#2-single-host-systemd-unit-behind-nginx)
- [3. Container image](#3-container-image)
- [Recipes you'll likely want next](#recipes-youll-likely-want-next)

## Common assumptions

Every recipe assumes:

- Python 3.12 (3.10 / 3.11 also supported).
- The server is launched as `camt053-mcp --transport=http`; the
  MCP endpoint is `http://HOST:PORT/mcp` (streamable HTTP).
- TLS terminates at a reverse proxy (nginx, Cloud Load Balancer,
  ingress controller). The server itself listens on plain HTTP on
  a local socket — never expose it without TLS in front.
- The bearer token lives in the `CAMT053_MCP_TOKEN` environment
  variable, injected from your secret store. The server refuses
  to start over HTTP without it.
- Tenants identify themselves per request with the optional
  `Camt053-Account` header; the `camt053_mcp.audit` logger emits
  one JSON line per request carrying `service` + `scope` so calls
  stay attributable per tenant.

---

## 1. Multi-tenant HTTP service

The smallest shared deployment: one server instance, several
tenant teams, bearer auth, per-tenant attribution.

### Start the server

```sh
export CAMT053_MCP_TOKEN="$(openssl rand -hex 32)"   # or from your vault
camt053-mcp --transport=http --bind=127.0.0.1:8080
```

- `--bind` defaults to `127.0.0.1:8080` (loopback-only). Bind
  `0.0.0.0:8080` only when a firewall/proxy fronts the port.
- stdio deployments are untouched: `camt053-mcp` with no flags
  behaves exactly as before and needs no token.

### Point each tenant's MCP client at it

```json
{
  "mcpServers": {
    "camt053": {
      "url": "https://camt053.internal.example.com/mcp",
      "headers": {
        "Authorization": "Bearer <token-from-your-vault>",
        "Camt053-Account": "acme-treasury"
      }
    }
  }
}
```

Each team sets its own `Camt053-Account` value. Tools see it via
the request context (`get_tenant_context` echoes it back), and
every request lands in the audit log scoped to that tenant:

```json
{"event": "http.request.authorized", "path": "/mcp", "scope": "acme-treasury", "service": "camt053-mcp", "timestamp_utc": "2026-07-15T10:00:00.000000Z"}
```

### Verify the auth wall

```sh
# No token → 401 + WWW-Authenticate: Bearer
curl -si https://camt053.internal.example.com/mcp | head -1
# HTTP/1.1 401 Unauthorized

# Wrong token → 401
curl -si -H "Authorization: Bearer nope" \
  https://camt053.internal.example.com/mcp | head -1
# HTTP/1.1 401 Unauthorized
```

### Route the audit log

The audit stream is a standard Python logger, so route it like
any other. To ship it to an append-only file:

```python
# sitecustomize.py or your launcher wrapper
import logging.handlers

handler = logging.handlers.WatchedFileHandler(
    "/var/log/camt053-mcp/audit.jsonl"
)
handler.setLevel(logging.INFO)
logging.getLogger("camt053_mcp.audit").addHandler(handler)
```

One JSON object per line; `service` + `scope` on every record.

---

## 2. Single-host systemd unit behind nginx

### `/etc/systemd/system/camt053-mcp.service`

```ini
[Unit]
Description=camt053 MCP server (streamable HTTP, multi-tenant)
After=network-online.target
Wants=network-online.target

[Service]
User=camt053
Group=camt053
# Token injected from a root-owned env file (never on the cmdline).
EnvironmentFile=/etc/camt053-mcp/token.env
ExecStart=/opt/camt053-mcp/venv/bin/camt053-mcp --transport=http --bind=127.0.0.1:8080
Restart=on-failure
RestartSec=2

# Hardening
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/var/log/camt053-mcp

[Install]
WantedBy=multi-user.target
```

`/etc/camt053-mcp/token.env` (mode `0600`, owner `root`):

```sh
CAMT053_MCP_TOKEN=<secret>
```

### nginx TLS front

```nginx
server {
    listen 443 ssl;
    server_name camt053.internal.example.com;

    ssl_certificate     /etc/ssl/certs/camt053.crt;
    ssl_certificate_key /etc/ssl/private/camt053.key;

    location /mcp {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        # Streamable HTTP uses SSE responses: disable buffering.
        proxy_buffering off;
        proxy_read_timeout 300s;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
    }
}
```

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now camt053-mcp
```

---

## 3. Container image

The repo's `Dockerfile` builds the stdio server; for HTTP, run the
same image with the flag and the secret injected:

```sh
docker run --rm -p 8080:8080 \
  -e CAMT053_MCP_TOKEN \
  camt053-mcp:latest \
  camt053-mcp --transport=http --bind=0.0.0.0:8080
```

Inside a compose stack, keep the port off the host and let your
proxy container join the same network:

```yaml
services:
  camt053-mcp:
    image: camt053-mcp:latest
    command: camt053-mcp --transport=http --bind=0.0.0.0:8080
    environment:
      CAMT053_MCP_TOKEN: ${CAMT053_MCP_TOKEN:?set in .env or your vault}
    expose:
      - "8080"
    restart: unless-stopped
```

---

## Recipes you'll likely want next

- **Per-tenant tokens / OAuth.** The bearer token is deliberately a
  single shared service credential; issue per-tenant credentials at
  the proxy (mTLS, OAuth2 introspection) and keep `Camt053-Account`
  as the scope carrier.
- **Rate limiting.** Enforce per-tenant limits at the proxy keyed on
  the `Camt053-Account` header.
- **Metrics.** Scrape uvicorn/proxy access logs, or wrap the ASGI app
  with your OpenTelemetry middleware of choice.
