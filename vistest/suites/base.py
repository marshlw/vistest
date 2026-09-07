# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""What a foreign test suite looks like on disk.

Everything that depends on somebody else's tool lives in one declarative
object. That is the whole point of this module: adding Playwright, Cypress,
BackstopJS or the next thing must not mean editing the runner, the collector
and the auto-detection — it must mean one more `SuiteProfile`.

Three questions decide whether a suite can be connected at all, and a profile
answers exactly those:

1. **How is it started.** `pytest` is assembled by us (interpreter, plugin,
   markers) and is the only shape the adapter can attach to. Everything else is
   started by its own command, as written.
2. **Where do the pictures land.** A run leaves files behind; we have to know
   which directories to look in — and, just as importantly, which to stay out
   of. `rglob("*.png")` over a Node repository walks `node_modules`.
3. **Which picture is which.** This is where the previous version was wrong in
   a way that made the whole non-Python path useless. Classification went by
   *prefix* (`actual_login.png`), and the entire JS ecosystem names by
   *suffix*: `login-actual.png`, `login-expected.png`, `login-diff.png`. Every
   one of them was classified as a baseline, no actuals were found, and the run
   ended with «did not find a single fresh actual_*.png».

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
class SuiteProfile:
    """A description of one testing tool. Declarative on purpose.

    `id` is what gets written into the project description, so it is part of
    the configuration format: renaming one is a migration, not a refactor.
    """

    id: str
    title: str

    # ---- how it is started -------------------------------------------- #
    runner: str = "command"
    command: tuple[str, ...] = ()
    #  Flags that pass the browser choice. Empty means the tool has no such
    #  flag, and that is the important case: appending `--browser` to
    #  `mvn test` breaks a build that was working. Formatted with {browser}.
    browser_arg: tuple[str, ...] = ()
    #  How this tool is told to run ONE test instead of the whole suite.
    #  Formatted with {only}. Empty means the tool cannot be aimed — and then
    #  the honest answer is a refusal, not a full run: «re-check this one
    #  snapshot» that quietly runs the whole suite for twenty minutes is worse
    #  than a button that says it cannot.
    only_arg: tuple[str, ...] = ()
    #  Can the comparison be intercepted inside their process. True only for
    #  pytest: the adapter is a pytest plugin and lives in their interpreter.
    intercept: bool = False

    # ---- what their tests look like ----------------------------------- #
    #  How this project's dependencies get installed, as (marker, command)
    #  pairs: the first pair whose marker file exists in the project root
    #  wins, and an empty marker always matches. Pairs and not one command,
    #  because the answer genuinely depends on the repository — `npm ci`
    #  without a lockfile fails, and a team on pnpm has neither.
    #
    #  Empty means we do not know how, and then the honest answer is to say
    #  so. The button used to pip-install into VisTest's own environment
    #  whatever the project was, which for a Node or JVM suite is not a
    #  partial answer but a wrong one.
    install: tuple[tuple[str, tuple[str, ...]], ...] = ()

    #  Glob patterns of test FILES. For pytest they are read from the
    #  project's own config instead — it is the authority there, and a static
    #  list would disagree with it.
    test_files: tuple[str, ...] = ()
    #  What a test declaration looks like in such a file. Used only to say how
    #  many are in it: a person counts tests, a list shows files, and six rows
    #  against eleven baselines reads as loss rather than as a different unit.
    test_decl: str = ''

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

    # ---- detection ----------------------------------------------------- #
    #  Globs relative to the root; any single match identifies the suite.
    detect: tuple[str, ...] = ()

    note: str = ""

    # ------------------------------------------------------------------ #
    def with_overrides(self, naming: dict | None = None,
                       search_dirs=None, keep_dir=None) -> SuiteProfile:
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
    def install_command(self, root: Path) -> list[str]:
        """Чем ставить зависимости ИМЕННО этого репозитория. Пусто — не знаем."""
        for marker, command in self.install:
            if not marker or (Path(root) / marker).exists():
                return list(command)
        return []

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
