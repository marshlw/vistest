# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

from .hooks import RegionAnnotator, get_annotator, set_annotator
from .pipeline import AIPipeline

__all__ = ["AIPipeline", "RegionAnnotator", "get_annotator", "set_annotator"]
