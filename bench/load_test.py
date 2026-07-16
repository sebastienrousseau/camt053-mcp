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

"""Real-HTTP load benchmark for the camt053-mcp streamable transport.

Drives the actual MCP JSON-RPC flow (``initialize`` ->
``notifications/initialized`` -> ``tools/call``) over HTTP with
``asyncio`` + ``httpx`` against a real server process (spawned as a
subprocess with static bearer auth, or an external ``--url``).

Two modes measure the cost of MCP session handling:

* ``reuse`` -- one MCP session per virtual user (VU), reused for every
  tool call; per-op latency is the ``tools/call`` round trip.
* ``fresh`` -- a brand-new MCP session for every tool call
  (initialize + initialized + call + DELETE); per-op latency is the
  whole cycle, i.e. what a client that refuses to reuse sessions pays.

Reported per scenario: achieved RPS (tool ops / wall time), p50 / p95 /
p99 / mean latency, error rate, and the server process's RSS (before,
peak, after). Results feed ``docs/BENCHMARKS.md``.

Usage::

    poetry run python bench/load_test.py --sessions 100 --calls 20
    poetry run python bench/load_test.py --sessions 1000 --calls 5 \\
        --mode reuse --json bench/results/1000-reuse.json

This script is a standalone benchmark harness: it is not collected by
pytest (``testpaths = tests``) and does not count toward the coverage
gate, but it is lint- and format-clean like the rest of the repo.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import resource
import socket
import statistics
import subprocess  # nosec B404 (spawns our own server binary)
import sys
import threading
import time
from dataclasses import asdict, dataclass, field

import httpx

#: The bearer token used for the spawned benchmark server.
BENCH_TOKEN = "bench-token-c53"  # nosec B105 (local benchmark only)

#: The tool called on every operation: real work (BIC validation)
#: with a small, constant payload, so the transport dominates.
TOOL_CALL_PARAMS = {
    "name": "validate_identifier",
    "arguments": {"kind": "bic", "value": "NWBKGB2LXXX"},
}

_INIT_PARAMS = {
    "protocolVersion": "2025-06-18",
    "capabilities": {},
    "clientInfo": {"name": "camt053-bench", "version": "0.0.0"},
}


@dataclass
class ScenarioResult:
    """Aggregated outcome of one (mode, sessions, calls) scenario."""

    mode: str
    sessions: int
    calls_per_session: int
    wall_seconds: float
    ops: int
    errors: int
    rps: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    mean_ms: float
    rss_before_mib: float
    rss_peak_mib: float
    rss_after_mib: float
    latencies_ms: list[float] = field(default_factory=list, repr=False)


class MemorySampler:
    """Samples a process's RSS (via ``ps``) on a background thread."""

    def __init__(self, pid: int, interval_s: float = 0.25) -> None:
        """Sample ``pid`` every ``interval_s`` seconds once started."""
        self._pid = pid
        self._interval = interval_s
        self._samples: list[float] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def rss_mib(self) -> float:
        """Read the process's current RSS in MiB (0.0 if unreadable)."""
        try:
            out = subprocess.run(  # nosec B603 B607 (fixed argv)
                ["ps", "-o", "rss=", "-p", str(self._pid)],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            return int(out) / 1024.0
        except (subprocess.SubprocessError, ValueError):
            return 0.0

    def _run(self) -> None:
        """Poll RSS until stopped."""
        while not self._stop.is_set():
            self._samples.append(self.rss_mib())
            self._stop.wait(self._interval)

    def __enter__(self) -> MemorySampler:
        """Start sampling."""
        self._samples.append(self.rss_mib())
        self._thread.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Stop sampling."""
        self._stop.set()
        self._thread.join(timeout=2)

    @property
    def peak_mib(self) -> float:
        """The highest RSS observed so far."""
        return max(self._samples, default=0.0)


def _percentile(samples: list[float], pct: float) -> float:
    """Return the ``pct`` percentile (0..100) of ``samples``."""
    if not samples:
        return 0.0
    ordered = sorted(samples)
    index = min(len(ordered) - 1, max(0, round(pct / 100 * len(ordered)) - 1))
    return ordered[index]


def _headers(token: str, session_id: str | None = None) -> dict[str, str]:
    """Build the streamable-HTTP request headers."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session_id:
        headers["mcp-session-id"] = session_id
    return headers


def _rpc_payload(text: str) -> dict | None:
    """Parse the JSON-RPC message out of an SSE or JSON body."""
    stripped = text.lstrip()
    if stripped.startswith("{"):
        return json.loads(stripped)
    payload = None
    for line in text.splitlines():
        if line.startswith("data:"):
            payload = json.loads(line[5:].strip())
    return payload


async def _initialize(client: httpx.AsyncClient, url: str, token: str) -> str:
    """Run the MCP initialize handshake; return the session id."""
    response = await client.post(
        url,
        headers=_headers(token),
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": _INIT_PARAMS,
        },
    )
    response.raise_for_status()
    session_id = response.headers.get("mcp-session-id")
    if not session_id:
        raise RuntimeError("initialize returned no mcp-session-id")
    notified = await client.post(
        url,
        headers=_headers(token, session_id),
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
    )
    notified.raise_for_status()
    return session_id


async def _call_tool(
    client: httpx.AsyncClient,
    url: str,
    token: str,
    session_id: str,
    request_id: int,
) -> None:
    """Perform one tools/call and verify it succeeded."""
    response = await client.post(
        url,
        headers=_headers(token, session_id),
        json={
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": TOOL_CALL_PARAMS,
        },
    )
    response.raise_for_status()
    payload = _rpc_payload(response.text)
    if payload is None or "result" not in payload:
        raise RuntimeError(f"unexpected RPC payload: {payload!r}")
    if payload["result"].get("isError"):
        raise RuntimeError(f"tool error: {payload['result']!r}")


async def _end_session(
    client: httpx.AsyncClient, url: str, token: str, session_id: str
) -> None:
    """Terminate an MCP session (best effort)."""
    try:
        await client.delete(url, headers=_headers(token, session_id))
    except httpx.HTTPError:
        pass


def _client() -> httpx.AsyncClient:
    """One VU's HTTP client (own connection, generous timeout)."""
    return httpx.AsyncClient(
        timeout=httpx.Timeout(120.0),
        limits=httpx.Limits(max_connections=4),
    )


class _StartGate:
    """A ready-then-go barrier: VUs report ready, the runner fires.

    Every VU calls :meth:`ready` once its setup is complete, then
    awaits :meth:`wait`. The runner awaits :meth:`all_ready`, starts
    the wall clock, and calls :meth:`go` -- so in reuse mode the timed
    window measures steady-state tool calls only, never the initial
    session ramp-up.
    """

    def __init__(self, parties: int) -> None:
        """Expect ``parties`` VUs."""
        self._pending = parties
        self._all_ready = asyncio.Event()
        self._go = asyncio.Event()

    def ready(self) -> None:
        """Mark one VU's setup as complete."""
        self._pending -= 1
        if self._pending <= 0:
            self._all_ready.set()

    async def all_ready(self) -> None:
        """Wait until every VU has reported ready."""
        await self._all_ready.wait()

    def go(self) -> None:
        """Open the gate: VUs start their measured work."""
        self._go.set()

    async def wait(self) -> None:
        """Block a VU until the runner opens the gate."""
        await self._go.wait()


async def _vu_reuse(
    url: str,
    token: str,
    calls: int,
    latencies: list[float],
    errors: list[int],
    gate: _StartGate,
) -> None:
    """One reuse-mode VU: initialize once, then ``calls`` tool calls."""
    async with _client() as client:
        try:
            session_id = await _initialize(client, url, token)
        except (httpx.HTTPError, RuntimeError):
            errors.append(calls)  # the whole VU's work is lost
            gate.ready()
            await gate.wait()
            return
        gate.ready()
        await gate.wait()
        for index in range(calls):
            started = time.perf_counter()
            try:
                await _call_tool(client, url, token, session_id, index + 2)
                latencies.append((time.perf_counter() - started) * 1000.0)
            except (httpx.HTTPError, RuntimeError):
                errors.append(1)
        await _end_session(client, url, token, session_id)


async def _vu_fresh(
    url: str,
    token: str,
    calls: int,
    latencies: list[float],
    errors: list[int],
    gate: _StartGate,
) -> None:
    """One fresh-mode VU: a full new session per tool call."""
    async with _client() as client:
        gate.ready()
        await gate.wait()
        for _ in range(calls):
            started = time.perf_counter()
            try:
                session_id = await _initialize(client, url, token)
                await _call_tool(client, url, token, session_id, 2)
                await _end_session(client, url, token, session_id)
                latencies.append((time.perf_counter() - started) * 1000.0)
            except (httpx.HTTPError, RuntimeError):
                errors.append(1)


async def _run_scenario_async(
    mode: str, url: str, token: str, sessions: int, calls: int
) -> tuple[list[float], int, float]:
    """Run all VUs for one scenario; return (latencies, errors, wall)."""
    latencies: list[float] = []
    errors: list[int] = []
    gate = _StartGate(sessions)
    vu = _vu_reuse if mode == "reuse" else _vu_fresh
    tasks = [
        asyncio.create_task(vu(url, token, calls, latencies, errors, gate))
        for _ in range(sessions)
    ]
    # In reuse mode session setup happens before the gate opens, so
    # the timed window measures steady-state tool calls only; in fresh
    # mode session setup IS the measured operation.
    await gate.all_ready()
    started = time.perf_counter()
    gate.go()
    await asyncio.gather(*tasks)
    wall = time.perf_counter() - started
    return latencies, sum(errors), wall


def run_scenario(
    mode: str,
    url: str,
    token: str,
    sessions: int,
    calls: int,
    sampler: MemorySampler | None,
) -> ScenarioResult:
    """Run one scenario and aggregate its results."""
    rss_before = sampler.rss_mib() if sampler else 0.0
    peak_before = sampler.peak_mib if sampler else 0.0
    latencies, errors, wall = asyncio.run(
        _run_scenario_async(mode, url, token, sessions, calls)
    )
    rss_after = sampler.rss_mib() if sampler else 0.0
    ops = len(latencies)
    return ScenarioResult(
        mode=mode,
        sessions=sessions,
        calls_per_session=calls,
        wall_seconds=round(wall, 3),
        ops=ops,
        errors=errors,
        rps=round(ops / wall, 1) if wall > 0 else 0.0,
        p50_ms=round(_percentile(latencies, 50), 1),
        p95_ms=round(_percentile(latencies, 95), 1),
        p99_ms=round(_percentile(latencies, 99), 1),
        mean_ms=(round(statistics.fmean(latencies), 1) if latencies else 0.0),
        rss_before_mib=round(rss_before, 1),
        rss_peak_mib=round(
            max(sampler.peak_mib if sampler else 0.0, peak_before), 1
        ),
        rss_after_mib=round(rss_after, 1),
        latencies_ms=latencies,
    )


def _raise_nofile_limit(target: int) -> None:
    """Raise RLIMIT_NOFILE towards ``target`` (best effort)."""
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft >= target:
        return
    try:
        resource.setrlimit(
            resource.RLIMIT_NOFILE,
            (min(target, hard if hard > 0 else target), hard),
        )
    except (ValueError, OSError) as exc:
        print(f"warning: could not raise nofile limit: {exc}")


def _free_port() -> int:
    """Ask the OS for a free loopback TCP port."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _spawn_server(port: int) -> subprocess.Popen:
    """Start the real MCP server subprocess with bearer auth."""
    env = dict(os.environ)
    env["CAMT053_MCP_TOKEN"] = BENCH_TOKEN
    process = subprocess.Popen(  # nosec B603 (our own module, fixed argv)
        [
            sys.executable,
            "-m",
            "camt053_mcp.server",
            "--transport=http",
            f"--bind=127.0.0.1:{port}",
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return process
        except OSError:
            if process.poll() is not None:
                raise RuntimeError("benchmark server exited early") from None
            time.sleep(0.1)
    process.terminate()
    raise RuntimeError("benchmark server did not start within 30s")


def _print_table(results: list[ScenarioResult]) -> None:
    """Print the results as a markdown table."""
    columns = (
        "mode",
        "sessions",
        "calls/VU",
        "ops",
        "errors",
        "wall s",
        "RPS",
        "p50 ms",
        "p95 ms",
        "p99 ms",
        "mean ms",
        "RSS peak MiB",
    )
    print("| " + " | ".join(columns) + " |")
    print("|" + "|".join(["---"] * len(columns)) + "|")
    for r in results:
        row = (
            r.mode,
            r.sessions,
            r.calls_per_session,
            r.ops,
            r.errors,
            r.wall_seconds,
            r.rps,
            r.p50_ms,
            r.p95_ms,
            r.p99_ms,
            r.mean_ms,
            r.rss_peak_mib,
        )
        print("| " + " | ".join(str(value) for value in row) + " |")


def main(argv: list[str] | None = None) -> int:
    """Run the benchmark; return a process exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--sessions", type=int, default=100, help="concurrent MCP sessions"
    )
    parser.add_argument(
        "--calls", type=int, default=20, help="tool calls per session"
    )
    parser.add_argument(
        "--mode",
        choices=("reuse", "fresh", "both"),
        default="both",
        help="session-reuse ON (reuse), OFF (fresh), or both",
    )
    parser.add_argument(
        "--url",
        default=None,
        help="external MCP endpoint (skips spawning a server)",
    )
    parser.add_argument(
        "--token",
        default=BENCH_TOKEN,
        help="bearer token for --url mode",
    )
    parser.add_argument(
        "--json", default=None, help="write results (with raw latencies) here"
    )
    args = parser.parse_args(argv)

    _raise_nofile_limit(max(4096, args.sessions * 8))

    process: subprocess.Popen | None = None
    if args.url:
        url, token = args.url, args.token
        sampler_ctx: MemorySampler | None = None
    else:
        port = _free_port()
        process = _spawn_server(port)
        url, token = f"http://127.0.0.1:{port}/mcp", BENCH_TOKEN
        sampler_ctx = MemorySampler(process.pid)

    modes = ["reuse", "fresh"] if args.mode == "both" else [args.mode]
    results: list[ScenarioResult] = []
    try:
        if sampler_ctx is not None:
            sampler_ctx.__enter__()
        for mode in modes:
            print(
                f"running mode={mode} sessions={args.sessions} "
                f"calls={args.calls} against {url} ..."
            )
            results.append(
                run_scenario(
                    mode, url, token, args.sessions, args.calls, sampler_ctx
                )
            )
    finally:
        if sampler_ctx is not None:
            sampler_ctx.__exit__(None, None, None)
        if process is not None:
            process.terminate()
            process.wait(timeout=10)

    _print_table(results)
    if args.json:
        payload = [asdict(result) for result in results]
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        print(f"raw results written to {args.json}")
    return 1 if any(result.errors for result in results) else 0


if __name__ == "__main__":
    sys.exit(main())
