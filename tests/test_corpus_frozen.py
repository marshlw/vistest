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

import numpy as np
import pytest

from . import corpus as cp


@pytest.fixture(scope="module")
def cases():
    return cp.load()


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
