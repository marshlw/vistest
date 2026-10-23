# VisTest - visual regression testing for Python and Playwright.
# Copyright 2026 Kirill Kulagin
# SPDX-License-Identifier: Apache-2.0
#
# This file is part of VisTest, licensed under the Apache License, Version 2.0.
# You may not use it except in compliance with the License. See LICENSE for
# the terms and NOTICE for attribution and the trademark.

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
