# SPDX-FileCopyrightText: 2026 Sebastien Rousseau <sebastian.rousseau@gmail.com>
# SPDX-License-Identifier: Apache-2.0 OR MIT
"""scripts/verify_versions.py holds the suite floors to this version."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parent.parent / "scripts" / "verify_versions.py"
)


@pytest.fixture
def family(monkeypatch, tmp_path):
    """``_check_family`` run against a tree written to ``tmp_path``."""
    spec = importlib.util.spec_from_file_location("verify_versions", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    (tmp_path / "requirements").mkdir()

    def run(pyproject: str, req: str, docker: str | None):
        (tmp_path / "pyproject.toml").write_text(pyproject)
        (tmp_path / "requirements" / "test.in").write_text(req)
        if docker is not None:
            (tmp_path / "Dockerfile").write_text(docker)
        return module._check_family("0.0.23")[1]

    return run


PYPROJECT = """[tool.poetry.dependencies]
camt053 = ">=0.0.23,<1"
camt053-loader-mt940 = ">=0.0.23"
camt053-loader-mt942 = ">=0.0.23"
"""
REQ = "camt053>=0.0.23,<1\ncamt053-loader-mt940>=0.0.23\n"
DOCKER = 'ARG CAMT053_PIP_SPEC="camt053==0.0.23"\n'


def test_a_tree_on_the_suite_version_is_clean(family):
    assert family(PYPROJECT, REQ, DOCKER) == []


def test_a_stale_pyproject_floor_fails(family):
    stale = PYPROJECT.replace('camt053 = ">=0.0.23', 'camt053 = ">=0.0.17')
    assert family(stale, REQ, DOCKER) == [
        "pyproject.toml: camt053>=0.0.17, the suite is at 0.0.23"
    ]


def test_a_stale_requirements_floor_fails(family):
    stale = REQ.replace("mt940>=0.0.23", "mt940>=0.0.9")
    assert family(PYPROJECT, stale, DOCKER) == [
        "requirements/test.in: camt053-loader-mt940>=0.0.9, the suite is "
        "at 0.0.23"
    ]


def test_a_stale_docker_pin_fails(family):
    stale = DOCKER.replace("0.0.23", "0.0.14")
    assert family(PYPROJECT, REQ, stale) == ["Dockerfile pins camt053==0.0.14"]


def test_no_dockerfile_is_not_a_problem(family):
    assert family(PYPROJECT, REQ, None) == []
