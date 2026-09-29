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

What v2 reads of `DiffConfig` is listed in `DIFFCONFIG_READ`: the size
policy, the memory limit, the kinds a user asked to ignore, and the two
fields severity is weighted with (severity orders the report and decides
nothing in v2). Every other field — the v1 thresholds, consensus, SSIM,
morphology, alignment, `fail_severity`, the area policy, the presets built
from them — has no effect on v2. `tests/test_engine_v2.py` holds that list
against the code.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..settings import Gap

#: What the numbers below were measured on.
CALIBRATION_SAMPLE = (
    "browser corpus, calibration half only: templates table, form, cards, "
    "article — 228 pairs; opencv-python 4.13.0, numpy 2.4, x86-64")

#: The fields of `DiffConfig` v2 reads. Nothing else in it changes a v2 result.
DIFFCONFIG_READ = (
    "fail_on_size_change", "size_tolerance_px",   # the size policy, as in v1
    "max_pixels",                                  # the memory guard
    "ignore_kinds",                                # kinds a user set aside
    "above_fold_px", "above_fold_weight",          # severity, which orders only
)

#: (d) of the re-rasterisation rule: the share of the page's ink clusters that
#: must hold a changed pixel before a region can be called re-rasterised text
#: (core/v2/rerender.py, `page_text_change`). Above it, the change reached the
#: page; below it, it is local.
#: Noise (it must be ABOVE): the smallest share among the re-rasterisation
#: families — hinting_none, no_lcd_no_subpixel, geometric_precision,
#: full_chromium, os_windows — is 0.549 (39 of 71 clusters, form: the input
#: outlines, checkboxes and icons of a form do not re-rasterise). Signal (it
#: must stay BELOW): the largest share among signal pairs where every region
#: passes (a)–(c), so that (d) alone stands between the pair and a miss, is
#: 0.291 (48 of 165, article/offset/plus1px). The geometric middle is 0.400.
#: The page-shift families (shift_0.25px, 0.5px: 0.25–1.0) are not in the
#: sample: a moved page is step 3, not re-rasterisation.
TEXT_SHARE = Gap(
    value=0.40,
    noise=0.549, noise_at="form/render/* and form/os/windows (39 of 71 clusters)",
    signal=0.291, signal_at="article/offset/plus1px (48 of 165 clusters)",
    sample=CALIBRATION_SAMPLE, measured="2026-09-29")

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
    #: (a) of the re-rasterisation rule: the ink of A and of B may differ by
    #: less than this (ΔE00). The number is the task's "≈ 2", not fitted. On
    #: the calibration half it has room on both sides: in the five
    #: re-rasterisation families 1735 of 1794 regions (96.7 %) read under it,
    #: 1718 of them at 0.0; a colour change of ΔE00 4 reads 3.88–6.19; ΔE00 2
    #: (DISPUTED, not counted) reads 1.74–2.04 — on the line, as it should.
    ink_delta_e: float = 2.0
    #: (d) of the re-rasterisation rule; see TEXT_SHARE.
    min_text_share: float = TEXT_SHARE.value
