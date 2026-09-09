# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Compatibility re-export. Ignore zones now live in `vistest.core.regions`.

They moved because they belong to the comparison core: a zone decides which
pixels take part in a diff, and that decision needs neither the service, nor
the database, nor a config file. Nothing about the behaviour changed with the
move, and this module keeps the historical import path working.
"""

from __future__ import annotations

from .core.regions import (
    IDENTITY,
    PAD_PX,
    Resolved,
    find_nodes,
    held_by,
    mask,
    normalize,
    normalize_all,
    resolve,
)

__all__ = ["IDENTITY", "PAD_PX", "Resolved", "find_nodes", "held_by", "mask",
           "normalize", "normalize_all", "resolve"]
