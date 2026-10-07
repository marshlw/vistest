# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""`examples/test_library.py` does what its docstring promises (review 6.7, A2).

The install check (`scripts/check_install.py`) used to run the example of the
service's fixture, so a green install matrix said nothing about the library.
Its step 6 — red without baselines, `--vistest-update` writes them to
`tests/__vistest__/`, green, red after the page changes — is
`check_install.example_outcomes`, and it is run here against the package of
this checkout.

It used to count the outcomes in pytest's terminal output, and the count
depended on the width of the terminal: on GitHub's runners the short summary
is printed in full, every message appeared twice, and all fifteen install
jobs went red. The outcomes are read from a JUnit report now; the browser
test below runs at 80 and at 250 columns.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
EXAMPLE = EXAMPLES / "test_library.py"


def _check_install():
    spec = importlib.util.spec_from_file_location(
        "check_install_under_test", ROOT / "scripts" / "check_install.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_example_is_the_library_and_the_install_check_runs_it():
    calls = {n.func.id for n in ast.walk(ast.parse(EXAMPLE.read_text("utf-8")))
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "expect_screenshot" in calls
    assert _check_install().EXAMPLE == "test_library.py"


JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" errors="1" failures="2" skipped="1" tests="5">
<testcase classname="tests.test_library" name="test_landing_page[chromium]">
<failure message="vistest.library.errors.BaselineMissing: vistest: no baseline for
'landing.png' (linux-chromium-1x-1280x800)">tests/test_library.py:51: BaselineMissing</failure>
</testcase>
<testcase classname="tests.test_library" name="test_pricing_cards[chromium]">
<failure message="vistest.library.errors.ScreenshotMismatch: vistest: 'pricing.png'
differs">...</failure></testcase>
<testcase classname="tests.test_library" name="test_dark_theme[chromium]"/>
<testcase classname="tests.test_library" name="test_setup">
<error message="failed on setup with &quot;fixture 'page' not found&quot;">...</error>
</testcase>
<testcase classname="tests.test_library" name="test_skipped">
<skipped type="pytest.skip" message="no playwright">...</skipped></testcase>
</testsuite></testsuites>
"""


def test_the_outcomes_are_read_from_the_junit_report_one_per_test(tmp_path):
    report = tmp_path / "junit.xml"
    report.write_text(JUNIT, encoding="utf-8")
    assert _check_install().junit_cases(report) == [
        ("test_landing_page[chromium]", "failed", "vistest.library.errors.BaselineMissing"),
        ("test_pricing_cards[chromium]", "failed",
         "vistest.library.errors.ScreenshotMismatch"),
        ("test_dark_theme[chromium]", "passed", ""),
        ("test_setup", "error", 'failed on setup with "fixture \'page\' not found"'),
        ("test_skipped", "skipped", ""),
    ]


@pytest.mark.parametrize("columns", ["80", "250"])
def test_red_then_update_then_green_then_red_after_a_change(tmp_path, capsys, columns):
    """The install check's step 6, on this checkout, at either terminal width."""
    pytest.importorskip("pytest_playwright")
    check = _check_install()
    env = {**os.environ, "COLUMNS": columns}
    try:
        check.example_outcomes(sys.executable, EXAMPLES, tmp_path / "project", env)
    except check.Failed as e:
        if "Executable doesn't exist" in str(e):
            pytest.skip("no Chromium for Playwright on this machine")
        raise
    said = capsys.readouterr().out
    assert "1. no baselines    : 3 failed (BaselineMissing)" in said, said
    assert "2. --vistest-update: 3 passed" in said, said
    assert "3. compared        : 3 passed — baselines untouched" in said, said
    assert "4. page changed    : 3 failed (ScreenshotMismatch) — baselines untouched" \
        in said, said
