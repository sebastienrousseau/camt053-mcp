# SPDX-FileCopyrightText: 2026 Sebastien Rousseau <sebastian.rousseau@gmail.com>
# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Every third-party module camt053_mcp imports is a declared dependency.

A package imported directly but only installed because another
dependency requires it (uvicorn, starlette and pydantic arrive with the
MCP SDK) breaks without warning when that dependency stops requiring
it. Module-level imports are read with ``ast``; imports under
``if TYPE_CHECKING:`` are for type checkers only and are skipped.
"""

from __future__ import annotations

import ast
import sys
import tomllib
from importlib.metadata import packages_distributions
from pathlib import Path

from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "camt053_mcp"


def _imported_names(tree: ast.Module) -> set[str]:
    """Top-level module names imported at module level."""
    names: set[str] = set()
    for node in tree.body:
        body = [node]
        if isinstance(node, ast.If):
            test = node.test
            if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
                continue
            body = node.body
        elif isinstance(node, ast.Try):
            body = node.body
        for statement in body:
            if isinstance(statement, ast.Import):
                names.update(a.name.split(".")[0] for a in statement.names)
            elif (
                isinstance(statement, ast.ImportFrom)
                and statement.level == 0
                and statement.module
            ):
                names.add(statement.module.split(".")[0])
    return names


def _third_party_imports() -> dict[str, set[str]]:
    """Map each third-party module name to the files that import it."""
    found: dict[str, set[str]] = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for name in _imported_names(tree):
            if name in sys.stdlib_module_names or name in (
                "__future__",
                "camt053_mcp",
            ):
                continue
            found.setdefault(name, set()).add(path.name)
    return found


def _declared() -> set[str]:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        deps = tomllib.load(handle)["tool"]["poetry"]["dependencies"]
    return {canonicalize_name(name) for name in deps if name != "python"}


def test_every_imported_distribution_is_declared() -> None:
    distributions = packages_distributions()
    declared = _declared()
    missing = {
        module: sorted(files)
        for module, files in _third_party_imports().items()
        if not declared
        & {canonicalize_name(d) for d in distributions.get(module, [module])}
    }
    assert missing == {}, f"imported but not declared: {missing}"


def test_type_checking_imports_are_skipped() -> None:
    tree = ast.parse(
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n    import opentelemetry\n"
        "try:\n    import uvicorn\nexcept ImportError:\n    pass\n"
    )
    assert _imported_names(tree) == {"typing", "uvicorn"}
