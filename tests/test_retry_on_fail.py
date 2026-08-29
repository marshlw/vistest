# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Пересъёмка при падении.

Визуальные тесты умирают не от отсутствия интеграций, а от того, что через месяц
красному перестают верить и жмут «принять» не глядя. Одна анимация, один
спиннер, одна подгрузка шрифта — и набор краснеет через раз.

Логика лечения уже была доказана внутри `doctor`: он грузит одну и ту же
неизменную страницу несколько раз, и любое расхождение между загрузками ложное
**по построению**. Здесь та же логика применяется внутри прогона, но только к
тому, что и так упало.

Главное, что здесь проверяется, — что эта штука **не умеет прятать устойчивый
регресс**. Механизм, который тихо превращает красное в зелёное, был бы хуже, чем
его отсутствие: он снимает единственный сигнал, ради которого всё написано.

Три свойства держат это:

* сравнивается **первый** кадр, а не второй — второй только свидетель;
* подавляются **конкретные области**, а не вердикт целиком;
* «прошло со второго раза» пишется в заметку, а не проглатывается.

И одна честная граница, у которой тоже есть тест: **мерцающую** поломку два
кадра отличить от мерцающего шума не могут — не могут в принципе, ни здесь, ни у
человека. Поэтому зелёный в таком случае не молчит, а говорит «страница здесь
нестабильна, чините в источнике» — для недетерминированного рендера это ровно
тот текст, который нужен.
"""

from __future__ import annotations

import numpy as np
import pytest

from vistest.config import VisTestConfig
from vistest.service import CheckService
from vistest.storage import BaselineRecord, FileBaselineStore

H, W = 80, 120


def _page(*, spinner: int = 0, header_shift: bool = False) -> np.ndarray:
    """Страница: неподвижная шапка и уголок, в котором крутится спиннер."""
    img = np.full((H, W, 3), 240, dtype=np.uint8)
    img[4:14, 4:100] = 40                       # шапка
    if header_shift:
        img[4:14, 4:100] = 240
        img[10:20, 4:100] = 40                  # шапка уехала вниз
    if spinner:
        img[60:76, 96:116] = spinner            # живой уголок
    return img


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("VISTEST_ROOT", str(tmp_path / ".vistest"))
    cfg = VisTestConfig.load()
    store = FileBaselineStore(tmp_path / "baselines")
    service = CheckService(cfg, platform="test-1x", run_dir=tmp_path / "run",
                           store=store)
    store.save(BaselineRecord(name="page.png", image=_page(spinner=10)))
    return service


def _check(service, image, recapture=None, **kw):
    return service.check("page.png", image, render=False,
                         recapture=recapture, **kw)


# --------------------------------------------------------------------------- #
#  Что должно оставаться красным
# --------------------------------------------------------------------------- #
def test_a_real_change_survives_the_second_capture(svc):
    """Настоящий регресс воспроизводится — на то он и настоящий.

    Шапка уехала и на первом кадре, и на втором: между кадрами она никуда не
    дрожит, значит в маску не попадает и остаётся красной.
    """
    moved = _page(spinner=10, header_shift=True)
    res = _check(svc, moved, recapture=lambda: moved)
    assert res.failed
    assert any("nothing on this page moved" in n for n in res.notes)


def test_a_real_change_survives_even_when_the_page_is_alive(svc):
    """Спиннер дрожит, шапка уехала — падать должна шапка.

    Это главный тест файла. Подавить вердикт целиком из-за одного живого уголка
    значило бы построить машину для сокрытия регрессов.
    """
    first = _page(spinner=10, header_shift=True)
    second = _page(spinner=250, header_shift=True)
    res = _check(svc, first, recapture=lambda: second)
    assert res.failed
    assert any("the difference remains" in n for n in res.notes)


def test_a_flickering_break_is_named_instability_not_success(svc):
    """Честная граница механизма, и она должна быть проверена, а не умолчана.

    Если поломка проявляется через раз, второй кадр может застать страницу
    целой — область попадёт в маску, и вердикт станет зелёным. Двух кадров,
    чтобы отличить мерцающий шум от мерцающей поломки, не хватает никому.

    Поэтому зелёный здесь обязан быть **не молчаливым**: для мерцающей поломки
    «страница здесь нестабильна, чините в источнике» — ровно тот текст, который
    нужен, потому что чинить надо недетерминированный рендер.
    """
    broken = _page(spinner=10, header_shift=True)
    clean = _page(spinner=10)                    # второй кадр «как эталон»
    res = _check(svc, broken, recapture=lambda: clean)

    assert not res.failed
    note = next(n for n in res.notes if "did not reproduce" in n)
    assert "unstable" in note and "fixing at the source" in note


def test_the_verdict_is_about_the_first_frame(svc):
    """Второй кадр — свидетель, а не замена картинки.

    Проверяется по размеру: у кадров разная высота (обычное дело на живой
    странице — что-то догрузилось), и отчёт обязан описывать тот кадр, который
    упал. Сравнивать второй значило бы выбирать снимок поудачнее, пока не
    позеленеет.
    """
    first = _page(spinner=10, header_shift=True)
    second = np.vstack([_page(spinner=250, header_shift=True),
                        np.full((40, W, 3), 240, dtype=np.uint8)])
    res = _check(svc, first, recapture=lambda: second)

    assert res.size_actual == (W, H), f"отчёт про второй кадр: {res.size_actual}"


def test_the_picture_a_person_opens_is_the_one_that_failed(svc):
    """`actual.png` — первый кадр.

    Человек идёт смотреть на то, что упало; подсунуть ему удачный кадр значит
    показать картинку, к которой вердикт не относится.
    """
    first = _page(spinner=250, header_shift=True)
    second = _page(spinner=120, header_shift=True)
    res = svc.check("page.png", first, render=True, recapture=lambda: second)

    from vistest.capture.playwright_capture import read_png

    saved = read_png(res.artifacts["actual"])
    assert np.array_equal(saved, first)


# --------------------------------------------------------------------------- #
#  Что должно становиться зелёным
# --------------------------------------------------------------------------- #
def test_a_difference_that_does_not_reproduce_is_noise(svc):
    """Дрожит только то, что и разошлось с эталоном.

    Такое расхождение ложное по построению — ровно то, что `doctor` меряет на
    неизменной странице.
    """
    first = _page(spinner=250)
    second = _page(spinner=120)
    assert _check(svc, first).failed, "без пересъёмки это падение"

    res = _check(svc, first, recapture=lambda: second)
    assert not res.failed
    assert any("did not reproduce" in n for n in res.notes)


def test_passing_on_retry_is_not_silent(svc):
    """«Прошло со второго раза» — это не «всё хорошо».

    Это «страница здесь нестабильна», и человек обязан это прочитать: снимок,
    который лечится пересъёмкой из раза в раз, надо чинить в источнике.
    """
    res = _check(svc, _page(spinner=250), recapture=lambda: _page(spinner=120))
    note = next(n for n in res.notes if "did not reproduce" in n)
    # Заголовок заметки — сам факт «упало, потом прошло». Без него остальное
    # читается как обычное описание шума, а не как то, что произошло с этим
    # снимком в этом прогоне.
    assert "Failed on the first capture and passed on the second" in note
    assert "unstable" in note
    assert "fixing at the source" in note
    assert "%" in note, "доля дрожащей площади названа числом"


def test_the_lesson_is_remembered(svc, tmp_path):
    """Найденная нестабильность уходит в накопленную маску снимка.

    Иначе пересъёмка платила бы за один и тот же спиннер каждый прогон, вечно.
    """
    _check(svc, _page(spinner=250), recapture=lambda: _page(spinner=120))
    # Каталог спрашиваем у хранилища: имя снимка и имя каталога — разные вещи.
    assert (svc.store.dir_for("page.png") / "stability.png").exists()

    # И следующий прогон падать уже не должен — маска накоплена.
    assert not _check(svc, _page(spinner=200)).failed


# --------------------------------------------------------------------------- #
#  Когда пересъёмки не происходит
# --------------------------------------------------------------------------- #
def test_a_passing_snapshot_costs_nothing(svc):
    """Второй кадр снимается только у упавшего — иначе прогон дорожает вдвое."""
    calls = []

    def recapture():
        calls.append(1)
        return _page(spinner=10)

    assert not _check(svc, _page(spinner=10), recapture=recapture).failed
    assert calls == []


def test_it_can_be_switched_off(svc):
    """Прогон, где важнее время, а не тишина, — это законный выбор."""
    from dataclasses import replace

    svc.cfg.capture = replace(svc.cfg.capture, retry_on_fail=False)
    calls = []
    res = _check(svc, _page(spinner=250),
                 recapture=lambda: calls.append(1) or _page(spinner=120))
    assert res.failed
    assert calls == []


def test_without_a_way_to_recapture_nothing_changes(svc):
    """Не каждый вызывающий умеет снимать заново — и это нормально."""
    assert _check(svc, _page(spinner=250)).failed


def test_a_broken_second_capture_does_not_break_the_verdict(svc):
    """Вердикт уже посчитан по первому кадру.

    Уронить проверку из-за того, что не удалось снять ЛИШНИЙ кадр, значит
    потерять готовый ответ ради уточнения к нему.
    """
    def boom():
        raise RuntimeError("browser is gone")

    res = _check(svc, _page(spinner=250), recapture=boom)
    assert res.failed
    assert any("Could not take a second capture" in n for n in res.notes)
    assert any("browser is gone" in n for n in res.notes)


def test_a_second_capture_that_returns_nothing_is_not_a_crash(svc):
    res = _check(svc, _page(spinner=250), recapture=lambda: None)
    assert res.failed


def test_a_new_baseline_is_not_retried(svc):
    """Сравнивать не с чем — пересъёмка ничего не решит."""
    calls = []
    res = svc.check("fresh.png", _page(spinner=10), render=False,
                    recapture=lambda: calls.append(1) or _page())
    assert res.verdict.value == "new_baseline"
    assert calls == []


# --------------------------------------------------------------------------- #
#  Порог, записанный в паспорте снимка
#
#  Третий уровень после глобального и проектного. Двух не хватало: один шумный
#  дашборд заставлял ослаблять порог для всего набора — то есть чинить один
#  снимок ценой чувствительности всех остальных.
# --------------------------------------------------------------------------- #
def _with_thresholds(svc, name="page.png", **values):
    record = svc.store.load(name)
    record.meta = {**(record.meta or {}), "thresholds": values}
    svc.store.save(record)


def _block(shift: int = 0) -> np.ndarray:
    """Страница с блоком, который может съехать на пару пикселей.

    Именно такой случай и заставляет заводить порог на снимок: сдвиг на этом
    экране терпим, а на соседнем — нет.
    """
    img = np.full((H, W, 3), 240, dtype=np.uint8)
    img[4:14, 4:100] = 40
    img[40 + shift:52 + shift, 20:60] = 90
    return img


def test_a_snapshot_threshold_changes_its_verdict(svc):
    """Ради этого всё и написано: один снимок терпимее остальных."""
    svc.store.save(BaselineRecord(name="block.png", image=_block()))
    moved = _block(shift=3)
    assert svc.check("block.png", moved, render=False).failed

    _with_thresholds(svc, "block.png", max_changed_area_pct=100)
    assert not svc.check("block.png", moved, render=False).failed


def test_it_applies_only_to_the_snapshot_that_carries_it(svc):
    """Иначе это не третий уровень, а тот же глобальный, только окольным путём."""
    svc.store.save(BaselineRecord(name="block.png", image=_block()))
    svc.store.save(BaselineRecord(name="other.png", image=_block()))
    _with_thresholds(svc, "block.png", max_changed_area_pct=100)

    assert not svc.check("block.png", _block(shift=3), render=False).failed
    assert svc.check("other.png", _block(shift=3), render=False).failed


def test_the_call_wins_over_the_passport(svc):
    """`assert_screenshot(fail_severity=...)` написан рядом с проверкой.

    Он знает о ней больше, чем настройка, выставленная когда-то в интерфейсе, —
    и потому он самый сильный из уровней.
    """
    svc.store.save(BaselineRecord(name="block.png", image=_block()))
    _with_thresholds(svc, "block.png", max_changed_area_pct=100)
    moved = _block(shift=3)
    assert not svc.check("block.png", moved, render=False).failed

    res = svc.check("block.png", moved, render=False,
                    diff_overrides={"max_changed_area_pct": 0.01})
    assert res.failed


def test_a_none_in_the_call_does_not_erase_the_passport(svc):
    """`None` в переопределении значит «не задано», а не «сбросить».

    Раннер передаёт все четыре ручки всегда, и незаполненные приходят как
    `None`. Считать их сбросом означало бы, что паспорт снимка не действует
    никогда — ровно там, где он и нужен.
    """
    svc.store.save(BaselineRecord(name="block.png", image=_block()))
    _with_thresholds(svc, "block.png", max_changed_area_pct=100)
    res = svc.check("block.png", _block(shift=3), render=False,
                    diff_overrides={"fail_severity": None,
                                    "max_changed_area_pct": None})
    assert not res.failed


def test_garbage_in_the_passport_does_not_stop_the_run(svc):
    """Одна кривая правка паспорта не должна останавливать весь прогон.

    Сравнение при этом идёт по общим порогам — то есть по строгим, а не по
    выдуманным из мусора.
    """
    _with_thresholds(svc, fail_severity="loose", max_changed_area_pct=None)
    assert _check(svc, _page(spinner=250)).failed


def test_a_snapshot_can_be_made_stricter_too(svc):
    """Третий уровень работает в обе стороны, а не только «потерпи»."""
    barely = _page(spinner=12)               # почти неотличимо от эталона (10)
    assert not _check(svc, barely).failed

    _with_thresholds(svc, fail_severity=0, max_changed_area_pct=0)
    assert _check(svc, barely).failed
