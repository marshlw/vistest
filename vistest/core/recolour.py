# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Large flat recolour: a colour change the structural term cannot see.

Consensus lets a pixel through when colour AND structure both changed. That is
what keeps sub-pixel rendering, codec noise and dithering out, and it has one
blind spot that is not bad luck but construction: SSIM compares local mean,
contrast and correlation *after* removing the mean. A uniform shift of colour
over a flat area moves the mean and leaves contrast and correlation exactly
where they were. SSIM stays at 1 inside the area and only drops on its border.
A header repainted from one blue to another — ΔE00 6.7 on every pixel, well
above the 2.3 of a visible difference — reaches segmentation as a 2-pixel line
along its bottom edge, and the opening removes that.

`strong_color` (ΔE > 4 × threshold) is one way past that: a difference too big
to be anything else. This is the second, for a difference that is moderate but
*uniform*: a connected area where ΔE00 exceeds the threshold, large, with the
same ΔE across it. Two conditions, both load-bearing (the measurements are in
`settings.FLAT_RECOLOUR_AREA` and `settings.FLAT_RECOLOUR_CV`):

* area >= 1.25 % of the frame. Uniform noise exists, but small: a scroll bar
  that appeared is uniform and 0.3 % of the frame at most.
* σ/μ of ΔE <= 0.09. Large noise exists, but not uniform: a re-dithered
  gradient is 2.7 % of the frame with σ/μ 0.18; sensor noise covers 43 % with
  0.31.

What this is NOT:

* Not a threshold on ΔE itself. There is no room for one: the lightest header
  recolour measured has μ = 3.86 (generator layout4), a re-dithered gradient
  has μ = 3.09 (layout0). Anything between them would be fitted to the corpus.
* Not a test of direction. The first hypothesis was that a recolour moves all
  pixels along one Lab vector and noise does not. Measured and rejected: on
  both corpora no noise component passes area and uniformity and would be
  stopped by direction alone — it separates nothing the two conditions above
  do not already separate.

Where it stops: a small uniform recolour — a 40×20 badge — sits in the same
place as the scroll bar and is not taken past consensus by this rule. That is
a known limit of the rule, not something left unfinished. It is what the rule
can know without knowing what the element is.

The mask this returns only joins the candidate set. Everything after
consensus — the anti-aliasing test, segmentation, `explain.py` — applies to it
as to anything else.
"""

from __future__ import annotations

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None


def large_flat_recolour(
    de_map: np.ndarray,
    color_hit: np.ndarray,
    *,
    min_area_pct: float,
    max_cv: float,
) -> tuple[np.ndarray, int]:
    """-> (mask of the qualifying components, how many there were).

    `color_hit` is `de_map > delta_e_threshold`; the components are its
    8-connected regions, measured on `de_map` itself.
    """
    empty = np.zeros(de_map.shape, dtype=bool)
    if cv2 is None or not color_hit.any():
        return empty, 0
    n, lab, stats, _ = cv2.connectedComponentsWithStats(
        color_hit.astype(np.uint8), connectivity=8)
    min_px = min_area_pct / 100.0 * de_map.size
    big = [i for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] >= min_px]
    if not big:
        return empty, 0
    flat_labels = lab.ravel()
    de = de_map.ravel().astype(np.float64)
    count = np.bincount(flat_labels, minlength=n).astype(np.float64)
    total = np.bincount(flat_labels, weights=de, minlength=n)
    total2 = np.bincount(flat_labels, weights=de * de, minlength=n)
    keep = []
    for i in big:
        mu = total[i] / count[i]
        sd = np.sqrt(max(total2[i] / count[i] - mu * mu, 0.0))
        if mu > 0 and sd / mu <= max_cv:
            keep.append(i)
    if not keep:
        return empty, 0
    return np.isin(lab, keep), len(keep)
