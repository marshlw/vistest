# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Доменные модели. Никаких зависимостей от cv2/playwright — только stdlib."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class ChangeKind(str, Enum):
    """Класс изменения региона. Порядок ~ по возрастанию подозрительности."""

    NOISE = "noise"              # отфильтровано (AA, нестабильность, perceptual gate)
    ANTIALIAS = "antialias"      # только субпиксельный рендер
    MOVED = "moved"              # тот же контент, другая позиция
    RESIZED = "resized"          # тот же контент, другой размер
    COLOR = "color"              # структура та же, цвет другой
    TEXT = "text"                # высокая плотность краёв => вероятно текст
    ADDED = "added"              # было пусто/фон -> появился контент
    REMOVED = "removed"          # был контент -> стало пусто/фон
    CONTENT = "content"          # всё остальное


class Verdict(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    NEW_BASELINE = "new_baseline"
    ERROR = "error"


@dataclass
class DiffRegion:
    x: int
    y: int
    w: int
    h: int
    kind: ChangeKind = ChangeKind.CONTENT
    severity: float = 0.0          # 0..100
    de_mean: float = 0.0           # средняя ΔE00 внутри маски региона
    de_max: float = 0.0
    ssim_local: float = 1.0
    pixel_count: int = 0           # число пикселей маски (не bbox!)
    fill_ratio: float = 0.0        # pixel_count / (w*h)
    moved_dx: int = 0
    moved_dy: int = 0
    match_score: float = 0.0       # NCC при поиске сдвига
    edge_density: float = 0.0
    selector: str | None = None    # из DOM-attribution
    element_text: str | None = None
    perceptual_distance: float | None = None
    gate_probability: float | None = None   # оценка обучаемого гейта, 0..1
    caption: str | None = None     # из LLM
    suppressed_by: str | None = None  # причина, если регион отброшен
    # Номер крупного плана этого региона (`region_<N>` в артефактах), если он
    # рисовался. Проставляется при рендере: сопоставлять картинку с регионом по
    # позиции в списке нельзя — список приходит отсортированным по severity из
    # SQL, а порядок сортировки на равных значениях не совпадает.
    region_index: int | None = None

    @property
    def area(self) -> int:
        return self.w * self.h

    @property
    def bbox(self) -> tuple[int, int, int, int]:
        return self.x, self.y, self.w, self.h

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["kind"] = self.kind.value
        d["area"] = self.area
        return d


@dataclass
class CompareResult:
    name: str
    verdict: Verdict
    regions: list[DiffRegion] = field(default_factory=list)
    suppressed: list[DiffRegion] = field(default_factory=list)

    # глобальные метрики
    ssim_global: float = 1.0
    de_mean: float = 0.0
    de_p95: float = 0.0
    changed_pixels: int = 0
    total_pixels: int = 0
    changed_area_pct: float = 0.0
    max_severity: float = 0.0

    # выравнивание / размеры
    align_dx: float = 0.0
    align_dy: float = 0.0
    aligned: bool = False
    size_expected: tuple[int, int] = (0, 0)
    size_actual: tuple[int, int] = (0, 0)
    size_changed: bool = False

    duration_ms: int = 0
    artifacts: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return self.verdict is Verdict.FAIL

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "verdict": self.verdict.value,
            "regions": [r.to_dict() for r in self.regions],
            "suppressed": [r.to_dict() for r in self.suppressed],
            "metrics": {
                "ssim_global": round(self.ssim_global, 6),
                "de_mean": round(self.de_mean, 4),
                "de_p95": round(self.de_p95, 4),
                "changed_pixels": self.changed_pixels,
                "total_pixels": self.total_pixels,
                "changed_area_pct": round(self.changed_area_pct, 6),
                "max_severity": round(self.max_severity, 2),
            },
            "alignment": {
                "dx": round(self.align_dx, 3),
                "dy": round(self.align_dy, 3),
                "applied": self.aligned,
            },
            "size": {
                "expected": list(self.size_expected),
                "actual": list(self.size_actual),
                "changed": self.size_changed,
            },
            "duration_ms": self.duration_ms,
            "artifacts": self.artifacts,
            "notes": self.notes,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    def summary(self) -> str:
        """Человекочитаемое сообщение для AssertionError / лога."""
        lines = [
            f"Visual mismatch: {self.name}",
            f"  SSIM={self.ssim_global:.5f}  ΔE00 mean={self.de_mean:.2f}"
            f" p95={self.de_p95:.2f}",
            f"  changed area={self.changed_area_pct:.3f}%  "
            f"({self.changed_pixels}/{self.total_pixels} px)",
            f"  max severity={self.max_severity:.1f}  regions={len(self.regions)}"
            f" (suppressed={len(self.suppressed)})",
        ]
        if self.aligned:
            lines.append(f"  alignment applied: dx={self.align_dx:+.2f}"
                         f" dy={self.align_dy:+.2f}")
        if self.size_changed:
            lines.append(f"  SIZE CHANGED: {self.size_expected} -> {self.size_actual}")
        for r in sorted(self.regions, key=lambda x: -x.severity)[:10]:
            extra = []
            if r.kind is ChangeKind.MOVED:
                extra.append(f"shift=({r.moved_dx:+d},{r.moved_dy:+d})")
            if r.selector:
                extra.append(r.selector)
            if r.element_text:
                extra.append(f'"{r.element_text}"')
            if r.caption:
                extra.append(r.caption)
            tail = ("  " + " | ".join(extra)) if extra else ""
            lines.append(
                f"    [{r.kind.value:9s}] sev={r.severity:5.1f} "
                f"@({r.x},{r.y}) {r.w}x{r.h} ΔE={r.de_mean:.1f}{tail}"
            )
        for n in self.notes:
            lines.append(f"  note: {n}")
        return "\n".join(lines)


@dataclass
class Shot:
    """Результат захвата страницы."""

    image_path: str
    width: int
    height: int
    device_scale: float = 1.0
    stability_mask_path: str | None = None
    dom_path: str | None = None
    unstable_ratio: float = 0.0
    meta: dict[str, Any] = field(default_factory=dict)
