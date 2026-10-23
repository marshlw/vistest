# VisTest - visual regression testing for Python and Playwright.
# Copyright 2026 Kirill Kulagin
# SPDX-License-Identifier: Apache-2.0
#
# This file is part of VisTest, licensed under the Apache License, Version 2.0.
# You may not use it except in compliance with the License. See LICENSE for
# the terms and NOTICE for attribution and the trademark.

"""The offline report: one run, explained in one HTML file.

Quality statistics over the history of runs, the PR comment and the CI
decision are the server's.
"""

from .html import build_report, render_report

__all__ = ["build_report", "render_report"]
