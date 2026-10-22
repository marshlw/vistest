# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""What every test of the library shares.

Nothing yet: this file marks `tests/` as the library's half of the suite,
which runs where the server is not installed and reads only what the open
repository holds (`tests/open_paths.py`). The filter for this repository's
root `vistest.yaml` is in the root `conftest.py` (o1d2).
"""

from __future__ import annotations
