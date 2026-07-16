// Copyright (C) 2023-2026 Sebastien Rousseau.
// SPDX-License-Identifier: Apache-2.0
//
// k6 load scenario for the camt053-mcp streamable-HTTP transport.
//
// Drives the real MCP JSON-RPC flow (initialize -> notifications/
// initialized -> tools/call) against a running server, in two
// scenarios:
//
//   * session_reuse    -- one MCP session per VU, reused for every
//                         tool call (the recommended client shape);
//   * session_per_call -- a brand-new MCP session for every tool
//                         call (initialize + call + DELETE), showing
//                         the cost of NOT reusing sessions.
//
// Usage (server started separately):
//
//   CAMT053_MCP_TOKEN=bench-token \
//     camt053-mcp --transport=http --bind=127.0.0.1:8080 &
//
//   k6 run bench/k6/mcp_load.js \
//     -e URL=http://127.0.0.1:8080/mcp \
//     -e TOKEN=bench-token \
//     -e VUS=100 -e DURATION=30s \
//     -e SCENARIO=both        # or: session_reuse | session_per_call
//
// Parameters (all via -e):
//   URL      MCP endpoint            (default http://127.0.0.1:8080/mcp)
//   TOKEN    bearer token            (default bench-token)
//   VUS      concurrent VUs/sessions (default 100)
//   DURATION per-scenario duration   (default 30s)
//   SCENARIO which scenario(s) to run (default both, back to back)

import http from "k6/http";
import { check, fail } from "k6";
import { Trend, Counter } from "k6/metrics";

const URL = __ENV.URL || "http://127.0.0.1:8080/mcp";
const TOKEN = __ENV.TOKEN || "bench-token";
const VUS = parseInt(__ENV.VUS || "100", 10);
const DURATION = __ENV.DURATION || "30s";
const SCENARIO = __ENV.SCENARIO || "both";

const toolLatency = new Trend("mcp_tool_call_ms", true);
const initLatency = new Trend("mcp_initialize_ms", true);
const rpcErrors = new Counter("mcp_rpc_errors");

const BASE_HEADERS = {
  "Content-Type": "application/json",
  Accept: "application/json, text/event-stream",
  Authorization: `Bearer ${TOKEN}`,
};

function scenarios() {
  const all = {};
  if (SCENARIO === "session_reuse" || SCENARIO === "both") {
    all.session_reuse = {
      executor: "constant-vus",
      exec: "sessionReuse",
      vus: VUS,
      duration: DURATION,
    };
  }
  if (SCENARIO === "session_per_call" || SCENARIO === "both") {
    all.session_per_call = {
      executor: "constant-vus",
      exec: "sessionPerCall",
      vus: VUS,
      duration: DURATION,
      // When both run, stagger the second scenario after the first.
      startTime: SCENARIO === "both" ? DURATION : "0s",
    };
  }
  return all;
}

export const options = {
  scenarios: scenarios(),
  thresholds: {
    // Enterprise NFR anchors; see docs/BENCHMARKS.md.
    "mcp_tool_call_ms{scenario:session_reuse}": [
      "p(95)<200",
      "p(99)<500",
    ],
    mcp_rpc_errors: ["count==0"],
  },
};

// Parse the JSON-RPC payload out of a streamable-HTTP response
// (SSE `data:` line, or plain JSON when the server negotiates it).
function rpcPayload(response) {
  const body = response.body || "";
  if (body.startsWith("{")) {
    return JSON.parse(body);
  }
  let payload = null;
  for (const line of body.split("\n")) {
    if (line.startsWith("data:")) {
      payload = JSON.parse(line.slice(5).trim());
    }
  }
  return payload;
}

function rpc(method, params, id, sessionId) {
  const headers = Object.assign({}, BASE_HEADERS);
  if (sessionId) {
    headers["mcp-session-id"] = sessionId;
  }
  const message = { jsonrpc: "2.0", method: method };
  if (params !== null) {
    message.params = params;
  }
  if (id !== null) {
    message.id = id;
  }
  return http.post(URL, JSON.stringify(message), { headers: headers });
}

function initializeSession() {
  const started = Date.now();
  const response = rpc(
    "initialize",
    {
      protocolVersion: "2025-06-18",
      capabilities: {},
      clientInfo: { name: "k6-bench", version: "0.0.0" },
    },
    1,
    null,
  );
  initLatency.add(Date.now() - started);
  const ok = check(response, {
    "initialize is 200": (r) => r.status === 200,
    "session id present": (r) => !!r.headers["Mcp-Session-Id"],
  });
  if (!ok) {
    rpcErrors.add(1);
    fail(`initialize failed: HTTP ${response.status}`);
  }
  const sessionId = response.headers["Mcp-Session-Id"];
  const notified = rpc("notifications/initialized", {}, null, sessionId);
  check(notified, { "initialized accepted": (r) => r.status === 202 });
  return sessionId;
}

function callTool(sessionId, id) {
  const started = Date.now();
  const response = rpc(
    "tools/call",
    {
      name: "validate_identifier",
      arguments: { kind: "bic", value: "NWBKGB2LXXX" },
    },
    id,
    sessionId,
  );
  toolLatency.add(Date.now() - started);
  const payload = rpcPayload(response);
  const ok = check(response, {
    "tools/call is 200": (r) => r.status === 200,
  }) &&
    check(payload, {
      "tool result ok": (p) => p && p.result && !p.result.isError,
    });
  if (!ok) {
    rpcErrors.add(1);
  }
}

function endSession(sessionId) {
  const headers = Object.assign({}, BASE_HEADERS, {
    "mcp-session-id": sessionId,
  });
  http.del(URL, null, { headers: headers });
}

// One MCP session per VU, reused across every iteration.
const vuSessions = {};

export function sessionReuse() {
  let sessionId = vuSessions[__VU];
  if (!sessionId) {
    sessionId = initializeSession();
    vuSessions[__VU] = sessionId;
  }
  callTool(sessionId, __ITER + 2);
}

// A brand-new MCP session for every single tool call.
export function sessionPerCall() {
  const sessionId = initializeSession();
  callTool(sessionId, 2);
  endSession(sessionId);
}
