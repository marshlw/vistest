# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""The arguments of `expect_screenshot` after review B (review v1: R3, R5, R8, 1.1).

One word for one setting, in the call and in vistest.yaml: `fail_severity`
and `max_changed_area_pct` instead of a `threshold` that was a number or a
mapping; `keep_pointer`, `blur_focus` and one `timeout_ms` for the whole check
instead of five capture knobs; no engine to choose.
"""

from __future__ import annotations

import inspect
import json
import time
from pathlib import Path

import numpy as np
import pytest

from vistest import expect_screenshot
from vistest.config import ConfigError, VisTestConfig
from vistest.core import pngio
from vistest.core.settings import CaptureConfig
from vistest.core.thresholds import ThresholdError
from vistest.library import context as _context
from vistest.library import targets as _targets
from vistest.library.errors import CaptureError, ScreenshotMismatch


def frame(fill: int = 40, box: tuple[int, int, int, int] | None = None) -> bytes:
    picture = np.full((80, 120, 3), fill, np.uint8)
    if box:
        x, y, w, h = box
        picture[y:y + h, x:x + w] = 235
    return pngio.encode(picture)


@pytest.fixture
def ctx(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    context = _context.LibraryContext(root=tmp_path, platform_override="p")
    _context.install(context)
    yield context
    _context.uninstall()


def accept(ctx, png: bytes, name: str = "page.png") -> None:
    ctx.update = True
    try:
        expect_screenshot(png, name)
    finally:
        ctx.update = False


# --- R3: fail_severity and max_changed_area_pct, numbers ------------------ #
def test_fail_severity_is_a_number_of_the_call_and_is_named_so(ctx):
    accept(ctx, frame())
    with pytest.raises(ScreenshotMismatch) as raised:
        expect_screenshot(frame(box=(10, 10, 60, 30)), "page.png", fail_severity=60)
    assert "threshold 60 (call)" in str(raised.value)


def test_both_numbers_of_the_call_reach_the_comparison(ctx):
    accept(ctx, frame())
    with pytest.raises(ScreenshotMismatch) as both:
        expect_screenshot(frame(box=(10, 10, 60, 30)), "page.png",
                          fail_severity=60, max_changed_area_pct=30)
    assert "threshold 60 (call), area limit 30.00%" in str(both.value)


def test_a_high_enough_fail_severity_passes_the_same_change(ctx):
    accept(ctx, frame())
    result = expect_screenshot(frame(box=(10, 10, 60, 30)), "page.png",
                               fail_severity=100, max_changed_area_pct=100)
    assert result.verdict.value == "pass"


@pytest.mark.parametrize("kw, words", [
    ({"fail_severity": 900}, r"fail_severity=.*900 is outside 0…100"),
    ({"fail_severity": -1}, r"fail_severity=.*outside"),
    ({"max_changed_area_pct": 101}, r"max_changed_area_pct=.*101% is outside 0%…100%"),
])
def test_a_number_outside_its_range_is_refused_at_the_call(ctx, kw, words):
    accept(ctx, frame())
    with pytest.raises(ThresholdError, match=words):
        expect_screenshot(frame(), "page.png", **kw)


@pytest.mark.parametrize("value", [True, "40", {"fail_severity": 40}, [40]])
def test_fail_severity_that_is_not_a_number_is_refused_by_name(ctx, value):
    with pytest.raises(TypeError, match=r"expect_screenshot: fail_severity is a number"):
        expect_screenshot(frame(), "page.png", fail_severity=value)


def test_threshold_is_gone_from_the_call():
    params = inspect.signature(expect_screenshot).parameters
    assert "threshold" not in params
    assert {"fail_severity", "max_changed_area_pct"} <= set(params)
    with pytest.raises(TypeError, match="unexpected keyword argument 'threshold'"):
        expect_screenshot(frame(), "page.png", threshold=60)


def test_the_same_two_names_are_read_from_vistest_yaml(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "vistest.yaml").write_text(
        "diff:\n  fail_severity: 60\n  max_changed_area_pct: 30\n", "utf-8")
    context = _context.LibraryContext(root=tmp_path, platform_override="p")
    _context.install(context)
    try:
        accept(context, frame())
        with pytest.raises(ScreenshotMismatch) as both:
            expect_screenshot(frame(box=(10, 10, 60, 30)), "page.png")
    finally:
        _context.uninstall()
    assert "threshold 60 (vistest.yaml), area limit 30.00% (vistest.yaml)" \
        in str(both.value)


# --- R8, 1.1: keep_pointer, blur_focus and one timeout_ms ------------------ #
class StuckPage:
    """A page wrapper whose loader never goes away and whose frames never settle."""

    viewport_size = {"width": 60, "height": 40}

    def __init__(self, settles: bool = False):
        self.frames = 0
        self.settles = settles

    def goto(self, *_):  # pragma: no cover - only makes it page-like
        ...

    def evaluate(self, script, *args):
        if "readyState" in script:
            return {"readyState": "complete", "fonts": "loaded",
                    "loaders": [] if self.settles else ["div.spinner"],
                    "images": [], "quiet": 1000, "said": not self.settles}
        return 1.0 if "devicePixelRatio" in script else True

    def screenshot(self, **kw):
        self.frames += 1
        fill = 40 if self.settles else self.frames % 250
        return pngio.encode(np.full((40, 60, 3), fill, np.uint8))


def rows(ctx) -> list[dict]:
    return [json.loads(p.read_text("utf-8"))
            for p in sorted(ctx.parts_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)]


def test_the_call_takes_two_capture_switches_and_one_time_limit():
    params = set(inspect.signature(expect_screenshot).parameters)
    assert {"keep_pointer", "blur_focus", "timeout_ms"} <= params
    assert not params & {"reset_hover_focus", "restore_scroll", "ready_timeout_ms",
                         "stable_timeout_ms"}


def test_timeout_ms_bounds_the_whole_check_and_says_what_it_waited_for(ctx):
    started = time.monotonic()
    with pytest.raises(CaptureError) as e:
        expect_screenshot(StuckPage(), "stuck.png", timeout_ms=800)
    took = time.monotonic() - started
    text = str(e.value)
    assert took < 2.0, (took, text)
    assert text.startswith("vistest: could not check 'stuck.png': the check ran out "
                           "of its time (800 ms) before taking the screenshot"), text
    assert "still showing a loader" in text and "div.spinner" in text, text


def test_a_page_that_fits_in_timeout_ms_is_checked_as_usual(ctx):
    ctx.update = True
    expect_screenshot(StuckPage(settles=True), "fine.png", timeout_ms=2000)
    ctx.update = False
    assert expect_screenshot(StuckPage(settles=True), "fine.png",
                             timeout_ms=2000).verdict.value == "pass"
    #  The frames' limit is cut to the check's: min(5000, 2000).
    assert rows(ctx)[-1]["capture"]["timeout_ms"] == 2000


@pytest.mark.parametrize("value, error", [(0, ValueError), (-5, ValueError),
                                          ("1s", TypeError), (1.5, TypeError),
                                          (True, TypeError)])
def test_a_bad_timeout_ms_is_refused_by_name(ctx, value, error):
    with pytest.raises(error, match=r"expect_screenshot: timeout_ms"):
        expect_screenshot(StuckPage(settles=True), "page.png", timeout_ms=value)


def test_the_pointer_and_the_focus_are_the_two_switches_and_nothing_else():
    default = CaptureConfig()
    assert _targets.pointer_plan(default, None, None) == (True, False)
    assert _targets.pointer_plan(default, True, None) == (False, False)
    assert _targets.pointer_plan(default, None, True) == (True, True)
    assert _targets.pointer_plan(CaptureConfig(keep_pointer=True, blur_focus=True),
                                 None, None) == (False, True)
    assert _targets.pointer_plan(CaptureConfig(keep_pointer=True, blur_focus=True),
                                 False, False) == (True, False)
    #  The service's switch is not the library's: it changes nothing here.
    assert _targets.pointer_plan(CaptureConfig(reset_hover_focus=True), None, None) \
        == (True, False)


def test_restore_scroll_in_vistest_yaml_is_refused_with_its_new_name(tmp_path):
    path = tmp_path / "vistest.yaml"
    path.write_text("capture:\n  restore_scroll: false\n", "utf-8")
    with pytest.raises(ConfigError, match=r"vistest\.yaml: capture\.restore_scroll is "
                                          r"called capture\.match_baseline_scroll"):
        VisTestConfig.load(path)
    path.write_text("capture:\n  match_baseline_scroll: false\n", "utf-8")
    assert VisTestConfig.load(path).capture.match_baseline_scroll is False
