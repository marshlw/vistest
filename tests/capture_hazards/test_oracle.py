# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""The stand's oracle: `window.__ready` turns true exactly when the hazard is over.

Without the library: a watcher installed before the page's own scripts looks
at the page every animation frame with a condition of its own — written here,
from the DOM, not from the page's code — and notes when that condition first
holds and when `__ready` first does. The condition must hold when `__ready`
turns true, `__ready` must follow it within a few frames (the page waits two,
to be painted), and where the hazard is a request the server holds, the
condition must not hold before that hold is over: the delay is real.

Runs on the calibration seeds only, and only on its own:

    pytest -m capture_hazards tests/capture_hazards
"""

from __future__ import annotations

import pytest

from . import server, stand

pytestmark = pytest.mark.capture_hazards

#: The hazard is over when — per page, from the DOM.
OVER = {
    "spinner": "!document.querySelector('.spinner') "
               "&& document.querySelectorAll('#slot tbody tr').length === 6",
    "skeleton": "!document.querySelector('.sk') "
                "&& document.querySelectorAll('#detail tbody tr').length === 4",
    "lazyimg": "document.querySelectorAll('#top img').length === 4 && "
               "[...document.querySelectorAll('#top img')]"
               ".every((i) => i.complete && i.naturalWidth > 0)",
    "font": "!!document.querySelector('h1.hz') && [...document.fonts].some((f) => "
            "f.family.replace(/[\"']/g, '') === 'Hazard' && f.status === 'loaded')",
    "banner": "!!document.getElementById('banner')",
    "scrollbar": "document.querySelectorAll('#list tbody tr').length === 30",
    "hover": "document.readyState === 'complete' && document.fonts.status === 'loaded'",
    "scrolled": "document.readyState === 'complete' && document.fonts.status === 'loaded'",
    "raf": "document.readyState === 'complete' && document.fonts.status === 'loaded'",
    "counter": "document.readyState === 'complete' && document.fonts.status === 'loaded'",
}

#: The requests the server holds, per page: the hazard cannot be over sooner.
HELD = {
    "spinner": lambda s: stand.delay_ms("data:spinner", s),
    "skeleton": lambda s: stand.delay_ms("data:skeleton", s),
    "lazyimg": lambda s: max(stand.delay_ms(f"img{i}", s) for i in range(4)),
    "font": lambda s: stand.delay_ms("font", s),
    "banner": lambda s: stand.delay_ms("banner", s),
    "scrollbar": lambda s: stand.delay_ms("rows", s),
}

#: `__ready` follows the end of the hazard by two frames; a few more of slack.
WITHIN_MS = 150

WATCH = """(() => {
  const over = () => { try { return !!(%s); } catch (e) { return false; } };
  const o = window.__oracle = { atStart: null, over: null, ready: null, overAtReady: null };
  const tick = () => {
    const t = performance.now(), c = over();
    if (o.atStart === null) o.atStart = c;
    if (c && o.over === null) o.over = t;
    if (window.__ready === true && o.ready === null) { o.ready = t; o.overAtReady = c; return; }
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
})();"""

PAGES = sorted({h.page for h in stand.HAZARDS})


@pytest.fixture(scope="module")
def base():
    with server.serve() as url:
        yield url


@pytest.fixture(scope="module")
def browser():
    sync_api = pytest.importorskip("playwright.sync_api")
    pw = sync_api.sync_playwright().start()
    try:
        b = pw.chromium.launch()
    except Exception as e:  # pragma: no cover - depends on the machine
        pw.stop()
        pytest.skip(f"no Chromium: {str(e).splitlines()[0]}")
    yield b
    b.close()
    pw.stop()


def test_every_page_has_an_oracle():
    assert set(OVER) == set(PAGES)
    assert set(HELD) <= set(PAGES)


@pytest.mark.parametrize("seed", stand.CALIBRATION)
@pytest.mark.parametrize("page", PAGES)
def test_ready_turns_true_exactly_when_the_hazard_is_over(base, browser, page, seed):
    hazard = next(h for h in stand.HAZARDS if h.page == page)
    context = browser.new_context(viewport=stand.VIEWPORT)
    try:
        context.add_init_script(WATCH % OVER[page])
        tab = context.new_page()
        tab.goto(stand.page_url(base, hazard, seed))
        tab.wait_for_function("window.__oracle && window.__oracle.ready !== null",
                              timeout=stand.READY_TIMEOUT_MS)
        o = tab.evaluate("window.__oracle")
        assert o["overAtReady"] is True, o              # over when it says ready
        assert o["over"] is not None and o["over"] <= o["ready"], o
        assert o["ready"] - o["over"] <= WITHIN_MS, o   # and says it at once
        if page in HELD:
            held = HELD[page](seed)
            assert o["atStart"] is False, o             # not over before it began
            assert o["over"] >= held, (o, held)         # the server really held it
    finally:
        context.close()
