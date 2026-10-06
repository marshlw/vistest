# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""What a failed check prints, and what an accept refuses (review v1: 4.1, 4.2, 4.10, 5.3)."""

from __future__ import annotations

import subprocess
import sys
import textwrap
import warnings
from pathlib import Path

import numpy as np
import pytest

from vistest import expect_screenshot
from vistest.core import pngio
from vistest.core.comparator import ImageTooLarge
from vistest.library import context as _context
from vistest.library.errors import VisTestWarning

PROJECT_TEST = textwrap.dedent('''
    import numpy as np
    from vistest import expect_screenshot
    from vistest.core import pngio

    def picture(fill):
        return pngio.encode(np.full((40, 60, 3), fill, np.uint8))

    def test_missing():
        expect_screenshot(picture(200), "missing.png")

    def test_differs():
        expect_screenshot(picture(30), "differs.png")

    class Broken:
        def screenshot(self, **kw):
            raise RuntimeError("a bug in somebody's page wrapper")

    def test_a_bug_keeps_its_frames():
        expect_screenshot(Broken(), "broken.png")
''')


@pytest.fixture(scope="module")
def failed_run(tmp_path_factory) -> str:
    """A project's pytest run: one check without a baseline, one that differs."""
    root = tmp_path_factory.mktemp("project")
    (root / "test_checks.py").write_text(PROJECT_TEST, encoding="utf-8")
    (root / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    base = root / "__vistest__" / "flat"
    base.mkdir(parents=True)
    (base / "differs.png").write_bytes(pngio.encode(np.full((40, 60, 3), 200, np.uint8)))
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-p", "no:xdist",
         "-o", "vistest_platform=flat", "test_checks.py"],
        cwd=root, capture_output=True, text=True, timeout=300,
        env={**__import__("os").environ, "COLUMNS": "200"})
    return done.stdout + done.stderr


def _section(out: str, test: str) -> str:
    head = f"_ {test} _"
    start = out.index(head)
    rest = out[start + len(head):]
    ends = [i for i in (rest.find("\n___"), rest.find("\n====")) if i >= 0]
    return rest[:min(ends)] if ends else rest


def test_a_missing_baseline_shows_the_test_line_and_the_message_only(failed_run):
    part = _section(failed_run, "test_missing")
    assert "def expect_screenshot" not in part and "library/__init__.py" not in part, part
    assert 'expect_screenshot(picture(200), "missing.png")' in part
    assert len(part.strip().splitlines()) < 20, part


def test_a_difference_shows_the_test_line_and_the_message_only(failed_run):
    part = _section(failed_run, "test_differs")
    assert "def expect_screenshot" not in part and "library/__init__.py" not in part, part
    assert "differs from the baseline" in part


@pytest.mark.parametrize("test, line", [("test_missing", "  expected:"),
                                         ("test_differs", "  baseline:")])
def test_the_message_is_printed_once(failed_run, test, line):
    part = _section(failed_run, test)
    assert part.count(line) == 1, part
    assert "Visual diff" not in part


def test_a_bug_still_shows_where_it_happened(failed_run):
    part = _section(failed_run, "test_a_bug_keeps_its_frames")
    assert "library/targets.py" in part, part
    assert "a bug in somebody's page wrapper" in part


# --- 4.10 and 5.3: what an accept says, and what it refuses -------------- #
@pytest.fixture
def ctx(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _context.reset_warning()
    context = _context.LibraryContext(root=tmp_path, platform_override="test", update=True)
    _context.install(context)
    yield context
    _context.uninstall()


class LoadingPage:
    """A page whose readiness probe says, for ever, that a loader is up."""

    viewport_size = {"width": 60, "height": 40}

    def goto(self, *_):  # pragma: no cover - only makes it page-like
        ...

    def evaluate(self, script, *args):
        if "readyState" in script:
            return {"readyState": "complete", "fonts": "loaded", "loaders": ["div.spinner"],
                    "images": [], "quiet": 1000, "said": True}
        return 1.0 if "devicePixelRatio" in script else True

    def screenshot(self, **kw):
        return pngio.encode(np.full((40, 60, 3), 240, np.uint8))


def test_accepting_a_page_that_was_not_ready_is_said(ctx):
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        expect_screenshot(LoadingPage(), "loading.png", ready_timeout_ms=150,
                          stable_timeout_ms=0)
    said = [str(w.message) for w in seen if issubclass(w.category, VisTestWarning)]
    assert any("not ready" in t and "accepted as the baseline anyway" in t
               and "div.spinner" in t for t in said), said


def test_a_baseline_over_the_engine_limit_is_not_written(tmp_path, monkeypatch):
    monkeypatch.setenv("VISTEST_ENGINE_MAX_PIXELS", "1000")
    monkeypatch.chdir(tmp_path)
    context = _context.LibraryContext(root=tmp_path, platform_override="test", update=True)
    _context.install(context)
    try:
        with pytest.raises(ImageTooLarge, match=r"60×40.*could never be compared.*not written"):
            expect_screenshot(pngio.encode(np.zeros((40, 60, 3), np.uint8)), "big.png")
    finally:
        _context.uninstall()
    assert not list(Path(context.baselines).rglob("big.png"))
