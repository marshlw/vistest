# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""The registry of suite profiles — the one place a new tool is added.

Adding support for the next language is a `SuiteProfile` in `builtin.py` and a
line in `PROFILES`. Nothing in the runner, the collector or the auto-detection
needs to know it exists. That is the whole design goal: the previous shape put
one tool's assumptions (pytest, `actual_` prefixes, `python -m pytest`) into
three different modules, and every new ecosystem meant touching all three.

Nothing here imports `Project`, deliberately — `projects` imports this module,
and the reverse would close the circle. The functions take the few attributes
they need by duck typing.
"""

from __future__ import annotations

import os
from pathlib import Path

from .base import ACTUAL, DIFF, EXPECTED, OTHER, SKIP_DIRS, Snapshot, SuiteProfile
from .builtin import DEFAULT, FALLBACK, PROFILES

__all__ = ["SuiteProfile", "Snapshot", "PROFILES", "SKIP_DIRS",
           "ACTUAL", "EXPECTED", "DIFF", "OTHER",
           "get", "ids", "choices", "detect", "forget", "resolve", "for_runner"]

_BY_ID = {p.id: p for p in PROFILES}

#  How deep auto-detection is willing to look for a marker such as
#  `__image_snapshots__`. A monorepo is not a reason to walk forever, and a
#  marker that deep is not evidence of anything anyway.
_DETECT_DEPTH = 4


def get(profile_id: str) -> SuiteProfile | None:
    return _BY_ID.get(str(profile_id or "").strip().lower())


def ids() -> list[str]:
    return [p.id for p in PROFILES]


def choices() -> list[dict]:
    """What the interface offers in the profile selector."""
    return [{"id": p.id, "title": p.title, "runner": p.runner,
             "command": list(p.command), "note": p.note}
            for p in PROFILES]


# --------------------------------------------------------------------------- #
#  Detection walks the top of somebody else's repository, and the answer is
#  asked for on every project card render. Caching it is not an optimisation
#  but a correctness matter for the interface: without it, listing eight
#  projects means eight tree walks per page.
_CACHE: dict[str, str] = {}


def forget(root: str | Path | None = None) -> None:
    """Drop the detection cache — after connecting or editing a project."""
    if root is None:
        _CACHE.clear()
    else:
        _CACHE.pop(str(Path(root).expanduser().resolve()), None)


def detect(root: str | Path) -> SuiteProfile | None:
    """Which tool this repository is built around, judged by its markers.

    Returns `None` rather than guessing when nothing matches: a wrong profile
    is worse than no profile, because it silently classifies files under the
    wrong rules and the run ends with «no pairs found» and no reason given.
    """
    root = Path(root).expanduser()
    if not root.is_dir():
        return None
    key = str(root.resolve())
    if key in _CACHE:
        return get(_CACHE[key])
    found = _detect_uncached(root)
    _CACHE[key] = found.id if found else ""
    return found


def _detect_uncached(root: Path) -> SuiteProfile | None:
    names = _shallow_index(root)
    for profile in PROFILES:
        if profile is FALLBACK:
            continue
        for pattern in profile.detect:
            if _matches(root, names, pattern):
                return profile
    return None


def _shallow_index(root: Path) -> set[str]:
    """Relative posix paths near the top of the tree, for glob matching."""
    found: set[str] = set()
    root_len = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        depth = len(here.parts) - root_len
        if depth >= _DETECT_DEPTH:
            dirnames[:] = []
        dirnames[:] = [d for d in dirnames
                       if d not in SKIP_DIRS and not d.startswith(".")]
        for name in (*filenames, *dirnames):
            found.add((here / name).relative_to(root).as_posix())
        if len(found) > 20000:
            break
    return found


def _matches(root: Path, names: set[str], pattern: str) -> bool:
    from fnmatch import fnmatch

    if pattern.startswith("**/"):
        tail = pattern[3:]
        return any(fnmatch(n, tail) or fnmatch(n.rsplit("/", 1)[-1], tail)
                   or fnmatch(n, pattern) for n in names)
    if "*" in pattern or "?" in pattern:
        return any(fnmatch(n, pattern) for n in names)
    return pattern in names or (root / pattern).exists()


# --------------------------------------------------------------------------- #
def for_runner(runner: str) -> SuiteProfile:
    """The profile a project falls back to when nothing was chosen or found.

    A pytest project keeps the historical behaviour exactly; anything started
    by its own command gets the widest rules, because we know nothing about it
    beyond the fact that it is not pytest.
    """
    return DEFAULT if (runner or "pytest") == "pytest" else FALLBACK


def resolve(project) -> SuiteProfile:
    """The profile this project actually runs under, overrides applied.

    Precedence, and each step exists for a reason:

    * an explicit `suite` in the project description — a person looked at the
      run and said what the tool is, and that beats any detection;
    * detection by repository markers, but only for a project that did not
      choose — and only when the detected profile agrees with how the project
      is started, because `runner: command` on a repository that also happens
      to contain `pyproject.toml` is not a pytest suite;
    * the fallback for the runner.

    On top of whichever won, the project's own `naming` and `search_dirs`
    corrections are applied. Those are what keep the profile list a floor
    rather than a ceiling.
    """
    chosen = str(getattr(project, "suite", "") or "auto").strip().lower()
    runner = str(getattr(project, "runner", "") or "pytest").lower()

    profile = None
    if chosen and chosen != "auto":
        profile = get(chosen)
    if profile is None and chosen in ("", "auto"):
        try:
            found = detect(project.root_path)
        except Exception:
            found = None
        if found is not None and found.runner == runner:
            profile = found
    if profile is None:
        profile = for_runner(runner)

    return profile.with_overrides(
        naming=getattr(project, "naming", None) or None,
        search_dirs=tuple(getattr(project, "search_dirs", ()) or ()),
        keep_dir=getattr(project, "keep_dir", None) or None,
    )
