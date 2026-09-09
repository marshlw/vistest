# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""VisTest — a visual regression testing framework.

Everything below `vistest.core` is the comparison engine and nothing else: it
knows how to compare two pictures, which regions to ignore, how snapshot files
are named and which threshold wins. It reads no config file, opens no database
and imports no web framework.

Everything above it — capture, storage, artifacts, the runner, the service —
is reached through this module's attributes and is imported on first use. That
is not a micro-optimisation. `import vistest.core` runs this file first, and
while these were plain imports it pulled the whole orchestrator, the baseline
storage and the Playwright capture module along with it. For the library mode,
where somebody drops the engine into their own project, that is the difference
between a package that fits into their test run and one that brings a service
with it.

The names are unchanged, so `from vistest import CheckService` still works.

`__getattr__` is invisible to a type checker and to an editor: both read the
source, and in the source these names do not exist. The `TYPE_CHECKING` block
below is what puts them back — it is the real import list, it never runs, and
mypy and autocompletion read exactly it. The pairing is checked by
`tests/test_lazy_exports.py`, so the block cannot drift away from `_LAZY`
without a test saying so.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .core.comparator import compare
from .models import ChangeKind, CompareResult, DiffRegion, Verdict

if TYPE_CHECKING:
    #  Never executed. This is the same import list `__getattr__` performs at
    #  runtime, written so that a type checker and an editor can see it. Adding
    #  a name to `_LAZY` means adding it here too.
    from .config import VisTestConfig as VisTestConfig
    from .integrations import VisualSession as VisualSession
    from .integrations import VisualTestCase as VisualTestCase
    from .integrations import visual_check as visual_check
    from .integrations import visual_session as visual_session
    from .library import BaselineMissing as BaselineMissing
    from .library import ScreenshotMismatch as ScreenshotMismatch
    from .library import VisTestWarning as VisTestWarning
    from .library import VisualCheckError as VisualCheckError
    from .library import expect_screenshot as expect_screenshot
    from .runner import VisualMismatch as VisualMismatch
    from .runner import VisualTester as VisualTester
    from .service import CheckService as CheckService
    from .storage import FileStore as FileStore
    from .storage import SnapshotKey as SnapshotKey
    from .storage import SnapshotMeta as SnapshotMeta
    from .storage import SnapshotStore as SnapshotStore

__version__ = "0.1.0"

#  attribute -> module it comes from, relative to this package. Kept as data so
#  that the list of what is deliberately deferred can be read at a glance.
_LAZY = {
    #  The loader, not the settings objects: `vistest.config` is what searches
    #  the working directory for `vistest.yaml` and reads the environment. The
    #  engine is configured with `vistest.core.settings.DiffConfig`, which is
    #  plain data and stays eager.
    "VisTestConfig": ".config",
    #  The library mode. Deferred for the same reason as everything else here,
    #  and for one more: `vistest.library` reads the config and touches the
    #  filesystem, and `import vistest` must keep doing neither.
    "expect_screenshot": ".library",
    "BaselineMissing": ".library",
    "ScreenshotMismatch": ".library",
    "VisualCheckError": ".library",
    "VisTestWarning": ".library",
    "SnapshotKey": ".storage",
    "SnapshotMeta": ".storage",
    "SnapshotStore": ".storage",
    "FileStore": ".storage",
    "VisualTester": ".runner",
    "VisualMismatch": ".runner",
    "CheckService": ".service",
    "visual_check": ".integrations",
    "visual_session": ".integrations",
    "VisualSession": ".integrations",
    "VisualTestCase": ".integrations",
}


def __getattr__(name):
    """Deferred access to the layers above the engine.

    The imported object is written back into the module's own namespace, so
    this runs once per name and the second access is an ordinary attribute
    lookup.
    """
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    value = getattr(import_module(module, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Everything reachable, including what has not been imported yet.

    Without this, `dir(vistest)` shows only what happens to have been touched
    already, so the same call answers differently depending on what ran before
    it. Interactive completion reads this.
    """
    return sorted({*globals(), *_LAZY})


__all__ = [
    # the library mode
    "expect_screenshot",
    "BaselineMissing",
    "ScreenshotMismatch",
    "VisualCheckError",
    "VisTestWarning",
    "SnapshotKey",
    "SnapshotMeta",
    "SnapshotStore",
    "FileStore",
    # the service
    "VisualTester",
    "VisualMismatch",
    "VisTestConfig",
    "CheckService",
    "CompareResult",
    "DiffRegion",
    "ChangeKind",
    "Verdict",
    "compare",
    # from vistest.integrations
    "visual_check",
    "visual_session",
    "VisualSession",
    "VisualTestCase",
]
