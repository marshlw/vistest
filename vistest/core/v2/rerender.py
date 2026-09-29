# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Explanation «text re-rasterisation»: four properties, all of them required.

A renderer that draws the same text again — another hinting mode, grey
anti-aliasing instead of LCD, `geometricPrecision`, another build, another
OS — changes the *shape* of glyph edges. It does not change what the text is,
what colour it is written in, what it is written on, and it does not do it
to one word: it does it to the page. A region is re-rasterised text only when
all four of these hold, each measured on its own:

(a) `ink_colour` — the ink did not change colour. The core of the strokes —
    the extreme each colour channel reaches away from the paper, found in
    each frame on its own — is compared between the frames (ΔE00). A new
    ink colour moves the core; a re-rasterised edge does not.
(b) `shape_within` — the shape agrees to one pixel both ways. Binarised at half
    the ink contrast of their own frame, the ink masks satisfy
    A ⊆ dilate(B, 1) and B ⊆ dilate(A, 1). A different glyph, a word that
    moved by two pixels, a line that wrapped elsewhere does not.
(c) `background_unchanged` — the paper is the same colour, and nothing changed
    away from the ink (farther than `AWAY_PX` from the ink of either frame).
(d) `page_text_change` — the change is not local. Re-rasterisation is a
    property of the renderer: it reaches the text of the whole page. When
    the text changed in one block and the rest of the page's text is the
    same, pixel for pixel, the explanation does not apply. Without this (a)–(c)
    cannot tell letter-spacing +0.2 px from a hinting change: both stay within
    a pixel and keep their colour.

The rule writes what it measured for each property into `suppressed_by`, and
into `DiffRegion.annotations` for regions it refused, so that a person can
check the numbers and disagree.

What the four properties cannot see: a change of *all* the text of the page
at once that keeps colour and stays within a pixel — a global
`font-weight`, say, or a global letter-spacing of a fraction of a pixel. (d)
passes because everything changed, and (a)–(c) pass because each glyph stays
within a pixel. `ink_mass` measures how much ink there is on each side; it is
reported, not used — a candidate for a fifth property.
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
#  All four, for one region
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Assessment:
    """The four properties of one region, measured, and the verdict on each."""

    ink: InkColour
    shape: Shape
    background: Background
    text: TextChange
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
                "d": self.text.holds(self.share_limit)}

    @property
    def explained(self) -> bool:
        return all(self.holds.values())

    @property
    def failed(self) -> str:
        return ",".join(k for k, ok in self.holds.items() if not ok)

    @property
    def mass_ratio(self) -> float:
        return self.mass_b / self.mass_a if self.mass_a > 0 else float("nan")

    def sentence(self) -> str:
        return "; ".join((self.ink.text(), self.shape.text(), self.background.text(),
                          self.text.text()))


def assess(c: Crop, text: TextChange, *, ink_limit: float, share_limit: float,
           jnd: float) -> Assessment:
    ma, mb = ink_mass(c)
    return Assessment(ink=ink_colour(c), shape=shape_within(c),
                      background=background_unchanged(c), text=text,
                      mass_a=ma, mass_b=mb, ink_limit=ink_limit,
                      share_limit=share_limit, jnd=jnd)
