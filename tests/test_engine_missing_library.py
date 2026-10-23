# VisTest - visual regression testing for Python and Playwright.
# Copyright 2026 Kirill Kulagin
# SPDX-License-Identifier: Apache-2.0
#
# This file is part of VisTest, licensed under the Apache License, Version 2.0.
# You may not use it except in compliance with the License. See LICENSE for
# the terms and NOTICE for attribution and the trademark.


"""What the library knows about the browsers that are installed here (o1c:
split from server/tests/test_engine_missing.py, which keeps the server's
refusal, its platform folders and its baselines card)."""

from __future__ import annotations

import pytest

from vistest import matrix


@pytest.fixture(autouse=True)
def _forget_probes():
    """The probe remembers its answer for the whole process; a test that
    fakes «firefox is not installed» must not leak it into the next one."""
    matrix._STATE.clear()
    yield
    matrix._STATE.clear()


# --------------------------------------------------------------------------- #
#  Что знает интерфейс
# --------------------------------------------------------------------------- #
def test_the_interface_is_told_which_engines_actually_exist(monkeypatch):
    """Один список «что бывает» — это и была причина всего остального."""
    monkeypatch.setattr(matrix, "_probe_here",
                        lambda: {"chromium": True, "firefox": False,
                                 "webkit": False})
    state = matrix.installed_browsers(recheck=True)
    assert state == {"chromium": matrix.INSTALLED,
                     "firefox": matrix.NOT_INSTALLED,
                     "webkit": matrix.NOT_INSTALLED}


def test_no_playwright_is_a_separate_answer_with_a_separate_command(monkeypatch):
    """Два диагноза — две команды. Склеить их значит отправить половину людей
    выполнять то, что им не поможет."""
    monkeypatch.setattr(matrix, "_probe_here", dict)
    state = matrix.installed_browsers(recheck=True)
    assert set(state.values()) == {matrix.NO_PLAYWRIGHT}
    said = matrix.install_hint("webkit", matrix.NO_PLAYWRIGHT)
    assert "pip install playwright" in said
    assert "playwright install webkit" in said


def test_a_missing_engine_at_launch_is_a_refusal_not_a_traceback():
    """Двадцать строк рамочки Playwright говорят «инструмент сломался» ровно
    там, где инструмент отработал правильно и честно назвал, чего не хватает."""
    class FakeType:
        def launch(self, **kw):
            raise RuntimeError(
                "BrowserType.launch: Executable doesn't exist at "
                "/opt/pw-browsers/firefox-1495/firefox/firefox")

    class FakePlaywright:
        firefox = FakeType()

    with pytest.raises(matrix.BrowserMissing) as got:
        matrix.launch(FakePlaywright(), "firefox", headless=True)
    assert "playwright install firefox" in str(got.value)


def test_a_launch_failure_of_any_other_kind_is_not_disguised():
    """Проглотить чужую ошибку под своим объяснением — худшее из возможного."""
    class FakeType:
        def launch(self, **kw):
            raise RuntimeError("Target page, context or browser has been closed")

    class FakePlaywright:
        webkit = FakeType()

    with pytest.raises(RuntimeError) as got:
        matrix.launch(FakePlaywright(), "webkit", headless=True)
    assert not isinstance(got.value, matrix.BrowserMissing)
