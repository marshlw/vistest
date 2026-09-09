# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Baseline storage.

Eager here: what has no dependencies beyond the engine — the key, the passport,
the protocol, the name-to-path rules and the atomic write. Deferred: the three
stores that decode images, because each of them imports the capture layer and
through it the config loader.

That split is not tidiness. `from vistest.storage import FileStore` runs this
file, and while the stores were plain imports it pulled the capture layer into
every process that only wanted to look up a file — including somebody else's
pytest, which is exactly who the library mode is for. The mechanism is the same
one `vistest/__init__.py` uses, and `tests/test_lazy_exports.py` is what keeps
the two lists from drifting apart.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import (
    BaselineRecord,
    BaselineStore,
    SnapshotKey,
    SnapshotMeta,
    SnapshotStore,
    platforms_with,
)
from .file import DEFAULT_BASELINE_ROOT, FileStore
from .paths import safe_name, split_project

if TYPE_CHECKING:
    #  Never executed. The same import list `__getattr__` performs, written so
    #  that a type checker and an editor can see it.
    from .external import ExternalBaselineStore as ExternalBaselineStore
    from .fs import FileBaselineStore as FileBaselineStore
    from .pair import SiblingBaselineStore as SiblingBaselineStore

_LAZY = {
    "FileBaselineStore": ".fs",
    "ExternalBaselineStore": ".external",
    "SiblingBaselineStore": ".pair",
}


def __getattr__(name):
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    value = getattr(import_module(module, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_LAZY})


__all__ = [
    "BaselineRecord",
    "BaselineStore",
    "SnapshotKey",
    "SnapshotMeta",
    "SnapshotStore",
    "platforms_with",
    "FileStore",
    "DEFAULT_BASELINE_ROOT",
    "safe_name",
    "split_project",
    # deferred, see _LAZY
    "FileBaselineStore",
    "ExternalBaselineStore",
    "SiblingBaselineStore",
]
