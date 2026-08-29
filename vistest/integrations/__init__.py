# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Подключение VisTest к уже написанным тестам.

Три способа, от самого простого:

1. **Функция** — работает в любом Python-тесте, без фикстур и наследования:

       from vistest.integrations import visual_check
       visual_check(driver, "checkout.png")      # driver: Playwright Page или Selenium

2. **Миксин** — для проектов на unittest:

       class MyTest(VisualTestCase, unittest.TestCase):
           def test_home(self):
               self.driver.get(URL)
               self.assert_screenshot("home.png")

3. **Контекстный менеджер** — когда нужно собрать несколько проверок в один прогон:

       with visual_session() as vs:
           vs.check(driver, "step1.png")
           vs.check(driver, "step2.png")

Для тестов не на Python — HTTP-эндпоинт `POST /api/check`, см. `clients/`.
"""

from __future__ import annotations

from .driver import Driver, PlaywrightDriver, SeleniumDriver, wrap_driver
from .session import (
    VisualSession,
    VisualTestCase,
    visual_check,
    visual_session,
)

__all__ = [
    "visual_check",
    "visual_session",
    "VisualSession",
    "VisualTestCase",
    "wrap_driver",
    "Driver",
    "PlaywrightDriver",
    "SeleniumDriver",
]
