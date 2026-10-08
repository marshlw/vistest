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

from pathlib import Path

__all__ = ["BaselineMissing", "CaptureError", "ScreenshotMismatch", "VisTestWarning",
           "VisualCheckError", "hide_for_verdicts"]



#: Changes set aside, spelled out in a failure message; the rest are counted.
SUPPRESSED_SHOWN = 3
#: Changes that count, said in words in a failure message; the rest are counted.
CHANGED_SHOWN = 3


def description(r) -> str:
    """What the engine measured on a change (core/v2/describe.py), or ''."""
    from . import words

    return words.sentence(r)


def changed_line(r) -> str:
    """'97x12 at (219, 522): recolored: #343649 → #586074, color difference 14.2'."""
    from . import words

    return words.region(r)


def suppressed_line(r) -> str:
    """'3x3 at (176, 101): too small to count (2 px, under 4)'."""
    from . import words

    return words.suppressed(r)


def _path(label: str, path) -> str:
    from .words import COLUMN

    return f"  {label + ':':<{COLUMN - 2}}{path}"


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


def hide_for_verdicts(excinfo) -> bool:
    """For pytest's `__tracebackhide__`: hide the library's frames for its own words.

    A failed check, a page that could not be photographed, an argument refused
    with what to do instead — the message is the whole story, and pytest used
    to print two hundred lines of `expect_screenshot`'s source above it. For
    anything else — a bug here, an error from Playwright — the frames stay.
    Set at module level in the library's modules: pytest reads it from a
    frame's globals as well as from its locals.
    """
    error = getattr(excinfo, "value", None)
    if isinstance(error, (VisualCheckError, CaptureError)):
        return True
    from ..core.comparator import ImageTooLarge

    if isinstance(error, ImageTooLarge):
        return True
    return isinstance(error, (TypeError, ValueError)) and \
        str(error).startswith("expect_screenshot")


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
              accept: str = "pytest --vistest-update", asked: str = ""):
        from . import words

        lines = [f"vistest: no baseline for {name!r}" + _where(platform, asked),
                 _path("expected", baseline)]
        if actual is not None:
            lines.append(_path("captured", actual))
        if elsewhere:
            #  The most common and least legible failure this tool produces.
            #  «No baseline» in a repository that visibly has one reads as a
            #  bug in the tool; the reason is that the baseline belongs to
            #  another platform, and nothing in the message used to say so.
            #  Both cases are covered — a run on a new platform, and a run that
            #  suddenly has no platform at all because the target is bytes.
            found = ", ".join(p or "the root" for p in elsewhere)
            lines += words.labelled(
                "found for", f"{found} — baselines are per-platform, and a picture taken "
                "in one browser or window size is not comparable in another")
        lines += words.command("create it", accept)
        lines += words.labelled("", "then commit the file — in CI the baseline has to come "
                                    "from the repository, not from the run")
        return cls(words.console("\n".join(lines)),
                   artifacts={"baseline": str(baseline),
                              "actual": str(actual) if actual else ""})


class ScreenshotMismatch(VisualCheckError):
    """The screenshot and the baseline differ beyond the thresholds in force.

    The message, line by line (review v1, 4.3–4.8): the numbers the verdict
    was taken on; `reason:` — what changed; then what the capture, the second
    look and the page have to say, one `notes` line each (`capture:`, `second
    look:`, `hint:`); `renderer:` for a picture of a page; the files; the
    changes in words with the scale of their color difference; what was set
    aside; and how to accept it where the check ran (`accept`).
    """

    @classmethod
    def build(cls, *, name: str, platform: str, result, reason: str,
              baseline: Path, actual: Path, diff: Path | None,
              report: Path | None, limits: dict,
              accept: str = "pytest --vistest-update", renderer: str = "",
              asked: str = "", notes=()):
        from ..core.engines import V1_DEPRECATED
        from . import words

        lines = [f"vistest: {name!r} differs from the baseline" + _where(platform, asked),
                 *words.limits_lines(result, limits)]
        if limits.get("engine") == "v1":
            lines += words.wrap(V1_DEPRECATED, "  ", "    ")
        lines += words.labelled("reason", reason)
        for label, text in notes:
            lines += words.labelled(label, text)
        if renderer:
            #  Whether the browser draws text the way it did for the baseline:
            #  read before the diff, it changes what the diff means.
            lines += words.labelled("renderer", renderer.removeprefix("renderer: "))
        lines += [_path("baseline", baseline), _path("actual", actual)]
        #  What changed, in the engine's words, before what was set aside.
        said = [r for r in getattr(result, "regions", None) or () if words.sentence(r)]
        shown = [words.region(r) for r in said[:CHANGED_SHOWN]]
        for i, text in enumerate(shown):
            lines += words.labelled("changed" if i == 0 else "", text)
        if len(said) > CHANGED_SHOWN:
            lines += words.labelled("", f"... and {len(said) - CHANGED_SHOWN} more in the "
                                        "report")
        #  What a threshold a person set let through: said, never dropped.
        if below := words.below(result):
            shown.append(below)
            lines += words.labelled("let through", below)
        if scale := words.scale_for(shown):
            lines += words.labelled("", f"({scale})")
        suppressed = list(getattr(result, "suppressed", None) or ())
        if suppressed:
            from ..plugins.runtime import count_suppressed, say_suppressed

            #  Said here too: a failure that also hides something is read
            #  differently from one that does not. Where each one is, and
            #  why, in short; the engine's measurements are in the report.
            lines += words.labelled(
                "also", "; ".join(say_suppressed(count_suppressed(suppressed))))
            for r in suppressed[:SUPPRESSED_SHOWN]:
                lines += words.labelled("", words.suppressed(r))
            if len(suppressed) > SUPPRESSED_SHOWN:
                lines += words.labelled("", f"... and {len(suppressed) - SUPPRESSED_SHOWN}"
                                            " more in the report")
        if diff is not None:
            lines.append(_path("diff", diff))
        if report is not None:
            lines.append(_path("report", report))
        lines += words.command("accept it", accept)
        return cls(words.console("\n".join(lines)), result=result,
                   artifacts={"baseline": str(baseline), "actual": str(actual),
                              "diff": str(diff) if diff else "",
                              "report": str(report) if report else ""})


__tracebackhide__ = hide_for_verdicts
