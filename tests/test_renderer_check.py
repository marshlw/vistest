# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""core/renderer.py: two canaries in, same / changed (N px) / unknown out."""

from __future__ import annotations

import numpy as np
import pytest

from vistest.core import compare, pngio
from vistest.core import renderer as rd


def _canary(v: int = 255, h: int = 6, w: int = 8) -> np.ndarray:
    img = np.full((h, w, 3), v, np.uint8)
    img[2:4, 2:6] = 20
    return img


def test_no_canary_is_unknown_and_says_which_is_missing():
    assert rd.check(None).status == rd.UNKNOWN
    assert rd.check((None, _canary())).why == "no canary for the baseline's"
    assert rd.check((_canary(), None)).why == "no canary for this run's"
    assert rd.check((None, None)).why == "no canary for either"
    assert rd.check(None).line() == "renderer: unknown — no canary was given"


def test_the_same_pixels_are_the_same_renderer():
    c = rd.check((_canary(), _canary()))
    assert c.same and c.pixels is None
    assert c.line() == "renderer: same as the baseline's"


def test_other_pixels_are_another_renderer_counted():
    b = _canary()
    b[0, 0] = (254, 255, 255)
    b[5, 7] = 0
    c = rd.check((_canary(), b))
    assert c.changed and c.pixels == 2
    assert c.line() == "renderer: different from the baseline's (canary: 2 px)"


def test_png_bytes_and_arrays_are_the_same_canary():
    assert rd.check((pngio.encode(_canary()), _canary())).same


def test_another_size_is_another_renderer():
    c = rd.check((_canary(), _canary(h=7)))
    assert c.changed and c.pixels == 7 * 8


def test_anything_else_is_refused():
    with pytest.raises(TypeError, match="renderer="):
        rd.check(_canary())            # one array, not a pair — unpacks wrongly
    with pytest.raises(ValueError, match="H×W×3"):
        rd.check((np.zeros((4, 4)), np.zeros((4, 4))))


def test_v1_ignores_the_renderer():
    a = np.full((40, 60, 3), 255, np.uint8)
    b = a.copy()
    b[10:20, 10:30] = 0
    plain = compare(a, b, engine="v1")
    for renderer in ((a, a), (a, b), None):
        r = compare(a, b, renderer=renderer, engine="v1")
        assert r.verdict == plain.verdict and r.notes == plain.notes
        assert "renderer" not in r.maps


def test_a_canary_nobody_drew_says_so():
    c = rd.RendererCheck(rd.NOT_CHECKED, why="the check passed")
    assert c.line() == "renderer: not checked (the check passed)"
    assert not c.same and not c.changed
