# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Быстрое заведение эталонов: интерактивная запись и снимки по URL."""

from .codegen import generate_test_file
from .session import Recorder, run_recorder
from .snap import snap_urls

__all__ = ["Recorder", "run_recorder", "snap_urls", "generate_test_file"]
