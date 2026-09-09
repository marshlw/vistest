# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Subpixel alignment.

The most common cause of "the entire screenshot is red" — the page shifted by 1–2 px
(different header height, scrollbar, layout rounding). Pixel-level comparison
after such a shift is meaningless.

Strategy:
  1. phaseCorrelate on the L*-channel — global shift with subpixel precision,
     robust to noise and local changes (works in the frequency domain).
  2. If the shift is small (< max_shift) — compensate with warp and compare further.
  3. If large — DO NOT compensate: this is a real layout regression, it should
     be reported, not hidden.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None


@dataclass
class Alignment:
    dx: float = 0.0
    dy: float = 0.0
    confidence: float = 0.0
    applied: bool = False
    reason: str = ""

    @property
    def magnitude(self) -> float:
        return float(np.hypot(self.dx, self.dy))


def estimate_shift(gray_exp: np.ndarray, gray_act: np.ndarray) -> Alignment:
    """Estimate global shift of actual relative to expected."""
    if cv2 is None:
        return Alignment(reason="cv2 unavailable")
    if gray_exp.shape != gray_act.shape:
        return Alignment(reason="different sizes")
    if min(gray_exp.shape[:2]) < 16:
        return Alignment(reason="image too small")

    a = gray_exp.astype(np.float32)
    b = gray_act.astype(np.float32)
    # Hann window removes FFT edge artifacts (otherwise the image frame itself
    # produces a strong false peak).
    win = cv2.createHanningWindow((a.shape[1], a.shape[0]), cv2.CV_32F)
    (dx, dy), response = cv2.phaseCorrelate(a, b, win)
    return Alignment(dx=float(dx), dy=float(dy), confidence=float(response))


def apply_shift(img: np.ndarray, dx: float, dy: float) -> np.ndarray:
    """Shift actual by (-dx,-dy) to align with expected.

    BORDER_REPLICATE, not zeros: a black border would produce a guaranteed
    false diff with width equal to the shift.
    """
    if cv2 is None or (abs(dx) < 1e-3 and abs(dy) < 1e-3):
        return img
    m = np.array([[1.0, 0.0, -dx], [0.0, 1.0, -dy]], dtype=np.float32)
    h, w = img.shape[:2]
    return cv2.warpAffine(
        img, m, (w, h),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE,
    )


def align_images(
    rgb_exp: np.ndarray,
    rgb_act: np.ndarray,
    gray_exp: np.ndarray,
    gray_act: np.ndarray,
    *,
    enabled: bool = True,
    max_shift_px: float = 8.0,
    min_confidence: float = 0.05,
) -> tuple[np.ndarray, Alignment]:
    """Return (aligned actual, alignment description)."""
    if not enabled:
        return rgb_act, Alignment(reason="disabled")

    al = estimate_shift(gray_exp, gray_act)
    if al.reason:
        return rgb_act, al
    if al.magnitude < 0.05:
        al.reason = "no shift"
        return rgb_act, al
    if al.confidence < min_confidence:
        al.reason = f"low confidence ({al.confidence:.3f})"
        return rgb_act, al
    if al.magnitude > max_shift_px:
        al.reason = (f"shift {al.magnitude:.1f}px > the {max_shift_px}px limit"
                     " — this is a layout regression")
        return rgb_act, al

    al.applied = True
    al.reason = f"compensated shift dx={al.dx:+.2f} dy={al.dy:+.2f}"
    return apply_shift(rgb_act, al.dx, al.dy), al


def find_local_shift(
    gray_exp: np.ndarray,
    gray_act: np.ndarray,
    bbox: tuple[int, int, int, int],
    *,
    search_px: int = 64,
) -> tuple[int, int, float]:
    """Find the region's content within the search window in actual.

    Answers: "Did this block change or just move?".
    Returns (dx, dy, ncc). ncc close to 1 → this is MOVED, not CONTENT.
    """
    if cv2 is None:
        return 0, 0, 0.0

    x, y, w, h = bbox
    H, W = gray_exp.shape[:2]
    # Searching for a region nearly filling the screen is pointless (and expensive),
    # but a 50%-of-screen limit would discard legitimate large blocks.
    if w < 6 or h < 6 or w > W * 0.9 or h > H * 0.9:
        return 0, 0, 0.0

    patch = gray_exp[y:y + h, x:x + w]
    if patch.size == 0 or float(patch.std()) < 3.0:
        # A uniform block will match anywhere — the result is meaningless.
        return 0, 0, 0.0

    sx0, sy0 = max(0, x - search_px), max(0, y - search_px)
    sx1, sy1 = min(W, x + w + search_px), min(H, y + h + search_px)
    window = gray_act[sy0:sy1, sx0:sx1]
    if window.shape[0] < h or window.shape[1] < w:
        return 0, 0, 0.0

    res = cv2.matchTemplate(window, patch, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(res)
    found_x, found_y = sx0 + max_loc[0], sy0 + max_loc[1]
    return int(found_x - x), int(found_y - y), float(max_val)


def reconcile_sizes(
    a: np.ndarray, b: np.ndarray, pad_value: int = 0
) -> tuple[np.ndarray, np.ndarray, bool]:
    """Bring to a common size by PADDING, not cropping.

    Cropping to min(h,w) is a common mistake: it silently hides the most
    frequent real regression (page height changed). By padding, we keep
    the difference visible in the diff mask and artifacts.
    """
    if a.shape == b.shape:
        return a, b, False

    h = max(a.shape[0], b.shape[0])
    w = max(a.shape[1], b.shape[1])

    def pad(img):
        if img.shape[0] == h and img.shape[1] == w:
            return img
        pads = [(0, h - img.shape[0]), (0, w - img.shape[1])]
        if img.ndim == 3:
            pads.append((0, 0))
        return np.pad(img, pads, mode="constant", constant_values=pad_value)

    return pad(a), pad(b), True
