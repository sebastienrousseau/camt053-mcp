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

"""Guard against version drift between pyproject.toml and the package."""

import re
from pathlib import Path

import camt053_mcp

_PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def _pyproject_version() -> str:
    """Extract the project version from pyproject.toml via regex.

    A regex is used deliberately instead of ``tomllib`` because Python 3.10
    is in the CI matrix and ``tomllib`` only landed in the stdlib in 3.11.
    """
    text = _PYPROJECT.read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert match is not None, "no version found in pyproject.toml"
    return match.group(1)


def test_version_matches_pyproject() -> None:
    """``__version__`` must equal the version declared in pyproject.toml."""
    assert camt053_mcp.__version__ == _pyproject_version()
