# VisTest - visual regression testing for Python and Playwright.
# Copyright 2026 Kirill Kulagin
# SPDX-License-Identifier: Apache-2.0
#
# This file is part of VisTest, licensed under the Apache License, Version 2.0.
# You may not use it except in compliance with the License. See LICENSE for
# the terms and NOTICE for attribution and the trademark.

"""Newest part wins even when the clock did not move between two checks.

On Windows `time.time()` can return the same value for two checks a few
milliseconds apart (CI, windows-latest, Python 3.11: a check that passed was
reported as the baseline it had just been accepted from). The parts' file
names are random, so with equal clocks the winner was a coin toss.
"""

from __future__ import annotations

import pytest

from vistest.report import library


@pytest.mark.parametrize("attempt", range(24))
def test_the_part_written_last_wins_on_a_frozen_clock(tmp_path, monkeypatch, attempt):
    monkeypatch.setattr(library.time, "time", lambda: 1_700_000_000.0)
    parts = tmp_path / f"parts{attempt}"
    parts.mkdir()
    library.write_part(parts, {"name": "one.png", "verdict": "new_baseline"})
    library.write_part(parts, {"name": "one.png", "verdict": "pass"})

    rows = library.read_parts(parts).entries
    assert [r["verdict"] for r in rows] == ["pass"]


def test_parts_without_seq_still_go_by_the_clock(tmp_path):
    import json

    (tmp_path / "a.json").write_text(json.dumps(
        {"name": "x.png", "verdict": "pass", "written_at": 2.0}), "utf-8")
    (tmp_path / "b.json").write_text(json.dumps(
        {"name": "x.png", "verdict": "fail", "written_at": 1.0}), "utf-8")
    assert [r["verdict"] for r in library.read_parts(tmp_path).entries] == ["pass"]
