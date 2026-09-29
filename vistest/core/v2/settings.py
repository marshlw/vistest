# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""What the v2 path is configured with, and where each number came from.

Every number here was chosen on the **calibration half** of the browser
corpus (templates table, form, cards, article) and nowhere else. The held-out
half (landing, dark) is measured after the choice and never consulted for it:
a number moved after looking at it stops meaning what the table says.

v2 reads three fields of `DiffConfig` as they are — the size policy
(`fail_on_size_change`, `size_tolerance_px`) and the memory limit
(`max_pixels`) — and nothing else from it. The v1 thresholds (consensus, SSIM,
morphology, severity) have no meaning here.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..settings import Gap

#: What the numbers below were measured on.
CALIBRATION_SAMPLE = (
    "browser corpus, calibration half only: templates table, form, cards, "
    "article — 224 pairs; opencv-python 4.13.0, numpy 2.4, x86-64")

#: The smallest region, in candidate pixels (ΔE00 > 1.0, grouped within 2 px).
#: Noise: the largest group opacity 0.98 leaves is 1 px (table, article; form
#: and cards leave none). Signal: the smallest "largest group" of any SIGNAL
#: pair is 13 px (article/border_radius/plus2px; table and form +2px: 17).
#: The geometric middle is 3.6, rounded up to 4. Every value from 2 to 8
#: gives the same verdicts on the calibration half; 16 misses one pair.
MIN_REGION_PX = Gap(
    value=4,
    noise=1, noise_at="table/opacity/0.98 and article/opacity/0.98",
    signal=13, signal_at="article/border_radius/plus2px",
    sample=CALIBRATION_SAMPLE, measured="2026-09-29")


@dataclass(frozen=True)
class V2Config:
    #: A pixel is a candidate when ΔE00 between the frames is above this.
    #: 1.0 is the threshold of discernibility CIEDE2000 is built around; the
    #: v1 threshold (2.3, "commonly accepted JND") is the edge of what a
    #: person notices without looking for it, and a signal on text sits below
    #: it pixel by pixel even when the ink moved by ΔE00 15 — the changed
    #: pixels of a glyph are its anti-aliased edge, mixed with the paper.
    jnd_delta_e: float = 1.0
    #: Candidates this close belong to one region: the mask is dilated by an
    #: ellipse of this radius before connected components. Proximity, not
    #: shape — nothing is removed, so an underline one pixel high stays.
    group_px: int = 2
    #: The smallest region, in candidate pixels. Not a share of the frame: a
    #: fixed share means a different thing on a screenshot of another size.
    #: A smaller group is still a region — reported as suppressed, with this
    #: rule named — so that every candidate pixel is accounted for.
    min_region_px: int = int(MIN_REGION_PX.value)
