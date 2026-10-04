<!-- SPDX-FileCopyrightText: 2026 Sebastien Rousseau <sebastian.rousseau@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 OR MIT -->

# AGENTS.md

Invariants for AI-assisted contributions to `camt053-mcp`. Read this before changing anything.

Everything here applies equally to humans and automated agents. It is addressed to agents because agents can make breaking changes across multiple files before anyone notices.

## 1. Core Invariants

1. **Strict SemVer sequencing policy**: Public releases stay on the `0.0.x` line and increment strictly by `0.0.1`. Never manually edit version numbers outside the active release branch `feat/v<next-version>`. `v0.1.0` is forbidden until `v0.0.999` exists.
2. **Single Active Release PR Invariant**: Across all repositories, there MUST be at most ONE active pull request targeting `main`, which MUST be the release iteration branch `feat/v<next-version>`.
3. **Dual licensing**: The repository is dual-licensed under Apache-2.0 OR MIT. All files must declare an SPDX license header.
4. **Single source of truth**: The version in `pyproject.toml` is the single source of truth. It must agree with `__version__`, `glama.json`, `server.json`, `CITATION.cff`, and `CHANGELOG.md` (verified by `scripts/verify_versions.py`).
5. **Deterministic bank-to-customer statement processing**: Parsing, validation, reverse mapping, audit trails, and journal exports must remain deterministic, tamper-evident, and closed-world.

## 2. Before You Claim To Be Done (Verification Gates)

Before concluding any task or preparing a commit, run:

```console
make check
```

Or run the individual gates:

```console
poetry run pytest
poetry run ruff check camt053_mcp/ tests/
poetry run black --check camt053_mcp/ tests/
poetry run mypy camt053_mcp/
python3 scripts/verify_versions.py
```

All unit tests and conformance tests must pass with 0 failures, 0 warnings, and 100% line and branch coverage.

## 3. Hygiene First

Before any feature, fix, or release work, check repository health:
1. Verify CI is green on `main`.
2. Ensure linter and formatter pass without warnings or new suppressions.
3. Every function must remain within the complexity ceilings (Cyclomatic ≤ 10, Cognitive ≤ 15, Halstead ≤ 30, Lines of code ≤ 60 per function, ≤ 500 per file).

## 4. Things That Look Like Bugs and Are Not

- **Framework adapters are lazy**: `camt053_mcp.adapters` exports tools for LangChain, CrewAI, and LlamaIndex without making those heavy packages mandatory dependencies.
- **Vector search and tracing are optional extras**: `sqlite-vec` (`[vector]`) and OpenTelemetry (`[otel]`) are optional extras; server functions degrade gracefully when absent.
