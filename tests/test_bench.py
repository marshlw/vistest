# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""`vistest bench`: pairs from elsewhere, v2 against Playwright's comparator.

On small PNGs drawn here: the three layouts and what each does not account
for, a pair of two sizes, a folder that is none of them, the path without
Node, labels.csv and the count from it, the same text and the same report
for the same input. And on the browser corpus: the command on the table
template's calibration pairs gives, pair by pair, the verdicts the benchmark
gives (v2 through `run_vistest`, Playwright through the committed native
JSON). held-out-2 is not read.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from vistest import bench
from vistest.bench import layouts
from vistest.bench import playwright as pw
from vistest.core import pngio

ROOT = Path(__file__).resolve().parents[1]


def png(fill: int = 240, size=(40, 60), box: tuple | None = None, color=(20, 20, 20)) -> bytes:
    img = np.full((*size, 3), fill, np.uint8)
    if box:
        x, y, w, h = box
        img[y:y + h, x:x + w] = color
    return pngio.encode(img)


def put(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def args(**kw) -> argparse.Namespace:
    base = dict(folder=None, expected=None, actual=None, baselines=None, out="out",
                labels=None, playwright=None, no_playwright=True, no_ai=False)
    base.update(kw)
    return argparse.Namespace(**base)


def run(capsys, **kw) -> str:
    assert bench.main(args(**kw)) == 0
    return capsys.readouterr().out


# --------------------------------------------------------------------------- #
#  The three layouts
# --------------------------------------------------------------------------- #
def test_playwright_test_results_are_recognised_and_the_rest_is_said(tmp_path):
    tr = tmp_path / "test-results"
    put(tr / "checkout-chromium" / "cart-expected.png", png())
    put(tr / "checkout-chromium" / "cart-actual.png", png(box=(5, 5, 10, 10)))
    put(tr / "checkout-chromium" / "cart-diff.png", png(0))
    put(tr / "checkout-chromium" / "trace.zip", b"zip")
    put(tr / "checkout-chromium" / "error-context.md", b"#")
    put(tr / "home-chromium" / "hero-actual.png", png())          # no baseline yet
    put(tr / "home-chromium" / "screenshot.png", png())           # not a pair
    found = layouts.discover(tr)
    assert found.layout == "Playwright test-results"
    assert [p.id for p in found.pairs] == ["checkout-chromium/cart"]
    assert found.pairs[0].canary_expected is None and found.pairs[0].canary_actual is None
    assert found.unrecognised == [
        "home-chromium/hero-actual.png: no hero-expected.png next to it (Playwright "
        "writes only the actual when there is no baseline)",
        "home-chromium/screenshot.png: a picture that is not <name>-expected.png or "
        "<name>-actual.png"]
    assert found.skipped == ["Playwright's own -diff.png / -previous.png, left out: 1",
                             "not pictures, left out: 2 files (.md 1, .zip 1)"]


def test_a_vistest_run_is_read_with_its_canaries(tmp_path):
    """The baseline's canary from its passport; the run's from what the
    library kept under .vistest/renderers/, or the baseline's when the check
    said the renderer was the same."""
    root = tmp_path / "project"
    base = root / "tests" / "__vistest__" / "linux-chromium-1x-60x40"
    canary_a, canary_b = png(255, (8, 8)), png(0, (8, 8))
    sha_a = "a" * 64
    sha_b = "b" * 64
    put(root / "tests" / "__vistest__" / ".renderers" / f"{sha_a}.png", canary_a)
    put(root / ".vistest" / "renderers" / f"{sha_b}.png", canary_b)
    for name, run in (("same", {"status": "same", "pixels": None}),
                      ("other", {"status": "changed", "pixels": 9, "run_sha256": sha_b}),
                      ("passed", {"status": "not_checked", "pixels": None})):
        put(base / f"{name}.png", png())
        (base / f"{name}.json").write_text(json.dumps(
            {"renderer": {"sha256": sha_a, "canary_version": 1}}), encoding="utf-8")
        put(root / ".vistest" / "actual" / "linux-chromium-1x-60x40" / f"{name}.png",
            png(box=(1, 1, 5, 5)))
        put(root / ".vistest" / "report" / "parts" / f"{name}.json", json.dumps(
            {"name": f"{name}.png", "platform": "linux-chromium-1x-60x40",
             "renderer": run, "images": {}}).encode())
    put(root / ".vistest" / "actual" / "linux-chromium-1x-60x40" / "lost.png", png())
    for folder in (root, root / ".vistest"):
        found = layouts.discover(folder)
        assert found.layout == "VisTest run"
        pairs = {p.id: p for p in found.pairs}
        assert sorted(pairs) == ["linux-chromium-1x-60x40/other",
                                 "linux-chromium-1x-60x40/passed",
                                 "linux-chromium-1x-60x40/same"]
        same, other, passed = (pairs[f"linux-chromium-1x-60x40/{n}"]
                               for n in ("same", "other", "passed"))
        assert same.canary_expected == same.canary_actual          # the check said so
        assert other.canary_actual == root / ".vistest" / "renderers" / f"{sha_b}.png"
        assert passed.canary_actual is None and "a check that passed draws none" in \
            passed.canary_note
        assert found.unrecognised == [".vistest/actual/linux-chromium-1x-60x40/lost.png: "
                                      "no baseline at tests/__vistest__/"
                                      "linux-chromium-1x-60x40/lost.png"]


def test_a_vistest_run_the_library_wrote(tmp_path, monkeypatch, capsys):
    """The layout as the library really writes it: a baseline from bytes and a
    failed check."""
    from vistest import expect_screenshot
    from vistest.library import context as _context
    from vistest.library.errors import ScreenshotMismatch

    monkeypatch.chdir(tmp_path)
    ctx = _context.LibraryContext(root=tmp_path, platform_override="linux-chromium-1x-60x40")
    _context.install(ctx)
    try:
        ctx.update = True
        expect_screenshot(png(), "cart.png")
        ctx.update = False
        with pytest.raises(ScreenshotMismatch):
            expect_screenshot(png(box=(5, 5, 10, 10)), "cart.png")
    finally:
        _context.uninstall()
    found = layouts.discover(tmp_path)
    assert [p.id for p in found.pairs] == ["linux-chromium-1x-60x40/cart"]
    out = run(capsys, folder=str(tmp_path), out=str(tmp_path / "bench"))
    assert "layout: VisTest run; 1 pair" in out
    assert "linux-chromium-1x-60x40/cart · v2 fail — 1 region: 10x10 at (5, 5)" in out
    assert "renderer: unknown — the baseline names no canary" in out


def test_two_folders_with_canaries_next_to_the_pictures(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    put(a / "home.png", png())
    put(b / "home.png", png(box=(2, 2, 4, 4)))
    put(a / "home.canary.png", png(255, (8, 8)))
    put(a / "deep" / "card.png", png())
    put(b / "deep" / "card.png", png())
    put(a / "only-here.png", png())
    put(b / "only-there.png", png())
    put(b / "notes.txt", b"x")
    found = layouts.discover(expected=a, actual=b)
    assert found.layout == "two folders"
    assert [p.id for p in found.pairs] == ["deep/card", "home"]
    home = found.pairs[1]
    assert home.canary_expected == a / "home.canary.png" and home.canary_actual is None
    assert home.canary_note == "no home.canary.png in --actual"
    assert found.unrecognised == ["only-here.png: in --expected, not in --actual",
                                  "only-there.png: in --actual, not in --expected"]
    assert found.skipped == ["not pictures, left out: 1 file (.txt 1)"]


def test_a_folder_that_is_no_layout_is_refused_with_what_was_looked_for(tmp_path, capsys):
    put(tmp_path / "x.png", png())
    with pytest.raises(layouts.LayoutError, match="no layout recognised"):
        layouts.discover(tmp_path)
    assert bench.main(args(folder=str(tmp_path))) == 2
    assert "use --expected and --actual" in capsys.readouterr().err
    with pytest.raises(layouts.LayoutError, match="go together"):
        layouts.discover(expected=tmp_path)


# --------------------------------------------------------------------------- #
#  The pairs
# --------------------------------------------------------------------------- #
def test_two_sizes_are_compared_as_they_are_and_said(tmp_path, capsys):
    a, b = tmp_path / "a", tmp_path / "b"
    put(a / "page.png", png(size=(40, 60)))
    put(b / "page.png", png(size=(48, 60)))
    put(a / "broken.png", b"not a png")
    put(b / "broken.png", b"not a png")
    out = run(capsys, expected=str(a), actual=str(b), out=str(tmp_path / "o"))
    assert "sizes differ: page: the baseline is 60x40, the screenshot 60x48 — compared " \
           "as they are" in out
    assert "not recognised: broken: not a picture this can read" in out
    assert "pairs: 1 — v2 failed 1, passed 0" in out


def test_without_node_the_playwright_columns_are_empty_and_say_how(tmp_path, capsys,
                                                                     monkeypatch):
    monkeypatch.setattr(pw.shutil, "which", lambda name: None)
    a, b = tmp_path / "a", tmp_path / "b"
    put(a / "same.png", png())
    put(b / "same.png", png())
    put(a / "moved.png", png(box=(5, 5, 10, 10)))
    put(b / "moved.png", png(box=(20, 5, 10, 10)))
    got = pw.run(layouts.discover(expected=a, actual=b).pairs, look_from=[a])
    assert isinstance(got, pw.Unavailable) and got.why == "no node on PATH"
    assert got.how.startswith("install Node.js 18 or newer, then pass --playwright")
    out = run(capsys, expected=str(a), actual=str(b), out=str(tmp_path / "o"),
              no_playwright=False)
    assert ("Playwright: the columns are empty — no node on PATH. To fill them: install "
            "Node.js 18 or newer") in out
    assert "v2 failed (no Playwright to disagree with):" in out
    rows = list(csv.DictReader(open(tmp_path / "o" / "labels.csv", encoding="utf-8")))
    assert [(r["pair"], r["v2"], r["playwright 0.05/0"], r["label"]) for r in rows] == \
        [("moved", "fail", "—", "")]


def test_the_page_shift_flag(tmp_path):
    """A failure where the page's fractional move is proven is flagged."""
    from vistest.bench import PairResult

    pair = layouts.Pair("p", tmp_path / "a.png", tmp_path / "b.png")
    flagged = PairResult(pair, True, 1, ["4x4 at (0, 0): x"], "renderer: unknown", (0.25, 0.0))
    passed = PairResult(pair, False, 0, [], "renderer: unknown", (0.25, 0.0))
    assert flagged.shift_flag and not passed.shift_flag
    assert bench.disagreement_line(flagged).endswith(
        "[the page moved by (+0.25, +0) px, proven]")


# --------------------------------------------------------------------------- #
#  labels.csv and the count
# --------------------------------------------------------------------------- #
def _result(pid, v2, p005, p02):
    pair = layouts.Pair(pid, Path("e.png"), Path("a.png"))
    r = bench.PairResult(pair, v2, int(v2), ["1x1 at (0, 0): x"] if v2 else [], "", None)
    r.playwright = {"0.05/0": {"failed": p005, "pixels": 1 if p005 else 0},
                    "0.2/0": {"failed": p02, "pixels": 1 if p02 else 0}}
    return r


def test_the_count_is_the_benchmarks(tmp_path):
    """NOISE failed is a false failure, SIGNAL passed a miss; unsure is printed
    and not counted; a label that is none of the three is said."""
    results = [_result("a", True, False, False),      # v2 fails, Playwright passes
               _result("b", False, True, False),
               _result("c", True, True, True),
               _result("d", False, False, True),
               _result("e", True, False, False)]
    labels_file = tmp_path / "labels.csv"
    labels_file.write_text("pair,label\na,SIGNAL\nb,noise\nc,NOISE\nd,unsure\n"
                           "e,maybe\nf,SIGNAL\n", encoding="utf-8")
    labels, said = bench.read_labels(labels_file)
    assert labels == {"a": "SIGNAL", "b": "NOISE", "c": "NOISE", "d": "unsure",
                      "f": "SIGNAL"}
    assert said == ["labels.csv line 6: label 'maybe' is not SIGNAL, NOISE or unsure — "
                    "not counted"]
    lines = bench.count_labels(results, labels, playwright=True)
    assert lines[0] == ("labelled pairs: 4 — SIGNAL 1, NOISE 2, unsure 1 (printed, not "
                        "counted)")
    assert lines[1] == "labelled but not in this input: 1 — f"
    table = {line.split()[0] + (" " + line.split()[1] if line.startswith("Playwright")
                                else ""): line.split()[-3:] for line in lines[3:]}
    assert table == {"v2": ["1/2", "0/1", "0/1"],
                     "Playwright 0.05/0": ["2/2", "1/1", "0/1"],
                     "Playwright 0.2/0": ["1/2", "1/1", "1/1"]}


def test_labels_csv_and_back(tmp_path, capsys):
    """The disagreements go to labels.csv with an empty label; filled in and
    handed back with --labels, they are counted — and a rerun keeps them."""
    a, b = tmp_path / "a", tmp_path / "b"
    for name, box in (("button", (5, 5, 10, 10)), ("plain", None)):
        put(a / f"{name}.png", png())
        put(b / f"{name}.png", png(box=box) if box else png())
    out_dir = tmp_path / "o"
    run(capsys, expected=str(a), actual=str(b), out=str(out_dir))
    before = (out_dir / "labels.csv").read_text(encoding="utf-8")
    header, row = before.splitlines()
    assert header == "pair,v2,playwright 0.05/0,playwright 0.2/0,v2 says,label"
    assert row.startswith('button,fail,—,—,"fail — 1 region: 10x10 at (5, 5): ')
    assert row.endswith('",')                                     # the label: empty
    (out_dir / "labels.csv").write_text(before.rstrip("\n") + "SIGNAL\n", encoding="utf-8")
    text = run(capsys, expected=str(a), actual=str(b), out=str(out_dir),
               labels=str(out_dir / "labels.csv"))
    assert "labelled pairs: 1 — SIGNAL 1, NOISE 0, unsure 0 (printed, not counted)" in text
    assert [line.split()[-3:] for line in text.splitlines() if line.startswith("v2  ")] == \
        [["0/0", "0/1", "0/0"]]
    after = (out_dir / "labels.csv").read_text(encoding="utf-8")
    assert after.splitlines()[1].endswith(",SIGNAL")             # the label is kept


def test_the_same_input_gives_the_same_output(tmp_path, capsys):
    a, b = tmp_path / "a", tmp_path / "b"
    put(a / "x.png", png())
    put(b / "x.png", png(box=(3, 3, 6, 6)))
    first = run(capsys, expected=str(a), actual=str(b), out=str(tmp_path / "o"))
    report = (tmp_path / "o" / "report.html").read_bytes()
    second = run(capsys, expected=str(a), actual=str(b), out=str(tmp_path / "o"))
    assert first == second
    assert (tmp_path / "o" / "report.html").read_bytes() == report
    assert first.splitlines()[1] == (f"Nothing was sent anywhere: the pictures were read "
                                     f"where they are, and what this wrote is in "
                                     f"{(tmp_path / 'o').as_posix()}/.")
    html = report.decode("utf-8")
    assert "Nothing was sent anywhere" in html and 'class="row fail" open' in html


def test_the_output_folder_is_ignored_by_git():
    assert "vistest-bench-out/" in (ROOT / ".gitignore").read_text(encoding="utf-8").split()


def test_the_cli_takes_the_command(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    put(a / "x.png", png())
    put(b / "x.png", png())
    done = subprocess.run([sys.executable, "-c", "from vistest.cli import main; "
                           "raise SystemExit(main())", "bench", "--expected", str(a),
                           "--actual", str(b), "--out", str(tmp_path / "o"),
                           "--no-playwright"], capture_output=True, text=True, cwd=tmp_path)
    assert done.returncode == 0, done.stderr
    assert "pairs: 1 — v2 failed 0, passed 1" in done.stdout


# --------------------------------------------------------------------------- #
#  On the browser corpus: the benchmark's verdicts, pair by pair
# --------------------------------------------------------------------------- #
def test_the_command_gives_the_benchmarks_verdicts_on_calibration_pairs(tmp_path, capsys):
    """Calibration pairs of the table template as two folders with their
    canaries next to them: v2's verdict per pair is `run_vistest`'s, and
    Playwright's (when Node and scripts/bench are there) the committed native
    JSON's."""
    sys.path.insert(0, str(ROOT / "scripts"))
    sys.path.insert(0, str(ROOT / "tests"))
    import browser_corpus as bc
    from benchmark_browser import run_vistest

    m = bc.load_manifest()                       # the sealed half is not read
    assert m["templates"]["table"]["split"] == bc.CALIBRATION
    table = [c for c in m["cases"] if c["template"] == "table"]
    #  Every NOISE and DISPUTED pair of the template and every third SIGNAL one:
    #  each kind of answer, at a third of the time.
    cases = [c for i, c in enumerate(table) if c["label"] != "SIGNAL" or i % 3 == 0]
    sub = dict(m, cases=cases)
    fps = m["renderers"]["fingerprints"]
    a, b = tmp_path / "expected", tmp_path / "actual"
    for c in cases:
        for side, src, key in ((a, bc.CORPUS_DIR / m["templates"]["table"]["base"],
                                c["renderer"]["expected"]),
                               (b, bc.CORPUS_DIR / c["actual"], c["renderer"]["actual"])):
            dst = side / f"{c['name']}.png"
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            if key:
                shutil.copyfile(bc.CORPUS_DIR / fps[key]["file"],
                                side / f"{c['name']}.canary.png")
    have_pw = shutil.which("node") and (ROOT / "scripts" / "bench" / "node_modules").is_dir()
    run(capsys, expected=str(a), actual=str(b), out=str(tmp_path / "o"),
        no_playwright=not have_pw, playwright=str(ROOT / "scripts" / "bench"))
    got = {p["pair"]: p for p in json.loads((tmp_path / "o" / "pairs.json").read_text("utf-8"))}
    assert sorted(got) == sorted(c["name"] for c in cases)
    v2 = run_vistest(sub, "balanced", engine="v2")
    assert {k: "fail" if f else "pass" for k, f in v2.failed.items()} == \
        {k: p["v2"] for k, p in got.items()}
    if not have_pw:
        pytest.skip("no node or scripts/bench/node_modules: Playwright's half not checked")
    native = json.loads((ROOT / "docs" / "benchmark_browser_native.json").read_text("utf-8"))
    for name, p in got.items():
        want = native["results"][name]["failed"]
        assert p["playwright"]["0.05/0"]["failed"] == want["0.05/0"], name
        assert p["playwright"]["0.2/0"]["failed"] == want["0.2/0"], name
