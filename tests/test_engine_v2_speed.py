# VisTest - visual regression testing for Python and Playwright.
# Copyright 2026 Kirill Kulagin
# SPDX-License-Identifier: Apache-2.0
#
# This file is part of VisTest, licensed under the Apache License, Version 2.0.
# You may not use it except in compliance with the License. See LICENSE for
# the terms and NOTICE for attribution and the trademark.

"""v2 is faster and says the same (plan, v8).

`rerender.block_shift` searched every whole-pixel move with ΔE00 on every
pixel. It now bounds each move from below by what lightness alone proves
apart, and computes ΔE00 for the moves in the order of that bound, stopping
when none can beat the best. The search it replaced is kept here, word for
word, as the reference: on frames where moves compete — a block moved, text
redrawn, noise, a frame edge in reach — both name the same move with the
same number of pixels left, in both modes.
"""

from __future__ import annotations

import numpy as np
import pytest

from vistest.core import color as _color
from vistest.core.v2 import rerender as rr


def _reference(exp, act, lab_exp, lab_act, where, *, jnd, limit,
               reach_px=rr.SHIFT_REACH_PX, pure_only=False):
    """`block_shift` as it was before v8."""
    ys, xs = where if isinstance(where, tuple) else np.nonzero(where)
    n = len(ys)
    H, W = exp.shape[:2]
    best = (0, 0, n + 1)
    allowed = limit * n if pure_only else float("inf")
    moves = sorted(((dx, dy) for dy in range(-reach_px, reach_px + 1)
                    for dx in range(-reach_px, reach_px + 1) if dx or dy),
                   key=lambda m: (abs(m[0]) + abs(m[1]), abs(m[1]), m[1], m[0]))
    for dx, dy in moves:
        sy, sx = ys - dy, xs - dx
        inside = (sy >= 0) & (sy < H) & (sx >= 0) & (sx < W)
        left = int((~inside).sum())
        if left >= best[2] or left > allowed:
            continue
        py, px, qy, qx = ys[inside], xs[inside], sy[inside], sx[inside]
        if pure_only:
            sure = rr._surely_apart(lab_act[py, px, 0], lab_exp[qy, qx, 0], jnd)
            left += int(sure.sum())
            if left > allowed or left >= best[2]:
                continue
            py, px, qy, qx = py[~sure], px[~sure], qy[~sure], qx[~sure]
        differ = np.any(act[py, px] != exp[qy, qx], axis=1)
        if differ.any():
            de = _color.delta_e_ciede2000(lab_act[py[differ], px[differ]][None],
                                          lab_exp[qy[differ], qx[differ]][None])[0]
            left += int((de > jnd).sum())
        if left < best[2] and left <= allowed:
            best = (dx, dy, left)
            if not left:
                break
    dx, dy, left = best
    if best[2] > n:
        return rr.BlockShift(dx=0, dy=0, left=n, changed=n, limit=limit)
    return rr.BlockShift(dx=dx, dy=dy, left=min(left, n), changed=n, limit=limit)


def _frame(rng, kind):
    """A pair of small frames and the changed pixels between them."""
    H, W = 40, 56
    paper = rng.integers(200, 256, 3)
    exp = np.empty((H, W, 3), np.uint8)
    exp[:] = paper
    for _ in range(rng.integers(3, 9)):            # «glyphs»: dark blobs
        y, x = rng.integers(0, H - 6), rng.integers(0, W - 6)
        h, w = rng.integers(2, 7), rng.integers(1, 7)
        exp[y:y + h, x:x + w] = rng.integers(0, 120, 3)
    act = exp.copy()
    if kind == "moved":
        dx, dy = rng.integers(-3, 4), rng.integers(-3, 4)
        act = np.roll(np.roll(exp, dy, axis=0), dx, axis=1)
    elif kind == "redrawn":
        noise = rng.integers(-30, 31, exp.shape)
        act = np.clip(exp.astype(int) + noise * (exp < 150), 0, 255).astype(np.uint8)
    elif kind == "faint":
        act = np.clip(exp.astype(int) + rng.integers(-2, 3, exp.shape), 0, 255).astype(np.uint8)
    else:                                          # «edge»: a block at the frame's edge moved
        act[:, :6] = np.roll(exp[:, :6], 2, axis=0)
    lab_e = _color.srgb_to_lab(exp)
    lab_a = _color.srgb_to_lab(act)
    de = _color.delta_e_ciede2000(lab_e, lab_a)
    where = de > 1.0
    if not where.any():
        where[rng.integers(0, H), rng.integers(0, W)] = True
    return exp, act, lab_e, lab_a, where


@pytest.mark.parametrize("kind", ["moved", "redrawn", "faint", "edge"])
@pytest.mark.parametrize("pure_only", [False, True])
def test_the_search_names_what_the_old_one_named(kind, pure_only):
    rng = np.random.default_rng(20261013)
    for _ in range(40):
        exp, act, lab_e, lab_a, where = _frame(rng, kind)
        for limit in (0.02, 0.2):
            for w in (where, np.nonzero(where)):
                new = rr.block_shift(exp, act, lab_e, lab_a, w, jnd=1.0, limit=limit,
                                     pure_only=pure_only)
                old = _reference(exp, act, lab_e, lab_a, w, jnd=1.0, limit=limit,
                                 pure_only=pure_only)
                assert new == old, (kind, pure_only, limit)


def test_the_paper_of_a_crop_is_read_once():
    """The papers are cached on the crop: the same array on every read."""
    rng = np.random.default_rng(1)
    exp, act, _, _, where = _frame(rng, "redrawn")
    labels = where.astype(np.int32)
    c = rr.crop(exp, act, labels, 1, (0, 0, exp.shape[1], exp.shape[0]), group_px=2)
    assert c.paper_a is c.paper_a and c.paper_b is c.paper_b
    assert np.array_equal(c.paper_a, rr.paper(c.a))
