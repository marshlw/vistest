# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""VisTest's server mode in tests that are already written — four ways in.

Needs vistest[server]; the tests are skipped without Playwright (and the
unittest one without Selenium). The point of the file is how little an
existing project has to change.

    python run.py test docs/server/examples/test_existing_suite.py -v
"""

from __future__ import annotations

import unittest
from pathlib import Path

import pytest

pytest.importorskip("playwright")

#  The library's demo page, shared: examples/ is the library's (review v1, R2).
DEMO = (Path(__file__).resolve().parents[3] / "examples" / "demo_page.html").as_uri()


# --------------------------------------------------------------------------- #
#  1. One line in a test that exists
# --------------------------------------------------------------------------- #
def test_one_liner(page):
    """The test is written already — exactly one line is added.

    `visual_check` finds out the driver (a Playwright Page or a Selenium
    WebDriver), steadies the page, takes three frames to find what moves and
    compares with the baseline.
    """
    from vistest.integrations import visual_check

    page.goto(DEMO)
    visual_check(page, "existing-one-liner.png")


# --------------------------------------------------------------------------- #
#  2. An explicit session: several checks in one run
# --------------------------------------------------------------------------- #
def test_explicit_session(page):
    from vistest.integrations import visual_session

    page.goto(DEMO)
    with visual_session() as vs:
        vs.check(page, "existing-header.png", clip_selector="header")
        vs.check(page, "existing-pricing.png", clip_selector="#pricing")
        # soft=True: collect the difference, do not fail here
        res = vs.check(page, "existing-footer.png", clip_selector="footer", soft=True)
        assert res.verdict.value in ("pass", "new_baseline"), res.summary()


# --------------------------------------------------------------------------- #
#  3. A picture someone else's code took
# --------------------------------------------------------------------------- #
def test_check_bytes_from_elsewhere(page):
    """The project's own helper takes the screenshot — VisTest only compares."""
    from vistest.integrations import visual_session

    page.goto(DEMO)
    # Bytes taken elsewhere are not stabilised: mask the counter that ticks every 200 ms.
    png_bytes = page.screenshot(full_page=True,     # your existing code
                                mask=[page.locator("#live-counter")])

    with visual_session() as vs:
        vs.check_image("existing-from-bytes.png", png_bytes)


# --------------------------------------------------------------------------- #
#  4. unittest through a mixin
# --------------------------------------------------------------------------- #
from vistest.integrations import VisualTestCase  # noqa: E402


class LegacyUnittestSuite(VisualTestCase, unittest.TestCase):
    """A unittest + Selenium project.

    The whole change to the class is `VisualTestCase` among its bases and
    `self.assert_screenshot(...)` calls. The driver is `self.driver` (or
    `self.page` / `self.browser` / `self.wd`); nothing to configure.
    """

    @classmethod
    def setUpClass(cls):
        webdriver = pytest.importorskip("selenium.webdriver")
        from selenium.webdriver.chrome.options import Options

        opts = Options()
        opts.add_argument("--headless=new")
        opts.add_argument("--force-color-profile=srgb")
        opts.add_argument("--font-render-hinting=none")
        try:
            cls.driver = webdriver.Chrome(options=opts)
        except Exception as e:  # no driver in this environment
            raise unittest.SkipTest(f"Chrome WebDriver is not available: {e}") from e

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "driver", None):
            cls.driver.quit()

    def test_landing(self):
        self.driver.get(DEMO)
        self.assert_screenshot("legacy-landing.png")

    def test_pricing_component(self):
        self.driver.get(DEMO)
        self.assert_screenshot("legacy-pricing.png", clip_selector="#pricing")
