# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""The numbers a failure prints have to be numbers a person can reconcile.

Found on a live run against a third-party suite, in one message:

    severity 100.0 (limit 25.0), changed area 1.44% (limit 0.15%)
    reason: a shift in 2 regions, something disappeared in 1 region;
            largest 5x7 at (759, 449)

Three defects, each covered here:

1. The area (13 000 px) could not be reached by adding up the regions
   (~400 px). The area is measured before segmentation, and the opening
   step, which then ran first, erased thin strokes — most of a text change.
   The result now says where every changed pixel went, and the one-line
   reason says it too; the order is now close → open, so text is kept.
2. A 27-pixel speck scored 100. Size is now a factor, not a summand, and
   the scale saturates softly, so small stays small and the top keeps order.
3. Two neighbouring radio buttons "moved" by +48 and -58 at once. A region
   whose content fits several places is no longer given a vector unless the
   rest of the page agrees on one of them.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from tests import synthetic as syn
from vistest.core.classify import classify_region
from vistest.core.comparator import compare
from vistest.core.settings import DiffConfig
from vistest.models import ChangeKind, CompareResult, DiffRegion, Verdict
from vistest.report.library import describe

HD = (720, 1280)


def _c(a, b, **kw):
    return compare(a, b, cfg=DiffConfig(**kw))


def _blank_hd():
    return syn.blank(HD[1], HD[0])


# --------------------------------------------------------------------------- #
#  1. Area and regions add up
# --------------------------------------------------------------------------- #
def _thin_text_page(label: str) -> np.ndarray:
    img = _blank_hd()
    for i in range(12):
        cv2.putText(img, f"{label} row {i}  12:0{i}  value {i * 7}",
                    (60, 80 + i * 26), 0, 0.5, (40, 40, 40), 1, cv2.LINE_AA)
    #  One solid change so that at least one region survives.
    return img


@pytest.mark.parametrize("mutate", [
    syn.regress_button_color, syn.regress_button_removed,
    syn.regress_layout_moved, syn.regress_tiny_icon, syn.regress_taller_page,
])
def test_the_pixel_accounting_always_sums_to_the_changed_pixels(mutate):
    base = syn.page()
    r = _c(base, mutate(base))
    assert r.changed_pixels == (r.region_pixels + r.suppressed_pixels
                                + r.unassigned_pixels), r.summary()
    assert min(r.region_pixels, r.suppressed_pixels, r.unassigned_pixels) >= 0
    assert r.region_area_pct <= r.changed_area_pct + 1e-9


def test_a_thin_text_change_now_lands_inside_a_region():
    """The live case in miniature: most of the change is 1-px strokes.

    This test used to assert the opposite — that most of such a change ends
    up in no region at all — because the opening ran before the closing and
    erased every stroke thinner than five pixels. The order is now close →
    open (see `segment.clean_mask`), so the changed words are regions.
    """
    exp = _thin_text_page("Alpha")
    act = _thin_text_page("Omega")
    r = _c(exp, act)

    assert r.regions, r.summary()
    assert r.region_pixels > 0.9 * r.changed_pixels, r.summary()
    assert r.verdict is Verdict.FAIL, r.summary()


def test_changes_no_region_keeps_are_still_counted_and_named():
    """Scattered specks stay outside every region, and the reason says so."""
    exp = _blank_hd()
    act = exp.copy()
    for i in range(30):
        for j in range(12):
            y, x = 40 + i * 22, 500 + j * 22
            act[y, x] = (20, 20, 20)
    cv2.rectangle(act, (100, 100), (116, 110), (200, 40, 40), -1)
    r = _c(exp, act)

    assert r.regions, r.summary()
    assert r.unassigned_pixels > r.region_pixels, r.summary()
    reason = describe(r)
    assert f"{r.changed_area_pct:.2f}% changed" in reason, reason
    assert "in no region" in reason, reason


def test_the_reason_stays_short_when_the_regions_explain_the_area():
    exp = _blank_hd()
    act = exp.copy()
    cv2.rectangle(act, (100, 100), (220, 160), (200, 40, 40), -1)
    r = _c(exp, act)
    assert r.unassigned_pixels <= 0.1 * r.changed_pixels, r.summary()
    assert "in no region" not in describe(r)


def test_the_accounting_is_in_the_serialised_metrics():
    base = syn.page()
    m = _c(base, syn.regress_button_color(base)).to_dict()["metrics"]
    for key in ("region_pixels", "suppressed_pixels", "unassigned_pixels",
                "region_area_pct"):
        assert key in m


def _result_with(kinds: list[ChangeKind]) -> CompareResult:
    res = CompareResult(name="x", verdict=Verdict.FAIL)
    res.total_pixels = 10_000
    for i, kind in enumerate(kinds):
        res.regions.append(DiffRegion(x=i, y=i, w=10 + i, h=5, kind=kind,
                                      severity=float(i)))
    return res


def test_the_reason_never_drops_kinds_silently():
    kinds = [ChangeKind.TEXT] * 3 + [ChangeKind.COLOR] * 2 + [
        ChangeKind.ADDED, ChangeKind.REMOVED, ChangeKind.RESIZED]
    reason = describe(_result_with(kinds))
    assert reason.startswith("8 regions:"), reason
    assert "other changes in 2" in reason, reason


def test_the_region_named_in_the_reason_is_called_what_it_is():
    """It is picked by severity. Calling it "largest" was checkably false."""
    res = _result_with([ChangeKind.TEXT, ChangeKind.COLOR])
    res.regions[0].w, res.regions[0].h = 400, 300        # large, severity 0
    reason = describe(res)
    assert "most severe 11x5" in reason, reason
    assert "largest" not in reason


# --------------------------------------------------------------------------- #
#  2. Severity is a scale, not a switch
# --------------------------------------------------------------------------- #
def _severity_of(draw) -> float:
    exp = _blank_hd()
    act = exp.copy()
    draw(act)
    return _c(exp, act).max_severity


def test_a_speck_scores_far_below_a_real_element():
    speck = _severity_of(
        lambda im: cv2.rectangle(im, (700, 300), (706, 305), (20, 20, 20), -1))
    button = _severity_of(
        lambda im: cv2.rectangle(im, (100, 100), (360, 152), (40, 90, 220), -1))
    assert speck < 50, speck
    assert button > 80, button
    assert button - speck > 30


def test_nothing_short_of_a_large_change_reaches_the_top():
    """The old score pinned at 100 for anything sharp. The new one does not."""
    s = _severity_of(
        lambda im: cv2.rectangle(im, (100, 100), (160, 130), (40, 90, 220), -1))
    assert s < 100.0


def test_severity_keeps_its_order_at_the_top():
    """Soft saturation: two large changes of different size stay different."""
    a = _severity_of(
        lambda im: cv2.rectangle(im, (100, 100), (300, 160), (40, 90, 220), -1))
    b = _severity_of(
        lambda im: cv2.rectangle(im, (100, 100), (700, 460), (40, 90, 220), -1))
    assert a < b


def test_a_moved_block_and_a_speck_are_told_apart():
    exp, act = _blank_hd(), _blank_hd()
    cv2.rectangle(exp, (60, 80), (420, 200), (52, 120, 246), -1)
    cv2.putText(exp, "PANEL", (110, 155), 0, 1.2, (255, 255, 255), 3, cv2.LINE_AA)
    cv2.rectangle(act, (60, 140), (420, 260), (52, 120, 246), -1)
    cv2.putText(act, "PANEL", (110, 215), 0, 1.2, (255, 255, 255), 3, cv2.LINE_AA)
    moved = _c(exp, act)
    assert any(r.kind is ChangeKind.MOVED for r in moved.regions), moved.summary()

    speck = _severity_of(
        lambda im: cv2.rectangle(im, (700, 300), (706, 305), (20, 20, 20), -1))
    assert moved.max_severity > speck + 15, (moved.max_severity, speck)


# --------------------------------------------------------------------------- #
#  3. No invented vectors
# --------------------------------------------------------------------------- #
def _ring(img, cx, cy):
    cv2.circle(img, (cx, cy), 8, (110, 110, 110), 2)
    cv2.circle(img, (cx, cy), 4, (40, 90, 220), -1)


def _classify(exp, act, box, prefer=None):
    lab = lambda im: cv2.cvtColor(im, cv2.COLOR_RGB2GRAY)  # noqa: E731
    ge, ga = lab(exp), lab(act)
    de = np.abs(ge.astype(np.float32) - ga.astype(np.float32))
    mask = de > 10
    x, y, w, h = box
    return classify_region(box, int(mask[y:y + h, x:x + w].sum()), 0.5,
                           gray_exp=ge, gray_act=ga, de_map=de, mask=mask,
                           total_pixels=ge.size, prefer_shift=prefer)


def _two_copies():
    """The ring sits at y=200 in the baseline and at 152 and 248 in actual."""
    exp, act = syn.blank(400, 400), syn.blank(400, 400)
    _ring(exp, 100, 200)
    _ring(act, 100, 152)
    _ring(act, 100, 248)
    return exp, act, (90, 190, 21, 21)


def test_a_region_that_fits_two_places_gets_no_vector():
    exp, act, box = _two_copies()
    r = _classify(exp, act, box)
    assert r.kind is not ChangeKind.MOVED, (r.kind, r.moved_dx, r.moved_dy)
    assert r.move_alternatives == 2
    assert (r.moved_dx, r.moved_dy) == (0, 0)


def test_the_shift_the_page_agrees_on_breaks_the_tie():
    exp, act, box = _two_copies()
    r = _classify(exp, act, box, prefer=(0, 48))
    assert r.kind is ChangeKind.MOVED
    assert (r.moved_dx, r.moved_dy) == (0, 48)


def test_a_tie_breaker_that_is_not_among_the_matches_is_ignored():
    exp, act, box = _two_copies()
    r = _classify(exp, act, box, prefer=(0, 30))
    assert r.kind is not ChangeKind.MOVED


def test_a_single_clear_match_is_still_a_move():
    exp, act = syn.blank(400, 400), syn.blank(400, 400)
    _ring(exp, 100, 200)
    _ring(act, 100, 248)
    r = _classify(exp, act, (90, 190, 21, 21))
    assert r.kind is ChangeKind.MOVED
    assert (r.moved_dx, r.moved_dy) == (0, 48)
    assert r.move_alternatives == 0


def _filter_panel(offset: int) -> np.ndarray:
    """Identical radio buttons next to distinct labels, as in a filter panel."""
    img = _blank_hd()
    if offset:
        cv2.putText(img, "Period", (700, 82), 0, 0.8, (30, 30, 30), 2, cv2.LINE_AA)
    labels = ["Live", "Message", "Status", "Direction", "Medium", "Flight"]
    for i, label in enumerate(labels):
        y = 60 + offset + i * 48
        _ring(img, 712, y + 10)
        cv2.putText(img, label, (740, y + 18), 0, 0.8, (30, 30, 30), 2,
                    cv2.LINE_AA)
    return img


def test_a_shifted_panel_of_repeated_controls_names_one_vector():
    """Every vector the result names must be the one the panel moved by."""
    r = _c(_filter_panel(0), _filter_panel(48))
    moved = [x for x in r.regions if x.kind is ChangeKind.MOVED]
    assert moved, r.summary()
    assert {(x.moved_dx, x.moved_dy) for x in moved} == {(0, 48)}, r.summary()


def test_an_unresolved_ambiguity_is_explained_in_the_notes():
    exp, act, _ = _two_copies()
    r = _c(exp, act)
    assert not any(x.kind is ChangeKind.MOVED for x in r.regions), r.summary()
    assert any("matched equally well" in n for n in r.notes), r.notes


# --------------------------------------------------------------------------- #
#  4. A moved block is scored by how far it went
# --------------------------------------------------------------------------- #
def _panel_page(dy: int, *, top: int = 200, height: int = 720,
                ink=(110, 110, 110)) -> np.ndarray:
    """A static header, and a light filter panel of one-pixel text below it."""
    img = syn.blank(1280, height)
    cv2.rectangle(img, (0, 0), (1280, 56), (245, 246, 248), -1)
    cv2.putText(img, "Flights", (24, 38), 0, 0.8, (30, 30, 30), 2, cv2.LINE_AA)
    y0 = top + dy
    cv2.rectangle(img, (40, y0), (280, y0 + 132), (228, 230, 235), 1)
    for i in range(4):
        cv2.rectangle(img, (54, y0 + 12 + i * 28), (66, y0 + 24 + i * 28), ink, 1)
        cv2.putText(img, f"Option {i}", (76, y0 + 23 + i * 28), 0, 0.5, ink, 1,
                    cv2.LINE_AA)
    return img


@pytest.mark.parametrize("dy", [12, 16, 24, 32, 48])
def test_a_shifted_panel_fails_on_severity_not_only_on_area(dy):
    """The live case: a panel 48 px lower scored 24.7 and failed on area alone.

    A smaller panel, or a smaller page, would not reach the area limit, and
    the move would pass without a word.
    """
    r = _c(_panel_page(0), _panel_page(dy))
    assert r.max_severity >= DiffConfig().fail_severity, r.summary()
    assert any(x.kind is ChangeKind.MOVED for x in r.regions), r.summary()


def _checkbox_page(d: int, y0: int = 1000) -> np.ndarray:
    img = syn.blank(1280, 1400)
    cv2.rectangle(img, (0, 0), (1280, 56), (245, 246, 248), -1)
    cv2.putText(img, "Flights", (24, 38), 0, 0.8, (30, 30, 30), 2, cv2.LINE_AA)
    cv2.rectangle(img, (300, y0 + d), (312, y0 + d + 12), (90, 90, 90), 1)
    return img


def test_a_small_element_moved_less_than_its_size_is_not_waved_through():
    """Below the fold, a 12-px checkbox moved by 16 px scored 17 and passed,
    while the same checkbox moved by 32 px (no overlap: removed + added)
    scored 35 and failed."""
    for d in (16, 24):
        r = _c(_checkbox_page(0), _checkbox_page(d))
        assert r.verdict is Verdict.FAIL, (d, r.summary())
        assert r.max_severity >= DiffConfig().fail_severity, (d, r.summary())


def test_moved_weight_grows_with_the_shift_in_element_sizes():
    from vistest.core.classify import MOVED_FULL_WEIGHT, moved_weight

    def region(h, dy):
        return DiffRegion(x=0, y=0, w=40, h=h, kind=ChangeKind.MOVED, moved_dy=dy)

    floor = 0.35
    #  A 100-px panel: the box spans 100 + shift.
    weights = [moved_weight(region(100 + d, d), floor) for d in (0, 4, 12, 48, 100, 300)]
    assert weights[0] == floor
    assert weights == sorted(weights)
    assert weights[-2] == weights[-1] == MOVED_FULL_WEIGHT
    #  Sideways works the same way, and the larger of the two ratios counts.
    side = DiffRegion(x=0, y=0, w=52, h=40, kind=ChangeKind.MOVED,
                      moved_dx=40, moved_dy=1)
    assert moved_weight(side, floor) == MOVED_FULL_WEIGHT
    #  A preset that asks for more than the full weight keeps it.
    assert moved_weight(region(100, 0), 1.5) == 1.5
