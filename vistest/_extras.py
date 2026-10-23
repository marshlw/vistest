# VisTest - visual regression testing for Python and Playwright.
# Copyright 2026 Kirill Kulagin
# SPDX-License-Identifier: Apache-2.0
#
# This file is part of VisTest, licensed under the Apache License, Version 2.0.
# You may not use it except in compliance with the License. See LICENSE for
# the terms and NOTICE for attribution and the trademark.

"""What the library says when the server is not there.

The library is `vistest`. The review interface, the API, the server-mode
fixtures and the server's commands are another package, `vistest-server`,
which plugs into the library through entry points (`vistest.commands`,
`vistest.pytest`, `vistest.plugins`). Without it there is nothing to plug in:
the CLI and the pytest plugin offer the library's half and, asked for the
server's, say so in one line — instead of argparse's `invalid choice` or a
traceback from somewhere deep inside.
"""

from __future__ import annotations

__all__ = ["NOT_INSTALLED"]

#: The one line about the server, for when it is wanted and not installed.
NOT_INSTALLED = "the server is the separate vistest-server package, not installed here"
