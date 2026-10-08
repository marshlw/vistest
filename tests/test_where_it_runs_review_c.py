# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""How to accept a picture, and a warning, said where the check runs (review
v1, 4.11 and 8a of step C).

Under pytest: `pytest --vistest-update`. From a script: the environment
variable, the only switch a script has. In `vistest check`: the same command
with `--update` — it used to advise pytest. And `vistest check` says a warning
as a command does: one line, with its own flag, not Python's warning with a
path into cli.py and a line of its source.
"""

from __future__ import annotations

import os
import subprocess
import sys

import numpy as np
import pytest

from vistest.core import pngio

CHECK = """
import numpy as np
from vistest.core import pngio
from vistest import expect_screenshot
img = np.full((40, 60, 3), 255, np.uint8)
"""


def _env(**extra) -> dict:
    clean = {k: v for k, v in os.environ.items() if not k.startswith("VISTEST_")}
    return {**clean, "PYTHONIOENCODING": "utf-8", **extra}


def test_under_pytest_it_says_pytest(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", "utf-8")
    (tmp_path / "test_x.py").write_text(
        CHECK + "\ndef test_x():\n    expect_screenshot(pngio.encode(img), 'x.png')\n",
        "utf-8")
    done = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                           "-p", "no:xdist", "-W", "ignore"], cwd=tmp_path,
                          capture_output=True, encoding="utf-8", timeout=300, env=_env())
    assert "create it:   pytest --vistest-update" in done.stdout, done.stdout


def test_from_a_script_it_says_the_environment_variable(tmp_path):
    script = tmp_path / "probe.py"
    script.write_text(CHECK + "try:\n    expect_screenshot(pngio.encode(img), 'x.png')\n"
                      "except AssertionError as e:\n    print(e)\n", "utf-8")
    done = subprocess.run([sys.executable, "-W", "ignore", str(script)], cwd=tmp_path,
                          capture_output=True, encoding="utf-8", timeout=300, env=_env())
    assert "create it:   set VISTEST_UPDATE_BASELINES=1 and run it again" in done.stdout, \
        done.stdout + done.stderr
    assert "--vistest-update" not in done.stdout


def _cli(args, cwd) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "vistest.cli", *args], cwd=cwd,
                          capture_output=True, encoding="utf-8", timeout=300, env=_env())


@pytest.fixture
def two(tmp_path):
    a = np.full((60, 80, 3), 255, np.uint8)
    b = a.copy()
    b[20:40, 20:60] = (37, 99, 235)
    (tmp_path / "a.png").write_bytes(pngio.encode(a))
    (tmp_path / "b.png").write_bytes(pngio.encode(b))
    return tmp_path


def test_vistest_check_says_the_same_command_with_update(two):
    done = _cli(["check", "card.png", "b.png"], two)
    assert done.returncode == 2, done.stdout + done.stderr
    assert "create it:   vistest check card.png b.png --update" in done.stdout, done.stdout
    assert "--vistest-update" not in done.stdout
    assert _cli(["check", "card.png", "a.png", "--update"], two).returncode == 0
    done = _cli(["check", "card.png", "b.png", "--baselines", "__vistest__"], two)
    assert done.returncode == 1, done.stdout + done.stderr
    assert ("accept it:   vistest check card.png b.png --baselines __vistest__ --update"
            in done.stdout), done.stdout


def test_vistest_check_says_its_warning_in_one_line_with_its_own_flag(two):
    done = _cli(["check", "card.png", "b.png"], two)
    assert done.stderr.splitlines() == [
        "vistest check: warning: byte targets have no platform: baselines go to the root; "
        "pass --platform if they vary by machine"], done.stderr
    assert len(done.stderr.splitlines()[0]) <= 120
    assert "cli.py" not in done.stderr and "vistest_platform" not in done.stderr


def test_the_report_says_what_wrote_a_baseline_where_it_ran(two):
    import json

    assert _cli(["check", "card.png", "a.png", "--update"], two).returncode == 0
    (row,) = [json.loads(p.read_text("utf-8"))
              for p in (two / ".vistest" / "report" / "parts").glob("*.json")]
    assert row["reason"] == "baseline created by vistest check --update", row["reason"]
