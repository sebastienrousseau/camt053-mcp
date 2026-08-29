# Rulebook vector search

`search_rulebook_vector` answers the question `cite_rulebook` cannot:
*which clause is this about?* `cite_rulebook` needs an exact
`scheme`/`version`/`clause` triple. Vector search takes a description and
returns the closest curated clauses, ranked, each with a similarity
score.

```jsonc
search_rulebook_vector("structured address requirement", top_k=3)
```

```jsonc
{
  "query": "structured address requirement",
  "top_k": 3,
  "returned": 3,
  "method": "deterministic lexical-vector cosine retrieval (BLAKE2b-hashed word + char 3/4-gram term frequencies, 256-dim, L2-normalised, ranked by sqlite-vec cosine distance)",
  "results": [
    {
      "scheme": "HVPS+",
      "version": "2026",
      "clause": "structured-address-alignment",
      "title": "HVPS+ aligns with CBPR+ structured-address rule",
      "summary": "HVPS+ market practice guidelines align with CBPR+ on the structured postal address requirement ...",
      "source_url": "https://www.swift.com/standards/iso-20022-programme/hvps-plus",
      "as_of": "2026-06-22",
      "score": 0.702729
    }
  ]
}
```

The usual flow is two calls: search to find the clause id, then
`cite_rulebook` for the full citation. `list_rulebook_clauses` browses
everything if you would rather scan than search.

## How retrieval works

It is **not** a neural embedding model. There is no downloaded model, no
inference, and no network call at query time.

Each clause summary and each query are turned into a fixed
256-dimension term-frequency vector:

1. The text is tokenised into whole words.
2. Each token also contributes character 3-grams and 4-grams, so
   `address` matches `addresses` without needing a stemmer.
3. Every feature is mapped to a bucket with **BLAKE2b** rather than
   Python's built-in `hash`, which is salted per process. This is what
   makes results identical across processes and CI runs.
4. The vector is L2-normalised, so cosine distance is a pure direction
   comparison and long clauses do not outrank short ones simply for
   being long.

`sqlite-vec` holds the corpus in an in-memory virtual table and does the
cosine KNN. The corpus is built once per process and reused.

## What this buys, and what it costs

**Deterministic.** The same query returns the same ranking every time,
which means a result can be quoted in a test and stay true.

**Offline.** Nothing is fetched. The index is built from the clauses
already compiled into the package.

**Lexical, not semantic.** This is the real limit. The search matches
*wording*, not meaning. A query that shares no vocabulary with a clause
will not find it, however closely the ideas relate — asking for "postcode
formatting" will not surface a clause that only ever says "structured
address". Describe the rule in the words the rulebook would use.

## What is indexed

Only the curated clause summaries that already back `cite_rulebook`. No
external, copyrighted, or auth-gated rulebook text is stored, indexed, or
returned. The `source_url` on each result points at the publisher so you
can read the original where it is publicly available.

## Installation

`sqlite-vec` is an optional dependency, imported lazily. The base
install pulls in neither it nor anything else for this feature:

```sh
python -m pip install 'camt053-mcp[vector]'
```

Call the tool without the extra and it returns a normal error payload
naming the extra, rather than raising — a client on a base install gets a
usable message rather than a stack trace.

## The build-flag requirement

There is a second requirement that catches people out. Loading
`sqlite-vec` needs a CPython **compiled with loadable SQLite extension
support** (`--enable-loadable-sqlite-extensions`). The python.org macOS
installers and several distribution packages ship it **disabled**, and on
such an interpreter `sqlite-vec` installs perfectly and still cannot
load.

Before 0.0.19 that surfaced as a bare `AttributeError` from inside the
index build. It now returns the same kind of actionable error the missing
extra does, naming the flag to look for.

To check your interpreter:

```sh
python -c "import sqlite3; print(hasattr(sqlite3.connect(':memory:'), 'enable_load_extension'))"
```

`True` means you are fine. If it prints `False`, use a Homebrew, `uv`, or
`pyenv` build — those normally have it enabled.

The test suite treats this the same way: the tests that need a live index
skip on an interpreter that cannot load extensions, rather than failing,
exactly as they already skip when `sqlite-vec` is not installed at all.
