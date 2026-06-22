"""Tests for the curated rulebook registry and its MCP tools."""

from __future__ import annotations

import pytest

from camt053_mcp import rulebook
from camt053_mcp.server import cite_rulebook, list_rulebook_clauses

REQUIRED_FIELDS = {
    "scheme",
    "version",
    "clause",
    "title",
    "summary",
    "source_url",
    "as_of",
}


class TestRegistryShape:
    """The static registry is well-formed."""

    def test_at_least_one_entry_per_scheme(self) -> None:
        all_entries = rulebook.list_clauses()
        schemes = {e["scheme"] for e in all_entries}
        assert {"SEPA", "CBPR+", "HVPS+"}.issubset(schemes)

    def test_every_entry_has_required_fields(self) -> None:
        for e in rulebook.list_clauses():
            assert REQUIRED_FIELDS.issubset(e.keys()), e

    def test_every_entry_has_https_source(self) -> None:
        for e in rulebook.list_clauses():
            assert e["source_url"].startswith("https://"), e["clause"]

    def test_clauses_are_kebab_case(self) -> None:
        for e in rulebook.list_clauses():
            assert e["clause"].islower(), e["clause"]
            assert " " not in e["clause"], e["clause"]


class TestListClauses:
    """list_clauses + the MCP tool that wraps it."""

    def test_unfiltered_returns_everything(self) -> None:
        assert len(list_rulebook_clauses()) == len(rulebook.list_clauses())

    def test_filter_by_scheme(self) -> None:
        sepa = list_rulebook_clauses(scheme="SEPA")
        assert sepa
        assert all(e["scheme"] == "SEPA" for e in sepa)

    def test_filter_by_version(self) -> None:
        v2026 = list_rulebook_clauses(version="2026")
        assert v2026
        assert all(e["version"] == "2026" for e in v2026)

    def test_filter_by_scheme_and_version(self) -> None:
        both = list_rulebook_clauses(scheme="CBPR+", version="2026")
        assert both
        assert all(
            e["scheme"] == "CBPR+" and e["version"] == "2026" for e in both
        )

    def test_unknown_filter_returns_empty(self) -> None:
        assert list_rulebook_clauses(scheme="ACH") == []


class TestCite:
    """cite_rulebook + the tool that wraps it."""

    def test_known_citation_has_disclaimer(self) -> None:
        result = cite_rulebook(
            "CBPR+", "2026", "structured-address-mandate-nov-2026"
        )
        assert "disclaimer" in result
        assert "source_url" in result
        assert result["scheme"] == "CBPR+"

    def test_unknown_scheme_returns_error(self) -> None:
        assert "error" in cite_rulebook("ACH", "2025", "anything")

    def test_unknown_version_returns_error(self) -> None:
        assert "error" in cite_rulebook("SEPA", "1999", "iban-only")

    def test_unknown_clause_returns_error(self) -> None:
        assert "error" in cite_rulebook("SEPA", "2025", "no-such-clause")

    @pytest.mark.parametrize(
        ("scheme", "version", "clause"),
        [
            (e["scheme"], e["version"], e["clause"])
            for e in rulebook.list_clauses()
        ],
    )
    def test_every_registered_entry_is_citable(
        self, scheme: str, version: str, clause: str
    ) -> None:
        result = cite_rulebook(scheme, version, clause)
        assert "error" not in result
        assert REQUIRED_FIELDS.issubset(result.keys())


def test_register_rejects_duplicates() -> None:
    """The internal register helper guards against accidental duplicates."""
    entry = {
        "scheme": "SEPA",
        "version": "2025",
        "clause": "iban-only",  # already registered
        "title": "x",
        "summary": "x",
        "source_url": "https://x",
        "as_of": "2026-06-22",
    }
    with pytest.raises(ValueError, match="duplicate"):
        rulebook._register(entry)  # type: ignore[arg-type]
