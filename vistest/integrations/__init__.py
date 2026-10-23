# VisTest - visual regression testing for Python and Playwright.
# Copyright 2026 Kirill Kulagin
# SPDX-License-Identifier: Apache-2.0
#
# This file is part of VisTest, licensed under the Apache License, Version 2.0.
# You may not use it except in compliance with the License. See LICENSE for
# the terms and NOTICE for attribution and the trademark.

"""The driver: one interface over a Playwright page and a Selenium WebDriver.

`wrap_driver(page_or_webdriver)` and `driver.capture(cfg=...)`. Connecting
VisTest to existing tests in server mode — `visual_check`, `visual_session`,
`VisualTestCase` — lives with the server, in `vistest_server.integrations`.
"""

from __future__ import annotations

from .driver import Driver, PlaywrightDriver, SeleniumDriver, wrap_driver

__all__ = ["wrap_driver", "Driver", "PlaywrightDriver", "SeleniumDriver"]
