# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Baseline storage of the library.

What has no dependencies beyond the engine — the key, the passport, the
protocol, the name-to-path rules and the atomic write — and the library's own
store, `FileStore` (`tests/__vistest__/`). The directory of passports,
`FileBaselineStore`, is here too and deferred (it decodes images and imports
the capture layer): `vistest baselines import` and `vistest doctor` write and
read that tree. The server's other stores — a store of another project, the
sibling layout — live with the server; nothing here names them (o1).
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
from .file import DEFAULT_BASELINE_ROOT, FALLBACK_BASELINE_ROOT, FileStore, default_root
from .paths import safe_name, split_project

if TYPE_CHECKING:
    from .fs import FileBaselineStore as FileBaselineStore


def __getattr__(name):
    """`FileBaselineStore` on first use: it decodes images."""
    if name != "FileBaselineStore":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from .fs import FileBaselineStore

    globals()[name] = FileBaselineStore
    return FileBaselineStore


__all__ = [
    "BaselineRecord",
    "BaselineStore",
    "SnapshotKey",
    "SnapshotMeta",
    "SnapshotStore",
    "platforms_with",
    "FileStore",
    "DEFAULT_BASELINE_ROOT",
    "FALLBACK_BASELINE_ROOT",
    "default_root",
    "safe_name",
    "split_project",
    "FileBaselineStore",
]
