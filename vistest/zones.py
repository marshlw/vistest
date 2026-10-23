# VisTest - visual regression testing for Python and Playwright.
# Copyright 2026 Kirill Kulagin
# SPDX-License-Identifier: Apache-2.0
#
# This file is part of VisTest, licensed under the Apache License, Version 2.0.
# You may not use it except in compliance with the License. See LICENSE for
# the terms and NOTICE for attribution and the trademark.

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
