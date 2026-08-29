# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Классификация регионов и расчёт severity.

Идея: бинарного «значимо / незначимо» недостаточно. Сдвиг подписи на 2 px и
пропавшая кнопка «Оплатить» — оба «значимы» по площади, но это события разного
масштаба. Поэтому каждому региону присваивается класс и непрерывная severity
0..100, а порог падения задаётся политикой в конфиге.
"""

from __future__ import annotations

import numpy as np

from ..models import ChangeKind, DiffRegion
from .align import find_local_shift
from .structure import edge_density, local_ssim

# Насколько класс изменения важен сам по себе.
KIND_WEIGHT: dict[ChangeKind, float] = {
    ChangeKind.NOISE: 0.0,
    ChangeKind.ANTIALIAS: 0.0,
    ChangeKind.MOVED: 0.35,      # переопределяется diff.moved_severity_scale
    ChangeKind.RESIZED: 0.80,
    ChangeKind.COLOR: 0.90,
    ChangeKind.TEXT: 1.00,
    ChangeKind.CONTENT: 1.00,
    ChangeKind.ADDED: 1.20,
    ChangeKind.REMOVED: 1.25,    # исчезнувший элемент — почти всегда баг
}

_FLAT_STD = 4.0          # ниже — считаем область однородной («пусто»)
_FLAT_EDGES = 0.012
_TEXT_EDGES = 0.10       # выше — плотность краёв как у текста
_COLOR_STRUCT_OK = 0.90  # структура сохранена => изменился только цвет


def classify_region(
    box: tuple[int, int, int, int],
    pixel_count: int,
    fill_ratio: float,
    *,
    gray_exp: np.ndarray,
    gray_act: np.ndarray,
    de_map: np.ndarray,
    mask: np.ndarray,
    total_pixels: int,
    detect_moved: bool = True,
    move_search_px: int = 64,
    move_match_threshold: float = 0.93,
    above_fold_px: int = 900,
    above_fold_weight: float = 1.5,
    moved_scale: float = 0.35,
) -> DiffRegion:
    x, y, w, h = box
    crop_exp = gray_exp[y:y + h, x:x + w]
    crop_act = gray_act[y:y + h, x:x + w]
    crop_mask = mask[y:y + h, x:x + w]
    crop_de = de_map[y:y + h, x:x + w]

    de_vals = crop_de[crop_mask] if crop_mask.any() else crop_de
    de_mean = float(de_vals.mean()) if de_vals.size else 0.0
    de_max = float(de_vals.max()) if de_vals.size else 0.0

    ssim_local = local_ssim(crop_exp, crop_act)
    ed_exp = edge_density(crop_exp)
    ed_act = edge_density(crop_act)

    region = DiffRegion(
        x=x, y=y, w=w, h=h,
        de_mean=de_mean, de_max=de_max,
        ssim_local=ssim_local,
        pixel_count=pixel_count,
        fill_ratio=fill_ratio,
        edge_density=max(ed_exp, ed_act),
    )

    # --- 1. Не уехал ли блок целиком? ---
    if detect_moved:
        dx, dy, ncc = find_local_shift(
            gray_exp, gray_act, (x, y, w, h), search_px=move_search_px
        )
        if ncc >= move_match_threshold and (dx, dy) != (0, 0):
            region.kind = ChangeKind.MOVED
            region.moved_dx, region.moved_dy, region.match_score = dx, dy, ncc
            region.severity = _severity(region, total_pixels, moved_scale,
                                        above_fold_px, above_fold_weight)
            return region

    # --- 2. Появление / исчезновение ---
    std_exp = float(crop_exp.std()) if crop_exp.size else 0.0
    std_act = float(crop_act.std()) if crop_act.size else 0.0
    empty_exp = std_exp < _FLAT_STD and ed_exp < _FLAT_EDGES
    empty_act = std_act < _FLAT_STD and ed_act < _FLAT_EDGES

    if empty_exp and not empty_act:
        region.kind = ChangeKind.ADDED
    elif empty_act and not empty_exp:
        region.kind = ChangeKind.REMOVED
    # --- 3. Только цвет: структура на месте ---
    elif ssim_local >= _COLOR_STRUCT_OK and de_mean >= 2.0:
        region.kind = ChangeKind.COLOR
    # --- 4. Текст ---
    elif max(ed_exp, ed_act) >= _TEXT_EDGES:
        region.kind = ChangeKind.TEXT
    else:
        region.kind = ChangeKind.CONTENT

    weight = KIND_WEIGHT.get(region.kind, 1.0)
    region.severity = _severity(region, total_pixels, weight,
                                above_fold_px, above_fold_weight)
    return region


def _severity(
    r: DiffRegion,
    total_pixels: int,
    kind_weight: float,
    above_fold_px: int,
    above_fold_weight: float,
) -> float:
    """0..100. Три ортогональных вклада + вес класса + вес позиции.

    size   — сколько пикселей реально изменилось (маска, не bbox).
             Референс — 0.2% площади экрана: столько занимает заметный элемент.
    color  — насколько сильно изменился цвет (ΔE 12 = «явно другой цвет»).
    struct — насколько разошлась структура.
    """
    ref = max(total_pixels * 0.002, 400.0)
    size_term = min(1.0, (r.pixel_count / ref) ** 0.5)
    color_term = min(1.0, r.de_mean / 12.0)
    struct_term = min(1.0, max(0.0, 1.0 - r.ssim_local) / 0.35)

    base = 0.45 * size_term + 0.30 * color_term + 0.25 * struct_term
    pos_weight = above_fold_weight if r.y < above_fold_px else 1.0

    return float(np.clip(100.0 * base * kind_weight * pos_weight, 0.0, 100.0))


def recompute_severity(r: DiffRegion, total_pixels: int, cfg) -> float:
    weight = (cfg.moved_severity_scale if r.kind is ChangeKind.MOVED
              else KIND_WEIGHT.get(r.kind, 1.0))
    r.severity = _severity(r, total_pixels, weight,
                           cfg.above_fold_px, cfg.above_fold_weight)
    return r.severity
