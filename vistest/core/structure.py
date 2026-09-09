# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Structural metrics: SSIM map, gradient similarity, edge density.

SSIM is implemented with cv2 convolutions to avoid pulling scikit-image into
mandatory dependencies and to get a map, not a single number: a single number
over a full-page screenshot is almost always > 0.99 and useless as a gate.
"""

from __future__ import annotations

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

_C1 = (0.01 * 255) ** 2
_C2 = (0.03 * 255) ** 2


def _blur(img: np.ndarray, sigma: float = 1.5) -> np.ndarray:
    # Kernel size must not exceed image: on small crops 11×11 goes out of bounds
    # and gives unstable results.
    k = min(11, (min(img.shape[:2]) // 2) * 2 + 1)
    k = max(3, k if k % 2 else k - 1)
    return cv2.GaussianBlur(img, (k, k), sigma, borderType=cv2.BORDER_REFLECT)


def ssim_map(gray_exp: np.ndarray, gray_act: np.ndarray, sigma: float = 1.5):
    """Return (SSIM map float32 in [-1,1], mean across the map)."""
    if cv2 is None:
        raise RuntimeError("SSIM requires opencv-python")

    a = gray_exp.astype(np.float32)
    b = gray_act.astype(np.float32)

    mu_a = _blur(a, sigma)
    mu_b = _blur(b, sigma)
    mu_a2, mu_b2, mu_ab = mu_a * mu_a, mu_b * mu_b, mu_a * mu_b

    sa = _blur(a * a, sigma) - mu_a2
    sb = _blur(b * b, sigma) - mu_b2
    sab = _blur(a * b, sigma) - mu_ab

    num = (2.0 * mu_ab + _C1) * (2.0 * sab + _C2)
    den = (mu_a2 + mu_b2 + _C1) * (sa + sb + _C2)
    smap = num / np.maximum(den, 1e-12)
    return smap.astype(np.float32), float(smap.mean())


def local_ssim(gray_exp: np.ndarray, gray_act: np.ndarray) -> float:
    """SSIM of a small fragment. For tiny crops the window shrinks."""
    if cv2 is None or gray_exp.size == 0 or gray_exp.shape != gray_act.shape:
        return 0.0
    h, w = gray_exp.shape[:2]
    if min(h, w) < 4:
        # Too small for window statistics — fall back to normalized difference.
        d = np.abs(gray_exp.astype(np.float32) - gray_act.astype(np.float32)).mean()
        return float(max(0.0, 1.0 - d / 255.0))
    sigma = 1.5 if min(h, w) >= 11 else max(0.6, min(h, w) / 7.0)
    _, mean = ssim_map(gray_exp, gray_act, sigma=sigma)
    return float(mean)


def gradient_magnitude(gray: np.ndarray) -> np.ndarray:
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    return cv2.magnitude(gx, gy)


def gradient_similarity(gray_exp: np.ndarray, gray_act: np.ndarray) -> np.ndarray:
    """GMS map (Gradient Magnitude Similarity), 1 = shapes match.

    Complements SSIM: sensitive specifically to edge displacement/appearance,
    while almost ignoring uniform brightness changes (e.g., different gamma).
    """
    if cv2 is None:
        return np.ones(gray_exp.shape, dtype=np.float32)
    ga = gradient_magnitude(gray_exp)
    gb = gradient_magnitude(gray_act)
    c = 170.0
    return ((2.0 * ga * gb + c) / (ga * ga + gb * gb + c)).astype(np.float32)


def edge_density(gray: np.ndarray) -> float:
    """Fraction of edge pixels. High (>0.10) — almost certainly text."""
    if cv2 is None or gray.size == 0:
        return 0.0
    if min(gray.shape[:2]) < 3:
        return 0.0
    edges = cv2.Canny(gray, 60, 160)
    return float(np.count_nonzero(edges)) / float(gray.size)
