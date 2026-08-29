# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Сборка AI-слоя в один объект-хук для comparator.compare(ai_hooks=...).

Порядок важен:
  1. attribution   — дёшево, без сети, даёт имена элементам
  2. perceptual    — локальная модель, отсеивает «математически заметное,
                     но человеком незаметное»
  3. captioner     — дорого и в сети, только для того, что выжило

Любая ступень может отсутствовать. Ни одна не имеет права уронить прогон.
"""

from __future__ import annotations

import logging

import numpy as np

from ..config import AIConfig
from ..models import ChangeKind, CompareResult, DiffRegion
from .attribution import attribute, diagnose, dom_changes
from .captioner import Captioner
from .gate import RegionGate, default_model_path, features, guard
from .perceptual import PerceptualModel

log = logging.getLogger("vistest.ai")


class AIPipeline:
    def __init__(
        self,
        cfg: AIConfig | None = None,
        *,
        dom_expected: dict | None = None,
        dom_actual: dict | None = None,
        gate: RegionGate | None = None,
    ):
        self.cfg = cfg or AIConfig()
        self.dom_expected = dom_expected
        self.dom_actual = dom_actual
        self._gate = gate if gate is not None else self._load_gate()
        self._perceptual = (
            PerceptualModel(self.cfg.perceptual_model_path)
            if self.cfg.perceptual_enabled else None
        )
        self._captioner = Captioner(self.cfg) if self.cfg.captioner_enabled else None

    def _load_gate(self) -> RegionGate | None:
        if not self.cfg.gate_enabled:
            return None
        path = self.cfg.gate_model_path or default_model_path()
        gate = RegionGate.load(path)
        if gate is not None and self.cfg.gate_threshold > 0:
            gate.threshold = self.cfg.gate_threshold
        return gate

    # ------------------------------------------------------------------ #
    def refine(
        self,
        regions: list[DiffRegion],
        expected: np.ndarray,
        actual: np.ndarray,
        result: CompareResult,
    ) -> list[DiffRegion]:
        if self.cfg.attribution_enabled:
            try:
                regions = attribute(
                    regions, self.dom_expected, self.dom_actual,
                    min_coverage=self.cfg.attribution_min_iou,
                )
                changes = dom_changes(self.dom_expected, self.dom_actual)
                if changes.get("counts"):
                    c = changes["counts"]
                    result.artifacts["_dom_changes"] = changes  # type: ignore[assignment]
                    if any(c.values()):
                        result.notes.append(
                            f"DOM: +{c['added']} elements, -{c['removed']}, "
                            f"moved {c['moved']}"
                        )
                # Диагноз важнее констатации: «регион изменился» человек видит
                # и сам, а вот «это дата, вот что с ней делать» — нет.
                result.notes.extend(diagnose(regions))
            except Exception as e:
                log.warning("attribution: %s", e)

        if self._gate is not None:
            try:
                self._apply_gate(regions, result)
            except Exception as e:
                log.warning("gate: %s", e)

        if self._perceptual is not None:
            try:
                self._apply_perceptual(regions, expected, actual)
            except Exception as e:
                log.warning("perceptual: %s", e)

        if self._captioner is not None:
            try:
                from ..render.artifacts import draw_heatmap

                de_map = result.artifacts.get("_de_map")
                heat = draw_heatmap(actual, de_map) if de_map is not None else actual
                self._captioner.annotate(regions, expected, actual, heat)
            except Exception as e:
                log.warning("captioner: %s", e)

        return regions

    # ------------------------------------------------------------------ #
    def _apply_gate(self, regions, result) -> None:
        """Понизить до NOISE регионы, которые модель считает шумом.

        Только понизить. Гейт не имеет права поднять severity или сделать
        сравнение красным: ложное подавление ограничено и видно в отчёте,
        выдуманное падение — нет.
        """
        gate = self._gate
        if gate is None or not regions:
            return

        page_h = max(1, max((r.y + r.h) for r in regions))
        total = result.total_pixels or page_h
        hits = 0
        held = 0
        for r in regions:
            if r.kind in (ChangeKind.NOISE, ChangeKind.ANTIALIAS):
                continue
            row = features(r, total_pixels=total, page_height=page_h,
                           aligned=result.aligned,
                           size_changed=result.size_changed,
                           siblings=len(regions))
            decision = gate.decide(row)
            r.gate_probability = decision.noise_probability
            if not decision.suppress:
                continue
            # Модель обучена на синтетике и увидит не всё. За двумя границами
            # с ней не спорят: крупный регион и сменившаяся геометрия страницы —
            # это то, ради чего визуальное тестирование существует.
            blocked = guard(r, total_pixels=total,
                            size_changed=result.size_changed)
            if blocked:
                held += 1
                continue
            r.kind = ChangeKind.NOISE
            r.severity = 0.0
            r.suppressed_by = decision.explain()
            hits += 1

        if held:
            result.notes.append(
                f"Learned gate: {held} region(s) looked like noise to the model "
                "but were kept — a safety rule outranks it.")
        if hits:
            result.notes.append(
                f"Learned gate: {hits} region(s) recognized as noise and "
                "suppressed. It only suppresses — it never fails a snapshot.")

    # ------------------------------------------------------------------ #
    def _apply_perceptual(self, regions, expected, actual) -> None:
        model = self._perceptual
        if model is None or not model.available:
            return

        candidates, pairs = [], []
        for r in regions:
            if r.kind in (ChangeKind.NOISE, ChangeKind.ANTIALIAS):
                continue
            if min(r.w, r.h) < self.cfg.perceptual_min_region_px:
                continue
            pad = 8
            h, w = expected.shape[:2]
            x0, y0 = max(0, r.x - pad), max(0, r.y - pad)
            x1, y1 = min(w, r.x + r.w + pad), min(h, r.y + r.h + pad)
            if x1 - x0 < 4 or y1 - y0 < 4:
                continue
            candidates.append(r)
            pairs.append((expected[y0:y1, x0:x1], actual[y0:y1, x0:x1]))

        dists = model.distances(pairs)
        if dists is None:
            return
        for r, d in zip(candidates, dists, strict=False):
            r.perceptual_distance = d
            if d < self.cfg.perceptual_tolerance:
                # Человек этого не увидит: пережатие, дизеринг, другой AA.
                r.kind = ChangeKind.NOISE
                r.suppressed_by = f"perceptual:{d:.4f}<{self.cfg.perceptual_tolerance}"
                r.severity = 0.0
