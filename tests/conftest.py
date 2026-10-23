# VisTest - visual regression testing for Python and Playwright.
# Copyright 2026 Kirill Kulagin
# SPDX-License-Identifier: Apache-2.0
#
# This file is part of VisTest, licensed under the Apache License, Version 2.0.
# You may not use it except in compliance with the License. See LICENSE for
# the terms and NOTICE for attribution and the trademark.

"""What every test of the library shares.

Nothing yet: this file marks `tests/` as the library's half of the suite,
which runs where the server is not installed and reads only what the open
repository holds (`tests/open_paths.py`). The filter for this repository's
root `vistest.yaml` is in the root `conftest.py` (o1d2).
"""

from __future__ import annotations
