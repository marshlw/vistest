# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""The server mode's `visual` fixture on the demo page. Needs vistest[server]
and a browser:  pip install -e ".[server,browser]".

    python run.py test docs/server/examples/ -v

The first run creates the baselines (in the server's layout, `.vistest/`),
the second compares. A library project does not need any of this: it calls
`expect_screenshot` — `examples/test_library.py`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("playwright")

#  The library's demo page, shared: examples/ is the library's (review v1, R2).
DEMO = (Path(__file__).resolve().parents[3] / "examples" / "demo_page.html").as_uri()


@pytest.fixture
def demo(page):
    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(DEMO)
    return page


def test_landing_page(demo, visual):
    """The simplest case: the whole page."""
    visual.assert_screenshot("landing.png")


def test_pricing_card_only(demo, visual):
    """One component — faster and steadier than the whole page."""
    visual.assert_screenshot("pricing-card.png", clip_selector="#pricing")


def test_dark_theme(demo, visual):
    demo.click("#theme-toggle")
    visual.assert_screenshot("landing-dark.png")


def test_with_manual_mask(demo, visual):
    """A manual mask is rarely needed: the stability mask catches what moves.

    It helps with content that changes slower than the frames are taken —
    an exchange rate loaded once a minute, for one.
    """
    visual.assert_screenshot("landing-masked.png", mask_selectors=["#live-counter"])


def test_soft_mode_collects_everything(demo, visual_soft):
    """Every difference of the test is collected and fails once, in one report."""
    visual_soft.assert_screenshot("soft-top.png", clip_selector="header")
    visual_soft.assert_screenshot("soft-pricing.png", clip_selector="#pricing")
    visual_soft.assert_screenshot("soft-footer.png", clip_selector="footer")


@pytest.mark.parametrize("width,label", [(390, "mobile"), (768, "tablet"), (1440, "desktop")])
def test_responsive(page, visual, width, label):
    """A baseline per width — there is no other way to check a responsive page."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(DEMO)
    visual.assert_screenshot(f"landing-{label}.png")
