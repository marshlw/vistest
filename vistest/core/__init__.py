# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""The comparison engine, and nothing else.

What is inside this package answers one question — how do these two pictures
differ and does that difference count as a failure — and answers it from its
arguments alone. It opens no database, reads no `vistest.yaml`, consults no
environment beyond one engine memory limit, and imports no web framework. The
guarantee is enforced by `tests/test_core_standalone.py`, which imports this
package in a clean process and fails if the service layer has come along.

The pieces:

* `comparator` — the cascade: alignment, ΔE00, SSIM, anti-alias filtering,
  segmentation into regions, classification, verdict;
* `align`, `antialias`, `color`, `segment`, `structure`, `classify` — the
  stages it is assembled from;
* `noise` — instability masks and masks from boxes;
* `regions` — ignore zones, bound to an element rather than to a rectangle;
* `naming` — how somebody else's snapshot files are named and which is which;
* `thresholds` — what is editable, what is valid, and which layer wins;
* `settings` — the plain dataclasses all of the above are configured with.

Everything that reads the world to fill those in — `vistest.config` for the
config file and the environment, `vistest.api.thresholds` for the database —
lives outside and passes the result in.
"""

from .comparator import ImageTooLarge, compare, strip_internal
from .naming import NamingProfile, Snapshot
from .regions import Resolved
from .settings import ConfigError, DiffConfig
from .thresholds import ThresholdError, ThresholdStore

__all__ = [
    "compare",
    "strip_internal",
    "ImageTooLarge",
    "DiffConfig",
    "ConfigError",
    "NamingProfile",
    "Snapshot",
    "Resolved",
    "ThresholdError",
    "ThresholdStore",
]
