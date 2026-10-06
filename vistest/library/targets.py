# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Whatever the caller handed us -> PNG bytes.

`expect_screenshot(target, ...)` takes a Playwright `Page` or `Locator`, PNG
bytes, a `PIL.Image`, a numpy array or a path. Five kinds of thing, and the
module exists so that the public function does not have to know about any of
them.

**Playwright is never imported here.** Not lazily, not inside a function, not
under `TYPE_CHECKING` in a way that runs. A page is recognised by what it can
do — it has a callable `screenshot`, and a `goto` distinguishes it from a
locator — and that has two consequences worth having: `pip install vistest`
stays free of a browser, and a project that wraps its page object in its own
class works without being taught about.

Masks come in three forms and go two different ways, which is the one thing in
here that is not obvious:

* a **locator** or a **CSS selector** is Playwright's kind of mask. It is
  painted over during capture, before the picture exists, exactly as
  `page.screenshot(mask=[...])` does it. Selectors need a page to be resolved
  against, so passing one with `bytes` is an error and says so.
* a **box** — `(x, y, w, h)` or `{"x": …}` — is ours. It is not painted at
  all: it becomes an ignore mask for the comparison. Painting a rectangle into
  the picture would write our decision into the user's baseline, where it
  cannot be revisited; an ignore mask leaves the pixels alone and can be
  changed the next run.

A live page is photographed the way Playwright's own `toHaveScreenshot()`
photographs it, because that is the assertion this one replaces and most of
the flakiness of screenshot tests is born at capture, not at comparison:

* animations are stopped (`animations="disabled"`), the text caret is hidden
  (`caret="hide"`), and the picture is in CSS pixels (`scale="css"`), so a
  HiDPI laptop and a 1x CI runner take pictures of the same size;
* web fonts are waited for (`document.fonts.ready`) before the first frame;
* the page is asked whether it is ready (capture/ready.py, the same code the
  service runs): loaded, no request of it in flight when the pytest plugin
  counts them, no loader in the photographed area, its images loaded and
  decoded, nothing in the area changing for a moment. Each step has a limit;
  one that gives up is said in the result, and the page is photographed as
  it is;
* frames are taken until two in a row are identical byte for byte, within a
  time limit — five seconds unless the call or `capture.stable_timeout_ms`
  says otherwise. A page that never settles is not an error: the last frame is
  compared, and the result says, in the reason and in the report, that it was
  a frame of a page that would not hold still.
"""

from __future__ import annotations

import inspect
import io
import os
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core import pngio
from . import js as _js
from .errors import CaptureError, hide_for_verdicts

__all__ = ["Capture", "Stability", "capture", "frames_after", "pointer_plan", "settle_frames",
           "split_masks"]

#: `scale=` values, as Playwright spells them. "css" is the default: one
#: picture pixel per CSS pixel whatever the screen, which is what makes a
#: baseline taken on a HiDPI laptop comparable with one taken on a 1x runner.
SCALES = ("css", "device")
DEFAULT_SCALE = "css"

#: The pause before each further frame, in milliseconds — Playwright's own
#: schedule for `toHaveScreenshot()`, then once a second until the time is up.
PAUSES_MS = (0, 100, 250, 500)
LATER_PAUSE_MS = 1000


@dataclass(frozen=True)
class Stability:
    """How many frames it took to get a picture that holds still.

    `stable` is `None` when nobody asked: a target that is already a picture,
    or a call with the time limit set to zero. `elapsed_ms` runs from the start
    of the first frame to the end of the last one taken — the time to stability
    when the page settled, the time spent waiting for it when it did not.
    """

    frames: int = 1
    stable: bool | None = None
    elapsed_ms: int = 0
    timeout_ms: int = 0

    def as_dict(self) -> dict:
        return {"frames": self.frames, "stable": self.stable,
                "elapsed_ms": self.elapsed_ms, "timeout_ms": self.timeout_ms}

    def unsettled_text(self) -> str:
        """The words for a page that did not settle; empty when it did."""
        if self.stable is not False:
            return ""
        return (f"the page did not settle: {self.frames} frames in "
                f"{self.elapsed_ms} ms, no two in a row identical — the last "
                "one was compared")


@dataclass(frozen=True)
class Capture:
    """A picture, plus what we learned about where it came from."""

    png: bytes
    #  Browser and viewport read off a live page, when the target was one.
    browser: str = ""
    viewport: str = ""
    #  How to describe the target in an error message.
    source: str = "image"
    boxes: tuple = field(default_factory=tuple)
    #  Picture pixels per CSS pixel, when the target was a live page: 1.0 for
    #  `scale="css"`, the page's devicePixelRatio for `scale="device"`, `None`
    #  when it could not be read (and the config's value is used instead).
    scale: float | None = None
    scale_mode: str = ""
    stability: Stability = field(default_factory=Stability)
    #  Things that went wrong on the way to the picture without stopping it —
    #  a font wait that raised, say. Said in the result, never swallowed.
    notes: tuple = field(default_factory=tuple)
    #  Takes one more frame the same way — one, without the stability loop —
    #  for a second look at a failure. Only a target that can photograph
    #  itself has one; a picture handed in cannot be taken again.
    retake: Callable[[], bytes] | None = field(default=None, compare=False,
                                               repr=False)
    #  What the wait for readiness found (capture/ready.py); None when the
    #  target was a picture or the wait was off.
    ready: Any = None
    #  What the live page told about the picture (library/page_facts.py):
    #  what was under the pointer and in focus, the kind of launch, where a
    #  Locator's element was in the window. Empty for a picture handed in.
    facts: dict = field(default_factory=dict, compare=False)
    #  The elements that were moving when the frames never held still.
    moving: tuple = ()
    #  Names the elements at points of the picture, on the live page.
    namer: Callable[[list], list] | None = field(default=None, compare=False, repr=False)
    #  calm(since): nothing of the page was in flight, or ended, after `since`
    #  (time.monotonic); None when requests are not counted.
    calm: Callable[[float], bool] | None = field(default=None, compare=False, repr=False)
    #  late(): what was still on its way after the picture was taken — the
    #  page's requests that were in flight or ended since, and pictures that
    #  finished loading since — by name, for the message of a failure on a page
    #  that kept changing. Empty when nothing was; None when it cannot be asked.
    late: Callable[[], list[str]] | None = field(default=None, compare=False, repr=False)
    #  The element was put back where its baseline had it.
    placed: bool = False

    def was_ready(self) -> bool | None:
        """Ready and held still: True; not ready or never still: False; unknown: None."""
        if self.stability.stable is False:
            return False
        if self.ready is None or self.ready.ok is None:
            return None if self.stability.stable is None else True
        return bool(self.ready.ok)


def split_masks(mask: Sequence[Any] | None) -> tuple[list, list]:
    """`mask=[...]` -> (things Playwright paints, boxes we ignore at compare time)."""
    painted: list = []
    boxes: list = []
    for item in mask or ():
        if isinstance(item, str):
            painted.append(item)
        elif isinstance(item, dict) and {"x", "y", "w", "h"} <= set(item):
            boxes.append(item)
        elif isinstance(item, (tuple, list)) and len(item) == 4 \
                and all(isinstance(v, (int, float)) for v in item):
            boxes.append(tuple(int(v) for v in item))
        elif hasattr(item, "bounding_box") or hasattr(item, "element_handle"):
            painted.append(item)          # a Playwright locator
        else:
            raise TypeError(
                f"mask: {item!r} is not a locator, a CSS selector or a box. "
                "A box is (x, y, w, h) or {'x': .., 'y': .., 'w': .., 'h': ..}.")
    return painted, boxes


# --------------------------------------------------------------------------- #
def _is_page(target: Any) -> bool:
    """A page navigates; a locator does not."""
    return hasattr(target, "goto") or hasattr(target, "viewport_size")


def _page_of(target: Any):
    if _is_page(target):
        return target
    return getattr(target, "page", None)


def _describe(target: Any) -> str:
    module = type(target).__module__.split(".")[0]
    return f"{module}.{type(target).__name__}" if module else type(target).__name__


def _viewport_of(page) -> str:
    try:
        size = page.viewport_size
    except Exception:
        return ""
    if not size:
        #  A context created without a viewport follows the window. There is no
        #  size to record, and inventing one would put every run's baselines
        #  under a directory named after whatever the window happened to be.
        return ""
    try:
        return f"{int(size['width'])}x{int(size['height'])}"
    except (KeyError, TypeError, ValueError):
        return ""


def _browser_of(page) -> str:
    try:
        return str(page.context.browser.browser_type.name)
    except Exception:
        return ""


def _wait_for_fonts(page, notes: list) -> None:
    """`document.fonts.ready`, before the first frame. Skipped, not guessed.

    A page wrapper without `evaluate` is photographed as it is. A wait that
    raises is written down: the picture is still taken, and whoever reads a
    red result caused by a font swap gets told that the wait did not happen.
    """
    evaluate = getattr(page, "evaluate", None)
    if not callable(evaluate):
        return
    from ..capture.stabilize import WAIT_FONTS_JS

    try:
        _js.call(page, WAIT_FONTS_JS, what="waiting for web fonts")
    except CaptureError:
        raise
    except _js.SlowAnswer as e:
        notes.append(f"{e}; the screenshot was taken without waiting longer")
    except Exception as e:
        notes.append(f"waiting for web fonts failed ({type(e).__name__}: {e}); "
                     "the screenshot was taken without it")


def _device_ratio(page) -> float | None:
    evaluate = getattr(page, "evaluate", None) if page is not None else None
    if not callable(evaluate):
        return None
    try:
        ratio = float(_js.call(page, "() => window.devicePixelRatio",
                               what="reading the device pixel ratio"))
    except CaptureError:
        raise
    except Exception:
        return None
    return ratio if ratio > 0 else None


def check_scale(scale: str) -> str:
    if scale not in SCALES:
        raise ValueError(f"expect_screenshot: scale={scale!r} is not one of "
                         f"{', '.join(repr(s) for s in SCALES)}")
    return scale


def check_timeout(value: Any, what: str = "stable_timeout_ms") -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"expect_screenshot: {what} must be a whole number of "
                        f"milliseconds, got {value!r}")
    if value < 0:
        raise ValueError(f"expect_screenshot: {what} must be 0 or more, "
                         f"got {value}")
    return value


#: The second look's schedule: no frame at once — the one that failed was
#: just taken — and two identical frames count only once this much time has
#: passed, so that a number ticking every 200 ms is not «confirmed» still by
#: two frames taken within one tick.
LOOK_PAUSES_MS = PAUSES_MS[1:]
LOOK_MIN_MS = 300


def frames_after(shoot: Callable[[], bytes], previous: bytes, *, timeout_ms: int,
                 pauses: tuple[int, ...] = LOOK_PAUSES_MS, min_ms: int = LOOK_MIN_MS,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep,
                 calm: Callable[[float], bool] | None = None,
                 ) -> tuple[list[bytes], bool]:
    """More frames after `previous`, until two in a row are identical or time is up.

    `calm(since)` — when requests are counted — says whether nothing of the
    page was in flight, or ended, after `since` (a `clock()` reading). Two
    identical frames count as settled only if it holds for all the time since
    these frames began: a page waiting for its next answer is two identical
    frames too.

    Returns the frames taken (at least one) and whether the last two —
    `previous` included — were identical with at least `min_ms` behind them:
    the second look (core/retry.py) needs every frame, not only the last, to
    tell a one-off change from a page that keeps changing.
    """
    started = clock()
    frames: list[bytes] = []
    attempt = 0
    while True:
        pause = pauses[attempt] if attempt < len(pauses) else LATER_PAUSE_MS
        attempt += 1
        if frames and (clock() - started) * 1000 + pause >= timeout_ms:
            return frames, False
        if pause:
            sleep(pause / 1000)
        current = shoot()
        frames.append(current)
        if current == previous and (clock() - started) * 1000 >= min_ms \
                and (calm is None or calm(started)):
            return frames, True
        previous = current


def settle_frames(shoot: Callable[[], bytes], *, timeout_ms: int,
                  clock: Callable[[], float] = time.monotonic,
                  sleep: Callable[[float], None] = time.sleep,
                  ) -> tuple[bytes, Stability]:
    """Frames until two in a row are identical, or until the time is up.

    Byte equality, as the task of this loop is narrow: not «close enough» —
    the comparison decides that later, against the baseline — but «the page
    has stopped changing». Two identical PNGs of one page are two identical
    renders; the encoder is deterministic.

    At least two frames are always taken when the limit is above zero: one
    frame proves nothing about stability, and a slow first frame must not be
    reported as a page that «did not settle» after one look. The limit bounds
    the frames after that. Zero turns the loop off — one frame, and
    `stable=None` because nobody checked.
    """
    started = clock()

    def elapsed() -> int:
        return int((clock() - started) * 1000)

    previous = shoot()
    if timeout_ms <= 0:
        return previous, Stability(frames=1, stable=None,
                                   elapsed_ms=elapsed(), timeout_ms=0)
    frames = 1
    attempt = 0
    while True:
        pause = PAUSES_MS[attempt] if attempt < len(PAUSES_MS) else LATER_PAUSE_MS
        attempt += 1
        if frames >= 2 and elapsed() + pause >= timeout_ms:
            return previous, Stability(frames=frames, stable=False,
                                       elapsed_ms=elapsed(), timeout_ms=timeout_ms)
        if pause:
            sleep(pause / 1000)
        current = shoot()
        frames += 1
        if current == previous:
            return current, Stability(frames=frames, stable=True,
                                      elapsed_ms=elapsed(), timeout_ms=timeout_ms)
        previous = current


# --------------------------------------------------------------------------- #
def capture(target: Any, *, mask: Sequence[Any] | None = None,
            full_page: bool | None = None,
            timeout_ms: int | None = None,
            scale: str = DEFAULT_SCALE,
            stable_timeout_ms: int = 5000,
            ready_timeout_ms: int = 0,
            quiet_ms: int | None = None,
            ignore_requests: Sequence[str] = (),
            pointer_away: bool = True,
            blur_focus: bool = False,
            place: dict | None = None,
            window: dict | None = None) -> Capture:
    """Take the picture. Everything that can be wrong here is said out loud.

    `ready_timeout_ms` — each step of the wait for readiness at most this
    long (capture/ready.py); 0, the default here, does not wait: the caller
    that wants it — `expect_screenshot` — passes the config's value.
    `pointer_away` — the pointer off the page, so that no element is under it
    (the default); `blur_focus` — the focus off whatever has it. Both before
    anything else. `place` — where a Locator's element was in
    the window when its baseline was taken (`{"x", "y"}`, the passport's): the
    page is scrolled to put it there again. `window` — the same for a picture
    of the window (not full_page): the scroll its baseline had, `{"x", "y"}`;
    the window is set to it instantly before the picture and put back after,
    and one note says where it was and where it was set.
    """
    painted, boxes = split_masks(mask)
    check_scale(scale)
    check_timeout(stable_timeout_ms)
    check_timeout(ready_timeout_ms, "ready_timeout_ms")

    # ---- already a picture ------------------------------------------- #
    if isinstance(target, (bytes, bytearray, memoryview)):
        png = bytes(target)
        pngio.dimensions(png, source="the bytes passed in")
        _refuse_painted(painted, "bytes")
        return Capture(png=png, source="bytes", boxes=tuple(boxes))

    if isinstance(target, str) and _URL.match(target):
        raise TypeError(
            f"expect_screenshot: {target!r} is an address, and the check takes a page: "
            "open it first (page.goto(url)) and pass the page")

    if isinstance(target, (str, os.PathLike)):
        path = Path(target)
        try:
            png = path.read_bytes()
        except OSError as e:
            raise FileNotFoundError(f"cannot read the screenshot {path}: {e}") \
                from None
        pngio.dimensions(png, source=path)
        _refuse_painted(painted, f"the file {path}")
        return Capture(png=png, source=str(path), boxes=tuple(boxes))

    #  numpy array — what somebody calling the engine directly already has.
    if hasattr(target, "shape") and hasattr(target, "dtype"):
        _refuse_painted(painted, "an array")
        return Capture(png=pngio.encode(_rgb_array(target)), source="array",
                       boxes=tuple(boxes))

    #  PIL image: has a save() that takes a format, a mode and a size.
    if hasattr(target, "save") and hasattr(target, "mode") \
            and hasattr(target, "size"):
        buffer = io.BytesIO()
        target.convert("RGB").save(buffer, format="PNG")
        _refuse_painted(painted, "a PIL image")
        return Capture(png=buffer.getvalue(), source="PIL.Image",
                       boxes=tuple(boxes))

    # ---- something that can take its own screenshot -------------------- #
    if not callable(getattr(target, "screenshot", None)):
        raise TypeError(
            f"expect_screenshot: cannot take a screenshot of {_describe(target)}. "
            "Pass a Playwright Page or Locator, PNG bytes, a PIL image, "
            "a numpy array or a path to a PNG." + _selenium_hint(target))
    _refuse_async(target)

    page = _page_of(target)
    from . import page_facts as _facts

    _refuse_closed(page)
    element = not _is_page(target)
    owner = target if element else page
    notes: list[str] = []
    if page is not None:
        _wait_for_fonts(page, notes)
        _js.check()
    if pointer_away and page is not None:
        _pointer_away(page)
    _count = _inflight_for(page)
    calm = ((lambda since: _count.calm_since(since, page, tuple(ignore_requests)))
            if _count is not None else None)
    window_shot = (not element and page is not None
                   and not (full_page if full_page is not None else False))
    set_window = window if window_shot else None
    prepared = _facts.prepare(owner, element, place=place if element else None,
                              blur=blur_focus) if page is not None else {}
    #  Where the test left the window and the scrollable boxes around an
    #  element, before the placing below and Playwright's own scrolling into
    #  view move them. The picture needs the element where the baseline had it;
    #  the test needs the page where it was.
    before = prepared.get("scroll") if (element or window_shot) else None
    at_shot: dict | None = None

    def moved() -> bool:
        return before is not None and not _facts.scrolled_like(before, at_shot)

    def put_back() -> None:
        if not moved():
            return
        if not _facts.put_back(owner, before, element):
            notes.append("the page could not be scrolled back to where the test "
                         "left it")

    if prepared.get("ignored"):
        #  `data-vistest="ignore"` — the page's own way to say «not this»,
        #  the same mark the service honours: painted, like a selector mask.
        painted = [*painted, '[data-vistest="ignore"]']
    #  What `toHaveScreenshot()` passes, where it concerns the picture.
    options: dict = {"type": "png", "animations": "disabled", "caret": "hide",
                     "scale": scale}
    if painted:
        options["mask"] = _resolve_painted(painted, page, target)
    if timeout_ms is None and _js.current() is not None and _js._playwright(target):
        #  Within the check's deadline (library/js.py): Playwright's own default
        #  is thirty seconds per frame, whatever the check was given.
        timeout_ms = _js.screenshot_timeout()
    if timeout_ms is not None:
        options["timeout"] = timeout_ms

    if _is_page(target):
        if full_page is not None:
            options["full_page"] = bool(full_page)
    elif full_page:
        raise ValueError(
            "expect_screenshot: full_page has no meaning for a Locator — "
            "it already captures exactly that element. Pass the Page instead.")

    try:
        readiness = None
        if page is not None and ready_timeout_ms > 0:
            readiness = _wait_ready(target, page, painted, boxes,
                                    bool(options.get("full_page")), ready_timeout_ms,
                                    quiet_ms, tuple(ignore_requests))
        if set_window is not None:
            #  Once the page has arrived: set the window to where the baseline's
            #  was, and look again — what is in the new view may still be on its way.
            got = _facts.set_window(owner, set_window)
            if got is not None and before is not None:
                note = _window_note(before, set_window, got)
                if note:
                    notes.append(note)
            if readiness is not None and got is not None and (
                    tuple(got.get("at") or ()) != (int(before.get("x") or 0),
                                                   int(before.get("y") or 0))
                    if before is not None else True):
                readiness = _wait_ready(target, page, painted, boxes,
                                        bool(options.get("full_page")), ready_timeout_ms,
                                        quiet_ms, tuple(ignore_requests))

        _js.check()
        last_two: list[bytes] = []
        taken_at = time.monotonic()
        mark = _page_clock(page) if page is not None else None

        def shoot() -> bytes:
            _js.check()
            if "timeout" in options and _js.current() is not None:
                options["timeout"] = _js.screenshot_timeout()
            try:
                taken = target.screenshot(**options)
            except TypeError as error:
                if _js._playwright(target):
                    raise
                raise _not_playwright(target, error) from None
            except Exception as error:  # noqa: BLE001 - a dead page is said, not hung on
                _js.diagnose(owner if page is not None else target, error,
                             "taking the screenshot", options.get("timeout"))
                raise
            if inspect.isawaitable(taken):
                getattr(taken, "close", lambda: None)()
                raise TypeError(_ASYNC)
            got = bytes(taken)
            last_two[:] = [*last_two[-1:], got]
            return got

        png, stability = settle_frames(shoot, timeout_ms=stable_timeout_ms)
        pngio.dimensions(png, source=f"the screenshot of {_describe(target)}")
        full = bool(options.get("full_page"))
        seen = _facts.facts(owner, element, full) if page is not None else {}
        at_shot = seen.pop("scroll", None)
        if window_shot and isinstance(at_shot, dict):
            seen["window"] = {"x": int(at_shot.get("x") or 0), "y": int(at_shot.get("y") or 0)}

        def namer(points):
            #  The names are read at points of the picture, which is where the
            #  page was scrolled when it was taken: put it there, ask, put it back.
            if not moved() or at_shot is None:
                return _facts.name_points(owner, element, full, points)
            _facts.put_back(owner, at_shot, element)
            try:
                return _facts.name_points(owner, element, full, points)
            finally:
                put_back()

        def retake() -> bytes:
            #  One more frame of the same picture: from the place the first
            #  ones were taken at, and the page back as the test left it after.
            if not moved() or at_shot is None:
                return shoot()
            _facts.put_back(owner, at_shot, element)
            try:
                return shoot()
            finally:
                put_back()
    finally:
        #  Whatever happened to the picture: the page back as it was found.
        put_back()

    def late() -> list[str]:
        names: list[str] = []
        if _count is not None:
            names += _count.touched_since(taken_at, page, tuple(ignore_requests))
        names += _pictures_since(page, mark)
        return list(dict.fromkeys(names))

    moving: list = []
    if stability.stable is False and len(last_two) == 2:
        #  Frames that never held still: which elements were moving?
        from ..core import noise as _noise

        try:
            a, b = (pngio.decode(x, source="a frame") for x in last_two)
            if a.shape == b.shape:
                moving = _named(namer, _noise.stability_mask([a, b], dilate_px=1))
        except Exception:  # noqa: BLE001 - naming is help, not the verdict
            moving = []
    return Capture(
        png=png,
        browser=_browser_of(page) if page is not None else "",
        viewport=_viewport_of(page) if page is not None else "",
        source=_describe(target),
        boxes=tuple(boxes),
        scale=1.0 if scale == "css" else _device_ratio(page),
        scale_mode=scale,
        stability=stability,
        notes=tuple(notes),
        retake=retake,
        ready=readiness,
        facts=seen,
        moving=tuple(moving),
        namer=namer if page is not None else None,
        calm=calm,
        late=late,
        placed=bool(prepared.get("placed")),
    )


def is_page_like(target: Any) -> bool:
    return _is_page(target)


def is_live(target: Any) -> bool:
    """Something that photographs itself — a page, a locator — not a picture."""
    if isinstance(target, (bytes, bytearray, memoryview, str, os.PathLike)):
        return False
    if hasattr(target, "shape") and hasattr(target, "dtype"):
        return False
    return callable(getattr(target, "screenshot", None))


def describe(target: Any, scale: str = DEFAULT_SCALE) -> Capture:
    """What `capture` would say about the target's platform, without a picture."""
    page = _page_of(target)
    return Capture(png=b"", browser=_browser_of(page) if page is not None else "",
                   viewport=_viewport_of(page) if page is not None else "",
                   source=_describe(target),
                   scale=1.0 if scale == "css" else _device_ratio(page), scale_mode=scale)


def name_parts(namer, mask) -> list[dict]:
    """The elements under the largest parts of a mask, named on the live page."""
    return _named(namer, mask)


def _window_note(before: dict, wanted: dict, got: dict | None) -> str:
    """One line when the window was set to its baseline's scroll: where it was, where to."""
    was = (int(before.get("x") or 0), int(before.get("y") or 0))
    want = (wanted["x"], wanted["y"])
    at = tuple(got["at"]) if isinstance(got, dict) and got.get("at") else want
    if was == want == at:
        return ""
    if at != want:
        top = tuple(got.get("max") or ())
        return (f"the window was at scroll {was} and could not be set to {want} where "
                f"the baseline was taken: the page scrolls to {top} at most, so it was "
                f"photographed at {at} — the difference stays")
    return (f"the window was at scroll {was}; set to {want}, where the baseline was "
            "taken, for the picture and put back after "
            "(capture.restore_scroll: false turns this off)")


_PAGE_CLOCK_JS = "() => performance.now()"
_PICTURES_JS = r"""
(a) => {
  const out = [];
  for (const e of performance.getEntriesByType('resource')) {
    if (e.initiatorType === 'img' && e.responseEnd > a.t) out.push(e.name);
  }
  for (const i of document.images) {
    if (!i.complete && i.loading !== 'lazy') out.push(i.currentSrc || i.src);
  }
  return out;
}
"""


def _page_clock(page) -> float | None:
    """The page's own clock now (performance.now), to ask later what finished after it."""
    try:
        got = _js.call(page, _PAGE_CLOCK_JS, what="reading the page's clock")
        return float(got)
    except CaptureError:
        raise
    except Exception:  # noqa: BLE001 - naming is help, not the verdict
        return None


def _pictures_since(page, mark: float | None) -> list[str]:
    """Pictures that finished loading after `mark`, or are still loading: `GET /img/a.png`."""
    if page is None or mark is None:
        return []
    try:
        got = _js.call(page, _PICTURES_JS, {"t": mark},
                       what="asking which pictures were still loading")
    except Exception:  # noqa: BLE001 - naming is help, not the verdict
        return []
    out = []
    for url in got or ():
        bare = str(url).split("?", 1)[0].split("#", 1)[0]
        out.append(f"image {bare}")
    return out


def _inflight_for(page):
    if page is None:
        return None
    from ..capture import inflight as _inflight

    return _inflight.for_page(page)


def pointer_plan(cfg, reset_hover_focus: bool | None, keep_pointer: bool | None,
                 blur_focus: bool | None) -> tuple[bool, bool]:
    """(pointer away, focus off) for one check.

    By default: the pointer away, the focus left. `reset_hover_focus=True` (or
    `capture.reset_hover_focus`) is both. `keep_pointer` and `blur_focus` —
    the call's, else the config's — each decide their own half, and win.
    `reset_hover_focus=False` is what it always was: the pointer and the focus
    left exactly where the test put them.
    """
    reset = getattr(cfg, "reset_hover_focus", False) if reset_hover_focus is None \
        else bool(reset_hover_focus)
    if reset_hover_focus is False:
        away, blur = False, False
    elif reset:
        away, blur = True, True
    else:
        away = not getattr(cfg, "keep_pointer", False)
        blur = bool(getattr(cfg, "blur_focus", False))
    if keep_pointer is not None:
        away = not keep_pointer
    if blur_focus is not None:
        blur = bool(blur_focus)
    return away, blur


_URL = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)

_ASYNC = ("expect_screenshot: this is Playwright's async API, and the check drives "
          "the sync one (playwright.sync_api — pytest-playwright's `page` is one). "
          "With an async page, take the picture yourself and pass the bytes: "
          "expect_screenshot(await page.screenshot(), name) — without the waiting "
          "and the frames a page gets")


def _refuse_async(target: Any) -> None:
    """An async Page or Locator, refused by name before anything is asked of it.

    Its methods return coroutines: the check used to fail on the first of them
    with «cannot convert 'coroutine' object to bytes» and a RuntimeWarning.
    """
    if (type(target).__module__.startswith("playwright.async_api")
            or inspect.iscoroutinefunction(getattr(target, "screenshot", None))):
        raise TypeError(_ASYNC)


def _selenium_hint(target: Any) -> str:
    if not type(target).__module__.startswith("selenium"):
        return ""
    return (" For Selenium, pass the PNG it takes: driver.get_screenshot_as_png(), "
            "or element.screenshot_as_png for one element.")


def _not_playwright(target: Any, error: TypeError) -> TypeError:
    """`target.screenshot` refused Playwright's options: say what to pass instead."""
    hint = _selenium_hint(target) or (
        " Pass the PNG bytes it takes instead: expect_screenshot(<its PNG bytes>, name).")
    return TypeError(
        f"expect_screenshot: {_describe(target)}.screenshot() does not take Playwright's "
        f"options (type, animations, caret, scale, …): {error}.{hint}")


def _rgb_array(array: Any):
    """A numpy array as the H×W×3 uint8 RGB picture it has to be, or a refusal that
    says how to make it one. A float array used to be rounded to 0 and 1 in
    silence — a black baseline."""
    import numpy as np

    a = np.asarray(array)
    if a.ndim == 2:
        raise TypeError(f"expect_screenshot: a grey array {a.shape}; the picture is "
                        "H×W×3 RGB — np.stack([a] * 3, axis=-1)")
    if a.ndim != 3 or a.shape[2] not in (3, 4):
        raise TypeError(f"expect_screenshot: an array of shape {a.shape}; the picture "
                        "is H×W×3 RGB")
    if a.shape[2] == 4:
        raise TypeError(f"expect_screenshot: an RGBA array {a.shape}; the picture is "
                        "RGB — drop the alpha: a[..., :3]")
    if a.dtype == np.uint8:
        return a
    if a.dtype == np.bool_ or a.dtype.kind not in "iuf":
        raise TypeError(f"expect_screenshot: an array of {a.dtype}; the picture is "
                        "uint8, 0–255")
    lo, hi = (float(a.min()), float(a.max())) if a.size else (0.0, 0.0)
    if a.dtype.kind == "f":
        scale = ("(a * 255).round().astype(np.uint8)" if hi <= 1.0
                 else "a.round().astype(np.uint8)")
        raise TypeError(f"expect_screenshot: a float array, values {lo:g}…{hi:g}; the "
                        f"picture is uint8, 0–255 — {scale}")
    if lo < 0 or hi > 255:
        raise TypeError(f"expect_screenshot: an integer array with values {lo:g}…{hi:g}; "
                        "the picture is 0–255")
    return a.astype(np.uint8)


def _refuse_closed(page: Any) -> None:
    """A closed page is said at once, by name: there is nothing to photograph."""
    closed = getattr(page, "is_closed", None) if page is not None else None
    if not callable(closed):
        return
    try:
        is_closed = bool(closed())
    except Exception:  # noqa: BLE001 - a wrapper that cannot say is not closed
        return
    if is_closed:
        raise CaptureError("the page is closed: it was closed before the check "
                           "(by the test, or by a fixture's teardown)")


def _pointer_away(page: Any) -> None:
    """The pointer off the page: at (-1, -1) nothing is under it, nothing hovered."""
    mouse = getattr(page, "mouse", None)
    move = getattr(mouse, "move", None)
    if callable(move):
        try:
            move(-1, -1)
        except Exception:  # noqa: BLE001 - a page that has no pointer to move
            pass


def _named(namer, mask) -> list[dict]:
    """The elements under the largest parts of a mask: [{sel, tag, box}]."""
    from .page_facts import regions_of

    parts = regions_of(mask)
    names = namer([(x + w // 2, y + h // 2) for x, y, w, h in parts])
    out = []
    for (x, y, w, h), got in zip(parts, names, strict=False):
        if isinstance(got, dict) and got.get("sel"):
            out.append({"sel": got["sel"], "tag": got.get("tag", ""), "box": [x, y, w, h]})
    return out


def _wait_ready(target: Any, page: Any, painted: list, boxes: list, full_page: bool,
                limit_ms: int, quiet_ms: int | None, ignore: tuple[str, ...] = ()):
    """capture/ready.py on this target: the page, or a locator's element."""
    from ..capture import inflight as _inflight
    from ..capture import ready as _ready

    owner = page if _is_page(target) else target
    evaluate = getattr(owner, "evaluate", None)
    if not callable(evaluate):
        return _ready.Readiness(ok=None, note="the page could not be asked")
    elements = []
    for item in painted:
        if isinstance(item, str):
            continue
        try:
            elements.extend(item.element_handles())
        except Exception:  # noqa: BLE001 - a mask we cannot resolve is not waited around
            pass
    arg = {"v": _ready.VERSION, "reset": True, "fullPage": full_page,
           "masks": [m for m in painted if isinstance(m, str)], "maskEls": elements,
           "boxes": [list(_box(b)) for b in boxes]}

    def probe():
        try:
            if owner is page:
                return _js.call(owner, _ready.PAGE_PROBE_JS, arg,
                                what="asking whether the page is ready")
            if _js._is_locator(owner):
                return _js.call(owner, _ready.PROBE_JS, arg,
                                what="asking whether the element is ready")
            return evaluate(_ready.PROBE_JS, arg, timeout=limit_ms)
        finally:
            arg["reset"] = False

    pause = getattr(page, "wait_for_timeout", None)
    sleep = (lambda s: pause(s * 1000)) if callable(pause) else time.sleep
    count = _inflight.for_page(page)
    held = (lambda: count.snapshot(page, ignore)) if count else None
    return _ready.wait(probe, inflight=held,
                       quiet_ms=_ready.DEFAULT_QUIET_MS if quiet_ms is None else quiet_ms,
                       limit_ms=limit_ms, sleep=sleep)


def _box(b) -> tuple[int, int, int, int]:
    if isinstance(b, dict):
        return int(b["x"]), int(b["y"]), int(b["w"]), int(b["h"])
    return tuple(int(v) for v in b)


def _refuse_painted(painted: list, what: str) -> None:
    if not painted:
        return
    raise ValueError(
        f"expect_screenshot: a mask given as a locator or a CSS selector needs "
        f"a live page to resolve against, and the target is {what}. "
        "Use a box — (x, y, w, h) — or pass the Page.")


def _resolve_painted(painted: list, page, target) -> list:
    out = []
    for item in painted:
        if not isinstance(item, str):
            out.append(item)
            continue
        owner = page if page is not None else target
        locator = getattr(owner, "locator", None)
        if not callable(locator):
            raise ValueError(
                f"expect_screenshot: cannot resolve the CSS selector {item!r} — "
                f"{_describe(target)} has no .locator(). Pass a Playwright "
                "Page or Locator, or use a box.")
        out.append(locator(item))
    return out


#  pytest: the library's own verdicts and refusals are shown without its
#  frames (library/errors.py, hide_for_verdicts).
__tracebackhide__ = hide_for_verdicts
