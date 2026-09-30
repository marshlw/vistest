# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""v2, the explanation «the page moved by a fraction of a pixel».

Pages built by hand, where the answer is known by construction: cards with
borders (box edges, long and straight) and words of short strokes inside
them. Moved by a quarter pixel the way Chromium moves a page — boxes
anti-aliased at the fraction, text snapped to the grid in the direction of
the move — the page is proven to have moved and every region is explained.
Then what the move must not explain: a card moved by a whole pixel (the
proof fails on the edges), words redrawn with nothing else moved (no edge
moved at all), a recoloured card on a moved page, words moved against the
page's direction. Then an icon the rasteriser draws again at the fraction,
by coverage: the move misses most of it, its coverage moments keep it (f5);
and what they must not keep: the same icon in another ink, or at another
fraction. Then the numbers chosen on the calibration half, replayed on the
pairs they were measured on.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

from vistest.core import color, compare
from vistest.core import warp as _warp
from vistest.core.v2 import V2Config
from vistest.core.v2 import base as v2base
from vistest.core.v2 import pageshift as ps
from vistest.core.v2.settings import (
    MOVE_TOLERANCE,
    PAGE_SHIFT_COVER,
    PAGE_SHIFT_MOVED,
    SHIFT_CENTROID_PX,
    SHIFT_MASS_CHANGE,
    SHIFT_REGION_MISS,
)
from vistest.models import ChangeKind, Verdict

ROOT = Path(__file__).resolve().parents[1]
PAGE = (250, 250, 250)
CARD = (255, 255, 255)
BORDER = (60, 70, 90)
INK = (31, 41, 55)
CARDS = ((10, 10, 100, 60), (130, 10, 100, 60), (10, 90, 220, 60))
#: Two canaries: the same pixels (the renderer is the same), and two that
#: differ by a pixel (it changed).
SAME = (np.zeros((2, 2, 3), np.uint8), np.zeros((2, 2, 3), np.uint8))
CHANGED = (np.zeros((2, 2, 3), np.uint8), np.full((2, 2, 3), 255, np.uint8))


def _cards(h=160, w=240, border=BORDER) -> np.ndarray:
    """Cards with a 2 px border on a grey page: box edges 60 px and longer."""
    img = np.empty((h, w, 3), np.uint8)
    img[:] = PAGE
    for x, y, bw, bh in CARDS:
        img[y:y + bh, x:x + bw] = border
        img[y + 2:y + bh - 2, x + 2:x + bw - 2] = CARD
    return img


def _words(img, dx=0, dy=0) -> np.ndarray:
    """A line of «o» in every card: 6×10 px rings, shorter than a structure edge."""
    for x0, y0, bw, _ in CARDS:
        for x in range(x0 + 10, x0 + bw - 14, 9):
            y = y0 + 20 + dy
            img[y:y + 10, x + dx:x + dx + 6] = INK
            img[y + 2:y + 8, x + dx + 2:x + dx + 4] = CARD
    return img


def _moved(img, dx, dy) -> np.ndarray:
    return _warp.to_uint8(_warp.shift(img, dx, dy))


def _page_and_moved(text_dx=0, text_dy=1, dx=0.25, dy=0.25):
    """The baseline, and the page drawn (dx, dy) over: boxes at the fraction,
    words snapped by (text_dx, text_dy) whole pixels."""
    base = _words(_cards())
    moved = _words(_moved(_cards(), dx, dy), text_dx, text_dy)
    return base, moved


def _fit(a, b):
    la, lb = color.srgb_to_lab(a), color.srgb_to_lab(b)
    _, cand = v2base.candidates(la, lb, v2base.differs(a, b), 1.0)
    return ps.fit(a, la, lb, cand, 1.0, MOVE_TOLERANCE.value)


# --------------------------------------------------------------------------- #
#  The pieces
# --------------------------------------------------------------------------- #
def test_structure_edges_are_the_boxes_not_the_words():
    img = _words(_cards())
    edges = ps.structure_edges(color.srgb_to_lab(img))
    assert edges[10:70, 10].all() and edges[10, 20:100].all()     # a card's border
    assert not edges[30:40, 20:26].any()                           # an «o»


@pytest.mark.parametrize("d, whole, every", [
    ((0.25, 0.25), [(0.0, 1.0), (1.0, 0.0), (1.0, 1.0)],
     [(0.0, 0.25), (0.0, 1.0), (0.25, 0.0), (0.25, 0.25), (0.25, 1.0), (1.0, 0.0),
      (1.0, 0.25), (1.0, 1.0)]),
    ((0.5, 0.0), [(1.0, 0.0)], [(0.5, 0.0), (1.0, 0.0)]),
    ((-0.25, 0.5), [(-1.0, 0.0), (-1.0, 1.0), (0.0, 1.0)],
     [(-1.0, 0.0), (-1.0, 0.5), (-1.0, 1.0), (-0.25, 0.0), (-0.25, 0.5), (-0.25, 1.0),
      (0.0, 0.5), (0.0, 1.0)]),
])
def test_the_moves_a_shift_makes_are_the_fraction_and_the_whole_pixels_around_it(
        d, whole, every):
    assert ps.displacements(*d) == (whole, every)


def test_pixels_a_move_brings_in_from_outside_are_neither_reproduced_nor_refuted():
    ys, xs = np.array([0, 0, 5, 9]), np.array([0, 5, 0, 9])
    assert ps._entering((10, 10), (0.25, 0.0), ys, xs).tolist() == [True, False, True, False]
    assert ps._entering((10, 10), (0.0, -1.0), ys, xs).tolist() == [False, False, False, True]


# --------------------------------------------------------------------------- #
#  1. The page moved: fitted and proven on the box edges
# --------------------------------------------------------------------------- #
def test_a_page_moved_by_a_quarter_pixel_is_found_and_proven():
    fit = _fit(*_page_and_moved())
    assert (fit.dx, fit.dy) == (0.25, 0.25)
    assert fit.moved_share >= PAGE_SHIFT_MOVED.value
    assert fit.cover == 1.0
    assert fit.holds(PAGE_SHIFT_MOVED.value, PAGE_SHIFT_COVER.value)
    assert fit.text().startswith("the page moved by (+0.25, +0.25) px: ")
    assert "moved by the fraction and by no whole pixel; the move reproduces 100% " \
           "of the changed pixels" in fit.text()


def test_a_card_moved_by_a_whole_pixel_moves_no_edge_by_a_fraction():
    """offset, padding: a block that moved by whole pixels."""
    base = _words(_cards())
    moved = base.copy()
    x, y, bw, bh = CARDS[2]
    moved[y:y + bh + 1, x:x + bw] = PAGE
    moved[y + 1:y + bh + 1, x:x + bw] = base[y:y + bh, x:x + bw]
    fit = _fit(base, moved)
    assert fit.moved == 0 and fit.moved_share == 0.0
    assert not fit.holds(PAGE_SHIFT_MOVED.value, PAGE_SHIFT_COVER.value)


def test_words_redrawn_with_no_box_moved_move_no_edge():
    """A renderer that draws text otherwise: no box edge moves by a fraction
    (a line of tight letters can make a long edge of its own; it is redrawn,
    not moved)."""
    base = _words(_cards())
    redrawn = _words(_cards())
    ring = np.all(redrawn == INK, axis=2)
    redrawn[ring] = (40, 52, 66)                                   # another ink …
    redrawn[np.roll(ring, 1, axis=1) & ~ring] = (150, 155, 162)    # … another edge
    fit = _fit(base, redrawn)
    assert fit.moved == 0 and fit.covered == 0
    assert not fit.holds(PAGE_SHIFT_MOVED.value, PAGE_SHIFT_COVER.value)


# --------------------------------------------------------------------------- #
#  2. Region by region, once the page is proven
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text_dx, text_dy", [(0, 0), (0, 1), (1, 1)])
def test_a_page_moved_by_a_fraction_passes_whatever_pixel_its_text_snapped_to(
        text_dx, text_dy):
    base, moved = _page_and_moved(text_dx, text_dy)
    r = compare(base, moved, engine="v2")
    assert r.verdict is Verdict.PASS and not r.regions and r.suppressed
    assert all(s.suppressed_by.startswith("page-shift: the page moved by (+0.25, +0.25) px")
               and s.kind is ChangeKind.NOISE and s.severity == 0 for s in r.suppressed)
    rec = r.maps["v2_page_shift"]
    assert rec["holds"] and (rec["dx"], rec["dy"]) == (0.25, 0.25)
    assert all(x["explained"] for x in rec["regions"])
    assert any(n.startswith("Noise explained and suppressed: ") and "by the page's move" in n
               for n in r.notes)


def test_the_page_move_does_not_depend_on_the_renderer():
    base, moved = _page_and_moved()
    same = (np.zeros((2, 2, 3), np.uint8),) * 2
    for renderer in (None, same):
        assert compare(base, moved, engine="v2", renderer=renderer).verdict is Verdict.PASS


def test_a_recoloured_card_on_a_moved_page_is_more_than_the_move():
    """A change bigger than the move: the move no longer reproduces most of the
    page's changed pixels, and the page is not proven to have moved."""
    base, moved = _page_and_moved()
    x, y, bw, bh = CARDS[1]
    inner = moved[y + 2:y + bh - 2, x + 2:x + bw - 2]
    inner[np.all(inner == CARD, axis=2)] = (236, 244, 255)      # a pale blue fill
    r = compare(base, moved, engine="v2")
    assert r.verdict is Verdict.FAIL
    rec = r.maps["v2_page_shift"]
    assert rec["moved"] >= PAGE_SHIFT_MOVED.value and rec["cover"] < PAGE_SHIFT_COVER.value
    assert rec["holds"] is False and rec["regions"] == []


def test_recoloured_words_on_a_moved_page_are_not_the_move():
    base, moved = _page_and_moved()
    x, y, bw, bh = CARDS[1]
    card = moved[y:y + bh, x:x + bw]
    card[np.all(card == INK, axis=2)] = (122, 31, 31)             # #7a1f1f
    r = compare(base, moved, engine="v2", renderer=SAME)
    assert r.verdict is Verdict.FAIL and r.maps["v2_page_shift"]["holds"]
    notes = [n for g in r.regions for n in g.annotations if n["kind"] == ps.RULE]
    assert len(notes) == len(r.regions) == 1
    assert notes[0]["text"].startswith("not the page's move (+0.25, +0.25) px — the move "
                                       "misses ")
    assert "; not the drawing moved (M0 B/A " in notes[0]["text"]
    assert ", and not text redrawn: text redrawn at the new position: (a) ink " \
           in notes[0]["text"]


def test_words_moved_against_the_page_are_not_the_move():
    """Text snaps in the direction of the move; a word a pixel the other way is
    a change the move does not make."""
    base, moved = _page_and_moved(text_dx=-1, text_dy=0)
    r = compare(base, moved, engine="v2")
    assert r.verdict is Verdict.FAIL
    notes = [n["text"] for g in r.regions for n in g.annotations if n["kind"] == ps.RULE]
    assert notes and any("against the page's shift" in t for t in notes)


def test_without_the_proof_no_region_is_looked_at():
    base = _words(_cards())
    moved = base.copy()
    x, y, bw, bh = CARDS[2]
    moved[y:y + bh + 1, x:x + bw] = PAGE
    moved[y + 1:y + bh + 1, x:x + bw] = base[y:y + bh, x:x + bw]
    r = compare(base, moved, engine="v2")
    assert r.verdict is Verdict.FAIL
    rec = r.maps["v2_page_shift"]
    assert rec["holds"] is False and rec["regions"] == []
    assert not any(n["kind"] == ps.RULE for g in r.regions for n in g.annotations)
    assert not any("by the page's move" in n for n in r.notes)


# --------------------------------------------------------------------------- #
#  Coverage moments: a drawing the rasteriser drew again at the fraction
# --------------------------------------------------------------------------- #
ICON_INK = (55, 65, 81)


def _ring(img, x, y, dx=0.0, dy=0.0, ink=ICON_INK, r=5.0, k=8, size=16):
    """A 1 px ring drawn the way a rasteriser draws a path: the coverage of
    each pixel (k×k samples), blended with the paper in 8-bit sRGB."""
    yy, xx = np.mgrid[0:size * k, 0:size * k]
    cx, cy = (size / 2 + dx) * k, (size / 2 + dy) * k
    dist = np.hypot(xx + 0.5 - cx, yy + 0.5 - cy) / k
    cover = ((dist >= r - 0.5) & (dist <= r + 0.5)).reshape(size, k, size, k).mean((1, 3))
    paper = img[y:y + size, x:x + size].astype(float)
    img[y:y + size, x:x + size] = np.rint(
        paper + cover[..., None] * (np.array(ink, float) - paper)).astype(np.uint8)
    return img


def _page_with_icon(**icon):
    """The moved page of `_page_and_moved`, with an icon in the third card that
    the rasteriser drew again at (dx, dy) — by default at the page's move."""
    base, moved = _page_and_moved(0, 1)
    base = _ring(base, 100, 126)
    moved[126:142, 100:116] = CARD
    return base, _ring(moved, 100, 126, **{"dx": 0.25, "dy": 0.25, **icon})


def test_coverage_keeps_its_mass_and_its_centre_moves_by_the_move():
    a = _ring(np.full((16, 16, 3), 255, np.uint8), 0, 0)
    b = _ring(np.full((16, 16, 3), 255, np.uint8), 0, 0, dx=0.25, dy=0.25)
    mo = ps.coverage_moments(a, b, (0, 0, 16, 16), (0.25, 0.25), pad=0)
    assert mo.mass_change < 0.005
    assert mo.centroid_miss < 0.01
    assert mo.text().startswith("M0 B/A ")


def test_an_icon_drawn_again_at_the_fraction_is_the_move():
    base, moved = _page_with_icon()
    r = compare(base, moved, engine="v2", renderer=SAME)
    assert r.verdict is Verdict.PASS
    rec = next(x for x in r.maps["v2_page_shift"]["regions"] if x["box"][1] > 120)
    assert rec["missed"] > SHIFT_REGION_MISS.value and rec["how"] == ps.MOMENTS
    assert rec["mass_change"] <= SHIFT_MASS_CHANGE.value
    assert rec["centroid_miss"] <= SHIFT_CENTROID_PX.value
    said = next(s.suppressed_by for s in r.suppressed if s.y > 120)
    assert "this region: the drawing moved with the page: (b) shape within 1 px" in said
    assert "; M0 B/A " in said and "px from the page's (+0.25, +0.25)" in said


def test_the_icon_in_another_ink_is_not_the_move():
    base, moved = _page_with_icon(ink=(70, 80, 100))
    r = compare(base, moved, engine="v2", renderer=SAME)
    assert r.verdict is Verdict.FAIL
    rec = next(x for x in r.maps["v2_page_shift"]["regions"] if x["box"][1] > 120)
    assert rec["how"] == "" and rec["mass_change"] > SHIFT_MASS_CHANGE.value
    assert rec["centroid_miss"] <= SHIFT_CENTROID_PX.value


def test_the_icon_at_another_fraction_is_not_the_drawing_moved():
    """Its centre lands half a pixel from the page's move. (The older test for
    text redrawn at the new position still takes it — see f5's report.)"""
    base, moved = _page_with_icon(dx=0.75)
    r = compare(base, moved, engine="v2", renderer=SAME)
    rec = next(x for x in r.maps["v2_page_shift"]["regions"] if x["box"][1] > 120)
    assert rec["how"] != ps.MOMENTS
    assert rec["mass_change"] <= SHIFT_MASS_CHANGE.value
    assert rec["centroid_miss"] > SHIFT_CENTROID_PX.value


@pytest.mark.parametrize("renderer", [None, CHANGED, (SAME[0], None)])
def test_the_moments_are_asked_only_when_the_renderer_is_the_same(renderer):
    """The premise is the same rasteriser drawing the same path at the
    fraction: with another renderer, or one nobody measured, the region goes
    the way it went before f5 (here, as text redrawn at the new position)."""
    base, moved = _page_with_icon()
    r = compare(base, moved, engine="v2", renderer=renderer)
    rec = next(x for x in r.maps["v2_page_shift"]["regions"] if x["box"][1] > 120)
    assert rec["how"] != ps.MOMENTS and rec["mass_change"] is None


def test_v1_does_not_have_the_rule():
    base, moved = _page_and_moved()
    assert "v2_page_shift" not in compare(base, moved, engine="v1").maps


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
    fitted: dict = {}

    def pair(name):
        if name not in fitted:
            c = cases[name]
            assert c["split"] == "calibration", "numbers are measured on the calibration half"
            a = pngio.read(bc.CORPUS_DIR / m["templates"][c["template"]]["base"])
            b = pngio.read(bc.CORPUS_DIR / c["actual"])
            fitted[name] = (a, b, _fit(a, b))
        return fitted[name]
    return pair


def test_page_shift_moved_is_where_it_was_measured(corpus):
    *_, noise = corpus("article/render/shift_0.5px")
    assert round(noise.moved_share, 3) == PAGE_SHIFT_MOVED.noise >= PAGE_SHIFT_MOVED.value
    *_, signal = corpus("table/render/no_lcd_no_subpixel")
    assert round(signal.moved_share, 3) == PAGE_SHIFT_MOVED.signal < PAGE_SHIFT_MOVED.value


def test_page_shift_cover_is_where_it_was_measured(corpus):
    *_, noise = corpus("cards/render/shift_0.25px")
    assert round(noise.cover, 3) == PAGE_SHIFT_COVER.noise >= PAGE_SHIFT_COVER.value
    assert noise.holds(PAGE_SHIFT_MOVED.value, PAGE_SHIFT_COVER.value)
    #  Why the edges are asked first: a block moved by a whole pixel is
    #  reproduced entirely by a move, and moves no box edge by a fraction.
    *_, offset = corpus("table/offset/plus1px")
    assert offset.cover == 1.0 and offset.moved_share == 0.0
    assert not offset.holds(PAGE_SHIFT_MOVED.value, PAGE_SHIFT_COVER.value)


def test_shift_region_miss_is_where_it_was_measured(corpus):
    a, b, fit = corpus("form/render/shift_0.25px")
    r = compare(a, b, engine="v2")
    rec = next(x for x in r.maps["v2_page_shift"]["regions"]
               if list(x["box"]) == [288, 498, 16, 16])
    assert round(rec["missed"], 3) == SHIFT_REGION_MISS.noise <= SHIFT_REGION_MISS.value
    assert rec["explained"]


def test_move_tolerance_noise_side_is_where_it_was_measured(corpus):
    """The 95th percentile, over every changed pixel of the eight calibration
    page-shift pairs, of how far the best displacement misses."""
    misses = []
    for page in ("table", "form", "cards", "article"):
        for by in ("0.25px", "0.5px"):
            a, b, fit = corpus(f"{page}/render/shift_{by}")
            lb = color.srgb_to_lab(b)
            _, cand = v2base.candidates(color.srgb_to_lab(a), lb, v2base.differs(a, b), 1.0)
            ys, xs = np.nonzero(cand)
            best = np.full(len(ys), np.inf)
            for d in fit.every:
                moved = color.srgb_to_lab(_warp.to_uint8(_warp.sample(a, *d, ys, xs))[None])
                de = color.delta_e_ciede2000(moved, lb[ys, xs][None])[0]
                de[ps._entering(a.shape[:2], d, ys, xs)] = 0
                best = np.minimum(best, de)
            misses.append(best)
    p95 = float(np.percentile(np.concatenate(misses), 95))
    assert round(p95, 2) == MOVE_TOLERANCE.noise < MOVE_TOLERANCE.value < MOVE_TOLERANCE.signal


def _region_record(corpus, name, box):
    """A region of a calibration pair as the benchmark sees it: the shift
    pairs name the baseline's canary, so the renderer is the same."""
    a, b, _ = corpus(name)
    r = compare(a, b, engine="v2", renderer=SAME)
    return r, next(x for x in r.maps["v2_page_shift"]["regions"] if list(x["box"]) == box)


def _as_if_moved(corpus, name, box, move):
    """A region of a pair measured as if the page had moved by `move`: the
    actual frame moved by it, (b), (c) and the moments against it."""
    from vistest.core.v2 import rerender as rr

    a, b, _ = corpus(name)
    la, lb = color.srgb_to_lab(a), color.srgb_to_lab(b)
    _, cand = v2base.candidates(la, lb, v2base.differs(a, b), 1.0)
    labels, groups = v2base.group(cand, V2Config().group_px)
    g = next(g for g in groups if [g.x, g.y, g.w, g.h] == box)
    moved = _warp.to_uint8(_warp.shift(b, *move))
    crop = rr.crop(a, moved, labels, g.label, (g.x, g.y, g.w, g.h), V2Config().group_px)
    held = rr.shape_within(crop).holds() and rr.background_unchanged(crop).holds(1.0)
    return held, ps.coverage_moments(a, moved, (g.x, g.y, g.w, g.h), move)


def test_shift_mass_change_is_where_it_was_measured(corpus):
    _, noise = _region_record(corpus, "form/render/shift_0.25px", [983, 417, 10, 7])
    assert round(noise["mass_change"], 4) == SHIFT_MASS_CHANGE.noise
    assert noise["how"] == ps.MOMENTS
    held, signal = _as_if_moved(corpus, "table/word_swap/one", [256, 135, 9, 9],
                                (0.25, 0.25))
    assert held and signal.follows(SHIFT_CENTROID_PX.value)
    assert round(signal.mass_change, 4) == SHIFT_MASS_CHANGE.signal
    assert SHIFT_MASS_CHANGE.noise < SHIFT_MASS_CHANGE.value < SHIFT_MASS_CHANGE.signal


def test_shift_centroid_px_is_where_it_was_measured(corpus):
    _, noise = _region_record(corpus, "cards/render/shift_0.25px", [1100, 178, 14, 20])
    assert round(noise["centroid_miss"], 3) == SHIFT_CENTROID_PX.noise
    assert noise["how"] == ps.MOMENTS
    held, signal = _as_if_moved(corpus, "article/border_radius/plus2px", [1234, 33, 6, 6],
                                (0.25, 0.25))
    assert held and signal.conserved(SHIFT_MASS_CHANGE.value)
    assert round(signal.centroid_miss, 3) == SHIFT_CENTROID_PX.signal
    assert SHIFT_CENTROID_PX.noise < SHIFT_CENTROID_PX.value < SHIFT_CENTROID_PX.signal


def test_the_bookmark_is_kept_by_its_moments_and_the_checkbox_is_not(corpus):
    """article/render/shift_0.25px: the bookmark icon the move missed by 39 %
    is the drawing moved. form/render/shift_0.5px: the native checkbox sticks
    to the pixel grid — it loses a fifth of its coverage and stays red."""
    r, bookmark = _region_record(corpus, "article/render/shift_0.25px", [278, 141, 10, 13])
    assert bookmark["how"] == ps.MOMENTS and r.verdict is Verdict.PASS
    r, checkbox = _region_record(corpus, "form/render/shift_0.5px", [288, 498, 17, 17])
    assert not checkbox["explained"] and r.verdict is Verdict.FAIL
    assert round(checkbox["mass_change"], 2) == 0.20


def test_the_config_takes_the_numbers():
    v2 = V2Config()
    assert (v2.page_shift_moved, v2.page_shift_cover, v2.move_tolerance,
            v2.shift_region_miss) == (PAGE_SHIFT_MOVED.value, PAGE_SHIFT_COVER.value,
                                      MOVE_TOLERANCE.value, SHIFT_REGION_MISS.value)
    assert (v2.shift_mass_change, v2.shift_centroid_px) == (SHIFT_MASS_CHANGE.value,
                                                           SHIFT_CENTROID_PX.value)
