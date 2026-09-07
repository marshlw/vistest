# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Проверка `vistest doctor` на синтетике — без браузера.

Смысл команды в одной цифре: сколько ложных падений даёт неизменённая
страница. Тесты фиксируют, что цифра считается правильно в трёх ситуациях:
идеально стабильная страница, страница с живым элементом, страница с
плавающей высотой.
"""

from __future__ import annotations

import numpy as np
import pytest

from vistest.config import VisTestConfig
from vistest.core import noise as _noise
from vistest.doctor import SimpleFrame, analyze, render_text

from . import synthetic as syn

CFG = VisTestConfig.preset_of("balanced")


def _frames(images, masks=None, doms=None):
    masks = masks or [None] * len(images)
    doms = doms or [{}] * len(images)
    return [SimpleFrame(rgb=i, unstable=m, dom=d)
            for i, m, d in zip(images, masks, doms, strict=False)]


# --------------------------------------------------------------------------- #
#  Здоровый проект
# --------------------------------------------------------------------------- #
def test_identical_loads_are_clean():
    """Три одинаковых загрузки — ноль ложных падений, проект пригоден."""
    base = syn.page()
    rep = analyze(_frames([base, base.copy(), base.copy()]), cfg=CFG,
                  target="stable")

    assert rep.pairs == 2
    assert rep.raw_fails == 0
    assert rep.suppressed_fails == 0
    assert rep.suppressed_rate == 0.0
    assert rep.healthy
    assert not rep.sources


def test_sensor_noise_stays_clean():
    """Шум ниже порога различимости не должен считаться ложным падением."""
    base = syn.page()
    frames = _frames([base,
                      syn.add_sensor_noise(base, sigma=1.6, seed=1),
                      syn.add_sensor_noise(base, sigma=1.6, seed=2)])
    rep = analyze(frames, cfg=CFG, target="noisy-sensor")

    assert rep.suppressed_fails == 0, rep.verdict()
    assert rep.healthy


# --------------------------------------------------------------------------- #
#  Живой элемент — то, ради чего команда и нужна
# --------------------------------------------------------------------------- #
def test_live_block_is_reported_and_suppressed():
    """Блок, который догружается не всегда, валит сравнение — но подавляется.

    Это ровно та пара цифр, которую doctor показывает пользователю:
    «без подавления упало бы N, с подавлением — 0».
    """
    base = syn.page(show_promo=True)
    later = syn.page(show_promo=False)

    # Маска нестабильности, которую дал бы захват серией кадров.
    mask = _noise.stability_mask([base, later])
    rep = analyze(_frames([base, later], masks=[mask, mask]),
                  cfg=CFG, target="live-block")

    assert rep.raw_fails == 1, "без подавления это обязано падать"
    assert rep.raw_rate == 1.0
    assert rep.suppressed_fails == 0, rep.verdict()
    assert rep.healthy
    assert rep.sources, "источник шума должен быть локализован"

    top = rep.sources[0]
    assert top.pairs_seen == 1
    assert top.intra_load, "блок дрожит внутри загрузки — это должно быть видно"
    assert top.suppressed


def test_unmasked_noise_is_flagged_as_not_suppressed():
    """Шум, который маска не покрывает, должен быть честно отмечен."""
    base = syn.page(show_promo=True)
    later = syn.page(show_promo=False)          # маску намеренно не даём

    rep = analyze(_frames([base, later]), cfg=CFG, target="unmasked")

    assert rep.suppressed_fails == 1
    assert not rep.healthy
    assert any(not s.suppressed for s in rep.sources)
    assert any("mask_selectors" in r or "Mask them" in r
               for r in rep.recommendations())


# --------------------------------------------------------------------------- #
#  Плавающая высота — блокирует сравнение до всяких порогов
# --------------------------------------------------------------------------- #
def test_flapping_height_is_the_first_thing_reported():
    base = syn.page()
    taller = syn.page(extra_height=160)

    rep = analyze(_frames([base, taller]), cfg=CFG, target="flap")

    assert rep.size_flap
    assert not rep.healthy
    assert rep.heights == [base.shape[0], taller.shape[0]]
    assert "Page height" in rep.verdict()
    assert any("lazy-load" in r for r in rep.recommendations())


# --------------------------------------------------------------------------- #
#  Разделение шума на внутренний и межзагрузочный
# --------------------------------------------------------------------------- #
def test_intra_and_inter_noise_are_separated():
    """Две метрики меряют разное и не должны совпадать.

    Внутрикадровый шум приходит из масок захвата, межзагрузочный —
    из фактического расхождения кадров.
    """
    base = syn.page(show_promo=True)
    later = syn.page(show_promo=False)
    mask = _noise.stability_mask([base, later])

    with_mask = analyze(_frames([base, later], masks=[mask, mask]), cfg=CFG)
    without = analyze(_frames([base, later]), cfg=CFG)

    # Внутрикадровая метрика читает маски захвата и без них равна нулю…
    assert with_mask.intra_unstable_ratio > 0
    assert without.intra_unstable_ratio == 0

    # …а межзагрузочная считается по самим кадрам и от масок не зависит.
    assert with_mask.inter_unstable_ratio > 0
    assert without.inter_unstable_ratio > 0
    assert with_mask.inter_unstable_ratio == without.inter_unstable_ratio


# --------------------------------------------------------------------------- #
#  Контракт вывода
# --------------------------------------------------------------------------- #
def test_report_serializes_and_renders():
    base = syn.page()
    rep = analyze(_frames([base, syn.add_clock(base)]), cfg=CFG, target="x")

    d = rep.to_dict()
    assert d["false_fail"]["pairs"] == 1
    assert 0.0 <= d["false_fail"]["rate_with_suppression"] <= 1.0
    assert d["verdict"]
    assert "noise_mask" not in d          # numpy-массив не должен уехать в JSON

    import json

    json.dumps(d, ensure_ascii=False)      # не должно падать

    text = render_text(rep, color=False)
    assert "False failures" in text
    assert "\033[" not in text             # color=False — значит без ANSI


def test_single_frame_is_rejected():
    base = syn.page()
    with pytest.raises(ValueError):
        analyze(_frames([base]), cfg=CFG)


def test_masks_of_different_shape_do_not_crash():
    """Высота страницы гуляет — маски приходят разного размера."""
    base = syn.page()
    taller = syn.page(extra_height=160)
    rep = analyze(_frames(
        [base, taller],
        masks=[np.zeros(base.shape[:2], bool), np.zeros(taller.shape[:2], bool)],
    ), cfg=CFG)
    assert rep.pairs == 1


# --------------------------------------------------------------------------- #
#  Чем инсталляция способна запустить чужой набор
# --------------------------------------------------------------------------- #
def test_the_environment_report_names_the_runtimes():
    """Подключение с собственной командой выполняет её ВНУТРИ нашего контейнера.

    Пока этого списка не было, ответ на «почему `npx` не найден» находился
    только в логе упавшего прогона — то есть после того, как человек всё
    настроил и нажал запуск. Вопрос обязан решаться до.
    """
    pytest.importorskip("fastapi")

    from vistest.api.doctor import env_report

    names = [r["name"] for r in env_report()["runtimes"]]

    assert {"node", "npx", "java", "mvn", "dotnet"} <= set(names)
    for entry in env_report()["runtimes"]:
        assert set(entry) == {"name", "path"}
