# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Подключение VisTest к уже написанным тестам — три формы.

Эти тесты пропускаются, если нет selenium/playwright. Смысл файла —
показать минимальный объём изменений в существующем проекте.

Запуск:  python run.py test examples/test_existing_suite.py -v
"""

from __future__ import annotations

import unittest
from pathlib import Path

import pytest

pytest.importorskip("playwright")

DEMO = (Path(__file__).parent / "demo_page.html").resolve().as_uri()


# --------------------------------------------------------------------------- #
#  1. Одна строка в существующем тесте
# --------------------------------------------------------------------------- #
def test_one_liner(page):
    """Ваш тест уже написан — добавляется ровно одна строка.

    `visual_check` сам определит тип драйвера (Playwright Page или Selenium
    WebDriver), стабилизирует страницу, снимет три кадра для детекта динамики
    и сравнит с эталоном.
    """
    from vistest.integrations import visual_check

    page.goto(DEMO)
    visual_check(page, "existing-one-liner.png")


# --------------------------------------------------------------------------- #
#  2. Явная сессия: несколько проверок в один прогон
# --------------------------------------------------------------------------- #
def test_explicit_session(page):
    from vistest.integrations import visual_session

    page.goto(DEMO)
    with visual_session() as vs:
        vs.check(page, "existing-header.png", clip_selector="header")
        vs.check(page, "existing-pricing.png", clip_selector="#pricing")
        # soft=True — собрать расхождение, но не падать здесь
        res = vs.check(page, "existing-footer.png", clip_selector="footer", soft=True)
        assert res.verdict.value in ("pass", "new_baseline"), res.summary()


# --------------------------------------------------------------------------- #
#  3. Готовый снимок, сделанный чужим кодом
# --------------------------------------------------------------------------- #
def test_check_bytes_from_elsewhere(page):
    """Скриншот делает существующий хелпер проекта — VisTest только сравнивает."""
    from vistest.integrations import visual_session

    page.goto(DEMO)
    png_bytes = page.screenshot(full_page=True)     # ваш существующий код

    with visual_session() as vs:
        vs.check_image("existing-from-bytes.png", png_bytes)


# --------------------------------------------------------------------------- #
#  4. unittest через миксин
# --------------------------------------------------------------------------- #
from vistest.integrations import VisualTestCase  # noqa: E402


class LegacyUnittestSuite(VisualTestCase, unittest.TestCase):
    """Проект на unittest + Selenium.

    Всё изменение в существующем классе — добавить `VisualTestCase` в базовые
    и вызывать `self.assert_screenshot(...)`. Драйвер берётся из `self.driver`
    (или `self.page` / `self.browser` / `self.wd`), настройка не нужна.
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
        except Exception as e:  # драйвера нет в окружении — не наша проблема
            raise unittest.SkipTest(f"Chrome WebDriver недоступен: {e}") from e

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
