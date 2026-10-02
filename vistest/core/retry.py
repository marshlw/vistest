# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""A second look after a failure: one function for the server and the library.

It used to live inside `CheckService._retry`, which meant the server had it
and the library did not. Both call this now, and the two can no longer drift.

**The invariant: a change in data that arrived after `load` cannot be
hidden.** The first version of this function took one more frame and masked
whatever differed between the two; the capture-hazard stand (S1) showed what
that does on a page whose data comes late: the first frame shows the spinner,
the second the data, *everything* between them is «unstable», and the check
is green — with a real change in that data as green as the plain page. So:

* **Nothing is masked on a page that was not ready.** When the wait for
  readiness (capture/ready.py) gave up on a step, or the frames never held
  still, the verdict stays the one from the frame that was taken, and the
  note says so (`ready=False`).
* **The verdict is from the last frame.** More frames are taken after the
  failure, until two in a row are identical or the time is up. If the page
  changed after it looked ready and then held still, that later frame is the
  page as it is, and it is what gets compared. The picture a person opens is
  that frame too.
* **A later frame passes only when the page held still.** Two identical
  frames in a row at the end, and nothing live: otherwise a page that
  switches between two states — a carousel, a blinking banner, a flickering
  break — would pass whenever the last frame happened to be the right one.
  It fails, and the note says the matching frame was luck.
* **Nothing is masked as live either.** A pixel that changed in at least two
  of the intervals between consecutive frames is live — a counter, an
  animation that does not stop; one that changed once and held — the spinner
  that became a table — is a one-off transition. Neither is masked: masking
  the live pixels made a check pass by luck (on the stand, a counter one check
  in seven). A live area is named in the note, and the check fails until it
  is masked on purpose (`mask=[...]`).

What is still said rather than hidden: regions of the first comparison that
have no counterpart in the second — because the page changed under them —
are kept in `suppressed` with
`suppressed_by="unstable: …"`, and counted with every other suppression.
And a check that passes on the later frame passes with a note: the page
changed after it looked ready, which is worth fixing at the source.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from ..models import CompareResult, DiffRegion
from . import noise as _noise

__all__ = ["Later", "PREFIX", "SecondLook", "live_mask", "second_look"]

#: The head of `suppressed_by` for a region that did not reproduce.
PREFIX = "unstable"

#: How many intervals a pixel must change in to count as live.
LIVE_INTERVALS = 2


@dataclass
class Later:
    """The frames taken after the one that failed, oldest first.

    `settled` — the last two frames (the first one included) are identical.
    """

    frames: list[np.ndarray] = field(default_factory=list)
    settled: bool = False


@dataclass
class SecondLook:
    """What the second look found.

    `result` is what to report. `unstable` is a mask that was added to the
    comparison — always None now: nothing is masked (kept for the callers
    that persist it). `frame` is the picture the verdict is from: the last
    frame taken, or the first one when no other was taken. `live` — the
    pixels that kept changing, when some did.
    """

    result: CompareResult
    unstable: np.ndarray | None = None
    frame: np.ndarray | None = None
    live: np.ndarray | None = None


def live_mask(frames: list[np.ndarray]) -> np.ndarray:
    """Pixels that changed in at least two intervals, in the last frame's shape."""
    shape = frames[-1].shape[:2]
    counts = np.zeros(shape, dtype=np.uint8)
    for a, b in zip(frames, frames[1:], strict=False):
        changed = _noise.stability_mask([b, a])
        fitted = np.ones(shape, dtype=bool)
        h, w = min(shape[0], changed.shape[0]), min(shape[1], changed.shape[1])
        fitted[:h, :w] = changed[:h, :w]
        counts += fitted
    return counts >= LIVE_INTERVALS


def second_look(
    first: CompareResult,
    frame: np.ndarray,
    recapture: Callable[[], Later | np.ndarray | None],
    recompare: Callable[[np.ndarray, np.ndarray | None], CompareResult],
    *,
    ready: bool | None = True,
    not_ready: str = "",
) -> SecondLook:
    """More frames after a failure, and the verdict from the last of them.

    first     — the comparison that failed.
    frame     — the picture it was made from.
    recapture — the frames taken after it (`Later`), or one frame (an
                array: then nothing can be called live), or None (nothing to
                take); may raise (the browser is gone). Neither touches the
                verdict already computed.
    recompare — compares a frame against the same baseline again; the second
                argument is a mask to add to whatever was ignored the first
                time — None: nothing is added.
    ready     — False when the page was not ready (a readiness step gave up,
                or the frames never held still): nothing is taken and
                nothing is masked, and `not_ready` says why in the note.
    """
    if ready is False:
        first.notes.append(
            "The page was not ready" + (f" ({not_ready})" if not_ready else "")
            + ": the second look masked nothing, and the verdict is from the "
            "frame that was taken.")
        return SecondLook(first, None, frame)
    try:
        later = recapture()
    except Exception as e:
        first.notes.append(
            f"Could not take a second capture ({type(e).__name__}: {e}) — "
            "the verdict is from a single frame.")
        return SecondLook(first, None, frame)
    if later is None:
        return SecondLook(first, None, frame)
    if isinstance(later, np.ndarray):
        later = Later([later], settled=False)
    if not later.frames:
        return SecondLook(first, None, frame)

    seq = [frame, *later.frames]
    last = seq[-1]
    if all(f.shape == frame.shape and np.array_equal(f, frame) for f in later.frames):
        #  Not one pixel changed between the frames. That is a strong
        #  statement in favour of the failure, and worth saying aloud: whoever
        #  looks at it next is not wondering whether something blinked.
        first.notes.append(
            "Confirmed on a second capture: nothing on this page moved "
            "between the frames.")
        return SecondLook(first, np.zeros(frame.shape[:2], dtype=bool), frame)

    live = live_mask(seq) if len(seq) >= 3 else np.zeros(last.shape[:2], dtype=bool)
    changed = _noise.stability_mask([last, frame])
    share = float(changed.mean()) * 100
    again = recompare(last, None)
    _keep_what_vanished(first, again, share)

    if live.any():
        what = (f"{float(live.mean()) * 100:.1f}% of the page kept changing after it "
                "was ready — something on it lives (a counter, an animation that "
                "does not stop). That part was not masked: mask it if it is meant "
                "to change")
    elif later.settled:
        what = (f"the page changed after it looked ready ({share:.1f}% of it) and "
                "then held still")
    else:
        what = (f"the page changed after it looked ready ({share:.1f}% of it) and "
                "did not hold still again within the time")
    if not again.failed and (live.any() or not later.settled):
        #  A later frame that matches the baseline is a pass only when the
        #  page then held still. A page that switches between two states — a
        #  carousel, a blinking banner, a flickering break — would otherwise
        #  pass whenever the last frame happened to be the right one.
        first.notes.append(
            f"A second look was taken: {what}. A later frame matched the "
            "baseline, but the page did not hold still after it, so that is luck, "
            "not a pass: the verdict stays the failure.")
        return SecondLook(first, None, frame, live if live.any() else None)
    if again.failed:
        again.notes.append(
            f"A second look was taken: {what}. The verdict is from the later "
            "frame, and the difference remains.")
    else:
        again.notes.append(
            f"Failed on the first capture and passed on a later one: {what}. "
            "The verdict is from the later frame — but this snapshot was taken "
            "before the page was done, and that is worth fixing at the source.")
    return SecondLook(again, None, last, live if live.any() else None)


def _keep_what_vanished(first: CompareResult, again: CompareResult,
                        share: float) -> None:
    """Regions of the first comparison with no counterpart in the second.

    A counterpart is any region of the second comparison — reported or
    suppressed — whose box overlaps. What is left changed under the page's
    feet — the later frame no longer has it: it is moved to `suppressed` as
    unstable, and a region that was already suppressed keeps the reason it
    had.

    Their pixels are not added back to `suppressed_pixels`: the second
    comparison ignored them, so they are not among its changed pixels, and the
    three-way split of `changed_pixels` still adds up.
    """
    present = [*again.regions, *again.suppressed]
    reason = (f"{PREFIX}: did not reproduce on a later capture "
              f"({share:.1f}% of the page changed between the frames; the later "
              "frame was compared)")
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
