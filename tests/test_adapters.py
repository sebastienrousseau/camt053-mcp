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

"""Mock-verified tests for the framework adapters.

The agent frameworks (LangChain, CrewAI, LlamaIndex) are heavy and
conflict-prone, so they are NOT installed in the test environment. Instead
each adapter is exercised against a FAKE framework tool factory injected into
``sys.modules``; the fake records exactly what the adapter passes so we can
assert one framework tool per server tool, with the right name, description,
JSON input schema and a working callable. The missing-extra ``ImportError``
branch is covered by forcing the lazy import to fail.

This verifies the adapter LOGIC only; it is mock-verified, not live-verified
against the real frameworks.
"""

import sys
import types

import pytest

pytest.importorskip("mcp")

import camt053_mcp.adapters as adapters  # noqa: E402
import camt053_mcp.server as srv  # noqa: E402

EXPECTED_TOOLS = {
    "list_message_types",
    "list_return_reasons",
    "get_required_fields",
    "get_input_schema",
    "validate_records",
    "validate_identifier",
    "parse_statement",
    "convert_mt940_to_camt053",
    "convert_mt942",
    "validate_statement",
    "check_cbpr_readiness",
    "get_cbpr_cutover_date",
    "cite_rulebook",
    "list_rulebook_clauses",
    "search_rulebook_vector",
    "export_journal",
    "list_export_journal_targets",
    "classify_entry",
    "list_classify_entry_categories",
    "get_tenant_context",
    "list_entries",
    "filter_entries",
    "detect_statement_anomalies",
    "generate_reversal",
}


def _server_tool_map() -> dict:
    """Return the server's tools keyed by name for cross-checking."""
    return {t.name: t for t in srv.server._tool_manager.list_tools()}


class _Recorder:
    """A fake framework tool factory that records the adapter's call kwargs.

    A single class doubles as every framework's tool type: its classmethods
    (``from_function`` / ``from_defaults``) capture the callable, name,
    description and schema the adapter passes and return a lightweight object
    exposing them for assertions.
    """

    def __init__(self, *, func, name, description, schema):
        """Store the recorded attributes of one wrapped tool."""
        self.func = func
        self.name = name
        self.description = description
        self.schema = schema

    @classmethod
    def from_function(cls, *, func, name, description, args_schema):
        """Record a LangChain/CrewAI-style ``from_function`` construction."""
        return cls(
            func=func, name=name, description=description, schema=args_schema
        )

    @classmethod
    def from_defaults(cls, *, fn, name, description, fn_schema):
        """Record a LlamaIndex-style ``from_defaults`` construction."""
        return cls(
            func=fn, name=name, description=description, schema=fn_schema
        )


class _FakeToolException(Exception):
    """Stand-in for ``langchain_core.tools.ToolException``."""


def _inject(monkeypatch, dotted_names, **attrs):
    """Inject fake modules for a dotted import path into ``sys.modules``.

    Each name in ``dotted_names`` becomes an empty module; the attributes in
    ``attrs`` are set on the deepest (last) module so ``from <last> import X``
    resolves them.
    """
    modules = []
    for dotted in dotted_names:
        mod = types.ModuleType(dotted)
        monkeypatch.setitem(sys.modules, dotted, mod)
        modules.append(mod)
    for key, value in attrs.items():
        setattr(modules[-1], key, value)


def _assert_wrapped_all_tools(built):
    """Assert a built adapter list mirrors the server tools one-for-one."""
    server_tools = _server_tool_map()
    assert {item.name for item in built} == EXPECTED_TOOLS
    assert len(built) == len(server_tools)
    for item in built:
        source = server_tools[item.name]
        assert item.description == source.description
        assert item.schema == source.parameters
        assert item.schema["type"] == "object"


# ---------------------------------------------------------------------------
# _server_tools introspection
# ---------------------------------------------------------------------------
def test_server_tools_returns_all_registered_tools():
    """``_server_tools`` returns exactly the registered server toolset."""
    names = {t.name for t in adapters._server_tools()}
    assert names == EXPECTED_TOOLS


# ---------------------------------------------------------------------------
# _wrap_with_tool_exception: both branches (success + error mapping)
# ---------------------------------------------------------------------------
def test_wrap_with_tool_exception_passes_through_result():
    """A successful call returns the wrapped callable's result unchanged."""
    wrapped = adapters._wrap_with_tool_exception(
        lambda **kw: {"echo": kw}, _FakeToolException
    )
    assert wrapped(value="x") == {"echo": {"value": "x"}}


def test_wrap_with_tool_exception_maps_raised_error():
    """A raised error is remapped to the framework's tool exception."""

    def _boom(**kwargs):
        """Raise unconditionally to exercise the error-mapping branch."""
        raise ValueError("backend blew up")

    wrapped = adapters._wrap_with_tool_exception(_boom, _FakeToolException)
    try:
        wrapped()
        raised = False
    except _FakeToolException as exc:
        raised = True
        assert "backend blew up" in str(exc)
    assert raised is True


# ---------------------------------------------------------------------------
# LangChain adapter
# ---------------------------------------------------------------------------
def test_as_langchain_tools_wraps_every_tool(monkeypatch):
    """The LangChain adapter builds one wrapped tool per server tool."""
    _inject(
        monkeypatch,
        ["langchain_core", "langchain_core.tools"],
        StructuredTool=_Recorder,
        ToolException=_FakeToolException,
    )
    built = adapters.as_langchain_tools()
    _assert_wrapped_all_tools(built)

    # The recorded callable is the ToolException-wrapping closure; calling it
    # runs the real server tool and returns its payload.
    cutover_tool = next(i for i in built if i.name == "get_cbpr_cutover_date")
    out = cutover_tool.func()
    assert set(out) == {"cutover_date"}


def test_as_langchain_tools_missing_extra_raises(monkeypatch):
    """A missing langchain-core surfaces the install-extra ImportError."""
    monkeypatch.setitem(sys.modules, "langchain_core", None)
    try:
        adapters.as_langchain_tools()
        raised = False
    except ImportError as exc:
        raised = True
        assert "camt053-mcp[langchain]" in str(exc)
    assert raised is True


# ---------------------------------------------------------------------------
# CrewAI adapter
# ---------------------------------------------------------------------------
def test_as_crewai_tools_wraps_every_tool(monkeypatch):
    """The CrewAI adapter builds one tool per server tool, callable intact."""
    _inject(
        monkeypatch,
        ["crewai", "crewai.tools"],
        CrewStructuredTool=_Recorder,
    )
    built = adapters.as_crewai_tools()
    _assert_wrapped_all_tools(built)

    # CrewAI receives the raw server callable unchanged.
    server_tools = _server_tool_map()
    for item in built:
        assert item.func is server_tools[item.name].fn


def test_as_crewai_tools_missing_extra_raises(monkeypatch):
    """A missing crewai surfaces the install-extra ImportError."""
    monkeypatch.setitem(sys.modules, "crewai", None)
    try:
        adapters.as_crewai_tools()
        raised = False
    except ImportError as exc:
        raised = True
        assert "camt053-mcp[crewai]" in str(exc)
    assert raised is True


# ---------------------------------------------------------------------------
# LlamaIndex adapter
# ---------------------------------------------------------------------------
def test_as_llamaindex_tools_wraps_every_tool(monkeypatch):
    """The LlamaIndex adapter builds one tool per server tool, callable intact."""
    _inject(
        monkeypatch,
        ["llama_index", "llama_index.core", "llama_index.core.tools"],
        FunctionTool=_Recorder,
    )
    built = adapters.as_llamaindex_tools()
    _assert_wrapped_all_tools(built)

    # LlamaIndex receives the raw server callable unchanged.
    server_tools = _server_tool_map()
    for item in built:
        assert item.func is server_tools[item.name].fn


def test_as_llamaindex_tools_missing_extra_raises(monkeypatch):
    """A missing llama-index-core surfaces the install-extra ImportError."""
    monkeypatch.setitem(sys.modules, "llama_index", None)
    try:
        adapters.as_llamaindex_tools()
        raised = False
    except ImportError as exc:
        raised = True
        assert "camt053-mcp[llamaindex]" in str(exc)
    assert raised is True


# ---------------------------------------------------------------------------
# The adapters are plain module functions, not @server.tool: the server's
# advertised tool-set must be unchanged by importing/using them.
# ---------------------------------------------------------------------------
def test_adapters_do_not_alter_registered_toolset():
    """Building adapters leaves the server's registered toolset unchanged."""
    names = {t.name for t in srv.server._tool_manager.list_tools()}
    assert names == EXPECTED_TOOLS
