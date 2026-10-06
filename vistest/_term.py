# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Colours for the terminal, and only for the terminal.

The commands used to write escape codes whatever stdout was: piped into a
file or read in a CI log, `vistest doctor` came out as `[90m…[0m`. Colour is
now given only when stdout is a terminal, never when `NO_COLOR` is set to
anything (https://no-color.org), and always when `FORCE_COLOR` is.

`C["g"]` and its siblings are read at the moment they are used, so a stream
redirected after import is still seen as it is.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator, Mapping

__all__ = ["C", "CODES", "Colours"]

CODES = {"g": "\033[32m", "y": "\033[33m", "r": "\033[31m", "b": "\033[34m",
         "d": "\033[90m", "0": "\033[0m"}


class Colours(Mapping):
    """`C["r"]` — the escape code when colour is on, "" when it is not.

    A real mapping: `dict.fromkeys(C, "")` and `C.get(...)` work as they did
    on the plain dict this replaces. (Without `__iter__`, Python iterates a
    class with only `__getitem__` by asking for 0, 1, 2, … for ever.)
    """

    def __init__(self, stream=None) -> None:
        self._stream = stream

    def enabled(self) -> bool:
        if os.environ.get("NO_COLOR"):
            return False
        if os.environ.get("FORCE_COLOR"):
            return True
        stream = self._stream if self._stream is not None else sys.stdout
        isatty = getattr(stream, "isatty", None)
        try:
            return bool(callable(isatty) and isatty())
        except (ValueError, OSError):  # a closed stream
            return False

    def __getitem__(self, key: str) -> str:
        code = CODES[key]
        return code if self.enabled() else ""

    def __iter__(self) -> Iterator[str]:
        return iter(CODES)

    def __len__(self) -> int:
        return len(CODES)


C = Colours()
