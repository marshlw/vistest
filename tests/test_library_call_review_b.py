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
from pathlib import Path

import numpy as np
import pytest

from vistest import expect_screenshot
from vistest.core import pngio
from vistest.core.thresholds import ThresholdError
from vistest.library import context as _context
from vistest.library.errors import ScreenshotMismatch


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
