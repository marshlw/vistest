# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Region classification and severity calculation.

Idea: binary "significant/insignificant" is not enough. A label shifted 2 px
and a disappeared "Pay" button are both "significant" by area, but these are
events of different scales. So each region gets a class and a continuous
severity score 0..100, and the failure threshold is set by policy in config.
"""

from __future__ import annotations

import math

import numpy as np

from ..models import ChangeKind, DiffRegion
from .align import AMBIGUITY_MARGIN, MIN_SEARCH_SIDE, find_shift_candidates, match_at
from .structure import edge_density, local_ssim

# How important the change class is in itself.
KIND_WEIGHT: dict[ChangeKind, float] = {
    ChangeKind.NOISE: 0.0,
    ChangeKind.ANTIALIAS: 0.0,
    ChangeKind.MOVED: 0.35,      # the floor; see moved_weight()
    ChangeKind.RESIZED: 0.80,
    ChangeKind.COLOR: 0.90,
    ChangeKind.TEXT: 1.00,
    ChangeKind.CONTENT: 1.00,
    ChangeKind.ADDED: 1.20,
    ChangeKind.REMOVED: 1.25,    # a disappeared element is almost always a bug
}

_FLAT_STD = 4.0          # below this, we consider the area uniform ("empty")
_FLAT_EDGES = 0.012
_TEXT_EDGES = 0.10       # above this, edge density looks like text
_COLOR_STRUCT_OK = 0.90  # structure is preserved => only color changed


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
    prefer_shift: tuple[int, int] | None = None,
) -> DiffRegion:
    """Classify one region and score it.

    prefer_shift: the shift most unambiguous regions of this comparison agreed
                  on. An ambiguous region (its content fits equally well in
                  several places) is reported as MOVED only if one of those
                  places is exactly this shift; otherwise it is classified as
                  if nothing moved. See `_moved`.
    """
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

    # --- 1. Did the block move entirely? ---
    if detect_moved and _moved(region, gray_exp, gray_act, move_search_px,
                               move_match_threshold, prefer_shift):
        region.kind = ChangeKind.MOVED
        region.severity = _severity(region, total_pixels,
                                    moved_weight(region, moved_scale),
                                    above_fold_px, above_fold_weight)
        return region

    # --- 2. Appearance / disappearance ---
    std_exp = float(crop_exp.std()) if crop_exp.size else 0.0
    std_act = float(crop_act.std()) if crop_act.size else 0.0
    empty_exp = std_exp < _FLAT_STD and ed_exp < _FLAT_EDGES
    empty_act = std_act < _FLAT_STD and ed_act < _FLAT_EDGES

    if empty_exp and not empty_act:
        region.kind = ChangeKind.ADDED
    elif empty_act and not empty_exp:
        region.kind = ChangeKind.REMOVED
    # --- 3. Color only: structure is intact ---
    elif ssim_local >= _COLOR_STRUCT_OK and de_mean >= 2.0:
        region.kind = ChangeKind.COLOR
    # --- 4. Text ---
    elif max(ed_exp, ed_act) >= _TEXT_EDGES:
        region.kind = ChangeKind.TEXT
    else:
        region.kind = ChangeKind.CONTENT

    weight = KIND_WEIGHT.get(region.kind, 1.0)
    region.severity = _severity(region, total_pixels, weight,
                                above_fold_px, above_fold_weight)
    return region


#  The reference size: 0.2% of the frame, and never below 400 px. A change
#  this large is "a noticeable element" and gets the full size factor.
_SIZE_REF_FRAC = 0.002
_SIZE_REF_MIN_PX = 400.0
#  How fast the raw score approaches 100. The curve is `1 - exp(-raw / tau)`,
#  so the scale never clips: two different regions keep two different
#  numbers all the way up. With tau 0.6 a raw score of 1.0 reads ~81.
_SATURATION_TAU = 0.6
_SIZE_LOG_GAIN = 0.15


#: The weight a move reaches once the element has left its own footprint.
#: Equal to a content change: the element is somewhere else entirely, and a
#: move that does not overlap is what the classifier otherwise reports as a
#: removal plus an addition.
MOVED_FULL_WEIGHT = 1.0


def moved_weight(r: DiffRegion, floor: float) -> float:
    """Class weight of a MOVED region: grows with how far the element went.

    `floor` (`diff.moved_severity_scale`) was the whole weight before. That
    made the score of a move blind to the move: a 12-px checkbox shifted by
    24 px (old and new positions overlap, one MOVED region) scored 17 and
    passed, while the same checkbox shifted by 32 px (no overlap, reported as
    removed + added) scored 35 and failed. Longer moves passed, shorter ones
    failed, depending only on whether the two footprints touched.

    The weight now rises from `floor` to `MOVED_FULL_WEIGHT` with the shift
    measured in the element's own size along the direction of the move. A
    MOVED box spans the old and the new position, so the element's extent is
    the box minus the shift. A panel that slid by a tenth of its height stays
    close to the floor; an element that moved by its own size weighs what a
    content change weighs.
    """
    fx = abs(r.moved_dx) / max(1, r.w - abs(r.moved_dx)) if r.moved_dx else 0.0
    fy = abs(r.moved_dy) / max(1, r.h - abs(r.moved_dy)) if r.moved_dy else 0.0
    frac = min(1.0, max(fx, fy))
    top = max(floor, MOVED_FULL_WEIGHT)
    return floor + (top - floor) * frac


def _moved(region: DiffRegion, gray_exp, gray_act, search_px: int,
           threshold: float, prefer_shift: tuple[int, int] | None) -> bool:
    """Decide MOVED and fill in the vector — or refuse to guess.

    A small element that repeats (checkboxes, radio buttons, icons in a list)
    matches its neighbours as well as itself. Taking the best NCC then names a
    vector that is an artefact of scan order: two adjacent checkboxes "moved"
    by +48 and -58 at once. So:

      * one convincing match            -> MOVED with that vector;
      * several (or a region too small to
        search), and the content fits the
        shift the rest of the page agrees
        on as well as anywhere           -> MOVED with that shift;
      * several, no tie-breaker         -> not MOVED. The region falls through
        to appeared / disappeared / content, which is true without a vector.

    `region.move_alternatives` keeps the count of equally good matches, so the
    report can say why no vector was named.
    """
    box = (region.x, region.y, region.w, region.h)
    too_small = region.w < MIN_SEARCH_SIDE or region.h < MIN_SEARCH_SIDE
    peaks = [p for p in find_shift_candidates(gray_exp, gray_act, box,
                                              search_px=search_px)
             if p[2] >= threshold]
    if peaks and (peaks[0][0], peaks[0][1]) == (0, 0):
        return False
    if len(peaks) == 1:
        region.moved_dx, region.moved_dy, region.match_score = peaks[0]
        return True
    if len(peaks) > 1:
        region.move_alternatives = len(peaks)
    elif not too_small:
        return False

    # Ambiguous, or too small to search a window with: only the shift the
    # page as a whole agrees on may be named, and only if the content fits
    # there as well as anywhere else.
    if prefer_shift is None or prefer_shift == (0, 0):
        return False
    ncc = match_at(gray_exp, gray_act, box, *prefer_shift)
    best = peaks[0][2] if peaks else ncc
    if ncc < threshold or ncc < best - AMBIGUITY_MARGIN:
        return False
    region.moved_dx, region.moved_dy = prefer_shift
    region.match_score = ncc
    return True


def _severity(
    r: DiffRegion,
    total_pixels: int,
    kind_weight: float,
    above_fold_px: int,
    above_fold_weight: float,
) -> float:
    """0..100: how much this region matters.

    size       — how many pixels actually changed (mask, not bbox), relative
                 to 0.2% of the frame: `(px / ref) ** 0.5` up to the
                 reference, logarithmic beyond it.
    intensity  — how different those pixels are: colour (ΔE 12 = "clearly a
                 different colour") OR structure (1 - local SSIM), combined as
                 `1 - (1 - colour)(1 - structure)`. Either one is enough.

    The two are **multiplied**, not summed. The old score was
    `0.45·size + 0.30·colour + 0.25·structure`, so a region of thirty pixels
    with a sharp contrast collected 0.55 from colour and structure alone; the
    class weight (up to 1.25) and the above-the-fold weight (1.5) then pushed
    it past 100 and it was clipped. Every real change read as 100 and
    `fail_severity` became a two-position switch. Now a tiny change can be
    as sharp as it likes — it stays small.

    The product is scaled by class and position and mapped through a soft
    saturation instead of a hard clip, so ordering survives at the top end.
    """
    ref = max(total_pixels * _SIZE_REF_FRAC, _SIZE_REF_MIN_PX)
    q = max(r.pixel_count, 0) / ref
    # Past the reference the factor keeps growing, slowly, so that a whole
    # panel still outranks a button once both are "clearly noticeable".
    size_term = q ** 0.5 if q <= 1.0 else 1.0 + _SIZE_LOG_GAIN * math.log(q)
    color_term = min(1.0, max(0.0, r.de_mean) / 12.0)
    struct_term = min(1.0, max(0.0, 1.0 - r.ssim_local) / 0.35)
    intensity = 1.0 - (1.0 - color_term) * (1.0 - struct_term)

    pos_weight = above_fold_weight if r.y < above_fold_px else 1.0
    raw = size_term * intensity * kind_weight * pos_weight
    return float(np.clip(100.0 * (1.0 - np.exp(-raw / _SATURATION_TAU)),
                         0.0, 100.0))
