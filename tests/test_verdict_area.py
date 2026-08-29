# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Политика вердикта по суммарной площади.

Найдено на живых данных. Два снимка упали при `severity 0.0` — движок не нашёл
ни одного значимого региона, но суммарной площади набралось на порог. Так
выглядит диффузная субпиксельная разница, размазанная по экрану: подпись
другого конвейера захвата, а не поломка приложения.

Падать в такой ситуации значит спорить с собственным решением: площадь
считается ДО сегментации, и в неё попадают ровно те пиксели, которые движок
дальше сам отбросил как незначимые.

Тесты фиксируют границы нового поведения, потому что послабление в политике
вердикта опаснее ужесточения: им легко нечаянно ослепить движок.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from vistest.config import DiffConfig
from vistest.core.comparator import _verdict
from vistest.models import ChangeKind, CompareResult, DiffRegion, Verdict

CFG = DiffConfig()


def result(*, area=0.0, severity=0.0, regions=0, size_changed=False):
    res = CompareResult(name="t", verdict=Verdict.PASS)
    res.changed_area_pct = area
    res.max_severity = severity
    res.size_changed = size_changed
    res.size_expected = (100, 100)
    res.size_actual = (120, 100) if size_changed else (100, 100)
    res.regions = [
        DiffRegion(x=0, y=0, w=10, h=10, kind=ChangeKind.CONTENT,
                   severity=severity)
        for _ in range(regions)
    ]
    return res


# --------------------------------------------------------------------------- #
#  Новое поведение
# --------------------------------------------------------------------------- #
def test_diffuse_area_without_regions_does_not_fail():
    """Главный случай: severity 0, регионов нет, площадь за порогом."""
    res = result(area=0.835, severity=0.0, regions=0)
    assert _verdict(res, CFG) is Verdict.PASS


def test_it_says_why_it_did_not_fail():
    """Молчаливое непадение хуже падения: человек должен видеть решение."""
    res = result(area=0.835, regions=0)
    _verdict(res, CFG)
    text = " ".join(res.notes)

    assert "no failure" in text
    assert "area_requires_region" in text, "надо назвать, чем это отключается"


def test_area_still_fails_when_a_region_survived():
    """Если регион остался — площадь работает как раньше."""
    res = result(area=0.835, severity=5.0, regions=1)
    assert _verdict(res, CFG) is Verdict.FAIL
    assert "changed" in " ".join(res.notes)


def test_huge_area_fails_even_without_regions():
    """Субпиксельный шум не даёт пяти процентов экрана."""
    res = result(area=12.0, regions=0)
    assert _verdict(res, CFG) is Verdict.FAIL
    assert "the area is large" in " ".join(res.notes)


def test_hard_fail_threshold_is_configurable():
    cfg = replace(CFG, area_hard_fail_pct=1.0)
    res = result(area=2.0, regions=0)
    assert _verdict(res, cfg) is Verdict.FAIL


def test_old_behaviour_is_one_flag_away():
    """Послабление должно отключаться, а не быть вшитым намертво."""
    cfg = replace(CFG, area_requires_region=False)
    res = result(area=0.835, regions=0)
    assert _verdict(res, cfg) is Verdict.FAIL


# --------------------------------------------------------------------------- #
#  Что не должно было сломаться
# --------------------------------------------------------------------------- #
def test_severity_still_fails_on_its_own():
    res = result(area=0.0, severity=90.0, regions=1)
    assert _verdict(res, CFG) is Verdict.FAIL
    assert "severity" in " ".join(res.notes)


def test_size_change_still_fails_on_its_own():
    res = result(size_changed=True)
    assert _verdict(res, CFG) is Verdict.FAIL
    assert "size changed" in " ".join(res.notes)


def test_size_change_fails_even_without_regions():
    """Изменение размера — не диффузный шум, послабление на него не влияет."""
    res = result(area=0.4, regions=0, size_changed=True)
    assert _verdict(res, CFG) is Verdict.FAIL


def test_clean_comparison_passes():
    assert _verdict(result(), CFG) is Verdict.PASS


def test_area_just_below_threshold_passes():
    res = result(area=CFG.max_changed_area_pct - 0.001, severity=5.0, regions=1)
    assert _verdict(res, CFG) is Verdict.PASS


@pytest.mark.parametrize("severity", [0.0, 24.9])
def test_region_below_severity_threshold_still_enables_area(severity):
    """Условие — наличие региона, а не его серьёзность.

    Регион, не дотянувший до порога severity, всё равно означает, что движок
    нашёл что-то структурное. Тогда суммарная площадь снова осмысленна.
    """
    res = result(area=0.9, severity=severity, regions=2)
    assert _verdict(res, CFG) is Verdict.FAIL
