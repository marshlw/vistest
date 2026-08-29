# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Проверка машинерии бенчмарка.

Цифры из бенчмарка предполагается публиковать, поэтому важно, чтобы ломался
он громко. Тесты фиксируют три вещи: корпус собирается и размечен осмысленно,
каждый движок-конкурент отвечает на каждой паре, и счёт считается правильно.

Сами пороги качества здесь не проверяются — это дело `test_engine.py`.
Здесь проверяется, что таблица не врёт по построению.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from vistest.config import VisTestConfig

from . import baselines as bl
from . import benchmark as bench
from . import corpus as cp

CFG = VisTestConfig.preset_of("balanced")


@pytest.fixture(scope="module")
def cases():
    return cp.build()


# --------------------------------------------------------------------------- #
#  Корпус
# --------------------------------------------------------------------------- #
def test_corpus_has_both_groups(cases):
    noise = [c for c in cases if c.group == "NOISE"]
    signal = [c for c in cases if c.group == "SIGNAL"]
    assert len(noise) >= 5
    assert len(signal) >= 5
    assert all(not c.expected_fail for c in noise)
    assert all(c.expected_fail for c in signal)


def test_every_case_is_justified(cases):
    """Кейс без объяснения в публикуемой таблице недопустим."""
    for c in cases:
        assert c.why, f"кейс {c.name} без обоснования"
        assert c.name


def test_case_names_are_unique(cases):
    names = [c.name for c in cases]
    assert len(names) == len(set(names))
    slugs = [cp._slug(c.name) for c in cases]
    assert len(slugs) == len(set(slugs)), "имена схлопываются при экспорте в файлы"


def test_noise_cases_really_are_noise(cases):
    """NOISE-пары обязаны отличаться от эталона — иначе тест ничего не проверяет.

    Кроме `identical`, который для того и заведён.
    """
    for c in cases:
        if c.group != "NOISE" or c.name == "identical":
            continue
        if c.expected.shape != c.actual.shape:
            continue
        assert not np.array_equal(c.expected, c.actual), \
            f"{c.name} совпадает с эталоном байт в байт — кейс пустой"


def test_signal_cases_really_differ(cases):
    for c in cases:
        if c.group != "SIGNAL":
            continue
        assert (c.expected.shape != c.actual.shape
                or not np.array_equal(c.expected, c.actual))


def test_export_roundtrip(cases, tmp_path):
    out = cp.export_corpus(cases, tmp_path / "corpus")
    manifest = json.loads((out / "manifest.json").read_text("utf-8"))

    assert len(manifest["cases"]) == len(cases)
    for entry in manifest["cases"]:
        assert (out / entry["expected"]).exists()
        assert (out / entry["actual"]).exists()
        assert entry["group"] in ("NOISE", "SIGNAL")


# --------------------------------------------------------------------------- #
#  Конкуренты
# --------------------------------------------------------------------------- #
def test_every_engine_answers_on_every_case(cases):
    for engine in bl.ENGINES:
        for c in cases:
            res = engine.run(c.expected, c.actual)
            assert isinstance(res.failed, bool), f"{engine.key} / {c.name}"
            assert res.total_pixels > 0


def test_absdiff_is_maximally_strict(cases):
    """Самопис не прощает ничего: всё, кроме идентичных пар, — красное.

    Это не придирка к absdiff, это и есть причина, по которой самописные
    скрипты не живут дольше пары месяцев.
    """
    for c in cases:
        res = bl.absdiff(c.expected, c.actual)
        if c.name == "identical":
            assert not res.failed
        else:
            assert res.failed, f"absdiff неожиданно простил {c.name}"


def test_identical_passes_everywhere(cases):
    """На одинаковых изображениях не должен падать никто."""
    base = next(c for c in cases if c.name == "identical")
    for engine in bl.ENGINES:
        assert not engine.run(base.expected, base.actual).failed, engine.key


def test_pixelmatch_threshold_is_monotonic():
    """Выше порог — не больше отличий. Иначе порт сломан."""
    from . import synthetic as syn

    base = syn.page()
    noisy = syn.add_sensor_noise(base, 3.0, seed=7)
    counts = [bl.pixelmatch_numpy(base, noisy, threshold=t).diff_pixels
              for t in (0.0, 0.05, 0.1, 0.2, 0.5)]
    assert counts == sorted(counts, reverse=True)


def test_playwright_policy_fails_on_single_pixel():
    """Один непрощённый пиксель роняет toHaveScreenshot() — это и есть суть."""
    from . import synthetic as syn

    base = syn.page()
    touched = base.copy()
    touched[500, 500] = (255, 0, 0)     # ровно один пиксель, максимальный контраст

    assert bl.playwright_screenshot(base, touched).failed
    assert bl.playwright_screenshot(base, base.copy()).failed is False


def test_size_change_fails_everywhere():
    from . import synthetic as syn

    base = syn.page()
    taller = syn.page(extra_height=160)
    for engine in bl.ENGINES:
        assert engine.run(base, taller).failed, engine.key


# --------------------------------------------------------------------------- #
#  Счёт
# --------------------------------------------------------------------------- #
def test_score_counts_errors_on_the_right_side(cases):
    s = bench.Score(title="t")
    for c in cases:
        s.add(c, failed=c.expected_fail)          # идеальный инструмент

    assert s.false_fails == 0
    assert s.misses == 0
    assert s.correct == s.total == len(cases)
    assert s.false_fail_rate == 0.0
    assert s.miss_rate == 0.0


def test_score_of_always_green_tool(cases):
    """Инструмент, который никогда не падает: ноль ложных, все пропуски."""
    s = bench.Score(title="always green")
    for c in cases:
        s.add(c, failed=False)

    assert s.false_fails == 0
    assert s.false_fail_rate == 0.0
    assert s.miss_rate == 1.0
    assert s.correct == s.noise_total


def test_score_of_always_red_tool(cases):
    s = bench.Score(title="always red")
    for c in cases:
        s.add(c, failed=True)

    assert s.false_fail_rate == 1.0
    assert s.misses == 0
    assert s.correct == s.signal_total


def test_native_results_override_ports(cases):
    """При наличии нативного прогона в таблицу должны идти его цифры."""
    native = {"results": {"pixelmatch": {
        c.name: {"failed": c.expected_fail, "diff_pixels": 0,
                 "total_pixels": 1} for c in cases}}}

    s = bench.score_native(cases, native, "pixelmatch", "pixelmatch", "")
    assert s is not None
    assert s.native
    assert s.false_fails == 0 and s.misses == 0

    # Неполный нативный прогон использовать нельзя — молча дорисовывать нечего.
    partial = {"results": {"pixelmatch": {cases[0].name: {"failed": False}}}}
    assert bench.score_native(cases, partial, "pixelmatch", "x", "") is None
    assert bench.score_native(cases, {}, "pixelmatch", "x", "") is None


# --------------------------------------------------------------------------- #
#  Публикуемый markdown
# --------------------------------------------------------------------------- #
def test_markdown_is_complete_and_honest(cases):
    scores = [bench.score_vistest(cases, CFG)]
    scores += [bench.score_engine(cases, e) for e in bl.ENGINES]

    md = bench.markdown(scores, cases, CFG, native_used=False)

    for c in cases:
        assert f"`{c.name}`" in md, f"кейс {c.name} не попал в таблицу"
        assert c.why in md
    for s in scores:
        assert s.title in md

    # Оговорки, без которых таблицу публиковать нельзя.
    assert "не доказывает" in md
    assert "порт" in md
    assert "конфликт интересов" in md
    assert "Как воспроизвести" in md


def test_markdown_marks_ports(cases):
    scores = [bench.score_engine(cases, e) for e in bl.ENGINES]
    md = bench.markdown(scores, cases, CFG, native_used=False)
    assert "⁽ᵖ⁾" in md, "порт обязан быть помечен в публикуемой таблице"
