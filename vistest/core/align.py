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

from . import warp as _warp

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

    Goes through `core/warp.py` like every other translation in the engine —
    bilinear, edge replicated, written out in numpy. It used to be
    `cv2.warpAffine`, which rounds the offset to 1/32 px on OpenCV 4.x, and
    that made this the last stage whose output depended on which major was
    installed: six rows of the benchmark's detailed table differed between
    4.14 and 5.0 because of this one call, verdicts alike but metrics not.

    `warp.to_uint8` rounds half to even on the way back to 8-bit levels; its
    docstring says why that and not truncation.
    """
    if abs(dx) < 1e-3 and abs(dy) < 1e-3:
        return img
    moved = _warp.shift(img, -dx, -dy)
    return _warp.to_uint8(moved) if img.dtype == np.uint8 else moved


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

    #  What is reported is what is drawn. `phaseCorrelate` answers in full
    #  precision, the backend may not be able to draw that, and printing the
    #  request to two decimals claims a precision the stage does not have —
    #  the same defect `core/refit.py` was carrying. With the numpy shift the
    #  two are equal; with `cv2.warpAffine` on OpenCV 4.x they are not, and
    #  `warp.applied_shift` puts the estimate on the grid before it is used.
    al.dx, al.dy = _warp.applied_shift(al.dx, al.dy)
    al.applied = True
    al.reason = f"compensated shift dx={al.dx:+.2f} dy={al.dy:+.2f}"
    return apply_shift(rgb_act, al.dx, al.dy), al


#  Two matches count as "equally good" when the runner-up is within this much
#  NCC of the winner. Template matching of a 16×16 radio button returns 0.999
#  and 0.998 for two neighbouring radio buttons; picking the first is a coin
#  toss dressed up as a measurement.
AMBIGUITY_MARGIN = 0.03
_MAX_PEAKS = 6
#  Below this side a template carries too little to search a window with.
MIN_SEARCH_SIDE = 6


def _search(gray_exp, gray_act, bbox, search_px):
    """-> (NCC surface, window origin) or None when a search is meaningless."""
    if cv2 is None:
        return None
    x, y, w, h = bbox
    H, W = gray_exp.shape[:2]
    # Searching for a region nearly filling the screen is pointless (and expensive),
    # but a 50%-of-screen limit would discard legitimate large blocks.
    if w < MIN_SEARCH_SIDE or h < MIN_SEARCH_SIDE or w > W * 0.9 or h > H * 0.9:
        return None

    patch = gray_exp[y:y + h, x:x + w]
    if patch.size == 0 or float(patch.std()) < 3.0:
        # A uniform block will match anywhere — the result is meaningless.
        return None

    sx0, sy0 = max(0, x - search_px), max(0, y - search_px)
    sx1, sy1 = min(W, x + w + search_px), min(H, y + h + search_px)
    window = gray_act[sy0:sy1, sx0:sx1]
    if window.shape[0] < h or window.shape[1] < w:
        return None
    return cv2.matchTemplate(window, patch, cv2.TM_CCOEFF_NORMED), (sx0, sy0)


def find_shift_candidates(
    gray_exp: np.ndarray,
    gray_act: np.ndarray,
    bbox: tuple[int, int, int, int],
    *,
    search_px: int = 64,
    margin: float = AMBIGUITY_MARGIN,
) -> list[tuple[int, int, float]]:
    """Every place in actual where the region's content fits about as well
    as the best one. -> [(dx, dy, ncc), ...], best first; empty if no search.

    Peaks are separated by non-maximum suppression over half the region's
    size, so one match smeared over two neighbouring pixels counts once.
    More than one entry means the region is ambiguous: a row of identical
    checkboxes, radio buttons, table cells. The caller must not pick one.
    """
    found = _search(gray_exp, gray_act, bbox, search_px)
    if found is None:
        return []
    surface, (sx0, sy0) = found
    x, y, w, h = bbox
    surface = surface.copy()
    rx, ry = max(1, w // 2), max(1, h // 2)

    peaks: list[tuple[int, int, float]] = []
    best = None
    for _ in range(_MAX_PEAKS):
        _, val, _, loc = cv2.minMaxLoc(surface)
        val = float(val)
        if best is None:
            best = val
        elif val < best - margin:
            break
        peaks.append((int(sx0 + loc[0] - x), int(sy0 + loc[1] - y), val))
        lx, ly = loc
        surface[max(0, ly - ry):ly + ry + 1, max(0, lx - rx):lx + rx + 1] = -1.0
    return peaks


def find_local_shift(
    gray_exp: np.ndarray,
    gray_act: np.ndarray,
    bbox: tuple[int, int, int, int],
    *,
    search_px: int = 64,
) -> tuple[int, int, float]:
    """Find the region's content within the search window in actual.

    Answers: "Did this block change or just move?".
    Returns (dx, dy, ncc) of the best match. ncc close to 1 → this is MOVED,
    not CONTENT — unless `find_shift_candidates` finds a second match as good,
    which is what the classifier checks.
    """
    peaks = find_shift_candidates(gray_exp, gray_act, bbox,
                                  search_px=search_px, margin=0.0)
    return peaks[0] if peaks else (0, 0, 0.0)


def match_at(
    gray_exp: np.ndarray,
    gray_act: np.ndarray,
    bbox: tuple[int, int, int, int],
    dx: int,
    dy: int,
) -> float:
    """NCC of the region against actual at one given offset (0.0 if outside)."""
    if cv2 is None:
        return 0.0
    x, y, w, h = bbox
    H, W = gray_act.shape[:2]
    if x + dx < 0 or y + dy < 0 or x + dx + w > W or y + dy + h > H:
        return 0.0
    patch = gray_exp[y:y + h, x:x + w]
    target = gray_act[y + dy:y + dy + h, x + dx:x + dx + w]
    if patch.size == 0 or float(patch.std()) < 3.0 or float(target.std()) < 1e-6:
        return 0.0
    return float(cv2.matchTemplate(target, patch, cv2.TM_CCOEFF_NORMED)[0, 0])


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
