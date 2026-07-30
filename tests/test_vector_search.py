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

"""Tests for lexical-vector rulebook search (Cap 94).

The corpus under test is *only* the curated clauses in
:mod:`camt053_mcp.rulebook`; no external rulebook text is indexed.
"""

from __future__ import annotations

import math

import pytest

pytest.importorskip("sqlite_vec")

from camt053_mcp import rulebook, vector_search  # noqa: E402
from camt053_mcp.server import search_rulebook_vector  # noqa: E402


class TestEmbedding:
    """The deterministic lexical vectoriser."""

    def test_embedding_is_unit_normalised(self) -> None:
        """A non-empty text embeds to a fixed-length unit vector."""
        vec = vector_search._embed("structured postal address")
        assert len(vec) == vector_search._EMBED_DIM
        norm = math.sqrt(sum(value * value for value in vec))
        assert math.isclose(norm, 1.0, rel_tol=1e-6)

    def test_embedding_is_deterministic(self) -> None:
        """The same text always embeds to the same vector."""
        first = vector_search._embed("uetr end-to-end tracking")
        second = vector_search._embed("uetr end-to-end tracking")
        assert first == second

    def test_tokenless_text_yields_zero_vector(self) -> None:
        """Text with no alphanumeric tokens embeds to an all-zero vector."""
        vec = vector_search._embed("!!! --- ???")
        assert vec == [0.0] * vector_search._EMBED_DIM


class TestSearch:
    """Ranked retrieval over the curated rulebook clauses."""

    def test_structured_address_query_hits_cbpr_clause(self) -> None:
        """The CBPR+ 2026 address clause ranks in the top results."""
        result = vector_search.search(
            "structured address requirement", top_k=5
        )
        clauses = {row["clause"] for row in result["results"]}
        assert "structured-address-mandate-nov-2026" in clauses

    def test_results_carry_descending_scores(self) -> None:
        """Results are ranked most-similar first with scores in [0, 1]."""
        result = vector_search.search("instant payment settlement", top_k=4)
        scores = [row["score"] for row in result["results"]]
        assert scores == sorted(scores, reverse=True)
        assert all(0.0 <= score <= 1.0 for score in scores)

    def test_results_are_full_clause_dicts(self) -> None:
        """Each result carries the full clause plus a score; envelope honest."""
        result = vector_search.search("verification of payee", top_k=1)
        top = result["results"][0]
        expected = {
            "scheme",
            "version",
            "clause",
            "title",
            "summary",
            "source_url",
            "as_of",
            "score",
        }
        assert expected <= top.keys()
        assert result["method"]
        assert "disclaimer" in result
        assert result["query"] == "verification of payee"

    def test_top_k_limits_returned_count(self) -> None:
        """top_k caps the number of returned clauses."""
        result = vector_search.search("address", top_k=2)
        assert len(result["results"]) == 2
        assert result["returned"] == 2

    def test_top_k_clamped_to_corpus_size(self) -> None:
        """A top_k above the corpus size returns the whole corpus."""
        corpus_size = len(rulebook.list_clauses())
        result = vector_search.search("payment", top_k=corpus_size + 50)
        assert result["returned"] == corpus_size

    def test_empty_query_errors(self) -> None:
        """A blank query yields a graceful error payload."""
        assert "error" in vector_search.search("   ", top_k=3)

    def test_non_positive_top_k_errors(self) -> None:
        """A non-positive top_k yields a graceful error payload."""
        assert "error" in vector_search.search("address", top_k=0)


class TestMissingExtra:
    """Graceful degradation when the optional [vector] extra is absent."""

    def test_missing_sqlite_vec_returns_graceful_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A missing sqlite-vec surfaces an install hint, not a crash."""

        def _boom() -> object:
            """Simulate sqlite-vec not being installed."""
            raise ImportError("no module named sqlite_vec")

        monkeypatch.setattr(vector_search, "_import_sqlite_vec", _boom)
        result = vector_search.search("structured address", top_k=3)
        assert "error" in result
        assert "camt053-mcp[vector]" in result["error"]


class TestServerTool:
    """The MCP tool wrapper delegates to the search module."""

    def test_tool_wrapper_returns_ranked_results(self) -> None:
        """search_rulebook_vector returns ranked clause results."""
        result = search_rulebook_vector("uetr mandatory", top_k=2)
        assert len(result["results"]) == 2
        assert result["results"][0]["scheme"] in {"SEPA", "CBPR+", "HVPS+"}
