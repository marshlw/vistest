# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""What a rasteriser is allowed to do to a picture, and the search for it.

A rendering difference is a claim: "this is the same picture, drawn again".
This module makes the claim checkable. It knows three things a rasteriser may
legitimately do to the pixels of a baseline without changing what they say:

  position   draw it a fraction of a pixel away (hinting, fractional DPR,
             subpixel positioning of glyphs);
  weight     draw its strokes a fraction of a pixel heavier or lighter (font
             substitution, a different hinting mode, gamma of the blender);
  softness   draw its edges softer (another rasteriser, a scaling pass).

`fit` looks for the smallest combination of the three that turns the
baseline into the actual pixels at the places where they changed, and says
how much of the change that combination leaves unexplained. It does not
decide anything; `core/explain.py` does, and writes the answer into
`suppressed_by` with the numbers from here, so that a person can check it.

None of the three turns one glyph into another, one colour into another, or
adds an element that was not there. That is the whole argument, and the
reason the families are closed: adding a transformation to this list is a
decision about what the engine may call noise.

How the search is shaped
------------------------
The position is *estimated*, not searched: Lucas–Kanade on the window (up
to two iterations, and once more on the re-drawn picture), clipped to
`REACH`. The prototype searched a grid instead — 49 shifts × 20 bases, 980
warps per region — and added about 30% to every comparison; this search
costs a few milliseconds per frame. The weight is a one-dimensional search
on a continuous parameter (`_weighted`): quarter-pixel steps, then an eighth
and a sixteenth around the best, not one step of 3×3 morphology, which is a
whole pixel on each side and nothing in between. Softness is two values.
Weight and softness are also held to `ink_changed`: they may move an edge,
not change the colour of the stroke.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import warp as _warp

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None


#: Largest position change a re-render is allowed, in pixels.
REACH = 1.0
#: Largest stroke-weight change, in pixels per side. Thickness 2 -> 3 of an
#: OpenCV Hershey line (the `font fallback` case of the corpus) measures
#: 0.75 px per side under OpenCV 4.14; half a pixel does not explain it.
MAX_WEIGHT = 1.0
#: Weight grid: coarse pass, then two refinement steps either side of the best.
_WEIGHTS = (0.0, 0.25, -0.25, 0.5, -0.5, 0.75, -0.75, 1.0, -1.0)
_WEIGHT_STEP = 0.125
_WEIGHT_REFINE = 2          # 0.125, then 0.0625
#: Softness (Gaussian sigma, px). 0 — as drawn.
_BLURS = (0.0, 0.5, 1.0)
#: A shift estimate smaller than this is the same as no shift.
SAME_SHIFT = 0.05


@dataclass(frozen=True)
class Fit:
    """The best re-drawing of the baseline found for one piece of the frame."""

    dx: float = 0.0
    dy: float = 0.0
    weight: float = 0.0      # > 0: dark strokes heavier; < 0: light strokes heavier
    blur: float = 0.0
    left: int = 0            # changed pixels the re-drawing does not reproduce
    total: int = 0           # changed pixels asked about

    @property
    def residual(self) -> float:
        return self.left / self.total if self.total else 0.0

    @property
    def reproduced(self) -> float:
        return 1.0 - self.residual

    @property
    def identity(self) -> bool:
        return (abs(self.dx) < SAME_SHIFT and abs(self.dy) < SAME_SHIFT
                and self.weight == 0.0 and self.blur == 0.0)

    def how(self) -> str:
        """The transformation in words: 'moved +0.25,-0.10 px, dark strokes 0.38 px heavier'."""
        parts = []
        if abs(self.dx) >= SAME_SHIFT or abs(self.dy) >= SAME_SHIFT:
            parts.append(f"moved {self.dx:+.2f},{self.dy:+.2f} px")
        if self.weight > 0:
            parts.append(f"dark strokes {self.weight:.2f} px heavier")
        elif self.weight < 0:
            parts.append(f"light strokes {-self.weight:.2f} px heavier")
        if self.blur:
            parts.append(f"edges softened by {self.blur:.1f} px")
        return ", ".join(parts) if parts else "as it is"

    def sentence(self) -> str:
        """'the baseline moved +0.25,+0.00 px, dark strokes 0.25 px heavier
        reproduces 94% of the changed pixels (19 of 312 left)'."""
        return (f"the baseline {self.how()} reproduces "
                f"{100.0 * self.reproduced:.0f}% of the changed pixels "
                f"({self.left} of {self.total} left)")


# --------------------------------------------------------------------------- #
#  The transformations
# --------------------------------------------------------------------------- #
def _weighted(img: np.ndarray, t: float) -> np.ndarray:
    """Strokes `|t|` pixels heavier on each side, `t` continuous in [-1, 1].

    A grayscale dilation by a diamond of radius `|t|`, made of four
    sub-pixel shifts: at `t = 1` it is the 3×3 cross erosion, at `t = 0.3`
    it moves every edge by a third of a pixel. `t > 0` grows the dark side
    of every edge (min), `t < 0` grows the light side (max) — which of the
    two is "the text" depends on the palette, so both are asked.
    """
    if t == 0.0:
        return img
    r = abs(t)
    op = np.minimum if t > 0 else np.maximum
    out = img
    for dx, dy in ((r, 0.0), (-r, 0.0), (0.0, r), (0.0, -r)):
        out = op(out, _warp.shift(img, dx, dy))
    return out


def _soft(img: np.ndarray, sigma: float) -> np.ndarray:
    if not sigma:
        return img
    return cv2.GaussianBlur(img, (0, 0), sigma, borderType=cv2.BORDER_REPLICATE)


#: A re-drawing may move an edge; it may not change the colour of what the
#: edge bounds. Local extremes (5×5) of the re-drawn baseline must stay within
#: this many levels of the baseline's own.
INK_TOLERANCE = 12.0
_K5 = np.ones((5, 5), np.uint8)


def ink_changed(ref: np.ndarray, redrawn: np.ndarray, *, weight: float, blur: float,
                tol: float = INK_TOLERANCE) -> np.ndarray:
    """Pixels where the re-drawing changed the colour of a stroke.

    Stroke weight and softness are only rendering when they leave the ink
    alone. On a stroke one pixel wide they do not: "half a pixel lighter" or
    "half a pixel softer" turns black text grey, and grey text is a colour
    change, not a rasteriser. So every weight and softness candidate is
    checked here, pixel by pixel: a pixel whose 5×5 neighbourhood lost the
    darkest (or the lightest) value the baseline had there is not reproduced.

    Which extreme is checked follows the transformation. Heavier dark
    strokes (`weight > 0`) cannot make ink lighter; what they can erase is a
    thin *light* stroke — white text on a button — so the light extreme is
    checked. Heavier light strokes check the dark extreme. Softness can do
    both. A counter of a small glyph that closes under a heavier weight is
    flagged as well: at that size weight and colour are the same thing, and
    this rule sides with calling it a change.

    A shift is exempt: moving a thin stroke by half a pixel does split it
    into two lighter pixels — that is what anti-aliasing is.
    """
    d = None
    if weight < 0 or blur:
        d = np.abs(cv2.erode(redrawn, _K5) - cv2.erode(ref, _K5))
    if weight > 0 or blur:
        hi = np.abs(cv2.dilate(redrawn, _K5) - cv2.dilate(ref, _K5))
        d = hi if d is None else np.maximum(d, hi)
    if d is None:
        return np.zeros(ref.shape[:2], dtype=bool)
    if d.ndim == 3:
        d = d.max(axis=2)
    return d > tol


def render(base: np.ndarray, fit: Fit) -> np.ndarray:
    """The baseline, re-drawn the way `fit` says."""
    return _warp.shift(_soft(_weighted(base, fit.weight), fit.blur), fit.dx, fit.dy)


# --------------------------------------------------------------------------- #
#  Position estimate
# --------------------------------------------------------------------------- #
def estimate_shift(ref: np.ndarray, act: np.ndarray, window: np.ndarray,
                   *, reach: float = REACH, steps: int = 2) -> tuple[float, float]:
    """Lucas–Kanade on one window: where is `act` relative to `ref`.

    `ref`, `act`: single-channel float32 of the same shape. `window`: bool,
    the pixels that vote (the change and a margin around it — the edge that
    moved is next to the pixels that changed, not always on them).
    Returns (dx, dy) such that `ref` moved by it matches `act`, clipped to
    `reach`. A window with no texture in some direction answers 0 there.
    """
    dx = dy = 0.0
    w = window.astype(np.float32)
    if w.sum() < 3:
        return 0.0, 0.0
    for _ in range(steps):
        moved = _warp.shift(ref, dx, dy)
        gx = cv2.Sobel(moved, cv2.CV_32F, 1, 0, ksize=3) / 8.0
        gy = cv2.Sobel(moved, cv2.CV_32F, 0, 1, ksize=3) / 8.0
        gt = act - moved
        a11 = float((w * gx * gx).sum())
        a12 = float((w * gx * gy).sum())
        a22 = float((w * gy * gy).sum())
        b1 = float((w * gx * gt).sum())
        b2 = float((w * gy * gt).sum())
        # act(x) ≈ moved(x) - d·∇moved  ⇒  d = -A⁻¹ b
        det = a11 * a22 - a12 * a12
        trace = a11 + a22
        if trace <= 1e-6:
            break
        if det <= 1e-6 * trace * trace:
            # Texture in one direction only (a horizontal rule, one stroke):
            # solve along the direction that has it, leave the other at 0.
            if a11 >= a22:
                ddx, ddy = -b1 / a11, 0.0
            else:
                ddx, ddy = 0.0, -b2 / a22
        else:
            ddx = -(a22 * b1 - a12 * b2) / det
            ddy = -(-a12 * b1 + a11 * b2) / det
        dx = float(np.clip(dx + ddx, -reach, reach))
        dy = float(np.clip(dy + ddy, -reach, reach))
        if abs(ddx) < 0.02 and abs(ddy) < 0.02:
            break
    return dx, dy


# --------------------------------------------------------------------------- #
#  The search
# --------------------------------------------------------------------------- #
def shift_mask(mask: np.ndarray, dx: float, dy: float) -> np.ndarray:
    """A pixel mask carried along with the picture it describes."""
    if abs(dx) < 1e-6 and abs(dy) < 1e-6:
        return mask
    return _warp.shift(mask.astype(np.float32), dx, dy) > 0.0


def fit(ref: np.ndarray, act: np.ndarray, evidence: np.ndarray, tol,
        *, window: np.ndarray | None = None, good_enough: int = 0) -> Fit:
    """The re-drawing of `ref` that leaves the fewest `evidence` pixels unexplained.

    ref, act:  single-channel float32 crops of the same shape;
    evidence:  bool, the changed pixels that have to be reproduced;
    tol:       a pixel is reproduced when |redrawn − act| <= tol (scalar or
               an array of the crop's shape);
    window:    pixels that vote for the shift estimate (default: evidence
               grown by one pixel);
    good_enough: stop as soon as at most this many pixels are left.

    Order: shift estimate → weight (coarse, then refined) → softness →
    shift re-estimated on the re-drawn picture. The identity and the pure
    shift are always among the candidates, so the answer is never worse
    than "the baseline as it is".
    """
    total = int(evidence.sum())
    if total == 0:
        return Fit(total=0)
    if window is None:
        window = cv2.dilate(evidence.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0

    best = Fit(left=int(((np.abs(ref - act) > tol) & evidence).sum()), total=total)
    if best.left <= good_enough:
        return best

    # (weight, softness) -> (re-drawn baseline, pixels whose ink it changed)
    drawn: dict[tuple[float, float], tuple[np.ndarray, np.ndarray | None]] = {}

    def redraw(t: float, b: float):
        if (t, b) not in drawn:
            img = _soft(_weighted(ref, t), b)
            ink = ink_changed(ref, img, weight=t, blur=b) if (t or b) else None
            drawn[(t, b)] = (img, ink)
        return drawn[(t, b)]

    def consider(dx: float, dy: float, t: float, b: float) -> bool:
        """Try one re-drawing; True when it is good enough to stop."""
        nonlocal best
        #  What goes into the Fit has to be what was drawn, not what was asked
        #  for: on OpenCV 4.x the two differ by up to half of 1/32 px.
        dx, dy = _warp.applied_shift(dx, dy)
        img, ink = redraw(t, b)
        bad = (np.abs(_warp.shift(img, dx, dy) - act) > tol) & evidence
        if ink is not None:
            bad |= shift_mask(ink, dx, dy) & evidence
        left = int(bad.sum())
        if left < best.left:
            best = Fit(dx=dx, dy=dy, weight=t, blur=b, left=left, total=total)
        return best.left <= good_enough

    dx, dy = estimate_shift(ref, act, window)
    if consider(dx, dy, 0.0, 0.0):
        return best

    # Weight: a coarse pass at the estimated shift, then half a step around.
    for t in _WEIGHTS[1:]:
        if consider(dx, dy, t, 0.0):
            return best
    step = _WEIGHT_STEP
    for _ in range(_WEIGHT_REFINE):
        t0 = best.weight
        for t in (t0 - step, t0 + step):
            if (abs(t) <= MAX_WEIGHT and (t, 0.0) not in drawn
                    and consider(dx, dy, t, 0.0)):
                return best
        step /= 2

    # Softness at the best weight so far.
    tw = best.weight
    for b in _BLURS[1:]:
        if consider(dx, dy, tw, b):
            return best

    # The shift again, on the picture as re-drawn: a heavier stroke moves
    # the edge the first estimate was looking at.
    if best.weight or best.blur:
        img, _ = redraw(best.weight, best.blur)
        dx2, dy2 = estimate_shift(img, act, window)
        if abs(dx2 - best.dx) >= SAME_SHIFT or abs(dy2 - best.dy) >= SAME_SHIFT:
            consider(dx2, dy2, best.weight, best.blur)
    return best
