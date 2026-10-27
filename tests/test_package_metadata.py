# VisTest - visual regression testing for Python and Playwright.
# Copyright 2026 Kirill Kulagin
# SPDX-License-Identifier: Apache-2.0
#
# This file is part of VisTest, licensed under the Apache License, Version 2.0.
# You may not use it except in compliance with the License. See LICENSE for
# the terms and NOTICE for attribution and the trademark.

"""What PyPI shows about the package says what is true (o3).

The Python versions on the project page are the ones the wheel is installed
and run on in the Install check workflow, starting at requires-python; and
every extra names only what something in the repository uses.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]


def _matrix() -> list[str]:
    text = (ROOT / ".github" / "workflows" / "install-check.yml").read_text("utf-8")
    rows = re.findall(r"python:\s*\[([^\]]+)\]", text)
    assert rows, "the Install check matrix was not found"
    return sorted({v.strip().strip("\"'") for row in rows for v in row.split(",")},
                  key=lambda v: tuple(map(int, v.split("."))))


def test_the_python_classifiers_are_the_versions_the_wheel_is_checked_on():
    listed = [c.rsplit(" :: ", 1)[1] for c in PROJECT["classifiers"]
              if re.fullmatch(r"Programming Language :: Python :: 3\.\d+", c)]
    assert listed == _matrix()
    assert PROJECT["requires-python"] == f">={listed[0]}"


def test_it_says_it_is_a_pytest_plugin():
    assert "Framework :: Pytest" in PROJECT["classifiers"]
    assert "pytest11" in tomllib.loads(
        (ROOT / "pyproject.toml").read_text("utf-8"))["project"]["entry-points"]


def test_the_dev_extra_names_nothing_the_repository_does_not_import():
    #  httpx stayed here from the server's tests after o1; the library's never used it.
    used = "\n".join(p.read_text("utf-8", errors="replace")
                     for d in ("vistest", "tests", "scripts", "examples")
                     for p in (ROOT / d).rglob("*.py"))
    tools = {"pytest", "pytest-cov", "pytest-xdist", "ruff"}
    for req in PROJECT["optional-dependencies"]["dev"]:
        name = re.split(r"[<>=\[ ;]", req, maxsplit=1)[0]
        assert name in tools or re.search(rf"\bimport {name}\b|\bfrom {name}\b", used), name
