# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""A region that counts, in words: what was measured on it, and nothing else.

«recolored: #1f2937 → #4d5666, color difference 14.8», «line added: 57×1 px,
#2563eb», «fill: #2563eb → #4d77ff, color difference 8.0», «block moved by
+1 px along y» (the first regions of table/text_color/de15, underline/on,
fill/de8 and offset/plus1px of the browser corpus). One short sentence per
region that no rule explained — the one the failure message, the report, the
pass reason and `vistest bench` all say (library/words.py) — so that a person
reads what changed before opening the diff, and can check it against the diff.
The exact counts behind a sentence go into `detail`, which the report shows.

Each sentence is the reading of a measurement the engine already makes
(`rerender.py`), taken in this order, and the first that holds is said:

1. **a block that moved** — B in the region is A moved by whole pixels, and
   A is B moved back (`rerender.block_shift` both ways: the closest move
   within 4 px leaves at most `shift_residual` of the changed pixels, and
   the move back is the same move): «block moved by +1 px along y». Asked
   one way only, a line taken off a plain paper is «moved» too — the paper
   beside it is what the new frame shows there;
2. **a line added or removed** — the changed pixels fill a box at most
   `LINE_MAX_PX` thick and `LINE_MIN_LEN_PX` long, and the strokes are in
   one frame only: «line added: 57×1 px, #2563eb», the color the line has in
   the frame it is in;
3. **a fill** — the most frequent color of the region's neighbourhood (its
   paper, `rerender.background_unchanged`) changed discernibly:
   «fill: #2563eb → #4d77ff, color difference 8.0»;
4. **the strokes** (`rerender.ink_colour`, `rerender.shape_within`) — the same
   shapes within a pixel in a new color: «recolored: #1f2937 → #4d5666, color
   difference 14.8»; the same shapes in a color under `ink_limit` away:
   «edges redrawn within 1 px, same color (#c7ced8): the outline changed
   slightly» (a corner rounded 8 → 10 px), and with both colors and two
   decimals when they differ, so that 1.97 is not read as 2: «edges redrawn
   within 1 px, #1f2937 → #252f3e, color difference 1.97: the outline changed
   slightly» (table/text_color/de2); other shapes: «shape changed: 62% of the
   new strokes are not where the old ones were» (table/word_swap/one), the
   larger of the two shares, with the color when it changed too.

The color difference is CIEDE2000 (ΔE00); the scale is said once where the
sentences are read, not in each of them.

Something is always said: a region whose paper did not change has strokes in
at least one frame — the pixels that differ from the paper are what changed.

«Strokes» and not «text» or «letters»: what is measured is the marks on the
paper — a glyph, an icon, a border — and nothing here knows which. A
description is not a verdict: it changes no region's fate, and the engine
writes it only for the regions that count.
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


#: The categories, in the order they are asked; the report's «kind» column.
WHATS = ("moved", "line", "fill", "color", "redrawn", "shape")
#: Under this ΔE00 two colors are said as the same one (the hex may still
#: differ in its last digit).
SAME_COLOR = 0.5


@dataclass(frozen=True)
class Description:
    what: str       # one of WHATS
    text: str
    #  The exact counts behind `text`, for the report; '' when `text` has them.
    detail: str = ""

    def annotation(self, source: str) -> dict:
        out = {"text": self.text, "kind": KIND, "value": self.what, "source": source}
        if self.detail:
            out["detail"] = self.detail
        return out


def _change(a: str, b: str, delta_e: float, digits: int = 1) -> str:
    """«#2563eb → #1d4ed8, color difference 7.1» — the scale is said by the reader."""
    return f"{a} → {b}, color difference {delta_e:.{digits}f}"


def _share(outside: int, total: int) -> str:
    """«62%»; «under 1%» for a share that rounds to nothing but is not."""
    pct = 100.0 * outside / max(total, 1)
    return "under 1%" if 0 < pct < 0.5 else f"{pct:.0f}%"


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
        return Description("moved", forward.moved()[len("the "):])
    found = line(c)
    if found is not None:
        return found
    paper = _rr.background_unchanged(c)
    if paper.delta_e >= jnd:
        return Description("fill", "fill: " + _change(_rr._hex(c.paper_a), _rr._hex(c.paper_b),
                                                      paper.delta_e))
    ink = _rr.ink_colour(c)
    shape = _rr.shape_within(c)
    a, b = _rr._hex(ink.ink_a), _rr._hex(ink.ink_b)
    within = f"{_rr.SHAPE_TOLERANCE_PX} px"
    if shape.holds():
        if ink.delta_e >= ink_limit:
            return Description("color", "recolored: " + _change(a, b, ink.delta_e))
        #  A corner rounded 8 → 10 px, a stroke a shade heavier: on the
        #  renderer that drew the baseline nothing explains it away, so it
        #  counts — and the sentence must not read as «noise, accept it».
        same = ink.delta_e < SAME_COLOR or a == b
        colour = f"same color ({a})" if same else _change(a, b, ink.delta_e, 2)
        return Description(
            "redrawn", f"edges redrawn within {within}, {colour}: the outline changed slightly",
            detail=(f"every edge lies within {within} of the other picture's, and the "
                    f"strokes are within ΔE00 {ink.delta_e:.2f} of their old color (a new "
                    f"color starts at {ink_limit:g}); only a different renderer would "
                    "explain that away"))
    if shape.ink_a == 0:
        text = "shape changed: new strokes where there were none"
    elif shape.ink_b == 0:
        text = "shape changed: the strokes are gone"
    elif shape.outside_b * shape.ink_a >= shape.outside_a * shape.ink_b:
        text = (f"shape changed: {_share(shape.outside_b, shape.ink_b)} of the new strokes "
                "are not where the old ones were")
    else:
        text = (f"shape changed: {_share(shape.outside_a, shape.ink_a)} of the old strokes "
                "are not where the new ones are")
    detail = (f"{shape.outside_b} of {shape.ink_b} px of the new strokes and "
              f"{shape.outside_a} of {shape.ink_a} of the old lie farther than {within} "
              "from the other")
    if ink.delta_e >= ink_limit:
        text += "; color " + _change(a, b, ink.delta_e)
        detail += f"; ΔE00 {ink.delta_e:.2f} between their colors"
    return Description("shape", text, detail=detail)
