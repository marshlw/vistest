# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""VisTest — фреймворк визуального регрессионного тестирования."""

from .config import VisTestConfig
from .core.comparator import compare
from .models import ChangeKind, CompareResult, DiffRegion, Verdict
from .runner import VisualMismatch, VisualTester
from .service import CheckService

__version__ = "0.1.0"


def __getattr__(name):
    """Ленивый доступ к слою интеграций, чтобы не тянуть его при обычном импорте."""
    if name in ("visual_check", "visual_session", "VisualSession", "VisualTestCase"):
        from . import integrations

        return getattr(integrations, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "VisualTester",
    "VisualMismatch",
    "VisTestConfig",
    "CheckService",
    "CompareResult",
    "DiffRegion",
    "ChangeKind",
    "Verdict",
    "compare",
    # из vistest.integrations
    "visual_check",
    "visual_session",
    "VisualSession",
    "VisualTestCase",
]
