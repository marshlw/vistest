# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""The sensitive base of v2: candidates and regions, and nothing taken away.

Two functions, both deliberately dumb:

* `candidates` — ΔE00 above the threshold of discernibility, pixel by pixel.
  No consensus with structure, no anti-aliasing veto, no tolerance that grows
  with contrast. Those were filters; here nothing is filtered.
* `group` — candidates close to each other form one region: the mask is
  dilated by `group_px`, split into 8-connected components, and each
  component owns the candidate pixels under it. No opening, so a line one
  pixel high survives; no share of the frame, so a region is as small as a
  number of pixels says.

Every candidate pixel lands in exactly one group. That is the invariant the
accounting of the result rests on (`unassigned_pixels == 0`), and the tests
hold it.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .. import color as _color

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None


@dataclass(frozen=True)
class Group:
    """Candidate pixels that belong together. Box of the pixels, not of the dilation."""

    label: int
    x: int
    y: int
    w: int
    h: int
    pixels: int

    @property
    def fill(self) -> float:
        return self.pixels / float(max(self.w * self.h, 1))


def differs(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Where the RGB differs at all."""
    d = a != b
    return d[..., 0] | d[..., 1] | d[..., 2]


def candidates(lab_exp: np.ndarray, lab_act: np.ndarray, where: np.ndarray,
               jnd: float, ignore: np.ndarray | None = None
               ) -> tuple[np.ndarray, np.ndarray]:
    """-> (ΔE00 map, candidate mask). ΔE00 is computed where the RGB differs."""
    de = _color.delta_e_ciede2000_where(lab_exp, lab_act, where)
    cand = de > jnd
    if ignore is not None:
        cand &= ~ignore
    return de, cand


def group(cand: np.ndarray, group_px: int) -> tuple[np.ndarray, list[Group]]:
    """-> (label map over the candidate pixels, 0 elsewhere; groups by label).

    Groups are returned in a fixed order — top to bottom, then left to right
    by their first pixel — so the same pair gives the same list every time.
    """
    h, w = cand.shape
    labels = np.zeros((h, w), dtype=np.int32)
    if not cand.any():
        return labels, []
    m = cand.astype(np.uint8)
    if group_px > 0:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * group_px + 1,) * 2)
        m = cv2.dilate(m, k)
    _, lab = cv2.connectedComponents(m, connectivity=8, ltype=cv2.CV_32S)
    labels = np.where(cand, lab, 0).astype(np.int32)

    ys, xs = np.nonzero(cand)
    ls = labels[ys, xs]
    order = np.argsort(ls, kind="stable")
    ls, ys, xs = ls[order], ys[order], xs[order]
    starts = np.flatnonzero(np.r_[True, ls[1:] != ls[:-1]])
    ends = np.r_[starts[1:], ls.size]
    out = []
    for s, e in zip(starts, ends, strict=True):
        y0, y1 = int(ys[s:e].min()), int(ys[s:e].max())
        x0, x1 = int(xs[s:e].min()), int(xs[s:e].max())
        out.append(Group(label=int(ls[s]), x=x0, y=y0, w=x1 - x0 + 1,
                         h=y1 - y0 + 1, pixels=int(e - s)))
    out.sort(key=lambda g: (g.y, g.x, g.label))
    return labels, out
