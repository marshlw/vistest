# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""The anti-aliasing filter thins; only a named re-drawing erases.

`antialias.antialias_mask` answers per pixel "could this be a blend of two
edge colours?". Most pixels of a glyph that became another glyph answer yes.
Step 5 of the comparator used to take that answer as final and a changed
digit disappeared before segmentation. Now a group of changed pixels leaves
the frame whole only through `explain.explain_antialias`, which re-draws the
baseline (core/refit.py) and writes what it did and what it left over into
`suppressed_by`.

Nothing here is drawn with OpenCV at run time
---------------------------------------------

This file used to draw its own pages and its own words with `cv2.putText`,
and `cv2` rasterises text differently in 4.x and in 5.x: the same test then
answered differently depending on which OpenCV was installed. That is the
defect the frozen corpus was made for, and the test that guards the corpus
had it too. So the pictures come from one of two places, and neither of them
is a call to a drawing routine:

* **cases** — "the engine catches / does not catch this change" — take their
  pair from the frozen corpus (`tests/benchmark_corpus`, `corpus.load()`),
  every raster of it. What is on disk is what the engine sees, on any
  OpenCV, for ever. Sub-pixel re-rendering of a frozen page is written here
  in numpy (`_resampled`), not asked of `cv2.warpAffine`.

* **invariants** — "a pixel above the threshold is never suppressed", "the
  weight may not change the colour of a stroke" — build their own picture
  out of `np.full` and array slices. `arr[10:20, 5:6] = 120` is the same
  1356 pixels on every version of every library, which is the point: an
  invariant has to be checkable without a rasteriser having an opinion.

`_moved` below is the file's own bilinear shift, kept separate from the
engine's `core/warp.py` on purpose: a test that asks the engine to undo its
own arithmetic proves less than one that hands it a picture somebody else
built.
"""

from __future__ import annotations

import functools
from dataclasses import replace

import numpy as np
import pytest

from vistest.config import VisTestConfig
from vistest.core import antialias, color, explain, refit
from vistest.core.comparator import compare
from vistest.library.errors import ScreenshotMismatch, suppressed_line
from vistest.models import ChangeKind, Verdict

from . import corpus as cp

CFG = VisTestConfig.preset_of("balanced").diff

#: Every frozen raster is exercised, not the convenient one. A raster added to
#: `corpus.RENDERS` and to disk is picked up here without editing this file.
RASTERS = [r.suffix for r in cp.RENDERS]
RASTER_IDS = [r.key for r in cp.RENDERS]


# --------------------------------------------------------------------------- #
#  The pictures
# --------------------------------------------------------------------------- #
@functools.lru_cache(maxsize=1)
def _frozen() -> dict[str, cp.Case]:
    return {c.name: c for c in cp.load()}


def _pair(family: str, raster: str) -> tuple[np.ndarray, np.ndarray]:
    """The frozen `expected, actual` of one corpus case, in one raster."""
    case = _frozen()[family + raster]
    return case.expected, case.actual


def _page(raster: str) -> np.ndarray:
    """The frozen page every case of a raster starts from."""
    return _frozen()["identical" + raster].expected


def _moved(img: np.ndarray, dx: float, dy: float) -> np.ndarray:
    """Bilinear sub-pixel shift, edge replicated. numpy: exact everywhere."""
    h, w = img.shape[:2]
    ix, fx = int(np.floor(dx)), dx - np.floor(dx)
    iy, fy = int(np.floor(dy)), dy - np.floor(dy)

    def at(oy: int, ox: int) -> np.ndarray:
        ys = np.clip(np.arange(h) - oy, 0, h - 1)
        xs = np.clip(np.arange(w) - ox, 0, w - 1)
        return img[ys][:, xs].astype(np.float32)

    return ((1 - fy) * ((1 - fx) * at(iy, ix) + fx * at(iy, ix + 1))
            + fy * ((1 - fx) * at(iy + 1, ix) + fx * at(iy + 1, ix + 1)))


def _resampled(page: np.ndarray, amount: float) -> np.ndarray:
    """The page rendered again with a different sub-pixel origin.

    What a browser upgrade or another hinting mode does to a screenshot, and
    what `synthetic.resample_antialias` used to ask `cv2.warpAffine` for.
    """
    return np.clip(_moved(page, amount, amount * 0.5) + 0.5, 0, 255).astype(np.uint8)


def _luma(rgb: np.ndarray) -> np.ndarray:
    """Rec.601 grey, written out: the arithmetic `cv2.cvtColor` would do."""
    return np.clip(rgb @ np.float32([0.299, 0.587, 0.114]) + 0.5, 0, 255).astype(np.uint8)


def _stroke(img: np.ndarray, p0, p1, width: float, ink: float) -> None:
    """One anti-aliased stroke, laid down as coverage arithmetic.

    Coverage of a pixel = how far its centre is inside the stroke, clipped to
    [0, 1] — the same picture a rasteriser draws, and none of its opinions.
    """
    h, w = img.shape
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    (x0, y0), (x1, y1) = p0, p1
    dx, dy = float(x1 - x0), float(y1 - y0)
    t = np.clip(((xx - x0) * dx + (yy - y0) * dy) / (dx * dx + dy * dy), 0.0, 1.0)
    away = np.hypot(xx - (x0 + t * dx), yy - (y0 + t * dy))
    cover = np.clip(width / 2.0 + 0.5 - away, 0.0, 1.0)
    img[:] = img * (1.0 - cover) + float(ink) * cover


def _bars(ink: float = 20.0, paper: float = 250.0) -> np.ndarray:
    """Eight anti-aliased diagonal strokes: edges a rasteriser could move."""
    img = np.full((60, 300), paper, np.float32)
    for i in range(8):
        _stroke(img, (15 + i * 35, 10), (30 + i * 35, 50), 4, ink)
    return img


def _blocks(ink: float = 20.0, paper: float = 250.0) -> np.ndarray:
    """Axis-aligned strokes with hard edges: array slices, nothing else."""
    img = np.full((60, 300), paper, np.float32)
    for i in range(8):
        img[10:50, 15 + i * 35:19 + i * 35] = ink
    for row in range(3):
        img[12 + row * 16:14 + row * 16, 10:290] = ink
    return img


def _hairlines(ink: float, paper: float = 250.0, n: int = 12) -> np.ndarray:
    """Strokes one pixel wide — where weight and colour look the same."""
    img = np.full((40, 200), float(paper), np.float32)
    for i in range(n):
        img[8:32, 10 + i * 15] = ink
    return img


def _glyphs(swap: int | None = None, ink: float = 20.0, paper: float = 250.0) -> np.ndarray:
    """A line of eight identical glyph-like shapes; `swap` makes one of them
    a different shape — the same ink, in other places."""
    img = np.full((60, 300), paper, np.float32)
    for i in range(8):
        x = 12 + i * 36
        img[14:46, x:x + 3] = ink            # stem
        img[14:17, x:x + 20] = ink           # top bar
        img[28:31, x:x + 16] = ink           # middle bar
        img[43:46, x:x + 20] = ink           # bottom bar
    if swap is not None:
        x = 12 + swap * 36
        img[14:46, x:x + 22] = paper         # that glyph is gone
        img[14:46, x + 9:x + 12] = ink       # another one takes its place
        img[14:17, x:x + 20] = ink
        img[43:46, x + 4:x + 16] = ink
    return img


def _changed(a, b, tol=12.0):
    return np.abs(a - b) > tol


# --------------------------------------------------------------------------- #
#  The re-drawing — invariants, on pictures written by arithmetic
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("dx, dy", [(0.4, 0.2), (-0.7, 0.3), (1.0, 0.0), (0.0, -0.25)])
def test_the_shift_is_estimated_not_searched(dx, dy):
    ref = _blocks()
    act = _moved(ref, dx, dy)
    ex, ey = refit.estimate_shift(ref, act, np.ones(ref.shape, bool))
    assert abs(ex - dx) < 0.03 and abs(ey - dy) < 0.03, (ex, ey)


def test_a_shift_beyond_the_reach_is_clipped():
    ref = _blocks()
    act = _moved(ref, 1.8, 0.0)
    ex, _ = refit.estimate_shift(ref, act, np.ones(ref.shape, bool))
    assert ex <= refit.REACH


def test_a_subpixel_shift_is_reproduced_whole():
    """And the sentence names the shift it found, to the last digit it prints.

    The shift is an arbitrary fraction, not a round one: `warp.shift` draws
    the offset it was given, so the printed number is checkable against the
    one the picture was built with. That it does — on both stages that
    translate a picture — is `tests/test_warp.py`.
    """
    ref = _blocks()
    act = _moved(ref, 0.3, -0.2)
    f = refit.fit(ref, act, _changed(ref, act), 12.0)
    assert f.left == 0 and f.total > 500
    assert "moved +0.30,-0.20 px" in f.sentence()


@pytest.mark.parametrize("t", [0.25, -0.25, 0.5, -0.5])
def test_a_fractional_stroke_weight_is_found(t):
    ref = _bars()
    act = refit._weighted(ref, t)
    f = refit.fit(ref, act, _changed(ref, act), 12.0)
    assert f.reproduced >= 0.9, f.sentence()
    assert f.weight * t > 0 and abs(f.weight - t) <= 0.13, f.sentence()
    word = "dark" if t > 0 else "light"
    assert f"{word} strokes" in f.sentence()


def test_weight_is_searched_in_fractions_not_in_one_morphology_step():
    ref = _bars()
    act = refit._weighted(ref, 0.3)
    f = refit.fit(ref, act, _changed(ref, act), 12.0)
    # A 3x3 erosion would be a whole pixel; the answer is a fraction of one.
    assert 0.0 < f.weight < 1.0
    assert f.weight % 0.25 != 0 or f.reproduced >= 0.9


def test_another_glyph_is_not_a_re_drawing():
    """The same ink in other places is not a position, a weight or a softness."""
    ref = _glyphs()
    act = _glyphs(swap=3)
    f = refit.fit(ref, act, _changed(ref, act), 12.0)
    assert f.residual > explain.AA_KEEP_ABOVE, f.sentence()


@pytest.mark.parametrize("ink, paper, new_ink", [(20, 250, 120), (240, 30, 140)])
def test_a_thin_stroke_that_changed_colour_is_not_a_weight(ink, paper, new_ink):
    """At one pixel, 'lighter weight' and 'lighter colour' look the same.

    The ink rule sides with colour: a re-drawing may move an edge, it may not
    change the darkest (or lightest) value of the strokes it draws.
    """
    ref = _hairlines(ink, paper)
    act = _hairlines(new_ink, paper)
    f = refit.fit(ref, act, _changed(ref, act), 12.0)
    assert f.residual > 0.9, f.sentence()


def test_the_identity_is_never_beaten_by_a_worse_candidate():
    ref = _blocks()
    f = refit.fit(ref, ref.copy(), np.ones(ref.shape, bool), 12.0)
    assert f.left == 0 and f.identity


# --------------------------------------------------------------------------- #
#  Step 5: thinning is free, erasing is explained — on the frozen corpus
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("raster", RASTERS, ids=RASTER_IDS)
def test_a_changed_digit_is_no_longer_erased_by_the_per_pixel_filter(raster):
    base, act = _pair("price changed", raster)
    r = compare(base, act, cfg=CFG)
    assert r.verdict is Verdict.FAIL, r.summary()
    assert any("overruled" in n for n in r.notes), r.notes


#: Did the per-pixel filter alone decide the verdict on this raster? On the
#: thin one it erased the changed price whole and the pair passed — the bug
#: this file exists for. On the heavier one the same change leaves enough
#: behind for the old filter to fail anyway. Both are on disk, so both are
#: named: one raster is a case, two are a statement about stroke thickness.
_OLD_VETO_PASSES = {"": False, ", thin glyphs": True}


@pytest.mark.parametrize("raster", RASTERS, ids=RASTER_IDS)
def test_the_old_veto_kept_nothing_where_the_strokes_are_thin(raster):
    base, act = _pair("price changed", raster)
    legacy = compare(base, act, cfg=replace(CFG, explain_noise=False))
    erased = _OLD_VETO_PASSES[raster]
    assert (legacy.verdict is Verdict.PASS) is erased, legacy.summary()
    assert bool(legacy.regions) is not erased, [x.kind.value for x in legacy.regions]


@pytest.mark.parametrize("raster", RASTERS, ids=RASTER_IDS)
def test_the_old_veto_would_have_erased_it(raster):
    """Pixel by pixel, on every raster: the filter says yes to the new glyph."""
    base, act = _pair("price changed", raster)
    lab = compare(base, act, cfg=CFG)

    def lightness(rgb: np.ndarray) -> np.ndarray:
        return np.clip(color.srgb_to_lab(rgb)[:, :, 0] * 2.55, 0, 255).astype(np.uint8)

    ge = lightness(lab.maps["expected"])
    ga = lightness(lab.maps["aligned_actual"])
    aa = antialias.antialias_mask(ge, ga)
    diff = np.abs(ge.astype(int) - ga.astype(int)) > 12
    # The price is the only thing that changed on this page, so the changed
    # pixels are the changed glyphs. Most of them pass the per-pixel test:
    # that is the bug.
    assert diff.any()
    assert aa[diff].mean() > 0.5


@pytest.mark.parametrize("amount", [0.25, 0.4, 0.6])
@pytest.mark.parametrize("raster", RASTERS, ids=RASTER_IDS)
def test_a_re_rendered_page_passes_and_every_erasure_is_named(raster, amount):
    base = _page(raster)
    r = compare(base, _resampled(base, amount), cfg=CFG)
    assert r.verdict is Verdict.PASS, r.summary()
    aa = [s for s in r.suppressed if s.suppressed_by.startswith("antialias:")]
    assert aa, [s.suppressed_by for s in r.suppressed]
    for s in aa:
        assert s.kind is ChangeKind.ANTIALIAS
        assert s.pixel_count > 0 and s.severity == 0.0
        assert "reproduces" in s.suppressed_by
        assert "% of the changed pixels" in s.suppressed_by
        assert " left" in s.suppressed_by


@pytest.mark.parametrize("raster", RASTERS, ids=RASTER_IDS)
def test_the_sentence_carries_the_residual_that_decided(raster):
    base = _page(raster)
    r = compare(base, _resampled(base, 0.4), cfg=CFG)
    for s in r.suppressed:
        if not s.suppressed_by.startswith("antialias:"):
            continue
        pct = int(s.suppressed_by.split(" reproduces ")[1].split("%")[0])
        assert pct >= 100 * (1 - explain.AA_KEEP_ABOVE), s.suppressed_by


@pytest.mark.parametrize("raster", RASTERS, ids=RASTER_IDS)
def test_groups_are_reported_as_regions_not_as_pixels(raster):
    base = _page(raster)
    r = compare(base, _resampled(base, 0.4), cfg=CFG)
    aa = [s for s in r.suppressed if s.suppressed_by.startswith("antialias:")]
    # A page of text re-rendered is a few dozen lines, not hundreds of specks.
    assert len(aa) < 40, len(aa)
    boxes = [(s.x, s.y, s.x + s.w, s.y + s.h) for s in aa]
    gap = explain.AA_MERGE_GAP
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            assert not (a[0] - gap < b[2] and b[0] - gap < a[2]
                        and a[1] - gap < b[3] and b[1] - gap < a[3]), (a, b)


@pytest.mark.parametrize("raster", RASTERS, ids=RASTER_IDS)
def test_a_flat_colour_change_is_never_asked(raster):
    base, act = _pair("button color", raster)
    r = compare(base, act, cfg=CFG)
    assert r.verdict is Verdict.FAIL
    assert not any(s.suppressed_by.startswith("antialias:") for s in r.suppressed)


@pytest.mark.parametrize("raster", RASTERS, ids=RASTER_IDS)
def test_without_the_explanations_the_filter_is_the_old_one(raster):
    base = _page(raster)
    r = compare(base, _resampled(base, 0.4), cfg=replace(CFG, explain_noise=False))
    assert not any((s.suppressed_by or "").startswith("antialias:") for s in r.suppressed)


def test_the_restricted_mask_only_ever_shrinks():
    """Whatever the two masks are, the result is a subset of the one it restricts."""
    base = np.stack([_glyphs()] * 3, axis=-1).astype(np.uint8)
    act = np.stack([_glyphs(swap=3)] * 3, axis=-1).astype(np.uint8)
    rng = np.random.default_rng(0)
    cand = rng.random(base.shape[:2]) > 0.7
    aa = rng.random(base.shape[:2]) > 0.5
    out, _ = explain.explain_antialias(cand, aa, _luma(base), _luma(act))
    assert not (out & ~aa).any()


# --------------------------------------------------------------------------- #
#  Where people read it
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("raster", RASTERS, ids=RASTER_IDS)
def test_the_failure_message_spells_out_what_was_suppressed(tmp_path, raster):
    # `text overflow`: a real regression on a page that also carries noise the
    # filter took out, so the message has both a failure and an "also:".
    base, act = _pair("text overflow", raster)
    r = compare(base, act, cfg=CFG)
    assert r.verdict is Verdict.FAIL and r.suppressed
    err = ScreenshotMismatch.build(
        name="checkout", platform="", result=r, reason="test",
        baseline=tmp_path / "b.png", actual=tmp_path / "a.png", diff=None,
        report=None, limits={})
    text = str(err)
    assert "also:" in text
    first = r.suppressed[0]
    assert suppressed_line(first) in text
    assert first.suppressed_by in text


def test_the_line_reads_the_same_from_a_region_and_from_a_report_row():
    from vistest.models import DiffRegion

    region = DiffRegion(x=3, y=4, w=10, h=6, kind=ChangeKind.ANTIALIAS,
                        suppressed_by="antialias: the baseline moved +0.25,+0.00 px "
                                      "reproduces 94% of the changed pixels (3 of 50 left)")
    row = {"kind": "antialias", "x": 3, "y": 4, "w": 10, "h": 6,
           "suppressed_by": region.suppressed_by}
    assert suppressed_line(region) == suppressed_line(row)
    assert suppressed_line(row).startswith("10x6 at (3, 4): antialias: ")
    other = dict(row, kind="noise", suppressed_by="rerender: ... (was text)")
    assert suppressed_line(other) == "noise 10x6 at (3, 4): rerender: ... (was text)"
