# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""A failed check shows its pictures in Allure (plan, f4).

With `allure-pytest` installed and `--alluredir` given, the library attaches
to the failed test what a person needs to judge it without opening anything
else: the baseline, the screenshot it was compared with, what changed, and
`result.json` with the regions. A missing baseline attaches the screenshot
alone; a check that passes attaches nothing.

The run is real: pytest in a subprocess, the real allure-pytest writing its
result files, and those files read back. Byte targets, so no browser is
needed. Named `test_library_*` so that the Windows job runs it too.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_library_mode import run

pytest.importorskip("allure_pytest")

TESTS = '''
import os

import numpy as np

from vistest import expect_screenshot
from vistest.core import pngio


def _screenshot():
    frame = np.full((80, 120, 3), 40, np.uint8)
    if os.environ.get("DEMO_STATE") == "after":
        frame[10:50, 10:90] = 230
    return pngio.encode(frame)


def test_visual():
    expect_screenshot(_screenshot(), "home.png")


def test_plain():
    assert True
'''

PNG = b"\x89PNG\r\n\x1a\n"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_visual.py").write_text(TESTS, "utf-8")
    return tmp_path


def _results(directory: Path) -> dict[str, dict]:
    out = {}
    for f in directory.glob("*-result.json"):
        r = json.loads(f.read_text("utf-8"))
        out[r["name"]] = r
    return out


def _attached(directory: Path, result: dict) -> dict[str, bytes]:
    """Attachment name → the file's bytes, as Allure will show them."""
    return {a["name"]: (directory / a["source"]).read_bytes()
            for a in result.get("attachments", [])}


def test_a_failure_attaches_baseline_actual_diff_and_the_result(project: Path):
    assert run(project, "--vistest-update")[0] == 0
    code, output = run(project, "--alluredir=ar", state="after")
    assert code == 1, output

    results = _results(project / "ar")
    visual, plain = results["test_visual"], results["test_plain"]
    assert visual["status"] == "failed"
    files = _attached(project / "ar", visual)
    assert list(files) == ["Baseline", "Actual screenshot", "What changed", "result.json"]
    for name in ("Baseline", "Actual screenshot", "What changed"):
        assert files[name].startswith(PNG), name
    assert files["Baseline"] != files["Actual screenshot"]
    regions = json.loads(files["result.json"])["regions"]
    assert regions, "result.json carries the regions the failure is about"

    assert plain["status"] == "passed" and not plain.get("attachments")


def test_a_missing_baseline_attaches_the_screenshot_alone(project: Path):
    code, output = run(project, "--alluredir=ar")
    assert code == 1, output
    files = _attached(project / "ar", _results(project / "ar")["test_visual"])
    assert list(files) == ["Actual screenshot"]
    assert files["Actual screenshot"].startswith(PNG)


def test_a_check_that_passes_attaches_nothing(project: Path):
    assert run(project, "--vistest-update")[0] == 0
    code, output = run(project, "--alluredir=ar")
    assert code == 0, output
    for result in _results(project / "ar").values():
        assert result["status"] == "passed"
        assert not result.get("attachments"), result["name"]
