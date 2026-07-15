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

"""Load / stress test suite for the MCP server (marker ``stress``).

Scaled-down enterprise MCP NFR checks, sized to run in well under a
minute on a shared CI runner:

* **Sustained concurrency** -- a 32-worker thread pool hammers the hot
  tools (``parse_statement``, ``list_entries``, ``filter_entries``,
  ``generate_reversal``) with several hundred invocations and asserts
  zero errors plus a generous p95 latency ceiling.
* **Soak** -- repeated parse -> list -> reverse round-trips must not
  leak memory (tracemalloc-measured Python heap growth stays bounded).
* **Concurrent HTTP transport** -- several authenticated MCP sessions
  drive the real streamable-HTTP stack (uvicorn + bearer middleware)
  in parallel, each with its own tenant header, and every call must
  succeed with the right tenant attribution within a wall-clock bound.

Like the core camt053 repo's ``stress`` suite, this marker is excluded
from the default coverage-gated run (see ``addopts`` in
``pyproject.toml``). All bounds carry a deliberately generous cushion
so shared CI runners do not flake the build -- a failure here signals a
real regression, not runner noise. Run locally with::

    pytest tests/test_stress.py -m stress --no-cov
"""

import asyncio
import json
import time
import tracemalloc
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

pytest.importorskip("mcp")

from mcp import ClientSession  # noqa: E402
from mcp.client.streamable_http import streamable_http_client  # noqa: E402

import camt053_mcp.server as server  # noqa: E402
from tests.conftest import SAMPLE_STATEMENT_XML  # noqa: E402

pytestmark = pytest.mark.stress

# ─── Tunables ────────────────────────────────────────────────────────────────
#
# Every bound below is generous by design: locally the whole module runs
# in a few seconds, and even a shared GitHub Actions runner running
# ~10x slower stays comfortably inside the limits.

#: Thread-pool width for the sustained-concurrency scenario.
_CONCURRENCY_WORKERS = 32

#: Total tool invocations submitted to the pool ("several hundred").
_CONCURRENCY_CALLS = 400

#: Generous per-call p95 latency ceiling under 32-way contention.
#: Observed local p95 is ~0.3 s (the mix includes the XSD-validating
#: ``generate_reversal``); 5 s gives shared CI runners a ~15x cushion.
_CONCURRENCY_P95_CEILING_S = 5.0

#: Iterations for the soak scenario.
_SOAK_ITERATIONS = 200

#: Python-heap growth ceiling for the soak scenario. A real per-call
#: leak of even 1 KiB would show as ~200 KiB by the final snapshot;
#: 8 MiB tolerates allocator noise and interned-object warm-up.
_SOAK_GROWTH_CEILING_BYTES = 8 * 1024 * 1024

#: Concurrent MCP-over-HTTP sessions and calls per session.
_HTTP_SESSIONS = 8
_HTTP_CALLS_PER_SESSION = 5

#: Wall-clock ceiling for the whole concurrent-HTTP scenario.
_HTTP_WALL_CLOCK_CEILING_S = 30.0


def _percentile(samples: list[float], pct: float) -> float:
    """Return the ``pct`` percentile (0..100) of ``samples``."""
    ordered = sorted(samples)
    index = min(len(ordered) - 1, max(0, round(pct / 100 * len(ordered)) - 1))
    return ordered[index]


def _assert_no_error(result) -> None:
    """Fail if a tool result carries the server's error envelope."""
    if isinstance(result, dict):
        assert "error" not in result, result
    elif isinstance(result, list):
        assert not any(
            isinstance(row, dict) and "error" in row for row in result
        ), result[:1]
    else:  # generate_reversal returns XML (or a serialized error dict)
        assert isinstance(result, str)
        assert result.lstrip().startswith("<"), result[:200]


# The hot tools: the parse -> filter -> reverse workflow the server
# exists for, exercised round-robin under contention.
_HOT_CALLS = (
    lambda: server.parse_statement(SAMPLE_STATEMENT_XML),
    lambda: server.list_entries(SAMPLE_STATEMENT_XML),
    lambda: server.filter_entries(SAMPLE_STATEMENT_XML, "AC04"),
    lambda: server.generate_reversal(SAMPLE_STATEMENT_XML, "AC04"),
)


def test_sustained_concurrent_tool_invocations_zero_errors_bounded_p95():
    """32 workers x 400 mixed hot-tool calls: zero errors, bounded p95."""
    durations: list[float] = []

    def invoke(index: int) -> None:
        call = _HOT_CALLS[index % len(_HOT_CALLS)]
        started = time.perf_counter()
        result = call()
        durations.append(time.perf_counter() - started)
        _assert_no_error(result)

    with ThreadPoolExecutor(max_workers=_CONCURRENCY_WORKERS) as pool:
        # list() propagates the first worker exception, failing the test.
        list(pool.map(invoke, range(_CONCURRENCY_CALLS)))

    assert len(durations) == _CONCURRENCY_CALLS
    p95 = _percentile(durations, 95)
    assert p95 < _CONCURRENCY_P95_CEILING_S, (
        f"p95 latency {p95:.3f}s exceeds the "
        f"{_CONCURRENCY_P95_CEILING_S}s ceiling"
    )


def test_soak_repeated_workflow_has_bounded_memory_growth():
    """200 parse->list->reverse round-trips must not leak Python heap."""
    # Warm up caches (schemas, interned strings) outside the measurement.
    for _ in range(5):
        server.parse_statement(SAMPLE_STATEMENT_XML)
        server.generate_reversal(SAMPLE_STATEMENT_XML, "AC04")

    tracemalloc.start()
    try:
        baseline, _ = tracemalloc.get_traced_memory()
        for _ in range(_SOAK_ITERATIONS):
            _assert_no_error(server.parse_statement(SAMPLE_STATEMENT_XML))
            _assert_no_error(server.list_entries(SAMPLE_STATEMENT_XML))
            _assert_no_error(
                server.generate_reversal(SAMPLE_STATEMENT_XML, "AC04")
            )
        final, _ = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    growth = final - baseline
    assert growth < _SOAK_GROWTH_CEILING_BYTES, (
        f"Python heap grew {growth / 1024:.0f} KiB over "
        f"{_SOAK_ITERATIONS} iterations "
        f"(ceiling {_SOAK_GROWTH_CEILING_BYTES / 1024:.0f} KiB)"
    )


async def _drive_one_http_session(url: str, token: str, tenant: str) -> None:
    """Run one authenticated MCP session making several tool calls."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Camt053-Account": tenant,
    }
    async with httpx.AsyncClient(headers=headers, timeout=30) as client:
        async with streamable_http_client(url, http_client=client) as (
            read_stream,
            write_stream,
            _,
        ):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                for _ in range(_HTTP_CALLS_PER_SESSION):
                    result = await session.call_tool("get_tenant_context", {})
                    assert not result.isError
                    payload = json.loads(result.content[0].text)
                    # Attribution must never bleed across sessions.
                    assert payload["tenant"] == tenant


def test_concurrent_http_sessions_all_succeed_with_own_tenant(http_server):
    """8 parallel authed HTTP sessions x 5 calls: all succeed, scoped."""

    async def go():
        await asyncio.gather(
            *(
                _drive_one_http_session(
                    http_server.url, http_server.token, f"tenant-{i}"
                )
                for i in range(_HTTP_SESSIONS)
            )
        )

    started = time.perf_counter()
    asyncio.run(go())
    elapsed = time.perf_counter() - started
    assert elapsed < _HTTP_WALL_CLOCK_CEILING_S, (
        f"{_HTTP_SESSIONS} sessions x {_HTTP_CALLS_PER_SESSION} calls took "
        f"{elapsed:.1f}s (ceiling {_HTTP_WALL_CLOCK_CEILING_S}s)"
    )
