# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""How somebody else's snapshot files are named, and which is which.

This is the half of a suite profile that the comparison core needs: given a
directory of PNGs left behind by a foreign test run, decide for each file
whether it is an actual, a baseline or a diff picture, and what the snapshot it
belongs to is called. Nothing here starts a process, reads a config file or
touches a database — it takes a root path as an argument and answers.

The other half — how that suite is *started* (`command`, `install`,
`browser_arg`, `only_arg`, detection by repository markers) — is a service-layer
concern and stayed in `vistest.suites`. `SuiteProfile` there extends
`NamingProfile` from here, so every profile is still one object and every
existing attribute is still on it.

The naming rules themselves are the part that was wrong once in a way that made
the whole non-Python path useless, so they are worth stating plainly.
Classification used to go by *prefix* (`actual_login.png`), and the entire JS
ecosystem names by *suffix*: `login-actual.png`, `login-expected.png`,
`login-diff.png`. Every one of those was classified as a baseline, no actuals
were found, and the run ended with «did not find a single fresh actual_*.png».

So a profile carries regular expressions, not prefixes, and each of them names
the snapshot through a group called `name`.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath

#  Directories nobody ever wants walked. `node_modules` is the reason this
#  list exists at all: a single Playwright project holds tens of thousands of
#  files there, and a run used to pay for that walk twice.
SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox",
    ".idea", ".vscode", ".gradle", ".next", ".nuxt", ".cache", "vendor",
    "site-packages", ".vistest",
})

ACTUAL = "actual"
EXPECTED = "expected"
DIFF = "diff"
OTHER = "other"


@dataclass(frozen=True)
class Snapshot:
    """One picture found after a run, already understood."""

    kind: str                    # actual | expected | diff | other
    name: str                    # snapshot name, without the kind marker
    path: Path
    group: str = ""              # what ties an actual to its expected and diff

    @property
    def is_actual(self) -> bool:
        return self.kind == ACTUAL


@dataclass(frozen=True)
class NamingProfile:
    """Where a suite's pictures land and how they are named.

    Declarative on purpose, and separate from the launch half on purpose: a
    library user who never starts anybody's tests still needs these rules to
    read a directory of PNGs, and they must be able to get them without pulling
    in the runner.
    """

    # ---- where the pictures are --------------------------------------- #
    #  Relative to the project root. Scanned in addition to the baseline
    #  folder and the run directory.
    search_dirs: tuple[str, ...] = ()
    #  Scan the tests directory too. True for suites that write the actual
    #  next to the baseline (pytest suites usually do); false for the ones
    #  with a dedicated output folder, where scanning the tests tree only
    #  costs time.
    search_tests: bool = False
    #  Hints for auto-detection: folder names that usually hold baselines.
    baseline_dirs: tuple[str, ...] = ()

    # ---- which picture is which --------------------------------------- #
    #  Full-match regexes against the file NAME, each with a `name` group.
    actual: tuple[str, ...] = ()
    expected: tuple[str, ...] = ()
    diff: tuple[str, ...] = ()
    #  Removed from the extracted name. Playwright writes the baseline as
    #  `login-chromium-linux.png` — the platform is already ours to decide, and
    #  keeping it in the name would make one snapshot look like three.
    strip: tuple[str, ...] = ()
    #  Keep the directory the picture was found in as part of the name. Two
    #  specs may each hold a `login.png`, and flattening them into one name
    #  means the second silently overwrites the first.
    keep_dir: bool = False

    # ------------------------------------------------------------------ #
    def with_overrides(self, naming: dict | None = None,
                       search_dirs=None, keep_dir=None) -> NamingProfile:
        """The project's own corrections on top of the profile.

        There is always a suite whose layout nobody predicted — a Java project
        with paths from its own `pom.xml`, a fork of a tool with renamed
        folders. Refusing those would make the profile list a ceiling instead
        of a floor, so a project may override any pattern and add directories.
        """
        naming = {k: v for k, v in (naming or {}).items() if v}
        patch: dict = {}
        for field_name in ("actual", "expected", "diff", "strip"):
            if field_name in naming:
                value = naming[field_name]
                patch[field_name] = tuple(
                    [value] if isinstance(value, str) else value)
        if keep_dir is not None:
            patch["keep_dir"] = bool(keep_dir)
        if search_dirs:
            patch["search_dirs"] = tuple(dict.fromkeys(
                (*self.search_dirs, *search_dirs)))
        return replace(self, **patch) if patch else self

    # ------------------------------------------------------------------ #
    def roots(self, root: Path, *, extra: tuple[Path, ...] = ()) -> list[Path]:
        """Directories to walk after a run, deduplicated, existing ones only."""
        out: list[Path] = []
        seen: set[Path] = set()
        for candidate in (*extra, *(root / d for d in self.search_dirs)):
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            if resolved in seen or not resolved.exists():
                continue
            seen.add(resolved)
            out.append(resolved)
        return out

    # ------------------------------------------------------------------ #
    def classify(self, path: Path, *, relative_to: Path | None = None
                 ) -> Snapshot:
        """Which of the three this file is, and what the snapshot is called.

        Order matters: `diff` is tried first. A pattern loose enough to catch
        `login-actual.png` will happily catch `login-diff.png` too, and a diff
        image accepted as a result is worse than no result — the engine would
        compare a picture of red rectangles against the baseline and report a
        regression that does not exist.
        """
        for kind, patterns in ((DIFF, self.diff), (EXPECTED, self.expected),
                               (ACTUAL, self.actual)):
            for pattern in patterns:
                m = re.fullmatch(pattern, path.name, re.IGNORECASE)
                if not m:
                    continue
                raw = (m.groupdict().get("name") or path.stem)
                return Snapshot(kind=kind,
                                name=self._name(raw, path, relative_to),
                                path=path,
                                group=self._group(raw, path))
        return Snapshot(kind=OTHER, name="", path=path)

    # ------------------------------------------------------------------ #
    def _name(self, raw: str, path: Path, relative_to: Path | None) -> str:
        """The name the snapshot is stored and shown under.

        The `.png` stays on. It has always been part of the name in the
        history, in the store and on the review screen, and dropping it now
        would make every existing snapshot look like a new one under a new
        name — the same picture twice in the list, with the history split
        between them.
        """
        name = self._strip(raw) + ".png"
        if not self.keep_dir or relative_to is None:
            return name
        try:
            rel = path.parent.resolve().relative_to(Path(relative_to).resolve())
        except (ValueError, OSError):
            return name
        parts = [part for part in rel.parts if part not in (".", "")]
        return PurePosixPath(*parts, name).as_posix() if parts else name

    def _group(self, raw: str, path: Path) -> str:
        """Ties the three pictures of one comparison together.

        Deliberately the directory plus the stripped name and NOT the name the
        snapshot is stored under: `login-actual.png` and `login-expected.png`
        sit in the same folder, and that is what makes them a pair regardless
        of how the name is later shortened.
        """
        return f"{path.parent}\x00{self._strip(raw)}"

    def _strip(self, raw: str) -> str:
        name = str(raw).removesuffix(".png")
        for pattern in self.strip:
            name = re.sub(pattern, "", name, flags=re.IGNORECASE)
        return name.strip("-_. ") or "snapshot"

    # ------------------------------------------------------------------ #
    def walk(self, roots, *, relative_to: Path | None = None,
             limit: int = 20000):
        """Every PNG under `roots`, understood, without stepping into junk.

        `os.walk` and not `rglob` for one reason: it can prune. `rglob` has no
        way to say «do not descend into node_modules», and on a Node project
        that is the difference between a hundred files and a hundred thousand.
        """
        seen = 0
        for root in roots:
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames
                               if d not in SKIP_DIRS and not d.startswith(".")]
                here = Path(dirpath)
                for filename in filenames:
                    if not filename.lower().endswith(".png"):
                        continue
                    seen += 1
                    if seen > limit:
                        return
                    yield self.classify(here / filename,
                                        relative_to=relative_to)
