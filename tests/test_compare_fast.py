# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Two shortcuts in `compare`, and the proof that neither changes an answer.

A green run is mostly pairs that are identical, pixel for pixel, and the rest
differ in a few thousand pixels out of a million. The cascade used to pay the
full price for both: alignment, two colour conversions, CIEDE2000 and SSIM over
the whole frame.

* **Identical arrays** return at once: PASS, no regions, the metrics of a
  perfect match.
* **ΔE00 is computed only where the RGB differs.** Everywhere else it is 0 —
  exactly what the formula gives a colour against itself.

Speed is the point, but the promise is the other half: the engine's verdicts
and numbers do not move. The benchmark's `--no-timing` output and
`metrics.json` are the published form of that promise and were compared byte
for byte before and after; the tests here hold it on every run.
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pytest

from vistest.config import VisTestConfig
from vistest.core import color, pngio
from vistest.core import comparator as comparator_module
from vistest.core.comparator import compare

CORPUS = Path(__file__).resolve().parent / "benchmark_corpus"
CFG = VisTestConfig.preset_of("balanced").diff


def pair(case: str) -> tuple[np.ndarray, np.ndarray]:
    d = CORPUS / case
    return (pngio.decode((d / "expected.png").read_bytes()),
            pngio.decode((d / "actual.png").read_bytes()))


# --------------------------------------------------------------------------- #
#  Identical
# --------------------------------------------------------------------------- #
def test_an_identical_pair_is_a_perfect_pass():
    expected, _ = pair("button_color")
    res = compare(expected, expected.copy(), cfg=CFG, engine="v1")
    assert res.verdict.value == "pass"
    assert res.regions == [] and res.suppressed == []
    assert res.ssim_global == 1.0
    assert res.de_mean == 0.0 and res.de_p95 == 0.0
    assert res.changed_pixels == 0 and res.changed_area_pct == 0.0
    assert res.region_pixels == res.suppressed_pixels == res.unassigned_pixels == 0
    assert res.max_severity == 0.0
    assert res.total_pixels == expected.shape[0] * expected.shape[1]
    assert not res.size_changed and not res.aligned
    assert any("pixel for pixel" in n for n in res.notes)


def test_it_leaves_the_maps_a_renderer_expects():
    expected, _ = pair("identical")
    res = compare(expected, expected.copy(), cfg=CFG, engine="v1")
    assert res.maps["de_map"].shape == expected.shape[:2]
    assert not res.maps["de_map"].any() and not res.maps["mask"].any()
    assert res.maps["expected"] is expected


def test_it_matches_what_the_full_cascade_says_about_the_same_pair():
    """The corpus's own identical pairs: the numbers `metrics.json` records."""
    for case in ("identical", "identical__thin_glyphs"):
        expected, actual = pair(case)
        assert np.array_equal(expected, actual), "the corpus pair is identical"
        fast = compare(expected, actual, cfg=CFG, engine="v1").to_dict()["metrics"]
        slow = _full(expected, actual).to_dict()["metrics"]
        assert fast == slow, case


def test_one_pixel_is_enough_to_run_the_cascade():
    expected, _ = pair("identical")
    changed = expected.copy()
    changed[0, 0] = 255 - changed[0, 0]
    res = compare(expected, changed, cfg=CFG, engine="v1")
    assert not any("pixel for pixel" in n for n in res.notes)


def test_a_different_size_is_never_identical():
    expected, _ = pair("identical")
    res = compare(expected, expected[:-10].copy(), cfg=CFG, engine="v1")
    assert res.size_changed and res.verdict.value == "fail"


def test_an_identical_900x1200_pair_takes_milliseconds():
    """Measured under 10 ms (about 1 ms here); the bound is generous for CI.

    Before the shortcut this pair took a quarter of a second. Best of five, so
    one slow scheduling slice on a shared runner is not a failure.
    """
    expected, _ = pair("identical")
    assert expected.shape[:2] == (1200, 900)
    actual = expected.copy()
    best = min(_timed(lambda: compare(expected, actual, cfg=CFG, engine="v1"))
               for _ in range(5))
    assert best < 50, f"{best:.1f} ms"


def _timed(fn) -> float:
    started = time.perf_counter()
    fn()
    return (time.perf_counter() - started) * 1000


# --------------------------------------------------------------------------- #
#  ΔE00 where the RGB differs
# --------------------------------------------------------------------------- #
def _same_floats(a: np.ndarray, b: np.ndarray) -> bool:
    return a.shape == b.shape and np.array_equal(a.view(np.uint32), b.view(np.uint32))


@pytest.mark.parametrize("seed", range(6))
def test_the_selective_map_is_the_full_map_bit_for_bit(seed):
    rng = np.random.default_rng(seed)
    h, w = rng.integers(1, 400, 2)
    expected = rng.integers(0, 256, (h, w, 3), dtype=np.uint8)
    actual = expected.copy()
    changed = rng.random((h, w)) < rng.random()
    actual[changed] = rng.integers(0, 256, (int(changed.sum()), 3), dtype=np.uint8)

    lab_e, lab_a = color.srgb_to_lab(expected), color.srgb_to_lab(actual)
    full = color.delta_e_ciede2000(lab_e, lab_a)
    where = np.any(expected != actual, axis=2)
    assert _same_floats(color.delta_e_ciede2000_where(lab_e, lab_a, where), full)
    assert not full[~where].any(), "a colour against itself is exactly 0"


@pytest.mark.parametrize("share", [0.5, 0.79, 0.81, 0.95])
def test_the_dense_path_gives_the_same_values(share):
    """Past DENSE_SHARE the full map is computed and masked: same floats."""
    rng = np.random.default_rng(11)
    e = rng.integers(0, 256, (60, 70, 3), dtype=np.uint8)
    a = rng.integers(0, 256, (60, 70, 3), dtype=np.uint8)
    lab_e, lab_a = color.srgb_to_lab(e), color.srgb_to_lab(a)
    where = rng.random((60, 70)) < share
    expected = np.where(where, color.delta_e_ciede2000(lab_e, lab_a), 0).astype(np.float32)
    assert _same_floats(color.delta_e_ciede2000_where(lab_e, lab_a, where), expected)


def test_nothing_selected_is_all_zeros():
    lab = color.srgb_to_lab(np.zeros((4, 5, 3), np.uint8))
    out = color.delta_e_ciede2000_where(lab, lab, np.zeros((4, 5), bool))
    assert out.dtype == np.float32 and out.shape == (4, 5) and not out.any()


def test_chunks_do_not_change_the_values():
    rng = np.random.default_rng(7)
    e = rng.integers(0, 256, (90, 110, 3), dtype=np.uint8)
    a = rng.integers(0, 256, (90, 110, 3), dtype=np.uint8)
    lab_e, lab_a = color.srgb_to_lab(e), color.srgb_to_lab(a)
    where = np.ones((90, 110), bool)
    assert _same_floats(color.delta_e_ciede2000_where(lab_e, lab_a, where, chunk=37),
                        color.delta_e_ciede2000(lab_e, lab_a))


CASES = sorted(p.parent.name for p in CORPUS.glob("*/expected.png"))


@pytest.mark.parametrize("case", CASES[::3])
def test_the_whole_result_is_unchanged_on_the_corpus(case):
    """The engine with the selective map against the engine with the full one."""
    expected, actual = pair(case)
    fast = compare(expected, actual, cfg=CFG, engine="v1")
    slow = _full(expected, actual)
    a, b = fast.to_dict(), slow.to_dict()
    a.pop("duration_ms"), b.pop("duration_ms")
    assert a == b, case
    assert _same_floats(fast.maps["de_map"], slow.maps["de_map"])


def _full(expected, actual):
    """`compare` as it was: no shortcut, and ΔE00 over every pixel."""
    def every_pixel(lab1, lab2, where, **kw):
        return color.delta_e_ciede2000(lab1, lab2)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(color, "delta_e_ciede2000_where", every_pixel)
        patch.setattr(comparator_module, "_same_pixels", lambda a, b: False)
        return compare(expected, actual, cfg=CFG, engine="v1")
