# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""How the library photographs a live page: parity with `toHaveScreenshot()`.

Most flaky screenshot tests are not flaky at comparison. They are flaky at
capture: a spinner caught mid-turn, a caret caught mid-blink, a web font that
arrived a frame after the picture. Playwright's own assertion handles all of
that before it compares anything, and the library used to take one bare
`screenshot()` and hope.

Two kinds of test live here. The first half runs anywhere: a fake page that
records what it was asked for, and a fake clock where time matters. The second
half starts a real Chromium and is skipped — not failed — when there is none,
and it asks for Chromium only, so whether other browsers are installed makes no
difference to it.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from vistest import expect_screenshot
from vistest.core import pngio
from vistest.library import context as _context
from vistest.library import targets
from vistest.library.errors import BaselineMissing, ScreenshotMismatch


def frame(fill: int = 40, size: tuple[int, int] = (80, 120)) -> bytes:
    return pngio.encode(np.full((*size, 3), fill, np.uint8))


@pytest.fixture
def ctx(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    context = _context.LibraryContext(root=tmp_path)
    _context.install(context)
    yield context
    _context.uninstall()


def accept(ctx, target, name="page.png", **kw):
    ctx.update = True
    try:
        return expect_screenshot(target, name, **kw)
    finally:
        ctx.update = False


def rows(ctx) -> list[dict]:
    """The report rows written so far, oldest first."""
    found = [json.loads(p.read_text("utf-8")) for p in ctx.parts_dir.glob("*.json")]
    return sorted(found, key=lambda r: r["written_at"])


class FakePage:
    """A page that hands out a scripted sequence of frames, then the last one."""

    viewport_size = {"width": 1440, "height": 900}

    def __init__(self, *frames: bytes, ratio: float = 1.0, fonts_error=None):
        self.frames = list(frames)
        self.ratio = ratio
        self.fonts_error = fonts_error
        self.calls: list[dict] = []
        self.evaluated: list[str] = []

    def goto(self, *_): ...

    def locator(self, selector):
        return f"locator({selector})"

    def evaluate(self, script, *args):
        self.evaluated.append(script)
        if "devicePixelRatio" in script:
            return self.ratio
        if self.fonts_error is not None:
            raise self.fonts_error
        return True

    def screenshot(self, **kwargs):
        self.calls.append(kwargs)
        return self.frames.pop(0) if len(self.frames) > 1 else self.frames[0]


# --------------------------------------------------------------------------- #
#  What is asked of Playwright
# --------------------------------------------------------------------------- #
def test_the_screenshot_is_taken_as_to_have_screenshot_takes_it(ctx):
    page = FakePage(frame())
    accept(ctx, page)
    first = page.calls[0]
    assert first["animations"] == "disabled"
    assert first["caret"] == "hide"
    assert first["scale"] == "css"
    assert first["type"] == "png"


def test_fonts_are_waited_for_before_the_first_frame(ctx):
    from vistest.capture.stabilize import WAIT_FONTS_JS

    page = FakePage(frame())
    accept(ctx, page)
    assert page.evaluated[0] == WAIT_FONTS_JS


def test_a_font_wait_that_fails_is_said_not_swallowed(ctx):
    page = FakePage(frame(), fonts_error=RuntimeError("fonts API gone"))
    accept(ctx, page)
    result = expect_screenshot(page, "page.png")
    assert any("fonts API gone" in n for n in result.notes)
    assert "fonts API gone" in rows(ctx)[-1]["reason"]


# --------------------------------------------------------------------------- #
#  The stability loop
# --------------------------------------------------------------------------- #
class Clock:
    """Time that moves only when somebody sleeps or a frame is taken."""

    def __init__(self, frame_ms: int = 0):
        self.now = 0.0
        self.frame_ms = frame_ms
        self.pauses: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.pauses.append(seconds)
        self.now += seconds


def scripted(clock: Clock, *frames: bytes):
    queue = list(frames)

    def shoot() -> bytes:
        clock.now += clock.frame_ms / 1000
        return queue.pop(0) if len(queue) > 1 else queue[0]

    return shoot


def test_two_identical_frames_in_a_row_end_the_loop():
    clock = Clock(frame_ms=10)
    png, st = targets.settle_frames(
        scripted(clock, b"a", b"b", b"c", b"c", b"d"), timeout_ms=5000,
        clock=clock, sleep=clock.sleep)
    assert png == b"c"
    assert st.stable is True and st.frames == 4
    #  Playwright's schedule: 0, 100, 250 ms before frames two to four.
    assert clock.pauses == [0.1, 0.25]
    assert st.elapsed_ms == 350 + 4 * 10


def test_a_page_that_never_settles_gives_back_its_last_frame():
    clock = Clock(frame_ms=10)
    counter = iter(range(10_000))

    png, st = targets.settle_frames(lambda: str(next(counter)).encode(),
                                    timeout_ms=2000, clock=clock, sleep=clock.sleep)
    assert st.stable is False
    assert png == str(st.frames - 1).encode(), "the last frame, not the first"
    assert st.elapsed_ms < 2000
    assert "did not settle" in st.unsettled_text()


def test_one_slow_frame_is_not_called_unstable():
    """A first frame slower than the whole limit still gets its second look."""
    clock = Clock(frame_ms=3000)
    png, st = targets.settle_frames(scripted(clock, b"x"), timeout_ms=1000,
                                    clock=clock, sleep=clock.sleep)
    assert st.frames == 2 and st.stable is True


def test_zero_takes_one_frame_and_claims_nothing():
    clock = Clock()
    _, st = targets.settle_frames(scripted(clock, b"a", b"b"), timeout_ms=0,
                                  clock=clock, sleep=clock.sleep)
    assert st.frames == 1 and st.stable is None
    assert st.unsettled_text() == ""


def test_frames_and_time_to_stability_go_into_the_report_row(ctx):
    page = FakePage(frame(10), frame(20), frame(30), frame(30))
    accept(ctx, page)
    captured = rows(ctx)[-1]["capture"]
    assert captured["frames"] == 4 and captured["stable"] is True
    assert captured["elapsed_ms"] >= 350 and captured["timeout_ms"] == 5000
    assert captured["scale"] == "css"


def test_an_unsettled_page_is_compared_on_its_last_frame_and_says_so(ctx):
    accept(ctx, FakePage(frame(40)))
    counter = iter(range(1, 250))

    class Moving(FakePage):
        """Every frame different from the one before: it never settles."""

        def screenshot(self, **kwargs):
            png = frame(40 + next(counter) % 2 * 60)
            self.calls.append(png)
            return png

    page = Moving(frame(0))
    try:
        result = expect_screenshot(page, "page.png", stable_timeout_ms=400)
        notes = result.notes
    except ScreenshotMismatch as e:
        notes = e.result.notes
    row = rows(ctx)[-1]
    assert row["capture"]["stable"] is False
    assert row["capture"]["frames"] == len(page.calls) >= 3
    assert "did not settle" in row["reason"]
    assert any("did not settle" in n for n in notes)
    #  The artifact is the frame that was compared: the last one taken.
    assert Path(row["images"]["actual"]).read_bytes() == page.calls[-1]
    assert row["verdict"] == ("pass" if page.calls[-1] == frame(40) else "fail")


def test_an_unsettled_failure_carries_the_line_in_the_exception(ctx):
    accept(ctx, FakePage(frame(40)))
    counter = iter(range(1, 250))

    class Moving(FakePage):
        def screenshot(self, **kwargs):
            return frame(140 + next(counter) % 100)

    with pytest.raises(ScreenshotMismatch) as e:
        expect_screenshot(Moving(frame(0)), "page.png", stable_timeout_ms=300)
    assert "did not settle" in str(e.value)


def test_accepting_an_unsettled_frame_warns(ctx):
    counter = iter(range(1, 250))

    class Moving(FakePage):
        def screenshot(self, **kwargs):
            return frame(next(counter))

    with pytest.warns(Warning, match="did not settle"):
        accept(ctx, Moving(frame(0)), stable_timeout_ms=300)


# --------------------------------------------------------------------------- #
#  The time limit: the call, the config, and nonsense in either
# --------------------------------------------------------------------------- #
def test_the_limit_comes_from_the_config(tmp_path, monkeypatch):
    (tmp_path / "vistest.yaml").write_text(
        "capture:\n  stable_timeout_ms: 1234\n", "utf-8")
    monkeypatch.chdir(tmp_path)
    context = _context.LibraryContext(root=tmp_path,
                                      config_path=str(tmp_path / "vistest.yaml"))
    _context.install(context)
    try:
        accept(context, FakePage(frame()))
        assert rows(context)[-1]["capture"]["timeout_ms"] == 1234
    finally:
        _context.uninstall()


def test_the_call_beats_the_config(ctx):
    accept(ctx, FakePage(frame()), stable_timeout_ms=777)
    assert rows(ctx)[-1]["capture"]["timeout_ms"] == 777


@pytest.mark.parametrize("value", ["5s", -1, 2.5, True])
def test_a_bad_limit_in_the_config_is_loud(tmp_path, value):
    from vistest.config import ConfigError, VisTestConfig

    path = tmp_path / "vistest.yaml"
    path.write_text(f"capture:\n  stable_timeout_ms: {json.dumps(value)}\n", "utf-8")
    with pytest.raises(ConfigError) as e:
        VisTestConfig.load(path)
    assert "stable_timeout_ms" in str(e.value) and "vistest.yaml" in str(e.value)


def test_a_misspelt_key_is_loud(tmp_path):
    path = tmp_path / "vistest.yaml"
    path.write_text("capture:\n  stable_timeout: 100\n", "utf-8")
    from vistest.config import VisTestConfig

    with pytest.raises(ValueError, match="capture.stable_timeout"):
        VisTestConfig.load(path)


@pytest.mark.parametrize("value, error", [(-5, ValueError), ("1s", TypeError),
                                          (1.5, TypeError)])
def test_a_bad_limit_in_the_call_is_refused(ctx, value, error):
    with pytest.raises(error, match="stable_timeout_ms"):
        expect_screenshot(FakePage(frame()), "page.png", stable_timeout_ms=value)


def test_an_unknown_scale_is_refused(ctx):
    with pytest.raises(ValueError, match="scale"):
        expect_screenshot(FakePage(frame()), "page.png", scale="retina")


# --------------------------------------------------------------------------- #
#  Scale and the platform key
# --------------------------------------------------------------------------- #
def test_css_pixels_are_1x_whatever_the_screen(ctx):
    accept(ctx, FakePage(frame(), ratio=2.0))
    [written] = list(ctx.baselines.rglob("*.png"))
    assert "-1x-" in written.parent.name


def test_device_pixels_carry_the_screens_ratio_in_the_key(ctx):
    """A 2x picture never shares a directory — or a baseline — with a 1x one."""
    page = FakePage(frame(size=(160, 240)), ratio=2.0)
    accept(ctx, page, scale="device")
    assert page.calls[0]["scale"] == "device"
    [written] = list(ctx.baselines.rglob("*.png"))
    assert "-2x-" in written.parent.name


def test_a_device_scale_baseline_is_explained_not_just_reported(ctx):
    """The migration case: a HiDPI baseline from before `scale="css"`.

    The key did not move (it said 1x then, and says 1x now), so the old
    baseline is found — and it is exactly twice the size of the new picture.
    That is not left as «the picture changed size».
    """
    old = FakePage(frame(size=(160, 240)))
    accept(ctx, old)                         # stands in for the old 2x capture
    with pytest.raises(ScreenshotMismatch) as e:
        expect_screenshot(FakePage(frame(size=(80, 120))), "page.png")
    message = str(e.value)
    assert "exactly 2x" in message and "--vistest-update" in message
    assert 'scale="device"' in message


def test_byte_targets_are_not_touched_by_any_of_this(ctx):
    accept(ctx, frame())
    assert "capture" not in rows(ctx)[-1] or \
        rows(ctx)[-1]["capture"].get("stable") is None


# --------------------------------------------------------------------------- #
#  A real Chromium
# --------------------------------------------------------------------------- #
ANIMATED_PAGE = """<!doctype html>
<html><head><style>
  body { margin: 0; font: 16px sans-serif; background: #fff; }
  .spinner { width: 40px; height: 40px; margin: 20px;
             border: 6px solid #ddd; border-top-color: #06c; border-radius: 50%;
             animation: spin 0.4s linear infinite; }
  .pulse { width: 120px; height: 20px; margin: 20px; background: #c33;
           animation: pulse 0.3s ease-in-out infinite alternate; }
  @keyframes spin { to { transform: rotate(360deg); } }
  @keyframes pulse { from { opacity: 1; } to { opacity: 0.1; } }
  input { margin: 20px; font-size: 20px; }
</style></head>
<body>
  <div class="spinner"></div>
  <div class="pulse"></div>
  <input id="field" value="caret here" autofocus>
</body></html>"""

RESTLESS_PAGE = """<!doctype html>
<html><body style="margin:0;font:32px monospace">
  <div id="n">0</div>
  <script>
    let i = 0;
    setInterval(() => { document.getElementById('n').textContent = String(++i); }, 16);
  </script>
</body></html>"""


@pytest.fixture(scope="module")
def chromium():
    """Chromium, or a skip that says why. Other browsers are never asked for."""
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
    context = chromium.new_context(viewport={"width": 320, "height": 240})
    page = context.new_page()
    yield page
    context.close()


def test_a_bare_screenshot_of_this_page_really_does_move(page):
    """The control. Without it the next test could pass on a page that is still."""
    page.set_content(ANIMATED_PAGE)
    page.focus("#field")
    shots = set()
    for _ in range(6):
        shots.add(page.screenshot(type="png"))
        page.wait_for_timeout(70)
    assert len(shots) > 1


def test_css_animation_and_a_blinking_caret_give_a_stable_frame(ctx, page):
    page.set_content(ANIMATED_PAGE)
    page.focus("#field")
    accept(ctx, page, "animated.png")
    first = rows(ctx)[-1]["capture"]
    assert first["stable"] is True, first

    for _ in range(3):
        page.wait_for_timeout(130)            # a different moment of every cycle
        result = expect_screenshot(page, "animated.png")
        assert result.verdict.value == "pass"
        captured = rows(ctx)[-1]["capture"]
        assert captured["stable"] is True and captured["frames"] == 2, captured


def test_a_restless_page_is_judged_on_its_last_frame_and_says_so(ctx, page):
    page.set_content(RESTLESS_PAGE)
    with pytest.warns(Warning, match="did not settle"):
        accept(ctx, page, "restless.png", stable_timeout_ms=600)

    try:
        expect_screenshot(page, "restless.png", stable_timeout_ms=600)
    except ScreenshotMismatch as e:
        assert "did not settle" in str(e)
    row = rows(ctx)[-1]
    assert row["capture"]["stable"] is False
    assert row["capture"]["frames"] >= 3
    assert "did not settle" in row["reason"]
    assert row["verdict"] in ("pass", "fail")      # a verdict, not an error


def test_a_locator_is_photographed_the_same_way(ctx, page):
    page.set_content(ANIMATED_PAGE)
    accept(ctx, page.locator(".spinner"), "spinner.png")
    page.wait_for_timeout(170)
    assert expect_screenshot(page.locator(".spinner"),
                             "spinner.png").verdict.value == "pass"


def test_missing_baseline_still_raises_with_a_live_page(ctx, page):
    page.set_content(ANIMATED_PAGE)
    with pytest.raises(BaselineMissing):
        expect_screenshot(page, "nothing-yet.png")


# --------------------------------------------------------------------------- #
#  A second look at a failure — the server's logic, now here too
# --------------------------------------------------------------------------- #
def page_png(*, spinner: int = 10, header_shift: bool = False) -> bytes:
    """A still header and a corner where a spinner turns (as the server's test)."""
    img = np.full((80, 120, 3), 240, np.uint8)
    img[4:14, 4:100] = 40
    if header_shift:
        img[4:14, 4:100] = 240
        img[10:20, 4:100] = 40
    img[60:76, 96:116] = spinner
    return pngio.encode(img)


def test_a_failure_that_does_not_reproduce_is_suppressed_as_unstable(ctx):
    accept(ctx, FakePage(page_png(spinner=10)))
    #  Two identical frames settle the loop; the third is the second look.
    page = FakePage(page_png(spinner=250), page_png(spinner=250),
                    page_png(spinner=120))
    result = expect_screenshot(page, "page.png")
    assert result.verdict.value == "pass"
    assert any((r.suppressed_by or "").startswith("unstable:")
               for r in result.suppressed)
    assert any("passed on the second" in n for n in result.notes)
    assert len(page.calls) == 3
    row = rows(ctx)[-1]
    assert row["suppressed_by_reason"] == {
        "suppressed: did not reproduce on a second capture": 1}


def test_a_steady_regression_survives_the_second_look(ctx):
    accept(ctx, FakePage(page_png(spinner=10)))
    page = FakePage(page_png(spinner=250, header_shift=True),
                    page_png(spinner=250, header_shift=True),
                    page_png(spinner=120, header_shift=True))
    with pytest.raises(ScreenshotMismatch) as e:
        expect_screenshot(page, "page.png")
    result = e.value.result
    assert all(r.y < 40 for r in result.regions)
    assert any((r.suppressed_by or "").startswith("unstable:")
               for r in result.suppressed)


def test_the_picture_compared_is_the_first_frame(ctx):
    accept(ctx, FakePage(page_png(spinner=10)))
    first = page_png(spinner=250)
    page = FakePage(first, first, page_png(spinner=120))
    expect_screenshot(page, "page.png")
    assert Path(rows(ctx)[-1]["images"]["actual"]).read_bytes() == first


def test_a_passing_check_takes_no_extra_frame(ctx):
    accept(ctx, FakePage(page_png()))
    page = FakePage(page_png())
    expect_screenshot(page, "page.png")
    assert len(page.calls) == 2              # the stability pair, nothing more


def test_the_second_look_can_be_switched_off(ctx):
    from dataclasses import replace

    accept(ctx, FakePage(page_png(spinner=10)))
    ctx.config.capture = replace(ctx.config.capture, retry_on_fail=False)
    page = FakePage(page_png(spinner=250), page_png(spinner=250),
                    page_png(spinner=120))
    with pytest.raises(ScreenshotMismatch):
        expect_screenshot(page, "page.png")
    assert len(page.calls) == 2


def test_a_picture_handed_in_is_never_retaken(ctx):
    accept(ctx, page_png(spinner=10))
    with pytest.raises(ScreenshotMismatch) as e:
        expect_screenshot(page_png(spinner=250), "page.png")
    assert not any("second capture" in n for n in e.value.result.notes)


def test_the_library_goes_through_the_shared_function(ctx, monkeypatch):
    import vistest.core.retry as retry

    seen = []
    real = retry.second_look
    monkeypatch.setattr(retry, "second_look",
                        lambda *a, **k: seen.append(1) or real(*a, **k))
    accept(ctx, FakePage(page_png(spinner=10)))
    expect_screenshot(FakePage(page_png(spinner=250), page_png(spinner=250),
                               page_png(spinner=120)), "page.png")
    assert seen == [1]
