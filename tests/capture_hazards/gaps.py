# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""The quiet window's Gap record, on the calibration seeds: how long must it be?

    python -m tests.capture_hazards.gaps            # loading pages, 3 loads per seed

The readiness wait (vistest/capture/ready.py) ends when every step holds at
once and nothing in the area has changed for `quiet_ms`. Too short a window
ends the wait in a pause *inside* the page's loading — after the other steps
already hold, before the page's last re-render. That pause is the noise the
window has to outlast:

* every loading page of the stand is loaded the way a check finds it, with a
  recorder installed before the page's scripts: the library's own probe
  (`PROBE_JS`), called every animation frame, notes each change it sees (DOM
  mutations in the viewport, layout shifts, finished responses) and whether
  the other page-side steps hold (load, fonts, loaders, images); the
  requests come from Playwright's own timing of each one;
* for each load and each path — (1) with the requests counted, (2) without —
  the moment the other steps start holding for good is found, and the longest
  pause between that moment and the changes that still come before the page
  says it is ready (`window.__ready`) is the noise of that load.

The signal side — what the window costs on a page with nothing to wait for —
is measured by `measure.py` on the static rows (pulse and the masked ones).
Calibration seeds only; nothing here reads the held-out ones.
"""

from __future__ import annotations

import argparse
import statistics
import sys

from . import server, stand

LOADING = ("spinner", "skeleton", "lazy_images", "web_font", "late_banner", "scrollbar",
           "scrollbar_visible", "silent_fetch", "after_click")

RECORDER = """(() => {
  const probe = %s;
  const rec = window.__rec = { states: [], changes: [] };
  let last = null, first = true;
  const tick = () => {
    try {
      const r = probe(null, { v: 'gap', reset: first, masks: [], boxes: [], fullPage: false });
      first = false;
      const st = window.__vistestReady;
      if (st && st.last !== last) {
        if (last !== null) rec.changes.push([st.last, st.what]);
        last = st.last;
      }
      const ok = r.readyState === 'complete' && r.fonts === 'loaded'
        && !r.loaders.length && !r.images.length;
      rec.states.push([performance.now(), ok ? 1 : 0]);
    } catch (e) {}
    requestAnimationFrame(tick);
  };
  const go = () => requestAnimationFrame(tick);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', go);
  else go();
})();"""


def _guard_end(times: list[float], holds) -> float | None:
    """The start of the last run of `holds` that reaches the end of `times`."""
    start = None
    for t in times:
        if holds(t):
            if start is None:
                start = t
        else:
            start = None
    return start


def one_load(browser, base: str, h: stand.Hazard, seed: int) -> dict:
    from vistest.capture.inflight import NOT_COUNTED
    from vistest.capture.ready import PROBE_JS

    spans: list[tuple[float, float]] = []
    context = browser.new_context(viewport=stand.VIEWPORT)

    def finished(request):
        try:
            if request.resource_type in NOT_COUNTED:
                return
            t = request.timing
            spans.append((t["startTime"], t["startTime"] + max(t["responseEnd"], 0)))
        except Exception:  # noqa: BLE001 - a request without timing is left out
            pass

    context.on("requestfinished", finished)
    context.add_init_script(RECORDER % PROBE_JS.strip())
    page = context.new_page()
    try:
        page.goto(stand.page_url(base, h, seed))
        if h.step == "click":
            page.click(stand.CLICK_TARGET)
        page.wait_for_function("window.__ready === true", timeout=stand.READY_TIMEOUT_MS)
        page.wait_for_timeout(300)
        rec = page.evaluate("window.__rec")
        origin = page.evaluate("performance.timeOrigin")
        ready = page.evaluate("window.__events.ready")
    finally:
        context.close()
    #  Requests on the page's clock.
    spans = [(a - origin, b - origin) for a, b in spans]
    states = rec["states"]
    sample = [t for t, _ in states]

    def page_ok(t: float) -> bool:
        ok = 0
        for when, v in states:
            if when > t:
                break
            ok = v
        return bool(ok)

    def no_requests(t: float) -> bool:
        return not any(a <= t < b for a, b in spans)

    out = {}
    times = sorted({*sample, *(b for _, b in spans)})
    times = [t for t in times if t <= ready]
    for path, holds in (("1", lambda t: page_ok(t) and no_requests(t)), ("2", page_ok)):
        start = _guard_end(times, holds)
        if start is None:
            out[path] = {"noise": None, "what": "the other steps never held before ready"}
            continue
        later = [c for c, _ in rec["changes"] if start < c <= ready]
        points = [start, *later]
        gaps = [b - a for a, b in zip(points, points[1:], strict=False)]
        noise = max(gaps) if gaps else 0.0
        out[path] = {"noise": noise, "changes": len(later)}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tests.capture_hazards.gaps")
    ap.add_argument("--loads", type=int, default=3, help="loads per page and seed")
    ap.add_argument("--only", help="hazards, comma-separated")
    args = ap.parse_args(argv)
    from playwright.sync_api import sync_playwright

    only = set(args.only.split(",")) if args.only else set(LOADING)
    rows: dict[tuple[str, str], list[float]] = {}
    never: dict[tuple[str, str], int] = {}
    with server.serve() as base, sync_playwright() as pw:
        browsers = {"default": pw.chromium.launch(),
                    "scrollbars": pw.chromium.launch(ignore_default_args=["--hide-scrollbars"])}
        try:
            for h in stand.HAZARDS:
                if h.key not in only:
                    continue
                for seed in stand.CALIBRATION:
                    for _ in range(args.loads):
                        got = one_load(browsers[h.launch], base, h, seed)
                        for path, r in got.items():
                            if r["noise"] is None:
                                never[(h.key, path)] = never.get((h.key, path), 0) + 1
                            else:
                                rows.setdefault((h.key, path), []).append(r["noise"])
        finally:
            for b in browsers.values():
                b.close()
    print(f"Quiet-window noise: the longest pause, after the other steps hold, before "
          f"the page's last change — seeds {stand.CALIBRATION[0]}–{stand.CALIBRATION[-1]}, "
          f"{args.loads} loads each; ms")
    print(f"{'hazard':18s} | {'path 1: max':>11s} {'p95':>6s} {'median':>6s} | "
          f"{'path 2: max':>11s} {'p95':>6s} {'median':>6s}")
    for key in LOADING:
        if key not in only:
            continue
        cells = []
        for path in ("1", "2"):
            xs = sorted(rows.get((key, path), []))
            if not xs:
                cells.append(f"{'—':>11s} {'':>6s} {'':>6s}")
                continue
            p95 = xs[min(len(xs) - 1, int(round(0.95 * (len(xs) - 1))))]
            cells.append(f"{xs[-1]:>11.0f} {p95:>6.0f} {statistics.median(xs):>6.0f}")
        extra = "".join(f"  [path {p}: other steps never held before ready ×{n}]"
                        for (k, p), n in sorted(never.items()) if k == key)
        print(f"{key:18s} | {cells[0]} | {cells[1]}{extra}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
