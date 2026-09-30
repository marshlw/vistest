# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""v2, the environment: v1's scroll-bar and JPEG rules, and why no caret rule.

The scroll bar is v1's rule as it is. JPEG detection is v1's as it is; what
a region needs to be called codec noise is not, and two tests here show the
two things v1's test would have let through in v2: a new ink colour within
30 % of the local contrast, and a change hidden in the 5 % of a region v1
leaves unexplained — on a JPEG frame v2's grouping joins a whole page into
one region. The synthetic pairs are the frozen ones of
`tests/benchmark_corpus`, both rasters.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from vistest.core import color, compare, pngio
from vistest.core import explain as v1explain
from vistest.core.v2 import V2Config
from vistest.models import DiffRegion, Verdict

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests" / "benchmark_corpus"
RASTERS = ("", ", thin glyphs")


def _pair(name: str):
    cases = {c["name"]: c for c in json.loads((CORPUS / "manifest.json").read_text(
        encoding="utf-8"))["cases"]}
    c = cases[name]
    return pngio.read(CORPUS / c["expected"]), pngio.read(CORPUS / c["actual"])


# --------------------------------------------------------------------------- #
#  The scroll bar: v1's rule as it is
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("raster", RASTERS)
def test_a_scroll_bar_that_appeared_is_the_environment(raster):
    r = compare(*_pair("scrollbar" + raster), engine="v2")
    assert r.verdict is Verdict.PASS and not r.regions
    assert r.maps["v2_environment"]["scrollbar"] == [{"edge": "right", "width": 12}]
    assert all(s.suppressed_by.startswith("scrollbar: inside a 12px band on the right "
                                          "edge that changed along its whole length (was ")
               for s in r.suppressed)
    assert "Noise explained and suppressed: 1 region(s) by a scroll bar band." in r.notes


def test_a_band_that_is_not_at_the_edge_is_not_a_scroll_bar():
    a, b = _pair("scrollbar")
    moved = np.full_like(b, 250)
    moved[:, :-30] = b[:, 30:]            # the band 30 px in from the edge
    moved[:, -30:] = a[:, -30:]
    assert compare(a, moved, engine="v2").maps["v2_environment"]["scrollbar"] == []


# --------------------------------------------------------------------------- #
#  JPEG: v1's detection, v2's test per region
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("raster", RASTERS)
def test_a_frame_through_a_jpeg_encoder_is_the_environment(raster):
    r = compare(*_pair("jpeg q=75" + raster), engine="v2")
    assert r.verdict is Verdict.PASS and not r.regions
    assert r.maps["v2_environment"]["jpeg"] == 75
    assert all(s.suppressed_by.startswith(
        "jpeg: the baseline re-encoded at JPEG quality 75 reproduces 100% of the changed "
        "pixels within ΔE00 1; what is left is in groups under 4 px (0 of ")
        for s in r.suppressed if not s.suppressed_by.startswith("min-size"))


@pytest.mark.parametrize("raster", RASTERS)
def test_a_quality_v1_does_not_try_is_not_found(raster):
    """v1 tries 50, 60, 70, 75, 80, 85, 90, 95. At 88 the nearest, 90, leaves
    more than half of the error the baseline leaves — no JPEG, and v2 stays red
    (v1 passes this pair through its anti-aliasing filter, not its jpeg rule)."""
    r = compare(*_pair("jpeg q=88" + raster), engine="v2")
    assert r.maps["v2_environment"]["jpeg"] is None and r.verdict is Verdict.FAIL


def test_the_whole_frame_is_re_encoded_once_the_quality_is_known():
    """v1 re-encodes the changed boxes on the 16-px grid. A decoder that
    upsamples chroma reads the neighbouring blocks, so a box comes out
    otherwise than the frame on its outermost pixels — within v1's tolerance,
    not within ΔE00 1. The frame re-encoded is the frame."""
    a, b = _pair("jpeg q=75")
    assert np.array_equal(v1explain.reencode(a, 75), b)
    x0, y0, x1, y1 = 48, 752, 336, 816                  # on the grid, over the promo
    box = v1explain.reencode(np.ascontiguousarray(a[y0:y1, x0:x1]), 75)
    ys, xs = np.nonzero(np.any(box != b[y0:y1, x0:x1], axis=2))
    assert len(ys)
    assert all(y in (0, y1 - y0 - 1) or x in (0, x1 - x0 - 1)
               for y, x in zip(ys, xs, strict=True))


def _lines(ink_block=(90, 96, 108)) -> np.ndarray:
    """A page of thin text: ten lines of 1 px strokes; the fourth line's first
    half in `ink_block`."""
    img = np.full((240, 320, 3), (250, 250, 252), np.uint8)
    img[10:230, 10:310] = 255
    for i, y0 in enumerate(range(20, 220, 20)):
        for x in range(20, 300, 5):
            ink = ink_block if (i == 3 and x < 160) else (90, 96, 108)
            img[y0:y0 + 9, x] = ink
            img[y0 + 8, x:x + 3] = ink
    return img


def _jpeg(img, quality=75) -> np.ndarray:
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    assert ok
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


def test_a_page_of_thin_text_through_jpeg_is_the_environment():
    base = _lines()
    r = compare(base, _jpeg(base), engine="v2")
    assert r.verdict is Verdict.PASS and r.maps["v2_environment"]["jpeg"] == 75


def test_a_new_ink_on_a_jpeg_frame_is_not_codec_noise():
    """Half a line of text in a new ink, ΔE00 17, on a frame that went
    through JPEG. The ringing joins the page into one region; the new ink is
    what the re-encoding leaves, and it stays red."""
    base = _lines()
    act = _jpeg(_lines(ink_block=(130, 100, 112)))
    r = compare(base, act, engine="v2")
    assert r.verdict is Verdict.FAIL and r.maps["v2_environment"]["jpeg"] == 75
    (region,) = r.regions
    note = next(n for n in region.annotations if n["kind"] == "jpeg")
    assert note["text"].startswith("not JPEG re-encoding — the baseline re-encoded at JPEG "
                                   "quality 75 reproduces ")
    assert note["value"] >= V2Config().min_region_px

    #  What v1's test would have said of the same region: every pixel within
    #  30 % of the local contrast — and 5 % of a page-sized region is more
    #  than the half line that changed.
    reenc = v1explain.reencode(base, 75)
    box = DiffRegion(x=region.x, y=region.y, w=region.w, h=region.h)
    left, total = v1explain._unexplained(reenc, act, r.maps["mask"], box,
                                         shifts=((0.0, 0.0),))
    assert left == 0 and total == region.pixel_count
    lab_re, lab_act = color.srgb_to_lab(reenc), color.srgb_to_lab(act)
    new_ink = int(((color.delta_e_ciede2000(lab_re, lab_act) > 1.0)
                   & r.maps["mask"]).sum())
    assert v1explain._small(new_ink, total)


def test_nothing_is_looked_at_when_the_size_changed():
    r = compare(*_pair("page taller +160"), engine="v2")
    assert r.maps["v2_environment"] == {"scrollbar": [], "jpeg": None, "explained": 0}


# --------------------------------------------------------------------------- #
#  The caret: no rule, in v1 or here
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("raster", RASTERS)
def test_there_is_no_caret_rule(raster):
    """A caret is a 2 px line, pixel for pixel what a border or an underline
    added is. v1 has no rule for it: its pixels reach the change mask and end
    up in no region, which is how v1 also loses an underline. v2 keeps it.
    The library hides the caret when it captures (`caret="hide"`)."""
    a, b = _pair("caret" + raster)
    v1 = compare(a, b, engine="v1")
    assert v1.verdict is Verdict.PASS and not v1.regions and not v1.suppressed
    assert v1.unassigned_pixels == v1.changed_pixels > 0
    v2 = compare(a, b, engine="v2")
    assert v2.verdict is Verdict.FAIL
    assert [(g.x, g.y, g.w, g.h) for g in v2.regions] == [(219, 95, 3, 29)]
