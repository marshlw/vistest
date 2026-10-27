# VisTest - visual regression testing for Python and Playwright.
# Copyright 2026 Kirill Kulagin
# SPDX-License-Identifier: Apache-2.0
#
# This file is part of VisTest, licensed under the Apache License, Version 2.0.
# You may not use it except in compliance with the License. See LICENSE for
# the terms and NOTICE for attribution and the trademark.

"""Three things a real application showed about 0.2.0a1 (step rl1).

* A page whose bundle did not load is its background and nothing else, and
  `--vistest-update` wrote it as the baseline without a word: a picture of one
  color is not written now, unless the call says it is meant
  (`allow_uniform_baseline=True`).
* A background that changed on the whole picture «overlapped» the button in
  focus, and the message advised `blur_focus=True`: the hint about the focus
  or the pointer is given only for a change about that element.
* A single-page application with an empty `<div id="root">` passed every step
  of the readiness wait: a page that shows nothing but its background is
  waited for, and when nothing comes, the message says so.

In a real Chromium, on pages set with `set_content` — no network.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from vistest import expect_screenshot
from vistest.core import pngio
from vistest.library import NEAR_AREA_RATIO, _failure_hints, uniform
from vistest.library import context as _context
from vistest.library import targets as _targets
from vistest.library.errors import BaselineMissing, ScreenshotMismatch, UniformBaseline

BLANK_LINE = _targets.BLANK_LINE


@pytest.fixture
def ctx(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    context = _context.LibraryContext(root=tmp_path)
    _context.install(context)
    yield context
    _context.uninstall()


@pytest.fixture(scope="module")
def chromium():
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        manager = sync_api.sync_playwright().start()
    except Exception as e:  # pragma: no cover - depends on the machine
        pytest.skip(f"Playwright could not start: {e}")
    try:
        browser = manager.chromium.launch()
    except Exception as e:  # pragma: no cover - depends on the machine
        manager.stop()
        pytest.skip(f"no Chromium to run against: {str(e).splitlines()[0]}")
    yield browser
    browser.close()
    manager.stop()


@pytest.fixture
def page(chromium):
    context = chromium.new_context(viewport={"width": 400, "height": 300})
    page = context.new_page()
    yield page
    context.close()


def capture_cfg(ctx, **kw) -> None:
    from dataclasses import replace

    ctx._config = replace(ctx.config, capture=replace(ctx.config.capture, **kw))


def accept(ctx, target, name, mode=True, **kw):
    ctx.update = mode
    try:
        return expect_screenshot(target, name, **kw)
    finally:
        ctx.update = False


def baselines(ctx, name: str) -> list[Path]:
    return list(Path(ctx.baselines).rglob(name))


def last_row(ctx) -> dict:
    rows = [json.loads(p.read_text("utf-8")) for p in ctx.parts_dir.glob("*.json")]
    return max(rows, key=lambda r: r.get("written_at", 0))


#: What the stand showed: the application's bundle did not load, and the page
#: is its background colour on every pixel.
EMPTY_ROOT = """<!doctype html><html><head><style>
  html, body { margin: 0; height: 100%; background: #f5f7fb; }
  #root { min-height: 100%; }
</style></head><body><div id="root"></div></body></html>"""

#: The same page, rendered: large fields of background, a little text.
SPARSE = """<!doctype html><html><head><style>
  html, body { margin: 0; height: 100%; background: #f5f7fb; font: 14px sans-serif; }
  #root { min-height: 100%; }
  h1 { margin: 120px 0 0 170px; font-size: 14px; font-weight: normal; color: #555; }
</style></head><body><div id="root"><h1>Sign in</h1></div></body></html>"""

#: The application arrives late: its script fills `#root` after `delay` ms.
LATE = """<!doctype html><html><head><style>
  html, body { margin: 0; height: 100%; background: #f5f7fb; font: 16px sans-serif; }
  #root { min-height: 100%; }
  form { margin: 40px; padding: 20px; background: #fff; border-radius: 8px; }
</style></head><body><div id="root"></div>
<script>setTimeout(() => {
  document.getElementById('root').innerHTML = '<form><p>Email</p><input></form>';
}, DELAY);</script></body></html>"""


# --------------------------------------------------------------------------- #
#  3.1 — a picture of one color is not written as the baseline
# --------------------------------------------------------------------------- #
def test_a_page_of_one_color_is_not_written_as_the_baseline(ctx, page):
    capture_cfg(ctx, ready_timeout_ms=300)
    page.set_content(EMPTY_ROOT)
    with pytest.raises(UniformBaseline) as e:
        accept(ctx, page, "login.png")
    text = str(e.value)
    assert "'login.png' was not written as the baseline" in text
    assert "one color, #f5f7fb, on 100% of it" in text
    assert "the page looks like it was not drawn" in text
    assert BLANK_LINE in text
    assert "expect_screenshot(..., allow_uniform_baseline=True)" in text
    assert isinstance(e.value, AssertionError)
    assert baselines(ctx, "login.png") == []
    #  The picture that was taken is kept, and the check leaves its row.
    assert Path(e.value.artifacts["actual"]).exists()
    assert last_row(ctx)["verdict"] == "error"


def test_a_picture_of_one_color_on_purpose_is_written_with_the_switch(ctx, page):
    capture_cfg(ctx, ready_timeout_ms=300)
    page.set_content(EMPTY_ROOT)
    accept(ctx, page, "blank.png", allow_uniform_baseline=True)
    assert len(baselines(ctx, "blank.png")) == 1
    #  A page that is empty by design is not failed for it: the check passes,
    #  and says what it saw.
    result = expect_screenshot(page, "blank.png")
    assert result.verdict.value == "pass"
    assert BLANK_LINE in result.notes


def test_an_ordinary_page_with_large_fields_of_background_is_written_as_before(ctx, page):
    page.set_content(SPARSE)
    assert accept(ctx, page, "sparse.png").verdict.value == "new_baseline"
    assert len(baselines(ctx, "sparse.png")) == 1
    assert BLANK_LINE not in last_row(ctx)["reason"]


def test_an_accept_over_a_baseline_does_not_write_a_blank_page_either(ctx, page):
    """`--vistest-update=all` (and `changed`, on the failure it makes) over a
    baseline that exists: the blank picture is refused, the baseline stays."""
    capture_cfg(ctx, ready_timeout_ms=300)
    page.set_content(SPARSE)
    accept(ctx, page, "login.png")
    path = baselines(ctx, "login.png")[0]
    before = path.read_bytes()
    page.set_content(EMPTY_ROOT)
    for mode in ("changed", "all"):
        with pytest.raises(UniformBaseline) as e:
            accept(ctx, page, "login.png", mode=mode)
        assert "; the baseline there is kept as it was" in str(e.value)
        assert path.read_bytes() == before


def test_a_picture_handed_in_is_the_callers_own_and_is_written(ctx):
    """Bytes, a file, an array: the caller made the picture; nothing to say about a page."""
    accept(ctx, np.full((40, 60, 3), 200, np.uint8), "swatch.png")
    assert len(baselines(ctx, "swatch.png")) == 1


def test_what_counts_as_one_color():
    def png(a):
        return pngio.encode(a)

    flat = np.full((720, 1280, 3), (0xf5, 0xf7, 0xfb), np.uint8)
    assert uniform(png(flat)) == ("#f5f7fb", 0, 720 * 1280)
    #  Dithering of a flat fill: within two levels.
    noisy = flat.copy()
    noisy[::7, ::5] += 2
    assert uniform(png(noisy))[0] in ("#f5f7fb", "#f7f9fd")
    #  A stray dot or two: still one color, and the share says how much.
    dots = flat.copy()
    dots[10, 10] = dots[500, 900] = 0
    assert uniform(png(dots)) == ("#f5f7fb", 2, 720 * 1280)
    #  One glyph of the smallest text is more than that.
    glyph = flat.copy()
    glyph[300:305, 600:604] = 40
    assert uniform(png(glyph)) is None
    #  A gradient background is not one color.
    ramp = np.repeat(np.linspace(200, 255, 1280).astype(np.uint8)[None, :, None],
                     720, axis=0).repeat(3, axis=2)
    assert uniform(png(ramp)) is None
    #  A small picture gets no stray pixels at all: a dot in it is content.
    small = np.full((20, 20, 3), 255, np.uint8)
    small[10, 10] = 0
    assert uniform(png(small)) is None


# --------------------------------------------------------------------------- #
#  3.2 — the hint about the focus and the pointer only for a change about them
# --------------------------------------------------------------------------- #
def _hints(focus_box, regions, size=(1280, 720), what="focus"):
    shot = SimpleNamespace(facts={what: {"sel": "button.primary:nth-of-type(1)",
                                         "box": list(focus_box)}},
                           stability=SimpleNamespace(stable=True), late=None,
                           moving=(), namer=None, retake=None)
    result = SimpleNamespace(regions=[SimpleNamespace(x=x, y=y, w=w, h=h)
                                      for x, y, w, h in regions],
                             size_actual=size, notes=[])
    away, blur = (what != "hover"), False
    return _failure_hints(shot, result, None, None, away, blur)


def test_a_change_on_the_whole_picture_is_not_blamed_on_the_focus():
    """What the stand showed: the theme did not apply, the background changed on
    97.7 % of the picture, and the message advised blur_focus=True."""
    assert _hints((560, 400, 160, 40), [(0, 0, 1280, 703)]) == []
    assert _hints((560, 400, 160, 40), [(0, 0, 1280, 703)], what="hover") == []


def test_a_change_much_larger_than_the_element_is_not_blamed_on_it():
    #  Half the page, overlapping the button: not about the button.
    assert _hints((560, 400, 160, 40), [(0, 300, 1280, 360)]) == []


def test_a_change_at_the_element_in_focus_is_still_named():
    hints = _hints((560, 400, 160, 40), [(556, 396, 168, 48)])      # a focus ring
    assert len(hints) == 1
    assert "button.primary:nth-of-type(1)" in hints[0] and "has the focus" in hints[0]
    assert "blur_focus=True" in hints[0]
    #  A tooltip the hover opened under the element: bigger than it, still its own.
    tip = _hints((560, 400, 160, 40), [(540, 396, 300, 120)], what="hover")
    assert len(tip) == 1 and "under the pointer" in tip[0]
    #  Exactly at the limit, and just past it.
    near = (160 + 32) * (40 + 32)
    side = int((NEAR_AREA_RATIO * near) ** 0.5)
    assert _hints((560, 400, 160, 40), [(500, 380, side, side)])
    assert _hints((560, 400, 160, 40), [(500, 380, side + 2, side + 2)]) == []


FOCUSED = """<!doctype html><html><head><style>
  body { margin: 0; font: 16px sans-serif; background: #ffffff; }
  button { margin: 40px; padding: 10px 18px; border: 0; background: #2563eb; color: #fff;
           outline: none; }
</style></head><body><button class="primary">Sign in</button></body></html>"""


def test_a_recoloured_background_with_a_button_in_focus_gives_no_focus_hint(ctx, page):
    page.set_content(FOCUSED)
    accept(ctx, page, "theme.png")
    page.evaluate("() => { document.body.style.background = '#0f172a'; }")
    page.focus("button")
    with pytest.raises(ScreenshotMismatch) as e:
        expect_screenshot(page, "theme.png")
    text = str(e.value)
    assert "has the focus" not in text and "blur_focus" not in text


# --------------------------------------------------------------------------- #
#  3.3 — a page that shows nothing but its background is waited for
# --------------------------------------------------------------------------- #
def test_an_application_that_arrives_late_is_photographed_with_its_content(ctx, page):
    capture_cfg(ctx, ready_timeout_ms=3000)
    page.set_content(LATE.replace("DELAY", "700"))
    accept(ctx, page, "late.png")
    written = baselines(ctx, "late.png")[0].read_bytes()
    assert uniform(written) is None
    assert BLANK_LINE not in last_row(ctx)["reason"]


def test_an_application_that_never_arrives_is_said_in_a_missing_baseline(ctx, page):
    capture_cfg(ctx, ready_timeout_ms=400)
    page.set_content(EMPTY_ROOT)
    with pytest.raises(BaselineMissing) as e:
        expect_screenshot(page, "login.png")
    assert BLANK_LINE in str(e.value)
    assert BLANK_LINE in last_row(ctx)["reason"]


def test_an_application_that_never_arrives_is_said_in_a_failure(ctx, page):
    capture_cfg(ctx, ready_timeout_ms=400)
    page.set_content(SPARSE)
    accept(ctx, page, "login.png")
    page.set_content(EMPTY_ROOT)
    with pytest.raises(ScreenshotMismatch) as e:
        expect_screenshot(page, "login.png")
    assert f"  capture:     {BLANK_LINE}" in str(e.value).splitlines()


def test_what_the_page_shows_is_seen(page):
    """The probe: text, a picture, a box of colour count; a full-size wrapper of
    the page's own colour, a hidden element and an empty element do not."""
    def blank(html):
        page.set_content(html)
        return _targets._wait_content(page, False, 1)[0]

    head = "<style>html,body{margin:0;height:100%;background:#f5f7fb}</style>"
    assert blank(EMPTY_ROOT) is True
    assert blank(head + '<div id="root" style="min-height:100%;background:#123"></div>') \
        is True
    assert blank(head + '<div id="root"><p style="visibility:hidden">x</p></div>') is True
    assert blank(head + '<div id="root"><div></div><span> </span></div>') is True
    assert blank(head + '<div id="root"><p>Sign in</p></div>') is False
    assert blank(head + '<div style="width:40px;height:40px;background:#fff"></div>') \
        is False
    assert blank(head + '<svg width="10" height="10"></svg>') is False
    assert blank(head + '<div style="width:40px;height:40px;border:1px solid #ccc">'
                        '</div>') is False
    #  Text below the window does not count for a picture of the window.
    assert blank(head + '<p style="margin-top:2000px">far</p>') is True
