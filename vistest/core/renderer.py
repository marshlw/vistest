# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Did the renderer change between the baseline and this run? Measured, not guessed.

`compare(..., renderer=(baseline_canary, run_canary))` takes the two
fingerprints of the renderer (`vistest.library.canary`): the same fixed page,
drawn once where the baseline was taken and once where the screenshot was.
The answer is one of three, and it is a pixel count, not a model:

* **same** — the two canaries are the same pixels. Whatever differs between
  the screenshots, the renderer did not do it.
* **changed** — they differ, by this many pixels. The renderer draws text
  otherwise; how much of the screenshots' difference that explains is for
  the engine's rules to show.
* **unknown** — a canary is missing (`renderer=None`, an old baseline, a
  picture handed in as bytes). Nothing is assumed either way.

A canary is PNG bytes or an RGB array. Two canaries of different sizes are
a changed renderer; the count is then every pixel of the larger one.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import pngio

__all__ = ["CHANGED", "NOT_CHECKED", "SAME", "UNKNOWN", "RendererCheck", "check"]

SAME, CHANGED, UNKNOWN = "same", "changed", "unknown"
#: Nobody drew the canaries, because nothing depended on them (the library
#: draws them lazily; see vistest/library/fingerprint.py).
NOT_CHECKED = "not_checked"


@dataclass(frozen=True)
class RendererCheck:
    """What the two canaries say."""

    status: str
    #: Pixels in which the canaries differ; `None` unless `changed`.
    pixels: int | None = None
    #: Why the answer is `unknown`, in words; empty otherwise.
    why: str = ""

    @property
    def same(self) -> bool:
        return self.status == SAME

    @property
    def changed(self) -> bool:
        return self.status == CHANGED

    def line(self) -> str:
        """One line for a report: same, different (N px) or unknown."""
        if self.status == SAME:
            return "renderer: same as the baseline's"
        if self.status == CHANGED:
            return f"renderer: different from the baseline's (canary: {self.pixels} px)"
        if self.status == NOT_CHECKED:
            return "renderer: not checked" + (f" ({self.why})" if self.why else "")
        return "renderer: unknown" + (f" — {self.why}" if self.why else "")

    def as_dict(self) -> dict:
        return {"status": self.status, "pixels": self.pixels,
                **({"why": self.why} if self.why else {})}


def _rgb(canary) -> np.ndarray:
    if isinstance(canary, (bytes, bytearray, memoryview)):
        return pngio.decode(bytes(canary), source="a renderer canary")
    arr = np.asarray(canary)
    if arr.ndim != 3 or arr.shape[2] < 3:
        raise ValueError(f"a renderer canary is PNG bytes or an H×W×3 array, "
                         f"not an array of shape {arr.shape}")
    return arr[..., :3]


def check(renderer) -> RendererCheck:
    """`renderer` is `None` or `(baseline canary, run canary)`, either of them `None`."""
    if renderer is None:
        return RendererCheck(UNKNOWN, why="no canary was given")
    try:
        base, run = renderer
    except (TypeError, ValueError):
        raise TypeError("renderer= is None or a pair (baseline canary, run "
                        f"canary), not {type(renderer).__name__}") from None
    if base is None or run is None:
        which = ("the baseline's" if base is None and run is not None
                 else "this run's" if run is None and base is not None else "either")
        return RendererCheck(UNKNOWN, why=f"no canary for {which}")
    a, b = _rgb(base), _rgb(run)
    if a.shape != b.shape:
        return RendererCheck(CHANGED, pixels=int(max(a.shape[0] * a.shape[1],
                                                     b.shape[0] * b.shape[1])))
    n = int(np.any(a != b, axis=2).sum())
    return RendererCheck(SAME) if n == 0 else RendererCheck(CHANGED, pixels=n)
