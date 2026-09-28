# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""`storage.atomic` against a replace that Windows refuses.

Every file the library mode writes — the actual picture, the diff, the
baseline under `--vistest-update`, the report's parts — goes through
`atomic.write_bytes`. On Windows its `os.replace` fails with WinError 5 while
another process holds the target or is replacing it at the same moment, which
is what two tests sharing a snapshot name do under `pytest -n`, and what an
antivirus does to a PNG that has just appeared.

The refusal is simulated here rather than provoked, so these tests run on any
platform: `os.replace` is swapped for one that refuses a given number of times,
and the platform switch is the module's own `RETRY_REPLACE`. The file is named
`test_library_*` so that the Windows job, which runs the library's tests, runs
these too.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from vistest.storage import atomic

REAL_REPLACE = os.replace


class Refusing:
    """`os.replace` that says «access denied» the first `times` times."""

    def __init__(self, times: float):
        self.times = times
        self.calls = 0

    def __call__(self, src, dst):
        self.calls += 1
        if self.calls <= self.times:
            raise PermissionError(13, "Access is denied", str(dst))
        return REAL_REPLACE(src, dst)


def leftovers(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.iterdir() if p.name.endswith(".tmp"))


@pytest.fixture
def on_windows(monkeypatch):
    monkeypatch.setattr(atomic, "RETRY_REPLACE", True)


def test_a_replace_refused_twice_goes_through_on_the_third(tmp_path, monkeypatch,
                                                           on_windows):
    replace = Refusing(times=2)
    monkeypatch.setattr(atomic.os, "replace", replace)
    target = tmp_path / "shared.png"
    target.write_bytes(b"what the other writer left")

    assert atomic.write_bytes(target, b"ours") == target

    assert replace.calls == 3
    assert target.read_bytes() == b"ours"
    assert leftovers(tmp_path) == []


def test_a_replace_that_never_goes_through_gives_up_at_the_limit(tmp_path,
                                                                 monkeypatch,
                                                                 on_windows):
    """The original error, no temporary left, and not a moment much past the limit.

    The lower bound is the point as much as the upper one: giving up after the
    first refusal would pass a test that only checked for the exception.
    """
    replace = Refusing(times=float("inf"))
    monkeypatch.setattr(atomic.os, "replace", replace)
    target = tmp_path / "shared.png"

    started = time.monotonic()
    with pytest.raises(PermissionError) as refused:
        atomic.write_bytes(target, b"ours")
    elapsed = time.monotonic() - started

    assert refused.value.errno == 13
    assert refused.value.filename == str(target)
    assert replace.calls > 2
    assert atomic.REPLACE_RETRY_SECONDS <= elapsed < atomic.REPLACE_RETRY_SECONDS + 1.0
    assert not target.exists()
    assert leftovers(tmp_path) == []


def test_off_windows_the_first_refusal_is_raised_at_once(tmp_path, monkeypatch):
    """On POSIX a PermissionError is about permissions; waiting will not fix it."""
    monkeypatch.setattr(atomic, "RETRY_REPLACE", False)
    replace = Refusing(times=1)
    monkeypatch.setattr(atomic.os, "replace", replace)

    started = time.monotonic()
    with pytest.raises(PermissionError):
        atomic.write_bytes(tmp_path / "shared.png", b"ours")

    assert replace.calls == 1
    assert time.monotonic() - started < 0.5
    assert leftovers(tmp_path) == []


def test_only_a_refusal_is_retried(tmp_path, monkeypatch, on_windows):
    """Anything else will not go away by waiting, on Windows either."""
    calls = []

    def missing(src, dst):
        calls.append(dst)
        raise FileNotFoundError(2, "The system cannot find the path specified",
                                str(dst))

    monkeypatch.setattr(atomic.os, "replace", missing)
    with pytest.raises(FileNotFoundError):
        atomic.write_bytes(tmp_path / "shared.png", b"ours")

    assert len(calls) == 1
    assert leftovers(tmp_path) == []


def test_the_switch_follows_the_platform():
    assert atomic.RETRY_REPLACE is (sys.platform == "win32")
