# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""What `import vistest` offers at the top: the library and nothing else (review v1, R1).

The server's classes used to be exported next to `expect_screenshot` — the
runner, the check service, the stores, the config loader — so the first thing
a newcomer saw in `dir(vistest)` was the server. They are reached by their
full path now; a name that left the top says where it went, in one line, and
there is no shim that keeps it working with a warning.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
import warnings
from pathlib import Path

import pytest

import vistest

LIBRARY = {"expect_screenshot", "BaselineMissing", "ScreenshotMismatch",
           "VisualCheckError", "CaptureError", "VisTestWarning", "CompareResult",
           "Verdict", "compare"}

#: Every name that was exported at the top before 0.2.0.dev3, and its module.
MOVED = {
    "VisTestConfig": "vistest.config",
    "VisualSession": "vistest.integrations",
    "VisualTestCase": "vistest.integrations",
    "visual_check": "vistest.integrations",
    "visual_session": "vistest.integrations",
    "VisualTester": "vistest.runner",
    "VisualMismatch": "vistest.runner",
    "CheckService": "vistest.service",
    "FileStore": "vistest.storage",
    "SnapshotKey": "vistest.storage",
    "SnapshotMeta": "vistest.storage",
    "SnapshotStore": "vistest.storage",
    "ChangeKind": "vistest.models",
    "DiffRegion": "vistest.models",
}


def test_the_top_of_the_package_is_the_library():
    assert set(vistest.__all__) == LIBRARY
    assert len(vistest.__all__) == len(LIBRARY)


def test_every_library_name_resolves_and_capture_error_is_the_librarys():
    for name in LIBRARY:
        assert getattr(vistest, name) is not None, name
    from vistest.library.errors import CaptureError

    assert vistest.CaptureError is CaptureError


@pytest.mark.parametrize("name, module", sorted(MOVED.items()))
def test_a_name_that_left_the_top_says_in_one_line_where_it_is(name, module):
    with warnings.catch_warnings():
        warnings.simplefilter("error")          # no DeprecationWarning shim
        with pytest.raises(AttributeError) as gone:
            getattr(vistest, name)
    text = str(gone.value)
    assert "\n" not in text, text
    assert f"from {module} import {name}" in text, text
    #  ... and it is really there.
    assert getattr(importlib.import_module(module), name) is not None
    assert not hasattr(vistest, name)
    assert name not in dir(vistest)


def test_a_name_nobody_ever_exported_is_a_plain_attribute_error():
    with pytest.raises(AttributeError, match=r"^module 'vistest' has no attribute 'nope'$"):
        vistest.nope  # noqa: B018


def test_from_vistest_import_a_moved_name_is_pythons_import_error():
    """`from vistest import X` turns the AttributeError into Python's own
    ImportError; the full paths are in the CHANGELOG."""
    done = subprocess.run([sys.executable, "-c", "from vistest import CheckService"],
                          cwd=Path(vistest.__file__).parents[1], capture_output=True,
                          encoding="utf-8", timeout=120)
    assert done.returncode == 1
    assert "ImportError: cannot import name 'CheckService' from 'vistest'" in done.stderr


def test_a_star_import_brings_only_the_library():
    done = subprocess.run(
        [sys.executable, "-c",
         "from vistest import *; print(sorted(n for n in dir() if not n.startswith('_')))"],
        cwd=Path(vistest.__file__).parents[1], capture_output=True, encoding="utf-8",
        timeout=120)
    assert done.returncode == 0, done.stderr
    assert set(eval(done.stdout)) == LIBRARY  # noqa: S307 - our own repr of a list
