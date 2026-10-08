# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""What a person reads, said once and short (review v1, step C).

4.3 the threshold line without the machinery; 4.4 the reason, the second
look and a hint on lines of their own; 4.5 the report's kind is the
sentence's; 4.6–4.7 one sentence for a change in the message, the report,
the pass reason and `vistest bench`, with no «letters» or «ink»; 4.8 the
color difference once with its scale, SSIM and the canary in the report's
details, no renderer for pictures not from a page; 4.9 «color»; 4.11 how to
accept, where the check runs. And no line of a message longer than 120
characters but a path.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import warnings
from pathlib import Path

import numpy as np
import pytest

from vistest import expect_screenshot
from vistest.core import pngio
from vistest.library import context as _context
from vistest.library import words
from vistest.library.errors import BaselineMissing, ScreenshotMismatch

from .test_engine_v2_describe import _page, _words

WIDTH = 120
MACHINERY = ("engine v2", "rule", "region")


@pytest.fixture
def ctx(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    context = _context.LibraryContext(root=tmp_path, platform_override="p")
    _context.install(context)
    yield context
    _context.uninstall()


def _png(img: np.ndarray) -> bytes:
    return pngio.encode(img)


def _button(fill=(52, 120, 246)) -> np.ndarray:
    img = _page()
    img[40:80, 40:160] = fill
    return img


def _fails(ctx, before, after, name="card.png", **kw) -> str:
    ctx.update = True
    expect_screenshot(_png(before), name)
    ctx.update = False
    with pytest.raises(ScreenshotMismatch) as e:
        expect_screenshot(_png(after), name, **kw)
    return str(e.value)


def _rows(ctx) -> list[dict]:
    rows = [json.loads(p.read_text("utf-8")) for p in ctx.parts_dir.glob("*.json")]
    return sorted(rows, key=lambda r: r.get("written_at", 0))


def _long(text: str, root: Path) -> list[str]:
    """The lines over WIDTH that are not a path."""
    return [line for line in text.splitlines()
            if len(line) > WIDTH and str(root) not in line]


def _not_paths(text: str, root: Path) -> str:
    return "\n".join(line for line in text.splitlines() if str(root) not in line)


# --------------------------------------------------------------------------- #
#  4.3 — the numbers the verdict was taken on
# --------------------------------------------------------------------------- #
def test_the_threshold_line_is_short_and_has_no_word_of_the_machinery(ctx, tmp_path):
    message = _fails(ctx, _button(), _button((154, 160, 170)))
    line = message.splitlines()[1]
    assert re.fullmatch(r"  1 change not explained as rendering noise · worst \d+/100 · "
                        r"no threshold set, so any change fails · \d+\.\d\d% of the frame",
                        line), line
    for word in MACHINERY:
        assert word not in _not_paths(message, tmp_path), (word, message)


def test_a_threshold_a_person_set_is_named_with_where_it_came_from(ctx):
    message = _fails(ctx, _button(), _button((154, 160, 170)), fail_severity=10)
    assert re.search(r"1 change at or above the threshold 10 \(set in the call\) · "
                     r"worst [\d.]+/100 · [\d.]+% of the frame \(limit 0\.15%, the default\)",
                     " ".join(message.split())), message


# --------------------------------------------------------------------------- #
#  4.4 — the reason, the second look and a hint: a line each
# --------------------------------------------------------------------------- #
def test_notes_are_lines_of_their_own_under_their_labels(tmp_path):
    a, b = _button(), _button((154, 160, 170))
    from vistest.core import compare

    r = compare(a, b, engine="v2")
    text = str(ScreenshotMismatch.build(
        name="card.png", platform="", result=r, reason=words.reason(r),
        notes=[("capture", "the window was at scroll (0, 646); set to (0, 991)"),
               ("second look", words.look("Confirmed on a second capture: the frames of "
                                          "this run are identical, so the difference "
                                          "from the baseline is real, not motion.")),
               ("hint", '#counter changes by itself: mask it — '
                        'expect_screenshot(..., mask=["#counter"])')],
        baseline=tmp_path / "b.png", actual=tmp_path / "a.png", diff=None, report=None,
        limits={"engine": "v2"}))
    lines = text.splitlines()
    assert f"  reason:      {words.reason(r)}" in lines, text
    reason = next(x for x in lines if x.startswith("  reason:"))
    assert "mask" not in reason and "second" not in reason
    assert any(x.startswith("  second look: the second picture is the same as the first")
               for x in lines), text
    hint = next(x for x in lines if x.startswith("  hint:"))
    assert 'mask=["#counter"]' in hint, text


@pytest.fixture(scope="module")
def chromium():
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        manager = sync_api.sync_playwright().start()
    except Exception as e:  # pragma: no cover - depends on the machine
        pytest.skip(f"Playwright could not start: {e}")
    try:
        browser = manager.chromium.launch()
    except Exception as e:  # pragma: no cover - depends on the machine
        manager.stop()
        pytest.skip(f"no Chromium to run against: {str(e).splitlines()[0]}")
    yield browser
    browser.close()
    manager.stop()


@pytest.fixture
def page(chromium):
    context = chromium.new_context(viewport={"width": 400, "height": 240})
    page = context.new_page()
    yield page
    context.close()


COUNTER = ('<body style="background:#fff;font:20px sans-serif"><p>Orders</p>'
           '<b id="counter">0</b><script>let n = 0; setInterval(() => '
           '{ document.getElementById("counter").textContent = ++n; }, 150)</script></body>')


def test_the_mask_hint_of_a_page_that_keeps_changing_is_a_line_of_its_own(ctx, page,
                                                                          tmp_path):
    from dataclasses import replace

    ctx._config = replace(ctx.config, capture=replace(
        ctx.config.capture, stable_timeout_ms=700, ready_timeout_ms=700))
    ctx.platform_override = ""
    page.set_content(COUNTER)
    ctx.update = True
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        expect_screenshot(page, "counter.png")
    ctx.update = False
    page.wait_for_timeout(400)
    with pytest.raises(ScreenshotMismatch) as e:
        expect_screenshot(page, "counter.png")
    text = str(e.value)
    lines = text.splitlines()
    reason = next(x for x in lines if x.startswith("  reason:"))
    assert "mask" not in reason and "second" not in reason.lower(), text
    assert any(x.startswith("  second look: ") for x in lines), text
    hint = [i for i, x in enumerate(lines) if x.startswith("  hint:")]
    assert hint, text
    #  The hint with the mask, from its own line on (wrapped, it goes on below).
    tail = " ".join(x.strip() for x in lines[hint[0]:hint[0] + 3])
    assert 'mask=["#counter"]' in tail, text
    assert not _long(text, tmp_path), _long(text, tmp_path)
    row = _rows(ctx)[-1]
    assert [label for label, _ in row["lines"]][:1] == ["reason"]
    assert "hint" in [label for label, _ in row["lines"]]


# --------------------------------------------------------------------------- #
#  4.5 — the kind is the sentence's
# --------------------------------------------------------------------------- #
def test_the_report_and_the_diff_name_a_change_by_its_sentence(ctx, monkeypatch):
    from vistest.render import artifacts

    said = []
    monkeypatch.setattr(artifacts, "_label", lambda img, text, *a, **k: said.append(text))
    _fails(ctx, _words(_page()), _words(_page(), heavy=range(8)), name="words.png")
    row = _rows(ctx)[-1]
    old = {r["kind"] for r in row["regions"]}                    # the old classifier's
    assert old and "redrawn" not in old
    from vistest.report.library import _regions_table

    html = _regions_table(row)
    assert "<td>redrawn</td>" in html
    assert not any(f"<td>{k}</td>" in html for k in old), html
    assert said and all(t.startswith("REDRAWN ") for t in said), said
    assert row["reason"].startswith("redrawn in 8; worst "), row["reason"]


# --------------------------------------------------------------------------- #
#  4.6, 4.7 — one sentence, everywhere; no «letters», no «ink»
# --------------------------------------------------------------------------- #
CORNER = ('<body style="background:#fff"><div style="margin:40px;width:200px;height:80px;'
          'border:1px solid #c7ced8;background:#fff;border-radius:{r}"></div></body>')


def test_a_corner_rounded_8_to_10_px_is_said_once_the_same_way_everywhere(ctx, page,
                                                                          tmp_path):
    ctx.platform_override = ""
    page.set_content(CORNER.format(r="8px"))
    ctx.update = True
    expect_screenshot(page, "corner.png")
    ctx.update = False
    page.set_content(CORNER.format(r="10px"))
    with pytest.raises(ScreenshotMismatch) as e:
        expect_screenshot(page, "corner.png")
    message = str(e.value)
    failed = _rows(ctx)[-1]
    expect_screenshot(page, "corner.png", fail_severity=100)
    passed = _rows(ctx)[-1]

    sentence = "edges redrawn within 1 px, same color (#c7ced8): the outline changed slightly"
    assert sentence in message
    assert {words.sentence(r) for r in failed["regions"]} == {sentence}
    assert sentence in passed["reason"] and passed["verdict"] == "pass"

    from vistest.bench import layouts, measure

    out = tmp_path / "bench"
    found = layouts.discover(None, expected=str(next(ctx.baselines.rglob("corner.png"))
                                               .parent),
                             actual=str(next((tmp_path / ".vistest" / "actual")
                                             .rglob("corner.png")).parent))
    (said,) = [r.said for r in measure(found, out, ai=False)]
    assert all(s.endswith(f": {sentence}") for s in said), said
    for text in (message, passed["reason"], *said):
        for word in ("letters", "ink", "moved by less than"):
            assert word not in text, (word, text)


def test_a_shape_is_said_as_a_share_and_the_counts_stay_in_the_report(ctx):
    a = _words(_page())
    b = a.copy()
    b[30:42, 44:48] = (51, 51, 51)                  # a stem became a bar
    b[36:38, 40:52] = (51, 51, 51)
    message = _fails(ctx, a, b, name="shape.png")
    assert "shape changed: 23% of the new strokes are not where the old ones were" in message
    assert "of the new ink" not in message and " px of the new" not in message
    from vistest.report.library import _regions_table

    html = _regions_table(_rows(ctx)[-1])
    assert "26 of 114 px of the new strokes and 0 of 72 of the old lie farther than 1 px" \
        in html


# --------------------------------------------------------------------------- #
#  4.8 — ΔE once with its scale; SSIM and the canary in the details
# --------------------------------------------------------------------------- #
def test_the_color_difference_is_said_once_with_its_scale(ctx, tmp_path):
    message = _fails(ctx, _button(), _button((154, 160, 170)))
    text = _not_paths(message, tmp_path)
    assert text.count("ΔE") == 1, text
    assert "color difference 22.9" in text
    assert "(color difference is ΔE00 (CIEDE2000): 1 ≈ barely visible" in text


def test_ssim_and_the_canary_are_in_the_details_not_in_the_summary():
    from vistest.report.library import _row

    html = _row({"name": "a.png", "verdict": "fail", "reason": "x",
                 "metrics": {"max_severity": 40.0, "ssim_global": 0.9998, "de_mean": 0.2},
                 "limits": {"engine": "v2"}, "capture": {"canary_ms": 317}}, [10 ** 9])
    summary = html.split("</summary>", 1)[0]
    assert "SSIM" not in summary and "canary" not in summary, summary
    details = html.split('<details class="more">', 1)[1]
    assert "SSIM 0.9998" in details and "canary drawn in 317 ms" in details


def test_a_picture_not_from_a_page_has_no_renderer_line(ctx):
    message = _fails(ctx, _button(), _button((154, 160, 170)))
    assert "\n  renderer:" not in message
    #  The report row keeps what the check knew: no canary on either side.
    assert _rows(ctx)[-1]["renderer"]["status"] == "unknown"


def _cli(args, cwd, **env) -> subprocess.CompletedProcess:
    clean = {k: v for k, v in os.environ.items() if not k.startswith("VISTEST_")}
    return subprocess.run([sys.executable, "-m", "vistest.cli", *args], cwd=cwd,
                          capture_output=True, env={**clean, "PYTHONIOENCODING": "utf-8",
                                                    **env}, timeout=300)


@pytest.fixture
def two(tmp_path):
    (tmp_path / "a.png").write_bytes(_png(_button()))
    (tmp_path / "b.png").write_bytes(_png(_button((154, 160, 170))))
    return tmp_path


def test_vistest_compare_says_the_message_words_without_a_renderer(two):
    done = _cli(["compare", "a.png", "b.png", "-o", "out"], two)
    out = done.stdout.decode("utf-8")
    assert done.returncode == 1, out + done.stderr.decode()
    assert "renderer" not in out and "SSIM" not in out and "region" not in out, out
    assert "  reason:      fill in 1; worst 120x40 at (40, 40)" in out, out
    assert ": fill: #3478f6 → #9aa0aa, color difference 22.9" in out, out
    assert out.count("ΔE") == 1, out
    assert not _long(out, two), out


# --------------------------------------------------------------------------- #
#  4.9 — color, as in CSS
# --------------------------------------------------------------------------- #
def test_color_is_spelled_as_in_css_wherever_a_person_reads_it(ctx, two):
    message = _fails(ctx, _button(), _button((154, 160, 170)))
    from vistest.report.library import build

    build(ctx.parts_dir, ctx.report)
    html = Path(ctx.report).read_text("utf-8")
    compared = _cli(["compare", "a.png", "b.png", "-o", "out"], two).stdout.decode()
    for side, name in (("e", "a.png"), ("f", "b.png")):
        (two / side).mkdir()
        (two / side / "card.png").write_bytes((two / name).read_bytes())
    benched = _cli(["bench", "--expected", "e", "--actual", "f", "--out", "bout",
                    "--no-playwright", "--no-ai"], two)
    assert benched.returncode == 0, benched.stderr.decode()
    for text in (message, html, compared, benched.stdout.decode()):
        assert "colour" not in text.lower(), text


# --------------------------------------------------------------------------- #
#  120 characters, but a path
# --------------------------------------------------------------------------- #
def test_no_line_of_a_message_is_longer_than_120_characters_but_a_path(ctx, tmp_path):
    said = [_fails(ctx, _button(), _button((154, 160, 170))),
            _fails(ctx, _button(), _button((154, 160, 170)), name="t.png",
                   fail_severity=10, max_changed_area_pct=0.01)]
    a = _words(_page())
    b = a.copy()
    b[30:42, 44:48] = (51, 51, 51)
    said.append(_fails(ctx, a, b, name="shape.png"))
    many = _page()
    for i in range(6):
        many[20 + 15 * i:21 + 15 * i, 30:150] = (209, 213, 219)
    said.append(_fails(ctx, _page(), many, name="lines.png"))
    said.append(str(BaselineMissing.build(
        name="x.png", platform="linux-chromium-1x-1280x720", baseline=tmp_path / "x.png",
        actual=tmp_path / "a.png", elsewhere=["linux-chromium-1x-800x600", "",
                                              "windows-chromium-1x-1280x720"])))
    for text in said:
        assert not _long(text, tmp_path), _long(text, tmp_path)


def test_the_wrap_keeps_brackets_whole():
    text = ('#counter changes by itself, and no request or picture was on its way: if it '
            'is meant to, mask it — expect_screenshot(..., mask=["#counter"]) — or mark it '
            'data-vistest="ignore"')
    lines = words.labelled("hint", text)
    assert all(len(x) <= WIDTH for x in lines)
    assert any('expect_screenshot(..., mask=["#counter"])' in x for x in lines), lines


# --------------------------------------------------------------------------- #
#  The words behind them
# --------------------------------------------------------------------------- #
def test_what_was_set_aside_is_said_without_the_engine_s_file_names():
    r = {"x": 176, "y": 101, "w": 3, "h": 3, "kind": "noise",
         "suppressed_by": "min-size: 2 px < 4 px (v2.min_region_px) (was text)"}
    assert words.suppressed(r) == "3x3 at (176, 101): too small to count (2 px, under 4)"
    r["suppressed_by"] = ("rerender-text: (a) ink #111111 → #111111, ΔE00 0.4; … "
                          "(was text)")
    assert words.suppressed(r) == ("3x3 at (176, 101): the same text drawn again by another "
                                   "renderer — (a) ink #111111 → #111111, ΔE00 0.4; …")
    assert words.suppressed_detail(r) == "(a) ink #111111 → #111111, ΔE00 0.4; …"


def test_the_second_look_is_said_without_its_label_twice():
    from vistest.core.retry import second_look
    from vistest.models import CompareResult, Verdict

    first = CompareResult(name="x", verdict=Verdict.FAIL)
    frame = np.zeros((4, 4, 3), np.uint8)
    look = second_look(first, frame, lambda: frame.copy(), lambda f, m: first)
    (note,) = look.result.notes
    assert words.look(note) == ("the second picture is the same as the first, so the "
                                "change is real, not motion")
    first = CompareResult(name="x", verdict=Verdict.FAIL)
    second_look(first, frame, lambda: frame, lambda f, m: first, ready=False)
    assert words.look(first.notes[-1]).startswith("none: the page was not ready")
    assert words.look("something else entirely") == "something else entirely"


