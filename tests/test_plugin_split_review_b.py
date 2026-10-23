# VisTest - visual regression testing for Python and Playwright.
# Copyright 2026 Kirill Kulagin
# SPDX-License-Identifier: Apache-2.0
#
# This file is part of VisTest, licensed under the Apache License, Version 2.0.
# You may not use it except in compliance with the License. See LICENSE for
# the terms and NOTICE for attribution and the trademark.

"""The library's pytest plugin, and the server's half of it (review v1: R2, R6, 2.7).

With `vistest` or `vistest[browser]` alone, `pytest --fixtures` and
`pytest --help` show the library: `vistest`, `vistest_config`, and five
flags. The server's fixtures and flags come with `vistest-server`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from . import _no_server

LIBRARY_FLAGS = ["--vistest-update", "--vistest-baselines", "--vistest-platform",
                 "--vistest-report", "--vistest-config"]
SERVER_FLAGS = ["--vistest-api", "--vistest-perceptual", "--vistest-fail-on"]
SERVER_FIXTURES = ["visual", "visual_soft", "_vistest_session", "vistest_run_id"]


def _flags(help_text: str) -> list[str]:
    return sorted(set(re.findall(r"(--vistest-[a-z-]+)", help_text)))


def _fixtures(text: str) -> set[str]:
    return set(re.findall(r"^([A-Za-z_][A-Za-z0-9_]*)(?: \[| --)", text, re.M))


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", "utf-8")
    (tmp_path / "test_x.py").write_text("def test_x():\n    pass\n", "utf-8")
    return tmp_path


def test_without_the_server_pytest_help_has_the_five_library_flags(project):
    done = _no_server.run("pytest", ["--help"], project)
    assert done.returncode == 0, done.stderr
    assert _flags(done.stdout) == sorted(LIBRARY_FLAGS)


def test_without_the_server_pytest_fixtures_has_no_server_fixture(project):
    done = _no_server.run("pytest", ["--fixtures", "-v", "-p", "no:cacheprovider"],
                          project)
    assert done.returncode == 0, done.stdout + done.stderr
    fixtures = _fixtures(done.stdout)
    assert {"vistest", "vistest_config"} <= fixtures, done.stdout
    assert not fixtures & set(SERVER_FIXTURES), fixtures & set(SERVER_FIXTURES)


def test_without_the_server_a_test_asking_for_visual_is_told_it_is_not_there(project):
    (project / "test_x.py").write_text("def test_x(visual):\n    pass\n", "utf-8")
    done = _no_server.run("pytest", ["-q", "-p", "no:cacheprovider", "-p", "no:xdist"],
                          project)
    assert done.returncode == 1, done.stdout + done.stderr
    assert "fixture 'visual' not found" in done.stdout


