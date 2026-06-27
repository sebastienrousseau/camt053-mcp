#!/usr/bin/env python3
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

"""Atheris fuzz harness for the camt053-mcp tool entry points.

Every function exercised here is a real MCP tool that consumes *untrusted*
input straight from an agent / client: raw statement XML, identifier strings,
message-type strings, and rulebook lookup keys. The tools are contracted to
either return a JSON-serialisable value or an ``{"error": ...}`` payload; the
only failures they are documented to raise are :class:`ValueError` (which
subsumes :class:`json.JSONDecodeError`) and
:class:`camt053.exceptions.Camt053Error`.

This harness catches *only* those documented exceptions, so any other
uncaught exception (the tool crashing on adversarial input) is surfaced by
libFuzzer as a real bug.

Run locally::

    pip install atheris
    python fuzz/fuzz_parser.py -atheris_runs=100000

In CI this target is built and run by ClusterFuzzLite (see ``.clusterfuzzlite``).
"""

import json
import sys

import atheris

with atheris.instrument_imports():
    from camt053.exceptions import Camt053Error

    from camt053_mcp import export_journal, server

# Exceptions the tools are documented to raise. json.JSONDecodeError is a
# subclass of ValueError, but it is listed explicitly for intent. Anything
# outside this tuple escaping a tool is a crash worth reporting.
_DOCUMENTED = (ValueError, json.JSONDecodeError, Camt053Error)


def test_one_input(data: bytes) -> None:
    """Drive the untrusted-input tool entry points with fuzzed input."""
    fdp = atheris.FuzzedDataProvider(data)
    text = fdp.ConsumeUnicodeNoSurrogates(sys.maxsize)

    try:
        # Statement XML pipeline -- parse / validate / compliance / export.
        server.parse_statement(text)
        server.validate_statement(text)
        server.check_cbpr_readiness(text)
        server.export_journal(text)
        export_journal.export(text, "xero")
        export_journal.export(text, "qbo")

        # Identifier + schema lookups driven by fuzzed string arguments.
        server.validate_identifier(text, text)
        server.get_required_fields(text)
        server.get_input_schema(text)
        server.validate_records(text, [])

        # Curated rulebook registry lookups.
        server.cite_rulebook(text, text, text)
        server.list_rulebook_clauses(text, text)
    except _DOCUMENTED:
        # Documented, expected failure modes -- not a bug.
        return


def main() -> None:
    """Wire the harness into the libFuzzer driver and start fuzzing."""
    atheris.Setup(sys.argv, test_one_input)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
