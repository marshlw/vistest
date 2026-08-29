# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Обучение гейта регионов.

Запуск:

    python -m vistest.ai.train                 # обучить на синтетическом корпусе
    python -m vistest.ai.train --out my.json   # положить рядом, а не в пакет
    python -m vistest.ai.train --report        # показать, что выучилось

Откуда берётся разметка. Из корпуса (`tests/corpus.py`), где ответ задан
построением: случай в группе NOISE — страница по существу не менялась, значит
любой найденный в нём регион по определению шум; случай в группе SIGNAL —
регресс есть, и все его регионы считаются сигналом.

Разметка по случаю, а не по региону, сделана намеренно. В SIGNAL-случае рядом с
настоящим изменением попадаются и шумовые регионы, и, называя их сигналом, мы
смещаем модель в сторону «не подавлять». Смещение осознанное: гейт умеет только
подавлять, поэтому его ошибка в сторону осторожности стоит одного лишнего
разбирательства, а в обратную — пропущенного бага.

Апертура при сборе намеренно шире рабочей: `morph_open_px=0` и низкий порог
плотности. На дефолтных настройках движок гасит почти весь шум сам, регионов
почти нет — и учиться не на чем. Широкая апертура даёт и трудные шумовые
регионы, и полный набор сигнальных.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..config import DiffConfig
from ..core.comparator import compare
from .gate import RegionGate, default_model_path, features

# Апертура для добычи обучающих примеров: ловим всё, что движок в принципе
# способен увидеть, включая то, что рабочие настройки отбрасывают.
HARVEST = DiffConfig(morph_open_px=0, min_region_px=8, min_region_fill=0.02,
                     max_regions=400)


@dataclass
class Dataset:
    X: np.ndarray
    y: np.ndarray            # 1 — шум (подавить), 0 — настоящее изменение
    groups: list[str]        # явление: по нему прячут данные при проверке


# --------------------------------------------------------------------------- #
def _overlaps(region, mask: np.ndarray, *, min_share: float = 0.15) -> bool:
    """Попадает ли регион в область настоящего изменения."""
    h, w = mask.shape
    y0, y1 = max(0, region.y), min(h, region.y + region.h)
    x0, x1 = max(0, region.x), min(w, region.x + region.w)
    if y1 <= y0 or x1 <= x0:
        return False
    window = mask[y0:y1, x0:x1]
    return bool(window.mean() >= min_share)


def harvest(cases, cfg: DiffConfig | None = None, log=print) -> Dataset:
    """Корпус → таблица признаков с метками.

    Метка ставится региону, а не случаю. В NOISE-случае страница по существу
    не менялась, поэтому шум — всё найденное. В SIGNAL-случае сигнал — только то,
    что попадает в область настоящего изменения; остальное на той же странице
    такой же шум, как и в NOISE-случае, и объявлять его сигналом значит учить
    модель неправде ради удобства разметки.

    Там, где маски нет (отобранный корпус её не считает), остаётся прежнее
    осторожное правило: весь SIGNAL-случай — сигнал. Ошибка при этом смещает
    модель в сторону «не подавлять», а это дешёвая сторона.
    """
    cfg = cfg or HARVEST
    rows: list[list[float]] = []
    labels: list[int] = []
    groups: list[str] = []

    for case in cases:
        res = compare(case.expected, case.actual, cfg=cfg, name=case.name)
        regions = list(res.regions) + list(res.suppressed)
        if not regions:
            continue
        page_h = max(case.expected.shape[0], case.actual.shape[0])
        mask = getattr(case, "change_mask", None)
        family = getattr(case, "family", "") or case.name

        signal_regions = 0
        for r in regions:
            if case.group == "NOISE":
                label = 1
            elif mask is None:
                label = 0
            else:
                label = 0 if _overlaps(r, mask) else 1
            signal_regions += label == 0
            rows.append(features(
                r, total_pixels=res.total_pixels or page_h * case.expected.shape[1],
                page_height=page_h, aligned=res.aligned,
                size_changed=res.size_changed, siblings=len(regions)))
            labels.append(label)
            groups.append(family)
        log(f"  {case.group:6s} {case.name:26s} regions: {len(regions):3d} "
            f"(signal {signal_regions})")

    if not rows:
        raise RuntimeError("the corpus gave no regions — there is nothing to train on")
    return Dataset(np.asarray(rows, dtype=np.float64),
                   np.asarray(labels, dtype=np.float64), groups)


# --------------------------------------------------------------------------- #
def fit(data: Dataset, *, l2: float = 1.0, steps: int = 4000,
        lr: float = 0.2) -> tuple[np.ndarray, float, np.ndarray, np.ndarray]:
    """Логистическая регрессия градиентным спуском. Возвращает (w, b, mean, std).

    Numpy и ничего больше: тащить scikit-learn ради пятнадцати признаков значило
    бы утяжелить оффлайн-установку на сотню мегабайт ради ста строк математики.

    Классы уравновешены по весу: шумовых регионов на порядок больше сигнальных,
    и без балансировки модель выучила бы «подавлять всё» — то есть ровно ту
    ошибку, которая стоит дороже всего.
    """
    X, y = data.X, data.y
    mean = X.mean(axis=0)
    std = X.std(axis=0)
    std[std < 1e-9] = 1.0
    Z = (X - mean) / std

    n_pos = max(1.0, float(y.sum()))
    n_neg = max(1.0, float(len(y) - y.sum()))
    sample_w = np.where(y > 0.5, len(y) / (2 * n_pos), len(y) / (2 * n_neg))

    # Внутри класса случаи тоже неравны: сенсорный шум даёт три сотни регионов,
    # изменившаяся цена — один. Без нормировки по случаю модель училась бы
    # почти исключительно на зернистости одной картинки.
    if data.groups:
        counts: dict[str, int] = {}
        for g in data.groups:
            counts[g] = counts.get(g, 0) + 1
        per_case = np.asarray([1.0 / counts[g] for g in data.groups])
        sample_w = sample_w * per_case * (len(y) / per_case.sum())

    w = np.zeros(Z.shape[1])
    b = 0.0
    for _ in range(steps):
        p = 1.0 / (1.0 + np.exp(-np.clip(Z @ w + b, -40, 40)))
        err = (p - y) * sample_w
        grad_w = Z.T @ err / len(y) + l2 * w / len(y)
        grad_b = err.mean()
        w -= lr * grad_w
        b -= lr * grad_b
    return w, b, mean, std


def probabilities(gate: RegionGate, data: Dataset) -> np.ndarray:
    return np.asarray([gate.decide(list(row)).noise_probability for row in data.X])


def choose_threshold(gate: RegionGate, data: Dataset, *,
                     max_signal_suppressed: float = 0.0) -> float:
    """Самый низкий порог, при котором ни один сигнальный регион не подавлен.

    Не «оптимальный по F1». Точность подавления здесь не симметрична: лишний
    разбор ложного падения стоит минут, пропущенный регресс — инцидента. Порог
    выбирается по худшему допустимому исходу, а не по среднему.
    """
    p = probabilities(gate, data)
    signal = p[data.y < 0.5]
    if len(signal) == 0:
        return 0.5
    limit = float(np.quantile(signal, 1.0 - max_signal_suppressed)) \
        if max_signal_suppressed > 0 else float(signal.max())
    # Чуть выше самого «шумного» из сигнальных, но не впритык к единице.
    return float(min(0.99, max(0.5, limit + 1e-3)))


# --------------------------------------------------------------------------- #
def cross_validate(cases, cfg: DiffConfig, *, l2: float = 1.0,
                   log=print) -> dict:
    """Проверка с исключением явления целиком.

    Обучаться и проверяться на одном корпусе — не измерение, а пересказ. Прятать
    надо не случай, а явление: «изменившаяся цена» встречается на шести вёрстках,
    и, спрятав одну, мы оставили бы модели пять близнецов, которые подскажут
    ответ. Убрали явление целиком — обучили заново — посмотрели, узнаёт ли.

    Это нижняя граница того, чего ждать на незнакомых данных, и именно её честно
    публиковать.
    """
    stats = {"false_fail_before": 0, "false_fail_after": 0,
             "missed_before": 0, "missed_after": 0, "notes": []}

    # Признаки не зависят от того, какие случаи попали в обучение, поэтому
    # собираются один раз, а на каждом шаге просто выбрасывается один случай.
    # Иначе проверка пересчитывала бы сравнение картинок семьсот раз.
    full = harvest(cases, log=lambda _t: None)
    groups = np.asarray(full.groups)
    families = sorted({getattr(c, "family", "") or c.name for c in cases})

    for family in families:
        keep = groups != family
        if not keep.any():
            continue
        data = Dataset(full.X[keep], full.y[keep],
                       [g for g in full.groups if g != family])
        w, b, mean, std = fit(data, l2=l2)
        gate = RegionGate(w, b, mean, std, threshold=0.5)
        gate.threshold = choose_threshold(gate, data)

        held = [c for c in cases
                if (getattr(c, "family", "") or c.name) == family]
        one = evaluate(gate, held, cfg, log=lambda _t: None)
        for key in ("false_fail_before", "false_fail_after",
                    "missed_before", "missed_after"):
            stats[key] += one[key]
        stats["notes"] += one["flipped"]
    return stats


def evaluate(gate: RegionGate, cases, cfg: DiffConfig, log=print) -> dict:
    """Что даёт гейт на уровне вердиктов, а не регионов.

    Проценты по регионам никого не интересуют. Интересует, сколько случаев
    покраснело зря и сколько регрессов утекло — до и после.
    """
    from ..config import AIConfig
    from .pipeline import AIPipeline

    ai = AIPipeline(AIConfig(attribution_enabled=False, gate_enabled=True),
                    gate=gate)

    stats = {"false_fail_before": 0, "false_fail_after": 0,
             "missed_before": 0, "missed_after": 0, "flipped": []}
    for case in cases:
        plain = compare(case.expected, case.actual, cfg=cfg, name=case.name)
        gated = compare(case.expected, case.actual, cfg=cfg, name=case.name,
                        ai_hooks=ai)
        fail_before = plain.verdict.value == "fail"
        fail_after = gated.verdict.value == "fail"
        if case.group == "NOISE":
            stats["false_fail_before"] += fail_before
            stats["false_fail_after"] += fail_after
            if fail_before and not fail_after:
                stats["flipped"].append(f"false failure suppressed: {case.name}")
        else:
            stats["missed_before"] += not fail_before
            stats["missed_after"] += not fail_after
            if fail_before and not fail_after:
                stats["flipped"].append(f"!! MISSED REGRESSION: {case.name}")
    return stats


# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Train the region gate")
    ap.add_argument("--out", default=None, help="where to put the model")
    ap.add_argument("--l2", type=float, default=1.0)
    ap.add_argument("--report", action="store_true", help="show weights and metrics")
    ap.add_argument("--layouts", type=int, default=6,
                    help="how many layouts to multiply by the families")
    args = ap.parse_args(argv)

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tests import corpus

    # Учим на генераторе (вёрстки × явления), а показываем человеку отобранный
    # корпус. Смешивать нельзя: отобранный тем и ценен, что его пятнадцать строк
    # можно прочитать и оспорить.
    cases = corpus.generate(args.layouts)
    print(f"training corpus: {len(cases)} cases from "
          f"{len({getattr(c, 'family', c.name) for c in cases})} families")
    data = harvest(cases, log=lambda _t: None)
    print(f"regions: {len(data.y)} "
          f"(noise {int(data.y.sum())}, signal {int(len(data.y) - data.y.sum())})")

    w, b, mean, std = fit(data, l2=args.l2)
    gate = RegionGate(w, b, mean, std, threshold=0.5, meta={
        "trained_on": "generated corpus (tests/corpus.py::generate)",
        "layouts": args.layouts,
        "cases": len(cases),
        "regions": int(len(data.y)),
        "signal_regions": int(len(data.y) - data.y.sum()),
        "l2": args.l2,
    })
    gate.threshold = choose_threshold(gate, data)
    gate.meta["threshold_rule"] = "above the noisiest signal region"

    out = Path(args.out) if args.out else default_model_path()
    gate.save(out)
    print(f"model saved: {out}  (threshold {gate.threshold:.3f}, "
          f"{out.stat().st_size} bytes)")

    if args.report:
        print("\nwhat was learned (weight > 0 — a sign of noise):")
        for name, weight in gate.top_weights(12):
            print(f"  {name:16s} {weight:+.3f}")

        wide = DiffConfig(morph_open_px=0)
        curated = corpus.build()

        print("\ncurated corpus, wide aperture (the model has not seen it):")
        stats = evaluate(gate, curated, wide, log=lambda _t: None)
        for key in ("false_fail_before", "false_fail_after",
                    "missed_before", "missed_after"):
            print(f"  {key}: {stats[key]}")
        for note in stats["flipped"]:
            print(f"    {note}")

        print("\nhonest validation on the generator "
              "(a whole family held out), wide aperture:")
        cv = cross_validate(cases, wide)
        for key in ("false_fail_before", "false_fail_after",
                    "missed_before", "missed_after"):
            print(f"  {key}: {cv[key]}")
        lost = [n for n in cv["notes"] if "MISSED" in n]
        if lost:
            print(f"  suppressed regressions: {len(lost)}")
            for n in lost[:5]:
                print(f"    {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
