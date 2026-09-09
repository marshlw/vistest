# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""What a foreign test suite looks like on disk, and how it is started.

Everything that depends on somebody else's tool lives in one declarative
object. That is the whole point of this module: adding Playwright, Cypress,
BackstopJS or the next thing must not mean editing the runner, the collector
and the auto-detection — it must mean one more `SuiteProfile`.

Three questions decide whether a suite can be connected at all, and a profile
answers exactly those:

1. **How is it started.** `pytest` is assembled by us (interpreter, plugin,
   markers) and is the only shape the adapter can attach to. Everything else is
   started by its own command, as written. That half is here.
2. **Where do the pictures land.** A run leaves files behind; we have to know
   which directories to look in — and, just as importantly, which to stay out
   of. `rglob("*.png")` over a Node repository walks `node_modules`.
3. **Which picture is which.** Regular expressions against the file name, each
   naming the snapshot through a group called `name`.

Questions two and three are `NamingProfile` in `vistest.core.naming`: they are
pure classification of files on disk, they are what a library user needs
without any of the launching, and the comparison core must be importable
without the service. `SuiteProfile` extends it rather than holding one, so a
profile stays a single flat object — `profile.actual`, `profile.classify(...)`
and `replace(profile, ...)` all work exactly as they did.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..core.naming import (
    ACTUAL,
    DIFF,
    EXPECTED,
    OTHER,
    SKIP_DIRS,
    NamingProfile,
    Snapshot,
)

__all__ = ["ACTUAL", "DIFF", "EXPECTED", "OTHER", "SKIP_DIRS",
           "NamingProfile", "Snapshot", "SuiteProfile"]


@dataclass(frozen=True)
class SuiteProfile(NamingProfile):
    """A description of one testing tool. Declarative on purpose.

    `id` is what gets written into the project description, so it is part of
    the configuration format: renaming one is a migration, not a refactor.

    The fields for where the pictures are and which is which are inherited from
    `NamingProfile`; only the launching half is declared here.
    """

    #  Defaulted because the base class has defaults and a dataclass cannot put
    #  a required field after an optional one. Every profile is built with
    #  keyword arguments and states both.
    id: str = ""
    title: str = ""

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

    # ---- detection ----------------------------------------------------- #
    #  Globs relative to the root; any single match identifies the suite.
    detect: tuple[str, ...] = ()

    note: str = ""

    # ------------------------------------------------------------------ #
    def install_command(self, root: Path) -> list[str]:
        """What installs THIS repository's dependencies. Empty — we do not know."""
        for marker, command in self.install:
            if not marker or (Path(root) / marker).exists():
                return list(command)
        return []
