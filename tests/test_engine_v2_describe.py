# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""v2, a region in words: what was measured on it, and nothing else.

One picture per sentence, built so that the answer is known: a card moved by
a pixel, a line drawn and one taken away, a button in another fill, words in
another ink, the same words drawn a little otherwise, a word that became
another. Then where the sentence is read — the failure message and the
report — and the sentences the engine writes on pairs of the browser corpus
whose change is known by construction.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

from vistest.core import compare
from vistest.core.v2 import describe as ds
from vistest.library.errors import ScreenshotMismatch, changed_line, description
from vistest.models import Verdict
from vistest.report.library import _regions_table

ROOT = Path(__file__).resolve().parents[1]
PAPER = (255, 255, 255)
PAGE = (250, 250, 252)
INK = (51, 51, 51)              # #333333


def _page() -> np.ndarray:
    img = np.empty((120, 200, 3), np.uint8)
    img[:] = PAGE
    img[10:110, 10:190] = PAPER                     # a card
    return img


def _words(img, ink=INK, x0=30, y0=30, n=8, heavy=()):
    """A line of «l»: 2 px stems 12 px tall, 9 px apart, with anti-aliased sides."""
    edge = np.rint(0.5 * np.array(ink, float) + 0.5 * np.array(PAPER, float))
    for i in range(n):
        x = x0 + 9 * i
        img[y0:y0 + 12, x:x + 2] = ink
        img[y0:y0 + 12, x - 1] = edge
        img[y0:y0 + 12, x + 2] = edge if i not in heavy else ink
    return img


def _one(a, b) -> str:
    """The sentence every region of the pair gets — one for all of them."""
    r = compare(a, b, engine="v2")
    said = {description(x) for x in r.regions}
    assert r.verdict is Verdict.FAIL and len(said) == 1, said
    return said.pop()


# --------------------------------------------------------------------------- #
#  One sentence per measurement
# --------------------------------------------------------------------------- #
def test_a_block_that_moved_is_said_with_its_move():
    a = _words(_page())
    b = _page()
    b[31:43, 20:110] = a[30:42, 20:110]
    assert _one(a, b) == "block moved by +1 px along y"


def test_a_line_added_is_said_with_its_size_and_colour():
    a = _words(_page())
    b = a.copy()
    b[60:61, 30:150] = (209, 213, 219)              # #d1d5db
    assert _one(a, b) == "line added: 120×1 px, #d1d5db"


def test_a_line_removed_is_said_too():
    a = _words(_page())
    a[60:100, 170:171] = (209, 213, 219)
    b = _words(_page())
    assert _one(a, b) == "line removed: 1×40 px, #d1d5db"


def test_a_fill_is_said_with_both_colours():
    a = _page()
    a[40:80, 40:160] = (52, 120, 246)               # a button
    b = _page()
    b[40:80, 40:160] = (154, 160, 170)
    assert _one(a, b) == "fill: #3478f6 → #9aa0aa, color difference 22.9"


def test_a_new_ink_on_the_same_shapes_is_said_with_both_inks():
    a = _words(_page())
    b = _words(_page(), ink=(122, 31, 31))           # #7a1f1f
    assert _one(a, b) == "recolored: #333333 → #7a1f1f, color difference 24.4"


def test_the_same_ink_drawn_a_little_otherwise_is_said_as_that():
    a = _words(_page())
    b = _words(_page(), heavy=range(8))             # every stem a shade heavier
    assert _one(a, b) == ("edges redrawn within 1 px, same color (#333333): the outline "
                          "changed slightly")


def test_other_shapes_are_counted():
    a = _words(_page())
    b = a.copy()
    b[30:42, 44:48] = INK                           # a stem became a bar
    b[36:38, 40:52] = INK
    text = _one(a, b)
    assert text == "shape changed: 23% of the new strokes are not where the old ones were"


def test_the_sentence_is_the_first_annotation_of_every_region_that_counts():
    a = _words(_page())
    b = _words(_page(), ink=(122, 31, 31))
    b[60:61, 30:150] = (209, 213, 219)
    r = compare(a, b, engine="v2")
    assert len(r.regions) == 9                      # eight stems and the line
    assert {g.annotations[0]["value"] for g in r.regions} == {"color", "line"}
    for g in r.regions:
        note = g.annotations[0]
        assert note["kind"] == ds.KIND and note["source"] == "engine v2"
    assert not any(n["kind"] == ds.KIND for s in r.suppressed for n in s.annotations)
    assert not any(n["kind"] == ds.KIND for g in compare(a, b, engine="v1").regions
                   for n in g.annotations)


# --------------------------------------------------------------------------- #
#  Where it is read
# --------------------------------------------------------------------------- #
def _mismatch(result, tmp_path) -> str:
    return str(ScreenshotMismatch.build(
        name="checkout", platform="", result=result, reason="test",
        baseline=tmp_path / "b.png", actual=tmp_path / "a.png", diff=None,
        report=None, limits={}))


def test_the_failure_message_says_what_changed(tmp_path):
    a = _words(_page())
    b = _words(_page(), ink=(122, 31, 31))
    r = compare(a, b, engine="v2")
    text = _mismatch(r, tmp_path)
    assert "  changed:     " + changed_line(r.regions[0]) in text
    assert changed_line(r.regions[0]).endswith(
        ": recolored: #333333 → #7a1f1f, color difference 24.4")


def test_the_failure_message_counts_what_it_does_not_spell_out(tmp_path):
    a = _page()
    b = a.copy()
    for i in range(5):
        b[20 + 15 * i:21 + 15 * i, 30:150] = (209, 213, 219)
    r = compare(a, b, engine="v2")
    assert len(r.regions) == 5
    text = _mismatch(r, tmp_path)
    assert text.count("line added: 120×1 px, #d1d5db") == 3
    assert "               ... and 2 more in the report" in text


def test_v1_says_what_it_said_before(tmp_path):
    a = _page()
    a[40:80, 40:160] = (52, 120, 246)
    b = _page()
    b[40:80, 40:160] = (154, 160, 170)
    r = compare(a, b, engine="v1")
    assert r.verdict is Verdict.FAIL
    assert "changed:" not in _mismatch(r, tmp_path)


def test_the_report_has_a_column_for_it():
    rows = [{"kind": "text", "severity": 40.0, "x": 1, "y": 2, "w": 3, "h": 4,
             "annotations": [{"kind": "description", "value": "color",
                              "text": "recolored: #333333 → #7a1f1f, color difference 29.3"},
                             {"kind": "page-shift", "text": "not the page's move"}]}]
    html = _regions_table({"regions": rows})
    assert "<th>what changed</th>" in html
    assert "<td>recolored: #333333 → #7a1f1f, color difference 29.3</td>" in html
    #  The kind is the sentence's, not the old classifier's (review v1, 4.5).
    assert "<td>color</td>" in html and "<td>text</td>" not in html
    assert "<td>not the page&#x27;s move</td>" in html or \
        "<td>not the page's move</td>" in html
    assert _regions_table({"regions": [dict(rows[0], annotations=[])]}) == ""


# --------------------------------------------------------------------------- #
#  On the browser corpus: changes known by construction
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def corpus():
    sys.path.insert(0, str(ROOT / "scripts"))
    import browser_corpus as bc

    from vistest.core import pngio

    m = bc.load_manifest()
    cases = {c["name"]: c for c in m["cases"]}

    def said(name):
        c = cases[name]
        r = compare(pngio.read(bc.CORPUS_DIR / m["templates"][c["template"]]["base"]),
                    pngio.read(bc.CORPUS_DIR / c["actual"]), engine="v2")
        return [description(x) for x in r.regions]
    return said


@pytest.mark.parametrize("name, sentence", [
    ("table/text_color/de15", "recolored: #1f2937 → #4d5666, color difference 14.8"),
    ("form/text_color/de15", "recolored: #111827 → #404758, color difference 15.0"),
    ("table/link_color/de15", "recolored: #2563eb → #6c8dff, color difference 15.0"),
    ("table/icon_color/de15", "recolored: #6b7280 → #949baa, color difference 15.0"),
    ("table/fill/de8", "fill: #2563eb → #4d77ff, color difference 8.0"),
    ("cards/fill/de8", "fill: #ea580c → #d04200, color difference 7.9"),
    ("table/underline/on", "line added: 57×1 px, #2563eb"),
    ("table/offset/plus1px", "block moved by +1 px along y"),
])
def test_the_corpus_changes_are_said_as_they_were_made(corpus, name, sentence):
    """Mutations of the calibration half whose change is known by construction:
    a new ink at ΔE00 15, a fill at ΔE00 8, an underline, a block moved a pixel
    down. The first region — the most severe — says it."""
    assert corpus(name)[0] == sentence
