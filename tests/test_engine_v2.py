# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""The v2 path, `compare(..., engine="v2")`: its base catches, and accounts.

What is held here is the contract of the base, not its figures on a corpus:
every discernible difference becomes part of a region, no pixel is left
unassigned, the smallest region is a number of pixels and not a share of the
frame, nothing is opened away, and the same pair gives the same answer. The
three pairs v1 is documented to lose (`scripts/diagnose_browser.py`) are
pinned as failures.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

from vistest.core import compare
from vistest.core.settings import DiffConfig
from vistest.core.v2 import V2Config
from vistest.core.v2 import base as v2base
from vistest.models import Verdict

ROOT = Path(__file__).resolve().parents[1]


def _page(h: int = 120, w: int = 160, bg=(250, 250, 250)) -> np.ndarray:
    img = np.empty((h, w, 3), np.uint8)
    img[:] = bg
    return img


def _accounted(r) -> None:
    assert r.unassigned_pixels == 0
    assert r.region_pixels + r.suppressed_pixels == r.changed_pixels


# --------------------------------------------------------------------------- #
#  The door
# --------------------------------------------------------------------------- #
def test_v2_is_the_default_and_an_unknown_engine_is_refused():
    """Step C of f5: v2 by default, v1 by name for one more release."""
    a = _page()
    b = a.copy()
    b[10:20, 10:20] = (0, 0, 0)
    one = compare(a, b)
    two = compare(a, b, engine="v2")
    assert [r.bbox for r in one.regions] == [r.bbox for r in two.regions]
    assert one.verdict is two.verdict and one.notes == two.notes
    assert any(n.startswith("Engine v2") for n in one.notes)
    old = compare(a, b, engine="v1")
    assert not any(n.startswith("Engine v2") for n in old.notes)
    assert old.notes[-1].startswith("engine v1 is deprecated")
    assert compare(a, b, cfg=DiffConfig(engine="v1")).notes == old.notes
    with pytest.raises(ValueError, match="engine must be"):
        compare(a, b, engine="v3")


def test_identical_pixels_pass_with_nothing_to_report():
    a = _page()
    r = compare(a, a.copy(), engine="v2")
    assert r.verdict is Verdict.PASS and not r.regions and not r.suppressed
    assert r.changed_pixels == 0


# --------------------------------------------------------------------------- #
#  The base
# --------------------------------------------------------------------------- #
def test_a_one_pixel_line_is_a_region_nothing_opens_it_away():
    """The underline v1 loses: 1 px high, 60 px long, a region of its own."""
    a = _page()
    b = a.copy()
    b[50, 40:100] = (37, 99, 235)
    r = compare(a, b, engine="v2")
    assert r.verdict is Verdict.FAIL
    assert len(r.regions) == 1
    reg = r.regions[0]
    assert (reg.x, reg.y, reg.w, reg.h) == (40, 50, 60, 1)
    assert reg.pixel_count == 60
    _accounted(r)


def test_a_difference_below_discernibility_is_not_a_candidate():
    a = _page(bg=(200, 200, 200))
    b = a.copy()
    b[30:60, 30:60] = (201, 200, 200)       # ΔE00 ≈ 0.3
    r = compare(a, b, engine="v2")
    assert r.verdict is Verdict.PASS and r.changed_pixels == 0
    b[30:60, 30:60] = (206, 200, 200)       # ΔE00 ≈ 2
    r = compare(a, b, engine="v2")
    assert r.verdict is Verdict.FAIL and r.changed_pixels == 900


def test_candidates_close_together_are_one_region_far_apart_two():
    a = _page()
    b = a.copy()
    #  Two 3×3 marks 4 px apart (gap of 3): within the 2 px grouping radius
    #  on both sides, one region.
    b[20:23, 20:23] = 0
    b[20:23, 26:29] = 0
    #  A third 20 px away: a region of its own.
    b[20:23, 49:52] = 0
    r = compare(a, b, engine="v2", v2=V2Config(min_region_px=1))
    assert sorted((g.x, g.w) for g in r.regions) == [(20, 9), (49, 3)]
    _accounted(r)


def test_the_smallest_region_is_pixels_and_is_still_accounted_for():
    a = _page()
    b = a.copy()
    b[10, 10] = (0, 0, 0)               # 1 px: below min_region_px
    b[60:64, 60:64] = (0, 0, 0)         # 16 px
    r = compare(a, b, engine="v2")
    assert V2Config().min_region_px > 1
    assert len(r.regions) == 1 and r.regions[0].pixel_count == 16
    assert len(r.suppressed) == 1
    small = r.suppressed[0]
    assert small.pixel_count == 1
    assert small.suppressed_by.startswith("min-size: 1 px < ")
    _accounted(r)
    #  Taken below one pixel, the rule takes nothing out.
    r = compare(a, b, engine="v2", v2=V2Config(min_region_px=1))
    assert len(r.regions) == 2 and not r.suppressed


def test_no_share_of_the_frame_decides():
    """The same 5×5 change fails on a thumbnail and on a large frame alike."""
    for h, w in ((64, 64), (1600, 2000)):
        a = _page(h, w)
        b = a.copy()
        b[30:35, 30:35] = (220, 38, 38)
        r = compare(a, b, engine="v2")
        assert r.verdict is Verdict.FAIL, (h, w)
        assert r.regions[0].pixel_count == 25


def test_group_labels_cover_exactly_the_candidates():
    rng = np.random.default_rng(7)
    cand = rng.random((80, 90)) > 0.97
    labels, groups = v2base.group(cand, 2)
    assert ((labels > 0) == cand).all()
    assert sum(g.pixels for g in groups) == int(cand.sum())
    assert len({g.label for g in groups}) == len(groups)
    for g in groups:
        ys, xs = np.nonzero(labels == g.label)
        assert (xs.min(), ys.min(), xs.max() - xs.min() + 1, ys.max() - ys.min() + 1) \
            == (g.x, g.y, g.w, g.h)
    assert [(g.y, g.x) for g in groups] == sorted((g.y, g.x) for g in groups)


def test_the_same_pair_gives_the_same_answer():
    rng = np.random.default_rng(3)
    a = rng.integers(0, 256, (90, 110, 3), dtype=np.uint8)
    b = a.copy()
    b[rng.random((90, 110)) > 0.9] = (0, 0, 0)
    one = compare(a, b, engine="v2")
    two = compare(a, b, engine="v2")
    assert [r.to_dict() for r in one.regions] == [r.to_dict() for r in two.regions]
    assert [r.to_dict() for r in one.suppressed] == [r.to_dict() for r in two.suppressed]
    assert one.notes == two.notes


def test_a_size_change_fails_as_in_v1():
    a = _page(100, 100)
    b = _page(110, 100)
    r = compare(a, b, engine="v2")
    assert r.size_changed and r.verdict is Verdict.FAIL


def test_the_ignore_mask_removes_candidates_before_grouping():
    a = _page()
    b = a.copy()
    b[10:20, 10:20] = 0
    mask = np.zeros(a.shape[:2], bool)
    mask[5:25, 5:25] = True
    r = compare(a, b, engine="v2", ignore_mask=mask)
    assert r.verdict is Verdict.PASS and r.changed_pixels == 0


# --------------------------------------------------------------------------- #
#  The pairs v1 is documented to lose
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
        return (pngio.read(bc.CORPUS_DIR / m["templates"][c["template"]]["base"]),
                pngio.read(bc.CORPUS_DIR / c["actual"]))
    return pair


@pytest.mark.parametrize("name", ["table/text_color/de15", "table/underline/on",
                                  "table/fill/de8", "table/border_removed/gone"])
def test_the_base_catches_what_v1_lost(corpus, name):
    exp, act = corpus(name)
    assert compare(exp, act, engine="v1").verdict is Verdict.PASS          # v1, as diagnosed
    r = compare(exp, act, engine="v2")
    assert r.verdict is Verdict.FAIL
    _accounted(r)


# --------------------------------------------------------------------------- #
#  The AI layer: whatever it does, nothing disappears
# --------------------------------------------------------------------------- #
class _DropsOne:
    """A layer that silently forgets the first region it is handed."""

    name = "test-dropper"

    def refine(self, regions, expected, actual, result):
        return regions[1:]


class _MarksOne:
    """A layer that suppresses a region the way the engine expects: by saying why."""

    def refine(self, regions, expected, actual, result):
        regions[0].suppressed_by = "gate: below the learned threshold"
        return regions


def _three_marks():
    a = _page()
    b = a.copy()
    for x in (10, 60, 110):
        b[20:26, x:x + 6] = (220, 38, 38)
    return a, b


def test_a_region_the_ai_layer_drops_is_suppressed_under_its_name():
    a, b = _three_marks()
    r = compare(a, b, engine="v2", ai_hooks=_DropsOne())
    assert len(r.regions) == 2
    dropped = [s for s in r.suppressed if s.suppressed_by.startswith("ai-layer:")]
    assert len(dropped) == 1 and dropped[0].pixel_count == 36
    assert dropped[0].suppressed_by.startswith("ai-layer: test-dropper did not return")
    _accounted(r)


def test_a_region_the_ai_layer_suppresses_keeps_the_layers_reason():
    a, b = _three_marks()
    r = compare(a, b, engine="v2", ai_hooks=_MarksOne())
    assert [s.suppressed_by for s in r.suppressed] == ["gate: below the learned threshold"]
    assert len(r.regions) == 2
    _accounted(r)


def test_a_layer_without_a_name_is_named_by_its_class():
    class Anonymous:
        def refine(self, regions, expected, actual, result):
            return []
    a, b = _three_marks()
    r = compare(a, b, engine="v2", ai_hooks=Anonymous())
    assert r.verdict is Verdict.PASS and not r.regions
    assert all(s.suppressed_by.startswith("ai-layer: Anonymous ") for s in r.suppressed)
    _accounted(r)


# --------------------------------------------------------------------------- #
#  What v2 reads of DiffConfig, and what it does not
# --------------------------------------------------------------------------- #
def test_v2_reads_only_the_diffconfig_fields_it_lists():
    """Change every field v2 claims to ignore, at once: not one number moves."""
    from dataclasses import fields, replace

    from vistest.core.settings import DiffConfig
    from vistest.core.v2.settings import DIFFCONFIG_READ

    base_cfg = DiffConfig()
    names = {f.name for f in fields(DiffConfig)}
    assert set(DIFFCONFIG_READ) <= names
    changed = {}
    for f in fields(DiffConfig):
        if f.name in DIFFCONFIG_READ:
            continue
        v = getattr(base_cfg, f.name)
        changed[f.name] = (not v if isinstance(v, bool) else
                           v * 3 + 1 if isinstance(v, (int, float)) else
                           ("moved",) if isinstance(v, tuple) else v)
    other = replace(base_cfg, **changed)
    rng = np.random.default_rng(11)
    a = rng.integers(0, 256, (80, 100, 3), dtype=np.uint8)
    b = a.copy()
    b[10:30, 10:40] = (0, 0, 0)
    b[rng.random((80, 100)) > 0.97] = (255, 255, 255)
    one = compare(a, b, engine="v2", cfg=base_cfg)
    two = compare(a, b, engine="v2", cfg=other)
    assert [r.to_dict() for r in one.regions] == [r.to_dict() for r in two.regions]
    assert [r.to_dict() for r in one.suppressed] == [r.to_dict() for r in two.suppressed]
    assert one.verdict is two.verdict
