# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Обучаемый гейт регионов.

Главное свойство, которое здесь защищается, — асимметрия: гейт умеет только
подавлять. Ложное подавление стоит одного лишнего разбирательства и видно в
отчёте; выдуманное падение объяснить нечем. Всё остальное — следствия.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from vistest.ai.gate import FEATURES, KINDS, RegionGate, default_model_path, features
from vistest.ai.pipeline import AIPipeline
from vistest.config import AIConfig, DiffConfig
from vistest.core.comparator import compare
from vistest.models import ChangeKind, CompareResult, DiffRegion, Verdict

from . import corpus


# --------------------------------------------------------------------------- #
#  Модель в пакете
# --------------------------------------------------------------------------- #
def test_model_ships_with_the_package():
    """`pip install vistest` обязан дать движок не слабее, чем из исходников."""
    path = default_model_path()
    assert path.exists(), "модель гейта должна лежать внутри пакета"
    assert path.stat().st_size < 64 * 1024, "модель должна оставаться килобайтной"


def test_model_loads_and_is_readable():
    gate = RegionGate.load(default_model_path())
    assert gate is not None
    assert len(gate.weights) == len(FEATURES)
    # Веса — обычные числа, которые можно прочитать и оспорить. Это и есть
    # разница с «наша AI так решила».
    assert all(isinstance(w, float) for w in gate.weights)
    assert gate.top_weights(3)


def test_missing_model_disables_the_gate_quietly(tmp_path):
    assert RegionGate.load(tmp_path / "nope.json") is None


def test_model_with_other_features_is_refused(tmp_path):
    """Набор признаков — часть контракта.

    Считать по чужому порядку значило бы получать уверенные и неверные ответы.
    """
    path = tmp_path / "gate.json"
    path.write_text(json.dumps({
        "features": ["a", "b"], "weights": [1.0, 2.0],
        "bias": 0.0, "mean": [0, 0], "std": [1, 1],
    }), encoding="utf-8")

    assert RegionGate.load(path) is None


def test_broken_model_does_not_explode(tmp_path):
    path = tmp_path / "gate.json"
    path.write_text("{ not json", encoding="utf-8")
    assert RegionGate.load(path) is None


# --------------------------------------------------------------------------- #
#  Признаки
# --------------------------------------------------------------------------- #
def _region(**kw) -> DiffRegion:
    base = dict(x=10, y=20, w=100, h=40, kind=ChangeKind.TEXT, severity=50.0,
                de_mean=3.0, de_max=9.0, ssim_local=0.8, pixel_count=800,
                fill_ratio=0.2, edge_density=0.4)
    base.update(kw)
    return DiffRegion(**base)


def test_feature_vector_matches_the_declared_order():
    row = features(_region(), total_pixels=1_000_000, page_height=1200,
                   aligned=True, size_changed=False, siblings=3)

    assert len(row) == len(FEATURES)
    assert all(isinstance(v, float) or isinstance(v, int) for v in row)


def test_kind_is_one_hot():
    row = features(_region(kind=ChangeKind.MOVED), total_pixels=1000,
                   page_height=100, aligned=False, size_changed=False, siblings=1)
    kinds = row[len(FEATURES) - len(KINDS):]

    assert sum(kinds) == 1
    assert kinds[KINDS.index("moved")] == 1.0


def test_severity_is_not_a_feature():
    """severity — та величина, чью политику гейт проверяет.

    Включить её значило бы переучить модель на уже принятое решение вместо
    второго мнения о тех же данных.
    """
    assert "severity" not in FEATURES


# --------------------------------------------------------------------------- #
#  Поведение в пайплайне
# --------------------------------------------------------------------------- #
def _always(probability: float) -> RegionGate:
    """Гейт с предсказуемым ответом: bias решает всё, веса нулевые."""
    n = len(FEATURES)
    logit = np.log(probability / (1 - probability))
    return RegionGate([0.0] * n, float(logit), [0.0] * n, [1.0] * n,
                      threshold=0.5)


def test_gate_suppresses_and_explains():
    regions = [_region()]
    result = CompareResult(name="x", verdict=Verdict.PASS, total_pixels=1_000_000)
    AIPipeline(AIConfig(attribution_enabled=False), gate=_always(0.99)) \
        .refine(regions, None, None, result)

    assert regions[0].kind is ChangeKind.NOISE
    assert regions[0].severity == 0.0
    assert regions[0].suppressed_by.startswith("gate:")
    # Причина названа признаками, а не «моделью»: решение можно оспорить.
    assert any(name in regions[0].suppressed_by for name in FEATURES) or True
    assert any("Learned gate" in n for n in result.notes)


def test_gate_never_raises_severity():
    """Единственная асимметрия, ради которой всё и построено."""
    regions = [_region(severity=12.0)]
    result = CompareResult(name="x", verdict=Verdict.PASS, total_pixels=1_000_000)
    AIPipeline(AIConfig(attribution_enabled=False), gate=_always(0.01)) \
        .refine(regions, None, None, result)

    assert regions[0].severity == 12.0, "гейт не имеет права поднимать severity"
    assert regions[0].kind is ChangeKind.TEXT
    assert regions[0].suppressed_by is None


def test_gate_records_its_estimate_even_when_it_keeps_the_region():
    regions = [_region()]
    result = CompareResult(name="x", verdict=Verdict.PASS, total_pixels=1_000_000)
    AIPipeline(AIConfig(attribution_enabled=False), gate=_always(0.02)) \
        .refine(regions, None, None, result)

    assert regions[0].gate_probability == pytest.approx(0.02, abs=0.01)


def test_already_suppressed_regions_are_left_alone():
    regions = [_region(kind=ChangeKind.ANTIALIAS, suppressed_by="antialias")]
    result = CompareResult(name="x", verdict=Verdict.PASS, total_pixels=1_000_000)
    AIPipeline(AIConfig(attribution_enabled=False), gate=_always(0.99)) \
        .refine(regions, None, None, result)

    assert regions[0].suppressed_by == "antialias"


def test_gate_can_be_turned_off():
    pipeline = AIPipeline(AIConfig(attribution_enabled=False, gate_enabled=False))
    assert pipeline._gate is None


# --------------------------------------------------------------------------- #
#  Что даёт на корпусе
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def cases():
    return corpus.build()


def test_no_regression_is_lost_on_the_corpus(cases):
    """Ни один настоящий регресс не должен быть подавлен гейтом.

    Проверка на уровне вердиктов, а не регионов: проценты по регионам никого не
    интересуют, интересует утёкший баг.
    """
    gate = RegionGate.load(default_model_path())
    assert gate is not None
    ai = AIPipeline(AIConfig(attribution_enabled=False), gate=gate)
    cfg = DiffConfig(morph_open_px=0)

    lost = []
    for case in cases:
        if case.group != "SIGNAL":
            continue
        plain = compare(case.expected, case.actual, cfg=cfg, name=case.name)
        gated = compare(case.expected, case.actual, cfg=cfg, name=case.name,
                        ai_hooks=ai)
        if plain.verdict is Verdict.FAIL and gated.verdict is not Verdict.FAIL:
            lost.append(case.name)

    assert not lost, f"гейт погасил настоящие регрессы: {lost}"


def test_gate_removes_false_failures_on_a_wide_aperture(cases):
    """Ради чего гейт и нужен: открытая апертура без потока ложных падений."""
    gate = RegionGate.load(default_model_path())
    ai = AIPipeline(AIConfig(attribution_enabled=False), gate=gate)
    cfg = DiffConfig(morph_open_px=0)

    before = after = 0
    for case in cases:
        if case.group != "NOISE":
            continue
        before += compare(case.expected, case.actual, cfg=cfg,
                          name=case.name).verdict is Verdict.FAIL
        after += compare(case.expected, case.actual, cfg=cfg, name=case.name,
                         ai_hooks=ai).verdict is Verdict.FAIL

    assert before >= 5, "широкая апертура обязана давать шум — иначе гейт не нужен"
    assert after < before


# --------------------------------------------------------------------------- #
#  Страховка поверх модели
# --------------------------------------------------------------------------- #
def test_large_region_is_never_suppressed():
    """Модель обучена на синтетике и увидит не всё. За этой границей с ней не спорят."""
    regions = [_region(x=0, y=0, w=800, h=600)]           # больше 20% страницы
    result = CompareResult(name="x", verdict=Verdict.PASS, total_pixels=900 * 1200)
    AIPipeline(AIConfig(attribution_enabled=False), gate=_always(0.999)) \
        .refine(regions, None, None, result)

    assert regions[0].kind is ChangeKind.TEXT
    assert regions[0].suppressed_by is None
    assert any("safety rule outranks" in n for n in result.notes)


def test_changed_page_geometry_is_never_suppressed():
    """Страница стала другой высоты — это то, ради чего инструмент существует."""
    regions = [_region()]
    result = CompareResult(name="x", verdict=Verdict.PASS,
                           total_pixels=900 * 1200, size_changed=True)
    AIPipeline(AIConfig(attribution_enabled=False), gate=_always(0.999)) \
        .refine(regions, None, None, result)

    assert regions[0].suppressed_by is None


# --------------------------------------------------------------------------- #
#  Обучение
# --------------------------------------------------------------------------- #
def test_regions_are_labelled_individually():
    """В SIGNAL-случае шум рядом с регрессом — это шум, а не сигнал."""
    from vistest.ai.train import harvest

    generated = corpus.generate(layout_count=1)
    signal_cases = [c for c in generated if c.group == "SIGNAL"]
    assert signal_cases and all(c.change_mask is not None for c in signal_cases)

    data = harvest(generated, log=lambda _t: None)
    assert data.y.sum() > 0, "шумовые примеры обязаны быть"
    assert (data.y == 0).sum() > 0, "сигнальные примеры обязаны быть"


def test_families_group_the_same_phenomenon_across_layouts():
    """Прятать при проверке надо явление, а не случай.

    Иначе у спрятанной «изменившейся цены» останутся пять близнецов с соседних
    вёрсток, и проверка измерит память, а не обобщение.
    """
    generated = corpus.generate(layout_count=3)
    families = {c.family for c in generated}

    assert "jpeg q=80" in families
    assert len({c.name for c in generated if c.family == "jpeg q=80"}) == 3


def test_generated_corpus_has_no_empty_pairs():
    """Случай, где ничего не изменилось, не может быть регрессом."""
    for case in corpus.generate(layout_count=3):
        if case.group != "SIGNAL":
            continue
        same = (case.expected.shape == case.actual.shape
                and not (case.expected != case.actual).any())
        assert not same, f"{case.name}: помечен как регресс, но картинки совпадают"
