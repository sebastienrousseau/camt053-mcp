"""Exercise every shipping example end-to-end as part of CI.

Each script under ``examples/`` whose name starts with two digits (the
per-tool examples) is imported as a module and its ``main`` coroutine
is run. The test passes if the example runs without raising — it does
not assert on stdout. This guarantees that the examples stay aligned
with the public MCP server shape: any drift breaks the build.

The example ``examples/mcp_tools.py`` (the umbrella overview script
that predated the per-tool split) is included too.
"""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import pytest

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"
# Each example file self-bootstraps its sys.path so ``_helpers`` resolves;
# nothing extra is needed here.


def _example_paths() -> list[Path]:
    return sorted(
        p for p in EXAMPLES_DIR.glob("*.py")
        if p.stem[0].isdigit() or p.stem == "mcp_tools"
    )


@pytest.mark.parametrize(
    "example",
    _example_paths(),
    ids=lambda p: p.stem,
)
def test_example_runs(example: Path) -> None:
    spec = importlib.util.spec_from_file_location(example.stem, example)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert hasattr(module, "main"), f"{example.name} missing main()"
    if asyncio.iscoroutinefunction(module.main):
        asyncio.run(module.main())
    else:
        module.main()
