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
* frames are taken until two in a row are identical byte for byte, within a
  time limit — five seconds unless the call or `capture.stable_timeout_ms`
  says otherwise. A page that never settles is not an error: the last frame is
  compared, and the result says, in the reason and in the report, that it was
  a frame of a page that would not hold still.
"""

from __future__ import annotations

import io
import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core import pngio

__all__ = ["Capture", "Stability", "capture", "settle_frames", "split_masks"]

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
        evaluate(WAIT_FONTS_JS)
    except Exception as e:
        notes.append(f"waiting for web fonts failed ({type(e).__name__}: {e}); "
                     "the screenshot was taken without it")


def _device_ratio(page) -> float | None:
    evaluate = getattr(page, "evaluate", None) if page is not None else None
    if not callable(evaluate):
        return None
    try:
        ratio = float(evaluate("() => window.devicePixelRatio"))
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
            stable_timeout_ms: int = 5000) -> Capture:
    """Take the picture. Everything that can be wrong here is said out loud."""
    painted, boxes = split_masks(mask)
    check_scale(scale)
    check_timeout(stable_timeout_ms)

    # ---- already a picture ------------------------------------------- #
    if isinstance(target, (bytes, bytearray, memoryview)):
        png = bytes(target)
        pngio.dimensions(png, source="the bytes passed in")
        _refuse_painted(painted, "bytes")
        return Capture(png=png, source="bytes", boxes=tuple(boxes))

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
        return Capture(png=pngio.encode(target), source="array",
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
            "a numpy array or a path to a PNG.")

    page = _page_of(target)
    #  What `toHaveScreenshot()` passes, where it concerns the picture.
    options: dict = {"type": "png", "animations": "disabled", "caret": "hide",
                     "scale": scale}
    if painted:
        options["mask"] = _resolve_painted(painted, page, target)
    if timeout_ms is not None:
        options["timeout"] = timeout_ms

    if _is_page(target):
        if full_page is not None:
            options["full_page"] = bool(full_page)
    elif full_page:
        raise ValueError(
            "expect_screenshot: full_page has no meaning for a Locator — "
            "it already captures exactly that element. Pass the Page instead.")

    notes: list[str] = []
    if page is not None:
        _wait_for_fonts(page, notes)

    def shoot() -> bytes:
        return bytes(target.screenshot(**options))

    png, stability = settle_frames(shoot, timeout_ms=stable_timeout_ms)
    pngio.dimensions(png, source=f"the screenshot of {_describe(target)}")
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
    )


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
