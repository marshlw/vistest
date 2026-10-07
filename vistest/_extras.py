# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Is the server's extra installed? Asked without importing any of it.

`pip install vistest` (or `vistest[browser]`) is the library; the review
interface, the API, the server-mode fixtures and the server's commands come
with `vistest[server]`. The pytest plugin and the CLI ask here before they
offer the server's half (review v1, R2 and R6), and say what to install when
it is not there — instead of `ModuleNotFoundError: No module named 'fastapi'`
from somewhere deep inside.

`importlib.util.find_spec` finds a module without running it, so the pytest
plugin, which is loaded in every pytest run of a project that has vistest
installed, pays a few path lookups for this and nothing more.
"""

from __future__ import annotations

import importlib.util

__all__ = ["SERVER_INSTALL", "server_missing"]

#: What to type when the server's half is wanted and missing.
SERVER_INSTALL = 'pip install "vistest[server]"'

#: The modules of the `server` extra (pyproject.toml), by their import names;
#: a tuple is one requirement that is importable under either name.
_SERVER_MODULES: tuple[str | tuple[str, ...], ...] = (
    "fastapi", "uvicorn", "requests", ("python_multipart", "multipart"))


def _importable(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def server_missing() -> list[str]:
    """The server extra's modules that cannot be imported here; empty when it is in."""
    missing = []
    for item in _SERVER_MODULES:
        names = item if isinstance(item, tuple) else (item,)
        if not any(_importable(n) for n in names):
            missing.append(names[0])
    return missing
