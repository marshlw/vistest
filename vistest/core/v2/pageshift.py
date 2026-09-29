# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Explanation «the page moved by a fraction of a pixel»: proven on box edges first.

A page drawn a fraction of a pixel away — a `transform`, a fractional
scroll, a zoom that rounds — changes nearly every edge on it and not one
thing it says. What it does is different for different things: box edges,
borders and backgrounds are anti-aliased at the new position (the change is
the old picture moved by the fraction, bilinear); text is snapped to the
pixel grid, and lands either where it was or a whole pixel over, in the
direction of the shift. So the explanation is proven on the page before it
is applied to a region, in two steps, each measured:

1. **The page moved** (`fit`). The shift (dx, dy), both under a pixel and
   not both whole, is fitted on the page's *structure edges* — straight
   edges at least `EDGE_MIN_LEN_PX` long in either frame: box borders, block
   edges, rules; text strokes are shorter. It must move a share of all of
   them (`moved`): the edges that the fraction reproduces and no whole-pixel
   move does. A block that moved by a whole pixel (offset, padding) moves no
   edge by a fraction; a renderer that drew text otherwise moves no box
   edge at all. Then the shift and its whole-pixel neighbours in the same
   direction (`displacements`) must reproduce most of the changed pixels of
   the page (`cover`).
2. **The region is what that move did.** Once the page is proven to have
   moved, a region is explained when its changed pixels are reproduced by
   the displacements, or when it is text redrawn at the new position: the
   properties of `rerender.py` — the same ink (a), the shape within a pixel
   (b), the same paper (c) — and, if it moved as a block by whole pixels,
   a move the page's shift allows (e').

All the moving goes through `core/warp.py` (`sample`, the same bilinear
arithmetic as `shift`, at the pixels that are looked at).
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

import numpy as np

from .. import color as _color
from .. import warp as _warp

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

RULE = "page-shift"

#: A structure edge: the L* gradient (Sobel) above this …
EDGE_GRADIENT = 20.0
#: … along a straight run at least this long. Text strokes are shorter: a
#: stem of 40 px is a heading of 56 px and more, and a box edge is longer.
EDGE_MIN_LEN_PX = 40
#: The shift is searched on quarter pixels and refined to an eighth.
GRID_STEP = 0.25
REFINE_STEP = 0.125


def structure_edges(lab: np.ndarray) -> np.ndarray:
    """Long straight edges of a frame (box borders, block edges, rules), dilated by a pixel."""
    lum = np.ascontiguousarray(lab[..., 0], dtype=np.float32)
    gx = cv2.Sobel(lum, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(lum, cv2.CV_32F, 0, 1, ksize=3)
    vertical = cv2.morphologyEx(
        (np.abs(gx) > EDGE_GRADIENT).astype(np.uint8), cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (1, EDGE_MIN_LEN_PX)))
    horizontal = cv2.morphologyEx(
        (np.abs(gy) > EDGE_GRADIENT).astype(np.uint8), cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (EDGE_MIN_LEN_PX, 1)))
    return cv2.dilate(vertical | horizontal, np.ones((3, 3), np.uint8)) > 0


def _entering(shape: tuple[int, int], d: tuple[float, float], ys: np.ndarray,
              xs: np.ndarray) -> np.ndarray:
    """Pixels a move by `d` fills from outside the frame: nothing in the
    baseline says what they show, so a move neither reproduces nor refutes
    them. (Edge replication, which `warp` uses, would guess.)"""
    h, w = shape
    out = np.zeros(len(ys), bool)
    if d[0] > 0:
        out |= xs < np.ceil(d[0])
    elif d[0] < 0:
        out |= xs >= w - np.ceil(-d[0])
    if d[1] > 0:
        out |= ys < np.ceil(d[1])
    elif d[1] < 0:
        out |= ys >= h - np.ceil(-d[1])
    return out


def _reproduced(exp: np.ndarray, lab_act: np.ndarray, d: tuple[float, float],
                ys: np.ndarray, xs: np.ndarray, tolerance: float) -> np.ndarray:
    """Is the baseline moved by `d` within `tolerance` of the actual frame at
    these pixels — or is the pixel one the move brings in from outside?"""
    moved = _warp.to_uint8(_warp.sample(exp, d[0], d[1], ys, xs))
    lab = _color.srgb_to_lab(moved[None])
    near = _color.delta_e_ciede2000(lab, lab_act[ys, xs][None])[0] <= tolerance
    return near | _entering(exp.shape[:2], d, ys, xs)


def _whole(v: float) -> bool:
    return float(v).is_integer()


def displacements(dx: float, dy: float) -> tuple[list, list]:
    """(whole-pixel moves, all moves) a page shifted by (dx, dy) makes of its content.

    Per axis: the fraction itself, and the whole pixels on either side of it
    — text lands on one of them. (0, 0) is not a move.
    """
    xs = sorted({float(np.floor(dx)), float(np.ceil(dx))})
    ys = sorted({float(np.floor(dy)), float(np.ceil(dy))})
    whole = [(x, y) for x in xs for y in ys if (x, y) != (0.0, 0.0)]
    every = sorted({(x, y) for x in (dx, *xs) for y in (dy, *ys)} - {(0.0, 0.0)})
    return whole, every


@dataclass
class PageShift:
    dx: float
    dy: float
    edges: int                  # structure-edge pixels of the page, either frame
    changed_edges: int          # of them, changed
    moved: int                  # changed edges the fraction reproduces and no whole move does
    covered: int                # changed pixels of the page some displacement reproduces
    changed: int                # changed pixels of the page
    whole: list = field(default_factory=list)
    every: list = field(default_factory=list)
    explained: np.ndarray | None = field(default=None, repr=False)

    @property
    def moved_share(self) -> float:
        return self.moved / self.edges if self.edges else 0.0

    @property
    def cover(self) -> float:
        return self.covered / self.changed if self.changed else 0.0

    def holds(self, min_moved: float, min_cover: float) -> bool:
        return self.moved_share >= min_moved and self.cover >= min_cover

    def text(self) -> str:
        return (f"the page moved by ({self.dx:+g}, {self.dy:+g}) px: "
                f"{100 * self.moved_share:.0f}% of its box edges ({self.moved} of "
                f"{self.edges} px) moved by the fraction and by no whole pixel; the "
                f"move reproduces {100 * self.cover:.0f}% of the changed pixels")


def fit(exp: np.ndarray, lab_exp: np.ndarray, lab_act: np.ndarray, cand: np.ndarray,
        jnd: float, tolerance: float) -> PageShift | None:
    """The fractional shift the page's box edges moved by, and what it reproduces.

    The shift and its proof on the edges are measured at the threshold of
    discernibility, `jnd`: a proof that loosens it proves less. What the
    move then reproduces of the page is measured at `tolerance`, the
    distance by which a bilinear move is allowed to miss what a rasteriser
    drew at the same offset.
    """
    edges = structure_edges(lab_exp) | structure_edges(lab_act)
    ey, ex = np.nonzero(edges & cand)
    if not len(ey):
        return None

    def score(d):
        return int(_reproduced(exp, lab_act, d, ey, ex, jnd).sum())

    steps = np.arange(-1 + GRID_STEP, 1, GRID_STEP)
    trials = [(float(x), float(y)) for x, y in itertools.product(steps, steps)
              if not (_whole(x) and _whole(y))]
    best = max(trials, key=lambda d: (score(d), -abs(d[0]) - abs(d[1])))
    around = [(best[0] + i * REFINE_STEP, best[1] + j * REFINE_STEP)
              for i in (-1, 0, 1) for j in (-1, 0, 1)]
    around = [d for d in around if abs(d[0]) < 1 and abs(d[1]) < 1
              and not (_whole(d[0]) and _whole(d[1]))]
    best = max(around, key=lambda d: (score(d), -abs(d[0]) - abs(d[1])))
    whole, every = displacements(*best)

    by_whole = np.zeros(len(ey), bool)
    for d in whole:
        by_whole |= _reproduced(exp, lab_act, d, ey, ex, jnd)
    moved = int((_reproduced(exp, lab_act, best, ey, ex, jnd) & ~by_whole).sum())

    cy, cx = np.nonzero(cand)
    hit = np.zeros(len(cy), bool)
    for d in every:
        hit |= _reproduced(exp, lab_act, d, cy, cx, tolerance)
    explained = np.zeros(cand.shape, bool)
    explained[cy[hit], cx[hit]] = True
    return PageShift(dx=best[0], dy=best[1], edges=int(edges.sum()),
                     changed_edges=len(ey), moved=moved, covered=int(hit.sum()),
                     changed=len(cy), whole=whole, every=every, explained=explained)


# --------------------------------------------------------------------------- #
#  A region, once the page is proven to have moved
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class RegionShift:
    """What the page's move does for one region."""

    missed: int             # changed pixels of the region no displacement reproduces
    pixels: int
    limit: float            # the share of `missed` a region may keep
    redrawn: object = None  # rerender.Assessment-like record when the move misses more
    reason: str = ""

    @property
    def missed_share(self) -> float:
        return self.missed / self.pixels if self.pixels else 0.0

    @property
    def explained(self) -> bool:
        return self.missed_share <= self.limit or bool(self.redrawn)

    def text(self) -> str:
        if self.missed_share <= self.limit:
            return (f"{100 * (1 - self.missed_share):.0f}% of its {self.pixels} changed px "
                    "are the baseline moved that way")
        return self.reason


def region(ps: PageShift, where: np.ndarray, crop, exp: np.ndarray, act: np.ndarray,
           lab_exp: np.ndarray, lab_act: np.ndarray, *, limit: float, ink_limit: float,
           jnd: float) -> RegionShift:
    """Is this region what the proven move did to the page?

    First by the pixels: the move reproduces all but `limit` of its changed
    pixels. Failing that, as text redrawn at the new position — the
    properties of `rerender.py` measured as they are there: (a) the ink,
    (b) the shape within a pixel, (c) the paper, and a whole-pixel move only
    in the page's direction (e').
    """
    from . import rerender as _rr

    pixels = int(where.sum())
    missed = int((where & ~ps.explained).sum())
    out = RegionShift(missed=missed, pixels=pixels, limit=limit)
    if out.missed_share <= limit:
        return out
    ink = _rr.ink_colour(crop)
    shape = _rr.shape_within(crop)
    paper = _rr.background_unchanged(crop)
    block = _rr.block_shift(exp, act, lab_exp, lab_act, where, jnd=jnd, limit=0.25)
    pure = not block.holds()
    allowed = not pure or (float(block.dx), float(block.dy)) in {tuple(map(float, d))
                                                                 for d in ps.whole}
    ok = ink.holds(ink_limit) and shape.holds() and paper.holds(jnd) and allowed
    move = (f"(e') {block.moved()}, a move the page's shift makes" if pure and allowed
            else f"(e') {block.moved()}, against the page's shift" if pure
            else "(e') not a pure shift")
    reason = "; ".join((f"text redrawn at the new position: {ink.text()}", shape.text(),
                        paper.text(), move))
    return RegionShift(missed=missed, pixels=pixels, limit=limit, redrawn=ok,
                       reason=reason if ok else
                       f"the move misses {100 * out.missed_share:.0f}% of its changed px, "
                       f"and it is not text redrawn: {reason}")
