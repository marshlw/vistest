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
"""

from __future__ import annotations

from dataclasses import replace

import cv2
import numpy as np
import pytest

from vistest.config import VisTestConfig
from vistest.core import explain, refit
from vistest.core.comparator import compare
from vistest.library.errors import ScreenshotMismatch, suppressed_line
from vistest.models import ChangeKind, Verdict

from . import synthetic as syn

CFG = VisTestConfig.preset_of("balanced").diff


def _text(text="Checkout", *, thickness=4, scale=1.2, ink=20, paper=250, size=(60, 300)):
    img = np.full(size, paper, np.uint8)
    cv2.putText(img, text, (10, 42), 0, scale, ink, thickness, cv2.LINE_AA)
    return img.astype(np.float32)


def _bars(ink=20, paper=250):
    img = np.full((60, 300), paper, np.uint8)
    for i in range(8):
        cv2.line(img, (15 + i * 35, 10), (30 + i * 35, 50), ink, 4, cv2.LINE_AA)
    return img.astype(np.float32)


def _changed(a, b, tol=12.0):
    return np.abs(a - b) > tol


# --------------------------------------------------------------------------- #
#  The re-drawing
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("dx, dy", [(0.4, 0.2), (-0.7, 0.3), (1.0, 0.0), (0.0, -0.25)])
def test_the_shift_is_estimated_not_searched(dx, dy):
    ref = _text()
    act = refit._shift(ref, dx, dy)
    ex, ey = refit.estimate_shift(ref, act, np.ones(ref.shape, bool))
    assert abs(ex - dx) < 0.03 and abs(ey - dy) < 0.03, (ex, ey)


def test_a_shift_beyond_the_reach_is_clipped():
    ref = _text()
    act = refit._shift(ref, 1.8, 0.0)
    ex, _ = refit.estimate_shift(ref, act, np.ones(ref.shape, bool))
    assert ex <= refit.REACH


def test_a_subpixel_shift_is_reproduced_whole():
    ref = _text()
    act = refit._shift(ref, 0.3, -0.2)
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
    ref = _text("Checkout", thickness=2)
    act = _text("Checkeut", thickness=2)
    f = refit.fit(ref, act, _changed(ref, act), 12.0)
    assert f.residual > explain.AA_KEEP_ABOVE, f.sentence()


@pytest.mark.parametrize("ink, paper, new_ink", [(20, 250, 120), (240, 30, 140)])
def test_a_thin_stroke_that_changed_colour_is_not_a_weight(ink, paper, new_ink):
    """At one pixel, 'lighter weight' and 'lighter colour' look the same.

    The ink rule sides with colour: a re-drawing may move an edge, it may not
    change the darkest (or lightest) value of the strokes it draws.
    """
    ref = _text(thickness=1, scale=0.6, ink=ink, paper=paper)
    act = _text(thickness=1, scale=0.6, ink=new_ink, paper=paper)
    f = refit.fit(ref, act, _changed(ref, act), 12.0)
    assert f.residual > 0.9, f.sentence()


def test_the_identity_is_never_beaten_by_a_worse_candidate():
    ref = _text()
    f = refit.fit(ref, ref.copy(), np.ones(ref.shape, bool), 12.0)
    assert f.left == 0 and f.identity


# --------------------------------------------------------------------------- #
#  Step 5: thinning is free, erasing is explained
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def layout():
    return syn.layouts(6)[0]


def test_a_changed_digit_is_no_longer_erased_by_the_per_pixel_filter(layout):
    base = syn.render(layout)
    act = syn.render(layout, price="259 990 RUB")
    r = compare(base, act, cfg=CFG)
    assert r.verdict is Verdict.FAIL, r.summary()
    assert any("overruled" in n for n in r.notes), r.notes

    legacy = compare(base, act, cfg=replace(CFG, explain_noise=False))
    assert legacy.verdict is Verdict.PASS, "the case no longer shows the old veto"


def test_the_old_veto_would_have_erased_it(layout):
    base = syn.render(layout)
    act = syn.render(layout, price="259 990 RUB")
    lab = compare(base, act, cfg=CFG)
    exp_g = lab.maps["expected"]
    act_g = lab.maps["aligned_actual"]
    from vistest.core import antialias, color

    ge = np.clip(color.srgb_to_lab(exp_g)[:, :, 0] * 2.55, 0, 255).astype(np.uint8)
    ga = np.clip(color.srgb_to_lab(act_g)[:, :, 0] * 2.55, 0, 255).astype(np.uint8)
    aa = antialias.antialias_mask(ge, ga)
    diff = np.abs(ge.astype(int) - ga.astype(int)) > 12
    # Most of the changed glyph passes the per-pixel test: that is the bug.
    x0, y0, x1, y1 = 214, 515, 228, 535
    assert aa[y0:y1, x0:x1][diff[y0:y1, x0:x1]].mean() > 0.5


@pytest.mark.parametrize("amount", [0.25, 0.4, 0.6])
def test_a_re_rendered_page_passes_and_every_erasure_is_named(amount):
    base = syn.page()
    r = compare(base, syn.resample_antialias(base, amount), cfg=CFG)
    assert r.verdict is Verdict.PASS, r.summary()
    aa = [s for s in r.suppressed if s.suppressed_by.startswith("antialias:")]
    assert aa, [s.suppressed_by for s in r.suppressed]
    for s in aa:
        assert s.kind is ChangeKind.ANTIALIAS
        assert s.pixel_count > 0 and s.severity == 0.0
        assert "reproduces" in s.suppressed_by
        assert "% of the changed pixels" in s.suppressed_by
        assert " left" in s.suppressed_by


def test_the_sentence_carries_the_residual_that_decided():
    base = syn.page()
    r = compare(base, syn.resample_antialias(base, 0.4), cfg=CFG)
    for s in r.suppressed:
        if not s.suppressed_by.startswith("antialias:"):
            continue
        pct = int(s.suppressed_by.split(" reproduces ")[1].split("%")[0])
        assert pct >= 100 * (1 - explain.AA_KEEP_ABOVE), s.suppressed_by


def test_groups_are_reported_as_regions_not_as_pixels():
    base = syn.page()
    r = compare(base, syn.resample_antialias(base, 0.4), cfg=CFG)
    aa = [s for s in r.suppressed if s.suppressed_by.startswith("antialias:")]
    # A page of text re-rendered is a few dozen lines, not hundreds of specks.
    assert len(aa) < 40, len(aa)
    boxes = [(s.x, s.y, s.x + s.w, s.y + s.h) for s in aa]
    gap = explain.AA_MERGE_GAP
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            assert not (a[0] - gap < b[2] and b[0] - gap < a[2]
                        and a[1] - gap < b[3] and b[1] - gap < a[3]), (a, b)


def test_a_flat_colour_change_is_never_asked():
    base = syn.page()
    act = syn.page(button_color=(150, 152, 158))
    r = compare(base, act, cfg=CFG)
    assert r.verdict is Verdict.FAIL
    assert not any(s.suppressed_by.startswith("antialias:") for s in r.suppressed)


def test_without_the_explanations_the_filter_is_the_old_one():
    base = syn.page()
    r = compare(base, syn.resample_antialias(base, 0.4),
                cfg=replace(CFG, explain_noise=False))
    assert not any((s.suppressed_by or "").startswith("antialias:") for s in r.suppressed)


def test_the_restricted_mask_only_ever_shrinks():
    base = syn.page()
    act = syn.regress_price_changed(base)
    rng = np.random.default_rng(0)
    cand = rng.random(base.shape[:2]) > 0.7
    aa = rng.random(base.shape[:2]) > 0.5
    ge = cv2.cvtColor(base, cv2.COLOR_RGB2GRAY)
    ga = cv2.cvtColor(act, cv2.COLOR_RGB2GRAY)
    out, _ = explain.explain_antialias(cand, aa, ge, ga)
    assert not (out & ~aa).any()


# --------------------------------------------------------------------------- #
#  Where people read it
# --------------------------------------------------------------------------- #
def test_the_failure_message_spells_out_what_was_suppressed(tmp_path, layout):
    base = syn.render(layout)
    act = syn.recompress(syn.render(layout, price="259 990 RUB"), 90)
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
