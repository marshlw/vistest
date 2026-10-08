# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""«→» and «Δ» on a console that cannot print them (from review A2; step C, 9).

On a Windows runner Python writes to a pipe in cp1252, which has neither.
pytest then printed a region's line as `fill: #c8c8c8 \\u2192 #1e1e1e`, and the
CLI as `fill: #c8c8c8 ? #1e1e1e, ?E00`. Both write `->` and `dE` now — under
`PYTHONIOENCODING=cp1252`, which is how the runner's pipe is emulated here.
"""

from __future__ import annotations

import os
import subprocess
import sys

import numpy as np
import pytest

from vistest.core import pngio
from vistest.library import words


def _env(**extra) -> dict:
    clean = {k: v for k, v in os.environ.items() if not k.startswith("VISTEST_")}
    return {**clean, **extra}


@pytest.fixture
def two(tmp_path):
    a = np.full((60, 80, 3), 255, np.uint8)
    b = a.copy()
    b[20:40, 20:60] = (37, 99, 235)
    (tmp_path / "a.png").write_bytes(pngio.encode(a))
    (tmp_path / "b.png").write_bytes(pngio.encode(b))
    return tmp_path


def test_the_spelling_where_the_console_has_no_place_for_a_character():
    text = "fill: #ffffff → #2563eb, color difference 66.5 (ΔE00: 1 ≈ barely visible)"
    assert words.console(text, _Stream("utf-8")) == text
    assert words.console(text, _Stream("cp1252")) == \
        "fill: #ffffff -> #2563eb, color difference 66.5 (dE00: 1 ~ barely visible)"
    #  What cp1252 holds stays: the dash, the guillemets, the times sign.
    assert words.console("a — «b» 3×1", _Stream("cp1252")) == "a — «b» 3×1"


class _Stream:
    def __init__(self, encoding):
        self.encoding = encoding


def test_vistest_compare_on_a_cp1252_console(two):
    done = subprocess.run([sys.executable, "-m", "vistest.cli", "compare", "a.png", "b.png",
                           "-o", "out"], cwd=two, capture_output=True, timeout=300,
                          env=_env(PYTHONIOENCODING="cp1252"))
    out = done.stdout.decode("cp1252")
    assert done.returncode == 1, out + done.stderr.decode("cp1252", "replace")
    assert "#ffffff -> #2563eb" in out, out
    assert "(color difference is dE00 (CIEDE2000): 1 ~ barely visible" in out, out
    assert "?" not in out and "\\u" not in out, out


def test_a_failed_check_under_pytest_on_a_cp1252_console(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", "utf-8")
    (tmp_path / "test_cp.py").write_text(
        "import numpy as np\n"
        "from vistest.core import pngio\n"
        "from vistest import expect_screenshot\n"
        "def test_before():\n"
        "    img = np.full((60, 80, 3), 255, np.uint8)\n"
        "    expect_screenshot(pngio.encode(img), 'x.png')\n"
        "def test_after():\n"
        "    img = np.full((60, 80, 3), 255, np.uint8)\n"
        "    img[20:40, 20:60] = (37, 99, 235)\n"
        "    expect_screenshot(pngio.encode(img), 'x.png')\n", "utf-8")
    run = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p",
           "no:xdist", "-W", "ignore"]
    first = subprocess.run([*run, "-k", "before", "--vistest-update"], cwd=tmp_path,
                           capture_output=True, timeout=300, env=_env())
    assert first.returncode == 0, first.stdout.decode("utf-8", "replace")
    done = subprocess.run([*run, "-k", "after"], cwd=tmp_path, capture_output=True,
                          timeout=300, env=_env(PYTHONIOENCODING="cp1252"))
    out = done.stdout.decode("cp1252")
    assert done.returncode == 1, out
    said = [line for line in out.splitlines() if line.startswith("E ")]
    assert any("#ffffff -> #2563eb" in line for line in said), out
    assert any("dE00" in line for line in said), out
    assert "\\u2192" not in out and "\\u0394" not in out, out
