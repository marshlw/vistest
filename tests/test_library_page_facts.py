# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""What the live page tells about a failure, and what S2b changed in a picture.

In a real Chromium, small pages: the element under the pointer or in focus is
named when it is what failed, and the option that moves the pointer away makes
the check pass; a Locator's element goes back where its baseline had it; an
element that changes by itself, and a canvas a script redraws, are named with
the mask to use; `data-vistest="ignore"` is painted out; the passport keeps
how the baseline was taken, and a check says when the launch differs or the
baseline was taken the old way.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vistest import expect_screenshot
from vistest.library import context as _context
from vistest.library.errors import ScreenshotMismatch


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


def accept(ctx, target, name, **kw):
    ctx.update = True
    try:
        return expect_screenshot(target, name, **kw)
    finally:
        ctx.update = False


def last_row(ctx) -> dict:
    rows = [json.loads(p.read_text("utf-8")) for p in ctx.parts_dir.glob("*.json")]
    return max(rows, key=lambda r: r.get("written_at", 0))


def passport(ctx, name: str) -> Path:
    return next(Path(ctx.baselines).rglob(name.replace(".png", ".json")))


BUTTONS = """<!doctype html><html><head><style>
  body { margin: 0; font: 16px sans-serif; background: #fff; }
  button { margin: 30px; padding: 10px 18px; border: 0; background: #2563eb; color: #fff; }
  button:hover { background: #1d4ed8; }
  input { margin: 0 30px; padding: 6px; border: 1px solid #999; outline: none; }
  input:focus { box-shadow: 0 0 0 4px rgba(37, 99, 235, .5); }
</style></head><body><button id="save">Save</button><br><input id="q"></body></html>"""


def test_a_hover_left_by_the_previous_step_is_not_photographed_by_default(ctx, page):
    """The pointer is moved off the page before the picture: no element is under it."""
    page.set_content(BUTTONS)
    accept(ctx, page, "buttons.png")
    page.hover("#save")
    assert expect_screenshot(page, "buttons.png").verdict.value == "pass"


def test_a_hover_kept_on_purpose_is_named_when_it_is_what_failed(ctx, page):
    page.set_content(BUTTONS)
    accept(ctx, page, "buttons.png")
    page.hover("#save")
    with pytest.raises(ScreenshotMismatch) as e:
        expect_screenshot(page, "buttons.png", keep_pointer=True)
    text = str(e.value)
    assert "#save" in text and "under the pointer" in text
    assert "keep_pointer" in text


def test_a_focus_left_by_the_previous_step_is_named(ctx, page):
    page.set_content(BUTTONS)
    accept(ctx, page, "buttons.png")
    page.focus("#q")
    with pytest.raises(ScreenshotMismatch) as e:
        expect_screenshot(page, "buttons.png")
    assert "#q" in str(e.value) and "has the focus" in str(e.value)
    assert "blur_focus=True" in str(e.value)


def test_the_option_moves_the_pointer_away_and_takes_the_focus_off(ctx, page):
    page.set_content(BUTTONS)
    accept(ctx, page, "buttons.png", reset_hover_focus=True)
    page.hover("#save")
    assert expect_screenshot(page, "buttons.png",
                             reset_hover_focus=True).verdict.value == "pass"
    page.focus("#q")
    assert expect_screenshot(page, "buttons.png",
                             reset_hover_focus=True).verdict.value == "pass"


def test_a_hover_photographed_on_purpose_still_works(ctx, page):
    """`keep_pointer=True`: a check of a hover state keeps passing; without it, it fails."""
    page.set_content(BUTTONS)
    page.hover("#save")
    accept(ctx, page, "hovered.png", keep_pointer=True)
    assert expect_screenshot(page, "hovered.png", keep_pointer=True).verdict.value == "pass"
    with pytest.raises(ScreenshotMismatch):
        expect_screenshot(page, "hovered.png")           # the pointer is moved away


SCROLLED = """<!doctype html><html><head><style>
  body { margin: 0; font: 16px sans-serif; }
  .gap { height: 700px; }
  #card { margin: 0 20px; height: 120px; border: 1px solid #ccc;
          background: linear-gradient(135deg, #e0e7ff, #fef3c7) fixed; }
</style></head><body><div class="gap"></div><div id="card">Plan</div>
<div class="gap"></div></body></html>"""


def test_an_element_goes_back_where_its_baseline_had_it(ctx, page):
    page.set_content(SCROLLED)
    accept(ctx, page.locator("#card"), "card.png")
    place = json.loads(passport(ctx, "card.png").read_text())["capture"]["place"]
    assert set(place) == {"x", "y", "scroll_x", "scroll_y"}

    page.evaluate("() => window.scrollTo(0, 650)")       # the card in view, elsewhere
    result = expect_screenshot(page.locator("#card"), "card.png")
    assert result.verdict.value == "pass"

    #  Where the picture was taken is read off the capture itself: the page is
    #  scrolled back afterwards (see the tests below), so the window no longer
    #  shows the place.
    from vistest.library import targets

    page.evaluate("() => window.scrollTo(0, 650)")
    shot = targets.capture(page.locator("#card"), place={"x": place["x"], "y": place["y"]})
    assert shot.facts["place"]["y"] == place["y"] and shot.placed
    assert page.evaluate("() => window.scrollY") == 650


def test_without_a_recorded_place_nothing_is_moved(ctx, page):
    page.set_content(SCROLLED)
    accept(ctx, page.locator("#card"), "card.png")
    path = passport(ctx, "card.png")
    data = json.loads(path.read_text())
    del data["capture"]["place"]
    path.write_text(json.dumps(data))
    page.evaluate("() => window.scrollTo(0, 650)")
    before = page.evaluate("() => window.scrollY")
    try:
        expect_screenshot(page.locator("#card"), "card.png")
    except ScreenshotMismatch:
        pass
    assert page.evaluate("() => window.scrollY") == before


TICKING = """<!doctype html><html><body style="margin:0;font:24px monospace">
<p>Visitors: <span id="n">0</span></p>
<script>let i = 0; setInterval(() => { document.getElementById('n').textContent = ++i; }, 200);
</script></body></html>"""


def test_an_element_that_changes_by_itself_is_named_with_its_mask(ctx, page):
    page.set_content(TICKING)
    page.wait_for_timeout(250)
    accept(ctx, page, "ticking.png")
    page.wait_for_timeout(450)
    with pytest.raises(ScreenshotMismatch) as e:
        expect_screenshot(page, "ticking.png")
    text = str(e.value)
    assert 'mask=["#n"]' in text and "changes by itself" in text
    page.wait_for_timeout(250)
    accept(ctx, page, "masked.png", mask=["#n"])
    page.wait_for_timeout(450)
    assert expect_screenshot(page, "masked.png", mask=["#n"]).verdict.value == "pass"


CANVAS = """<!doctype html><html><body style="margin:0">
<canvas id="anim" width="200" height="60"></canvas>
<script>const c = document.getElementById('anim').getContext('2d'); let x = 0;
(function draw() { c.fillStyle = '#fff'; c.fillRect(0, 0, 200, 60); c.fillStyle = '#2563eb';
  c.fillRect(x, 20, 30, 20); x = (x + 3) % 170; requestAnimationFrame(draw); })();
</script></body></html>"""


def test_a_canvas_a_script_redraws_is_named_and_why_css_does_not_stop_it(ctx, page):
    page.set_content(CANVAS)
    with pytest.warns(Warning):
        accept(ctx, page, "canvas.png", stable_timeout_ms=600)
    with pytest.raises(ScreenshotMismatch) as e:
        expect_screenshot(page, "canvas.png", stable_timeout_ms=600)
    text = str(e.value)
    assert 'mask=["#anim"]' in text and 'animations="disabled"' in text


def test_data_vistest_ignore_is_painted_out(ctx, page):
    page.set_content(TICKING.replace('<span id="n">', '<span id="n" data-vistest="ignore">'))
    page.wait_for_timeout(250)
    accept(ctx, page, "ignored.png")
    page.wait_for_timeout(450)
    assert expect_screenshot(page, "ignored.png").verdict.value == "pass"


def test_the_passport_keeps_how_the_baseline_was_taken(ctx, page):
    page.set_content(BUTTONS)
    accept(ctx, page, "buttons.png")
    taken = json.loads(passport(ctx, "buttons.png").read_text())["capture"]
    assert taken["version"] == 3
    assert taken["launch"] == {"headless": True, "scrollbar_px": 0}


def test_a_different_launch_is_said_in_one_line(ctx, page):
    page.set_content(BUTTONS)
    accept(ctx, page, "buttons.png")
    path = passport(ctx, "buttons.png")
    data = json.loads(path.read_text())
    data["capture"]["launch"] = {"headless": False, "scrollbar_px": 15}
    path.write_text(json.dumps(data))
    expect_screenshot(page, "buttons.png")
    reason = last_row(ctx)["reason"]
    assert ("the baseline was taken in a browser with a window, 15 px scroll bars, "
            "this check in one headless, no scroll bars") in reason


def test_a_baseline_taken_the_old_way_says_so_when_it_fails(ctx, page):
    page.set_content(BUTTONS)
    accept(ctx, page, "buttons.png")
    path = passport(ctx, "buttons.png")
    data = json.loads(path.read_text())
    del data["capture"]
    path.write_text(json.dumps(data))
    page.evaluate("() => { document.getElementById('save').textContent = 'Saved'; }")
    with pytest.raises(ScreenshotMismatch) as e:
        expect_screenshot(page, "buttons.png")
    assert "taken the old way" in str(e.value)
    assert "--vistest-update=changed" in str(e.value)


def test_a_passport_capture_record_is_checked_and_round_trips():
    from vistest.core.settings import ConfigError
    from vistest.storage.base import SnapshotMeta

    base = {"version": 1, "width": 4, "height": 4, "sha256": "0" * 64}
    record = {"version": 3, "launch": {"headless": False, "scrollbar_px": 15},
              "place": {"x": 20, "y": 120, "scroll_x": 0, "scroll_y": 600},
              "window_scroll": {"x": 0, "y": 650}, "device_scale_factor": 2.0}
    meta = SnapshotMeta.from_dict({**base, "capture": record})
    assert meta.capture == record
    assert SnapshotMeta.from_dict(meta.to_dict()).capture == record
    assert "capture" not in SnapshotMeta.from_dict(base).to_dict()
    for broken in ({"version": "2"}, {"launch": {"scrollbar_px": True}},
                   {"place": {"y": 1.5}}, {"when": 1}, [],
                   {"window_scroll": {"y": 1.5}}, {"device_scale_factor": 0},
                   {"device_scale_factor": "2"}):
        with pytest.raises(ConfigError):
            SnapshotMeta.from_dict({**base, "capture": broken})


# --------------------------------------------------------------------------- #
#  A picture of an element leaves the page scrolled where the test left it
# --------------------------------------------------------------------------- #
#  The card sits at the bottom of a scrollable box, the box in the middle of a
#  page that scrolls both ways: to photograph the card the window has to move,
#  and so does the box. Neither is the check's to leave moved.
CONTAINED = """<!doctype html><html><head><style>
  body { margin: 0; font: 16px sans-serif; }
  .gap { height: 900px; }
  .wide { width: 1200px; height: 1px; }
  #box { height: 150px; width: 340px; overflow: auto; margin: 0 20px;
         border: 1px solid #888; }
  .pad { height: 400px; }
  #card { height: 80px; margin: 0 8px; background: #dbeafe; }
</style></head><body><div class="gap"></div><div class="wide"></div>
<div id="box"><div class="pad"></div><div id="card">Plan <span id="n">0</span></div>
<div class="pad"></div></div><div class="gap"></div>
<script>let i = 0; if (location.hash !== '#still')
  setInterval(() => { document.getElementById('n').textContent = ++i; }, 200);
</script></body></html>"""

STILL = CONTAINED.replace("if (location.hash !== '#still')", "if (false)")


def scroll_of(page) -> dict:
    return page.evaluate("""() => ({x: window.scrollX, y: window.scrollY,
        left: document.getElementById('box').scrollLeft,
        top: document.getElementById('box').scrollTop})""")


def leave_scrolled(page, *, x, y, top):
    page.evaluate("([x, y, top]) => { window.scrollTo(x, y);"
                  " document.getElementById('box').scrollTop = top; }", [x, y, top])
    return scroll_of(page)


def test_a_picture_of_an_element_leaves_the_scrolling_where_it_was(ctx, page):
    """With a recorded place, and the card put back where its baseline had it."""
    page.set_content(STILL)
    before = leave_scrolled(page, x=30, y=640, top=0)
    accept(ctx, page.locator("#card"), "card.png")
    assert scroll_of(page) == before, "taking the baseline moved the page"
    place = json.loads(passport(ctx, "card.png").read_text())["capture"]["place"]
    assert set(place) == {"x", "y", "scroll_x", "scroll_y"}

    before = leave_scrolled(page, x=30, y=420, top=90)
    result = expect_screenshot(page.locator("#card"), "card.png")
    assert result.verdict.value == "pass"
    assert scroll_of(page) == before, "the check moved the window or the box"


def test_the_same_without_a_recorded_place(ctx, page):
    page.set_content(STILL)
    leave_scrolled(page, x=0, y=640, top=0)
    accept(ctx, page.locator("#card"), "card.png")
    path = passport(ctx, "card.png")
    data = json.loads(path.read_text())
    del data["capture"]["place"]
    path.write_text(json.dumps(data))

    before = leave_scrolled(page, x=45, y=100, top=0)      # the box not even in view
    expect_screenshot(page.locator("#card"), "card.png")
    assert scroll_of(page) == before


def test_a_failed_check_leaves_the_scrolling_too_and_still_names_what_moves(ctx, page):
    """The second look takes more frames, and the naming looks at the live page."""
    page.set_content(CONTAINED)
    page.wait_for_timeout(250)
    leave_scrolled(page, x=0, y=640, top=0)
    accept(ctx, page.locator("#card"), "ticking.png")
    page.wait_for_timeout(450)
    before = leave_scrolled(page, x=20, y=120, top=60)
    with pytest.raises(ScreenshotMismatch) as e:
        expect_screenshot(page.locator("#card"), "ticking.png")
    assert scroll_of(page) == before
    assert 'mask=["#n"]' in str(e.value) and "changes by itself" in str(e.value)


def test_a_page_is_not_scrolled_by_its_own_picture(ctx, page):
    page.set_content(STILL)
    before = leave_scrolled(page, x=30, y=400, top=40)
    accept(ctx, page, "page.png")
    expect_screenshot(page, "page.png")
    assert scroll_of(page) == before


# --------------------------------------------------------------------------- #
#  A page that scrolls smoothly: nothing waits for an animation, nothing is
#  photographed in the middle of one
# --------------------------------------------------------------------------- #
SMOOTH = STILL.replace("body { margin: 0;",
                       "html, #box { scroll-behavior: smooth; }\n  body { margin: 0;")
#  The place of an element in the window is what a `fixed` background shows, so
#  the picture is the witness of where the element stood.
SMOOTH_PAGE = SCROLLED.replace("<style>", "<style>\n  html { scroll-behavior: smooth; }", 1)


def jump(page, *, x, y, top=None):
    """The test's own scrolling, instant: the page's CSS would animate a plain scrollTo."""
    page.evaluate("""([x, y, top]) => {
        window.scrollTo({left: x, top: y, behavior: 'instant'});
        const box = document.getElementById('box');
        if (box && top !== null) box.scrollTo({top, behavior: 'instant'}); }""",
                  [x, y, top])
    page.wait_for_timeout(50)
    return page.evaluate("() => ({x: window.scrollX, y: window.scrollY})")


def test_on_a_smooth_page_the_element_goes_back_where_its_baseline_had_it(ctx, page):
    from vistest.library import targets

    page.set_content(SMOOTH_PAGE)
    jump(page, x=0, y=700)
    accept(ctx, page.locator("#card"), "card.png")
    place = json.loads(passport(ctx, "card.png").read_text())["capture"]["place"]

    before = jump(page, x=0, y=650)                  # the card in view, elsewhere
    result = expect_screenshot(page.locator("#card"), "card.png")
    assert result.verdict.value == "pass"            # the fixed background is the witness
    assert page.evaluate("() => ({x: scrollX, y: scrollY})") == before
    page.wait_for_timeout(700)                       # an animation still on its way
    assert page.evaluate("() => ({x: scrollX, y: scrollY})") == before

    jump(page, x=0, y=650)
    shot = targets.capture(page.locator("#card"), place={"x": place["x"], "y": place["y"]})
    assert shot.placed and shot.facts["place"]["y"] == place["y"]
    page.wait_for_timeout(700)
    assert page.evaluate("() => scrollY") == 650


def test_the_placing_does_not_animate(page):
    """Read at once, not after Playwright has waited for the animation to end.

    Taking the picture of an element makes Playwright wait until the element
    stops moving, which hides a smooth scroll in the end-to-end tests here:
    they pass either way. What the placing itself does is visible only right
    after it, so that is where it is asked.
    """
    from vistest.library import page_facts

    page.set_content(SMOOTH_PAGE)
    jump(page, x=0, y=700)
    card = page.locator("#card")
    target = page_facts.facts(card, True, False)["place"]

    jump(page, x=0, y=500)
    got = page_facts.prepare(card, True, place={"x": target["x"], "y": target["y"]},
                             blur=False)
    assert got["placed"] == [target["x"], target["y"]], "the element was not there yet"
    assert page.evaluate("() => scrollY") == 700, "the window was still on its way"


def test_a_smooth_window_and_a_smooth_box_are_left_where_the_test_had_them(ctx, page):
    page.set_content(SMOOTH)
    jump(page, x=0, y=640, top=0)
    accept(ctx, page.locator("#card"), "card.png")
    jump(page, x=25, y=420, top=90)
    before = scroll_of(page)
    assert expect_screenshot(page.locator("#card"), "card.png").verdict.value == "pass"
    assert scroll_of(page) == before
    page.wait_for_timeout(700)
    assert scroll_of(page) == before, "an animated scroll was still on its way"


def test_a_failed_check_on_a_smooth_page_leaves_it_where_it_was(ctx, page):
    page.set_content(SMOOTH.replace("if (false)", "if (true)"))   # the counter ticks
    page.wait_for_timeout(250)
    jump(page, x=0, y=640, top=0)
    accept(ctx, page.locator("#card"), "ticking.png")
    page.wait_for_timeout(450)
    jump(page, x=20, y=120, top=60)
    before = scroll_of(page)
    with pytest.raises(ScreenshotMismatch):
        expect_screenshot(page.locator("#card"), "ticking.png")
    page.wait_for_timeout(600)
    assert scroll_of(page) == before


# --------------------------------------------------------------------------- #
#  dev2: the window's scroll
# --------------------------------------------------------------------------- #
def test_a_picture_of_the_window_is_taken_where_its_baseline_was(ctx, page):
    page.set_content(SCROLLED)
    page.evaluate("() => window.scrollTo(0, 650)")
    accept(ctx, page, "window.png")
    record = json.loads(passport(ctx, "window.png").read_text())["capture"]
    assert record["window_scroll"] == {"x": 0, "y": 650}
    assert record["version"] == 3

    page.evaluate("() => window.scrollTo(0, 100)")        # the test left it elsewhere
    result = expect_screenshot(page, "window.png")
    assert result.verdict.value == "pass"
    assert page.evaluate("() => window.scrollY") == 100    # and it is put back
    assert any("(0, 100)" in n and "set to (0, 650)" in n for n in result.notes)


def test_the_window_scroll_can_be_switched_off(ctx, page):
    page.set_content(SCROLLED)
    page.evaluate("() => window.scrollTo(0, 650)")
    accept(ctx, page, "window.png")
    page.evaluate("() => window.scrollTo(0, 100)")
    with pytest.raises(ScreenshotMismatch):
        expect_screenshot(page, "window.png", restore_scroll=False)
    assert page.evaluate("() => window.scrollY") == 100


def test_a_page_too_short_to_scroll_that_far_keeps_the_difference_and_says_so(ctx, page):
    page.set_content(SCROLLED)
    page.evaluate("() => window.scrollTo(0, 650)")
    accept(ctx, page, "window.png")
    path = passport(ctx, "window.png")
    data = json.loads(path.read_text())
    data["capture"]["window_scroll"] = {"x": 0, "y": 99999}
    path.write_text(json.dumps(data))
    with pytest.raises(ScreenshotMismatch) as e:
        expect_screenshot(page, "window.png")
    assert "could not be set to (0, 99999)" in str(e.value)
    assert "the difference stays" in str(e.value)


def test_a_full_page_picture_is_not_scrolled(ctx, page):
    page.set_content(SCROLLED)
    accept(ctx, page, "full.png", full_page=True)
    assert "window_scroll" not in json.loads(passport(ctx, "full.png").read_text())["capture"]


def test_the_device_scale_factor_is_in_the_passport(ctx, page):
    page.set_content(SCROLLED)
    accept(ctx, page, "dpr.png")
    assert json.loads(passport(ctx, "dpr.png").read_text())["capture"][
        "device_scale_factor"] == 1.0
