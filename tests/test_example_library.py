# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""`examples/test_library.py` does what its docstring promises (review 6.7).

The install check (`scripts/check_install.py`) used to run the example of the
service's fixture, so a green install matrix said nothing about the library.
This is the same four outcomes the install check now asserts, run against
the package of this checkout: red without baselines, `--vistest-update`
writes them to `tests/__vistest__/`, green, red after the page changes.
"""

from __future__ import annotations

import ast
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "examples" / "test_library.py"
DEMO = ROOT / "examples" / "demo_page.html"


def test_the_example_is_the_library_and_the_install_check_runs_it():
    calls = {n.func.id for n in ast.walk(ast.parse(EXAMPLE.read_text("utf-8")))
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "expect_screenshot" in calls
    script = (ROOT / "scripts" / "check_install.py").read_text("utf-8")
    assert 'EXAMPLE = "test_library.py"' in script


def _hashes(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def test_red_then_update_then_green_then_red_after_a_change(tmp_path):
    pytest.importorskip("pytest_playwright")
    tests = tmp_path / "tests"
    tests.mkdir()
    shutil.copy2(EXAMPLE, tests / EXAMPLE.name)
    shutil.copy2(DEMO, tests / DEMO.name)
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", "utf-8")

    def run(*extra: str) -> tuple[int, str]:
        done = subprocess.run(
            [sys.executable, "-m", "pytest", f"tests/{EXAMPLE.name}", "-q",
             "-p", "no:cacheprovider", "-p", "no:xdist", *extra],
            cwd=tmp_path, capture_output=True, text=True, timeout=600)
        return done.returncode, done.stdout + done.stderr

    code, out = run()
    if "Executable doesn't exist" in out:
        pytest.skip("no Chromium for Playwright on this machine")
    assert code != 0 and out.count("no baseline for") >= 3, out
    base = tests / "__vistest__"
    assert not base.exists() or not list(base.rglob("*.png")), out

    code, out = run("--vistest-update")
    assert code == 0, out
    written = [p for p in base.rglob("*.png") if ".renderers" not in p.parts]
    assert sorted(p.name for p in written) == ["landing-dark.png", "landing.png",
                                               "pricing.png"], out
    before = _hashes(base)

    code, out = run()
    assert code == 0 and " failed" not in out, out
    assert _hashes(base) == before

    page = tests / DEMO.name
    page.write_text(page.read_text("utf-8").replace(".price{font-size:34px;",
                                                    ".price{font-size:30px;"), "utf-8")
    code, out = run()
    assert code != 0 and "differs from the baseline" in out, out
    assert _hashes(base) == before
