# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""What every test of the library shares.

The server's tests, and the restoration of the service modules they reload,
are in `server/tests/conftest.py` (o1c): nothing under `tests/` imports the
server, and this half of the suite runs where it is not installed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

#  The root vistest.yaml is the configuration of this repository's server and
#  its own visual checks — not a library project's. Every run of this suite
#  from the root reads it, and the library warns about the ~50 keys of it that
#  it does not read. That warning is right for a user and noise here: it is
#  filtered for the root file of this repository only, by its full path, and
#  only for this suite. A user's run, a vistest.yaml a test writes itself, and
#  the tests of the warning (pytester runs carry their own ini) are untouched.
ROOT_CONFIG = Path(__file__).resolve().parent.parent / "vistest.yaml"


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    #  A colon separates the fields of a filter, so the path's own (a Windows
    #  drive) is matched by «.» instead.
    path = re.escape(str(ROOT_CONFIG)).replace(":", ".")
    config.addinivalue_line(
        "filterwarnings",
        f"ignore:vistest. {path}. the library does not read"
        f":vistest.library.errors.VisTestWarning")
