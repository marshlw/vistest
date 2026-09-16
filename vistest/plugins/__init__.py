# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Extension points.

`vistest.plugins.api` is the contract a plugin is written against; the rest of
this package is how the core finds plugins and what it does with their
answers. Nothing here imports a plugin by name — plugins are found through the
``vistest.plugins`` entry-point group only.
"""

from __future__ import annotations

from .api import (
    API_VERSION,
    ENTRY_POINT_GROUP,
    Annotation,
    AuthProvider,
    AuthResult,
    BaselineSyncBackend,
    RegionAnnotator,
    RegionContext,
    RegionScorer,
)
from .loader import active_registry, ensure_loaded, plugins_disabled
from .registry import PluginRegistry

__all__ = [
    "API_VERSION",
    "ENTRY_POINT_GROUP",
    "Annotation",
    "AuthProvider",
    "AuthResult",
    "BaselineSyncBackend",
    "PluginRegistry",
    "RegionAnnotator",
    "RegionContext",
    "RegionScorer",
    "active_registry",
    "ensure_loaded",
    "plugins_disabled",
]
