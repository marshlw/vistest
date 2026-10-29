# VisTest - visual regression testing for Python and Playwright.
# Copyright 2026 Kirill Kulagin
# SPDX-License-Identifier: Apache-2.0
#
# This file is part of VisTest, licensed under the Apache License, Version 2.0.
# You may not use it except in compliance with the License. See LICENSE for
# the terms and NOTICE for attribution and the trademark.

"""What the engine set aside is on the diff picture, muted (rl2).

The message says «also: 35 differences suppressed as rendering noise», and a
person looks at the picture for where. These are drawn there now: a 1 px
dashed neutral grey frame, no fill, the background left dimmed, under the
real regions, at most `SET_ASIDE_DRAWN` of them. Without `set_aside` the
picture is the one drawn before, byte for byte — the old function is kept
below, verbatim, as the reference.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np
import pytest

from vistest import expect_screenshot
from vistest.core import pngio
from vistest.library import context as _context
from vistest.library.errors import ON_THE_DIFF, ScreenshotMismatch, VisTestWarning
from vistest.models import ChangeKind, CompareResult, DiffRegion, Verdict
from vistest.render import artifacts
from vistest.render.artifacts import (
    SET_ASIDE_DRAWN,
    SET_ASIDE_GREY,
    draw_boxes,
    legend_box,
    set_aside_legend,
    set_aside_reason,
    set_aside_word,
)

cv2 = pytest.importorskip("cv2")


# --------------------------------------------------------------------------- #
#  draw_boxes as it was on 17faf29, verbatim but for its name
# --------------------------------------------------------------------------- #
def _old_draw_boxes(base_rgb, regions, *, label: bool = True) -> np.ndarray:
    from vistest.render.artifacts import _label
    from vistest.render.palette import category, region_color, severity_color

    img = base_rgb.copy()
    dim = (img.astype(np.float32) * 0.55 + 255 * 0.45 * 0.25).astype(np.uint8)
    out = dim
    overlay = out.copy()

    for r in regions:
        c = region_color(r)
        cv2.rectangle(overlay, (r.x, r.y), (r.x + r.w, r.y + r.h), c, -1)
    cv2.addWeighted(overlay, 0.18, out, 0.82, 0, out)

    for r in regions:
        c = region_color(r)
        sc = severity_color(r.severity)
        thickness = 2 if r.severity < 50 else 3
        cv2.rectangle(out, (r.x, r.y), (r.x + r.w, r.y + r.h), c, thickness)
        out[r.y:r.y + r.h, r.x:r.x + r.w] = base_rgb[r.y:r.y + r.h, r.x:r.x + r.w]
        cv2.rectangle(out, (r.x, r.y), (r.x + r.w, r.y + r.h), c, thickness)

        if not label:
            continue
        said = category(r)
        text = f"{(said or r.kind.value).upper()} {r.severity:.0f}"
        if not said and r.kind is ChangeKind.MOVED:
            text += f" ({r.moved_dx:+d},{r.moved_dy:+d})"
        _label(out, text, r.x, r.y, sc)

        if r.selector:
            _label(out, r.selector[:52], r.x, r.y + r.h + 20, (60, 60, 60), scale=0.4)

    return out


# --------------------------------------------------------------------------- #
#  Pictures
# --------------------------------------------------------------------------- #
def _light() -> np.ndarray:
    img = np.full((240, 320, 3), 250, np.uint8)
    img[20:60, 20:300] = (30, 90, 200)
    cv2.putText(img, "Checkout", (30, 120), 0, 0.9, (20, 20, 20), 2, cv2.LINE_AA)
    return img


def _dark() -> np.ndarray:
    img = np.full((200, 260, 3), 18, np.uint8)
    img[100:180, 30:230] = (200, 60, 60)
    return img


def _noisy() -> np.ndarray:
    rng = np.random.default_rng(7)
    return rng.integers(0, 256, size=(180, 300, 3), dtype=np.uint8)


def _regions_light():
    return [DiffRegion(20, 20, 280, 40, kind=ChangeKind.COLOR, severity=62, pixel_count=9000),
            DiffRegion(30, 95, 120, 30, kind=ChangeKind.TEXT, severity=30,
                       pixel_count=800, selector="#total")]


def _regions_dark():
    return [DiffRegion(30, 100, 200, 80, kind=ChangeKind.MOVED, severity=80,
                       moved_dx=3, moved_dy=-2, pixel_count=16000)]


def _regions_noisy():
    return [DiffRegion(5, 5, 40, 40, kind=ChangeKind.ADDED, severity=10, pixel_count=100),
            DiffRegion(250, 140, 49, 39, kind=ChangeKind.REMOVED, severity=99,
                       pixel_count=1500),
            DiffRegion(100, 60, 60, 60, kind=ChangeKind.CONTENT, severity=55,
                       pixel_count=2000,
                       annotations=[{"kind": "description", "value": "shape"}])]


PICTURES = [(_light, _regions_light), (_dark, _regions_dark), (_noisy, _regions_noisy)]


def _quiet(x, y, w, h, *, by: str | None = "antialias: 94% reproduced", px: int = 50):
    return DiffRegion(x, y, w, h, kind=ChangeKind.ANTIALIAS, pixel_count=px,
                      suppressed_by=by)


# --------------------------------------------------------------------------- #
#  4.1 Without set_aside: the old picture, byte for byte
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("label", [True, False])
@pytest.mark.parametrize("picture,regions", PICTURES, ids=["light", "dark", "noisy"])
def test_without_set_aside_the_picture_is_the_old_one(picture, regions, label):
    base = picture()
    want = _old_draw_boxes(base, regions(), label=label)
    assert np.array_equal(draw_boxes(base, regions(), label=label), want)
    #  An empty set aside is no set aside.
    assert np.array_equal(draw_boxes(base, regions(), label=label, set_aside=()), want)
    assert np.array_equal(draw_boxes(base, regions(), label=label, set_aside=[]), want)


# --------------------------------------------------------------------------- #
#  4.2 A set-aside region: grey, dashed, the inside left dimmed
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("picture", [_light, _dark, _noisy], ids=["light", "dark", "noisy"])
def test_a_set_aside_region_is_a_dashed_grey_frame_over_the_dimmed_page(picture):
    base = picture()
    region = _quiet(60, 70, 150, 60)
    plain = draw_boxes(base, [])
    drawn = draw_boxes(base, [], set_aside=[region], label=False)

    x0, y0, x1, y1 = region.x, region.y, region.x + region.w, region.y + region.h
    for side in (drawn[y0, x0:x1 + 1], drawn[y1, x0:x1 + 1],
                 drawn[y0:y1 + 1, x0], drawn[y0:y1 + 1, x1]):
        grey = np.all(side == SET_ASIDE_GREY, axis=-1)
        #  Neutral: R = G = B wherever the frame is drawn.
        assert np.all(side[grey][:, 0] == side[grey][:, 1])
        assert np.all(side[grey][:, 1] == side[grey][:, 2])
        #  Dashed: most of the side is frame, and there are gaps in it.
        assert grey.mean() > 0.5, grey.mean()
        assert (~grey).sum() >= 3, "no gaps: the frame is not dashed"
    #  Inside, the dimmed page, not restored to its own brightness.
    assert np.array_equal(drawn[y0 + 1:y1, x0 + 1:x1], plain[y0 + 1:y1, x0 + 1:x1])
    assert not np.array_equal(drawn[y0 + 1:y1, x0 + 1:x1], base[y0 + 1:y1, x0 + 1:x1])
    #  Thin: one pixel, nothing drawn just outside the frame or just inside it.
    assert np.array_equal(drawn[y0 - 1, x0:x1], plain[y0 - 1, x0:x1])
    assert np.array_equal(drawn[y0 + 1, x0 + 1:x1], plain[y0 + 1, x0 + 1:x1])
    #  Nothing else changed but the legend: no caption at the frame.
    lx0, ly0, lx1, ly1 = legend_box(drawn.shape, set_aside_legend([region]))
    outside = np.ones(drawn.shape[:2], bool)
    outside[y0:y1 + 1, x0:x1 + 1] = False
    outside[ly0:ly1, lx0:lx1] = False
    assert np.array_equal(drawn[outside], plain[outside])


def test_the_reason_is_read_from_suppressed_by():
    region = _quiet(0, 0, 4, 4, by="antialias: the baseline moved +0.25,+0.00 px")
    assert set_aside_reason(region) == "antialias"
    assert set_aside_reason(_quiet(0, 0, 4, 4, by="min-size: 1 px < 4 px")) == "min-size"
    #  Below a threshold a person set: no `suppressed_by`.
    assert set_aside_reason(_quiet(0, 0, 4, 4, by=None)) == "below threshold"


# --------------------------------------------------------------------------- #
#  rl2b 4.1 No caption at a frame: above it, the dimmed page
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("label", [True, False])
@pytest.mark.parametrize("picture", [_light, _dark, _noisy], ids=["light", "dark", "noisy"])
def test_no_caption_is_drawn_at_a_set_aside_frame(picture, label):
    base = picture()
    region = _quiet(60, 70, 150, 40, by="page-shift: the page moved by (+0.375, +0.25) px")
    plain = draw_boxes(base, [], label=label)
    drawn = draw_boxes(base, [], set_aside=[region], label=label)
    #  Where the caption stood (up to 18 px above the frame, as wide as it).
    above = (slice(region.y - 20, region.y), slice(region.x, region.x + region.w + 1))
    assert np.array_equal(drawn[above], plain[above])


# --------------------------------------------------------------------------- #
#  4.3 Where it meets a real region, the real one is drawn over it, whole
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("picture,regions", PICTURES, ids=["light", "dark", "noisy"])
def test_a_real_region_is_drawn_over_what_was_set_aside(picture, regions):
    base = picture()
    real = regions()
    plain = draw_boxes(base, real)
    #  Set aside: inside each real region (its caption there too), and across
    #  each one's edge.
    quiet = []
    for r in real:
        quiet.append(_quiet(r.x + 4, r.y + 14, max(4, r.w // 2), max(4, r.h // 2)))
        quiet.append(_quiet(max(0, r.x - 10), max(0, r.y + r.h // 2), r.w, r.h))
    drawn = draw_boxes(base, real, set_aside=quiet)
    assert not np.array_equal(drawn, plain), "nothing set aside was drawn"
    for r in real:
        zone = (slice(r.y, r.y + r.h + 1), slice(r.x, r.x + r.w + 1))
        assert np.array_equal(drawn[zone], plain[zone]), r


# --------------------------------------------------------------------------- #
#  4.4 At most 40, the largest; rl2b 4.2 the legend
# --------------------------------------------------------------------------- #
def test_the_legend_says_the_reasons_in_human_words_most_frequent_first():
    shift = "page-shift: the page moved by (+0.375, +0.25) px"
    small = "min-size: 1 px < 4 px (v2.min_region_px)"
    quiet = [_quiet(0, 0, 4, 4, by=small) for _ in range(2)]
    quiet += [_quiet(0, 0, 4, 4, by=shift) for _ in range(35)]
    assert set_aside_legend(quiet) == "set aside: 35 subpixel shift; 2 too small"


def test_the_legend_words_come_from_one_dictionary():
    words = {"page-shift: x": "subpixel shift", "min-size: x": "too small",
             "rerender: x": "re-render noise", "antialias: x": "antialiasing",
             None: "below threshold", "scrollbar: x": "scrollbar"}
    for by, word in words.items():
        assert set_aside_word(_quiet(0, 0, 4, 4, by=by)) == word, by


def test_the_rules_engine_v2_writes_have_a_word():
    """The legend names what v2 itself writes into `suppressed_by`, not a raw rule name."""
    from vistest.core.v2 import pageshift, rerender

    for rule in (pageshift.RULE, rerender.RULE):
        assert set_aside_word(_quiet(0, 0, 4, 4, by=f"{rule}: x")) != rule, rule


def test_the_legend_names_three_reasons_and_counts_the_rest():
    by = (["page-shift: a"] * 5 + ["min-size: a"] * 4 + ["antialias: a"] * 3
          + ["rerender: a"] * 2 + ["scrollbar: a"])
    quiet = [_quiet(0, 0, 4, 4, by=b) for b in by]
    assert set_aside_legend(quiet) == ("set aside: 5 subpixel shift; 4 too small; "
                                       "3 antialiasing; +3 other")


def test_forty_five_set_aside_draw_forty_and_the_legend_says_so():
    base = np.full((400, 600, 3), 240, np.uint8)
    #  A grid of 45, each bigger than the one before.
    quiet = [_quiet(10 + (i % 9) * 64, 10 + (i // 9) * 64, 40, 40, px=100 + i)
             for i in range(45)]
    drawn = draw_boxes(base, [], set_aside=quiet, label=False)

    framed = [r for r in quiet if np.all(drawn[r.y, r.x] == SET_ASIDE_GREY)]
    assert len(framed) == SET_ASIDE_DRAWN == 40
    #  The five left out are the five smallest.
    assert {r.pixel_count for r in quiet if r not in framed} == {100, 101, 102, 103, 104}
    text = set_aside_legend(quiet)
    assert text == "set aside: 45 antialiasing (40 of 45 drawn)"


def test_forty_set_aside_the_legend_says_nothing_of_drawn():
    quiet = [_quiet(0, 0, 4, 4) for _ in range(40)]
    assert set_aside_legend(quiet) == "set aside: 40 antialiasing"
    assert set_aside_legend([]) == ""


class _Texts:
    """cv2, with every text drawn written down."""

    def __init__(self):
        self.said: list[str] = []

    def putText(self, img, text, *a, **kw):  # noqa: N802 - cv2's name
        self.said.append(text)
        return cv2.putText(img, text, *a, **kw)

    def __getattr__(self, name):
        return getattr(cv2, name)


@pytest.mark.parametrize("label", [True, False])
def test_the_legend_is_the_only_text_and_is_drawn_whatever_label_is(monkeypatch, label):
    base = _light()
    quiet = [_quiet(60, 150, 50, 20, by="page-shift: a"),
             _quiet(150, 150, 50, 20, by="min-size: a")]
    texts = _Texts()
    monkeypatch.setattr(artifacts, "cv2", texts)
    drawn = draw_boxes(base, [], set_aside=quiet, label=label)
    assert texts.said == ["set aside: 1 subpixel shift; 1 too small"]
    #  Neutral: every pixel of the legend is grey.
    x0, y0, x1, y1 = legend_box(drawn.shape, texts.said[0])
    px = drawn[y0:y1, x0:x1].reshape(-1, 3).astype(int)
    assert np.all(px[:, 0] == px[:, 1]) and np.all(px[:, 1] == px[:, 2])
    #  Bottom left, the corner nothing real is in.
    assert x0 < 20 and y1 > drawn.shape[0] - 20


# --------------------------------------------------------------------------- #
#  rl2b 4.3 The legend keeps out of the real regions
# --------------------------------------------------------------------------- #
_BL = DiffRegion(0, 190, 120, 49, kind=ChangeKind.COLOR, severity=70, pixel_count=900)
_BR = DiffRegion(200, 190, 119, 49, kind=ChangeKind.TEXT, severity=40, pixel_count=900)
_TOP = DiffRegion(0, 0, 319, 40, kind=ChangeKind.ADDED, severity=90, pixel_count=900)


@pytest.mark.parametrize("real,corner", [
    ([], "bottom left"),
    ([_BL], "bottom right"),
    ([_BL, _BR], "top left"),
    ([_BL, _BR, _TOP], "bottom left"),
], ids=["free", "bottom-left-taken", "both-bottom-taken", "all-taken"])
def test_the_legend_moves_away_from_a_real_region(real, corner):
    base = _light()
    quiet = [_quiet(140, 80, 40, 30, by="page-shift: a")]
    plain = draw_boxes(base, real)
    drawn = draw_boxes(base, real, set_aside=quiet)
    h, w = base.shape[:2]
    x0, y0, x1, y1 = legend_box(base.shape, set_aside_legend(quiet), real)
    where = {"bottom left": x0 < 20 and y1 > h - 20,
             "bottom right": x1 > w - 20 and y1 > h - 20,
             "top left": x0 < 20 and y0 < 20}
    assert where[corner], (corner, (x0, y0, x1, y1))
    #  Drawn where legend_box says: there the picture changed.
    assert not np.array_equal(drawn[y0:y1, x0:x1], plain[y0:y1, x0:x1])
    #  The real regions read whole, as without anything set aside.
    for r in real:
        zone = (slice(r.y, r.y + r.h + 1), slice(r.x, r.x + r.w + 1))
        assert np.array_equal(drawn[zone], plain[zone]), r


# --------------------------------------------------------------------------- #
#  render_all (vistest compare, the CLI) draws them too
# --------------------------------------------------------------------------- #
def test_render_all_draws_the_suppressed_and_the_below_threshold(tmp_path):
    base = np.full((200, 300, 3), 245, np.uint8)
    act = base.copy()
    act[20:60, 20:120] = (200, 40, 40)
    res = CompareResult(name="page", verdict=Verdict.FAIL,
                        regions=[DiffRegion(20, 20, 100, 40, kind=ChangeKind.COLOR,
                                            severity=70, pixel_count=4000)],
                        suppressed=[_quiet(150, 100, 60, 30)],
                        below_threshold=[_quiet(40, 120, 60, 40, by=None)])
    res.maps = {"expected": base, "aligned_actual": act}
    paths = artifacts.render_all(res, tmp_path)
    boxes = pngio.read(paths["boxes"])
    for r in (*res.suppressed, *res.below_threshold):
        assert np.all(boxes[r.y, r.x] == SET_ASIDE_GREY), r


# --------------------------------------------------------------------------- #
#  4.5 expect_screenshot: the diff file, and the message
# --------------------------------------------------------------------------- #
@pytest.fixture
def ctx(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    context = _context.LibraryContext(root=tmp_path)
    _context.install(context)
    yield context
    _context.uninstall()


def _noisy_pair() -> tuple[np.ndarray, np.ndarray]:
    """A page, and the same page re-sampled 0.4 px (anti-aliasing noise) with one
    real change: a red block that was not there."""
    from tests import corpus as cp

    case = {c.name: c for c in cp.load()}["antialias 0.4px"]
    actual = case.actual.copy()
    actual[40:90, 40:200] = (220, 40, 40)
    return case.expected, actual


def _fail(ctx, expected, actual) -> ScreenshotMismatch:
    ctx.update = True
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", VisTestWarning)
        expect_screenshot(pngio.encode(expected), "page.png")
    ctx.update = False
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", VisTestWarning)
        with pytest.raises(ScreenshotMismatch) as caught:
            expect_screenshot(pngio.encode(actual), "page.png")
    return caught.value


def test_the_diff_file_shows_the_noise_dashed_grey_and_the_message_says_so(ctx):
    error = _fail(ctx, *_noisy_pair())
    result = error.result
    assert result.regions and result.suppressed, "the pair no longer has both"
    diff = pngio.read(error.artifacts["diff"])

    #  The frames of the set-aside: on the left side of each, grey dashes
    #  with gaps between them (a caption may cover a few of them).
    seen = 0
    for r in result.suppressed:
        if r.h < 8:
            continue
        side = diff[r.y:r.y + r.h + 1, r.x]
        grey = np.all(side == SET_ASIDE_GREY, axis=-1)
        if grey.mean() > 0.5 and (~grey).any():
            seen += 1
    assert seen >= 1, "no dashed grey frame where the noise is"

    text = str(error)
    flat = " ".join(text.split())
    assert f"({ON_THE_DIFF})" in flat
    #  On the line that lists what was set aside.
    also = next(ln for ln in text.splitlines() if ln.lstrip().startswith("also:"))
    assert "dashed grey" in also


def test_without_a_diff_picture_the_message_does_not_point_at_one(ctx, monkeypatch):
    def broken(*_a, **_kw):
        raise RuntimeError("drawing failed")

    monkeypatch.setattr(artifacts, "draw_boxes", broken)
    error = _fail(ctx, *_noisy_pair())
    assert error.result.suppressed
    text = " ".join(str(error).split())
    assert "also:" in text
    assert ON_THE_DIFF not in text
    assert not error.artifacts["diff"]


def test_below_threshold_alone_is_pointed_at_on_its_own_line(tmp_path):
    from vistest.library import words

    res = CompareResult(name="page", verdict=Verdict.FAIL,
                        regions=[DiffRegion(0, 0, 10, 10, severity=80, pixel_count=100)],
                        below_threshold=[DiffRegion(20, 20, 10, 10, severity=5,
                                                    pixel_count=40)],
                        threshold={"value": 25, "source": "the call"})
    assert words.below(res), "the result is not one with a «let through» line"
    built = {d: str(ScreenshotMismatch.build(
        name="page", platform="", result=res, reason="test",
        baseline=tmp_path / "b.png", actual=tmp_path / "a.png", diff=d,
        report=None, limits={})) for d in (tmp_path / "d.png", None)}
    with_picture = next(ln for ln in built[tmp_path / "d.png"].splitlines()
                        if "let through:" in ln)
    assert ON_THE_DIFF in " ".join(built[tmp_path / "d.png"].split())
    assert "let through:" in with_picture
    assert ON_THE_DIFF not in built[None]
