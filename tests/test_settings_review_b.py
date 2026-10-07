# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Where a library setting comes from, and what vistest.yaml says that it does
not read (review v1: 2.1, 2.3, 2.5).

One order for every setting: the call (or the command line), then the
environment, then the file, then the default. Nothing erases a layer any more
— the presets did (2.1). And a key of vistest.yaml the library does not read
is said once per run, by name, instead of changing nothing in silence (2.3).
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import warnings
from pathlib import Path

import numpy as np
import pytest

from vistest import expect_screenshot
from vistest.config import VisTestConfig
from vistest.core import pngio
from vistest.library import context as _context
from vistest.library.errors import ScreenshotMismatch, VisTestWarning

ROOT = Path(__file__).resolve().parent.parent

YAML = """\
capture:
  full_page: true
  keep_pointer: true
  mask_selectors: ["#promo"]
diff:
  fail_severity: 25
  delta_e_threshold: 1.5
matrix:
  browsers: [chromium]
render:
  heatmap: false
plugins:
  fail_on: confirmed
ai:
  attribution_enabled: true
paths:
  root: .vistest
service:
  project: shop
update_baselines: false
"""
UNREAD = ["capture.full_page", "capture.mask_selectors", "diff.delta_e_threshold",
          "matrix.browsers", "render.heatmap", "service.project", "update_baselines"]


def test_the_keys_the_library_does_not_read_are_named_in_file_order(tmp_path):
    path = tmp_path / "vistest.yaml"
    path.write_text(YAML, "utf-8")
    cfg = VisTestConfig.load(path)
    assert _context.unread_keys(cfg) == UNREAD
    text = _context.unread_text(cfg)
    assert text == (f"vistest: {path}: the library does not read {', '.join(UNREAD)} — "
                    "they change nothing in this run (they are the server's settings)")
    assert "\n" not in text


def test_a_file_the_library_reads_whole_says_nothing(tmp_path):
    path = tmp_path / "vistest.yaml"
    path.write_text("capture:\n  keep_pointer: true\ndiff:\n  fail_severity: 30\n"
                    "plugins:\n  fail_on: any\n", "utf-8")
    assert _context.unread_text(VisTestConfig.load(path)) == ""


def _attributes_of(paths, owner: str) -> set[str]:
    """`x.<owner>.name`, `<owner>.name` and `getattr(<owner>, "name")` read in
    the code — not in its comments, where the keys are named too."""
    found: set[str] = set()
    for path in paths:
        for node in ast.walk(ast.parse(path.read_text("utf-8"))):
            if isinstance(node, ast.Attribute):
                inner = node.value
                if (isinstance(inner, ast.Attribute) and inner.attr == owner) or \
                        (isinstance(inner, ast.Name) and inner.id == owner):
                    found.add(node.attr)
            elif isinstance(node, ast.Call) and getattr(node.func, "id", "") == "getattr" \
                    and len(node.args) >= 2 and isinstance(node.args[0], ast.Name) \
                    and node.args[0].id == owner and isinstance(node.args[1], ast.Constant):
                found.add(node.args[1].value)
    return found - {"get"}          # a passport's `capture` record is a dict


def test_what_the_library_reads_is_what_its_code_reads():
    """LIBRARY_READS is a promise in a warning: it must match the code."""
    library = [*sorted((ROOT / "vistest" / "library").glob("*.py")),
               ROOT / "vistest" / "pytest_plugin.py"]
    capture = _attributes_of(library, "capture")
    #  pointer_plan reads its half of `capture:` as `cfg`.
    capture |= _attributes_of([ROOT / "vistest" / "library" / "targets.py"], "cfg")
    v2 = _attributes_of(sorted((ROOT / "vistest" / "core" / "v2").glob("*.py")), "cfg")
    v2 = {n for n in v2 if not n.startswith("v2_")} - {"threshold_source", "area_source"}
    v2 |= {"fail_severity", "max_changed_area_pct"}       # through cfg.v2_threshold/area
    reads = {k for k in _context.LIBRARY_READS if "." in k}
    assert {f"capture.{n}" for n in capture} == {k for k in reads if k.startswith("capture.")}
    assert {f"diff.{n}" for n in v2} == {k for k in reads if k.startswith("diff.")}


def _project(tmp_path: Path, yaml: str) -> Path:
    (tmp_path / "vistest.yaml").write_text(yaml, "utf-8")
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", "utf-8")
    for i in range(3):
        (tmp_path / f"test_{i}.py").write_text("def test_it():\n    pass\n", "utf-8")
    return tmp_path


def _pytest(project: Path, *args: str, env: dict | None = None) -> str:
    clean = {k: v for k, v in os.environ.items() if not k.startswith("VISTEST_")}
    done = subprocess.run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider",
                           "-W", "always::vistest.library.errors.VisTestWarning", *args],
                          cwd=project, capture_output=True, encoding="utf-8", timeout=300,
                          env={**clean, "PYTHONIOENCODING": "utf-8", **(env or {})})
    out = done.stdout + done.stderr
    assert done.returncode == 0, out
    return out


@pytest.mark.parametrize("workers", [[], ["-n", "2"]], ids=["one process", "xdist"])
def test_pytest_says_it_once_per_run(tmp_path, workers):
    out = _pytest(_project(tmp_path, YAML), *workers)
    said = [ln for ln in out.splitlines() if "the library does not read" in ln]
    assert len(said) == 1, out
    assert ", ".join(UNREAD) in said[0], said


def test_without_the_plugin_it_is_said_once_per_process(tmp_path, monkeypatch):
    (tmp_path / "vistest.yaml").write_text(YAML, "utf-8")
    monkeypatch.chdir(tmp_path)
    _context.uninstall()
    _context.reset_warning()
    try:
        with warnings.catch_warnings(record=True) as seen:
            warnings.simplefilter("always")
            _context.current()
            _context.uninstall()
            _context.current()
        said = [str(w.message) for w in seen if issubclass(w.category, VisTestWarning)
                and "does not read" in str(w.message)]
        assert len(said) == 1 and ", ".join(UNREAD) in said[0]
    finally:
        _context.uninstall()
        _context.reset_warning()


# --- 2.1, 2.5: one order — call, environment, vistest.yaml, default ------- #
def _frame(box: bool = False) -> bytes:
    picture = np.full((80, 120, 3), 40, np.uint8)
    if box:
        picture[10:40, 10:70] = 235
    return pngio.encode(picture)


@pytest.mark.parametrize("yaml, env, call, said", [
    ("", None, None, "no threshold is set"),
    ("diff:\n  fail_severity: 25\n", None, None, "threshold 25 (vistest.yaml)"),
    ("diff:\n  fail_severity: 25\n", "30", None, "threshold 30 (VISTEST_FAIL_SEVERITY)"),
    ("diff:\n  fail_severity: 25\n", "30", 60, "threshold 60 (call)"),
])
def test_the_threshold_comes_from_the_call_the_environment_the_file_the_default(
        tmp_path, monkeypatch, yaml, env, call, said):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("VISTEST_THRESHOLD_SOURCE", raising=False)
    if env is None:
        monkeypatch.delenv("VISTEST_FAIL_SEVERITY", raising=False)
    else:
        monkeypatch.setenv("VISTEST_FAIL_SEVERITY", env)
    if yaml:
        (tmp_path / "vistest.yaml").write_text(yaml + "capture:\n  keep_pointer: true\n",
                                               "utf-8")
    ctx = _context.install(_context.LibraryContext(root=tmp_path, platform_override="p"))
    try:
        ctx.update = True
        expect_screenshot(_frame(), "page.png")
        ctx.update = False
        with pytest.raises(ScreenshotMismatch) as e:
            expect_screenshot(_frame(box=True), "page.png",
                              **({"fail_severity": call} if call else {}))
        #  Nothing erases the file: its other keys are still there (2.1).
        assert ctx.config.capture.keep_pointer is bool(yaml)
    finally:
        _context.uninstall()
    assert said in str(e.value)


def test_under_the_plugin_the_environment_sits_between_the_flag_and_the_ini(tmp_path):
    """--vistest-platform, VISTEST_PLATFORM, vistest_platform in pyproject.toml."""
    (tmp_path / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\nvistest_platform = \"from-ini\"\n", "utf-8")
    (tmp_path / "test_x.py").write_text(
        "import numpy as np\nfrom vistest import expect_screenshot\nfrom vistest.core "
        "import pngio\n\ndef test_x():\n    expect_screenshot(pngio.encode(np.zeros((8, 8, 3), "
        "np.uint8)), 'x.png')\n", "utf-8")
    base = tmp_path / "__vistest__"

    def written() -> list[str]:
        return sorted(p.parent.name for p in base.rglob("x.png"))

    _pytest(tmp_path, "--vistest-update", "-p", "no:xdist")
    assert written() == ["from-ini"]
    _pytest(tmp_path, "--vistest-update", "-p", "no:xdist",
            env={"VISTEST_PLATFORM": "from-env"})
    assert written() == ["from-env", "from-ini"]
    _pytest(tmp_path, "--vistest-update", "-p", "no:xdist", "--vistest-platform=from-flag",
            env={"VISTEST_PLATFORM": "from-env"})
    assert written() == ["from-env", "from-flag", "from-ini"]
