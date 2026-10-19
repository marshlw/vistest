# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""The offline report: one run, explained in one HTML file.

Quality statistics over the history of runs, the PR comment and the CI
decision are the server's.
"""

from .html import build_report, render_report

__all__ = ["build_report", "render_report"]
