# VisTest - visual regression testing for Python and Playwright.
# Copyright 2026 Kirill Kulagin
# SPDX-License-Identifier: Apache-2.0
#
# This file is part of VisTest, licensed under the Apache License, Version 2.0.
# You may not use it except in compliance with the License. See LICENSE for
# the terms and NOTICE for attribution and the trademark.

"""The capture-hazard stand is not part of the ordinary run.

Its tests carry the `capture_hazards` mark and are skipped unless that mark
is asked for: `pytest -m capture_hazards tests/capture_hazards`.
"""

from __future__ import annotations

import pytest


def pytest_collection_modifyitems(config, items):
    if "capture_hazards" in (config.getoption("markexpr") or ""):
        return
    skip = pytest.mark.skip(reason="the capture-hazard stand runs on its own: "
                                   "pytest -m capture_hazards tests/capture_hazards")
    for item in items:
        if "capture_hazards" in item.keywords:
            item.add_marker(skip)
