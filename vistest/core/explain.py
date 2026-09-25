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
              within a tolerance that grows with local contrast — and, if
              that is not enough, a little heavier, lighter or softer
              (core/refit.py). That is subpixel text rendering, hinting, a
              different rasteriser or sensor noise. A glyph that became
              another glyph, a colour that became another colour, an element
              that appeared — none of them is reproduced by moving the old
              picture by a fraction of a pixel or redrawing its strokes.

One more rule runs earlier, in step 5 of the comparator, on groups of
changed pixels rather than on regions:

  antialias   the per-pixel anti-aliasing mask may erase a group only when a
              re-drawing of the baseline reproduces it (`explain_antialias`).
              Otherwise the mask is withdrawn from the group and it goes on
              to segmentation whole. Erased groups come back from here as
              suppressed regions of kind `antialias`.

Every rule writes the same kind of sentence into `suppressed_by`: what was
done to the baseline, and what share of the changed pixels that reproduced —
"rerender: the baseline moved +0.25,+0.00 px, dark strokes 0.25 px heavier
reproduces 97% of the changed pixels (3 of 120 left)". It is a claim about
the pixels, and it is written so that somebody can check it and disagree.

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
from . import refit as _refit
from . import warp as _warp
from .settings import Gap

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
#: Stroke weight (px per side, signed as in core/refit.py) and softness a
#: re-rendered region may also differ by, tried only when a shift alone left
#: between 5% and half of the region. Narrower than the step-5 family
#: (±1 px): here every candidate is paid for in colour and at nine positions,
#: and step 5 has already asked the wide question of the same pixels.
RERENDER_WEIGHTS = (0.0, 0.25, -0.25, 0.5, -0.5)
RERENDER_BLURS = (0.0, 0.5)

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
                    "jpeg", f"the baseline re-encoded at JPEG quality {quality} "
                            f"reproduces {100.0 * (total - left) / total:.0f}% of "
                            f"the changed pixels ({left} of {total} left)"))
        candidates = [r for r in candidates if not r.suppressed_by]

    # 3. Subpixel re-rendering.
    for r in candidates:
        fit = _rerender(exp, act, raw_mask, r)
        left, total = fit.left, fit.total
        if total == 0:
            # Closing fills the gaps between changed pixels and opening can
            # then keep a piece of the fill and drop the pixels around it.
            mark(r, Explanation("morphology", "no changed pixel inside the box; "
                                              "the region is a by-product of closing"))
            continue
        if total and _small(left, total):
            detail = fit.sentence()
            if fit.identity:
                detail += ", within zero-mean sensor noise"
            mark(r, Explanation("rerender", detail))

    _note(notes, counts)
    return regions


# --------------------------------------------------------------------------- #
#  antialias: the per-pixel veto, made to answer for itself
# --------------------------------------------------------------------------- #
#: The per-pixel filter may take a group of changed pixels out only when it
#: covers at least this share of the group; below it, it thins the group and
#: the group goes on to segmentation with what is left.
#:
#: Both thresholds below were fitted on `tests/corpus.generate(6)` with the
#: families split in two (see docs/ARCHITECTURE.ru.md, "antialias"), on
#: OpenCV 4.14 and 5.0: the plateau of no new false failures and no lost
#: regressions is cover 0.30–0.60 × keep_above 0.10–0.50 on both, and the
#: values are the point nearest its median. What bounds it: a changed glyph is
#: 56–78% covered by the per-pixel mask and the best re-drawing leaves 57–81%
#: of it unexplained; sub-pixel rendering residue leaves at most 15% (JPEG
#: ringing up to 47%, which the `jpeg` rule below takes out by name).
AA_COVER = 0.50
#: A covered group is taken out only when re-drawing the baseline (position,
#: weight, softness — see core/refit.py) leaves at most this share of its
#: changed pixels unexplained. Otherwise the per-pixel verdict is withdrawn
#: for the whole group: those pixels are not "a blend of two edges", whatever
#: each of them looks like on its own.
AA_KEEP_ABOVE = 0.30
#: The room around AA_KEEP_ABOVE, as the paragraph above states it, in the
#: shape `tests/test_margins.py` replays: sub-pixel rendering residue leaves at
#: most 15 % of a group unexplained, a changed glyph at least 57 %.
#: Replayed on the frozen corpus on 2026-09-25: residue at most 5.3 %
#: (`combined`), a changed glyph at least 67.2 % (`price changed, thin glyphs`).
AA_KEEP_ABOVE_GAP = Gap(
    value=AA_KEEP_ABOVE,
    noise=0.15, noise_at="sub-pixel rendering residue, generator",
    signal=0.57, signal_at="a changed glyph, generator",
    sample="tests/corpus.generate(6), families split in two, OpenCV 4.14 and 5.0",
    measured="when AA_KEEP_ABOVE was fitted (CHANGELOG: \"The anti-aliasing "
             "filter answers for itself\"); replayed 2026-09-25")
#: Grayscale tolerance of the re-drawing, levels of L*·2.55.
AA_TOLERANCE = 12.0
#: Groups smaller than this are thinned, never asked: they cannot become a
#: region on their own (segmentation keeps 24 px and up, after closing).
AA_MIN_PX = 12
#: Changed pixels closer than this belong to one group (closing radius).
AA_GROUP_PX = 2
#: Explained groups this close are reported as one region (the segmentation
#: gap: max(2 × morph_close_px, 10) with the default config).
AA_MERGE_GAP = 12
_AA_PAD = 4


def explain_antialias(
    candidate: np.ndarray,
    aa: np.ndarray,
    gray_exp: np.ndarray,
    gray_act: np.ndarray,
    *,
    cover: float = AA_COVER,
    keep_above: float = AA_KEEP_ABOVE,
    notes: list[str] | None = None,
) -> tuple[np.ndarray, list[DiffRegion]]:
    """Restrict the anti-aliasing mask to what a re-drawing explains.

    `antialias.antialias_mask` answers, pixel by pixel, "could this be a
    blend of the edge colours around it?". A glyph that became another glyph
    passes that test in most of its pixels: every stroke pixel of the new
    glyph lies between the ink and the paper of the old one. So the per-pixel
    answer is allowed to *thin* a group of changed pixels, and not to erase
    it. Erasing takes the constructive question: can the baseline be drawn
    again — a fraction of a pixel away, a fraction of a pixel heavier,
    a little softer — so that it becomes the actual picture there?

    Returns the restricted mask and one suppressed region per group that was
    erased, each with `suppressed_by` naming the rule, the re-drawing and
    what it left over. Groups the re-drawing did not explain keep none of
    the per-pixel mask and go on to segmentation whole.
    """
    if cv2 is None or not candidate.any() or not aa.any():
        return aa, []
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * AA_GROUP_PX + 1,) * 2)
    grouped = cv2.morphologyEx(candidate.astype(np.uint8), cv2.MORPH_CLOSE, k)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(grouped, connectivity=8)
    out = aa.copy()
    pieces: list[tuple[int, int, int, int, int, _refit.Fit]] = []
    h, w = candidate.shape
    ge = gray_exp.astype(np.float32, copy=False)
    ga = gray_act.astype(np.float32, copy=False)
    lifted = 0
    k3 = np.ones((3, 3), np.uint8)
    for i in range(1, n):
        x, y, bw, bh = (int(stats[i, cv2.CC_STAT_LEFT]), int(stats[i, cv2.CC_STAT_TOP]),
                        int(stats[i, cv2.CC_STAT_WIDTH]), int(stats[i, cv2.CC_STAT_HEIGHT]))
        x0, y0 = max(0, x - _AA_PAD), max(0, y - _AA_PAD)
        x1, y1 = min(w, x + bw + _AA_PAD), min(h, y + bh + _AA_PAD)
        group = (lab[y0:y1, x0:x1] == i) & candidate[y0:y1, x0:x1]
        px = int(group.sum())
        if px < AA_MIN_PX:
            continue
        covered = int((group & aa[y0:y1, x0:x1]).sum())
        if covered < cover * px:
            continue
        e = ge[y0:y1, x0:x1]
        # The same physics as `rerender`: on an edge a re-drawing is allowed
        # an error proportional to the contrast it re-draws.
        contrast = cv2.dilate(e, k3) - cv2.erode(e, k3)
        tol = np.maximum(AA_TOLERANCE, CONTRAST_TOLERANCE * contrast)
        f = _refit.fit(e, ga[y0:y1, x0:x1], group, tol,
                       good_enough=int(MAX_UNEXPLAINED * px))
        if f.residual > keep_above:
            out[y0:y1, x0:x1] &= ~group
            lifted += 1
            continue
        pieces.append((x, y, bw, bh, px, f))
    explained = [_aa_region(cluster) for cluster in _clusters(pieces, AA_MERGE_GAP)]
    if notes is not None and explained:
        notes.append(
            f"Noise explained and suppressed: {len(explained)} by re-drawing the "
            "baseline (anti-aliasing). Each suppressed region names the "
            "re-drawing and what it left over.")
    if notes is not None and lifted:
        notes.append(
            f"Anti-aliasing filter overruled on {lifted} group(s) of changed "
            "pixels: no re-drawing of the baseline (sub-pixel position, stroke "
            "weight, softness) reproduces them, so they were kept whole.")
    return out, explained


def _clusters(pieces, gap: int):
    """Pieces whose boxes are within `gap` of each other, as lists.

    One word re-rendered is a handful of groups; a paragraph is a hundred.
    The report gets one region per cluster, like segmentation gives one
    region per line, and not a hundred lines saying the same thing.
    """
    boxes = [[x, y, x + bw, y + bh, [i]] for i, (x, y, bw, bh, _, _) in enumerate(pieces)]
    merged = True
    while merged:
        merged = False
        out: list[list] = []
        for b in boxes:
            for o in out:
                if (b[0] - gap < o[2] and o[0] - gap < b[2]
                        and b[1] - gap < o[3] and o[1] - gap < b[3]):
                    o[0], o[1] = min(o[0], b[0]), min(o[1], b[1])
                    o[2], o[3] = max(o[2], b[2]), max(o[3], b[3])
                    o[4].extend(b[4])
                    merged = True
                    break
            else:
                out.append(b)
        boxes = out
    return [[pieces[i] for i in sorted(b[4])] for b in boxes]


def _aa_region(cluster) -> DiffRegion:
    x0 = min(p[0] for p in cluster)
    y0 = min(p[1] for p in cluster)
    x1 = max(p[0] + p[2] for p in cluster)
    y1 = max(p[1] + p[3] for p in cluster)
    px = sum(p[4] for p in cluster)
    fits = [p[5] for p in cluster]
    return DiffRegion(
        x=x0, y=y0, w=x1 - x0, h=y1 - y0, kind=ChangeKind.ANTIALIAS, severity=0.0,
        pixel_count=px, fill_ratio=px / float(max((x1 - x0) * (y1 - y0), 1)),
        suppressed_by=f"antialias: {_summary(fits)}")


def _summary(fits: list[_refit.Fit]) -> str:
    """One sentence for several re-drawn pieces, with the numbers that decided.

    One piece: its own sentence. Several: the largest move, weight and
    softening any of them needed, the share reproduced over all of them, and
    the worst piece — the one somebody arguing with the verdict should look at.
    """
    if len(fits) == 1:
        f = fits[0]
        return f.sentence() + (_WITHIN_EDGE if f.identity else "")
    left = sum(f.left for f in fits)
    total = sum(f.total for f in fits)
    move = max(max(abs(f.dx), abs(f.dy)) for f in fits)
    heavy = max((f.weight for f in fits), default=0.0)
    light = -min((f.weight for f in fits), default=0.0)
    soft = max(f.blur for f in fits)
    parts = []
    if move >= _refit.SAME_SHIFT:
        parts.append(f"moved by at most {move:.2f} px")
    if heavy > 0:
        parts.append(f"dark strokes up to {heavy:.2f} px heavier")
    if light > 0:
        parts.append(f"light strokes up to {light:.2f} px heavier")
    if soft:
        parts.append(f"edges softened by up to {soft:.1f} px")
    how = ", ".join(parts) if parts else "as it is"
    worst = max(fits, key=lambda f: f.residual)
    return (f"{len(fits)} pieces; the baseline {how} reproduces "
            f"{100.0 * (1 - left / total):.0f}% of the changed pixels "
            f"({left} of {total} left; worst piece {100.0 * worst.residual:.0f}% "
            f"left, {worst.left} of {worst.total})"
            + (_WITHIN_EDGE if not parts else ""))


#: What "as it is" means when it explains something: the changed pixels sit on
#: edges and differ by less than the edge tolerance allows.
_WITHIN_EDGE = (f", within the tolerance ({AA_TOLERANCE:.0f} levels, or "
                f"{CONTRAST_TOLERANCE:.0%} of the local contrast on an edge)")


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


def _search(ref, act, raw_mask, r, shifts, *, give_up_after: int = 0,
            weight: float = 0.0, blur: float = 0.0):
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

    # The tolerances above describe the baseline as drawn; the re-drawn one
    # is what gets moved and compared.
    ink_bad = None
    if weight or blur:
        drawn = _refit.render(e, _refit.Fit(weight=weight, blur=blur))
        ink_bad = _refit.ink_changed(e, drawn, weight=weight, blur=blur)
        e = drawn

    best = (total + 1, (0.0, 0.0))
    tried = 0
    for dx, dy in shifts:
        # Through the engine's one door for translation, like `align` and
        # `refit`: `_grid` puts every shift on the grid `warp.shift` draws
        # exactly, and that promise only holds for the backend it measured.
        moved = e if dx == 0.0 and dy == 0.0 else _warp.shift(e, dx, dy)
        diff = _chmax(np.abs(moved - a))
        mean_diff = _chmax(np.abs(cv2.blur(moved, win, borderType=cv2.BORDER_REPLICATE)
                                  - a_mean))
        explained = (diff <= edge_tol) | ((diff <= NOISE_PEAK) & (mean_diff <= mean_tol))
        if ink_bad is not None:
            explained &= ~_refit.shift_mask(ink_bad, dx, dy)
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
    move that explains a region, not the first one the loop happened on.

    Every point is put on the grid the backend can draw exactly
    (`warp.applied_shift`), so the shift this rule reports in `suppressed_by`
    is the one it drew. With the default quarter-pixel step that is already
    true on every backend; it stops being free the moment the step changes.
    """
    n = int(round(reach / step))
    pts = {_warp.applied_shift(around[0] + i * step, around[1] + j * step)
           for i in range(-n, n + 1) for j in range(-n, n + 1)}
    return sorted(pts, key=lambda p: (abs(p[0] - around[0]) + abs(p[1] - around[1]), p))


def _rerender(exp, act, raw_mask, r) -> _refit.Fit:
    """The best re-drawing of the region: position first, then stroke weight
    and softness at that position. Returns it as a `refit.Fit`."""
    # Coarse half-pixel pass, then quarter pixels around the best of it.
    left, total, best = _search(exp, act, raw_mask, r, _grid(_REACH, 2 * _STEP),
                                give_up_after=9)
    if total == 0 or left == 0 or left > total // 2:
        return _refit.Fit(dx=best[0], dy=best[1], left=left, total=total)
    fine = [s for s in _grid(_STEP, _STEP, best)
            if abs(s[0]) <= _REACH and abs(s[1]) <= _REACH]
    left2, _, best2 = _search(exp, act, raw_mask, r, fine)
    if left2 < left:
        left, best = left2, best2
    fit = _refit.Fit(dx=best[0], dy=best[1], left=left, total=total)
    if _small(left, total):
        return fit
    # Stroke weight and softness, at the position found and next to it.
    near = [s for s in _grid(_STEP, _STEP, best)
            if abs(s[0]) <= _REACH and abs(s[1]) <= _REACH]
    for t in RERENDER_WEIGHTS:
        for b in RERENDER_BLURS:
            if not t and not b:
                continue
            lw, _, bw = _search(exp, act, raw_mask, r, near, weight=t, blur=b)
            if lw < fit.left:
                fit = _refit.Fit(dx=bw[0], dy=bw[1], weight=t, blur=b,
                                 left=lw, total=total)
                if _small(lw, total):
                    return fit
    return fit


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
