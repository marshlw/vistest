# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""The machinery of `tests/benchmark.py --corpus browser`: the table cannot lie
by construction. The figures themselves are not asserted here — they are what
is being measured.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from . import benchmark_browser as bb

bc = bb.bc
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def manifest():
    return bc.load_manifest()


def _tool(manifest, key, fails):
    return bb.Tool(key=key, title=key, group="VisTest",
                   failed={c["name"]: fails(c) for c in manifest["cases"]})


def test_every_pair_is_on_exactly_one_row(manifest):
    table = bb.rows(manifest)
    names = [n for r in table for n in r.names]
    assert sorted(names) == sorted(c["name"] for c in manifest["cases"])
    labels = [r.label for r in table]
    assert labels == sorted(labels, key=[bc.SIGNAL, bc.NOISE, bc.DISPUTED].index)
    by_name = {c["name"]: c for c in manifest["cases"]}
    for r in table:
        assert {by_name[n]["label"] for n in r.names} == {r.label}
    keys = {r.key for r in table}
    assert {"opacity 0.98", "fill de2", "render:shift_0.5px"} <= keys
    assert len([r for r in table if r.label == bc.SIGNAL]) == len(bc.FAMILIES)


def test_the_score_counts_errors_on_the_right_side(manifest):
    green = _tool(manifest, "green", lambda c: False)
    red = _tool(manifest, "red", lambda c: True)
    g, r = bb.score(green, manifest["cases"]), bb.score(red, manifest["cases"])
    assert (g.false, g.misses) == (0, g.signal)
    assert (r.false, r.misses) == (r.noise, 0)
    assert g.disputed_failed == 0 and r.disputed_failed == r.disputed > 0
    oracle = _tool(manifest, "oracle", lambda c: c["label"] == bc.SIGNAL)
    o = bb.score(oracle, manifest["cases"])
    assert (o.false, o.misses) == (0, 0)


def test_the_halves_add_up_to_the_whole(manifest):
    t = _tool(manifest, "mixed", lambda c: hash(c["name"]) % 3 == 0)
    whole = bb.score(t, manifest["cases"])
    parts = [bb.score(t, manifest["cases"], h) for h in bb.HALVES]
    for attr in ("false", "noise", "misses", "signal", "disputed_failed", "disputed"):
        assert getattr(whole, attr) == sum(getattr(p, attr) for p in parts), attr


def test_the_frontier_drops_a_dominated_setting(manifest):
    cases = manifest["cases"]
    noise = [c["name"] for c in cases if c["label"] == bc.NOISE]
    good = _tool(manifest, "good", lambda c: c["label"] == bc.SIGNAL)
    worse = _tool(manifest, "worse", lambda c: c["label"] == bc.SIGNAL or c["name"] == noise[0])
    other = _tool(manifest, "other", lambda c: False)
    assert {t.key for t in bb.frontier([good, worse, other], cases)} == {"good"}
    #  Better on one axis and worse on the other: both stay on the frontier;
    #  «worse» beats «red» on false failures at equal misses.
    red = _tool(manifest, "red", lambda c: True)
    assert {t.key for t in bb.frontier([worse, other, red], cases)} == {"worse", "other"}


def test_worst_rows_are_the_highest_rates_and_never_disputed(manifest):
    table = bb.rows(manifest)
    red = _tool(manifest, "red", lambda c: True)
    wst = bb.worst(red, table, manifest["cases"])
    assert len(wst) == 3
    assert all(r.label == bc.NOISE and e == n for r, e, n in wst)
    assert bb.worst(_tool(manifest, "oracle", lambda c: c["label"] == bc.SIGNAL),
                    table, manifest["cases"]) == []


def test_the_report_is_the_same_twice_and_has_no_timing(manifest):
    tools = [_tool(manifest, "a", lambda c: c["label"] != bc.NOISE),
             _tool(manifest, "b", lambda c: "fill" in c["name"])]
    tools[0].ms = 1234.4
    one = bb.report(manifest, tools, timing=False, notes=[])
    two = bb.report(manifest, tools, timing=False, notes=[])
    assert one == two
    assert "1234" not in one
    assert "never used to choose a threshold" in one
    assert "held out landing, dark" in one
    assert "1234" in bb.report(manifest, tools, timing=True, notes=[])


# --------------------------------------------------------------------------- #
#  The native Playwright grid
# --------------------------------------------------------------------------- #
def test_the_published_grid_is_of_this_corpus(manifest):
    doc = bb.load_native(bb.NATIVE_DEFAULT)
    assert doc["grid"]["threshold"] == [0.2, 0.1, 0.05]
    assert doc["grid"]["maxDiffPixels"] == [0, 25, 100, 500]
    tools = bb.playwright_tools(doc, manifest["cases"])
    assert len(tools) == 12
    assert [t.key for t in tools if t.default] == ["0.2/0"]
    for c in manifest["cases"]:
        entry = doc["results"][c["name"]]
        for key, failed in entry["failed"].items():
            thr, mdp = key.split("/")
            assert failed == (entry["diff_pixels"][thr] > int(mdp)), (c["name"], key)


def test_a_grid_of_other_files_is_refused(tmp_path):
    copy = tmp_path / "c"
    shutil.copytree(bc.CORPUS_DIR, copy, ignore=shutil.ignore_patterns("templates"))
    m = json.loads((copy / "manifest.json").read_text("utf-8"))
    m["about"] += " (edited)"
    (copy / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    with pytest.raises(bb.NativeError, match="different browser corpus"):
        bb.load_native(bb.NATIVE_DEFAULT, copy / "manifest.json")
    fake = tmp_path / "port.json"
    fake.write_text(json.dumps({"source": "port"}), encoding="utf-8")
    with pytest.raises(bb.NativeError, match="not a native run"):
        bb.load_native(fake)


# --------------------------------------------------------------------------- #
#  VisTest, on a handful of pairs — the path the table takes
# --------------------------------------------------------------------------- #
def test_vistest_answers_on_every_kind_of_pair(manifest):
    picked = {}
    for c in manifest["cases"]:
        picked.setdefault((c["template"] == "table", c["label"], c["kind"]), c)
    small = dict(manifest, cases=list(picked.values()))
    tool = bb.run_vistest(small, "balanced")
    assert set(tool.failed) == {c["name"] for c in small["cases"]}
    assert tool.title == "VisTest balanced" and tool.default


def test_without_corpus_the_benchmark_is_the_synthetic_one():
    """`--corpus` defaults to the synthetic corpus: the old command is unchanged."""
    out = subprocess.run([sys.executable, str(ROOT / "tests" / "benchmark.py"), "--help"],
                         capture_output=True, text=True, check=True).stdout
    assert "--corpus {synthetic,browser}" in out
    src = (ROOT / "tests" / "benchmark.py").read_text("utf-8")
    assert 'default="synthetic"' in src
