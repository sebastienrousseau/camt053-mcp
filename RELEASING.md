<!-- SPDX-License-Identifier: Apache-2.0 OR MIT -->

# Releasing camt053-mcp

This document defines **what merits a release** and **how to cut one**,
so versions are deliberate rather than ad-hoc.

## Versioning scheme

camt053-mcp is versioned in lockstep with the
[`camt053`](https://github.com/sebastienrousseau/camt053) suite: the
core library and its sibling packages (`camt053-lsp`,
`camt053-writer-xlsx`, `camt053-loader-mt940`, `camt053-loader-mt942`)
ship the same `0.0.X`. This keeps the agent surface aligned with the
core library's public API and lets a user install the suite at one
pin. `scripts/check_suite_consistency.py` and the scheduled `Suite
Consistency` workflow fail when the packages drift.

## What merits a release

Cut a new version when there is user-visible change to ship - bug fixes,
security or dependency patches, new tools / resources / prompts, or
documentation that ships in the package - or when the suite bumps and
this package must follow.

## Pre-flight checklist

A release is ready only when **all** of the following hold on `main`:

1. `make check` is green (lint + type-check + tests at 100% coverage +
   examples).
2. `interrogate` reports 100% docstring coverage; `mypy --strict`,
   `ruff`, `black` are clean on `camt053_mcp/`, `tests/` and `benches/`.
3. Every Dependabot / CodeQL / bandit / pip-audit alert is resolved or
   has a documented, expiring suppression.
4. `CHANGELOG.md` has a dated section for the new version describing the
   change set (this is the single source of truth for the release).
5. The version is identical in `pyproject.toml`,
   `camt053_mcp/__init__.py`, `glama.json` (including its Docker tag),
   `server.json` and the changelog (enforced by
   `scripts/verify_versions.py`, which the `Version sources agree`
   workflow runs). The Glama directory and the MCP registry read those
   two manifests; a release that forgets them shows an old version to
   every agent that browses for the server.
6. `poetry.lock` is current: the SBOM job fails on a stale lock.

## Cutting the release

1. Bump the version in `pyproject.toml`, `camt053_mcp/__init__.py`,
   `glama.json` and `server.json`, and add the `CHANGELOG.md` section, in
   a single PR.
2. Merge the PR to `main` once CI is green.
3. Push a signed tag:

   ```bash
   git tag -s vX.Y.Z -m "camt053-mcp vX.Y.Z" <merge-commit>
   git push origin vX.Y.Z
   ```

4. The tag triggers `release.yml`: it builds the distributions, runs
   `twine check`, attaches a SLSA build provenance attestation,
   publishes to PyPI through OIDC trusted publishing, signs the
   distributions with cosign (keyless) and publishes the GitHub release
   with the artifacts. A second job produces the CycloneDX and SPDX
   SBOMs and the licence report.
5. The same tag triggers `publish-mcp.yml`, which publishes `server.json`
   to the official MCP registry through GitHub OIDC.

## After releasing

- Confirm the version is live on
  [PyPI](https://pypi.org/project/camt053-mcp/) and the GitHub release
  is published (not draft), with the SBOMs attached.
- Verify a clean install: `pip install camt053-mcp==X.Y.Z`.
- Check the [MCP registry](https://registry.modelcontextprotocol.io)
  and Glama listings show the new version.

## Optional CI integrations

These are deliberately gated so an empty / un-set secret skips the
step rather than failing the build:

- **PyPI trusted publisher** (`release.yml`): configured at
  <https://pypi.org/manage/account/publishing/>. The publisher claim
  set is `repo:sebastienrousseau/camt053-mcp:environment:pypi` with
  `workflow_ref` pointing at `.github/workflows/release.yml`.
- **MCP registry publisher** (`publish-mcp.yml`): `mcp-publisher login
  github-oidc`; no secret to store.
- **Docker image**: the `Dockerfile` builds the server for a container
  deployment; images are not published by CI.
