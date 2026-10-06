# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""What a failed visual check raises, and why it says what it says.

Both are `AssertionError`s. A visual check is an assertion — pytest colours it
as a failure rather than as an error, `pytest.raises(AssertionError)` catches
it, and a suite that wraps its checks in soft assertions keeps working. The
subclasses exist so that a caller who wants to tell «this never had a baseline»
from «this changed» can, without reading the message.

The messages are the actual product here. In the library mode nobody has a
review interface open: the whole of the diagnosis is these few lines in
somebody's CI log, read by a person who did not write this tool. So each one
names the snapshot, every file involved by path, and the one command that
resolves it.
"""

from __future__ import annotations

import re
from pathlib import Path

__all__ = ["BaselineMissing", "CaptureError", "ScreenshotMismatch", "VisTestWarning",
           "VisualCheckError"]



#: Suppressed regions spelled out in a failure message; the rest are counted.
SUPPRESSED_SHOWN = 3
#: Regions that count, said in words in a failure message; the rest are counted.
CHANGED_SHOWN = 3


def description(r) -> str:
    """The engine's sentence of what it measured on a region, or ''.

    Engine v2 writes one for every region that counts, as an annotation of
    kind «description» (vistest/core/v2/describe.py); v1 writes none.
    """
    notes = r.get("annotations") if isinstance(r, dict) else getattr(r, "annotations", None)
    for a in notes or ():
        if isinstance(a, dict) and a.get("kind") == "description":
            return str(a.get("text") or "")
    return ""


_REDRAWN = re.compile(r"strokes redrawn within (\d+) px; ink (#\w+) → (#\w+), "
                      r"ΔE00 ([\d.]+) \(below ([\d.]+)\)")


def plain(text: str) -> str:
    """The engine's sentence, with the one that read as a contradiction in words.

    «strokes redrawn within 1 px; ink #889ab3 → #889ab3, ΔE00 0.00 (below 2)»
    sat in the list of reasons a check failed, and nothing in it said why: the
    numbers are all «below» something. What it means: the letters moved by
    less than a pixel and the colour is the same — which on the renderer that
    took the baseline nobody explains as noise, so it fails.
    """
    def words(m):
        px, a, b, de, limit = m.groups()
        same = float(de) < 0.5 or a == b
        colour = (f"the colour is the same ({a})" if same
                  else f"the colour is within {limit} ΔE00 of the old one ({a} → {b})")
        return (f"letters moved by less than {px} px, {colour}; on the same renderer "
                "this is not explained as noise, so it counts")

    return _REDRAWN.sub(words, text)


def changed_line(r) -> str:
    """'97x12 at (219, 522): ink colour: #343649 → #586074, ΔE00 14.2'."""
    def get(key):
        return r.get(key) if isinstance(r, dict) else getattr(r, key, None)

    return f"{get('w')}x{get('h')} at ({get('x')}, {get('y')}): {plain(description(r))}"


def suppressed_line(r) -> str:
    """'48x12 at (216, 519): antialias: the baseline moved +0.25,+0.00 px ...'.

    The kind is named only when the reason does not start with it:
    'noise 97x12 at (219, 522): rerender: ... (was text)'.
    """
    def get(key):
        return r.get(key) if isinstance(r, dict) else getattr(r, key, None)

    kind = get("kind")
    kind = getattr(kind, "value", kind) or ""
    why = get("suppressed_by") or "suppressed"
    where = f"{get('w')}x{get('h')} at ({get('x')}, {get('y')})"
    #  "antialias 40x12 ...: antialias: ..." says the same word twice.
    if kind and not why.startswith(f"{kind}:"):
        where = f"{kind} {where}"
    return f"{where}: {why}"

def _limits_line(result, limits: dict) -> str:
    """The numbers the verdict was taken on, the way the engine takes it.

    v1 fails on severity against its limit, or on the changed area. v2 fails
    on any region no rule explained — unless a person set a threshold, and
    then only on those at or above it (or on the share of the frame the ones
    below cover), so the threshold is named with where it came from.
    """
    n = len(result.regions)
    regions = f"{n} region{'' if n == 1 else 's'}"
    if limits.get("engine") == "v2":
        from ..core import engines as _engines

        source = limits.get("threshold_source")
        area_source = limits.get("area_source") or _engines.AREA_DEFAULT_SOURCE
        threshold = (f"threshold {limits.get('fail_severity', 0):g} ({source}), "
                     f"area limit {limits.get('max_changed_area_pct', 0):.2f}% "
                     f"({area_source})"
                     if source else "no threshold is set, so any such region fails")
        said = getattr(result, "threshold", None) or {}
        if "area_pct" in said:
            #  Every region was below the threshold, and together they cover
            #  the share of the frame that fails on its own — said with what
            #  to do about it.
            threshold += (f" — all below it, together {said['area_pct']:.2f}% of "
                          f"the frame, at or over the area limit: "
                          f"{_engines.AREA_HINT}")
        return (f"  engine v2: {regions} that no rule of the engine explains away "
                "(not antialiasing, not a different renderer, not a block that only "
                f"moved), severity up to {result.max_severity:.1f} on a 0–100 scale "
                f"(0 — nothing, higher — a bigger change, 100 — the top); {threshold}; "
                f"changed area {result.changed_area_pct:.2f}% of the frame")
    return (f"  severity {result.max_severity:.1f}"
            f" (limit {limits.get('fail_severity', 0):.1f}),"
            f" changed area {result.changed_area_pct:.2f}%"
            f" (limit {limits.get('max_changed_area_pct', 0):.2f}%), {regions}")


class VisTestWarning(UserWarning):
    """An optional part is missing, and the run goes on without it.

    The other half of the rule this package follows. A configuration
    mistake raises; an absent optional piece — no Playwright, no pytest
    plugin, no report — warns and carries on. A category of its own so
    that a project can turn these into errors with `-W error::...` when it
    wants the strict reading, and so that they can be silenced without
    silencing everything else."""


class VisualCheckError(AssertionError):
    """Base for a check that did not pass. Carries the result when there is one."""

    def __init__(self, message: str, *, result=None, artifacts: dict | None = None):
        super().__init__(message)
        self.result = result
        self.artifacts = dict(artifacts or {})


def _where(platform: str, asked: str) -> str:
    """` (linux-chromium-1x-1280x720; the name given was 'a?.png')`, or less."""
    parts = [platform] if platform else []
    if asked:
        parts.append(f"the name given was {asked!r}")
    return f" ({'; '.join(parts)})" if parts else ""


class CaptureError(RuntimeError):
    """The picture could not be taken: the page is closed, crashed, or stopped answering.

    Not an `AssertionError`, on purpose. Nothing was compared, so nothing
    failed: the check could not be made, and pytest reports it as such. The
    message names the snapshot, what was being asked of the page when it
    stopped, and how long that was waited for — the alternative used to be a
    run that never returned.
    """


class BaselineMissing(VisualCheckError):
    """There is nothing to compare against yet."""

    @classmethod
    def build(cls, *, name: str, platform: str, baseline: Path,
              actual: Path | None, elsewhere: list[str] | None = None,
              update_flag: str = "--vistest-update", asked: str = ""):
        lines = [
            f"vistest: no baseline for {name!r}" + _where(platform, asked),
            f"  expected: {baseline}",
        ]
        if actual is not None:
            lines.append(f"  captured: {actual}")
        if elsewhere:
            #  The most common and least legible failure this tool produces.
            #  «No baseline» in a repository that visibly has one reads as a
            #  bug in the tool; the reason is that the baseline belongs to
            #  another platform, and nothing in the message used to say so.
            #  Both cases are covered — a run on a new platform, and a run that
            #  suddenly has no platform at all because the target is bytes.
            found = ", ".join(p or "the root" for p in elsewhere)
            lines.append(
                f"  found for: {found} — baselines are per-platform, and a "
                "picture taken in one browser or window size is not comparable "
                "in another")
        lines += [
            f"  create it with: pytest {update_flag}",
            "  then commit the file — in CI the baseline has to come from the "
            "repository, not from the run.",
        ]
        return cls("\n".join(lines),
                   artifacts={"baseline": str(baseline),
                              "actual": str(actual) if actual else ""})


class ScreenshotMismatch(VisualCheckError):
    """The screenshot and the baseline differ beyond the thresholds in force."""

    @classmethod
    def build(cls, *, name: str, platform: str, result, reason: str,
              baseline: Path, actual: Path, diff: Path | None,
              report: Path | None, limits: dict,
              update_flag: str = "--vistest-update", renderer: str = "",
              asked: str = ""):
        head = f"vistest: {name!r} differs from the baseline" + _where(platform, asked)
        from ..core.engines import V1_DEPRECATED

        lines = [
            head,
            _limits_line(result, limits),
            *([f"  {V1_DEPRECATED}"] if limits.get("engine") == "v1" else []),
            f"  reason: {reason}",
            #  Whether the browser draws text the way it did for the baseline:
            #  read before the diff, it changes what the diff means.
            *([f"  {renderer}"] if renderer else []),
            f"  baseline: {baseline}",
            f"  actual:   {actual}",
        ]
        #  What changed, in the engine's words, before what was set aside.
        said = [r for r in getattr(result, "regions", None) or () if description(r)]
        for i, r in enumerate(said[:CHANGED_SHOWN]):
            lines.append(("  changed:  " if i == 0 else "            ") + changed_line(r))
        if len(said) > CHANGED_SHOWN:
            lines.append(f"            ... and {len(said) - CHANGED_SHOWN} more in the report")
        #  What a threshold a person set let through: said, never dropped.
        from ..core.engines import below_line

        if below := below_line(result):
            lines.append(f"  {below}")
        suppressed = list(getattr(result, "suppressed", None) or ())
        if suppressed:
            from ..plugins.runtime import count_suppressed, say_suppressed

            #  Said here too: a failure that also hides something is read
            #  differently from one that does not.
            lines.append("  also:     "
                         + "; ".join(say_suppressed(count_suppressed(suppressed))))
            #  And why, in the engine's own words: a suppression is a claim
            #  ("the baseline moved 0.25 px reproduces 96% of it") that the
            #  reader is entitled to check against the diff.
            for r in suppressed[:SUPPRESSED_SHOWN]:
                lines.append(f"            {suppressed_line(r)}")
            if len(suppressed) > SUPPRESSED_SHOWN:
                lines.append(f"            ... and {len(suppressed) - SUPPRESSED_SHOWN}"
                             " more in the report")
        if diff is not None:
            lines.append(f"  diff:     {diff}")
        if report is not None:
            lines.append(f"  report:   {report}")
        lines.append(f"  accept it with: pytest {update_flag}")
        return cls("\n".join(lines), result=result,
                   artifacts={"baseline": str(baseline), "actual": str(actual),
                              "diff": str(diff) if diff else "",
                              "report": str(report) if report else ""})
