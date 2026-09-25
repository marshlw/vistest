# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""The room between noise and content, pinned — or the build is red.

A threshold that separates noise from content is right only while there is
room on both sides of it. The frozen corpus replay (`test_corpus_frozen.py`)
notices when a verdict flips; by then the room is already gone. This file
notices earlier: it measures the populations the thresholds were placed
between and fails when either side has crossed the threshold, or has moved
past the worst case the threshold's own record says it was placed against.
Each failure names the gap, the side, the pair and the number.

Two gaps, each recorded beside its threshold as a `settings.Gap`:

* large flat recolour (`settings.FLAT_RECOLOUR_AREA`, `FLAT_RECOLOUR_CV`) —
  how big and how uniform an area of ΔE00 must be to be taken past consensus;
* the anti-aliasing re-drawing (`explain.AA_KEEP_ABOVE_GAP`) — how much of a
  group the best re-drawing of the baseline may leave unexplained and still
  call it rendering residue.

The records were measured on the frozen corpus *and* the generator; this file
replays the frozen corpus only, which is why the check is two-sided: the
threshold must separate what is measured here, and what is measured here must
not be worse than the record.
"""

from __future__ import annotations

import numpy as np
import pytest

from vistest.config import VisTestConfig
from vistest.core import color as _color
from vistest.core import explain as _explain
from vistest.core import refit as _refit
from vistest.core.comparator import compare
from vistest.core.settings import FLAT_RECOLOUR_AREA, FLAT_RECOLOUR_CV

from . import corpus as cp

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

pytestmark = pytest.mark.skipif(cv2 is None, reason="needs OpenCV")

#: The families each side of the re-drawing gap is made of, by corpus family
#: name. "Sub-pixel rendering residue" is what the record says; the rest of the
#: NOISE group is taken out by other rules (jpeg, scroll bar, caret) and has no
#: business on either side of this threshold.
SUBPIXEL_RESIDUE = ("antialias 0.4px", "combined", "sensor noise σ=1.6",
                    "sensor noise σ=3.0", "shadow radius", "subpixel text")
CHANGED_GLYPH = ("price changed", "promo text")


@pytest.fixture(scope="module")
def cases():
    return cp.load()


@pytest.fixture(scope="module")
def probed(cases):
    """One engine run per pair, with what the two thresholds were asked about.

    -> {case name: (case, ΔE00 map the consensus saw, [re-drawing residuals])}

    Taken from inside the run rather than recomputed: the ΔE map is the one
    after global alignment, and the residuals are the ones that decided.
    """
    cfg = VisTestConfig.preset_of(cp.METRICS_PRESET)
    de_maps: list[np.ndarray] = []
    residuals: list[float] = []
    real_de, real_fit = _color.delta_e_ciede2000, _refit.fit

    def de_spy(*a, **kw):
        m = real_de(*a, **kw)
        de_maps.append(m)
        return m

    def fit_spy(*a, **kw):
        f = real_fit(*a, **kw)
        residuals.append(f.residual)
        return f

    out = {}
    mp = pytest.MonkeyPatch()
    mp.setattr(_color, "delta_e_ciede2000", de_spy)
    mp.setattr(_explain._refit, "fit", fit_spy)
    try:
        for c in cases:
            de_maps.clear()
            residuals.clear()
            compare(c.expected, c.actual, cfg=cfg.diff, name=c.name)
            assert de_maps, "the comparator no longer computes ΔE00 through core.color"
            out[c.name] = (c, de_maps[0], list(residuals))
    finally:
        mp.undo()
    return out


def _components(de: np.ndarray, threshold: float):
    """-> [(% of the frame, σ/μ of ΔE)] for each 8-connected ΔE > threshold."""
    hit = (de > threshold).astype(np.uint8)
    n, lab, _, _ = cv2.connectedComponentsWithStats(hit, connectivity=8)
    if n <= 1:
        return []
    flat = lab.ravel()
    d = de.ravel().astype(np.float64)
    cnt = np.bincount(flat, minlength=n).astype(np.float64)
    mu = np.bincount(flat, weights=d, minlength=n) / np.maximum(cnt, 1)
    sq = np.bincount(flat, weights=d * d, minlength=n) / np.maximum(cnt, 1)
    sd = np.sqrt(np.maximum(sq - mu * mu, 0.0))
    return [(100.0 * cnt[i] / de.size, sd[i] / mu[i]) for i in range(1, n)]


def _family(c) -> str:
    return c.family or c.name


def _is_flat_recolour_signal(c) -> bool:
    return _family(c) == "header color"


def _gap_message(what, side, where, got, threshold, recorded):
    return (f"{what}: the {side} side moved to {got:.3f} ({where}); the threshold "
            f"is {threshold} and the record placed it against {recorded}. "
            "The room between noise and content is closing: measure again on both "
            "corpora and move the threshold with its record, not on its own.")


def test_flat_recolour_area_gap(probed):
    """Uniform noise stays small; a header recolour stays large."""
    thr = VisTestConfig.preset_of(cp.METRICS_PRESET).diff.delta_e_threshold
    gap = FLAT_RECOLOUR_AREA
    noise, signal = [], []
    for name, (c, de, _) in probed.items():
        for pct, cv in _components(de, thr):
            if c.group == "NOISE" and cv <= FLAT_RECOLOUR_CV.value:
                noise.append((pct, name))
        if _is_flat_recolour_signal(c):
            signal.append((max(p for p, _ in _components(de, thr)), name))
    assert signal, "no header recolour in the frozen corpus: nothing pins this gap"
    worst_noise, worst_signal = max(noise, default=(0.0, "-")), min(signal)
    assert worst_noise[0] < gap.value, _gap_message(
        "flat recolour, area % of frame", "noise", worst_noise[1],
        worst_noise[0], gap.value, f"noise at most {gap.noise} ({gap.noise_at})")
    assert worst_noise[0] <= gap.noise, _gap_message(
        "flat recolour, area % of frame", "noise", worst_noise[1],
        worst_noise[0], gap.value, f"noise at most {gap.noise} ({gap.noise_at})")
    assert worst_signal[0] >= gap.value, _gap_message(
        "flat recolour, area % of frame", "signal", worst_signal[1],
        worst_signal[0], gap.value, f"signal at least {gap.signal} ({gap.signal_at})")
    assert worst_signal[0] >= gap.signal, _gap_message(
        "flat recolour, area % of frame", "signal", worst_signal[1],
        worst_signal[0], gap.value, f"signal at least {gap.signal} ({gap.signal_at})")


def test_flat_recolour_uniformity_gap(probed):
    """A header recolour stays uniform; large noise stays uneven."""
    thr = VisTestConfig.preset_of(cp.METRICS_PRESET).diff.delta_e_threshold
    gap = FLAT_RECOLOUR_CV
    noise, signal = [], []
    for name, (c, de, _) in probed.items():
        comps = _components(de, thr)
        big = [(pct, cv) for pct, cv in comps if pct >= FLAT_RECOLOUR_AREA.value]
        if c.group == "NOISE":
            noise += [(cv, name) for _, cv in big]
        if _is_flat_recolour_signal(c):
            signal.append((max(comps)[1], name))   # σ/μ of its largest component
    assert noise, "no large noise in the frozen corpus: nothing pins this side"
    worst_noise, worst_signal = min(noise), max(signal)
    for ok, side, where, got, rec in (
        (worst_signal[0] <= gap.value, "signal", worst_signal[1], worst_signal[0],
         f"signal at most {gap.signal} ({gap.signal_at})"),
        (worst_signal[0] <= gap.signal, "signal", worst_signal[1], worst_signal[0],
         f"signal at most {gap.signal} ({gap.signal_at})"),
        (worst_noise[0] > gap.value, "noise", worst_noise[1], worst_noise[0],
         f"noise at least {gap.noise} ({gap.noise_at})"),
        (worst_noise[0] >= gap.noise, "noise", worst_noise[1], worst_noise[0],
         f"noise at least {gap.noise} ({gap.noise_at})"),
    ):
        assert ok, _gap_message("flat recolour, σ/μ of ΔE00", side, where, got,
                                gap.value, rec)


def test_antialias_redrawing_gap(probed):
    """Rendering residue is re-drawn almost whole; a changed glyph is not."""
    gap = _explain.AA_KEEP_ABOVE_GAP
    noise = [(r, n) for n, (c, _, rs) in probed.items()
             if c.group == "NOISE" and _family(c) in SUBPIXEL_RESIDUE for r in rs]
    signal = [(r, n) for n, (c, _, rs) in probed.items()
              if c.group == "SIGNAL" and _family(c) in CHANGED_GLYPH for r in rs]
    assert noise and signal, (
        "the frozen corpus no longer asks the re-drawing about residue and "
        f"about a changed glyph (residue: {len(noise)}, glyph: {len(signal)})")
    worst_noise, worst_signal = max(noise), min(signal)
    for ok, side, where, got, rec in (
        (worst_noise[0] <= gap.value, "residue", worst_noise[1], worst_noise[0],
         f"residue at most {gap.noise} ({gap.noise_at})"),
        (worst_noise[0] <= gap.noise, "residue", worst_noise[1], worst_noise[0],
         f"residue at most {gap.noise} ({gap.noise_at})"),
        (worst_signal[0] > gap.value, "changed glyph", worst_signal[1], worst_signal[0],
         f"a changed glyph at least {gap.signal} ({gap.signal_at})"),
        (worst_signal[0] >= gap.signal, "changed glyph", worst_signal[1],
         worst_signal[0], f"a changed glyph at least {gap.signal} ({gap.signal_at})"),
    ):
        assert ok, _gap_message("anti-aliasing re-drawing, share left unexplained",
                                side, where, got, gap.value, rec)


def test_every_gap_record_has_room():
    """A record whose two sides touch is not a gap. Caught before any corpus."""
    for name, gap, noise_below in (
        ("FLAT_RECOLOUR_AREA", FLAT_RECOLOUR_AREA, True),
        ("FLAT_RECOLOUR_CV", FLAT_RECOLOUR_CV, False),
        ("AA_KEEP_ABOVE_GAP", _explain.AA_KEEP_ABOVE_GAP, True),
    ):
        lo, hi = (gap.noise, gap.signal) if noise_below else (gap.signal, gap.noise)
        assert lo < gap.value < hi, f"{name}: {gap.value} is not between {lo} and {hi}"
        assert gap.measured and gap.sample, f"{name} has no measurement beside it"


def test_below_the_area_threshold_explain_still_stands():
    """The area threshold lowered on purpose, until the rule fires on a scroll bar.

    A scroll bar that appears is a uniform recolour, 0.31 % of the frame on
    generator layout 2 — the largest uniform noise measured. Lower the area
    threshold beneath it and the rule takes the bar past consensus; the verdict
    must still be pass, because `explain.py` names it for what it is. If this
    goes red, the area threshold has become the last line against a scroll bar,
    and that is worth more attention than the threshold itself.
    """
    from dataclasses import replace

    from . import synthetic as syn

    lay = syn.layouts(3)[2]
    base = syn.render(lay)
    actual = syn.NOISE_TRANSFORMS["scrollbar"](lay, base)
    default = VisTestConfig.preset_of(cp.METRICS_PRESET).diff
    lowered = replace(default, flat_recolour_min_area_pct=0.25)

    quiet = compare(base, actual, cfg=default, name="scrollbar, default")
    assert not any("flat recolour" in n for n in quiet.notes), quiet.notes

    r = compare(base, actual, cfg=lowered, name="scrollbar, area threshold 0.25 %")
    assert any("flat recolour" in n for n in r.notes), (
        "the rule did not fire on the scroll bar with the threshold below it: "
        "this test no longer tests anything")
    assert r.verdict.value == "pass", (
        f"the scroll bar fails once the flat-recolour rule lets it through: "
        f"{[(g.kind.value, round(g.severity, 1)) for g in r.regions]}")
    assert any((s.suppressed_by or "").startswith("scrollbar") for s in r.suppressed), (
        [s.suppressed_by for s in r.suppressed])
