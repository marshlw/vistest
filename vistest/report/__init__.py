# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Оффлайн-отчёты: разбор прогона и накопленная статистика качества."""

from .evidence import collect_evidence, to_html, to_markdown, wilson
from .html import build_report, render_report

__all__ = ["build_report", "render_report",
           "collect_evidence", "to_markdown", "to_html", "wilson"]
