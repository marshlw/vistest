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


#  Above this many elements an array is described rather than written out.
#  A full-page ΔE map is 1440x20000 floats: as nested JSON lists that is
#  hundreds of megabytes attached to a test report, which is its own outage.
_MAX_INLINE_ELEMENTS = 64


def _json_default(value):
    """Fallback encoder for values json does not know.

    Metrics travel through numpy on their way here, and a numpy scalar or array
    is not JSON-serialisable. This used to raise from ``to_json`` — which is
    called while attaching the diff to a report, i.e. exactly when a test has
    already failed and the person most needs to see something. A serialiser
    feeding a report must not be able to fail; anything unknown is degraded to
    a plain value rather than raising.

    Note this is a safety net, not a licence: a metric field holding an array
    means the cascade put the wrong thing there, and that is a separate bug.

    Which is why a large array is described, not unrolled. Writing one out
    would replace a crash with a report nobody can open, and the description
    says plainly what was found and where — the point of a safety net is to
    make the underlying mistake visible, not comfortable.
    """
    shape = getattr(value, "shape", None)
    size = getattr(value, "size", None)
    if shape is not None and size is not None and size > _MAX_INLINE_ELEMENTS:
        return (f"<{type(value).__name__} shape={tuple(shape)} "
                f"dtype={getattr(value, 'dtype', '?')} — not a scalar, this "
                "field was filled with the wrong thing>")

    tolist = getattr(value, "tolist", None)
    if callable(tolist):          # numpy arrays and numpy scalars alike
        return tolist()
    return str(value)


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
    caption: str | None = None     # внешний аннотатор, vistest/ai/hooks.py
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
    #  name -> path on disk. Strings, and only strings: this dictionary is
    #  serialised into every report.
    artifacts: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    #  The maps the cascade produced, handed to the renderer and to `doctor`:
    #  `expected`, `aligned_actual`, `de_map`, `mask`. Full-frame numpy arrays,
    #  never serialised, and dropped by `strip_internal` once the pictures are
    #  drawn.
    #
    #  They used to live in `artifacts` under underscore-prefixed keys, with a
    #  `# type: ignore[assignment]` on every write. It worked for as long as
    #  every caller remembered to strip them first — and the day one did not,
    #  `to_json` raised `Object of type ndarray is not JSON serializable` from
    #  inside a report hook, which pytest turns into INTERNALERROR: a run of
    #  eleven tests died at the fifth because one screenshot legitimately
    #  differed. A field that says `dict[str, str]` and holds arrays is a trap
    #  set for whoever writes the next caller.
    maps: dict[str, Any] = field(default_factory=dict, repr=False,
                                 compare=False)

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
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False,
                          default=_json_default)

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
