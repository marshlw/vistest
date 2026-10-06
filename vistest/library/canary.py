# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""The renderer's fingerprint: one small fixed page, drawn the way frames are.

Two screenshots of the same page can differ because the page changed or
because the thing that drew it did — another hinting setting, another
Chromium build, another machine's fonts. From the two screenshots alone the
second cannot be proven; a glyph drawn a pixel to the right looks the same
either way. So it is measured on its own: a page that never changes — a few
lines of text in different fonts and sizes, one of them in a font shipped
with VisTest, one light on dark — drawn in a tab of the same browser context,
through the same capture as the screenshot itself. Two renderers that draw
this page to the same pixels are, for the purpose of a comparison, the same
renderer; two that do not are not, and the number of pixels says by how much.

Only the renderer is in it: the page's own stylesheet never reaches the
canary tab, so a CSS change (`text-rendering`, a fractional `transform`) is
the page's, not the renderer's, and leaves the fingerprint as it was. Nor
does the window: the canary is 360×156 px, and in a tab smaller than that in
either direction one of its lines comes out otherwise — so its tab is never
smaller than `MIN_TAB` (400×400 px), whatever the context's viewport.

**Playwright is never imported here**, as in `targets.py`: a context is
anything with `new_page()`, a page anything with `set_content()`.
"""

from __future__ import annotations

import base64
import hashlib
from functools import lru_cache
from pathlib import Path

from . import js as _js
from .targets import capture

__all__ = ["CANARY_VERSION", "MIN_TAB", "CanaryError", "TabRefused", "draw", "html",
           "page_sha256"]

#: Changes whenever the page below changes. Two fingerprints are comparable
#: only when they were drawn from the same page.
CANARY_VERSION = 1

FONT = Path(__file__).with_name("fonts") / "canary-sans.ttf"
FONT_FAMILY = "VisTest Canary Sans"
ELEMENT_ID = "vistest-canary"

#: (CSS of the line, its text). The shipped font at three sizes, one of them
#: bold and one light on dark; the machine's own serif, sans-serif and
#: monospace, because the fonts a machine resolves are part of how it draws.
LINES: tuple[tuple[str, str], ...] = (
    (f"font: 13px '{FONT_FAMILY}'", "Hamburgefonstiv 0123456789 AVWAY fi"),
    (f"font: 700 21px '{FONT_FAMILY}'", "Rasterised 1.5 px? Wg"),
    ("font: 16px serif", "Hamburgefonstiv 0123456789"),
    ("font: 12px sans-serif", "Hamburgefonstiv 0123456789 illegal1"),
    ("font: 13px monospace", "mono 0O 1lI |[] {}"),
    (f"font: 14px '{FONT_FAMILY}'; color: #f8fafc; background: #0f172a",
     "Light on dark 0123456789"),
    (f"font: italic 15px '{FONT_FAMILY}'; color: #1d4ed8",
     "VisTest renderer canary"),
)


class CanaryError(RuntimeError):
    """The canary could not be drawn the way it has to be."""


class TabRefused(RuntimeError):
    """The context would not open a tab — the context of `browser.new_page()`
    is one: it belongs to that one page. Nothing about the canary yet."""


@lru_cache(maxsize=1)
def html() -> str:
    """The page, whole: the font inlined, nothing loaded from anywhere."""
    font = base64.b64encode(FONT.read_bytes()).decode("ascii")
    rows = "\n".join(f'<div style="{css}; padding: 2px 6px">{text}</div>'
                     for css, text in LINES)
    return (
        "<!doctype html><html><head><meta charset='utf-8'><style>\n"
        f"@font-face {{ font-family: '{FONT_FAMILY}'; "
        f"src: url(data:font/ttf;base64,{font}) format('truetype'); }}\n"
        "html, body { margin: 0; background: #ffffff; color: #111827; }\n"
        f"#{ELEMENT_ID} {{ position: absolute; left: 0; top: 0; width: 360px; "
        "padding: 4px 0; background: #ffffff; line-height: 1.25; "
        "white-space: nowrap; overflow: hidden; }\n"
        "</style></head><body>\n"
        f'<div id="{ELEMENT_ID}">\n{rows}\n</div>\n</body></html>\n')


def page_sha256() -> str:
    """What the fingerprints of this version were drawn from."""
    return hashlib.sha256(html().encode("utf-8")).hexdigest()


_FONT_LOADED = f"() => document.fonts.check(\"13px '{FONT_FAMILY}'\")"

#: The smallest viewport the canary's tab gets, (width, height) in CSS px:
#: larger than the canary itself (360×156), where its pixels stop depending
#: on the window. A larger viewport is kept as it is.
MIN_TAB = (400, 400)


def _tab_size(page) -> dict:
    """The viewport the canary's tab is drawn at: the context's, raised to
    `MIN_TAB` where it is smaller; `MIN_TAB` when the context has none."""
    size = getattr(page, "viewport_size", None)
    width, height = MIN_TAB
    if isinstance(size, dict) and size.get("width") and size.get("height"):
        width, height = max(int(size["width"]), width), max(int(size["height"]), height)
    return {"width": width, "height": height}


def draw(context, *, stable_timeout_ms: int = 5000) -> bytes:
    """The canary, drawn in a new tab of `context`; PNG bytes.

    Through `targets.capture`, as every screenshot of the library is: fonts
    awaited, animations off, caret hidden, CSS pixels, frames until two in
    a row agree. The tab is at least `MIN_TAB`: the same canary in a 320 px
    window as in a 1280 px one. Raises `CanaryError` when the shipped font
    did not load or the tab would not hold still — a fingerprint of that is
    not one — and `TabRefused` when `context` would not open the tab at all.
    """
    new_page = getattr(context, "new_page", None)
    if not callable(new_page):
        raise CanaryError(f"{type(context).__name__} cannot open a tab")
    try:
        page = new_page()
    except Exception as e:
        raise TabRefused(f"{type(e).__name__}: {str(e).splitlines()[0] if str(e) else ''}"
                         ) from e
    try:
        size = _tab_size(page)
        if size != getattr(page, "viewport_size", None):
            page.set_viewport_size(size)
        page.set_content(html(), wait_until="load")
        cap = capture(page.locator(f"#{ELEMENT_ID}"),
                      stable_timeout_ms=stable_timeout_ms)
        if not _js.call(page, _FONT_LOADED, what="checking the canary's font"):
            raise CanaryError(f"the font {FONT_FAMILY!r} did not load")
    finally:
        page.close()
    if cap.notes:
        raise CanaryError("; ".join(cap.notes))
    if cap.stability.stable is False:
        raise CanaryError(cap.stability.unsettled_text())
    return cap.png
