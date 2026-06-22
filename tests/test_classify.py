"""Tests for the classify_entry tool + Sampling adapter (D3 in #17)."""

from __future__ import annotations

import json
from unittest import mock

import pytest

from camt053_mcp import classify
from camt053_mcp.server import (
    classify_entry as classify_entry_tool,
)
from camt053_mcp.server import (
    list_classify_entry_categories,
)

ENTRY = {
    "reference": "NTRY-0001",
    "amount": "1500.00",
    "currency": "EUR",
    "credit_debit_indicator": "DBIT",
    "counterparty_name": "ACME Payroll Services",
    "remittance_information_unstructured": "MARCH 2026 PAYROLL",
}


def _make_ctx(text: str | None, *, raise_exc: Exception | None = None):
    """Build a minimal mock context whose session.create_message returns ``text``."""
    ctx = mock.AsyncMock()
    if raise_exc is not None:
        ctx.session.create_message.side_effect = raise_exc
    else:
        content = mock.Mock(text=text)
        result = mock.Mock(content=content)
        ctx.session.create_message.return_value = result
    return ctx


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestPrompt:
    """The classification prompt template."""

    def test_contains_categories(self) -> None:
        prompt = classify._classification_prompt(ENTRY, ["a", "b", "c"])
        assert "a, b, c" in prompt or all(c in prompt for c in ("a", "b", "c"))

    def test_contains_entry_payload(self) -> None:
        prompt = classify._classification_prompt(ENTRY, ["x"])
        assert "NTRY-0001" in prompt
        assert "MARCH 2026 PAYROLL" in prompt

    def test_asks_for_single_line_json(self) -> None:
        prompt = classify._classification_prompt(ENTRY, ["x"])
        assert "JSON" in prompt
        assert "single line" in prompt or "no prose" in prompt


class TestNormalise:
    """The response normaliser validates category + confidence."""

    def test_happy_path(self) -> None:
        result = classify._normalise(
            {"category": "payroll", "confidence": 0.9, "explanation": "X"},
            ["payroll", "other"],
        )
        assert result == {
            "category": "payroll",
            "confidence": 0.9,
            "explanation": "X",
        }

    def test_clamps_confidence_into_range(self) -> None:
        r = classify._normalise(
            {"category": "x", "confidence": 99, "explanation": ""},
            ["x"],
        )
        assert r["confidence"] == 1.0

    def test_rejects_out_of_vocab_category(self) -> None:
        r = classify._normalise(
            {"category": "bogus", "confidence": 0.5}, ["x", "y"]
        )
        assert "error" in r

    def test_rejects_non_numeric_confidence(self) -> None:
        r = classify._normalise({"category": "x", "confidence": "high"}, ["x"])
        assert "error" in r

    def test_rejects_non_dict(self) -> None:
        r = classify._normalise(["not a dict"], ["x"])
        assert "error" in r


# ---------------------------------------------------------------------------
# classify_entry (async function under test)
# ---------------------------------------------------------------------------


class TestClassifyEntry:
    """End-to-end behaviour, with the Sampling round-trip mocked."""

    @pytest.mark.asyncio
    async def test_happy_path(self) -> None:
        ctx = _make_ctx(
            json.dumps(
                {
                    "category": "payroll",
                    "confidence": 0.95,
                    "explanation": "Monthly salary run.",
                }
            )
        )
        result = await classify.classify_entry(ctx, ENTRY)
        assert result["category"] == "payroll"
        assert result["confidence"] == 0.95

    @pytest.mark.asyncio
    async def test_sampling_failure_yields_error(self) -> None:
        ctx = _make_ctx(None, raise_exc=RuntimeError("not supported"))
        result = await classify.classify_entry(ctx, ENTRY)
        assert "error" in result
        assert "Sampling" in result["error"]

    @pytest.mark.asyncio
    async def test_empty_payload_yields_error(self) -> None:
        ctx = _make_ctx("")
        result = await classify.classify_entry(ctx, ENTRY)
        assert "error" in result
        assert "empty" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_non_json_response_yields_error(self) -> None:
        ctx = _make_ctx("I think this is payroll, with confidence 0.9.")
        result = await classify.classify_entry(ctx, ENTRY)
        assert "error" in result
        assert "JSON" in result["error"]

    @pytest.mark.asyncio
    async def test_explicit_categories_pass_through(self) -> None:
        ctx = _make_ctx(
            json.dumps(
                {"category": "marketing", "confidence": 0.7, "explanation": ""}
            )
        )
        result = await classify.classify_entry(
            ctx, ENTRY, categories=["marketing", "other"]
        )
        assert result["category"] == "marketing"

    @pytest.mark.asyncio
    async def test_default_categories_used_when_none(self) -> None:
        ctx = _make_ctx(
            json.dumps(
                {"category": "payroll", "confidence": 0.9, "explanation": ""}
            )
        )
        result = await classify.classify_entry(ctx, ENTRY, categories=None)
        assert result["category"] == "payroll"

    @pytest.mark.asyncio
    async def test_out_of_vocab_response(self) -> None:
        ctx = _make_ctx(
            json.dumps({"category": "elsewhere", "confidence": 0.5})
        )
        result = await classify.classify_entry(ctx, ENTRY)
        assert "error" in result


# ---------------------------------------------------------------------------
# MCP tool surface
# ---------------------------------------------------------------------------


class TestClassifyEntryTool:
    """The @server.tool wrapper preserves behaviour."""

    @pytest.mark.asyncio
    async def test_delegates_to_module(self) -> None:
        with mock.patch.object(
            classify,
            "classify_entry",
            new=mock.AsyncMock(return_value={"ok": True}),
        ) as patched:
            ctx = mock.AsyncMock()
            result = await classify_entry_tool(ctx, ENTRY)
            assert result == {"ok": True}
            patched.assert_awaited_once()


class TestListCategoriesTool:
    """The companion list-categories tool."""

    def test_returns_default_list(self) -> None:
        result = list_classify_entry_categories()
        assert isinstance(result, list)
        assert result == list(classify.DEFAULT_CATEGORIES)
        assert "payroll" in result
        assert "other" in result
