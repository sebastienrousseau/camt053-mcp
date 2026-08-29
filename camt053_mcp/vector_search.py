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

"""Lexical-vector similarity search over the curated rulebook (Cap 94).

Backs the ``search_rulebook_vector`` MCP tool. It builds a small
`sqlite-vec <https://github.com/asg017/sqlite-vec>`_ vector index over the
clauses curated in :mod:`camt053_mcp.rulebook` (the maintainer-authored
``_ENTRIES`` registry) and answers natural-language queries with the
top-k most similar clauses.

**Corpus provenance.** The *only* text indexed is the curated
rule-level summaries already in :mod:`camt053_mcp.rulebook`. No external,
copyrighted, or auth-gated rulebook text (SWIFT / Fed / ISO) is scraped,
embedded, or redistributed here. Adding clauses is done the same way as
for every other rulebook tool: extend ``rulebook.py``; this indexer picks
them up automatically.

**Embedding approach — honest labelling.** Retrieval uses a
*deterministic lexical-vector cosine* scheme, **not** a large neural
embedding model. Each clause (and the query) is turned into a fixed
256-dimension vector by the *hashing trick*: alphanumeric word tokens and
character 3/4-grams are hashed (via BLAKE2b, so the mapping is stable
across processes and CI runs — unlike the salted built-in ``hash``) into
buckets whose term-frequency counts form the vector, which is then
L2-normalised. ``sqlite-vec`` stores the vectors and ranks them by cosine
distance. This is offline, dependency-light, and fully reproducible: no
model download and no network at query time, so CI is deterministic.

**Optional dependency.** ``sqlite-vec`` lives behind the optional
``[vector]`` extra and is imported lazily. When it is not installed the
tool returns a graceful ``{"error": ...}`` payload telling the operator to
``pip install 'camt053-mcp[vector]'`` rather than raising at import time.
"""

from __future__ import annotations

import hashlib
import math
import re
import sqlite3
import struct
from typing import Any

from camt053_mcp import rulebook

#: Fixed dimensionality of every clause / query vector. Small enough to
#: keep the in-memory index trivial, large enough to keep hash collisions
#: between the rulebook's vocabulary rare.
_EMBED_DIM = 256

#: Character n-gram sizes folded into each token's features (in addition to
#: the whole-word token). 3/4-grams let a query like ``"address"`` match the
#: clause word ``"addresses"`` without an explicit stemmer.
_NGRAM_SIZES = (3, 4)

#: Alphanumeric token pattern used to split text into words before hashing.
_TOKEN_RE = re.compile(r"[a-z0-9]+")

#: Human-readable, honest description of the retrieval method, surfaced in
#: the tool response so callers are never misled into thinking this is a
#: large neural embedding.
_METHOD = (
    "deterministic lexical-vector cosine retrieval "
    "(BLAKE2b-hashed word + char 3/4-gram term frequencies, "
    f"{_EMBED_DIM}-dim, L2-normalised, ranked by sqlite-vec cosine distance)"
)

#: Install hint returned when the optional ``[vector]`` extra is absent.
_NO_EXTENSION_HINT = (
    "search_rulebook_vector needs a Python built with loadable SQLite "
    "extension support, which this interpreter lacks "
    "(sqlite3.Connection.enable_load_extension is missing). Reinstall "
    "Python from a build configured with "
    "--enable-loadable-sqlite-extensions -- python.org macOS installers "
    "and some distribution packages ship it disabled. Homebrew, uv and "
    "pyenv builds normally have it enabled."
)

_INSTALL_HINT = (
    "search_rulebook_vector requires the optional 'vector' extra "
    "(sqlite-vec). Install it with: pip install 'camt053-mcp[vector]'"
)


class VectorSearchUnavailable(RuntimeError):
    """Base for the reasons vector search cannot run on this machine.

    Both causes are environmental rather than a fault in the query, and
    both are reported to the caller as an actionable error string rather
    than raised through the tool boundary.
    """


class VectorExtraNotInstalled(VectorSearchUnavailable):
    """Raised when the optional ``[vector]`` extra (sqlite-vec) is absent."""


class VectorExtensionUnsupported(VectorSearchUnavailable):
    """Raised when this interpreter cannot load SQLite extensions.

    ``sqlite3.Connection.enable_load_extension`` only exists when CPython
    was compiled with ``--enable-loadable-sqlite-extensions``. The
    python.org macOS installers and several distribution builds ship it
    disabled, so ``sqlite-vec`` can be installed and still be unloadable.
    Without this check that case surfaces as a bare ``AttributeError``
    from deep inside the index build, which tells the caller nothing.
    """


#: Memoised corpus: a list of ``(clause dict, embedding)`` pairs built once
#: from :mod:`camt053_mcp.rulebook`. Retained module state is read *and*
#: written only through :func:`_corpus`, never write-only.
_CORPUS_CACHE: list[tuple[dict[str, Any], list[float]]] | None = None


def _import_sqlite_vec() -> Any:
    """Import and return the optional ``sqlite_vec`` module.

    Isolated as a single seam so the missing-extra path is easy to force in
    tests (monkeypatch this to raise ``ImportError``) and so the lazy import
    lives in exactly one place.

    Raises:
        ImportError: When the ``[vector]`` extra is not installed.
    """
    import sqlite_vec

    return sqlite_vec


def _feature_counts(text: str) -> dict[str, int]:
    """Return the raw feature counts for ``text``.

    Features are whole-word tokens (``"w:<token>"``) plus padded character
    3/4-grams of each token (``"c:<gram>"``); both are counted so a query
    matches on both exact words and shared substrings.

    Args:
        text: The raw text to featurise (case is folded to lower).
    """
    counts: dict[str, int] = {}
    for token in _TOKEN_RE.findall(text.lower()):
        counts["w:" + token] = counts.get("w:" + token, 0) + 1
        padded = "^" + token + "$"
        for size in _NGRAM_SIZES:
            for start in range(len(padded) - size + 1):
                gram = "c:" + padded[start : start + size]
                counts[gram] = counts.get(gram, 0) + 1
    return counts


def _hash_bucket(feature: str) -> int:
    """Map a feature string to a stable vector bucket in ``[0, _EMBED_DIM)``.

    Uses BLAKE2b (not the salted built-in ``hash``) so the same feature maps
    to the same bucket in every process and CI run, making retrieval fully
    reproducible.

    Args:
        feature: The feature string (a ``"w:"`` or ``"c:"`` token).
    """
    digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % _EMBED_DIM


def _embed(text: str) -> list[float]:
    """Embed ``text`` into an L2-normalised ``_EMBED_DIM`` vector.

    Applies the hashing trick over :func:`_feature_counts` and normalises
    the result to unit length so cosine distance is a pure angle. Empty or
    token-free text yields an all-zero vector (its cosine distance to every
    clause is then undefined-but-finite and ranks last).

    Args:
        text: The text to embed (a clause blob or a user query).
    """
    vector = [0.0] * _EMBED_DIM
    for feature, count in _feature_counts(text).items():
        vector[_hash_bucket(feature)] += float(count)
    norm = math.sqrt(sum(value * value for value in vector))
    if norm > 0.0:
        vector = [value / norm for value in vector]
    return vector


def _clause_text(entry: dict[str, Any]) -> str:
    """Return the text blob indexed for one rulebook clause.

    Concatenates the fields that carry lexical signal (scheme, version,
    clause id, title, summary) so a query can match on any of them.

    Args:
        entry: One rulebook clause dict from ``rulebook.list_clauses``.
    """
    return " ".join(
        str(entry[field])
        for field in ("scheme", "version", "clause", "title", "summary")
    )


def _build_corpus() -> list[tuple[dict[str, Any], list[float]]]:
    """Build the ``(clause, embedding)`` corpus from the rulebook registry.

    Reads every curated clause via :func:`camt053_mcp.rulebook.list_clauses`
    and embeds each one. Called once and memoised by :func:`_corpus`.
    """
    return [
        (entry, _embed(_clause_text(entry)))
        for entry in rulebook.list_clauses()
    ]


def _corpus() -> list[tuple[dict[str, Any], list[float]]]:
    """Return the memoised clause corpus, building it on first use.

    The module-level cache is both read and written here (never write-only),
    so the vectors are computed once per process yet the retained global has
    a genuine reader.
    """
    global _CORPUS_CACHE
    if _CORPUS_CACHE is None:
        _CORPUS_CACHE = _build_corpus()
    return _CORPUS_CACHE


def _pack(vector: list[float]) -> bytes:
    """Pack a float vector into the little-endian float32 blob sqlite-vec wants.

    Args:
        vector: The embedding to serialise.
    """
    return struct.pack(f"{len(vector)}f", *vector)


def _build_index(
    corpus: list[tuple[dict[str, Any], list[float]]],
) -> sqlite3.Connection:
    """Build a fresh in-memory ``sqlite-vec`` cosine index over ``corpus``.

    A new ``:memory:`` connection is created per call so the index is
    hermetic and thread-safe (no cross-call SQLite connection sharing). Each
    clause is inserted at ``rowid = its position in corpus`` so results map
    straight back to the clause dict.

    Args:
        corpus: The ``(clause, embedding)`` pairs to index.

    Raises:
        VectorExtraNotInstalled: When the ``[vector]`` extra is absent.
        VectorExtensionUnsupported: When this interpreter was built
            without loadable SQLite extension support.
    """
    try:
        sqlite_vec = _import_sqlite_vec()
    except ImportError as exc:
        raise VectorExtraNotInstalled(_INSTALL_HINT) from exc

    conn = sqlite3.connect(":memory:")
    if not hasattr(conn, "enable_load_extension"):
        conn.close()
        raise VectorExtensionUnsupported(_NO_EXTENSION_HINT)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute(
        "create virtual table vec_rulebook using "
        f"vec0(embedding float[{_EMBED_DIM}] distance=cosine)"
    )
    for rowid, (_entry, embedding) in enumerate(corpus):
        conn.execute(
            "insert into vec_rulebook(rowid, embedding) values (?, ?)",
            (rowid, _pack(embedding)),
        )
    return conn


def search(query: str, top_k: int = 5) -> dict[str, Any]:
    """Return the top-k rulebook clauses most similar to ``query``.

    Embeds ``query`` with the same deterministic lexical vectoriser used for
    the corpus, runs a cosine KNN over the in-memory ``sqlite-vec`` index,
    and returns each matching clause augmented with a ``score`` (cosine
    similarity in ``[0, 1]``, higher is closer).

    Args:
        query: The natural-language search string.
        top_k: The maximum number of clauses to return (clamped to the
            corpus size). Must be a positive integer.

    Returns:
        On success, ``{"query", "top_k", "returned", "method", "results",
        "disclaimer"}`` where ``results`` is the ranked clause list. On a bad
        argument or a missing ``[vector]`` extra, an ``{"error": ...}``
        payload instead.
    """
    if not isinstance(query, str) or not query.strip():
        return {"error": "query must be a non-empty string"}
    if top_k < 1:
        return {"error": "top_k must be a positive integer"}

    corpus = _corpus()
    try:
        conn = _build_index(corpus)
    except VectorSearchUnavailable as exc:
        return {"error": str(exc)}

    try:
        effective_k = min(top_k, len(corpus))
        rows = conn.execute(
            "select rowid, distance from vec_rulebook "
            "where embedding match ? and k = ? order by distance",
            (_pack(_embed(query)), effective_k),
        ).fetchall()
    finally:
        conn.close()

    results: list[dict[str, Any]] = []
    for rowid, distance in rows:
        entry, _embedding = corpus[rowid]
        results.append({**entry, "score": round(1.0 - float(distance), 6)})

    return {
        "query": query,
        "top_k": top_k,
        "returned": len(results),
        "method": _METHOD,
        "results": results,
        "disclaimer": rulebook._DISCLAIMER,
    }
