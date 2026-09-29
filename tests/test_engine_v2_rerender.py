# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""v2, the explanation «text re-rasterisation»: five properties, one test each.

Each property is exercised on pictures built by hand, where the answer is
known by construction: a stroke redrawn a pixel over, a stroke two pixels
over, glyphs that drift along the line, the same stroke in another ink, on
another paper, with a speck of change away from it; a page where every word
changed, and one where one did; a block moved by a whole pixel. Then the
rule end to end — only where the renderer is proven to have changed — and
the numbers chosen on the calibration half replayed on the pairs they were
measured on.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

from vistest.core import compare
from vistest.core.v2 import V2Config
from vistest.core.v2 import base as v2base
from vistest.core.v2 import rerender as rr
from vistest.core.v2.settings import GLYPH_DRIFT_PX, MIN_REGION_PX, SHIFT_RESIDUAL, TEXT_SHARE
from vistest.models import ChangeKind, Verdict

ROOT = Path(__file__).resolve().parents[1]
PAPER = (255, 255, 255)
INK = (31, 41, 55)            # #1f2937

#: Two canaries a pixel apart: the renderer changed. And the same one twice.
CHANGED = (np.zeros((2, 2, 3), np.uint8), np.full((2, 2, 3), 255, np.uint8))
SAME = (np.zeros((2, 2, 3), np.uint8), np.zeros((2, 2, 3), np.uint8))


def _blank(h=40, w=60, paper=PAPER) -> np.ndarray:
    img = np.empty((h, w, 3), np.uint8)
    img[:] = paper
    return img


def _stroke(img, x, y, w=2, h=12, ink=INK, edge=0.5, paper=PAPER):
    """A vertical stroke: `w` full-ink columns and one column of `edge` coverage
    on each side — what a glyph stem looks like at 1x."""
    ink_ = np.array(ink, float)
    pap = np.array(paper, float)
    img[y:y + h, x:x + w] = ink
    mix = np.rint(edge * ink_ + (1 - edge) * pap).astype(np.uint8)
    img[y:y + h, x - 1] = mix
    img[y:y + h, x + w] = mix
    return img


def _crop(a: np.ndarray, b: np.ndarray, group_px: int = 2) -> rr.Crop:
    """The whole picture as one region's neighbourhood."""
    la, lb = v2base._color.srgb_to_lab(a), v2base._color.srgb_to_lab(b)
    _, cand = v2base.candidates(la, lb, v2base.differs(a, b), 1.0)
    labels, groups = v2base.group(cand, group_px)
    assert groups, "the pictures must differ"
    lab = np.where(cand, 1, 0).astype(np.int32)
    ys, xs = np.nonzero(cand)
    box = (int(xs.min()), int(ys.min()), int(xs.max() - xs.min() + 1),
           int(ys.max() - ys.min() + 1))
    return rr.crop(a, b, lab, 1, box, group_px)


# --------------------------------------------------------------------------- #
#  (a) the ink did not change colour
# --------------------------------------------------------------------------- #
def test_a_the_same_ink_redrawn_elsewhere_keeps_its_colour():
    a = _stroke(_blank(), 20, 10, edge=0.5)
    b = _stroke(_blank(), 21, 10, edge=0.3)
    ic = rr.ink_colour(_crop(a, b))
    assert ic.delta_e < 0.5 and ic.holds(2.0)
    assert rr._hex(ic.ink_a) == rr._hex(ic.ink_b) == "#1f2937"


def test_a_a_new_ink_colour_is_measured_at_the_core_not_at_the_edge():
    """#1f2937 → #4d5666 is ΔE00 15, and #4d5666 is also what 20 % paper over
    #1f2937 looks like — the colour v1's tolerance let through. The core
    tells them apart."""
    a = _stroke(_blank(), 20, 10)
    b = _stroke(_blank(), 20, 10, ink=(77, 86, 102))
    ic = rr.ink_colour(_crop(a, b))
    assert ic.delta_e > 10 and not ic.holds(2.0)
    assert "#1f2937 → #4d5666" in ic.text()


def test_a_ink_is_read_channel_by_channel_as_lcd_antialiasing_draws_it():
    """No pixel of the stroke is fully covered in all three channels at once;
    each channel is, somewhere. The ink is still #1f2937."""
    a = _blank()
    b = _blank()
    for img, order in ((a, (0, 1, 2)), (b, (2, 0, 1))):
        for i, ch in enumerate(order):
            img[10:22, 20 + i] = PAPER
            img[10:22, 20 + i, ch] = INK[ch]
            img[10:22, 20 + i, (ch + 1) % 3] = 150
    ink_a = rr.ink_of(a.astype(np.float32), np.array(PAPER, np.float32),
                      np.ones(a.shape[:2], bool))
    assert rr._hex(ink_a) == "#1f2937"
    assert rr.ink_colour(_crop(a, b)).delta_e < 0.5


# --------------------------------------------------------------------------- #
#  (b) the shape agrees to one pixel both ways
# --------------------------------------------------------------------------- #
def test_b_one_pixel_over_is_within_the_tolerance():
    a = _stroke(_blank(), 20, 10)
    b = _stroke(_blank(), 21, 10)
    sh = rr.shape_within(_crop(a, b))
    assert sh.holds() and sh.outside == 0 and sh.ink_a > 0


def test_b_two_pixels_over_is_not():
    a = _stroke(_blank(), 20, 10)
    b = _stroke(_blank(), 22, 10)
    sh = rr.shape_within(_crop(a, b))
    assert not sh.holds() and sh.outside_a > 0 and sh.outside_b > 0


def test_b_ink_that_appeared_has_no_counterpart():
    """An underline: new ink, nothing of it within a pixel of the old ink."""
    a = _stroke(_blank(), 20, 10)
    b = a.copy()
    b[25, 10:50] = INK
    sh = rr.shape_within(_crop(a, b))
    assert sh.outside_a == 0 and sh.outside_b > 0 and not sh.holds()


def test_b_the_tolerance_is_one_pixel_and_says_so():
    a = _stroke(_blank(), 20, 10)
    b = _stroke(_blank(), 22, 10)
    c = _crop(a, b)
    assert not rr.shape_within(c).holds()
    assert rr.shape_within(c, tol_px=2).holds()        # what (b) does NOT allow
    assert rr.SHAPE_TOLERANCE_PX == 1
    assert "within 1 px" in rr.shape_within(c).text()


def _glyphs(img, xs, y=10):
    for x in xs:
        _stroke(img, x, y, w=2, h=12)
    return img


def test_b_each_glyph_may_drift_on_its_own_up_to_the_limit():
    """Three glyphs; the renderer moved the second by 2 px and the third by 3.
    With the pixel of tolerance on top, they need a drift of 1 and 2."""
    a = _glyphs(_blank(40, 80), (10, 25, 40))
    b = _glyphs(_blank(40, 80), (10, 27, 43))
    c = _crop(a, b)
    for limit, holds in ((0, False), (1, False), (2, True), (8, True)):
        g = rr.glyph_drift(c, limit)
        assert g.need_px == 2 and g.holds() is holds, limit
    assert rr.glyph_drift(c, 1).unfit == 2          # the third, in A and in B
    assert rr.glyph_drift(c, 2).outside == 0


def test_b_without_drift_it_is_the_shape_of_step_2():
    a = _stroke(_blank(), 20, 10)
    for x, holds in ((21, True), (22, False)):
        c = _crop(a, _stroke(_blank(), x, 10))
        assert rr.glyph_drift(c, 0).holds() is holds is rr.shape_within(c).holds()


def test_b_ink_that_fits_at_no_drift_says_so():
    a = _stroke(_blank(), 20, 10)
    b = a.copy()
    b[25, 10:50] = INK                               # an underline
    g = rr.glyph_drift(_crop(a, b), 8)
    assert g.need_px is None and not g.holds()
    assert "one fits at no shift up to 8 px" in g.text()


# --------------------------------------------------------------------------- #
#  (c) the background did not change
# --------------------------------------------------------------------------- #
def test_c_a_redrawn_stroke_leaves_the_paper_alone():
    a = _stroke(_blank(), 20, 10)
    b = _stroke(_blank(), 21, 10, edge=0.3)
    bg = rr.background_unchanged(_crop(a, b))
    assert bg.holds(1.0) and bg.changed_away == 0 and bg.delta_e == 0


def test_c_another_paper_is_a_change():
    a = _stroke(_blank(), 20, 10)
    b = _stroke(_blank(paper=(240, 244, 250)), 20, 10, paper=(240, 244, 250))
    bg = rr.background_unchanged(_crop(a, b))
    assert bg.delta_e > 1 and bg.changed_away > 0 and not bg.holds(1.0)


def test_c_a_change_away_from_the_ink_is_a_change():
    a = _stroke(_blank(), 20, 10)
    b = a.copy()
    b[12:15, 40:43] = (200, 200, 200)          # 17 px right of the stroke
    b = _stroke(b, 21, 10)
    bg = rr.background_unchanged(_crop(a, b))
    assert bg.delta_e == 0 and bg.changed_away == 9 and not bg.holds(1.0)


# --------------------------------------------------------------------------- #
#  (d) the change is not local
# --------------------------------------------------------------------------- #
def _page_of_words(n=10):
    img = _blank(200, 400)
    words = []
    for i in range(n):
        x, y = 20 + (i % 5) * 70, 30 + (i // 5) * 80
        for k in range(4):
            _stroke(img, x + 6 * k, y, w=2, h=12)
        words.append((x, y))
    return img, words


def _redraw(img, words, which):
    out = img.copy()
    for i in which:
        x, y = words[i]
        out[y - 2:y + 16, x - 3:x + 30] = PAPER
        for k in range(4):
            _stroke(out, x + 6 * k + 1, y, w=2, h=12, edge=0.6)
    return out


def _cand(a, b):
    la, lb = v2base._color.srgb_to_lab(a), v2base._color.srgb_to_lab(b)
    return v2base.candidates(la, lb, v2base.differs(a, b), 1.0)[1]


def test_d_every_word_of_the_page_changed():
    a, words = _page_of_words()
    b = _redraw(a, words, range(10))
    tc = rr.page_text_change(a, b, _cand(a, b))
    assert (tc.changed, tc.total) == (10, 10) and tc.holds(TEXT_SHARE.value)
    assert "100% of the page's text changed (10 of 10 ink clusters)" in tc.text()


def test_d_one_word_changed_and_the_rest_is_the_same_pixel_for_pixel():
    a, words = _page_of_words()
    b = _redraw(a, words, [3])
    tc = rr.page_text_change(a, b, _cand(a, b))
    assert (tc.changed, tc.total) == (1, 10)
    assert not tc.holds(TEXT_SHARE.value)


def test_d_a_page_without_ink_has_no_share():
    a = _blank(100, 100)
    tc = rr.page_text_change(a, a.copy(), np.zeros(a.shape[:2], bool))
    assert (tc.changed, tc.total, tc.share) == (0, 0, 0.0)
    assert not tc.holds(0.01)


# --------------------------------------------------------------------------- #
#  (e) not a pure shift
# --------------------------------------------------------------------------- #
def _block(a, b):
    la, lb = v2base._color.srgb_to_lab(a), v2base._color.srgb_to_lab(b)
    cand = _cand(a, b)
    return rr.block_shift(a, b, la, lb, cand, jnd=1.0, limit=SHIFT_RESIDUAL.value)


def test_e_a_block_moved_by_a_pixel_is_a_move_and_says_where():
    a = _glyphs(_blank(40, 80), (10, 25, 40))
    b = np.full_like(a, 255)
    b[1:] = a[:-1]                                  # the block, 1 px down
    bs = _block(a, b)
    assert (bs.dx, bs.dy, bs.left) == (0, 1, 0) and not bs.holds()
    assert bs.moved() == "the block moved by +1 px along y"
    assert bs.text().startswith("(e) the block moved by +1 px along y: B is A moved")


def test_e_both_axes_are_named():
    assert rr._moved(-2, 1) == "the block moved by -2 px along x and +1 px along y"


def test_e_a_redrawn_stroke_is_not_a_move():
    a = _glyphs(_blank(40, 80), (10, 25, 40))
    b = _blank(40, 80)
    for x in (10, 25, 40):
        _stroke(b, x, 10, w=2, h=12, edge=0.8)       # the edges drawn otherwise
    bs = _block(a, b)
    assert bs.residual > SHIFT_RESIDUAL.value and bs.holds()
    assert bs.text().startswith("(e) not a pure shift")


# --------------------------------------------------------------------------- #
#  The rule, end to end
# --------------------------------------------------------------------------- #
def test_every_word_redrawn_within_a_pixel_is_explained_when_the_renderer_changed():
    a, words = _page_of_words()
    b = _redraw(a, words, range(10))
    r = compare(a, b, engine="v2", renderer=CHANGED)
    assert r.verdict is Verdict.PASS and not r.regions
    assert r.suppressed and all(s.suppressed_by.startswith("rerender-text: (a) ink ")
                                for s in r.suppressed)
    s = r.suppressed[0].suppressed_by
    for part in ("(a) ink #1f2937 → #1f2937, ΔE00 0.0",
                 "(b) each glyph within 1 px after a shift along the line of up to 0 px",
                 "(c) paper ΔE00 0.0, 0 changed px", "(e) not a pure shift",
                 "(d, not deciding) 100% of the page's text changed", "(was "):
        assert part in s, part
    assert all(x.kind is ChangeKind.NOISE and x.severity == 0 for x in r.suppressed)
    assert r.unassigned_pixels == 0
    assert ("renderer differs from the baseline's (canary: 4 px): typography changes "
            "within a pixel cannot be verified here — make baselines on this "
            "renderer to check them") in r.notes


@pytest.mark.parametrize("renderer, note", [
    (None, "renderer: unknown — text re-rasterisation is not explained"),
    (SAME, "renderer: same as the baseline's"),
    ((CHANGED[0], None), "renderer: unknown — text re-rasterisation is not explained"),
])
def test_without_a_proven_renderer_change_the_rule_is_off(renderer, note):
    a, words = _page_of_words()
    b = _redraw(a, words, range(10))
    r = compare(a, b, engine="v2", renderer=renderer)
    assert r.verdict is Verdict.FAIL and len(r.regions) == 10
    assert note in r.notes
    assert r.maps["v2_rerender"] == []
    assert not any(n["kind"] == "rerender-text" for x in r.regions for n in x.annotations)


def test_one_word_redrawn_within_a_pixel_cannot_be_told_from_the_renderer():
    """Letter-spacing +0.2 px looks like this: within a pixel, same ink, same
    paper, one word. With the renderer changed it is taken out — (d) says
    10 % and decides nothing — and the note says what cannot be verified.
    With the same renderer it stays red."""
    a, words = _page_of_words()
    b = _redraw(a, words, [3])
    changed = compare(a, b, engine="v2", renderer=CHANGED)
    assert changed.verdict is Verdict.PASS
    assert "(d, not deciding) 10% of the page's text changed (1 of 10" in \
        changed.suppressed[0].suppressed_by
    assert any("cannot be verified here" in n for n in changed.notes)
    assert compare(a, b, engine="v2", renderer=SAME).verdict is Verdict.FAIL


def test_every_word_in_a_new_ink_is_not_explained():
    """A global colour change under another renderer: (a) does not hold."""
    a, words = _page_of_words()
    b = a.copy()
    b[np.all(a == INK, axis=2)] = (77, 86, 102)
    r = compare(a, b, engine="v2", renderer=CHANGED)
    assert r.verdict is Verdict.FAIL
    notes = [n for x in r.regions for n in x.annotations if n["kind"] == "rerender-text"]
    assert notes and all(n["value"] == "a" for n in notes)


def test_a_block_that_moved_is_not_explained_and_says_how_it_moved():
    a, words = _page_of_words()
    b = np.full_like(a, 255)
    b[1:] = a[:-1]
    r = compare(a, b, engine="v2", renderer=CHANGED)
    assert r.verdict is Verdict.FAIL
    notes = [n for x in r.regions for n in x.annotations if n["kind"] == "rerender-text"]
    assert notes and all(n["value"] == "e" for n in notes)
    assert notes[0]["text"] == ("not re-rasterised text — fails e: the block moved "
                                "by +1 px along y")


def test_the_measurements_are_kept_for_every_region_looked_at():
    a, words = _page_of_words()
    b = _redraw(a, words, [3])
    r = compare(a, b, engine="v2", renderer=CHANGED)
    recs = r.maps["v2_rerender"]
    assert len(recs) == len(r.regions) + len(r.suppressed)
    rec = recs[0]
    assert set(rec["holds"]) == {"a", "b", "c", "d", "e"}
    assert rec["failed"] == "" and rec["holds"]["d"] is False
    assert rec["glyph_need_px"] == 0 and rec["shift_residual"] > SHIFT_RESIDUAL.value
    assert rec["mass_a"] > 0 and rec["mass_b"] > 0


def test_ink_mass_is_measured_not_used():
    """A heavier stroke carries more ink; the ratio is reported and decides nothing."""
    a = _stroke(_blank(), 20, 10, w=2)
    b = _stroke(_blank(), 20, 10, w=3)
    c = _crop(a, b)
    ma, mb = rr.ink_mass(c)
    assert mb / ma > 1.2
    t = rr.TextChange(10, 10)
    block = rr.BlockShift(dx=1, dy=0, left=10, changed=10, limit=0.25)
    heavy = rr.assess(c, t, block, ink_limit=2.0, share_limit=0.4, jnd=1.0, drift_px=0)
    assert heavy.mass_ratio > 1.2 and set(heavy.holds) == {"a", "b", "c", "d", "e"}


# --------------------------------------------------------------------------- #
#  The numbers chosen on the calibration half, replayed where they were measured
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def corpus():
    sys.path.insert(0, str(ROOT / "scripts"))
    import browser_corpus as bc

    from vistest.core import pngio

    m = bc.load_manifest()
    cases = {c["name"]: c for c in m["cases"]}

    def pair(name):
        c = cases[name]
        assert c["split"] == "calibration", "numbers are measured on the calibration half"
        return (pngio.read(bc.CORPUS_DIR / m["templates"][c["template"]]["base"]),
                pngio.read(bc.CORPUS_DIR / c["actual"]))
    return pair


def _share(pair):
    a, b = pair
    return rr.page_text_change(a, b, _cand(a, b))


@pytest.mark.parametrize("name", ["form/render/hinting_none", "form/render/no_lcd_no_subpixel",
                                  "form/render/geometric_precision",
                                  "form/render/full_chromium", "form/os/windows"])
def test_text_share_noise_side_is_where_it_was_measured(corpus, name):
    tc = _share(corpus(name))
    assert round(tc.share, 3) == TEXT_SHARE.noise
    assert tc.share >= TEXT_SHARE.value


def test_text_share_signal_side_is_where_it_was_measured(corpus):
    tc = _share(corpus("article/offset/plus1px"))
    assert (tc.changed, tc.total) == (48, 165)
    assert round(tc.share, 3) == TEXT_SHARE.signal < TEXT_SHARE.value


def _region_at(pair, box):
    a, b = pair
    r = compare(a, b, engine="v2", renderer=CHANGED,
                v2=V2Config(glyph_drift_px=8))
    return next(x for x in r.maps["v2_rerender"] if list(x["box"]) == box)


def test_glyph_drift_signal_side_is_where_it_was_measured(corpus):
    """A «9» that became an «8»: one pixel of drift and it is gone."""
    for name, box in (("article/one_char@hinting_none/one", [194, 141, 9, 11]),
                      ("article/one_char@full_chromium/one", [194, 142, 9, 9])):
        rec = _region_at(corpus(name), box)
        assert rec["glyph_need_px"] == GLYPH_DRIFT_PX.signal > GLYPH_DRIFT_PX.value
        assert rec["holds"]["a"] and rec["holds"]["c"] and rec["holds"]["e"]


def test_shift_residual_is_where_it_was_measured(corpus):
    moved = _region_at(corpus("table/render/hinting_none"), [505, 77, 7, 30])
    assert round(moved["shift_residual"], 3) == SHIFT_RESIDUAL.signal
    assert tuple(moved["shift"]) == (-1, 0) and not moved["holds"]["e"]
    text = _region_at(corpus("article/render/full_chromium"), [547, 225, 46, 13])
    assert round(text["shift_residual"], 3) == SHIFT_RESIDUAL.noise
    assert text["holds"]["e"]
    #  What step 2 took out: exact moves.
    for name, box, shift in (("table/element_removed/gone", [202, 205, 139, 12], (0, -1)),
                             ("article/line_height/plus1px", [146, 315, 43, 14], (0, 1))):
        rec = _region_at(corpus(name), box)
        assert rec["shift_residual"] == 0.0 and tuple(rec["shift"]) == shift
        assert not rec["holds"]["e"]


def test_min_region_px_is_where_it_was_measured(corpus):
    def largest(name):
        a, b = corpus(name)
        _, groups = v2base.group(_cand(a, b), V2Config().group_px)
        return max((g.pixels for g in groups), default=0)
    assert largest("table/opacity/0.98") == MIN_REGION_PX.noise < MIN_REGION_PX.value
    assert largest("article/border_radius/plus2px") == MIN_REGION_PX.signal
