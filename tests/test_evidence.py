# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Накопленная статистика качества.

Цифру из этого отчёта предполагается показывать снаружи, поэтому тесты
проверяют не только арифметику, но и честность: интервал считается всегда,
неразмеченное не прячется, оговорки появляются сами.
"""

from __future__ import annotations

import pytest

from vistest.report.evidence import (
    caveats,
    confidence_note,
    to_html,
    to_markdown,
    wilson,
)


# --------------------------------------------------------------------------- #
#  Доверительный интервал
# --------------------------------------------------------------------------- #
def test_zero_of_eight_is_not_zero_percent():
    """Главная причина, по которой интервал вообще нужен.

    «0 ложных падений из 8» звучит как ноль процентов, но истинное значение
    при такой выборке может быть и тридцать. Обычное нормальное приближение
    здесь схлопывается в точку и врёт.
    """
    low, high = wilson(0, 8)
    assert low == 0.0
    assert high > 0.25, "интервал обязан быть широким на малой выборке"


def test_interval_narrows_as_data_grows():
    _, high_small = wilson(5, 20)
    _, high_big = wilson(50, 200)
    assert high_big < high_small


def test_interval_stays_inside_zero_one():
    for k, n in ((0, 3), (3, 3), (1, 2), (99, 100)):
        low, high = wilson(k, n)
        assert 0.0 <= low <= high <= 1.0


def test_full_rate_does_not_reach_certainty():
    """10 из 10 — не «100% наверняка»: нижняя граница должна быть меньше 1."""
    low, high = wilson(10, 10)
    assert high == 1.0
    assert low < 1.0


def test_empty_sample_gives_widest_interval():
    assert wilson(0, 0) == (0.0, 1.0)


def test_midpoint_is_close_to_the_share_on_large_samples():
    low, high = wilson(250, 1000)
    assert abs((low + high) / 2 - 0.25) < 0.02


# --------------------------------------------------------------------------- #
#  Оценка выборки
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("n,expect", [
    (0, "No data"),
    (5, "small"),
    (50, "small"),
    (200, "internal"),
    (1000, "publication"),
])
def test_confidence_note_scales_with_sample(n, expect):
    assert expect in confidence_note(n)


# --------------------------------------------------------------------------- #
#  Оговорки считаются, а не пишутся руками
# --------------------------------------------------------------------------- #
def test_baseline_caveats_are_always_present():
    """Две оговорки обязательны при любых данных: источник и субъективность."""
    notes = caveats(failed=100, reviewed=100, unreviewed=0, comparisons=1000)
    text = " ".join(notes)
    assert "own installation" in text
    assert "subjective" in text


def test_unreviewed_share_is_disclosed():
    notes = caveats(failed=100, reviewed=40, unreviewed=60, comparisons=500)
    text = " ".join(notes)
    assert "60" in text and "%" in text, "долю неразмеченного надо назвать"


def test_no_failures_warns_about_useless_tests():
    """Ноль падений — это может быть и высокое качество, и мёртвые тесты."""
    notes = caveats(failed=0, reviewed=0, unreviewed=0, comparisons=200)
    assert any("check nothing" in n for n in notes)


def test_small_sample_is_flagged():
    notes = caveats(failed=3, reviewed=3, unreviewed=0, comparisons=10)
    text = " ".join(notes)
    assert "too early" in text or "too few" in text


def test_full_coverage_adds_no_coverage_caveat():
    notes = caveats(failed=50, reviewed=50, unreviewed=0, comparisons=400)
    assert not any("Не размечено" in n for n in notes)


# --------------------------------------------------------------------------- #
#  Отрисовка
# --------------------------------------------------------------------------- #
def _sample(**over):
    data = {
        "generated_at": "2026-08-12T10:00:00",
        "project": "*", "days": 90,
        "period": {"from": "2026-05-14", "to": "2026-08-12"},
        "volume": {"runs": 40, "comparisons": 480, "snapshots": 12,
                   "passed": 430, "failed": 44, "new_baselines": 6},
        "false_fail": {"approved": 4, "rejected": 36, "reviewed": 40,
                       "unreviewed": 4, "rate": 0.1,
                       "ci95": [0.04, 0.23], "coverage": 0.909,
                       "confidence": "Выборка небольшая."},
        "by_month": [{"period": "2026-07", "comparisons": 200, "failed": 20,
                      "approved": 2, "rejected": 18, "reviewed": 20,
                      "rate": 0.1},
                     {"period": "2026-08", "comparisons": 280, "failed": 24,
                      "approved": 2, "rejected": 18, "reviewed": 20,
                      "rate": None}],
        "by_project": [{"project": "shop", "comparisons": 480, "failed": 44,
                        "approved": 4, "rejected": 36, "reviewed": 40,
                        "rate": 0.1, "ci": [0.04, 0.23]}],
        "platforms": [{"platform": "docker-chromium-1x", "browser": "chromium",
                       "runs": 40}],
        "conditions": {"preset": "balanced", "delta_e_threshold": 2.3,
                       "ssim_threshold": 0.9, "require_consensus": True,
                       "fail_severity": 25.0, "max_changed_area_pct": 0.15,
                       "align_enabled": True, "antialias_filter": True,
                       "stability_shots": 3},
        "caveats": ["Это данные собственной установки.", "Разметка субъективна."],
    }
    data.update(over)
    return data


def test_markdown_leads_with_the_number_and_the_interval():
    md = to_markdown(_sample())
    assert "10.0%" in md
    assert "4.0–23.0%" in md, "интервал должен стоять рядом с цифрой"


def test_markdown_states_the_measurement_conditions():
    """Без порогов цифра невоспроизводима, а значит непроверяема."""
    md = to_markdown(_sample())
    for token in ("balanced", "2.3", "0.9", "25.0"):
        assert token in md


def test_markdown_keeps_all_caveats():
    md = to_markdown(_sample())
    for note in _sample()["caveats"]:
        assert note in md


def test_markdown_survives_missing_month_rate():
    """Месяц без разметки не должен ломать таблицу."""
    md = to_markdown(_sample())
    assert "| 2026-08 |" in md and "—" in md


def test_html_is_self_contained():
    page = to_html(_sample())
    assert page.startswith("<!doctype html>")
    for forbidden in ("<script src=", 'href="http', "cdn.", "googleapis"):
        assert forbidden not in page, "внешних запросов быть не должно"


def test_html_shows_the_interval_and_coverage():
    page = to_html(_sample())
    assert "10.0" in page and "4.0" in page and "23.0" in page
    assert "91%" in page, "доля размеченного должна быть видна"


def test_html_escapes_project_names():
    page = to_html(_sample(caveats=['<script>alert(1)</script>']))
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


def test_html_renders_with_empty_history():
    empty = _sample(
        by_month=[], by_project=[], platforms=[],
        volume={"runs": 0, "comparisons": 0, "snapshots": 0, "passed": 0,
                "failed": 0, "new_baselines": 0},
        false_fail={"approved": 0, "rejected": 0, "reviewed": 0,
                    "unreviewed": 0, "rate": 0.0, "ci95": [0.0, 1.0],
                    "coverage": 1.0, "confidence": "No data."})
    page = to_html(empty)
    assert "No data" in page
    assert to_markdown(empty)
