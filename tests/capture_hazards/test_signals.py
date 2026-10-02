# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Every signal variant of the stand fails `expect_screenshot` — with and without the count.

The hole S1 found: the second look masked data that arrived after `load`,
and a real change in it passed. Each page of the stand with a signal variant
— its late data, its text, its picture changed for real — is photographed
here the way a test does it (`goto`, the step, the assertion at once) against
a baseline taken after `window.__ready`, on a calibration seed, both ways the
library sees a page: with the requests of the context counted (what the
pytest plugin does) and without. Every one of them must fail.

Only on its own: `pytest -m capture_hazards tests/capture_hazards`.
"""

from __future__ import annotations

import pytest

from . import measure, server, stand

pytestmark = pytest.mark.capture_hazards

SIGNALLED = [h for h in stand.HAZARDS if h.signal]


@pytest.fixture(scope="module")
def base():
    with server.serve() as url:
        yield url


@pytest.fixture(scope="module")
def browsers():
    sync_api = pytest.importorskip("playwright.sync_api")
    pw = sync_api.sync_playwright().start()
    try:
        out = {"default": pw.chromium.launch(),
               "scrollbars": pw.chromium.launch(ignore_default_args=["--hide-scrollbars"])}
    except Exception as e:  # pragma: no cover - depends on the machine
        pw.stop()
        pytest.skip(f"no Chromium: {str(e).splitlines()[0]}")
    yield out
    for b in out.values():
        b.close()
    pw.stop()


@pytest.mark.parametrize("counted", [True, False], ids=["counted", "page-only"])
@pytest.mark.parametrize("hazard", SIGNALLED, ids=[h.key for h in SIGNALLED])
def test_the_signal_variant_fails(base, browsers, hazard, counted, tmp_path):
    from vistest import expect_screenshot
    from vistest.capture.inflight import track
    from vistest.library import context as _context
    from vistest.library.errors import ScreenshotMismatch

    seed = stand.CALIBRATION[0]
    ctx = _context.LibraryContext(root=tmp_path, config_path=str(tmp_path / "none.yaml"))
    _context.install(ctx)
    browser = browsers[hazard.launch]
    step = measure.step_of(hazard, seed)
    mask = list(hazard.mask) or None
    name = f"{hazard.key}.png"
    try:
        c = browser.new_context(viewport=stand.VIEWPORT)
        if counted:
            track(c)
        p = c.new_page()
        p.goto(stand.page_url(base, hazard, seed))
        if hazard.step_in_baseline:
            measure._do_step(p, step)
        p.wait_for_function("window.__ready === true", timeout=stand.READY_TIMEOUT_MS)
        p.wait_for_timeout(stand.AFTER_READY_MS)
        ctx.update = True
        expect_screenshot(p.locator(hazard.target) if hazard.target else p, name, mask=mask)
        ctx.update = False
        c.close()

        c = browser.new_context(viewport=stand.VIEWPORT)
        if counted:
            track(c)
        p = c.new_page()
        p.goto(stand.page_url(base, hazard, seed, signal=True))
        measure._do_step(p, step)
        with pytest.raises(ScreenshotMismatch):
            expect_screenshot(p.locator(hazard.target) if hazard.target else p, name,
                              mask=mask)
        c.close()
    finally:
        _context.uninstall()
