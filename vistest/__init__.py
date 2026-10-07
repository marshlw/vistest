# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""VisTest — visual regression testing for pytest.

The top of the package is the library: `expect_screenshot`, the exceptions it
raises and the warning it gives, and the comparison itself (`compare`, its
`CompareResult` and `Verdict`). That is what a project that drops VisTest into
its own tests needs, and nothing else is exported here.

Everything else — the server's runner and check service, the stores, the
config loader, the integrations — is reached by its full path:
`from vistest.service import CheckService`. Asking the top for one of those
names is an AttributeError that says where it is now (`_MOVED`); there is no
shim that keeps the old spelling working with a warning — there was no public
release that promised it.

Everything below `vistest.core` is the comparison engine and nothing else, and
`import vistest` does not import the layers above it: the library itself is
loaded on first use (`_LAZY`). `vistest.library` reads the config and touches
the filesystem, and `import vistest` must keep doing neither.

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
from .models import CompareResult, Verdict

if TYPE_CHECKING:
    #  Never executed. This is the same import list `__getattr__` performs at
    #  runtime, written so that a type checker and an editor can see it. Adding
    #  a name to `_LAZY` means adding it here too.
    from .library import BaselineMissing as BaselineMissing
    from .library import CaptureError as CaptureError
    from .library import ScreenshotMismatch as ScreenshotMismatch
    from .library import VisTestWarning as VisTestWarning
    from .library import VisualCheckError as VisualCheckError
    from .library import expect_screenshot as expect_screenshot

#  The one place the version is written: pyproject.toml reads it from here.
__version__ = "0.2.0.dev2"

#  attribute -> module it comes from, relative to this package. Kept as data so
#  that the list of what is deliberately deferred can be read at a glance.
_LAZY = {
    "expect_screenshot": ".library",
    "BaselineMissing": ".library",
    "ScreenshotMismatch": ".library",
    "VisualCheckError": ".library",
    "CaptureError": ".library",
    "VisTestWarning": ".library",
}

#  Exported at the top until 0.2.0.dev3, and where each one is now. Only for
#  the message: asking for one of these is still an AttributeError.
_MOVED = {
    "VisTestConfig": "vistest.config",
    "VisualSession": "vistest.integrations",
    "VisualTestCase": "vistest.integrations",
    "visual_check": "vistest.integrations",
    "visual_session": "vistest.integrations",
    "VisualTester": "vistest.runner",
    "VisualMismatch": "vistest.runner",
    "CheckService": "vistest.service",
    "FileStore": "vistest.storage",
    "SnapshotKey": "vistest.storage",
    "SnapshotMeta": "vistest.storage",
    "SnapshotStore": "vistest.storage",
    "ChangeKind": "vistest.models",
    "DiffRegion": "vistest.models",
}


def __getattr__(name):
    """Deferred access to the library, and where a name that left the top went.

    The imported object is written back into the module's own namespace, so
    this runs once per name and the second access is an ordinary attribute
    lookup.
    """
    module = _LAZY.get(name)
    if module is None:
        moved = _MOVED.get(name)
        if moved is not None:
            raise AttributeError(
                f"module {__name__!r} has no attribute {name!r}: it is not exported "
                f"at the top since 0.2.0.dev3 — from {moved} import {name}")
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
    "expect_screenshot",
    "BaselineMissing",
    "ScreenshotMismatch",
    "VisualCheckError",
    "CaptureError",
    "VisTestWarning",
    "CompareResult",
    "Verdict",
    "compare",
]
