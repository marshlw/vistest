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
#: hold a changed pixel (core/v2/rerender.py, `page_text_change`). Since step
#: 2b it is measured and printed and decides nothing: the renderer's change
#: is proven by its canary instead. The number below is step 2's.
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


#: (b) of the re-rasterisation rule: how far along the line each glyph may
#: drift, in whole pixels, before its shape is compared (core/v2/rerender.py,
#: `glyph_drift`). Measured on the calibration half, the rule forced on.
#: Noise (it must be AT LEAST this for the region to be explained): the drift
#: the regions of renderer-change noise need — hinting_none,
#: no_lcd_no_subpixel, full_chromium, os_windows — among those that pass
#: (a), (c) and (e): median 1, 90th percentile 4, largest that fits at all 8;
#: 79 of 1278 fit at no drift up to 8. Signal (it must stay BELOW): the
#: smallest drift at which the change of a pair of the family «real changes
#: drawn by another renderer» disappears — 1 px: a «9» that became an «8»,
#: 9–12 px digits (article/one_char@hinting_none, @full_chromium and
#: cards/one_char@hinting_none): one pixel of drift, and the other digit lies
#: within the pixel of tolerance. The two do not separate; the value is the
#: signal side's: 0. The drift is measured and printed in every sentence; the
#: rule allows none, which makes (b) what it was in step 2.
GLYPH_DRIFT_PX = Gap(
    value=0,
    noise=8, noise_at="article/render/no_lcd_no_subpixel, cards/os/windows (and "
                      "79 regions that fit at no drift up to 8)",
    signal=1, signal_at="article/one_char@hinting_none, article/one_char@full_chromium, "
                        "cards/one_char@hinting_none (9 → 8)",
    sample=CALIBRATION_SAMPLE, measured="2026-09-29")

#: (e) of the re-rasterisation rule: a region whose closest whole-pixel move
#: (dx, dy) ≠ (0, 0), within 4 px, leaves at most this share of its changed
#: pixels changed is a block that moved (core/v2/rerender.py, `block_shift`).
#: Measured on the calibration half, the rule forced on, over the regions
#: that pass (a) and (c). A move (it must stay AT OR BELOW): all 290 regions
#: that step 2 took out in font_size, padding, line_height and
#: element_removed are exact moves, 0.000; and 78 regions of renderer-change
#: noise are moves too, 0.000–0.143 (table, form, cards: the renderer drew
#: the text narrower and the boxes and icons after it moved by 1–3 px).
#: Re-rasterised text (it must be ABOVE): the smallest residual of any other
#: renderer-noise region is 0.442. The geometric middle of 0.143 and 0.442
#: is 0.25. The 78 renderer-moved blocks are called moves, because they are:
#: the pairs that hold them fail.
SHIFT_RESIDUAL = Gap(
    value=0.25,
    noise=0.442, noise_at="the smallest residual of re-rasterised text in "
                          "hinting_none / no_lcd_no_subpixel / full_chromium / "
                          "os_windows",
    signal=0.143, signal_at="table/render/*, table/os/windows [505, 77, 7, 30] — "
                            "a box edge the narrower text moved by 1 px; step 2's "
                            "290 regions: 0.000",
    sample=CALIBRATION_SAMPLE, measured="2026-09-29")


#: Step 3, the page moved by a fraction of a pixel (core/v2/pageshift.py).
#: The share of the page's box edges — straight edges at least 40 px long, in
#: either frame — that the fitted fraction moves and no whole-pixel move
#: reproduces. Noise (it must be AT LEAST this): the smallest share among the
#: page-shift pairs, 0.174 (article/render/shift_0.5px; the others 0.186 to
#: 0.329). Signal and every other noise (it must stay BELOW): the largest
#: share of any other calibration pair, 0.027 (table/render/no_lcd_no_subpixel;
#: then geometric_precision 0.025, cards/font_size/minus1px 0.021 — a block
#: that moved by whole pixels moves no edge by a fraction). The geometric
#: middle is 0.068, rounded to 0.07; ×6.5 apart.
PAGE_SHIFT_MOVED = Gap(
    value=0.07,
    noise=0.174, noise_at="article/render/shift_0.5px",
    signal=0.027, signal_at="table/render/no_lcd_no_subpixel",
    sample=CALIBRATION_SAMPLE, measured="2026-09-29")

#: The share of the page's changed pixels that the move — the fraction and
#: the whole pixels next to it in its direction — reproduces within
#: `MOVE_TOLERANCE`. The task's word: «most». Noise (AT LEAST): the smallest
#: among the page-shift pairs, 0.792 (cards/render/shift_0.25px). Signal: no
#: other pair passes PAGE_SHIFT_MOVED, so none reaches this test; alone it
#: would separate nothing — table/offset/plus1px, a block moved by one whole
#: pixel, is reproduced to 1.000 — which is why the edges are asked first.
PAGE_SHIFT_COVER = Gap(
    value=0.5,
    noise=0.792, noise_at="cards/render/shift_0.25px",
    signal=0.0, signal_at="none: no other pair passes PAGE_SHIFT_MOVED "
                          "(alone, table/offset/plus1px reaches 1.000)",
    sample=CALIBRATION_SAMPLE, measured="2026-09-29")

#: How far (ΔE00) a bilinear move may miss what the rasteriser drew at the
#: same offset and still reproduce the pixel. Noise: over every changed pixel
#: of the eight calibration page-shift pairs, the best displacement misses by
#: at most 0.86 at the 95th percentile (0.63 at the 90th; 96.8% within 1.0).
#: Signal: 4.0, the smallest SIGNAL colour step of the corpus — a recoloured
#: pixel must not be «reproduced» by moving its neighbour. The geometric
#: middle is 1.85; the value is 2.0, the «≈ 2» of (a) — the same notion,
#: «the same colour», one number for it.
MOVE_TOLERANCE = Gap(
    value=2.0,
    noise=0.86, noise_at="95th percentile over the calibration page-shift pairs",
    signal=4.0, signal_at="the smallest SIGNAL colour step (ΔE00 4)",
    sample=CALIBRATION_SAMPLE, measured="2026-09-29")

#: Once the page is proven to have moved, the share of a region's changed
#: pixels the move may miss and the region still be what the move did (a
#: region that misses more can still be text redrawn at the new position:
#: (a), (b), (c), (e')). Noise: of the 52 page-shift regions that fail the
#: redrawn-text test, 50 miss at most 0.082 (form/render/shift_0.25px, a 16 px
#: checkbox); two miss 0.321 and 0.387 (a checkbox at a half pixel, a bookmark
#: icon: strokes a rasteriser draws otherwise than a bilinear move) and stay
#: red — a move that misses a third of a region does not reproduce it. Signal:
#: none; no SIGNAL pair of the corpus passes the page's proof, so none reaches
#: this test. 0.10 is the smallest round share above the noise side.
SHIFT_REGION_MISS = Gap(
    value=0.10,
    noise=0.082, noise_at="form/render/shift_0.25px (288, 498, 16, 16)",
    signal=1.0, signal_at="none: no SIGNAL pair passes the page's proof",
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
    #: (d) of the re-rasterisation rule; see TEXT_SHARE. Printed, not deciding.
    min_text_share: float = TEXT_SHARE.value
    #: (b) of the re-rasterisation rule: how far along the line each glyph may
    #: drift before its shape is compared; see GLYPH_DRIFT_PX.
    glyph_drift_px: int = int(GLYPH_DRIFT_PX.value)
    #: (e) of the re-rasterisation rule: a region whose best whole-pixel move
    #: leaves at most this share of its changed pixels is a block that moved;
    #: see SHIFT_RESIDUAL.
    shift_residual: float = SHIFT_RESIDUAL.value
    #: Step 3, the page moved by a fraction of a pixel (pageshift.py): the
    #: share of the page's box edges the fraction must move; see PAGE_SHIFT_MOVED.
    page_shift_moved: float = PAGE_SHIFT_MOVED.value
    #: … the share of the changed pixels the move must reproduce; PAGE_SHIFT_COVER.
    page_shift_cover: float = PAGE_SHIFT_COVER.value
    #: … how far a moved pixel may miss (ΔE00); MOVE_TOLERANCE.
    move_tolerance: float = MOVE_TOLERANCE.value
    #: … the share of a region's changed pixels the move may miss; SHIFT_REGION_MISS.
    shift_region_miss: float = SHIFT_REGION_MISS.value
