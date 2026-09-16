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


# The two safety rules that outrank any scorer live in the core now
# (`vistest.plugins.runtime.guard`): they constrain every scorer, not only this
# one. Re-exported for code that imported them from here.


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


# --------------------------------------------------------------------------- #
#  The gate behind the plugin contract
# --------------------------------------------------------------------------- #
def calibrate(value: float, boundary: float) -> float:
    """Map `value` in [0, 1] so that `boundary` lands on 0.5, monotonically.

    The plugin contract puts every scorer's decision boundary at 0.5; the gate
    has its own threshold, chosen when the model was trained.
    """
    value = min(1.0, max(0.0, float(value)))
    boundary = min(1.0 - 1e-6, max(1e-6, float(boundary)))
    if value <= boundary:
        #  At the boundary itself the gate suppressed; keep it on that side.
        return min(0.5 * value / boundary, 0.4999)
    return 0.5 + 0.5 * (value - boundary) / (1.0 - boundary)


class GateScorer:
    """`RegionScorer` and `RegionAnnotator` over `RegionGate`.

    As a scorer it answers "how likely is this region real": one minus the
    model's noise probability, calibrated so that the model's own threshold is
    0.5. As an annotator it says which features pushed the estimate, for the
    regions it scored in this thread — an estimate nobody can argue with is
    the thing this model was built not to be.

    Honours the `ai:` knobs it always had: `gate_enabled`, `gate_model_path`,
    `gate_threshold`. Disabled, or with no readable model, it abstains.
    """

    name = "gate"

    def __init__(self, gate: RegionGate | None = None):
        import threading

        self._fixed = gate
        self._models: dict[str, RegionGate | None] = {}
        self._lock = threading.Lock()
        self._local = threading.local()

    def _gate_for(self, settings) -> RegionGate | None:
        if self._fixed is not None:
            return self._fixed
        if settings is not None and not getattr(settings, "gate_enabled", True):
            return None
        path = str(getattr(settings, "gate_model_path", "") or default_model_path())
        with self._lock:
            if path not in self._models:
                self._models[path] = RegionGate.load(path)
            return self._models[path]

    def score(self, regions, ctx):
        gate = self._gate_for(ctx.settings)
        self._local.reasons = {}
        if gate is None:
            return None
        threshold = gate.threshold
        override = float(getattr(ctx.settings, "gate_threshold", 0.0) or 0.0)
        if self._fixed is None and override > 0:
            threshold = override

        out = []
        for r in regions:
            row = features(r, total_pixels=ctx.total_pixels,
                           page_height=ctx.page_height, aligned=ctx.aligned,
                           size_changed=ctx.size_changed,
                           siblings=ctx.region_count)
            decision = gate.decide(row)
            self._local.reasons[id(r)] = decision
            out.append(calibrate(1.0 - decision.noise_probability, 1.0 - threshold))
        return out

    def annotate(self, region, ctx):
        from ..plugins.api import Annotation

        decision = getattr(self._local, "reasons", {}).get(id(region))
        if decision is None:
            return ()
        why = "; ".join(decision.reasons) or "no single feature stood out"
        return [Annotation(
            kind="noise-probability",
            text=f"noise probability {decision.noise_probability:.2f} ({why})",
            value=round(decision.noise_probability, 4))]
