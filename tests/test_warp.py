# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""The engine names the shift it drew — on both paths that draw one.

Two stages translate a picture by a fraction of a pixel and then tell somebody
about it: `align` compensates a global shift and writes `compensated shift
dx=… dy=…` into the notes, `refit` re-draws the baseline and writes `the
baseline moved …` into `suppressed_by`. Both went through `cv2.warpAffine`,
which on OpenCV 4.x rounds the offset to a 1/32 px grid before drawing, and
both printed the number they had asked for. Two decimals of a precision that
version does not have, in the one place the engine asks to be believed.

They now share `core/warp.py`. The safety net is `shift_grid()`, which measures
what the backend applies, and `applied_shift()`, which puts a request on that
grid before anything is drawn. The tests below stand a rounding backend in for
the real one and check that both stages report a grid point — because a net
over one path and not the other is the failure mode that produced this file.
"""

from __future__ import annotations

import numpy as np
import pytest

from vistest.core import align, refit, warp

GRID = 1.0 / 32.0


def _blocks(ink=20.0, paper=250.0):
    """Axis-aligned strokes with hard edges: array slices, nothing else."""
    img = np.full((60, 300), paper, np.float32)
    for i in range(8):
        img[10:50, 15 + i * 35:19 + i * 35] = ink
    for row in range(3):
        img[12 + row * 16:14 + row * 16, 10:290] = ink
    return img


def _page(ink=20, paper=250):
    """Something for `align` to find a shift in, in three channels."""
    img = np.full((120, 200, 3), paper, np.uint8)
    for i in range(6):
        img[20:100, 18 + i * 30:22 + i * 30] = ink
    for row in range(3):
        img[24 + row * 28:27 + row * 28, 12:188] = ink
    return img


def _changed(a, b, tol=12.0):
    return np.abs(a - b) > tol


def _moved(img, dx, dy):
    """The file's own bilinear shift: what the engine is asked to recover."""
    h, w = img.shape[:2]
    ix, fx = int(np.floor(dx)), dx - np.floor(dx)
    iy, fy = int(np.floor(dy)), dy - np.floor(dy)

    def at(oy, ox):
        ys = np.clip(np.arange(h) - oy, 0, h - 1)
        xs = np.clip(np.arange(w) - ox, 0, w - 1)
        return img[ys][:, xs].astype(np.float32)

    return ((1 - fy) * ((1 - fx) * at(iy, ix) + fx * at(iy, ix + 1))
            + fy * ((1 - fx) * at(iy + 1, ix) + fx * at(iy + 1, ix + 1)))


@pytest.fixture
def rounding_backend(monkeypatch):
    """`warp.shift` stood in for by one that rounds to 1/32 px, as 4.x did."""
    exact = warp.shift

    def backend(img, dx, dy):
        return exact(img, round(dx / GRID) * GRID, round(dy / GRID) * GRID)

    monkeypatch.setattr(warp, "shift", backend)
    warp.shift_grid.cache_clear()
    try:
        yield GRID
    finally:
        warp.shift_grid.cache_clear()


def _on_grid(value: float) -> bool:
    return abs(value / GRID - round(value / GRID)) < 1e-9


# --------------------------------------------------------------------------- #
#  As it ships
# --------------------------------------------------------------------------- #
def test_the_shift_that_is_printed_is_the_shift_that_was_drawn():
    """`warp.shift` applies the offset it was given, so it can be quoted.

    Measured on a ramp of slope 1: shifting it by d lowers every value by d.
    """
    ramp = np.tile(np.arange(64, dtype=np.float32), (8, 1))
    for d in (0.3, -0.2, 0.0625, 0.51, -0.99):
        applied = float(ramp[4, 30] - warp.shift(ramp, d, 0.0)[4, 30])
        assert abs(applied - d) < 1e-4, (d, applied)
    assert warp.shift_grid() == 0.0
    assert warp.applied_shift(0.3, -0.2) == (0.3, -0.2)


def test_the_edge_is_replicated_not_filled():
    """A black border would be a false difference as wide as the shift."""
    img = np.full((8, 8), 200.0, np.float32)
    img[:, 4:] = 40.0
    out = warp.shift(img, 1.0, 0.0)
    assert out[0, 0] == pytest.approx(200.0)
    out = warp.shift(img, -1.0, 0.0)
    assert out[0, -1] == pytest.approx(40.0)


@pytest.mark.parametrize("value, expected", [
    (0.4, 0), (0.5, 0), (1.5, 2), (2.5, 2), (254.5, 254), (255.6, 255),
    (-3.0, 0), (300.0, 255),
])
def test_eight_bit_levels_are_rounded_half_to_even(value, expected):
    """One rule for the whole cascade, and ties do not drift upwards."""
    out = warp.to_uint8(np.full((2, 2), value, np.float32))
    assert out.dtype == np.uint8
    assert int(out[0, 0]) == expected


# --------------------------------------------------------------------------- #
#  With a backend that rounds — the net over both paths
# --------------------------------------------------------------------------- #
def test_the_grid_is_measured_not_assumed(rounding_backend):
    assert warp.shift_grid() == pytest.approx(rounding_backend)
    #  +0.30 is 9.6 steps of 1/32 and −0.20 is −6.4 of them: the request lands
    #  between two points the backend can draw, and is put on the nearer one.
    assert warp.applied_shift(0.3, -0.2) == (pytest.approx(10 / 32),
                                             pytest.approx(-6 / 32))


def test_refit_reports_a_shift_the_backend_can_draw(rounding_backend):
    ref = _blocks()
    f = refit.fit(ref, _moved(ref, 0.3, -0.2), _changed(ref, _moved(ref, 0.3, -0.2)), 12.0)
    assert _on_grid(f.dx) and _on_grid(f.dy), (f.dx, f.dy)
    assert (f.dx, f.dy) != (0.3, -0.2)
    assert f"moved {f.dx:+.2f},{f.dy:+.2f} px" in f.sentence()


def test_align_reports_a_shift_the_backend_can_draw(rounding_backend):
    base = _page()
    shifted = warp.to_uint8(_moved(base, 0.7, 0.4))
    gray_exp = base[:, :, 0].copy()
    gray_act = shifted[:, :, 0].copy()

    _, al = align.align_images(base, shifted, gray_exp, gray_act)

    assert al.applied, al.reason
    assert _on_grid(al.dx) and _on_grid(al.dy), (al.dx, al.dy)
    assert al.reason == f"compensated shift dx={al.dx:+.2f} dy={al.dy:+.2f}"


def test_both_paths_go_through_the_same_door():
    """One stand-in reaches both stages: that is what makes the net one net.

    Not an assertion about the numbers — the two tests above cover those — but
    about the wiring. A stage that kept its own `cv2.warpAffine` would sail
    past `shift_grid` and `applied_shift` without either of them noticing, and
    that is exactly how `core/align.py` stayed wrong for months.
    """
    calls = []
    exact = warp.shift

    def spy(img, dx, dy):
        calls.append((dx, dy))
        return exact(img, dx, dy)

    base = _page()
    shifted = warp.to_uint8(_moved(base, 0.7, 0.4))
    ref = _blocks()
    act = _moved(ref, 0.3, -0.2)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(warp, "shift", spy)
        align.align_images(base, shifted, base[:, :, 0].copy(), shifted[:, :, 0].copy())
        after_align = len(calls)
        refit.fit(ref, act, _changed(ref, act), 12.0)

    assert after_align, "align did not go through warp.shift"
    assert len(calls) > after_align, "refit did not go through warp.shift"
