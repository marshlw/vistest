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
"""

from __future__ import annotations

import io
import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core import pngio

__all__ = ["Capture", "capture", "split_masks"]


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


# --------------------------------------------------------------------------- #
def capture(target: Any, *, mask: Sequence[Any] | None = None,
            full_page: bool | None = None,
            timeout_ms: int | None = None) -> Capture:
    """Take the picture. Everything that can be wrong here is said out loud."""
    painted, boxes = split_masks(mask)

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
    options: dict = {"type": "png"}
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

    png = target.screenshot(**options)
    png = bytes(png)
    pngio.dimensions(png, source=f"the screenshot of {_describe(target)}")
    return Capture(
        png=png,
        browser=_browser_of(page) if page is not None else "",
        viewport=_viewport_of(page) if page is not None else "",
        source=_describe(target),
        boxes=tuple(boxes),
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
