#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Sebastien Rousseau <sebastian.rousseau@gmail.com>
# SPDX-License-Identifier: Apache-2.0 OR MIT
"""What the MCP layer costs on top of the library it wraps.

A server like this one is a thin shell: it validates arguments, runs a
hardened pre-flight over the XML, calls `camt053.services`, and writes an
audit record. The library underneath is already benchmarked in its own
repository, so measuring parse speed here would just measure that again.

The question that belongs here is **overhead**. An agent calling
`parse_statement` forty times while working through a folder pays the shell
cost forty times, and if that cost grows with document size — because the
pre-flight rescans, or the audit record copies the whole argument — then the
server becomes the bottleneck rather than the parser.

So this measures two things, and deliberately does **not** measure a third.

* **Floor dispatch cost** — `list_message_types`, a tool that touches no
  XML at all. Whatever it costs is pure shell: argument handling, the audit
  record, the return marshalling. An agent pays this on every call.
* **Tool throughput across sizes** — `parse_statement` on growing documents.
  This is what an agent actually experiences. Read `us/entry`: flat means
  the shell is not adding a per-byte cost on top of the parser.

What it does not do is subtract the direct `camt053.services` call from the
tool call to isolate overhead. That was the first design and it was wrong:
the difference is a fraction of a millisecond against a 40 ms parse, which
is below the noise floor, and the subtraction produced *negative* overhead
at larger sizes. A benchmark that reports a physically impossible number is
worse than no benchmark, because somebody will eventually quote it.

`camt053_mcp.auditing` truncates argument previews at 256 characters
specifically so a 5 MB statement does not land in the audit log. A
regression there would show up as `us/entry` climbing with size while every
unit test carried on passing.

Run::

    python benches/bench_tool_dispatch.py
    python benches/bench_tool_dispatch.py --json
    python benches/bench_tool_dispatch.py --quick     # what CI runs

Nothing here asserts a threshold: wall-clock is not comparable between
machines. CI runs ``--quick`` so a benchmark that has stopped compiling
against the current API fails the build rather than rotting into a file that
reads as verified and is not.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

# The server logs an INFO line per tool call. Useful in production, noise in
# a benchmark whose whole output is a table.
logging.disable(logging.CRITICAL)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from camt053_mcp import server  # noqa: E402

HEAD = """<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.08">
 <BkToCstmrStmt>
  <GrpHdr><MsgId>BENCH-1</MsgId><CreDtTm>2026-06-21T10:00:00</CreDtTm></GrpHdr>
  <Stmt>
   <Id>STMT-1</Id>
   <Acct><Id><IBAN>DE89370400440532013000</IBAN></Id></Acct>
   <Bal><Tp><CdOrPrtry><Cd>OPBD</Cd></CdOrPrtry></Tp>
    <Amt Ccy="EUR">1000.00</Amt><CdtDbtInd>CRDT</CdtDbtInd>
    <Dt><Dt>2026-06-20</Dt></Dt></Bal>
"""

NTRY = """   <Ntry><Amt Ccy="EUR">{amount}.00</Amt><CdtDbtInd>CRDT</CdtDbtInd>
    <Sts><Cd>BOOK</Cd></Sts><BookgDt><Dt>2026-06-21</Dt></BookgDt>
    <ValDt><Dt>2026-06-21</Dt></ValDt>
    <NtryDtls><TxDtls><Refs><EndToEndId>E2E-{i}</EndToEndId></Refs>
     </TxDtls></NtryDtls></Ntry>
"""

TAIL = """   <Bal><Tp><CdOrPrtry><Cd>CLBD</Cd></CdOrPrtry></Tp>
    <Amt Ccy="EUR">2000.00</Amt><CdtDbtInd>CRDT</CdtDbtInd>
    <Dt><Dt>2026-06-21</Dt></Dt></Bal>
  </Stmt>
 </BkToCstmrStmt>
</Document>
"""


def build(entries: int) -> str:
    """A camt.053 document carrying ``entries`` booked entries."""
    body = "".join(
        NTRY.format(amount=(i % 900) + 100, i=i) for i in range(entries)
    )
    return HEAD + body + TAIL


def _best(call, repeats: int) -> float:
    """Best-of timing; the minimum is the least noisy estimator available.

    One untimed call first. The XSD is compiled and cached on first use, so
    without a warm-up the first sample measures schema compilation and the
    overhead column comes out negative — which is not a fast shell, it is a
    slow first measurement in the wrong column.
    """
    call()
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        call()
        samples.append(time.perf_counter() - start)
    return min(samples)


def measure_floor(repeats: int) -> dict:
    """A tool that touches no XML: whatever this costs is pure shell."""
    best = _best(server.list_message_types, repeats)
    return {"case": "dispatch floor", "us": best * 1e6}


def measure(entries: int, repeats: int) -> dict:
    """The tool on a document of a given size, as an agent would call it."""
    xml = build(entries)
    best = _best(lambda: server.parse_statement(xml), repeats)
    return {
        "case": "parse_statement",
        "entries": entries,
        "bytes": len(xml),
        "tool_ms": best * 1e3,
        "us_per_entry": best * 1e6 / entries,
    }


def run(quick: bool) -> list[dict]:
    sizes = [10, 100] if quick else [10, 100, 500, 2_000]
    repeats = 3 if quick else 7
    return [measure_floor(repeats)] + [
        measure(size, repeats) for size in sizes
    ]


def render(rows: list[dict]) -> None:
    floor = rows[0]
    # Sub-microsecond rounds to "0", which reads as a broken measurement
    # rather than as a shell too cheap to measure.
    shown = f"{floor['us']:,.0f}" if floor["us"] >= 1 else f"{floor['us']:.3f}"
    print(f"dispatch floor (list_message_types): {shown} us/call")
    print(
        "  -- pure shell: argument handling, audit record, return "
        "marshalling.\n"
    )
    print(f"{'entries':>8}{'KiB':>9}{'tool ms':>11}{'us/entry':>11}")
    print("-" * 39)
    for row in rows[1:]:
        print(
            f"{row['entries']:>8}{row['bytes'] / 1024:>9.1f}"
            f"{row['tool_ms']:>11.2f}{row['us_per_entry']:>11.1f}"
        )
    body = rows[1:]
    if len(body) >= 2:
        drift = body[-1]["us_per_entry"] / body[0]["us_per_entry"]
        print(
            f"\nus/entry at {body[-1]['entries']:,} entries is {drift:.2f}x "
            f"the cost at {body[0]['entries']:,}. Roughly flat means the "
            f"shell adds no per-byte cost on top of the parser. A number "
            f"that climbs means something -- the pre-flight, or the audit "
            f"preview -- is reading the whole document."
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument(
        "--quick", action="store_true", help="small sizes, as CI runs"
    )
    args = parser.parse_args()

    rows = run(quick=args.quick)
    if args.json:
        json.dump(rows, sys.stdout, indent=1)
        print()
    else:
        render(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
