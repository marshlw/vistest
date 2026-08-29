# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Оркестратор сравнения. Здесь собирается весь каскад."""

from __future__ import annotations

import time

import numpy as np

from ..config import DiffConfig
from ..models import ChangeKind, CompareResult, DiffRegion, Verdict
from . import align as _align
from . import antialias as _aa
from . import classify as _cls
from . import color as _color
from . import segment as _seg
from . import structure as _struct


def compare(
    expected_rgb: np.ndarray,
    actual_rgb: np.ndarray,
    *,
    cfg: DiffConfig | None = None,
    name: str = "snapshot",
    ignore_mask: np.ndarray | None = None,
    ai_hooks=None,
) -> CompareResult:
    """Сравнить два RGB-изображения (uint8, H×W×3).

    ignore_mask: bool-маска «сюда не смотреть» (маски пользователя,
                 stability-маска, вечные ignore-регионы из истории).
    ai_hooks:    объект с необязательными методами
                 `perceptual_filter(regions, exp, act)` и
                 `attribute(regions)`. См. vistest.ai.pipeline.AIPipeline.
    """
    t0 = time.perf_counter()
    cfg = cfg or DiffConfig()

    res = CompareResult(name=name, verdict=Verdict.PASS)
    res.size_expected = (int(expected_rgb.shape[1]), int(expected_rgb.shape[0]))
    res.size_actual = (int(actual_rgb.shape[1]), int(actual_rgb.shape[0]))

    # ---------- 0. Размеры ----------
    exp, act, padded = _align.reconcile_sizes(expected_rgb, actual_rgb)
    if padded:
        res.size_changed = True
        res.notes.append(
            f"Size changed: {res.size_expected} → {res.size_actual}. "
            "The images were padded to a common size (not cropped), "
            "the difference goes into the diff."
        )
    if ignore_mask is not None and ignore_mask.shape != exp.shape[:2]:
        ignore_mask = _fit_mask(ignore_mask, exp.shape[:2])

    h, w = exp.shape[:2]
    res.total_pixels = h * w

    # ---------- 1. Перцептивная светлота ----------
    lab_exp = _color.srgb_to_lab(exp)
    gray_exp = np.clip(lab_exp[:, :, 0] * 2.55, 0, 255).astype(np.uint8)
    gray_act_raw = _color.luminance(act)

    # ---------- 2. Глобальное выравнивание ----------
    act_aligned, al = _align.align_images(
        exp, act, gray_exp, gray_act_raw,
        enabled=cfg.align_enabled,
        max_shift_px=cfg.max_align_shift_px,
    )
    res.align_dx, res.align_dy, res.aligned = al.dx, al.dy, al.applied
    if al.reason:
        res.notes.append(f"Alignment: {al.reason}")

    lab_act = _color.srgb_to_lab(act_aligned)
    gray_act = np.clip(lab_act[:, :, 0] * 2.55, 0, 255).astype(np.uint8)

    # ---------- 3. Перцептивная и структурная карты ----------
    de_map = _color.delta_e_ciede2000(lab_exp, lab_act)
    smap, ssim_global = _struct.ssim_map(gray_exp, gray_act)
    res.ssim_global = ssim_global

    color_hit = de_map > cfg.delta_e_threshold
    struct_hit = smap < cfg.ssim_threshold

    # ---------- 4. Кандидаты: КОНСЕНСУС, а не OR ----------
    if cfg.require_consensus:
        # Исключение: очень сильная цветовая разница на плоской заливке
        # структуру не меняет (SSIM остаётся высоким), поэтому пропускаем её
        # мимо консенсуса — иначе смена цвета фона осталась бы незамеченной.
        strong_color = de_map > (cfg.delta_e_threshold * 4.0)
        candidate = (color_hit & struct_hit) | strong_color
    else:
        candidate = color_hit | struct_hit

    # ---------- 5. Подавление известного шума ----------
    suppress = np.zeros((h, w), dtype=bool)
    if cfg.antialias_filter:
        suppress |= _aa.antialias_mask(gray_exp, gray_act, tolerance=cfg.aa_tolerance)
    if ignore_mask is not None:
        suppress |= ignore_mask

    mask = candidate & ~suppress

    de_masked = de_map[mask]
    res.changed_pixels = int(mask.sum())
    res.de_mean = float(de_masked.mean()) if de_masked.size else 0.0
    res.de_p95 = float(np.percentile(de_masked, 95)) if de_masked.size else 0.0
    res.changed_area_pct = 100.0 * res.changed_pixels / max(res.total_pixels, 1)

    # ---------- 6. Сегментация ----------
    cleaned = _seg.clean_mask(mask, open_px=cfg.morph_open_px, close_px=cfg.morph_close_px)
    _, boxes = _seg.components(
        cleaned,
        min_pixels=cfg.min_region_px,
        min_fill=cfg.min_region_fill,
        max_regions=cfg.max_regions,
    )
    boxes = _seg.merge_close_boxes(boxes, gap=max(cfg.morph_close_px * 2, 10))

    # ---------- 7. Классификация ----------
    regions: list[DiffRegion] = []
    for (x, y, bw, bh, px, fill, _lbl) in boxes:
        regions.append(
            _cls.classify_region(
                (x, y, bw, bh), px, fill,
                gray_exp=gray_exp, gray_act=gray_act,
                de_map=de_map, mask=cleaned,
                total_pixels=res.total_pixels,
                detect_moved=cfg.detect_moved,
                move_search_px=cfg.move_search_px,
                move_match_threshold=cfg.move_match_threshold,
                above_fold_px=cfg.above_fold_px,
                above_fold_weight=cfg.above_fold_weight,
                moved_scale=cfg.moved_severity_scale,
            )
        )

    # ---------- 8. AI-слой (опционально) ----------
    if ai_hooks is not None and regions:
        try:
            regions = ai_hooks.refine(regions, exp, act_aligned, res)
        except Exception as e:  # AI никогда не должен ронять тест
            res.notes.append(f"AI layer skipped: {type(e).__name__}: {e}")

    # ---------- 9. Разделение и вердикт ----------
    ignore_kinds = {ChangeKind(k) for k in cfg.ignore_kinds}
    for r in regions:
        if r.kind in ignore_kinds or r.suppressed_by:
            res.suppressed.append(r)
        else:
            res.regions.append(r)

    res.max_severity = max((r.severity for r in res.regions), default=0.0)
    res.verdict = _verdict(res, cfg)
    res.duration_ms = int((time.perf_counter() - t0) * 1000)

    # Сохраняем карты для рендера артефактов (не сериализуются в JSON).
    res.artifacts["_de_map"] = de_map          # type: ignore[assignment]
    res.artifacts["_mask"] = cleaned           # type: ignore[assignment]
    res.artifacts["_aligned_actual"] = act_aligned  # type: ignore[assignment]
    res.artifacts["_expected"] = exp           # type: ignore[assignment]
    return res


def _verdict(res: CompareResult, cfg: DiffConfig) -> Verdict:
    reasons = []
    if cfg.fail_on_size_change and res.size_changed:
        dw = abs(res.size_actual[0] - res.size_expected[0])
        dh = abs(res.size_actual[1] - res.size_expected[1])
        if max(dw, dh) > cfg.size_tolerance_px:
            reasons.append(f"size changed by {dw}×{dh}px")

    if res.max_severity >= cfg.fail_severity:
        reasons.append(f"severity {res.max_severity:.1f} ≥ {cfg.fail_severity}")

    if res.changed_area_pct >= cfg.max_changed_area_pct:
        # Площадь считается до сегментации: в неё попадают и пиксели, которые
        # дальше отброшены как слишком мелкие. Если после фильтрации не
        # осталось ни одного региона — движок сам признал всё найденное
        # незначимым, и падать по сумме отброшенного значит спорить с
        # собственным решением.
        diffuse = not res.regions
        huge = res.changed_area_pct >= cfg.area_hard_fail_pct

        if diffuse and cfg.area_requires_region and not huge:
            res.notes.append(
                f"Changed {res.changed_area_pct:.3f}% ≥ "
                f"{cfg.max_changed_area_pct}%, but not a single region passed "
                "filtering — the divergence is smeared out and insignificant, "
                "no failure. This is what a subpixel difference between "
                "snapshots from different capture pipelines looks like. "
                "Turn off with: area_requires_region=false"
            )
        else:
            reasons.append(
                f"changed {res.changed_area_pct:.3f}% ≥ "
                f"{cfg.max_changed_area_pct}%"
                + ("  (no significant regions, but the area is large)"
                   if diffuse and huge else "")
            )

    if reasons:
        res.notes.append("Reason for the failure: " + "; ".join(reasons))
        return Verdict.FAIL
    return Verdict.PASS


def _fit_mask(mask: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    """Подгоняет маску под размер изображения (обрезка/дополнение False)."""
    out = np.zeros(shape, dtype=bool)
    hh = min(shape[0], mask.shape[0])
    ww = min(shape[1], mask.shape[1])
    out[:hh, :ww] = mask[:hh, :ww].astype(bool)
    return out


def strip_internal(res: CompareResult) -> CompareResult:
    """Убрать numpy-массивы из artifacts перед сериализацией."""
    for k in list(res.artifacts):
        if k.startswith("_"):
            res.artifacts.pop(k)
    return res
