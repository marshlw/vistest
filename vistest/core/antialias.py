# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Suppression of subpixel anti-aliasing.

The main source of false positives in real projects. The same text rendered
twice produces letter boundaries differing by tens of brightness units —
due to subpixel hinting, GPU rasterization, fractional DPR.

Criterion (generalization of pixelmatch heuristic, vectorized):

    A pixel is anti-aliasing if its new value lies INSIDE the range of values
    of neighbors 3×3 in the other image (and vice versa), AND there is a real
    gradient around it.

Why this works:
  * AA-pixel is a blend of edge colors, so its value always lies between the
    colors on both sides of the edge, i.e., within the local range.
  * Real change (button changed color, text is different) produces a value
    OUTSIDE the local range — such a pixel is not suppressed.
  * On a flat fill min == max, range is zero → nothing is suppressed.
    So the filter physically cannot hide a background color change.
"""

from __future__ import annotations

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

_K3 = np.ones((3, 3), np.uint8)


def antialias_mask(
    gray_exp: np.ndarray,
    gray_act: np.ndarray,
    *,
    tolerance: float = 0.10,
    min_gradient: float = 12.0,
) -> np.ndarray:
    """Boolean mask of pixels explainable by anti-aliasing."""
    if cv2 is None:
        return np.zeros(gray_exp.shape, dtype=bool)

    a = gray_exp
    b = gray_act
    tol = np.float32(tolerance * 255.0)

    lo_a = cv2.erode(a, _K3).astype(np.float32)
    hi_a = cv2.dilate(a, _K3).astype(np.float32)
    lo_b = cv2.erode(b, _K3).astype(np.float32)
    hi_b = cv2.dilate(b, _K3).astype(np.float32)

    af = a.astype(np.float32)
    bf = b.astype(np.float32)

    # actual is explainable by expected neighbors and vice versa — symmetry
    # is important, otherwise a disappeared thin element would be accepted as AA.
    b_in_a = (bf >= lo_a - tol) & (bf <= hi_a + tol)
    a_in_b = (af >= lo_b - tol) & (af <= hi_b + tol)

    # Suppress only where there is a real edge.
    gradient = np.maximum(hi_a - lo_a, hi_b - lo_b)
    on_edge = gradient >= min_gradient

    return b_in_a & a_in_b & on_edge


def text_shift_mask(
    gray_exp: np.ndarray,
    gray_act: np.ndarray,
    *,
    radius: int = 1,
    tolerance: float = 10.0,
) -> np.ndarray:
    """Pixels that match with a shift of ±radius.

    Catches "text moved by one pixel due to different kerning": the pixel value
    in actual is found somewhere within a radius-sized window in expected.
    Unlike global alignment, it works locally — for individual text lines.
    """
    if cv2 is None:
        return np.zeros(gray_exp.shape, dtype=bool)

    k = 2 * radius + 1
    kernel = np.ones((k, k), np.uint8)
    lo = cv2.erode(gray_exp, kernel).astype(np.float32) - tolerance
    hi = cv2.dilate(gray_exp, kernel).astype(np.float32) + tolerance
    bf = gray_act.astype(np.float32)

    lo2 = cv2.erode(gray_act, kernel).astype(np.float32) - tolerance
    hi2 = cv2.dilate(gray_act, kernel).astype(np.float32) + tolerance
    af = gray_exp.astype(np.float32)

    return ((bf >= lo) & (bf <= hi)) & ((af >= lo2) & (af <= hi2))
