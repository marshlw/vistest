# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Перцептивное цветовое пространство и CIEDE2000.

Зачем: `cv2.absdiff` в sRGB не имеет отношения к тому, что видит человек.
Разница в 10 единиц RGB в тенях бросается в глаза, в светах — невидима.
CIEDE2000 — стандарт CIE, где 1.0 ≈ порог различимости (JND) независимо
от того, в какой части пространства находятся цвета.

Всё векторизовано и считается блоками по строкам, чтобы полностраничный
скриншот 1920×8000 не съел всю память на промежуточных массивах.
"""

from __future__ import annotations

import numpy as np

try:  # cv2 даёт быстрый и точный float32-путь
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

_POW25_7 = 25.0 ** 7
_DEG = np.float32(180.0 / np.pi)
_RAD = np.float32(np.pi / 180.0)

# Матрица sRGB(linear) -> XYZ, D65
_M = np.array(
    [[0.4124564, 0.3575761, 0.1804375],
     [0.2126729, 0.7151522, 0.0721750],
     [0.0193339, 0.1191920, 0.9503041]],
    dtype=np.float32,
)
_WHITE = np.array([0.95047, 1.00000, 1.08883], dtype=np.float32)


def srgb_to_lab(rgb_u8: np.ndarray) -> np.ndarray:
    """uint8 RGB (H,W,3) -> float32 CIELAB (H,W,3), L∈[0,100], a,b∈[-128,127]."""
    if rgb_u8.ndim != 3 or rgb_u8.shape[2] != 3:
        raise ValueError(f"Expected (H,W,3) RGB, got {rgb_u8.shape}")

    rgb = rgb_u8.astype(np.float32) / np.float32(255.0)

    if cv2 is not None:
        # cv2 ждёт float32 в [0,1]; отдаёт L∈[0,100], a,b∈[-127,127]
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2Lab)

    # --- numpy fallback ---
    lin = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    xyz = lin.reshape(-1, 3) @ _M.T
    xyz /= _WHITE
    eps, kappa = np.float32(216 / 24389), np.float32(24389 / 27)
    f = np.where(xyz > eps, np.cbrt(xyz), (kappa * xyz + 16.0) / 116.0)
    fx, fy, fz = f[:, 0], f[:, 1], f[:, 2]
    lab = np.empty_like(f)
    lab[:, 0] = 116.0 * fy - 16.0
    lab[:, 1] = 500.0 * (fx - fy)
    lab[:, 2] = 200.0 * (fy - fz)
    return lab.reshape(rgb.shape).astype(np.float32)


def luminance(rgb_u8: np.ndarray) -> np.ndarray:
    """Канал L* (перцептивная светлота) как uint8 — вход для SSIM/Canny.

    Именно L*, а не cv2 grayscale: grayscale линеен по sRGB и искажает
    контраст в тенях, из-за чего SSIM «не замечает» изменения на тёмных темах.
    """
    lab = srgb_to_lab(rgb_u8)
    return np.clip(lab[:, :, 0] * 2.55, 0, 255).astype(np.uint8)


def delta_e_ciede2000(
    lab1: np.ndarray,
    lab2: np.ndarray,
    *,
    kL: float = 1.0,
    kC: float = 1.0,
    kH: float = 1.0,
    chunk_rows: int = 512,
) -> np.ndarray:
    """Попиксельная ΔE00 между двумя Lab-изображениями -> float32 (H,W)."""
    if lab1.shape != lab2.shape:
        raise ValueError(f"Shapes differ: {lab1.shape} vs {lab2.shape}")

    h = lab1.shape[0]
    out = np.empty(lab1.shape[:2], dtype=np.float32)
    for y0 in range(0, h, chunk_rows):
        y1 = min(y0 + chunk_rows, h)
        out[y0:y1] = _de2000_block(lab1[y0:y1], lab2[y0:y1], kL, kC, kH)
    return out


def _de2000_block(lab1, lab2, kL, kC, kH):
    L1, a1, b1 = lab1[..., 0], lab1[..., 1], lab1[..., 2]
    L2, a2, b2 = lab2[..., 0], lab2[..., 1], lab2[..., 2]

    C1 = np.hypot(a1, b1)
    C2 = np.hypot(a2, b2)
    C_bar = 0.5 * (C1 + C2)

    C_bar7 = C_bar ** 7
    G = 0.5 * (1.0 - np.sqrt(C_bar7 / (C_bar7 + _POW25_7)))

    a1p = (1.0 + G) * a1
    a2p = (1.0 + G) * a2
    C1p = np.hypot(a1p, b1)
    C2p = np.hypot(a2p, b2)

    h1p = np.degrees(np.arctan2(b1, a1p)) % 360.0
    h2p = np.degrees(np.arctan2(b2, a2p)) % 360.0
    # Определение: при нулевой хроме оттенок не определён -> 0
    zero1 = (np.abs(a1p) + np.abs(b1)) == 0
    zero2 = (np.abs(a2p) + np.abs(b2)) == 0
    h1p = np.where(zero1, 0.0, h1p)
    h2p = np.where(zero2, 0.0, h2p)

    dLp = L2 - L1
    dCp = C2p - C1p

    Cprod_zero = (C1p * C2p) == 0
    dh = h2p - h1p
    dhp = np.where(dh > 180.0, dh - 360.0, np.where(dh < -180.0, dh + 360.0, dh))
    dhp = np.where(Cprod_zero, 0.0, dhp)
    dHp = 2.0 * np.sqrt(C1p * C2p) * np.sin(np.radians(dhp) * 0.5)

    Lp_bar = 0.5 * (L1 + L2)
    Cp_bar = 0.5 * (C1p + C2p)

    hsum = h1p + h2p
    habs = np.abs(h1p - h2p)
    hp_bar = np.where(
        Cprod_zero, hsum,
        np.where(
            habs <= 180.0, 0.5 * hsum,
            np.where(hsum < 360.0, 0.5 * (hsum + 360.0), 0.5 * (hsum - 360.0)),
        ),
    )

    T = (1.0
         - 0.17 * np.cos(np.radians(hp_bar - 30.0))
         + 0.24 * np.cos(np.radians(2.0 * hp_bar))
         + 0.32 * np.cos(np.radians(3.0 * hp_bar + 6.0))
         - 0.20 * np.cos(np.radians(4.0 * hp_bar - 63.0)))

    d_theta = 30.0 * np.exp(-(((hp_bar - 275.0) / 25.0) ** 2))
    Cp_bar7 = Cp_bar ** 7
    R_C = 2.0 * np.sqrt(Cp_bar7 / (Cp_bar7 + _POW25_7))
    R_T = -np.sin(np.radians(2.0 * d_theta)) * R_C

    dL50 = Lp_bar - 50.0
    S_L = 1.0 + (0.015 * dL50 * dL50) / np.sqrt(20.0 + dL50 * dL50)
    S_C = 1.0 + 0.045 * Cp_bar
    S_H = 1.0 + 0.015 * Cp_bar * T

    tL = dLp / (kL * S_L)
    tC = dCp / (kC * S_C)
    tH = dHp / (kH * S_H)

    de = np.sqrt(np.maximum(tL * tL + tC * tC + tH * tH + R_T * tC * tH, 0.0))
    return de.astype(np.float32)


def delta_e_76(lab1: np.ndarray, lab2: np.ndarray) -> np.ndarray:
    """Евклидова ΔE в Lab. Быстрее ΔE00 в ~15 раз, точность ниже.
    Используется как предфильтр: ΔE76 — верхняя оценка, где она мала,
    ΔE00 заведомо мала, и полную формулу считать не нужно."""
    d = lab1.astype(np.float32) - lab2.astype(np.float32)
    return np.sqrt(np.einsum("...i,...i->...", d, d)).astype(np.float32)
