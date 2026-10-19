# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""The driver: one interface over a Playwright page and a Selenium WebDriver.

`wrap_driver(page_or_webdriver)` and `driver.capture(cfg=...)`. Connecting
VisTest to existing tests in server mode — `visual_check`, `visual_session`,
`VisualTestCase` — lives with the server, in `vistest_server.integrations`.
"""

from __future__ import annotations

from .driver import Driver, PlaywrightDriver, SeleniumDriver, wrap_driver

__all__ = ["wrap_driver", "Driver", "PlaywrightDriver", "SeleniumDriver"]
