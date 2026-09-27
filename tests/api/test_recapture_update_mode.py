# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Re-capture through our own pytest writes the baseline, passing or not.

`--vistest-update` takes a mode since R3, and a bare flag means `changed`:
only missing baselines and failed checks are rewritten. That is right for a
person accepting a run from the command line, and wrong for the Re-capture
button, which asks for exactly this picture to become the baseline.
"""

from __future__ import annotations

import pytest


class _Job:
    cancelled = False

    def __init__(self):
        self.said: list[str] = []

    def say(self, text, *_a, **_k):
        self.said.append(text)


@pytest.fixture
def own_pytest(tmp_path, monkeypatch):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_page.py").write_text("def test_page(): pass\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VISTEST_TESTS_DIR", str(tmp_path / "tests"))

    from vistest.api import baselines as mod

    seen: dict = {}

    def fake_run(job, path, extra, browser=""):
        seen["path"], seen["extra"] = path, list(extra)
        return {"exit_code": 0}

    monkeypatch.setattr(mod, "_run_pytest", fake_run)
    return mod, seen


def test_recapture_asks_for_every_baseline_not_only_the_failed_ones(own_pytest):
    mod, seen = own_pytest
    job = _Job()
    mod._run_one_test(job, "test_page.py::test_page", "", update=True)
    assert seen["extra"] == ["--vistest-update=all"]
    assert any("--vistest-update=all" in line for line in job.said)


def test_a_plain_check_passes_no_update_flag(own_pytest):
    mod, seen = own_pytest
    mod._run_one_test(_Job(), "test_page.py::test_page", "", update=False)
    assert seen["extra"] == []
