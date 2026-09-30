# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Explanation «text re-rasterisation»: when the renderer changed, and only then.

A renderer that draws the same text again — another hinting mode, grey
anti-aliasing instead of LCD, another build, another OS — changes the *shape*
of glyph edges. It does not change what the text is, what colour it is
written in, what it is written on, and it does not pick a block up and put
it down elsewhere. From two screenshots alone that cannot be told from a
page whose typography changed within a pixel; so the rule does not try. The
engine runs it only when the renderer's canary (`core/renderer.py`) proves
the renderer changed, and says so in a note; with the same renderer, or an
unknown one, no region is taken out as re-rasterised text.

When it runs, a region is re-rasterised text only when all of these hold,
each measured on its own:

(a) `ink_colour` — the ink did not change colour. The core of the strokes —
    the extreme each colour channel reaches away from the paper, found in
    each frame on its own — is compared between the frames (ΔE00). A new
    ink colour moves the core; a re-rasterised edge does not.
(b) `glyph_drift` — the shape agrees to one pixel both ways, after each
    glyph is allowed its own shift along the line of up to K pixels
    (`V2Config.glyph_drift_px`). Binarised at half the ink contrast of their
    own frame, each ink component of A lies within dilate(B, 1) once
    shifted, and each of B within dilate(A, 1). K was measured on the
    calibration half and is 0: a «9» that became an «8» at 9–12 px needs
    one pixel of drift, less than the renderer's own noise needs — so
    `shape_within`, the step-2 form, is what decides, and the drift each
    region would need is printed.
(c) `background_unchanged` — the paper is the same colour, and nothing changed
    away from the ink (farther than `AWAY_PX` from the ink of either frame).
(e) `block_shift` — not a pure shift. If B in the region is A moved by a
    whole (dx, dy) ≠ (0, 0) within a few pixels, it is a block that moved —
    a layout change, not a re-rasterisation — and it is said in words: «the
    block moved by +1 px along y».

(d) `page_text_change` — the share of the page's ink clusters that changed —
    is measured and printed, and decides nothing: with the renderer's change
    proven by its canary, the page's share is not what separates noise from
    a change.

The rule writes what it measured for each property into `suppressed_by`, and
into `DiffRegion.annotations` for regions it refused, so that a person can
check the numbers and disagree. `ink_mass` measures how much ink there is on
each side; it is reported, not used.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .. import color as _color

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None


RULE = "rerender-text"

#: Margin around a region's box: the strokes whose edges changed have their
#: core inside it.
PAD = 3
#: The ink is binarised at this share of the frame's own ink contrast.
INK_SHARE = 0.5
#: (b) tolerance: one pixel, both ways (a 3×3 dilation — a diagonal neighbour
#: is one pixel away).
SHAPE_TOLERANCE_PX = 1
#: (c) "away from the ink": farther than this from the ink of either frame.
#: Anti-aliasing reaches one pixel past the binarised edge; two leaves room
#: for an edge that moved by the one pixel (b) allows.
AWAY_PX = 2


def _chmax(x: np.ndarray) -> np.ndarray:
    return np.maximum(np.maximum(x[..., 0], x[..., 1]), x[..., 2])


def _lab1(rgb) -> np.ndarray:
    px = np.clip(np.rint(np.asarray(rgb, dtype=np.float64)), 0, 255).astype(np.uint8)
    return _color.srgb_to_lab(px.reshape(1, 1, 3))


def delta_e(rgb_a, rgb_b) -> float:
    """ΔE00 between two colours given as RGB triples."""
    return float(_color.delta_e_ciede2000(_lab1(rgb_a), _lab1(rgb_b))[0, 0])


def _hex(rgb) -> str:
    return "#" + "".join(f"{int(v):02x}" for v in np.clip(np.rint(rgb), 0, 255))


# --------------------------------------------------------------------------- #
#  The neighbourhood of a region
# --------------------------------------------------------------------------- #
@dataclass
class Crop:
    """A region's neighbourhood, as both frames see it."""

    x0: int
    y0: int
    a: np.ndarray            # float32 RGB, baseline
    b: np.ndarray            # float32 RGB, actual
    footprint: np.ndarray    # the region's changed pixels, grown by the grouping radius
    changed: np.ndarray      # the region's changed pixels (candidates of this group)
    inner: np.ndarray        # the region's box grown by one pixel

    @property
    def paper_a(self) -> np.ndarray:
        return paper(self.a)

    @property
    def paper_b(self) -> np.ndarray:
        return paper(self.b)


def crop(exp: np.ndarray, act: np.ndarray, labels: np.ndarray, label: int,
         box: tuple[int, int, int, int], group_px: int, pad: int = PAD) -> Crop:
    x, y, w, h = box
    H, W = exp.shape[:2]
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(W, x + w + pad), min(H, y + h + pad)
    changed = labels[y0:y1, x0:x1] == label
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * group_px + 1,) * 2)
    footprint = cv2.dilate(changed.astype(np.uint8), k) > 0
    inner = np.zeros_like(changed)
    inner[max(0, y - 1 - y0):y + h + 1 - y0, max(0, x - 1 - x0):x + w + 1 - x0] = True
    return Crop(x0=x0, y0=y0,
                a=exp[y0:y1, x0:x1].astype(np.float32),
                b=act[y0:y1, x0:x1].astype(np.float32),
                footprint=footprint, changed=changed, inner=inner)


def paper(img: np.ndarray) -> np.ndarray:
    """The most frequent colour of the crop: what the text is written on."""
    q = img.astype(np.uint32)
    packed = (q[..., 0] << 16) | (q[..., 1] << 8) | q[..., 2]
    values, counts = np.unique(packed.ravel(), return_counts=True)
    v = int(values[int(np.argmax(counts))])
    return np.array([(v >> 16) & 255, (v >> 8) & 255, v & 255], dtype=np.float32)


def ink_distance(img: np.ndarray, paper_rgb: np.ndarray) -> np.ndarray:
    """How far each pixel is from the paper, largest channel, in levels."""
    return _chmax(np.abs(img - paper_rgb[None, None, :]))


# --------------------------------------------------------------------------- #
#  (a) the ink did not change colour
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class InkColour:
    delta_e: float
    ink_a: tuple[float, float, float]
    ink_b: tuple[float, float, float]

    def holds(self, limit: float) -> bool:
        return self.delta_e < limit

    def text(self) -> str:
        return (f"(a) ink {_hex(self.ink_a)} → {_hex(self.ink_b)}, "
                f"ΔE00 {self.delta_e:.1f}")


#: The ink colour is read at the `CORE_RANK`-th most extreme value of each
#: channel, not at the single most extreme pixel: one stray pixel does not
#: decide it.
CORE_RANK = 3


def ink_of(img: np.ndarray, paper_rgb: np.ndarray, where: np.ndarray) -> np.ndarray:
    """The colour of the ink where the strokes are fully covered, channel by channel.

    A glyph a pixel or two wide is drawn mostly at partial coverage, and with
    LCD anti-aliasing each channel is covered on its own: the core of a
    stroke is a fringe of three colours, and its median moves with every
    change of hinting. What does not move is the extreme each channel
    reaches somewhere in the region — the one sub-pixel that was fully
    covered. So each channel is read at its extreme away from the paper: in
    the direction it departs from the paper the most (darker for dark ink,
    lighter for light ink, per channel), at the `CORE_RANK`-th value. A new
    ink colour moves that extreme; partial coverage never reaches past it.
    """
    px = img[where]
    if px.size == 0:
        return paper_rgb.copy()
    dev = px - paper_rgb[None, :]
    sign = np.where(dev.max(axis=0) > -dev.min(axis=0), 1.0, -1.0).astype(np.float32)
    toward = np.sort(dev * sign[None, :], axis=0)
    ext = toward[-min(CORE_RANK, len(toward))]
    return paper_rgb + sign * ext


def ink_colour(c: Crop) -> InkColour:
    """(a) ΔE00 between the ink of A and the ink of B, each read in its own frame
    over the region's footprint (`ink_of`)."""
    ia = ink_of(c.a, c.paper_a, c.footprint)
    ib = ink_of(c.b, c.paper_b, c.footprint)
    return InkColour(delta_e=delta_e(ia, ib), ink_a=tuple(map(float, ia)),
                     ink_b=tuple(map(float, ib)))


# --------------------------------------------------------------------------- #
#  (b) the shape agrees to one pixel both ways
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Shape:
    outside_a: int      # ink pixels of A farther than the tolerance from ink of B
    ink_a: int
    outside_b: int
    ink_b: int

    @property
    def outside(self) -> int:
        return self.outside_a + self.outside_b

    def holds(self) -> bool:
        return self.outside == 0

    def text(self) -> str:
        return (f"(b) shape within {SHAPE_TOLERANCE_PX} px: {self.outside_a} of "
                f"{self.ink_a} ink px of A outside B, {self.outside_b} of "
                f"{self.ink_b} of B outside A")


def ink_masks(c: Crop) -> tuple[np.ndarray, np.ndarray]:
    """Ink of each frame, binarised at `INK_SHARE` of its own contrast."""
    out = []
    for img, pap in ((c.a, c.paper_a), (c.b, c.paper_b)):
        d = ink_distance(img, pap)
        top = float(np.where(c.footprint, d, 0.0).max())
        out.append(d >= INK_SHARE * top if top > 0 else np.zeros(d.shape, bool))
    return out[0], out[1]


def shape_within(c: Crop, tol_px: int = SHAPE_TOLERANCE_PX) -> Shape:
    """(b) A ⊆ dilate(B, tol) and B ⊆ dilate(A, tol), counted in the region's box.

    Counted inside the box grown by one pixel: past it the two frames are
    the same (the box holds every changed pixel of the region), and at the
    edge of the crop the dilation would see a cut-off neighbour as missing.
    """
    ia, ib = ink_masks(c)
    k = np.ones((2 * tol_px + 1,) * 2, np.uint8)
    da = cv2.dilate(ia.astype(np.uint8), k) > 0
    db = cv2.dilate(ib.astype(np.uint8), k) > 0
    return Shape(outside_a=int((ia & ~db & c.inner).sum()), ink_a=int((ia & c.inner).sum()),
                 outside_b=int((ib & ~da & c.inner).sum()), ink_b=int((ib & c.inner).sum()))


#: How far the drift each glyph needs is measured, in pixels. A glyph that
#: fits at no drift up to this is a glyph that is not there in the other frame.
GLYPH_DRIFT_REACH_PX = 8


@dataclass(frozen=True)
class GlyphShape:
    """(b) with the glyphs free to drift along the line, each on its own.

    Another renderer moves every glyph a little along the line — another
    hinting rounds each advance its own way, and the error adds up along a
    word — without changing what the glyph is. So each ink component (a
    glyph, or glyphs that touch) of each frame may take its own horizontal
    shift up to `limit` pixels; after it, the component must lie within one
    pixel of the other frame's ink, as in `shape_within`.
    """

    need_px: int | None     # the largest shift a glyph needs; None: one fits at none
    outside: int            # ink px still outside after each glyph's best shift ≤ limit
    glyphs: int             # ink components looked at, both frames
    unfit: int              # of them, those no shift up to the limit fits
    ink_a: int
    ink_b: int
    limit: int

    def holds(self) -> bool:
        return self.need_px is not None and self.need_px <= self.limit

    def text(self) -> str:
        need = (f"the worst needs {self.need_px} px" if self.need_px is not None
                else f"one fits at no shift up to {GLYPH_DRIFT_REACH_PX} px")
        return (f"(b) each glyph within {SHAPE_TOLERANCE_PX} px after a shift along "
                f"the line of up to {self.limit} px: {need}; {self.unfit} of "
                f"{self.glyphs} glyphs do not fit, {self.outside} ink px outside")


def _shift_x(mask: np.ndarray, s: int) -> np.ndarray:
    """`out[y, x] = mask[y, x + s]`, False where that falls off the crop."""
    out = np.zeros_like(mask)
    if s > 0:
        out[:, :-s] = mask[:, s:]
    elif s < 0:
        out[:, -s:] = mask[:, :s]
    else:
        out[:] = mask
    return out


def glyph_drift(c: Crop, limit_px: int, tol_px: int = SHAPE_TOLERANCE_PX,
                reach_px: int = GLYPH_DRIFT_REACH_PX) -> GlyphShape:
    """(b) Per glyph: the smallest horizontal shift that puts it within
    `tol_px` of the other frame's ink, both ways; counted in the region's box.
    """
    ia, ib = ink_masks(c)
    k = np.ones((2 * tol_px + 1,) * 2, np.uint8)
    da = cv2.dilate(ia.astype(np.uint8), k) > 0
    db = cv2.dilate(ib.astype(np.uint8), k) > 0
    shifts = sorted(range(-reach_px, reach_px + 1), key=lambda v: (abs(v), v))
    worst = 0
    none_fit = False
    outside = glyphs = unfit = 0
    for src, dst in ((ia, db), (ib, da)):
        n, lab = cv2.connectedComponents(src.astype(np.uint8), connectivity=8)
        mine = src & c.inner
        comps = np.unique(lab[mine])
        comps = comps[comps > 0]
        if not len(comps):
            continue
        need = np.full(n, -1, np.int64)
        least = np.full(n, np.iinfo(np.int64).max, np.int64)
        for s in shifts:
            bad = np.bincount(lab[mine & ~_shift_x(dst, s)], minlength=n)
            need[(need < 0) & (bad == 0)] = abs(s)
            if abs(s) <= limit_px:
                least = np.minimum(least, bad)
        glyphs += len(comps)
        unfit += int(((need[comps] < 0) | (need[comps] > limit_px)).sum())
        outside += int(least[comps].sum())
        if (need[comps] < 0).any():
            none_fit = True
        else:
            worst = max(worst, int(need[comps].max()))
    return GlyphShape(need_px=None if none_fit else worst, outside=outside,
                      glyphs=glyphs, unfit=unfit, ink_a=int((ia & c.inner).sum()),
                      ink_b=int((ib & c.inner).sum()), limit=limit_px)


# --------------------------------------------------------------------------- #
#  (c) the background did not change
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Background:
    delta_e: float          # between the papers of A and B
    changed_away: int       # changed pixels of the region away from the ink
    away: int               # pixels of the crop away from the ink

    def holds(self, jnd: float) -> bool:
        """The paper did not change discernibly, and no changed pixel is away
        from the ink. `jnd` is the base's own threshold of discernibility."""
        return self.delta_e < jnd and self.changed_away == 0

    def text(self) -> str:
        return (f"(c) paper ΔE00 {self.delta_e:.1f}, {self.changed_away} changed px "
                f"farther than {AWAY_PX} px from the ink")


def background_unchanged(c: Crop) -> Background:
    """(c) The paper of both frames, and the changed pixels away from all ink."""
    ia, ib = ink_masks(c)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * AWAY_PX + 1,) * 2)
    near = cv2.dilate((ia | ib).astype(np.uint8), k) > 0
    away = ~near
    return Background(delta_e=delta_e(c.paper_a, c.paper_b),
                      changed_away=int((c.changed & away).sum()),
                      away=int(away.sum()))


# --------------------------------------------------------------------------- #
#  (d) the change is not local
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class TextChange:
    changed: int            # ink clusters of the page with a changed pixel
    total: int              # ink clusters of the page

    @property
    def share(self) -> float:
        return self.changed / self.total if self.total else 0.0

    def holds(self, min_share: float) -> bool:
        return self.share >= min_share

    def text(self) -> str:
        return (f"(d) {100.0 * self.share:.0f}% of the page's text changed "
                f"({self.changed} of {self.total} ink clusters)")


#: Page ink: a pixel that differs from the 21×21 median around it by more than
#: this many levels (largest channel). Text is sparse, so the median is the
#: paper, whatever colour the paper is at that spot.
PAGE_INK_LEVELS = 40
_PAGE_MEDIAN = 21
#: Letters closer than this merge into one cluster (a word, an icon): the
#: horizontal and vertical reach of the merge.
_CLUSTER_KERNEL = (7, 3)
#: Clusters smaller than this many ink pixels are specks, not text.
_CLUSTER_MIN_PX = 4


def page_ink(img: np.ndarray) -> np.ndarray:
    """Pixels that stand out from the paper around them: glyphs, icons, rules."""
    med = cv2.medianBlur(np.ascontiguousarray(img), _PAGE_MEDIAN)
    return _chmax(np.abs(img.astype(np.int16) - med.astype(np.int16))) > PAGE_INK_LEVELS


def page_text_change(exp: np.ndarray, act: np.ndarray, cand: np.ndarray) -> TextChange:
    """(d) Share of the page's ink clusters that hold a changed pixel.

    The clusters are those of the baseline (and of the actual frame, for
    text that exists only there): letters merged into words by a short
    horizontal reach. A cluster changed when a candidate pixel (ΔE00 above
    the threshold of discernibility) lies on it or within the reach.
    """
    ink = page_ink(exp) | page_ink(act)
    k = cv2.getStructuringElement(cv2.MORPH_RECT, _CLUSTER_KERNEL)
    grown = cv2.dilate(ink.astype(np.uint8), k)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(grown, connectivity=8)
    if n <= 1:
        return TextChange(0, 0)
    ink_px = np.bincount(lab[ink], minlength=n)
    keep = ink_px >= _CLUSTER_MIN_PX
    keep[0] = False
    hit = np.zeros(n, bool)
    hit[np.unique(lab[cand & (grown > 0)])] = True
    return TextChange(changed=int((hit & keep).sum()), total=int(keep.sum()))


# --------------------------------------------------------------------------- #
#  (e) not a pure shift
# --------------------------------------------------------------------------- #
#: How far a block is looked for, in whole pixels along each axis.
SHIFT_REACH_PX = 4


def _moved(dx: int, dy: int) -> str:
    parts = [f"{v:+d} px along {axis}" for v, axis in ((dx, "x"), (dy, "y")) if v]
    return "the block moved by " + " and ".join(parts)


@dataclass(frozen=True)
class BlockShift:
    """(e) Is B, in the region, A moved by whole pixels?

    A re-rasterised glyph changes its edges; a block that moved is the same
    pixels somewhere else. So among the moves (dx, dy) ≠ (0, 0) within
    `SHIFT_REACH_PX`, the one that leaves the fewest changed pixels is found:
    B[p] against A[p − (dx, dy)] for every changed pixel p of the region.
    If what it leaves is at most `limit` of them, the region is a block
    that moved — a layout change, which this rule does not take out.
    """

    dx: int
    dy: int
    left: int           # changed pixels of the region still changed under the move
    changed: int        # changed pixels of the region
    limit: float

    @property
    def residual(self) -> float:
        return self.left / self.changed if self.changed else 1.0

    def holds(self) -> bool:
        return self.residual > self.limit

    def moved(self) -> str:
        return _moved(self.dx, self.dy)

    def text(self) -> str:
        if self.holds():
            return (f"(e) not a pure shift: the closest whole-pixel move "
                    f"({self.dx:+d}, {self.dy:+d}) leaves {100.0 * self.residual:.0f}% "
                    f"of {self.changed} changed px")
        return (f"(e) {self.moved()}: B is A moved, {100.0 * self.residual:.1f}% of "
                f"{self.changed} changed px left")


def _surely_apart(l1: np.ndarray, l2: np.ndarray, jnd: float) -> np.ndarray:
    """Pixels whose ΔE00 is above `jnd` on their L* alone, without computing it.

    ΔE00² = (ΔL'/S_L)² + (ΔC'/S_C)² + (ΔH'/S_H)² + R_T·(ΔC'/S_C)·(ΔH'/S_H), and
    |R_T| ≤ 2, so the last three terms add up to at least (|ΔC'/S_C| −
    |ΔH'/S_H|)² ≥ 0 and ΔE00 ≥ |ΔL*| / S_L. A margin of 1e-4 covers the
    float32 arithmetic of both.
    """
    d = l1 - l2
    m = 0.5 * (l1 + l2) - 50.0
    s_l = 1.0 + 0.015 * m * m / np.sqrt(20.0 + m * m)
    return np.abs(d) / s_l > jnd * (1.0 + 1e-4)


def block_shift(exp: np.ndarray, act: np.ndarray, lab_exp: np.ndarray,
                lab_act: np.ndarray, where: np.ndarray, *, jnd: float, limit: float,
                reach_px: int = SHIFT_REACH_PX, pure_only: bool = False) -> BlockShift:
    """(e) over the region's changed pixels `where` (a full-frame mask).

    `pure_only`: the caller only asks whether the block moved — a move is
    dropped as soon as the pixels it surely leaves (`_surely_apart`) are
    more than `limit` of them, and when none stays under it the answer is
    «no move» ((0, 0), every pixel left) rather than the closest one. The
    move named, when there is one, is the one the full search names.

    `where` is a full-frame mask, or the (ys, xs) of its pixels.
    """
    ys, xs = where if isinstance(where, tuple) else np.nonzero(where)
    n = len(ys)
    H, W = exp.shape[:2]
    best = (0, 0, n + 1)
    allowed = limit * n if pure_only else float("inf")
    #  The shortest move first: of two moves that explain the block equally
    #  well (a regular pattern), the smaller is the one named.
    moves = sorted(((dx, dy) for dy in range(-reach_px, reach_px + 1)
                    for dx in range(-reach_px, reach_px + 1) if dx or dy),
                   key=lambda m: (abs(m[0]) + abs(m[1]), abs(m[1]), m[1], m[0]))
    for dx, dy in moves:
        sy, sx = ys - dy, xs - dx
        inside = (sy >= 0) & (sy < H) & (sx >= 0) & (sx < W)
        left = int((~inside).sum())
        if left >= best[2] or left > allowed:
            continue
        py, px, qy, qx = ys[inside], xs[inside], sy[inside], sx[inside]
        if pure_only:
            #  What lightness alone proves apart first; ΔE00 for the rest only
            #  while the move can still stay under the limit.
            sure = _surely_apart(lab_act[py, px, 0], lab_exp[qy, qx, 0], jnd)
            left += int(sure.sum())
            if left > allowed or left >= best[2]:
                continue
            py, px, qy, qx = py[~sure], px[~sure], qy[~sure], qx[~sure]
        differ = np.any(act[py, px] != exp[qy, qx], axis=1)
        if differ.any():
            de = _color.delta_e_ciede2000(lab_act[py[differ], px[differ]][None],
                                          lab_exp[qy[differ], qx[differ]][None])[0]
            left += int((de > jnd).sum())
        if left < best[2] and left <= allowed:
            best = (dx, dy, left)
            if not left:
                break
    dx, dy, left = best
    if best[2] > n:
        return BlockShift(dx=0, dy=0, left=n, changed=n, limit=limit)
    return BlockShift(dx=dx, dy=dy, left=min(left, n), changed=n, limit=limit)


# --------------------------------------------------------------------------- #
#  Ink mass — measured and reported, not a property
# --------------------------------------------------------------------------- #
def ink_mass(c: Crop) -> tuple[float, float]:
    """How much ink the region holds in A and in B: the sum, over the
    footprint, of each pixel's distance in lightness (L*) from its paper.

    Lightness, not the largest channel: with LCD anti-aliasing a fringe pixel
    is far from the paper in one channel and near it in the others, and
    turning LCD off would read as a loss of ink.
    """
    out = []
    for img, pap in ((c.a, c.paper_a), (c.b, c.paper_b)):
        lab = _color.srgb_to_lab(np.clip(img, 0, 255).astype(np.uint8))
        lp = float(_lab1(pap)[0, 0, 0])
        out.append(float(np.abs(lab[..., 0] - lp)[c.footprint].sum()))
    return out[0], out[1]


# --------------------------------------------------------------------------- #
#  The properties, for one region
# --------------------------------------------------------------------------- #
#: The properties that decide. (d) is measured and printed, and decides
#: nothing: with the renderer's change proven by its canary, a change of the
#: page's text share is not what stands between noise and a miss.
DECIDING = ("a", "b", "c", "e")


@dataclass(frozen=True)
class Assessment:
    """The properties of one region, measured, and the verdict on each."""

    ink: InkColour
    shape: GlyphShape
    background: Background
    text: TextChange
    block: BlockShift
    mass_a: float
    mass_b: float
    ink_limit: float
    share_limit: float
    jnd: float

    @property
    def holds(self) -> dict[str, bool]:
        return {"a": self.ink.holds(self.ink_limit),
                "b": self.shape.holds(),
                "c": self.background.holds(self.jnd),
                "d": self.text.holds(self.share_limit),
                "e": self.block.holds()}

    @property
    def explained(self) -> bool:
        holds = self.holds
        return all(holds[k] for k in DECIDING)

    @property
    def failed(self) -> str:
        holds = self.holds
        return ",".join(k for k in DECIDING if not holds[k])

    @property
    def mass_ratio(self) -> float:
        return self.mass_b / self.mass_a if self.mass_a > 0 else float("nan")

    def sentence(self) -> str:
        return "; ".join((self.ink.text(), self.shape.text(), self.background.text(),
                          self.block.text(),
                          self.text.text().replace("(d)", "(d, not deciding)", 1)))

    def why_not(self) -> str:
        """The failed properties in words, the block's move first when it moved."""
        holds = self.holds
        parts = []
        if not holds["e"]:
            parts.append(self.block.moved())
        for key, prop in (("a", self.ink), ("b", self.shape), ("c", self.background)):
            if not holds[key]:
                parts.append(prop.text())
        return "; ".join(parts)


def assess(c: Crop, text: TextChange, block: BlockShift, *, ink_limit: float,
           share_limit: float, jnd: float, drift_px: int) -> Assessment:
    ma, mb = ink_mass(c)
    return Assessment(ink=ink_colour(c), shape=glyph_drift(c, drift_px),
                      background=background_unchanged(c), text=text, block=block,
                      mass_a=ma, mass_b=mb, ink_limit=ink_limit,
                      share_limit=share_limit, jnd=jnd)
