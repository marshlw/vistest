# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Обучаемый гейт регионов: «это шум или настоящее изменение».

Зачем он нужен, если есть пороги. Пороги решают задачу один раз и для всех.
Морфологическое «открытие», например, съедает одиночные пиксели шума — и вместе
с ними тонкие штрихи текста: на корпусе движок с `morph_open_px=2` молча
пропускает изменившуюся цену и другой промо-код. Открыть апертуру нельзя без
гейта: шум возвращается семью ложными падениями. Гейт разрывает этот компромисс —
ловим всё, отсеиваем осознанно.

Три решения, каждое принципиальное.

**Гейт умеет только подавлять.** Он никогда не поднимает severity и не делает
сравнение красным. Ложное подавление ограничено и видно в отчёте; выдуманное
падение — нет, и объяснить его нечем. Асимметрия намеренная.

**Признаки, а не пиксели.** Модель смотрит на то, что движок уже посчитал: ΔE,
SSIM, плотность краёв, площадь, заполненность, класс, положение. Это килобайты
коэффициентов вместо мегабайтов весов, инференс на numpy без новых зависимостей,
оффлайн-установка не толстеет — и, главное, решение читается словами: «подавлено,
потому что ΔE p95 ниже JND и плотность краёв высокая — это перерисованный текст».
Против «наша AI так решила» это и есть разница.

**severity в признаки не входит.** Это ровно та величина, чью политику мы
проверяем; включить её значило бы переучить модель на уже принятое решение.
Модель смотрит на то, из чего severity считается, и выносит второе мнение.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from pathlib import Path

from ..models import ChangeKind, DiffRegion

log = logging.getLogger("vistest.ai.gate")

# Порядок фиксирован: он записан в модель и не может меняться молча.
KINDS = ("noise", "antialias", "moved", "resized", "color", "text",
         "added", "removed", "content")

FEATURES = (
    "log_pixels",        # сколько пикселей маски — размер настоящего изменения
    "log_bbox",          # площадь рамки
    "fill_ratio",        # плотность: текст даёт разреженную рамку, блок — плотную
    "log_min_side",      # тонкая полоса или компактное пятно
    "aspect",            # вытянутость
    "area_frac",         # доля страницы
    "y_frac",            # выше сгиба или ниже
    "de_mean",           # средняя ΔE00 внутри маски
    "de_max",
    "de_ratio",          # de_max / de_mean — точечный всплеск или ровное поле
    "ssim_local",
    "edge_density",      # доля краевых пикселей: высокая — почти наверняка текст
    "move_mag",
    "match_score",
    "aligned",           # применялось ли глобальное выравнивание
    "size_changed",
    "log_siblings",      # сколько регионов нашлось всего
) + tuple(f"kind_{k}" for k in KINDS)


# Жёсткие правила поверх модели. Модель обучена на синтетике и увидит не всё;
# эти две границы не обсуждаются с ней вовсе, потому что цена ошибки за ними
# несоизмерима с выигрышем. Крупный регион и сменившаяся геометрия страницы —
# это то, ради чего визуальное тестирование существует.
MAX_SUPPRESSED_AREA_FRAC = 0.20


def guard(region, *, total_pixels: int, size_changed: bool) -> str:
    """Причина, по которой регион нельзя подавлять никогда. Пусто — можно."""
    if size_changed:
        return "page geometry changed"
    if total_pixels > 0 and (region.w * region.h) / total_pixels > MAX_SUPPRESSED_AREA_FRAC:
        return f"region covers over {MAX_SUPPRESSED_AREA_FRAC:.0%} of the page"
    return ""


@dataclass
class Decision:
    noise_probability: float
    suppress: bool
    reasons: list[str]

    def explain(self) -> str:
        return f"gate:{self.noise_probability:.2f} ({'; '.join(self.reasons)})"


# --------------------------------------------------------------------------- #
def features(region: DiffRegion, *, total_pixels: int, page_height: int,
             aligned: bool, size_changed: bool, siblings: int) -> list[float]:
    """Регион → вектор признаков в порядке `FEATURES`."""
    bbox = max(1, region.w * region.h)
    de_mean = float(region.de_mean)
    row = [
        math.log1p(max(0, region.pixel_count)),
        math.log1p(bbox),
        float(region.fill_ratio),
        math.log1p(max(0, min(region.w, region.h))),
        region.w / max(1, region.h),
        bbox / max(1, total_pixels),
        (region.y + region.h / 2) / max(1, page_height),
        de_mean,
        float(region.de_max),
        float(region.de_max) / max(de_mean, 0.1),
        float(region.ssim_local),
        float(region.edge_density),
        float(abs(region.moved_dx) + abs(region.moved_dy)),
        float(region.match_score),
        1.0 if aligned else 0.0,
        1.0 if size_changed else 0.0,
        math.log1p(max(0, siblings)),
    ]
    kind = region.kind.value if isinstance(region.kind, ChangeKind) else str(region.kind)
    row += [1.0 if kind == k else 0.0 for k in KINDS]
    return row


class RegionGate:
    """Логистическая регрессия на признаках. Инференс — одно скалярное произведение."""

    def __init__(self, weights, bias: float, mean, std, threshold: float,
                 feature_names=FEATURES, meta: dict | None = None):
        self.weights = list(weights)
        self.bias = float(bias)
        self.mean = list(mean)
        self.std = list(std)
        self.threshold = float(threshold)
        self.feature_names = tuple(feature_names)
        self.meta = meta or {}

    # ------------------------------------------------------------------ #
    @classmethod
    def load(cls, path: str | Path) -> RegionGate | None:
        """Модель с диска. Отсутствие файла — не ошибка, а выключенный гейт."""
        path = Path(path)
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text("utf-8"))
            gate = cls(
                weights=raw["weights"], bias=raw["bias"],
                mean=raw["mean"], std=raw["std"],
                threshold=raw.get("threshold", 0.5),
                feature_names=raw.get("features", FEATURES),
                meta=raw.get("meta", {}),
            )
        except Exception as e:
            log.warning("the gate model cannot be read (%s): %s", path, e)
            return None

        if len(gate.weights) != len(gate.feature_names):
            log.warning("the gate model has a different number of features — turning it off")
            return None
        if tuple(gate.feature_names) != FEATURES:
            # Набор признаков — часть контракта модели. Молча считать по чужому
            # порядку значило бы получать уверенные и неверные ответы.
            log.warning("the gate model was trained on a different feature set "
                        "— turning it off")
            return None
        return gate

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "features": list(self.feature_names),
            "weights": [round(float(w), 6) for w in self.weights],
            "bias": round(float(self.bias), 6),
            "mean": [round(float(m), 6) for m in self.mean],
            "std": [round(float(s), 6) for s in self.std],
            "threshold": self.threshold,
            "meta": self.meta,
        }, indent=1, ensure_ascii=False), encoding="utf-8")
        return path

    # ------------------------------------------------------------------ #
    def decide(self, row: list[float]) -> Decision:
        z = [(v - m) / (s if s > 1e-9 else 1.0)
             for v, m, s in zip(row, self.mean, self.std, strict=False)]
        contributions = [w * x for w, x in zip(self.weights, z, strict=False)]
        logit = self.bias + sum(contributions)
        p = 1.0 / (1.0 + math.exp(-max(-40.0, min(40.0, logit))))

        # Объяснение — три признака, сильнее прочих толкнувших решение.
        order = sorted(range(len(contributions)),
                       key=lambda i: -abs(contributions[i]))
        reasons = [
            f"{self.feature_names[i]}={row[i]:.3g}"
            for i in order[:3] if abs(contributions[i]) > 0.05
        ]
        return Decision(noise_probability=p, suppress=p >= self.threshold,
                        reasons=reasons)

    def top_weights(self, limit: int = 10) -> list[tuple[str, float]]:
        """Что модель вообще выучила. Нужно, чтобы её можно было оспорить."""
        pairs = list(zip(self.feature_names, self.weights, strict=False))
        return sorted(pairs, key=lambda p: -abs(p[1]))[:limit]


# --------------------------------------------------------------------------- #
def default_model_path() -> Path:
    """Модель, уезжающая вместе с пакетом."""
    return Path(__file__).with_name("region_gate.json")
