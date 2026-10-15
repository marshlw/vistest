# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Argos in the browser benchmark (plan, v7).

`scripts/bench_argos.mjs` runs Argos's own comparison (its `diffImages`,
over the odiff-bin it pins) on every pair of the browser corpus and writes
`docs/benchmark_browser_argos.json`; `tests/benchmark.py --corpus browser`
prints one row per threshold. The committed JSON must be of the corpus on
disk, cover its pairs and leave the sealed half alone; a JSON of another
corpus is refused. Where node and scripts/bench are installed, the script
itself is run on a corpus of three pairs made here.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import browser_corpus as bc  # noqa: E402

from tests import benchmark_browser as bb  # noqa: E402


@pytest.fixture(scope="module")
def argos() -> dict:
    return bb.load_argos(bb.ARGOS_DEFAULT)


def test_the_committed_run_is_of_this_corpus_and_leaves_the_sealed_half(argos):
    whole = bc.load_manifest(sealed=True)
    open_half = bc.unsealed(whole)["cases"]
    sealed = bc.sealed_only(whole)["cases"]
    assert sorted(argos["results"]) == sorted(c["name"] for c in open_half)
    assert not any(c["name"] in argos["results"] for c in sealed)
    assert argos["corpus"]["sealed"]["compared"] is False


def test_a_run_of_another_corpus_is_refused(tmp_path, argos):
    other = dict(argos, corpus=dict(argos["corpus"], sha256="0" * 64))
    path = tmp_path / "a.json"
    path.write_text(json.dumps(other), "utf-8")
    with pytest.raises(bb.NativeError, match="different browser corpus"):
        bb.load_argos(path)
    path.write_text(json.dumps(dict(argos, source="native")), "utf-8")
    with pytest.raises(bb.NativeError, match="not an Argos run"):
        bb.load_argos(path)


def test_one_row_per_threshold_and_the_default_is_marked(argos):
    cases = bc.load_manifest()["cases"]
    tools = bb.argos_tools(argos, cases)
    assert [t.key for t in tools] == ["a0.25", "a0.5", "a1"]
    assert [t.default for t in tools] == [False, True, False]
    assert all(t.group == "Argos" for t in tools)
    assert "threshold 0.5 (default)" in tools[1].title
    for t, thr in zip(tools, ("0.25", "0.5", "1"), strict=True):
        for c in cases:
            assert t.failed[c["name"]] is argos["results"][c["name"]]["failed"][thr]


def test_the_report_prints_argos_as_its_own_group(argos):
    manifest = bc.load_manifest()
    tools = bb.argos_tools(argos, manifest["cases"])
    out = bb.report(manifest, tools, timing=False, notes=[], engine_env="x")
    assert "By family — Argos" in out
    assert "Argos, held out:" in out
    md = bb.markdown(manifest, tools, None, ai=True, argos=argos)
    assert "## By family — Argos" in md and "argos-ci/argos" in md


def _png(rgb: np.ndarray) -> bytes:
    from vistest.core import pngio

    return pngio.encode(rgb)


@pytest.mark.skipif(shutil.which("node") is None
                    or not (ROOT / "scripts" / "bench" / "node_modules" / "odiff-bin").is_dir(),
                    reason="node and scripts/bench (npm ci --prefix scripts/bench) are needed")
def test_the_script_runs_argos_on_a_corpus(tmp_path):
    """Identical frames: no change at any threshold. A box drawn over the
    page: a change at every threshold. A two-pixel speck: under what Argos
    lets through at its default."""
    base = np.full((200, 300, 3), 245, np.uint8)
    boxed = base.copy()
    boxed[40:120, 60:200] = (30, 90, 200)
    speck = base.copy()
    speck[100, 100:102] = 0
    frames = {"base.png": base, "same.png": base, "boxed.png": boxed, "speck.png": speck}
    (tmp_path / "frames").mkdir()
    for name, rgb in frames.items():
        (tmp_path / "frames" / name).write_bytes(_png(rgb))
    manifest = {
        "templates": {"t": {"base": "frames/base.png", "split": "calibration"}},
        "cases": [{"name": f"t/{k}", "template": "t", "expected": "frames/base.png",
                   "actual": f"frames/{k}.png"} for k in ("same", "boxed", "speck")],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), "utf-8")
    out = subprocess.run(["node", str(ROOT / "scripts" / "bench_argos.mjs"), str(tmp_path)],
                         capture_output=True, text=True, check=True).stdout
    doc = json.loads(out)
    assert doc["source"] == "argos" and doc["corpus"]["cases"] == 3
    failed = {k: v["failed"] for k, v in doc["results"].items()}
    assert failed["t/same"] == {"0.25": False, "0.5": False, "1": False}
    assert failed["t/boxed"] == {"0.25": True, "0.5": True, "1": True}
    assert failed["t/speck"]["0.5"] is False
