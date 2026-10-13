# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""The server's baseline store writes through `storage.atomic` (plan, v5).

`storage/fs.py` had two helpers of its own: a temporary name made of the pid
alone, a bare `os.replace` with no patience on Windows, and a temporary left
behind when the replace failed. The refusal Windows gives while somebody holds
the target (WinError 5) is simulated, as in `test_library_atomic.py`, so these
run on any platform.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest

from vistest.storage import BaselineRecord, FileBaselineStore, atomic

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


def _record(value: int = 10) -> BaselineRecord:
    return BaselineRecord(name="shop/home.png",
                          image=np.full((6, 8, 3), value, dtype=np.uint8),
                          dom={"tag": "body"})


def _leftovers(root: Path) -> list[str]:
    return sorted(p.name for p in root.rglob("*") if p.is_file() and ".tmp" in p.name)


def test_a_refused_replace_is_retried_like_the_library(tmp_path, monkeypatch):
    """Windows refuses twice; the approval still lands, whole, with no debris."""
    monkeypatch.setattr(atomic, "RETRY_REPLACE", True)
    refusing = Refusing(2)
    monkeypatch.setattr(os, "replace", refusing)

    store = FileBaselineStore(tmp_path / "baselines")
    store.save(_record())

    assert refusing.calls > 2, "the store's writes go through atomic._replace"
    record = store.load("shop/home.png")
    assert record is not None and int(record.image[0, 0, 0]) == 10
    meta = json.loads((store.dir_for("shop/home.png") / "meta.json").read_text("utf-8"))
    assert meta["version"] == 1
    assert _leftovers(tmp_path) == []


def test_a_replace_that_keeps_failing_leaves_no_temporary(tmp_path, monkeypatch):
    """The error is raised as it came, and the temporary is not left behind."""
    monkeypatch.setattr(atomic, "RETRY_REPLACE", False)
    monkeypatch.setattr(os, "replace", Refusing(float("inf")))

    store = FileBaselineStore(tmp_path / "baselines")
    with pytest.raises(PermissionError):
        store.save(_record())
    assert _leftovers(tmp_path) == []


def test_temporaries_carry_more_than_the_pid(tmp_path, monkeypatch):
    """Two writes of one path in one process must not share a temporary name."""
    seen: list[str] = []

    def spy(src, dst):
        seen.append(Path(src).name)
        return REAL_REPLACE(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    store = FileBaselineStore(tmp_path / "baselines")
    store.save(_record(10))
    store.save(_record(20))
    names = [n for n in seen if "baseline.png" in n]
    assert len(names) == 2 and names[0] != names[1]
    assert all(n.startswith(".") for n in seen), seen
