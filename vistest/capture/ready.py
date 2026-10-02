# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Is the page ready to be photographed? One answer for the library and the service.

Frames until two in a row are identical say that a page has stopped *moving*,
not that it has *arrived*. With animations stopped for the screenshot, a
spinner over data that is still on its way is two identical frames in
seventy milliseconds — the capture-hazard stand (tests/capture_hazards)
measured exactly that: the loading state photographed, every time. So before
the frames, the page is asked, step by step:

* **load** — `document.readyState` is `complete`;
* **fonts** — `document.fonts.status` is `loaded`;
* **network** — no request of this page is in flight. Only known when
  somebody counted requests from the moment the context was created
  (`capture.inflight`, hung on every context by the pytest plugin of the
  library and by the service when it opens one); without that, requests
  started before the check cannot be seen from inside it, and this step is
  left out — `path` says which of the two this was;
* **loaders** — nothing in the photographed area says it is loading:
  `aria-busy="true"`, `role="progressbar"`, an indeterminate `<progress>`,
  or an endless animation that is a loader rather than a decoration (the
  rule is `LOADER_RULE` below, chosen on the stand's calibration seeds, and
  the places it can be wrong are written next to it);
* **images** — every image in the area has loaded and been decoded (a lazy
  one outside the viewport is not waited for: it would never come);
* **quiet** — for `quiet_ms` nothing changed in the area: no DOM mutation, no
  layout shift, no network response finished. Mutations inside masked
  elements and in `<head>` do not count.

Each step has its own limit (`limit_ms`). The steps are waited for together
— all must hold at the same moment — and a step that has not held within its
limit is given up: written into the result and into the check's message,
and the wait goes on without it. A page that is never ready is still
photographed; it is not an error. But the second look (core/retry.py) masks
nothing on a page that was not ready, and says why.

The page side is one function (`PROBE_JS`) evaluated again and again; the
first call installs its observers on the page. Playwright and Selenium both
run it: `probe` is whatever evaluates it — a page, a locator (then the area is
that element), the service's driver.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

__all__ = ["CAPTURE_VERSION", "DEFAULT_LIMIT_MS", "DEFAULT_QUIET_MS", "LOADER_RULE",
           "PAGE_PROBE_JS", "PROBE_JS", "Readiness", "SELECTOR_PROBE_JS", "STEPS", "Step",
           "VERSION", "old_way", "wait"]

#: The steps, in the order they are reported.
STEPS = ("load", "fonts", "network", "loaders", "images", "quiet")

#: How long each step may take, unless the config says otherwise.
DEFAULT_LIMIT_MS = 5000

#: How long nothing may change before the page counts as quiet. Chosen on the
#: stand's calibration seeds — the Gap record is in the S2a report.
DEFAULT_QUIET_MS = 40

#: How often the page is asked, at most.
POLL_MS = 25

#: A version tag for the page-side state: a page that a different version of
#: this code looked at gets its observers put up again.
VERSION = 2

#: The way pictures of a live page are taken, as a baseline's passport records
#: it (the library's `capture.version`, the service's `capture_version`).
#: 1 — before S2: frames until two match, nothing more (no record means 1).
#: 2 — the readiness wait (this module) with images painted again once
#: decoded, and in the library `data-vistest="ignore"` painted out and a
#: Locator's element put back where its baseline had it. A change here that
#: moves pixels of baselines already accepted comes with a new number: a
#: baseline taken the old way can differ from a picture taken the new way for
#: no fault of the page, and a failure against it says so (`old_way`).
CAPTURE_VERSION = 2


def old_way(how: str) -> str:
    """The line for a failure against a baseline taken before CAPTURE_VERSION."""
    return ("the baseline was taken the old way (before the readiness wait and the other "
            f"changes of capture version {CAPTURE_VERSION}); if that is the difference, "
            f"accept it again: {how}")

#: Which endless animations are loaders. Written here in words, used in the JS.
LOADER_RULE = """\
An endless animation in the area is a loader when its element (or one of two
ancestors) is named like one — class, id, aria-label or the animation's own
name containing spin, load, skeleton, shimmer, placeholder, progress, busy,
pending —, or when the animation turns the element by a quarter turn or more
(a spinner), or moves a background across it (a shimmer). Anything else
endless — pulsing, glowing, floating — is decoration, and is not waited for.

With the requests counted, the exact signal beats this guess: an animation
that only looks like a loader does not hold the picture once nothing is in
flight and the area is quiet. `aria-busy` and `role=progressbar` — what the
page says itself — always do.

Wrong when: a decoration turns or moves a background (a rotating logo, an
animated gradient) — without the request count it is waited for until the
step's limit, then photographed and said; a loader is neither named nor
turning (three pulsing dots called `dots`, Tailwind's `animate-pulse`
skeleton) — without the request count not seen, photographed early; with the
count, a spinner whose data comes over a WebSocket or an EventSource (not
counted) is not waited for. Each of those is a false failure at worst, never
a hidden change: the second look does not mask what arrived late."""

PROBE_JS = r"""
(el, a) => {
  const W = window, now = performance.now();
  const masks = () => {
    const out = [];
    for (const sel of (a.masks || [])) {
      try { for (const n of document.querySelectorAll(sel)) out.push(n); } catch (e) {}
    }
    for (const n of (a.maskEls || [])) if (n) out.push(n);
    return out;
  };
  const area = () => {
    if (el) { const r = el.getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; }
    if (a.fullPage) {
      const d = document.documentElement;
      return [-W.scrollX, -W.scrollY, Math.max(d.scrollWidth, innerWidth) - W.scrollX,
              Math.max(d.scrollHeight, innerHeight) - W.scrollY];
    }
    return [0, 0, innerWidth, innerHeight];
  };
  const hits = (r, b) => r.width > 0 && r.height > 0
    && r.right > b[0] && r.left < b[2] && r.bottom > b[1] && r.top < b[3];
  /* Boxes are in the picture's pixels: from the area's top left corner. */
  const inBox = (r) => {
    const st = W.__vistestReady, o = (st && st.area) || [0, 0];
    return (a.boxes || []).some((x) => r.left - o[0] >= x[0] && r.top - o[1] >= x[1]
      && r.right - o[0] <= x[0] + x[2] && r.bottom - o[1] <= x[1] + x[3]);
  };

  let s = W.__vistestReady;
  if (!s || s.v !== a.v || a.reset) {
    if (s) { for (const o of [s.mo, s.ls, s.ro]) { try { o && o.disconnect(); } catch (e) {} } }
    s = W.__vistestReady = { v: a.v, last: now, what: '', masks: [] };
    const relevant = (n) => {
      if (!n || n.nodeType !== 1) return false;
      if (n.closest && n.closest('head')) return false;
      for (const m of s.masks) if (m === n || m.contains(n)) return false;
      const r = n.getBoundingClientRect();
      if (!hits(r, s.area)) return false;
      return !inBox(r);
    };
    s.mo = new MutationObserver((recs) => {
      let i = 0;
      for (const r of recs) {
        if (++i > 50) { s.last = performance.now(); s.what = 'the DOM'; return; }
        const n = r.target.nodeType === 1 ? r.target : r.target.parentElement;
        if (relevant(n)) {
          s.last = performance.now();
          s.what = 'the DOM (' + (n.id ? '#' + n.id : n.tagName.toLowerCase()) + ')';
          return;
        }
      }
    });
    s.mo.observe(document.documentElement,
                 { subtree: true, childList: true, attributes: true, characterData: true });
    try {
      s.ls = new PerformanceObserver((l) => {
        for (const e of l.getEntries()) {
          if (!e.hadRecentInput) { s.last = performance.now(); s.what = 'a layout shift'; }
        }
      });
      s.ls.observe({ type: 'layout-shift' });
    } catch (e) {}
    try {
      s.ro = new PerformanceObserver(() => {
        s.last = performance.now(); s.what = 'a response';
      });
      s.ro.observe({ type: 'resource' });
    } catch (e) {}
    s.decoded = new WeakSet();
    s.decoding = new WeakSet();
    s.repainted = new WeakSet();
    s.repainting = new WeakSet();
  }
  s.area = area();
  s.masks = masks();
  const masked = (n) => s.masks.some((m) => m === n || m.contains(n));
  const visible = (n) => {
    const cs = getComputedStyle(n);
    return cs.visibility !== 'hidden' && cs.display !== 'none' && Number(cs.opacity) > 0;
  };
  const name = (n) => (n.id ? '#' + n.id : n.tagName.toLowerCase()
    + (n.classList && n.classList.length
      ? '.' + Array.from(n.classList).slice(0, 2).join('.') : ''));

  /* Loaders: what the page itself says, then endless animations by LOADER_RULE.
     The first kind is the page's own statement; the second is a guess, and
     `said` counts only the first. */
  const loaders = [];
  let said = 0;
  const BUSY = '[aria-busy="true"], [role="progressbar"], progress:not([value])';
  for (const n of document.querySelectorAll(BUSY)) {
    const r = n.getBoundingClientRect();
    if (hits(r, s.area) && visible(n) && !masked(n) && !inBox(r)) {
      loaders.push(name(n) + ' (busy)');
      said++;
    }
  }
  const WORDS = /spin|load|skeleton|shimmer|placeholder|progress|busy|pending/i;
  const named = (n, extra) => {
    let k = 0;
    for (let x = n; x && k < 3; x = x.parentElement, k++) {
      const label = [x.id, typeof x.className === 'string' ? x.className : '',
                     x.getAttribute && x.getAttribute('aria-label')].join(' ');
      if (WORDS.test(label)) return true;
    }
    return WORDS.test(extra || '');
  };
  const turns = (frames) => {
    let lo = Infinity, hi = -Infinity;
    for (const f of frames) {
      const t = String(f.transform || '') + ' ' + String(f.rotate || '');
      const ANGLE = /rotateZ?\(\s*(-?[\d.]+)(deg|turn|rad)\s*\)|^\s*(-?[\d.]+)(deg|turn|rad)/g;
      for (const m of t.matchAll(ANGLE)) {
        const v = Number(m[1] || m[3]), u = m[2] || m[4];
        const deg = u === 'turn' ? v * 360 : u === 'rad' ? v * 57.2958 : v;
        lo = Math.min(lo, deg); hi = Math.max(hi, deg);
      }
    }
    if (lo === Infinity) return false;
    return Math.max(hi, 0) - Math.min(lo, 0) >= 90;
  };
  const sweeps = (frames, pseudo) => frames.some((f) => f.backgroundPosition !== undefined
    || f['background-position'] !== undefined
    || (pseudo && /translate/.test(String(f.transform || ''))));
  if (typeof document.getAnimations === 'function') {
    for (const an of document.getAnimations()) {
      if (an.playState !== 'running' || !an.effect) continue;
      let timing;
      try { timing = an.effect.getComputedTiming(); } catch (e) { continue; }
      if (timing.iterations !== Infinity) continue;
      const n = an.effect.target;
      if (!n || n.nodeType !== 1) continue;
      const r = n.getBoundingClientRect();
      if (!hits(r, s.area) || !visible(n) || masked(n) || inBox(r)) continue;
      let frames = [];
      try { frames = an.effect.getKeyframes(); } catch (e) {}
      const why = named(n, an.animationName) ? 'named'
        : turns(frames) ? 'turning' : sweeps(frames, an.effect.pseudoElement) ? 'sweeping' : '';
      if (why) loaders.push(name(n) + ' (' + why + ')');
    }
  }

  /* Images in the area: loaded, decoded, then painted once more. An image
     painted while it was still being decoded is not always painted the same
     way: its rounded corners came out otherwise on three loads in eight on the
     stand (lazy_images). Painting it again once it is decoded — an invisible
     outline put on and taken off — gave one picture on every load of every
     calibration seed (the S2b report). */
  const images = [];
  const repaint = (img) => {
    s.repainted.add(img);
    s.repainting.add(img);
    const had = img.hasAttribute('style');
    const before = img.style.getPropertyValue('outline');
    const priority = img.style.getPropertyPriority('outline');
    img.style.setProperty('outline', '0px solid transparent', 'important');
    const frames = (n, then) => (n ? requestAnimationFrame(() => frames(n - 1, then)) : then());
    frames(2, () => {
      if (before) img.style.setProperty('outline', before, priority);
      else img.style.removeProperty('outline');
      if (!had && !img.getAttribute('style')) img.removeAttribute('style');
      frames(2, () => s.repainting.delete(img));
    });
  };
  for (const img of document.images) {
    const r = img.getBoundingClientRect();
    if (!hits(r, s.area) || masked(img) || inBox(r)) continue;
    if (img.loading === 'lazy' && !hits(r, [0, 0, innerWidth, innerHeight])) continue;
    if (!img.complete) { images.push(img.currentSrc || img.src); continue; }
    if (img.naturalWidth === 0) continue;
    if (!s.decoded.has(img)) {
      images.push(img.currentSrc || img.src);
      if (!s.decoding.has(img)) {
        s.decoding.add(img);
        const ok = () => s.decoded.add(img);
        try { img.decode().then(ok, ok); } catch (e) { ok(); }
      }
      continue;
    }
    if (!s.repainted.has(img)) repaint(img);
    if (s.repainting.has(img)) images.push(img.currentSrc || img.src);
  }

  return { readyState: document.readyState,
           fonts: document.fonts ? document.fonts.status : 'loaded',
           loaders: loaders.slice(0, 5), said, images: images.slice(0, 5),
           quiet: Math.round(now - s.last), what: s.what };
}
"""


#: The probe for a whole page, as `page.evaluate` takes it.
PAGE_PROBE_JS = "(a) => (" + PROBE_JS.strip() + ")(null, a)"

#: The probe for a driver that names its element by a selector (the service).
SELECTOR_PROBE_JS = ("(a) => (" + PROBE_JS.strip()
                     + ")(a.selector ? document.querySelector(a.selector) : null, a)")


@dataclass
class Step:
    """One step of the wait: did it hold, and when it last started holding."""

    name: str
    ok: bool | None = None          # None — not asked (the network, without a count)
    ms: int = 0                     # since the start of the wait
    limit_ms: int = 0
    detail: str = ""                # what was still pending when it gave up

    def as_dict(self) -> dict:
        out = {"ok": self.ok, "ms": self.ms}
        if self.detail:
            out["detail"] = self.detail
        return out


@dataclass
class Readiness:
    """The answer: was the page ready, and if not — which step gave up, on what.

    `ok` is True when every step that was asked held, False when one gave up,
    and None when the page could not be asked at all (a target that is a
    picture, a driver that runs no script): nothing is claimed then.
    """

    ok: bool | None = None
    ms: int = 0
    quiet_ms: int = 0
    path: str = ""                  # "requests" — counted from the context's start; "page"
    steps: list[Step] = field(default_factory=list)
    note: str = ""                  # why it could not be asked
    #  Animations that looked like loaders and were not waited for, because
    #  nothing was in flight and the area was quiet (path "requests" only).
    guessed: str = ""

    def failed_steps(self) -> list[Step]:
        return [s for s in self.steps if s.ok is False]

    def text(self) -> str:
        """The words for a page that was not ready; empty when it was."""
        bad = self.failed_steps()
        if not bad:
            return ""
        parts = [_WORDS[s.name](s) for s in bad]
        return ("the page was not ready within the limits: " + "; ".join(parts)
                + " — it was photographed as it was")

    def as_dict(self) -> dict:
        out = {"ok": self.ok, "ms": self.ms, "path": self.path,
               "quiet_ms": self.quiet_ms,
               "steps": {s.name: s.as_dict() for s in self.steps if s.ok is not None}}
        if self.note:
            out["note"] = self.note
        if self.guessed:
            out["guessed"] = self.guessed
        return out


_WORDS: dict[str, Callable[[Step], str]] = {
    "load": lambda s: f"still loading after {s.limit_ms} ms (document.readyState: {s.detail})",
    "fonts": lambda s: f"web fonts still loading after {s.limit_ms} ms",
    "network": lambda s: (f"{s.detail} still in flight after {s.limit_ms} ms (a request "
                          "the page never waits for, like a long poll, goes into "
                          "capture.ignore_requests)"),
    "loaders": lambda s: f"still showing a loader after {s.limit_ms} ms: {s.detail}",
    "images": lambda s: f"images in the area not loaded after {s.limit_ms} ms: {s.detail}",
    "quiet": lambda s: f"the area kept changing for {s.limit_ms} ms ({s.detail})",
}


def wait(probe: Callable[[], object], *,
         inflight: Callable[[], tuple[int, list[str]]] | None = None,
         quiet_ms: int = DEFAULT_QUIET_MS,
         limit_ms: int = DEFAULT_LIMIT_MS,
         sleep: Callable[[float], None] = time.sleep,
         clock: Callable[[], float] = time.monotonic) -> Readiness:
    """Wait until every step holds at once, each for at most `limit_ms`.

    probe    — evaluates `PROBE_JS` on the page and returns its dict; anything
               else (a driver that runs no script, an error) ends the wait
               with `ok=None`.
    inflight — (count, a few URLs) of this page's requests in flight, when
               they are counted (`RequestCount`); None when they are not —
               the network step is then not asked.
    sleep    — seconds; a Playwright page's `wait_for_timeout` keeps its
               events flowing while it waits.
    `limit_ms=0` asks nothing: `ok=None`.
    """
    if limit_ms <= 0:
        return Readiness(ok=None, quiet_ms=quiet_ms, note="the wait is off")
    started = clock()
    steps = {n: Step(n, limit_ms=limit_ms) for n in STEPS}
    if inflight is None:
        steps["network"].ok = None
    since: dict[str, int | None] = {n: None for n in STEPS}
    given_up: set[str] = set()
    overridden = ""
    path = "requests" if inflight is not None else "page"

    def elapsed() -> int:
        return int((clock() - started) * 1000)

    while True:
        try:
            got = probe()
        except Exception as e:  # noqa: BLE001 - a page that cannot be asked is said, not raised
            return Readiness(ok=None, ms=elapsed(), quiet_ms=quiet_ms, path=path,
                             note=f"the page could not be asked ({type(e).__name__})")
        if not isinstance(got, dict):
            return Readiness(ok=None, ms=elapsed(), quiet_ms=quiet_ms, path=path,
                             note="the page could not be asked")
        now = elapsed()
        holds = {
            "load": (got.get("readyState") == "complete", got.get("readyState", "")),
            "fonts": (got.get("fonts") == "loaded", ""),
            "loaders": (not got.get("loaders"), ", ".join(got.get("loaders") or [])),
            "images": (not got.get("images"), ", ".join(_short(_no_query(u))
                                                        for u in got.get("images") or [])),
            "quiet": (int(got.get("quiet") or 0) >= quiet_ms,
                      f"last: {got.get('what') or 'a change'}"),
        }
        if inflight is not None:
            count, urls = inflight()
            holds["network"] = (count == 0, _requests(count, urls))
            #  With requests counted, the exact signal beats the guess: an
            #  endless animation that only looks like a loader (named like one,
            #  turning, sweeping) does not hold the picture when nothing is in
            #  flight and the area is quiet — a rotating logo is not loading.
            #  What the page states itself (aria-busy, progressbar) still does.
            if (not holds["loaders"][0] and not got.get("said")
                    and holds["network"][0] and holds["quiet"][0]):
                overridden = holds["loaders"][1]
                holds["loaders"] = (True, "")
        for n, (ok, detail) in holds.items():
            if ok:
                if since[n] is None:
                    since[n] = now
            else:
                since[n] = None
                steps[n].detail = detail
                if now >= limit_ms and n not in given_up:
                    given_up.add(n)
        waiting = [n for n, (ok, _) in holds.items() if not ok and n not in given_up]
        if not waiting:
            break
        if waiting == ["quiet"]:
            #  Only the window is left: ask again when it can have passed, not
            #  every few milliseconds — on a busy page each question costs.
            sleep(max(5, quiet_ms - int(got.get("quiet") or 0) + 1) / 1000)
        else:
            sleep(POLL_MS / 1000)

    #  A step that was given up and held again later, while another one was
    #  still waited for, did arrive: what counts is how it stood at the end.
    total = elapsed()
    for n, (ok, _) in holds.items():
        steps[n].ok = bool(ok)
        steps[n].ms = since[n] if since[n] is not None else total
    return Readiness(ok=all(ok for ok, _ in holds.values()), ms=total, quiet_ms=quiet_ms,
                     path=path, steps=[steps[n] for n in STEPS],
                     guessed=overridden if holds["loaders"][0] else "")


def _short(url: str) -> str:
    url = str(url)
    return url if len(url) <= 80 else url[:77] + "…"


def _no_query(url: str) -> str:
    """A URL without its query string and fragment: tokens live there."""
    return str(url).split("?", 1)[0].split("#", 1)[0]


def _requests(count: int, held: list[str]) -> str:
    """`2 requests (GET /api/poll, POST /track)` — method and path, no query."""
    head = f"{count} request{'s' if count != 1 else ''}"
    return head + (f" ({', '.join(_short(u) for u in held[:3])})" if held else "")
