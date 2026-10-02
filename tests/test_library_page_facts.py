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
    return next(Path(ctx.root, "tests", "__vistest__").rglob(name.replace(".png", ".json")))


BUTTONS = """<!doctype html><html><head><style>
  body { margin: 0; font: 16px sans-serif; background: #fff; }
  button { margin: 30px; padding: 10px 18px; border: 0; background: #2563eb; color: #fff; }
  button:hover { background: #1d4ed8; }
  input { margin: 0 30px; padding: 6px; border: 1px solid #999; outline: none; }
  input:focus { box-shadow: 0 0 0 4px rgba(37, 99, 235, .5); }
</style></head><body><button id="save">Save</button><br><input id="q"></body></html>"""


def test_a_hover_left_by_the_previous_step_is_named(ctx, page):
    page.set_content(BUTTONS)
    accept(ctx, page, "buttons.png")
    page.hover("#save")
    with pytest.raises(ScreenshotMismatch) as e:
        expect_screenshot(page, "buttons.png")
    text = str(e.value)
    assert "#save" in text and "under the pointer" in text
    assert "reset_hover_focus=True" in text


def test_a_focus_left_by_the_previous_step_is_named(ctx, page):
    page.set_content(BUTTONS)
    accept(ctx, page, "buttons.png")
    page.focus("#q")
    with pytest.raises(ScreenshotMismatch) as e:
        expect_screenshot(page, "buttons.png")
    assert "#q" in str(e.value) and "has the focus" in str(e.value)


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
    """The option is off by default: a check of a hover state keeps passing."""
    page.set_content(BUTTONS)
    page.hover("#save")
    accept(ctx, page, "hovered.png")
    assert expect_screenshot(page, "hovered.png").verdict.value == "pass"


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
    rect = page.evaluate("() => document.getElementById('card').getBoundingClientRect().top")
    assert round(rect) == place["y"]


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
    assert taken["version"] == 2
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
    record = {"version": 2, "launch": {"headless": False, "scrollbar_px": 15},
              "place": {"x": 20, "y": 120, "scroll_x": 0, "scroll_y": 600}}
    meta = SnapshotMeta.from_dict({**base, "capture": record})
    assert meta.capture == record
    assert SnapshotMeta.from_dict(meta.to_dict()).capture == record
    assert "capture" not in SnapshotMeta.from_dict(base).to_dict()
    for broken in ({"version": "2"}, {"launch": {"scrollbar_px": True}},
                   {"place": {"y": 1.5}}, {"when": 1}, []):
        with pytest.raises(ConfigError):
            SnapshotMeta.from_dict({**base, "capture": broken})
