# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

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
