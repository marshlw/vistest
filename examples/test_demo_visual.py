# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Пример визуального теста. Требует playwright:  pip install -e ".[browser]".

Запуск:
    python run.py test examples/ -v
Первый прогон создаёт эталоны, второй — сравнивает.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("playwright")

DEMO = (Path(__file__).parent / "demo_page.html").resolve().as_uri()


@pytest.fixture
def demo(page):
    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(DEMO)
    return page


def test_landing_page(demo, visual):
    """Простейший случай: вся страница целиком."""
    visual.assert_screenshot("landing.png")


def test_pricing_card_only(demo, visual):
    """Сравниваем один компонент — быстрее и стабильнее, чем вся страница."""
    visual.assert_screenshot("pricing-card.png", clip_selector="#pricing")


def test_dark_theme(demo, visual):
    demo.click("#theme-toggle")
    visual.assert_screenshot("landing-dark.png")


def test_with_manual_mask(demo, visual):
    """Ручная маска нужна редко: динамику ловит stability-маска автоматически.

    Она полезна для контента, который меняется медленнее интервала между
    кадрами — например, курс валют, подгружаемый раз в минуту.
    """
    visual.assert_screenshot("landing-masked.png", mask_selectors=["#live-counter"])


def test_soft_mode_collects_everything(demo, visual_soft):
    """Все расхождения за тест собираются и падают одним отчётом."""
    visual_soft.assert_screenshot("soft-top.png", clip_selector="header")
    visual_soft.assert_screenshot("soft-pricing.png", clip_selector="#pricing")
    visual_soft.assert_screenshot("soft-footer.png", clip_selector="footer")


@pytest.mark.parametrize("width,label", [(390, "mobile"), (768, "tablet"), (1440, "desktop")])
def test_responsive(page, visual, width, label):
    """Отдельный эталон на каждую ширину — иначе адаптив не проверить."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(DEMO)
    visual.assert_screenshot(f"landing-{label}.png")
