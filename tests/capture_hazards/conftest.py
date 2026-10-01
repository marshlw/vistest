# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

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
