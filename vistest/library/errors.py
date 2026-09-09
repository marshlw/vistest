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

__all__ = ["BaselineMissing", "ScreenshotMismatch", "VisTestWarning",
           "VisualCheckError"]


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


class BaselineMissing(VisualCheckError):
    """There is nothing to compare against yet."""

    @classmethod
    def build(cls, *, name: str, platform: str, baseline: Path,
              actual: Path | None, elsewhere: list[str] | None = None,
              update_flag: str = "--vistest-update"):
        lines = [
            f"vistest: no baseline for {name!r}"
            + (f" ({platform})" if platform else ""),
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
              update_flag: str = "--vistest-update"):
        head = f"vistest: {name!r} differs from the baseline"
        if platform:
            head += f" ({platform})"
        lines = [
            head,
            f"  severity {result.max_severity:.1f}"
            f" (limit {limits.get('fail_severity', 0):.1f}),"
            f" changed area {result.changed_area_pct:.2f}%"
            f" (limit {limits.get('max_changed_area_pct', 0):.2f}%)",
            f"  reason: {reason}",
            f"  baseline: {baseline}",
            f"  actual:   {actual}",
        ]
        if diff is not None:
            lines.append(f"  diff:     {diff}")
        if report is not None:
            lines.append(f"  report:   {report}")
        lines.append(f"  accept it with: pytest {update_flag}")
        return cls("\n".join(lines), result=result,
                   artifacts={"baseline": str(baseline), "actual": str(actual),
                              "diff": str(diff) if diff else "",
                              "report": str(report) if report else ""})
