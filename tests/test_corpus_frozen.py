# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""The benchmark corpus on disk is what the code draws — or the build is red.

The curated corpus is frozen in `tests/benchmark_corpus/` because OpenCV 5
draws its text differently from 4.x, and a corpus drawn at run time made the
benchmark figure a function of the installed OpenCV. Freezing only helps while
the files and the generator agree. When they stop agreeing — `synthetic.py`
was edited, or OpenCV was upgraded — that has to be a red build naming the
pairs, not a figure that quietly drifts.

Kept apart from `test_benchmark.py` on purpose: CI skips that file, and this
check has to run on every merge.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from . import corpus as cp


@pytest.fixture(scope="module")
def cases():
    return cp.load()


@pytest.fixture(scope="module")
def measured(cases):
    """The engine run over the corpus once, for every check in this file."""
    return cp.measure(cases)["cases"]


def test_frozen_corpus_matches_the_generator():
    """Pixel for pixel, for the raster of the installed OpenCV.

    Redrawing is legitimate and deliberate: `python tests/benchmark.py
    --regenerate` under each version in `corpus.RENDERS`, then new figures in
    the README.
    """
    render, drifted = cp.drift()
    if render is None:
        pytest.fail(
            f"OpenCV {cp.installed_opencv()} is installed, and the corpus is "
            "frozen for " + ", ".join(r.package for r in cp.RENDERS) + ". "
            "Whether this version draws the same pixels is unknown, and there "
            "is nothing to compare with. If it is a new raster, add it to "
            "tests/corpus.py::RENDERS and run "
            "`python tests/benchmark.py --regenerate`.")
    assert not drifted, (
        f"the code no longer draws what is in tests/benchmark_corpus "
        f"(raster {render.key}, {render.package}): {len(drifted)} pair(s) "
        f"drifted: {', '.join(drifted)}. If that is intended, run "
        "`python tests/benchmark.py --regenerate`; otherwise the corpus would "
        "have moved silently.")


def test_every_render_is_frozen(cases):
    """Every raster is on disk, and each carries the same phenomena."""
    families: dict[str, list[str]] = {}
    for c in cases:
        families.setdefault(c.render, []).append(c.family)
    assert set(families) == {r.key for r in cp.RENDERS}
    first, *rest = families.values()
    assert all(f == first for f in rest), families


def test_the_suffix_says_what_differs(cases):
    """A case name says which raster drew it — not «v2»."""
    renders = {r.key: r for r in cp.RENDERS}
    for c in cases:
        assert c.name == c.family + renders[c.render].suffix, c.name
    suffixes = [r.suffix for r in cp.RENDERS]
    assert len(set(suffixes)) == len(suffixes)
    assert not any("v2" in s.lower() for s in suffixes)


def test_the_rasters_really_differ(cases):
    """If two rasters were equal, the second would be a copy, not a case."""
    by_family: dict[str, list[cp.Case]] = {}
    for c in cases:
        by_family.setdefault(c.family, []).append(c)
    for family, group in by_family.items():
        first, *rest = group
        for other in rest:
            assert (first.actual.shape != other.actual.shape
                    or not np.array_equal(first.actual, other.actual)), family


def test_a_missing_corpus_says_how_to_get_it(tmp_path):
    with pytest.raises(cp.CorpusError, match="--regenerate"):
        cp.load(tmp_path)


# --------------------------------------------------------------------------- #
#  The frozen answer
# --------------------------------------------------------------------------- #
def test_the_engine_still_says_what_is_written_down(cases, measured):
    """The engine's answer to every pair, against what is written down.

    Three times a difference between two OpenCV majors was found by hand,
    months late, by printing two tables and diffing them; the last one sat in
    `core/align.py`. This is that diff, run on every merge, against a file in
    the repository instead of against another machine.

    Two rules, because there are two kinds of thing here. The verdict, the
    number of regions and the sentence the engine puts in `suppressed_by` are
    compared **exactly** — they were identical on all 25 environments measured,
    and they are what the published figure is made of. The four continuous
    metrics are compared against a **flat, measured tolerance**
    (`corpus.METRICS_TOLERANCE`), which is the platform's noise floor; above it
    nothing is forgiven.

    Why a tolerance and not simply fewer digits
    -------------------------------------------

    Because rounding is a tolerance with cliffs. This file first stored the
    metrics rounded to what the table printed and compared them exactly, and it
    went red on the first run on somebody else's machine, in the last digit of
    the noisiest pair: `sensor noise σ=3.0`, `changed_area_pct` 0.689 against
    0.6891, `ssim` 0.94783 against 0.94782, verdicts identical.

    Rounding harder only moves the cliff. Measured at two decimals of a
    percentage, `sensor noise σ=3.0, thin glyphs` sits 3.7e-04 from the nearest
    rounding boundary while that same number moves by 3.8e-03 between machines
    — ten times the margin. Green by luck, and the luck runs out on whichever
    machine lands on the other side. A flat tolerance has no boundary to sit
    next to.

    And it is more sensitive, not less. Yesterday's `core/align.py` defect
    showed on six rows of the detailed table; rounded to the precision that
    reproduces, five of the six survived. With the tolerance plus the strict
    sentence, all six do — `antialias 0.4px` moves no metric past its tolerance
    and is caught only by its explanation changing.

    If this is red, read this before reaching for `--record-metrics`
    -------------------------------------------------------------------

    Red means the engine's answer changed, and the first question is *what* —
    the failure lists every line with the distance, the tolerance and the
    measurement that tolerance came from, so a hair and a mile do not look
    alike. Re-recording is right when the change was intended and that list
    has been read. Re-recording because the numbers "look close enough" is how
    the file becomes green only on the machine it was written on.

    The one case where red is not the engine: a machine outside the sample.
    The floor was measured on 25 environments and **all of them were x86-64**
    (`corpus.METRICS_SAMPLE`). They cover OpenCV's SIMD dispatcher *inside*
    one instruction family; Apple Silicon and Graviton are a different family
    with different kernels again, and nobody has run this there. So a red line
    that arrives from an ARM machine, on `changed_area_pct` alone, with the
    verdict, the region counts and the sentences all intact and a Δ of the
    same order as the spread printed beside it, is most likely a platform this
    floor has never seen.

    That is not a reason to raise the number until it goes green. It is a
    reason to measure on that machine — run `corpus.measure()` there and on a
    machine inside the sample, take the widest difference — and then write the
    new `spread`, `environments` and `measured` into `corpus.METRICS_TOLERANCE`
    together with the tolerance. A tolerance whose provenance still says
    "25 environments, x86-64" while it is sized for ARM is a number nobody can
    check, which is the state this file was built to get out of.
    """
    drifted = cp.metrics_drift(cases=cases, measured=measured)
    assert not drifted, (
        f"the engine no longer answers what tests/benchmark_corpus/metrics.json "
        f"records, on {len(drifted)} count(s):\n  "
        + "\n  ".join(drifted[:20])
        + ("\n  ..." if len(drifted) > 20 else "")
        + "\n\nIf the change is intended, look at every line above — that is "
        "what it did to the benchmark — then run "
        "`python tests/benchmark.py --record-metrics` and commit the diff.")


def test_the_recorded_answer_covers_the_whole_corpus(cases):
    """No pair may quietly fall out of the check by not being in the file."""
    recorded = cp.load_metrics()
    assert set(recorded["cases"]) == {c.name for c in cases}
    assert recorded["preset"] == cp.METRICS_PRESET
    #  Both rules are written into the file, so a reader of the file alone
    #  knows what is compared exactly and what against what tolerance.
    assert recorded["tolerance"] == cp._floors_as_json()
    assert recorded["strict"] == list(cp.METRICS_STRICT)
    assert recorded["sample"] == cp.METRICS_SAMPLE
    for row in recorded["cases"].values():
        assert set(row) == set(cp.METRICS_STRICT) | set(cp.METRICS_TOLERANCE)


def test_a_missing_answer_says_how_to_get_it(tmp_path):
    with pytest.raises(cp.CorpusError, match="--record-metrics"):
        cp.load_metrics(tmp_path / "metrics.json")


def _with(doc, case, **fields):
    return {**doc, "cases": {**doc["cases"], case: {**doc["cases"][case], **fields}}}


def _written(tmp_path, doc):
    path = tmp_path / "metrics.json"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.mark.parametrize("field", sorted(cp.METRICS_TOLERANCE))
def test_twice_the_tolerance_is_red_and_says_by_how_much(cases, measured, tmp_path, field):
    """The artificial check: move a value by 2x its tolerance, look for red.

    A test of the test. A tolerance nobody has watched fail is a tolerance
    nobody knows the size of.
    """
    doc = cp.load_metrics()
    victim = cases[0].name
    floor = cp.METRICS_TOLERANCE[field]
    moved = round(doc["cases"][victim][field] + 2 * floor.tolerance,
                  cp.METRICS_STORED_DECIMALS)

    drifted = cp.metrics_drift(_written(tmp_path, _with(doc, victim, **{field: moved})),
                               measured=measured)

    assert len(drifted) == 1, drifted
    line = drifted[0]
    assert line.startswith(f"{victim}: {field} {moved} -> "), line
    #  Distance, tolerance, and where the tolerance came from — all three, so
    #  a reader can tell a new machine from a moved engine without leaving the
    #  failure.
    assert f"Δ {2 * floor.tolerance:.1e}" in line, line
    assert f"tolerance {floor.tolerance:.1e}" in line, line
    assert floor.provenance() in line, line
    assert f"over {floor.environments} environments" in line, line
    assert floor.measured in line, line


@pytest.mark.parametrize("field", sorted(cp.METRICS_TOLERANCE))
def test_half_the_tolerance_is_green(cases, measured, tmp_path, field):
    """And the other half of the same check: inside the floor, nothing moves."""
    doc = cp.load_metrics()
    victim = cases[0].name
    moved = round(doc["cases"][victim][field]
                  + 0.5 * cp.METRICS_TOLERANCE[field].tolerance,
                  cp.METRICS_STORED_DECIMALS)
    assert cp.metrics_drift(_written(tmp_path, _with(doc, victim, **{field: moved})),
                            measured=measured) == []


def test_a_strict_field_has_no_tolerance_at_all(cases, measured, tmp_path):
    """Verdict, region count and the explanation: any difference is the answer."""
    doc = cp.load_metrics()
    victim = next(c.name for c in cases if doc["cases"][c.name]["suppressed"])
    for field, wrong in (("verdict", "error"),
                         ("regions", doc["cases"][victim]["regions"] + 1),
                         ("suppressed", ["something else: it was explained away"])):
        drifted = cp.metrics_drift(_written(tmp_path, _with(doc, victim, **{field: wrong})),
                                   measured=measured)
        assert len(drifted) == 1, (field, drifted)
        assert drifted[0].startswith(f"{victim}: {field} "), drifted
        #  No `(Δ …, tolerance …)` annotation: nothing was forgiven here.
        #  Matched on the annotation, not on the word — a suppression sentence
        #  can contain "tolerance" itself.
        assert "(Δ " not in drifted[0], drifted


def test_the_explanation_is_pinned_without_its_digits(cases):
    """The sentence is compared, the counts inside it are not.

    A pixel count inside `suppressed_by` follows the change mask, which is
    what the metric tolerances cover; the same count differs between machines.
    What may not change quietly is which rule fired and what it claims.
    """
    recorded = cp.load_metrics()["cases"]
    sentences = [s for row in recorded.values() for s in row["suppressed"]]
    assert sentences, "no pair in the corpus has a suppressed region"
    assert not any(ch.isdigit() for s in sentences for ch in s), \
        [s for s in sentences if any(ch.isdigit() for ch in s)][:3]
    assert any(s.startswith("antialias: ") for s in sentences)


def test_drift_names_the_pair_that_moved(cases, tmp_path):
    render = cp.render_for()
    if render is None:
        pytest.skip(f"OpenCV {cp.installed_opencv()} is not in RENDERS")
    root = cp.export_corpus(cases, tmp_path / "corpus")
    victim = next(c for c in cases if c.render == render.key
                  and c.group == "SIGNAL")
    touched = victim.actual.copy()
    touched[0, 0] = 255 - touched[0, 0]
    from vistest.core import pngio

    pngio.write(root / cp._slug(victim.name) / "actual.png", touched)

    assert cp.drift(root) == (render, [victim.name])


def test_regenerate_touches_only_its_own_raster(cases, tmp_path):
    render = cp.render_for()
    if render is None:
        pytest.skip(f"OpenCV {cp.installed_opencv()} is not in RENDERS")
    root = cp.export_corpus(cases, tmp_path / "corpus")
    foreign = next(c for c in cases if c.render != render.key)
    foreign_png = root / cp._slug(foreign.name) / "actual.png"
    before = foreign_png.read_bytes()

    cp.regenerate(root)

    assert foreign_png.read_bytes() == before
    assert cp.drift(root) == (render, [])
    assert [c.name for c in cp.load(root)] == [c.name for c in cases]


def test_regenerate_refuses_an_unknown_raster(monkeypatch, tmp_path):
    monkeypatch.setattr(cp, "installed_opencv", lambda: "9.9")
    with pytest.raises(cp.CorpusError, match="RENDERS"):
        cp.regenerate(tmp_path)
    assert not (tmp_path / "manifest.json").exists()
