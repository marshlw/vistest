# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Moving a picture by a fraction of a pixel — the one door in the engine.

Three places translate a picture: `align` compensates a global shift before
the comparison, `refit` re-draws the baseline while deciding whether a
difference is a rasteriser or a regression, and the `rerender` rule in
`explain` searches for the shift that reproduces a region. All three used to
call `cv2.warpAffine` for it. The first two were wrong because of it; the third
was right only because its quarter-pixel steps happen to lie on the grid
described below, which is luck rather than a guarantee.

`cv2.warpAffine` with `INTER_LINEAR` interpolates in fixed point on OpenCV 4.x
— `INTER_BITS` is 5 — so it rounds the translation to a 1/32 px grid before a
pixel is touched: ask for +0.30 and the picture moves by +0.3125. OpenCV 5 does
it in floating point and applies what it was given. The consequences were two,
and the second is the expensive one:

* the same pair of PNGs came out with different region metrics on the two
  majors, so a benchmark figure could not be compared with a figure taken on
  another machine;
* the engine printed the shift it had *asked* for — in `suppressed_by` and from
  there in the report, the failure message and the pytest summary — to two
  decimals it did not have. Explainability is the argument this engine makes
  for calling something noise, and an explanation that is wrong in the last
  digit it prints is worse than none.

So the arithmetic is written out here, once, for all of them: the same bilinear
interpolation and the same edge replication OpenCV did, in numpy, identical on
every version of every library. `shift_grid()` measures what the backend
actually applies and `applied_shift()` snaps a request to it before anything is
drawn, so what a stage reports is what it drew — and the promise survives
somebody putting `cv2.warpAffine` back.
"""

from __future__ import annotations

import functools

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None


def shift(img: np.ndarray, dx: float, dy: float) -> np.ndarray:
    """Bilinear translation by (dx, dy), edge replicated. -> float32.

    Four weighted views of the source and no library. Bilinear and replicate
    are kept exactly as `cv2.warpAffine(..., INTER_LINEAR, BORDER_REPLICATE)`
    had them: anything else would move the boundary between noise and
    regression, which is not what a portability fix is for. Replicate rather
    than zeros for the same reason it always was — a black border would be a
    guaranteed false difference as wide as the shift.
    """
    if abs(dx) < 1e-6 and abs(dy) < 1e-6:
        return img
    h, w = img.shape[:2]
    ix, fx = int(np.floor(dx)), float(dx - np.floor(dx))
    iy, fy = int(np.floor(dy)), float(dy - np.floor(dy))
    #  A shift by +d reads from -d: source index = target index - offset,
    #  clamped to the edge (BORDER_REPLICATE). Both neighbours are clamped from
    #  the unclamped index, or the last column would read its own neighbour.
    bx = np.arange(w) - ix
    by = np.arange(h) - iy
    cx, lx = np.clip(bx, 0, w - 1), np.clip(bx - 1, 0, w - 1)
    cy, ly = np.clip(by, 0, h - 1), np.clip(by - 1, 0, h - 1)
    src = img.astype(np.float32, copy=False)
    #  When the whole-pixel part is zero the "current" index is the identity
    #  ramp, and taking it would copy the array to get the array back. On a
    #  full page that is the difference the budget was measured against.
    out = None
    for wy, ry, ry_is_identity in ((1.0 - fy, cy, iy == 0), (fy, ly, False)):
        if wy == 0.0:
            continue
        row = src if ry_is_identity else src[ry]
        here = row if ix == 0 else row[:, cx]
        part = (1.0 - fx) * here + fx * row[:, lx] if fx else here
        out = wy * part if out is None else out + wy * part
    return out


def to_uint8(arr: np.ndarray) -> np.ndarray:
    """A picture computed in floating point, back to 8-bit levels.

    **Round half to even** (`np.rint`), and it is written down because the
    alternative is a bug that takes a year to find. `arr.astype(np.uint8)`
    truncates, which is not a rounding mode but a systematic bias of half a
    level downwards — apply it to a resampled screenshot and every comparison
    afterwards sees a picture slightly darker than the one that was captured.
    Half-up (`floor(x + 0.5)`) has no bias on real data but does on ties, and
    ties are not rare here: a bilinear blend of two 8-bit levels lands on .5
    whenever the offset is a half pixel, which is the commonest case there is.
    Half to even is unbiased on both, it is what IEEE 754 and `cv2.warpAffine`
    already did, and it is numpy's default, so a reader has one rule to hold.

    Every place in the engine that turns a float into 8-bit levels goes
    through here — the aligned actual (`align.apply_shift`), the L* channel
    packed into a byte for SSIM and the anti-alias filter (`color.luminance`
    and the two copies of it in `comparator.compare`), and anything written
    out as a PNG (`pngio.encode`). Three of those used to truncate, which is
    where the half-level bias was hiding. Boolean masks cast to `uint8` for
    OpenCV's morphology are not levels and are not this.

    Outside the engine, `render/artifacts.py` keeps its own arithmetic: those
    are pictures drawn for a person to look at — dimmed backdrops, heat maps —
    and no decision is taken on their values.
    """
    return np.rint(np.clip(arr, 0, 255)).astype(np.uint8)


#: Offset used to ask the backend what it can actually draw: half of the
#: 1/32 px grid OpenCV 4.x snaps to, so a backend that honours it and a backend
#: that rounds it answer differently.
_GRID_PROBE = 1.0 / 64.0
#: OpenCV's fixed-point interpolation step, 2**-INTER_BITS with INTER_BITS = 5.
_CV_GRID = 1.0 / 32.0


@functools.lru_cache(maxsize=1)
def shift_grid() -> float:
    """Size of the grid `shift` rounds a translation to; 0.0 when it does not.

    With the shift above it is 0.0 on every platform, and that is the point of
    having written the shift out. It is measured rather than assumed because
    the promise this module makes — the shift a stage prints is the shift it
    drew — has to survive somebody putting `cv2.warpAffine` back. Then this
    answers 1/32 on OpenCV 4.x and `applied_shift` closes the gap on its own.
    """
    if cv2 is None:
        return 0.0
    ramp = np.tile(np.arange(64, dtype=np.float32), (4, 1))   # slope: 1 per px
    applied = float(ramp[2, 30] - shift(ramp, _GRID_PROBE, 0.0)[2, 30])
    return 0.0 if abs(applied - _GRID_PROBE) < 1e-4 else _CV_GRID


def applied_shift(dx: float, dy: float) -> tuple[float, float]:
    """The nearest shift the backend draws exactly. Identity when there is no grid.

    Snapping the request instead of measuring the result is deliberate: a
    multiple of the grid is applied exactly, whatever rounding the backend
    would have done to a value between two grid points. So the numbers a stage
    reports describe the picture that was drawn, not the one that was asked for.
    """
    step = shift_grid()
    if not step:
        return dx, dy
    return round(dx / step) * step, round(dy / step) * step
