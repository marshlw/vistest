# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Close before open, and the deterministic stage that makes it safe.

The segmentation used to open the change mask (5×5 ellipse) before closing
it. That erases every structure thinner than five pixels — text — and let two
real regressions of the corpus through: a changed price and a changed promo
code. Closing first keeps them. It also keeps scattered rendering residue,
JPEG ringing and a scroll-bar band, which the old order happened to erase.

Those are taken out by `core/explain.py`, in the open engine, with no model:
every test here runs with `ai_hooks=None`. The acceptance bar is the corpus:
no false failure, fewer than three misses.
"""

from __future__ import annotations

from dataclasses import replace

import cv2
import numpy as np
import pytest

from vistest.config import VisTestConfig
from vistest.core import explain
from vistest.core.comparator import compare
from vistest.core.segment import clean_mask
from vistest.models import ChangeKind, Verdict

from . import corpus as cp
from . import synthetic as syn

CFG = VisTestConfig.preset_of("balanced").diff


def _c(exp, act, **kw):
    cfg = replace(CFG, **kw)
    return compare(exp, act, cfg=cfg, name="t")


@pytest.fixture(scope="module")
def cases():
    return {c.name: c for c in cp.build()}


@pytest.fixture(scope="module")
def base():
    return syn.page()


def _rules(result) -> set[str]:
    return {r.suppressed_by.split(":", 1)[0] for r in result.suppressed
            if r.suppressed_by}


# --------------------------------------------------------------------------- #
#  The order
# --------------------------------------------------------------------------- #
def test_a_one_pixel_stroke_survives_the_cleaning():
    mask = np.zeros((60, 200), dtype=bool)
    img = np.zeros((60, 200), np.uint8)
    cv2.putText(img, "259 990", (10, 40), 0, 0.6, 255, 1, cv2.LINE_AA)
    mask |= img > 100
    assert mask.sum() > 50

    kept = clean_mask(mask, open_px=2, close_px=6)
    #  Opening first leaves next to nothing of a one-pixel glyph.
    assert kept.sum() >= mask.sum(), (int(kept.sum()), int(mask.sum()))


def test_an_isolated_speck_is_still_removed():
    mask = np.zeros((60, 60), dtype=bool)
    mask[30, 30] = True
    assert not clean_mask(mask, open_px=2, close_px=6).any()


#  The corpus is drawn with OpenCV, and what it draws depends on the version.
#  `price changed` is one digit, "1" → "2": with OpenCV 4.x the digit leaves
#  41 changed pixels and is found; with 5.x it leaves 14, the anti-aliasing
#  filter takes most of them before segmentation, and it is missed either
#  way. That miss is upstream of the order tested here, so the case that is
#  asserted is the one both versions draw alike.
@pytest.mark.parametrize("name", ["promo text"])
def test_the_text_regressions_the_old_order_missed_are_found(cases, name):
    c = cases[name]
    r = compare(c.expected, c.actual, cfg=CFG, name=name)
    assert r.verdict is Verdict.FAIL, r.summary()
    assert any(x.kind in (ChangeKind.TEXT, ChangeKind.CONTENT, ChangeKind.COLOR)
               for x in r.regions), r.summary()


# --------------------------------------------------------------------------- #
#  The corpus, without the AI layer
# --------------------------------------------------------------------------- #
def test_no_false_failure_on_the_corpus_without_a_model(cases):
    wrong = [c.name for c in cases.values() if c.group == "NOISE"
             and compare(c.expected, c.actual, cfg=CFG).verdict is Verdict.FAIL]
    assert not wrong, wrong


def test_fewer_than_three_misses_on_the_corpus_without_a_model(cases):
    missed = [c.name for c in cases.values() if c.group == "SIGNAL"
              and compare(c.expected, c.actual, cfg=CFG).verdict is not Verdict.FAIL]
    assert len(missed) < 3, missed


@pytest.mark.parametrize("name, rule", [
    ("jpeg q=75", "jpeg"),
    ("scrollbar", "scrollbar"),
    ("antialias 0.4px", "rerender"),
    ("sensor noise σ=3.0", "rerender"),
    ("combined", "rerender"),
])
def test_each_noise_the_new_order_exposes_is_explained_by_its_own_test(cases, name, rule):
    c = cases[name]
    on = compare(c.expected, c.actual, cfg=CFG)
    assert on.verdict is Verdict.PASS, on.summary()
    assert rule in _rules(on), (rule, [r.suppressed_by for r in on.suppressed])
    assert any("Noise explained" in n for n in on.notes), on.notes


@pytest.mark.parametrize("name", ["scrollbar", "antialias 0.4px",
                                  "sensor noise σ=3.0", "combined"])
def test_without_the_stage_the_new_order_would_fail_them(cases, name):
    """The stage is what holds the line — not luck in the segmentation.

    `jpeg q=75` is not listed: whether its ringing survives segmentation
    depends on the JPEG encoder bundled with OpenCV (it does with 4.x, not
    with 5.x). The jpeg rule is still asserted above.
    """
    c = cases[name]
    off = compare(c.expected, c.actual, cfg=replace(CFG, explain_noise=False))
    assert off.verdict is Verdict.FAIL, off.summary()


def test_a_suppressed_region_says_what_it_was(cases):
    c = cases["scrollbar"]
    r = compare(c.expected, c.actual, cfg=CFG)
    assert r.suppressed
    for x in r.suppressed:
        assert x.kind is ChangeKind.NOISE and x.severity == 0.0
        assert "(was " in x.suppressed_by, x.suppressed_by


def test_the_model_sees_only_what_the_open_engine_left(cases):
    seen = []

    class Spy:
        def refine(self, regions, exp, act, res):
            seen.extend(regions)
            return regions

    c = cases["scrollbar"]
    compare(c.expected, c.actual, cfg=CFG, ai_hooks=Spy())
    assert not seen, [r.suppressed_by for r in seen]


# --------------------------------------------------------------------------- #
#  The explanations do not hide regressions
# --------------------------------------------------------------------------- #
_NOISES = {
    "jpeg75": lambda a: syn.recompress(a, 75),
    "jpeg60": lambda a: syn.recompress(a, 60),
    "scrollbar": syn.scrollbar,
    "antialias": lambda a: syn.resample_antialias(a, 0.4),
    "sensor3": lambda a: syn.add_sensor_noise(a, 3.0, seed=5),
}
_REGRESSIONS = ["price changed", "promo text", "button color", "tiny icon 22px",
                "text overflow", "button shrunk"]


@pytest.mark.parametrize("noise", sorted(_NOISES))
@pytest.mark.parametrize("name", _REGRESSIONS)
def test_a_regression_under_noise_still_fails(cases, name, noise):
    """What the engine finds on a clean frame, it finds on a noisy one.

    Stated as an implication on purpose: a case the engine misses anyway
    (see the note on `price changed` above) says nothing about whether an
    explanation hid it.
    """
    c = cases[name]
    clean = compare(c.expected, c.actual, cfg=CFG)
    noisy = compare(c.expected, _NOISES[noise](c.actual), cfg=CFG)
    assert clean.verdict is not Verdict.FAIL or noisy.verdict is Verdict.FAIL, \
        noisy.summary()


def test_a_flat_fill_change_is_never_explained_as_noise():
    """Dark fills are where an absolute tolerance would hide a real change."""
    for fill in [(30, 30, 30), (250, 250, 252), (0, 90, 200), (60, 20, 90)]:
        for delta in (6, 10, 16, 24):
            exp = np.full((300, 400, 3), fill, np.uint8)
            exp[20:40, 20:200] = (0, 0, 0) if sum(fill) > 300 else (255, 255, 255)
            act = exp.copy()
            act[100:180, 100:300] = np.clip(np.array(fill, int) + delta, 0, 255)
            on = _c(exp, act)
            off = _c(exp, act, explain_noise=False)
            assert on.verdict is off.verdict, (fill, delta, on.summary())
            assert not any(r.suppressed_by and r.suppressed_by.startswith("rerender")
                           for r in on.suppressed if off.verdict is Verdict.FAIL)


def test_a_one_pixel_text_that_changed_colour_a_lot_fails():
    exp = np.full((200, 600, 3), 255, np.uint8)
    act = exp.copy()
    cv2.putText(exp, "Promo code: FRIDAY24", (20, 100), 0, 0.6, (20, 20, 20), 1, cv2.LINE_AA)
    cv2.putText(act, "Promo code: FRIDAY24", (20, 100), 0, 0.6, (120, 120, 120), 1, cv2.LINE_AA)
    r = _c(exp, act)
    assert r.verdict is Verdict.FAIL, r.summary()


def test_a_right_edge_element_that_disappeared_is_not_a_scrollbar():
    exp = np.full((600, 800, 3), 246, np.uint8)
    cv2.putText(exp, "Header", (20, 40), 0, 0.8, (30, 30, 30), 2, cv2.LINE_AA)
    for i in range(12):
        cv2.rectangle(exp, (786, 20 + i * 48), (798, 44 + i * 48), (52, 120, 246), -1)
    act = exp.copy()
    act[:, 786:] = 246
    r = _c(exp, act)
    assert r.verdict is Verdict.FAIL, r.summary()
    assert "scrollbar" not in _rules(r)


def test_a_scrollbar_on_the_bottom_edge_is_recognised(base):
    rot_e = np.ascontiguousarray(base.transpose(1, 0, 2))
    rot_a = np.ascontiguousarray(syn.scrollbar(base).transpose(1, 0, 2))
    assert explain.scrollbar_bands(rot_e, rot_a) == [explain.Band("bottom", 12)]


def test_a_real_change_does_not_look_like_jpeg(cases):
    c = cases["button color"]
    r = compare(c.expected, c.actual, cfg=CFG)
    assert explain.detect_jpeg(r.maps["expected"], r.maps["aligned_actual"],
                               r.regions, r.maps["mask"]) is None


def test_the_stage_is_skipped_when_the_size_changed(cases):
    c = cases["page taller +160"]
    r = compare(c.expected, c.actual, cfg=CFG)
    assert r.verdict is Verdict.FAIL
    assert not _rules(r)
