# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""What the live page can tell about a picture, beyond its pixels.

A failure is easier to act on when it names things on the page: the element
under the pointer that the previous step left there, the field that has the
focus, the counter that changes by itself, the canvas a script redraws, the
kind of browser the baseline was taken in. All of it is read from the page
the check photographed, in the picture's own coordinates, and none of it
changes a verdict — it changes what the message says.

Also here, because it is the same kind of question asked of the same page:
putting an element back where it was in the window when its baseline was
taken (the passport keeps that place), and taking the pointer and the focus
off the page before the picture when the check asks for it.

Playwright is not imported: the page and the locator are used through
`evaluate`, as everywhere in the library.
"""

from __future__ import annotations

from typing import Any

__all__ = ["LAUNCH_HEADS", "facts", "name_points", "place", "prepare", "put_back",
           "regions_of", "scrolled_like"]

#: A short selector for an element: its id, a test id, or a short path of tags
#: and first classes — the shortest of those that names one element.
SELECTOR_FN = r"""
(n) => {
  const esc = (v) => (window.CSS && CSS.escape ? CSS.escape(v) : v);
  const one = (s) => { try { return document.querySelectorAll(s).length === 1; }
                       catch (e) { return false; } };
  if (!n || n.nodeType !== 1) return '';
  if (n.id && one('#' + esc(n.id))) return '#' + esc(n.id);
  for (const at of ['data-testid', 'data-test-id', 'data-test', 'data-qa']) {
    const v = n.getAttribute(at);
    if (v) {
      const s = '[' + at + '="' + v.replace(/"/g, '\\"') + '"]';
      if (one(s)) return s;
    }
  }
  const parts = [];
  for (let x = n, k = 0; x && x.nodeType === 1 && k < 5; x = x.parentElement, k++) {
    if (x.tagName === 'BODY' || x.tagName === 'HTML') break;
    if (x !== n && x.id) { parts.unshift('#' + esc(x.id)); if (one(parts.join(' > '))) break;
                           continue; }
    let p = x.tagName.toLowerCase();
    if (x.classList && x.classList.length) p += '.' + esc(x.classList[0]);
    const same = x.parentElement
      ? Array.from(x.parentElement.children).filter((c) => c.tagName === x.tagName) : [];
    if (same.length > 1) p += ':nth-of-type(' + (same.indexOf(x) + 1) + ')';
    parts.unshift(p);
    if (one(parts.join(' > '))) break;
  }
  return parts.join(' > ');
}
"""

#: The origin of the picture in the window: the element's corner, the page's
#: top left (scrolled away for a full page), or the window's.
_ORIGIN = r"""
  const W = window;
  let o;
  if (el) { const r = el.getBoundingClientRect(); o = [r.left, r.top]; }
  else if (a.fullPage) o = [-W.scrollX, -W.scrollY];
  else o = [0, 0];
"""

#: Where the window and every scrollable ancestor of the element are scrolled.
#: Ancestors are named by their distance from the element, so that the same
#: walk finds them again; a shadow root is stepped over to its host. The
#: document's own scrollers are the window's: not listed twice.
SCROLL_WALK = r"""
  const walk = (el, each) => {
    for (let n = el, d = 0; n && n.nodeType === 1; d++,
         n = n.parentElement || (n.getRootNode && n.getRootNode().host) || null) {
      if (n === document.documentElement || n === document.body) continue;
      each(n, d);
    }
  };
"""

SCROLL_STATE = r"""
  const scrollState = (el) => {
    const list = [];
    walk(el, (n, d) => {
      if (n.scrollHeight > n.clientHeight || n.scrollWidth > n.clientWidth)
        list.push([d, n.scrollLeft, n.scrollTop]);
    });
    return { x: W.scrollX, y: W.scrollY, inner: list };
  };
"""

PREPARE_JS = r"""
(el, a) => {
  const W = window, out = {};
""" + SCROLL_WALK + SCROLL_STATE + r"""
  //  Before anything here moves the page: where the test left it.
  if (el) out.scroll = scrollState(el);
  if (el && a.place) {
    //  Back where it was in the window when the baseline was taken, in whole
    //  pixels: the page is scrolled by the difference.
    const r = el.getBoundingClientRect();
    const dx = Math.round(r.left - a.place.x), dy = Math.round(r.top - a.place.y);
    //  Instantly, whatever the page's `scroll-behavior` says: an animated scroll
    //  would be photographed in its middle, or waited for.
    if (dx || dy) W.scrollTo({ left: Math.round(W.scrollX + dx),
                               top: Math.round(W.scrollY + dy), behavior: 'instant' });
    const s = el.getBoundingClientRect();
    out.placed = [Math.round(s.left), Math.round(s.top)];
  }
  if (a.blur) {
    const n = document.activeElement;
    if (n && n !== document.body && n !== document.documentElement && n.blur) n.blur();
  }
  out.ignored = document.querySelectorAll('[data-vistest="ignore"]').length;
  return out;
}
"""

FACTS_JS = r"""
(el, a) => {
  const W = window;
  const sel = """ + SELECTOR_FN.strip() + r""";
""" + _ORIGIN.replace("  const W = window;\n", "") + SCROLL_WALK + SCROLL_STATE + r"""
  const box = (n) => { const r = n.getBoundingClientRect();
    return [Math.round(r.left - o[0]), Math.round(r.top - o[1]),
            Math.round(r.width), Math.round(r.height)]; };
  const acting = 'a, button, input, select, textarea, label, summary, [role="button"], '
    + '[role="link"], [role="tab"], [role="menuitem"], [tabindex]';
  let hover = Array.from(document.querySelectorAll(':hover')).pop() || null;
  for (let x = hover, k = 0; x && k < 4; x = x.parentElement, k++) {
    if ((x.matches && x.matches(acting)) || x.id) { hover = x; break; }
  }
  if (hover && (hover.tagName === 'HTML' || hover.tagName === 'BODY')) hover = null;
  let focus = document.activeElement;
  if (!focus || focus === document.body || focus === document.documentElement) focus = null;
  const probe = document.createElement('div');
  probe.style.cssText = 'position:absolute;top:-10000px;left:-10000px;width:100px;'
    + 'height:100px;overflow:scroll;visibility:hidden';
  document.documentElement.appendChild(probe);
  const bar = probe.offsetWidth - probe.clientWidth;
  probe.remove();
  const ua = navigator.userAgent || '';
  const out = {
    hover: hover ? { sel: sel(hover), box: box(hover) } : null,
    focus: focus ? { sel: sel(focus), box: box(focus) } : null,
    launch: { headless: /HeadlessChrome/.test(ua) ? true : (/Chrome\//.test(ua) ? false : null),
              scrollbar_px: bar },
  };
  if (el) {
    const r = el.getBoundingClientRect();
    out.place = { x: Math.round(r.left), y: Math.round(r.top),
                  scroll_x: Math.round(W.scrollX), scroll_y: Math.round(W.scrollY) };
    //  Where the picture left the page: the place above is read from here.
    out.scroll = scrollState(el);
  }
  return out;
}
"""

#: The scrolling `a.state` describes, put back. Instantly, whatever the page's
#: `scroll-behavior` says: an animated scroll would still be on its way when
#: the next line of the test runs.
PUT_BACK_JS = r"""
(el, a) => {
  const W = window, s = a.state;
""" + SCROLL_WALK + r"""
  const at = new Map(s.inner.map(([d, left, top]) => [d, [left, top]]));
  walk(el, (n, d) => {
    const v = at.get(d);
    if (v && (n.scrollLeft !== v[0] || n.scrollTop !== v[1]))
      n.scrollTo({ left: v[0], top: v[1], behavior: 'instant' });
  });
  if (W.scrollX !== s.x || W.scrollY !== s.y)
    W.scrollTo({ left: s.x, top: s.y, behavior: 'instant' });
  return true;
}
"""

NAMES_AT_JS = r"""
(el, a) => {
  const sel = """ + SELECTOR_FN.strip() + r""";
""" + _ORIGIN + r"""
  return a.points.map(([x, y]) => {
    const vx = x + o[0], vy = y + o[1];
    if (vx < 0 || vy < 0 || vx >= innerWidth || vy >= innerHeight) return null;
    let n = document.elementFromPoint(vx, vy);
    if (!n || n.tagName === 'HTML' || n.tagName === 'BODY') return null;
    return { sel: sel(n), tag: n.tagName.toLowerCase() };
  });
}
"""

#: How the passport says the kind of browser, for the one line about it.
LAUNCH_HEADS = {True: "headless", False: "with a window", None: "of an unknown kind"}


def _call(owner: Any, element: bool, script: str, arg: dict):
    evaluate = getattr(owner, "evaluate", None)
    if not callable(evaluate):
        return None
    try:
        if element:
            return evaluate(script, arg)
        return evaluate(f"(a) => ({script.strip()})(null, a)", arg)
    except Exception:  # noqa: BLE001 - facts are help; a page that will not say is skipped
        return None


def prepare(owner: Any, element: bool, *, place: dict | None, blur: bool) -> dict:
    """Before the picture: put the element back, take the focus off; count `ignore` marks."""
    got = _call(owner, element, PREPARE_JS, {"place": place, "blur": blur})
    return got if isinstance(got, dict) else {}


def facts(owner: Any, element: bool, full_page: bool) -> dict:
    """After the picture: what is under the pointer, in focus, the launch, the place."""
    got = _call(owner, element, FACTS_JS, {"fullPage": full_page})
    return got if isinstance(got, dict) else {}


def put_back(owner: Any, state: dict | None) -> bool:
    """The element's window and scrolling ancestors back as `state` has them.

    `owner` is a Locator. True when the page did it; False when it could not
    be asked — the caller says so, a page left scrolled should not be a secret.
    """
    if not isinstance(state, dict):
        return False
    return _call(owner, True, PUT_BACK_JS, {"state": state}) is True


def scrolled_like(a: dict | None, b: dict | None) -> bool:
    """Whether two readings of the scrolling are the same (None is no reading)."""
    return isinstance(a, dict) and isinstance(b, dict) and a == b


def name_points(owner: Any, element: bool, full_page: bool,
                points: list[tuple[int, int]]) -> list[dict | None]:
    """The elements at these points of the picture, by selector; None where none."""
    if not points:
        return []
    got = _call(owner, element, NAMES_AT_JS,
                {"fullPage": full_page, "points": [list(p) for p in points]})
    return got if isinstance(got, list) else [None] * len(points)


def regions_of(mask, limit: int = 3) -> list[tuple[int, int, int, int]]:
    """The largest connected parts of a boolean mask, as (x, y, w, h), biggest first."""
    import numpy as np

    try:
        import cv2
    except ImportError:  # pragma: no cover - opencv is a dependency
        ys, xs = np.nonzero(mask)
        if not len(xs):
            return []
        return [(int(xs.min()), int(ys.min()), int(xs.max() - xs.min() + 1),
                 int(ys.max() - ys.min() + 1))]
    n, _, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    parts = sorted((stats[i] for i in range(1, n)), key=lambda s: -int(s[4]))
    return [(int(s[0]), int(s[1]), int(s[2]), int(s[3])) for s in parts[:limit]]


def place(record: dict | None) -> dict | None:
    """The passport's place of an element, checked: {x, y} in whole pixels, or None."""
    if not isinstance(record, dict):
        return None
    try:
        return {"x": int(record["x"]), "y": int(record["y"])}
    except (KeyError, TypeError, ValueError):
        return None
