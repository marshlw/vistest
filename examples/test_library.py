# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""VisTest as a library: one function, the baselines in your repository.

    pip install "vistest[browser]"
    playwright install chromium

    pytest examples/test_library.py                   # red: there are no baselines yet
    pytest examples/test_library.py --vistest-update  # writes them: look, then commit
    pytest examples/test_library.py                   # green
    # change the page, and the next run is red and says what changed

The baselines go to `tests/__vistest__/` under the project's root (or
`__vistest__/` when it has no `tests/` folder), one directory per platform —
the system, the browser and the window size, because text is drawn differently
on each. The pictures of the last run and the report go to `.vistest/`, which
belongs in `.gitignore`.

`scripts/check_install.py` runs this file in a fresh environment and checks all
four outcomes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("playwright")

from vistest import expect_screenshot  # noqa: E402

DEMO = (Path(__file__).parent / "demo_page.html").resolve().as_uri()

#  The line with a counter that ticks every 200 ms and a spinner that never
#  stops: masked, because a moving number is not a regression. A selector is
#  painted over in the picture, the way Playwright's own `mask=` does it.
LIVE = ["p.lead"]


@pytest.fixture
def demo(page):
    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(DEMO)
    return page


def test_landing_page(demo):
    """The whole window."""
    expect_screenshot(demo, "landing.png", mask=LIVE)


def test_pricing_cards(demo):
    """One element: a Locator is photographed alone, smaller and steadier."""
    expect_screenshot(demo.locator("#pricing"), "pricing.png")


def test_dark_theme(demo):
    """A state the test puts the page in first."""
    demo.click("#theme-toggle")
    expect_screenshot(demo, "landing-dark.png", mask=LIVE)
