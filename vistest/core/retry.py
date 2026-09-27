# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""A second frame after a failure: one function for the server and the library.

It used to live inside `CheckService._retry`, which meant the server had it
and the library did not. Both call this now, and the two can no longer drift.

**The first frame is compared, not the second.** Taking the second would be
choosing the luckier picture until it turns green. The second frame is a
witness, not a replacement: it says which pixels of this page do not hold
still, and exactly those go into the mask for a comparison of the *first*
frame again.

Hence the property that matters: what gets suppressed is specific regions,
never the verdict. A spinner that shivered goes into the mask; a header that
moved stays red, because between the two frames it did not shiver anywhere. A
steady regression cannot be hidden this way — steady means it reproduces.

A **flickering** one can, and that is said rather than left out. If a break
shows every other frame, the second frame may catch the page whole, the region
goes into the mask, and the verdict turns green. Two frames are not enough for
anyone — a person or an engine — to tell flickering noise from a flickering
break. So the green is never silent: «failed on the first capture, passed on
the second» is a note, with the share of the page that moved and the advice to
fix it at the source. For a flickering break that is exactly the right text.

**Nothing vanishes from the result either.** A region of the first comparison
that is absent from the second — because its pixels were among those that
moved — is kept, in `suppressed`, with `suppressed_by="unstable: …"`. Before,
it simply disappeared: the pixels were masked out and the region with them,
and a person reading a green result could not see that anything had been
there. Now the report and the pytest summary count it like every other
suppression.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from ..models import CompareResult, DiffRegion
from . import noise as _noise

__all__ = ["PREFIX", "SecondLook", "second_look"]

#: The head of `suppressed_by` for a region that did not reproduce.
PREFIX = "unstable"


@dataclass
class SecondLook:
    """What the second frame changed.

    `result` is what to report: the first comparison when nothing changed, the
    comparison of the first frame under the wider mask otherwise.
    `unstable` is the mask of pixels that moved between the two frames, or
    `None` when there was no second frame to compare with.
    """

    result: CompareResult
    unstable: np.ndarray | None = None


def second_look(
    first: CompareResult,
    frame: np.ndarray,
    recapture: Callable[[], np.ndarray | None],
    recompare: Callable[[np.ndarray], CompareResult],
) -> SecondLook:
    """Take one more frame, and recompare `frame` with what moved masked out.

    first     — the comparison that failed.
    frame     — the picture it was made from (the first frame).
    recapture — takes one more frame of the same page, as RGB; may return
                None (nothing to take) or raise (the browser is gone). Neither
                touches the verdict already computed.
    recompare — compares `frame` against the same baseline again, with the
                given mask of moving pixels added to whatever it ignored the
                first time. The caller owns the baseline, the thresholds and
                the masks; this function only decides when and what to ask.
    """
    try:
        second = recapture()
    except Exception as e:
        first.notes.append(
            f"Could not take a second capture ({type(e).__name__}: {e}) — "
            "the verdict is from a single frame.")
        return SecondLook(first)
    if second is None:
        return SecondLook(first)

    moved = _noise.stability_mask([frame, second])
    if not moved.any():
        #  Not one pixel shivered between the two frames. That is a strong
        #  statement in favour of the failure, and worth saying aloud: whoever
        #  looks at it next is not wondering whether something blinked.
        first.notes.append(
            "Confirmed on a second capture: nothing on this page moved "
            "between the two frames.")
        return SecondLook(first, moved)

    again = recompare(moved)
    share = float(moved.mean()) * 100
    _keep_what_vanished(first, again, share)

    if again.failed:
        again.notes.append(
            f"A second capture was taken: {share:.1f}% of the page is "
            "unstable and was suppressed, but the difference remains.")
        return SecondLook(again, moved)

    again.notes.append(
        f"Failed on the first capture and passed on the second: "
        f"{share:.1f}% of the page does not hold still. The difference did "
        "not reproduce, so it is noise — but this snapshot is unstable, and "
        "that is worth fixing at the source.")
    return SecondLook(again, moved)


def _keep_what_vanished(first: CompareResult, again: CompareResult,
                        share: float) -> None:
    """Regions of the first comparison with no counterpart in the second.

    A counterpart is any region of the second comparison — reported or
    suppressed — whose box overlaps. What is left had its pixels masked out
    as moving: a live region is moved to `suppressed` as unstable, and a
    region that was already suppressed keeps the reason it had.

    Their pixels are not added back to `suppressed_pixels`: the second
    comparison ignored them, so they are not among its changed pixels, and the
    three-way split of `changed_pixels` still adds up.
    """
    present = [*again.regions, *again.suppressed]
    reason = (f"{PREFIX}: did not reproduce on a second capture "
              f"({share:.1f}% of the page moved between the two frames)")
    for region in first.regions:
        if not any(_overlap(region, other) for other in present):
            kept = copy.copy(region)
            kept.suppressed_by = reason
            again.suppressed.append(kept)
    for region in first.suppressed:
        if not any(_overlap(region, other) for other in present):
            again.suppressed.append(region)


def _overlap(a: DiffRegion, b: DiffRegion) -> bool:
    return (a.x < b.x + b.w and b.x < a.x + a.w
            and a.y < b.y + b.h and b.y < a.y + a.h)
