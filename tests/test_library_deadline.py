# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Every question to a live page has a deadline (library/js.py, review 5.1).

`page.evaluate` has no timeout: on a page whose main thread is stuck, or whose
renderer crashed, `expect_screenshot` used to wait for ever. The browser half
runs each case in a child process with a timeout of its own, so that if the
hang ever comes back this file fails instead of hanging the suite with it.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import time

import pytest

from vistest.library import js
from vistest.library.errors import CaptureError


# --------------------------------------------------------------------------- #
#  The budget and the sorting of errors, without a browser
# --------------------------------------------------------------------------- #
class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_the_budget_is_the_limits_of_the_check_plus_slack():
    assert js.budget_ms(5000, 5000) == 2 * 5000 + 3 * 5000 + js.SLACK_MS
    assert js.budget_ms(0, 0) == js.SLACK_MS


def test_a_question_waits_the_cap_but_never_past_the_deadline():
    clock = _Clock()
    b = js.Budget(8000, cap_ms=5000, clock=clock)
    assert b.timeout_for("x") == 5000
    clock.now = 6.0
    assert b.timeout_for("x") == 2000
    clock.now = 8.5
    with pytest.raises(CaptureError, match="ran out of its time"):
        b.timeout_for("waiting for web fonts")
    assert "waiting for web fonts" in b.dead


def _playwright_error(name: str, text: str) -> Exception:
    cls = type(name, (Exception,), {"__module__": "playwright._impl._errors"})
    return cls(text)


class _Page:
    """Playwright-shaped enough for `diagnose`: it answers, or it does not."""

    __module__ = "playwright.sync_api._generated"

    def __init__(self, alive: bool, crash: bool = False):
        self.alive = alive
        self.crash = crash

    def wait_for_function(self, *a, **k):
        if not self.alive:
            raise _playwright_error("TimeoutError", "Timeout 1000ms exceeded.")

    def screenshot(self, **k):
        raise _playwright_error("Error", "Page.screenshot: Target crashed" if self.crash
                                else "Timeout 500ms exceeded.")


def test_a_closed_page_is_said_as_closed():
    with js.budget(30000) as b:
        with pytest.raises(CaptureError, match="the page is closed"):
            js.diagnose(_Page(alive=True), _playwright_error(
                "TargetClosedError", "Target page, context or browser has been closed"),
                "waiting for web fonts", 5000)
        assert b.dead
        #  Every later question of the same check is refused at once.
        with pytest.raises(CaptureError, match="the page is closed"):
            js.check()


def test_a_timeout_on_a_page_that_answers_is_a_slow_answer_not_a_dead_page():
    with js.budget(30000) as b:
        with pytest.raises(js.SlowAnswer, match="waiting for web fonts: no answer in 5000 ms"):
            js.diagnose(_Page(alive=True), _playwright_error("TimeoutError", "Timeout"),
                        "waiting for web fonts", 5000)
        assert not b.dead


def test_a_timeout_on_a_page_that_does_not_answer_is_a_busy_page():
    with pytest.raises(CaptureError, match="stopped answering.*main thread is busy"):
        js.diagnose(_Page(alive=False), _playwright_error("TimeoutError", "Timeout"),
                    "waiting for web fonts", 5000)


def test_a_timeout_on_a_crashed_page_is_a_crash():
    with pytest.raises(CaptureError, match="the page crashed"):
        js.diagnose(_Page(alive=False, crash=True), _playwright_error("TimeoutError", "T"),
                    "asking whether the page is ready", 5000)


def test_an_error_that_is_not_playwrights_is_left_alone():
    js.diagnose(_Page(alive=False), RuntimeError("Target crashed"), "x", 1)


def test_a_page_wrapper_without_a_timeout_is_asked_as_before():
    class Wrapper:
        def __init__(self):
            self.asked = []

        def evaluate(self, script, *args):
            self.asked.append((script, args))
            return 7

    w = Wrapper()
    assert js.call(w, "() => 7", what="x") == 7
    assert js.call(w, "(a) => a", {"k": 1}, what="x") == 7
    assert w.asked == [("() => 7", ()), ("(a) => a", ({"k": 1},))]


def test_a_playwright_page_is_asked_through_wait_for_function_with_a_timeout():
    class Handle:
        def json_value(self):
            return {"v": 3}

        def dispose(self):
            pass

    class Page:
        __module__ = "playwright.sync_api._generated"

        def __init__(self):
            self.kw = None

        def wait_for_function(self, expression, **kw):
            self.expression, self.kw = expression, kw
            return Handle()

        def evaluate(self, *a, **k):  # pragma: no cover - must not be used
            raise AssertionError("page.evaluate has no timeout")

    page = Page()
    with js.budget(30000, cap_ms=4000):
        assert js.call(page, "() => 3", what="x") == 3
    assert page.kw["timeout"] == 4000
    assert "(() => 3)(a)" in page.expression


# --------------------------------------------------------------------------- #
#  The same, in a real Chromium, each case in a child process
# --------------------------------------------------------------------------- #
CASE = textwrap.dedent('''
    import sys, time, warnings
    warnings.simplefilter("ignore")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("SKIP no playwright"); sys.exit(0)
    from vistest import expect_screenshot
    from vistest.library.errors import CaptureError
    case = sys.argv[1]
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as e:
            print("SKIP", str(e).splitlines()[0]); sys.exit(0)
        page = browser.new_page()
        page.set_content("<h1>hi</h1>")
        if case == "busy":
            page.evaluate("setTimeout(() => { while (true) {} }, 100)")
            page.wait_for_timeout(400)
        elif case == "crashed":
            try:
                page.goto("chrome://crash", timeout=3000)
            except Exception:
                pass
        elif case == "closed":
            page.close()
        started = time.monotonic()
        try:
            expect_screenshot(page, "dead.png")
            print("NO ERROR")
        except CaptureError as e:
            print(f"CaptureError after {time.monotonic() - started:.1f}s: {e}")
        browser.close()
''')


@pytest.mark.parametrize("case, words", [
    ("busy", "stopped answering"),
    ("crashed", "crashed"),
    ("closed", "the page is closed"),
])
def test_a_dead_page_fails_the_check_in_seconds_with_the_reason(tmp_path, case, words):
    script = tmp_path / "case.py"
    script.write_text(CASE, encoding="utf-8")
    started = time.monotonic()
    try:
        done = subprocess.run([sys.executable, str(script), case], cwd=tmp_path,
                              capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        pytest.fail(f"expect_screenshot on a {case} page did not return in 120 s")
    out = done.stdout + done.stderr
    if out.startswith("SKIP"):
        pytest.skip(out.strip())
    assert done.returncode == 0, out
    assert "CaptureError" in out and words in out, out
    assert "could not check 'dead.png'" in out, out
    assert time.monotonic() - started < 60, out


def test_a_warning_still_points_at_the_line_that_called_the_check(tmp_path, monkeypatch):
    """The check runs one frame deeper inside its deadline; its warnings must not."""
    import warnings

    import numpy as np

    from vistest import expect_screenshot
    from vistest.core import pngio
    from vistest.library import context as _context

    monkeypatch.chdir(tmp_path)
    _context.reset_warning()
    _context.install(_context.LibraryContext(root=tmp_path, update=True))
    try:
        with warnings.catch_warnings(record=True) as seen:
            warnings.simplefilter("always")
            expect_screenshot(pngio.encode(np.zeros((8, 8, 3), np.uint8)), "w.png")
    finally:
        _context.uninstall()
    ours = [w for w in seen if "no platform" in str(w.message)]
    assert ours and ours[0].filename == __file__
