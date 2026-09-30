# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""A region that counts, in words: what was measured on it, and nothing else.

«ink colour: #1f2937 → #4d5666, ΔE00 14.8», «line added: 57×1 px, #2563eb»,
«fill: #2563eb → #4d77ff, ΔE00 8.0», «block moved by +1 px along y» (the
first regions of table/text_color/de15, underline/on, fill/de8 and
offset/plus1px of the browser corpus). One sentence per region that no
rule explained, for the report and for the failure message — so that a
person reads what changed before opening the diff, and can check it
against the diff.

Each sentence is the reading of a measurement the engine already makes
(`rerender.py`), taken in this order, and the first that holds is said:

1. **a block that moved** — B in the region is A moved by whole pixels, and
   A is B moved back (`rerender.block_shift` both ways: the closest move
   within 4 px leaves at most `shift_residual` of the changed pixels, and
   the move back is the same move): «block moved by +1 px along y». Asked
   one way only, a line taken off a plain paper is «moved» too — the paper
   beside it is what the new frame shows there;
2. **a line added or removed** — the changed pixels fill a box at most
   `LINE_MAX_PX` thick and `LINE_MIN_LEN_PX` long, and the ink is in one
   frame only: «line added: 57×1 px, #2563eb», the colour the line has in
   the frame it is in;
3. **a fill** — the most frequent colour of the region's neighbourhood (its
   paper, `rerender.background_unchanged`) changed discernibly:
   «fill: #2563eb → #4d77ff, ΔE00 8.0»;
4. **the ink** (`rerender.ink_colour`, `rerender.shape_within`) — the same
   shapes to a pixel in a new colour: «ink colour: #1f2937 → #4d5666,
   ΔE00 14.8»; the same shapes in a colour under `ink_limit` away: «strokes
   redrawn within 1 px; ink #1f2937 → #252f3e, ΔE00 1.97 (below 2)» (table/
   text_color/de2) — two decimals, so that 1.97 is not read as 2; other
   shapes: «ink shape changed: 17 of 607 px of the new ink and 34 of 629 of
   the old lie farther than 1 px from the other» (table/word_swap/one),
   with the colour when it changed too.

Something is always said: a region whose paper did not change has ink in at
least one frame — the pixels that differ from the paper are what changed.

«Ink» and not «text»: what is measured is the strokes on the paper — a
glyph, an icon, a rule — and nothing here knows which. A description is not
a verdict: it changes no region's fate, and the engine writes it only for
the regions that count.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import rerender as _rr

#: A line is at most this thick …
LINE_MAX_PX = 2
#: … at least this long …
LINE_MIN_LEN_PX = 8
#: … and its changed pixels fill at least this share of their box; in the
#: frame without the line they are at most this share as far from the paper
#: as in the frame with it.
LINE_FILL = 0.8
LINE_OTHER_FRAME = 0.25

KIND = "description"


@dataclass(frozen=True)
class Description:
    what: str       # block | line | fill | ink colour | redrawn | ink shape
    text: str

    def annotation(self, source: str) -> dict:
        return {"text": self.text, "kind": KIND, "value": self.what, "source": source}


def _box(where: np.ndarray) -> tuple[int, int, int, int]:
    ys, xs = np.nonzero(where)
    return (int(xs.min()), int(ys.min()), int(xs.max() - xs.min() + 1),
            int(ys.max() - ys.min() + 1))


def line(c: _rr.Crop) -> Description | None:
    """A thin straight run of changed pixels, inked in one frame only."""
    if not c.changed.any():
        return None
    _, _, w, h = _box(c.changed)
    thick, long_ = min(w, h), max(w, h)
    if thick > LINE_MAX_PX or long_ < LINE_MIN_LEN_PX:
        return None
    if int(c.changed.sum()) < LINE_FILL * w * h:
        return None
    da = float(_rr.ink_distance(c.a, c.paper_a)[c.changed].mean())
    db = float(_rr.ink_distance(c.b, c.paper_b)[c.changed].mean())
    added = db > da
    faint, strong = (da, db) if added else (db, da)
    #  «In one frame only»: the other frame shows its paper there, or nearly —
    #  not the half-covered edge of a stroke that grew by a pixel.
    if faint > LINE_OTHER_FRAME * strong:
        return None
    frame = c.b if added else c.a
    colour = np.median(frame[c.changed], axis=0)
    return Description("line", f"line {'added' if added else 'removed'}: {w}×{h} px, "
                               f"{_rr._hex(colour)}")


def moved(forward: _rr.BlockShift, backward: _rr.BlockShift) -> bool:
    """B is A moved by (dx, dy), and A is B moved by (−dx, −dy)."""
    return (not forward.holds() and not backward.holds()
            and (backward.dx, backward.dy) == (-forward.dx, -forward.dy))


def describe(c: _rr.Crop, forward: _rr.BlockShift, backward: _rr.BlockShift, *,
             jnd: float, ink_limit: float) -> Description:
    """The first of the measurements in the module docstring that holds.

    `forward` is `rerender.block_shift` of the region from A to B, `backward`
    from B to A.
    """
    if moved(forward, backward):
        return Description("block", forward.moved()[len("the "):])
    found = line(c)
    if found is not None:
        return found
    paper = _rr.background_unchanged(c)
    if paper.delta_e >= jnd:
        return Description("fill", f"fill: {_rr._hex(c.paper_a)} → {_rr._hex(c.paper_b)}, "
                                   f"ΔE00 {paper.delta_e:.1f}")
    ink = _rr.ink_colour(c)
    shape = _rr.shape_within(c)
    colour = (f"{_rr._hex(ink.ink_a)} → {_rr._hex(ink.ink_b)}, "
              f"ΔE00 {ink.delta_e:.1f}")
    if shape.holds():
        if ink.delta_e >= ink_limit:
            return Description("ink colour", f"ink colour: {colour}")
        return Description("redrawn", f"strokes redrawn within {_rr.SHAPE_TOLERANCE_PX} px; "
                                      f"ink {_rr._hex(ink.ink_a)} → {_rr._hex(ink.ink_b)}, "
                                      f"ΔE00 {ink.delta_e:.2f} (below {ink_limit:g})")
    text = (f"ink shape changed: {shape.outside_b} of {shape.ink_b} px of the new ink "
            f"and {shape.outside_a} of {shape.ink_a} of the old lie farther than "
            f"{_rr.SHAPE_TOLERANCE_PX} px from the other")
    if ink.delta_e >= ink_limit:
        text += f"; ink colour {colour}"
    return Description("ink shape", text)
