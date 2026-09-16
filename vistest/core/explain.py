# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Deterministic noise explanations for regions.

A region survives segmentation because enough pixels changed close enough
together. That is a statement about *where*, not about *why*. This stage asks
the second question with three fixed, documented tests, and suppresses a
region only when one of them explains the pixels that put it there:

  scrollbar   a narrow band at the right or bottom edge of the frame that is
              featureless along its length in both images, differs between
              them along (almost) all of that length, and leaves the page
              next to it untouched. That is a scroll bar appearing or
              disappearing — the environment, not the layout.

  jpeg        the frame went through a JPEG encoder: re-encoding the baseline
              at some quality reproduces the actual pixels in the changed
              areas far better than the baseline itself does. A region whose
              pixels that re-encoded baseline explains is codec noise.

  rerender    the region's changed pixels are reproduced by drawing the
              baseline again up to one pixel away, in quarter-pixel steps,
              within a tolerance that grows with local contrast. That is
              subpixel text rendering, hinting, a different rasteriser or
              sensor noise. A glyph that became another glyph, a colour that
              became another colour, an element that appeared — none of them
              is reproduced by moving the old picture by a fraction of a pixel.

This is part of the open engine and runs with or without the AI layer. It
exists because the segmentation closes the change mask before opening it: the
other order erased every stroke thinner than five pixels, which is to say
text. Closing first keeps text, and also turns scattered rendering residue
into blobs; this stage is what takes those blobs back out, with a reason a
person can read and check.

What it deliberately does not explain:

  * a region already classified MOVED — a vector of a whole pixel or more is a
    statement about geometry, and moved regions have their own weight;
  * anything on a frame whose size changed;
  * a flat colour change: off the edges a pixel is only explained as noise
    when the mean around it did not move, so a different fill is never
    "reproduced", however dark or light it is.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..models import ChangeKind, DiffRegion

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None


# --------------------------------------------------------------------------- #
#  Tunables. Module constants, not config: they describe the physics of a
#  renderer and a codec, not a project's taste. The benchmark measures them.
# --------------------------------------------------------------------------- #
#: A region is explained when at most this share of its changed pixels —
#: and never more than a couple of pixels on a small region — is left
#: unexplained. A share alone is not enough: on a noisy frame a changed digit
#: sits in a region padded out with noise pixels that *are* explained, and 39
#: unexplained pixels out of 159 is still a changed digit. Measured on the
#: corpus and its generator: rendering and sensor residue leaves 0–1 pixels,
#: re-encoding leaves 0, the smallest real change (one digit) leaves 9.
MAX_UNEXPLAINED = 0.05
MAX_UNEXPLAINED_FLOOR_PX = 2
#: On an edge a subpixel move changes a pixel by a share of the local contrast.
CONTRAST_TOLERANCE = 0.30
#: Below this local contrast (levels, 3×3, largest channel) a pixel is not on an edge.
MIN_EDGE_CONTRAST = 12.0
#: Off the edges, noise is told from a colour change by what it does on
#: average. Sensor noise is zero-mean: a single pixel may be off by a lot,
#: the 5×5 mean around it barely moves. A different fill moves every pixel
#: the same way, so the mean moves with it. An absolute per-pixel tolerance
#: cannot tell them apart: on a dark fill eight levels is already a visible
#: change, on a light one it is not.
NOISE_PEAK = 24.0
NOISE_MEAN = 3.0
NEAR_EDGE_MEAN = 0.05
_MEAN_WINDOW = 5
#: The search: ±1 px in quarter-pixel steps.
_STEP = 0.25
_REACH = 1.0
#: Margin around a region crop, so that the warp has real pixels to pull in.
_PAD = 3

#: Scroll bars: the widest band still called one (Windows classic is 17 px).
SCROLLBAR_MAX_PX = 20
SCROLLBAR_MIN_PX = 4
#: Share of the band's length that must differ between the two images.
_BAND_COVERAGE = 0.80
#: Share of rows in which a band column must hold its dominant colour.
_BAND_UNIFORM = 0.60

#: JPEG qualities tried when looking for the encoder of the actual frame.
_JPEG_QUALITIES = (50, 60, 70, 75, 80, 85, 90, 95)
#: The re-encoded baseline must leave at most this share of the error the
#: plain baseline leaves, in the changed areas, to call the frame JPEG.
_JPEG_GAIN = 0.5


def _chmax(x: np.ndarray) -> np.ndarray:
    """Largest channel. `ndarray.max(axis=2)` is several times slower."""
    return np.maximum(np.maximum(x[..., 0], x[..., 1]), x[..., 2])


@dataclass
class Explanation:
    rule: str
    detail: str


def explain_regions(
    regions: list[DiffRegion],
    *,
    expected: np.ndarray,
    actual: np.ndarray,
    raw_mask: np.ndarray,
    aligned: bool,
    size_changed: bool,
    notes: list[str] | None = None,
) -> list[DiffRegion]:
    """Suppress regions one of the three tests explains. Returns the same list.

    expected / actual: RGB uint8 of the same shape, actual already aligned.
    raw_mask:          the change mask *before* morphology — the evidence.
    """
    if cv2 is None or not regions or size_changed:
        return regions
    live = [r for r in regions
            if not r.suppressed_by
            and r.kind not in (ChangeKind.NOISE, ChangeKind.ANTIALIAS)]
    if not live:
        return regions

    exp = expected
    act = actual
    counts: dict[str, int] = {}

    def mark(r: DiffRegion, why: Explanation) -> None:
        r.suppressed_by = f"{why.rule}: {why.detail} (was {r.kind.value})"
        r.kind = ChangeKind.NOISE
        r.severity = 0.0
        counts[why.rule] = counts.get(why.rule, 0) + 1

    # 1. Scroll bars: a frame-level fact, decided once.
    bands = scrollbar_bands(exp, act)
    if bands:
        for r in live:
            why = _in_band(r, bands, exp.shape[:2])
            if why is not None:
                mark(r, why)
        live = [r for r in live if not r.suppressed_by]

    candidates = [r for r in live if r.kind is not ChangeKind.MOVED]
    if not candidates:
        _note(notes, counts)
        return regions

    # 2. JPEG: also frame-level, measured on the changed areas only. A grid
    #    that alignment moved by a fraction of a pixel is no longer a grid.
    codec = None
    if not aligned:
        codec = detect_jpeg(exp, act, candidates, raw_mask)
    if codec is not None:
        quality, reencoded = codec
        for r in candidates:
            left, total = _unexplained(reencoded, act, raw_mask, r, shifts=((0.0, 0.0),))
            if total and _small(left, total):
                mark(r, Explanation(
                    "jpeg", f"{total - left}/{total} changed pixels reproduced "
                            f"by re-encoding the baseline at quality {quality}"))
        candidates = [r for r in candidates if not r.suppressed_by]

    # 3. Subpixel re-rendering.
    for r in candidates:
        left, total, shift = _rerender(exp, act, raw_mask, r)
        if total == 0:
            # Closing fills the gaps between changed pixels and opening can
            # then keep a piece of the fill and drop the pixels around it.
            mark(r, Explanation("morphology", "no changed pixel inside the box; "
                                              "the region is a by-product of closing"))
            continue
        if total and _small(left, total):
            how = ("by the baseline itself, within zero-mean sensor noise"
                   if shift == (0.0, 0.0) else
                   f"by the baseline moved {shift[0]:+.2f},{shift[1]:+.2f} px")
            mark(r, Explanation(
                "rerender", f"{total - left}/{total} changed pixels reproduced {how}"))

    _note(notes, counts)
    return regions


def _small(left: int, total: int) -> bool:
    return left <= max(MAX_UNEXPLAINED_FLOOR_PX, MAX_UNEXPLAINED * total)


def _note(notes: list[str] | None, counts: dict[str, int]) -> None:
    if notes is None or not counts:
        return
    names = {"scrollbar": "a scroll bar band",
             "jpeg": "JPEG re-encoding",
             "rerender": "subpixel re-rendering",
             "morphology": "being empty of changed pixels"}
    parts = [f"{n} by {names[k]}" for k, n in sorted(counts.items())]
    notes.append("Noise explained and suppressed: " + ", ".join(parts)
                 + ". Each suppressed region names the test that explained it.")


# --------------------------------------------------------------------------- #
#  rerender
# --------------------------------------------------------------------------- #
def _crop(r: DiffRegion, shape: tuple[int, int]) -> tuple[int, int, int, int]:
    h, w = shape
    return (max(0, r.x - _PAD), max(0, r.y - _PAD),
            min(w, r.x + r.w + _PAD), min(h, r.y + r.h + _PAD))


def _unexplained(ref: np.ndarray, act: np.ndarray, raw_mask: np.ndarray,
                 r: DiffRegion, *, shifts) -> tuple[int, int]:
    """Changed pixels of `r` that no shifted copy of `ref` reproduces."""
    left, total, _ = _search(ref, act, raw_mask, r, shifts)
    return left, total


def _search(ref, act, raw_mask, r, shifts, *, give_up_after: int = 0):
    x0, y0, x1, y1 = _crop(r, ref.shape[:2])
    e = ref[y0:y1, x0:x1].astype(np.float32)
    a = act[y0:y1, x0:x1].astype(np.float32)
    evidence = np.zeros((y1 - y0, x1 - x0), dtype=bool)
    ix0, iy0 = r.x - x0, r.y - y0
    evidence[iy0:iy0 + r.h, ix0:ix0 + r.w] = raw_mask[r.y:r.y + r.h, r.x:r.x + r.w]
    total = int(evidence.sum())
    if total == 0:
        return 0, 0, (0.0, 0.0)

    # Contrast per channel, like the difference it bounds: a warm border on a
    # cream fill is 85 levels apart in blue and 30 apart in grey.
    k = np.ones((3, 3), np.uint8)
    contrast = _chmax(cv2.dilate(e, k) - cv2.erode(e, k))
    edge_tol = np.where(contrast >= MIN_EDGE_CONTRAST,
                        CONTRAST_TOLERANCE * contrast, -1.0)
    # The mean of a window that reaches an edge moves when the edge is drawn
    # a little softer or sharper; allow for that share of the nearby contrast.
    k5 = np.ones((_MEAN_WINDOW, _MEAN_WINDOW), np.uint8)
    near = _chmax(cv2.dilate(e, k5) - cv2.erode(e, k5))
    mean_tol = NOISE_MEAN + NEAR_EDGE_MEAN * near
    win = (_MEAN_WINDOW, _MEAN_WINDOW)
    a_mean = cv2.blur(a, win, borderType=cv2.BORDER_REPLICATE)

    best = (total + 1, (0.0, 0.0))
    size = (e.shape[1], e.shape[0])
    tried = 0
    for dx, dy in shifts:
        if dx == 0.0 and dy == 0.0:
            moved = e
        else:
            m = np.float32([[1, 0, dx], [0, 1, dy]])
            moved = cv2.warpAffine(e, m, size, flags=cv2.INTER_LINEAR,
                                   borderMode=cv2.BORDER_REPLICATE)
        diff = _chmax(np.abs(moved - a))
        mean_diff = _chmax(np.abs(cv2.blur(moved, win, borderType=cv2.BORDER_REPLICATE)
                                  - a_mean))
        explained = (diff <= edge_tol) | ((diff <= NOISE_PEAK) & (mean_diff <= mean_tol))
        left = int((~explained & evidence).sum())
        if left < best[0]:
            best = (left, (dx, dy))
            if left == 0:
                break
        tried += 1
        # Shifts come nearest first. When none within half a pixel explains
        # even half of the region, the rest of the square will not bring it
        # to a few per cent: that is a change, not a re-render. It is what
        # keeps a large real change from paying for the whole search.
        if give_up_after and tried == give_up_after and best[0] > total // 2:
            break
    return best[0], total, best[1]


def _grid(reach: float, step: float, around=(0.0, 0.0)):
    """Shifts around a point, nearest first: the report names the smallest
    move that explains a region, not the first one the loop happened on."""
    n = int(round(reach / step))
    pts = [(around[0] + i * step, around[1] + j * step)
           for i in range(-n, n + 1) for j in range(-n, n + 1)]
    return sorted(pts, key=lambda p: (abs(p[0] - around[0]) + abs(p[1] - around[1]), p))


def _rerender(exp, act, raw_mask, r):
    # Coarse half-pixel pass, then quarter pixels around the best of it.
    left, total, best = _search(exp, act, raw_mask, r, _grid(_REACH, 2 * _STEP),
                                give_up_after=9)
    if total == 0 or left == 0 or left > total // 2:
        return left, total, best
    fine = [s for s in _grid(_STEP, _STEP, best)
            if abs(s[0]) <= _REACH and abs(s[1]) <= _REACH]
    left2, _, best2 = _search(exp, act, raw_mask, r, fine)
    return (left2, total, best2) if left2 < left else (left, total, best)


# --------------------------------------------------------------------------- #
#  jpeg
# --------------------------------------------------------------------------- #
def _reencode(img: np.ndarray, quality: int) -> np.ndarray:
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    return cv2.imdecode(buf, cv2.IMREAD_UNCHANGED) if ok else img


def detect_jpeg(exp: np.ndarray, act: np.ndarray, regions: list[DiffRegion],
                raw_mask: np.ndarray):
    """-> (quality, re-encoded baseline) if the actual frame is JPEG, else None.

    Why not the block grid or the spectrum. Both were measured first. The
    energy of the difference at 8-px block boundaries came out *lower* than
    elsewhere on a q=75 frame (ratio 0.88; ringing inside the blocks around
    text dominates), and interfaces are laid out on 4- and 8-px grids, so a
    button that changed colour produced a boundary ratio of 2.6. The grid is
    a property of the encoder, not of its error, and a page can share it.

    Re-encoding asks the question directly: does *this* encoder, applied to
    the baseline, produce the actual pixels? JPEG blocks are independent,
    so only the changed areas are encoded — cropped on the 16-px MCU grid,
    which keeps the result identical to encoding the whole frame.
    """
    h, w = exp.shape[:2]
    boxes = []
    for r in regions:
        x0 = max(0, (r.x - _PAD) // 16 * 16)
        y0 = max(0, (r.y - _PAD) // 16 * 16)
        x1 = min(w, -(-(r.x + r.w + _PAD) // 16) * 16)
        y1 = min(h, -(-(r.y + r.h + _PAD) // 16) * 16)
        if x1 > x0 and y1 > y0:
            boxes.append((x0, y0, x1, y1))
    if not boxes:
        return None

    def error(get) -> float:
        num = 0.0
        den = 0
        for x0, y0, x1, y1 in boxes:
            ev = raw_mask[y0:y1, x0:x1]
            if not ev.any():
                continue
            ref = get(x0, y0, x1, y1).astype(np.float32)
            d = _chmax(np.abs(ref - act[y0:y1, x0:x1].astype(np.float32)))
            num += float(d[ev].sum())
            den += int(ev.sum())
        return num / den if den else 0.0

    plain = error(lambda x0, y0, x1, y1: exp[y0:y1, x0:x1])
    if plain <= 0.0:
        return None

    best = None
    for q in _JPEG_QUALITIES:
        e = error(lambda x0, y0, x1, y1, q=q: _reencode(
            np.ascontiguousarray(exp[y0:y1, x0:x1]), q))
        if best is None or e < best[0]:
            best = (e, q)
    if best is None or best[0] > _JPEG_GAIN * plain:
        return None

    quality = best[1]
    out = exp.copy()
    for x0, y0, x1, y1 in boxes:
        out[y0:y1, x0:x1] = _reencode(np.ascontiguousarray(exp[y0:y1, x0:x1]), quality)
    return quality, out


# --------------------------------------------------------------------------- #
#  scrollbar
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Band:
    edge: str       # "right" | "bottom"
    width: int


def _uniform_columns(strip: np.ndarray) -> bool:
    """Each column of the strip holds one colour along most of its length."""
    for c in range(strip.shape[1]):
        col = strip[:, c].astype(np.int16)
        ref = np.median(col, axis=0)
        same = (np.abs(col - ref).max(axis=1) <= 3).mean()
        if same < _BAND_UNIFORM:
            return False
    return True


def _band_on_right(exp: np.ndarray, act: np.ndarray) -> int:
    """Width of a scroll-bar band on the right edge of two strips, or 0."""
    w = exp.shape[1]
    diff = _chmax(np.abs(exp.astype(np.int16) - act.astype(np.int16))) > 3
    # The band is where the difference sits: count from the edge inwards.
    width = 0
    for c in range(1, SCROLLBAR_MAX_PX + 2):
        if diff[:, w - c].mean() >= _BAND_COVERAGE:
            width = c
        else:
            break
    if width < SCROLLBAR_MIN_PX or width > SCROLLBAR_MAX_PX:
        return 0
    # The page beside the band did not change: nothing reflowed.
    beside = diff[:, max(0, w - width - 8):w - width]
    if beside.mean() > 0.01:
        return 0
    # Featureless along its length in both images: a track, not content.
    if not (_uniform_columns(exp[:, w - width:]) and _uniform_columns(act[:, w - width:])):
        return 0
    return width


def scrollbar_bands(exp: np.ndarray, act: np.ndarray) -> list[Band]:
    if exp.shape != act.shape:
        return []
    h, w = exp.shape[:2]
    if w < 4 * SCROLLBAR_MAX_PX or h < 4 * SCROLLBAR_MAX_PX:
        return []
    out = []
    # Only the strip that can hold a band, and the columns beside it.
    edge = SCROLLBAR_MAX_PX + 10
    right = _band_on_right(exp[:, -edge:], act[:, -edge:])
    if right:
        out.append(Band("right", right))
    bottom = _band_on_right(np.ascontiguousarray(exp[-edge:].transpose(1, 0, 2)),
                            np.ascontiguousarray(act[-edge:].transpose(1, 0, 2)))
    if bottom:
        out.append(Band("bottom", bottom))
    return out


def _in_band(r: DiffRegion, bands: list[Band], shape) -> Explanation | None:
    h, w = shape
    # Closing can pull a box a couple of pixels past the band's inner edge.
    slack = 2
    for b in bands:
        if b.edge == "right" and r.x >= w - b.width - slack and r.x + r.w <= w:
            return Explanation("scrollbar", f"inside a {b.width}px band on the right edge "
                                            "that changed along its whole length")
        if b.edge == "bottom" and r.y >= h - b.width - slack and r.y + r.h <= h:
            return Explanation("scrollbar", f"inside a {b.width}px band on the bottom edge "
                                            "that changed along its whole length")
    return None
