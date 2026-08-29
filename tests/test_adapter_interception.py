# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Перехват сравнения в чужих тестах.

Отдельный файл, потому что плагин живёт в чужом процессе и настраивается
переменными окружения: это единственный канал, который переживает запуск чужого
pytest. Проверяется здесь то, из-за чего снимки не доезжали до отчёта.
"""

from __future__ import annotations

import json
import sys
import textwrap

import pytest

from vistest import adapter


@pytest.fixture
def their_pages(tmp_path, monkeypatch):
    """Мини-проект: базовая страница и наследник со своим методом."""
    pkg = tmp_path / "demo"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "pages.py").write_text(textwrap.dedent("""
        class BasePage:
            def assert_screenshot(self, name, **kw):
                return ("base", name)

        class Login(BasePage):
            def assert_screenshot(self, name, **kw):
                return ("login", name)

        class Plain(BasePage):
            pass
    """), encoding="utf-8")

    monkeypatch.syspath_prepend(str(tmp_path))
    for mod in ("demo", "demo.pages"):
        sys.modules.pop(mod, None)
    monkeypatch.setenv("VISTEST_ADAPTER_TARGET", "demo.pages.BasePage.assert_screenshot")
    monkeypatch.setenv("VISTEST_ADAPTER_RUN", str(tmp_path / "run"))
    monkeypatch.delenv("VISTEST_ADAPTER_NAME_ARG", raising=False)

    adapter.pytest_configure(None)
    import demo.pages as pages

    yield pages
    for mod in ("demo", "demo.pages"):
        sys.modules.pop(mod, None)


def test_overriding_subclasses_are_intercepted_too(their_pages):
    """Свой `assert_screenshot` у страницы — не повод пройти мимо отчёта."""
    assert adapter._STATE["errors"] == []
    assert set(adapter._STATE["patched_classes"]) == {"BasePage", "Login"}
    assert getattr(their_pages.BasePage.assert_screenshot, "__vistest_patched__", False)
    assert getattr(their_pages.Login.assert_screenshot, "__vistest_patched__", False)


def test_patching_is_not_applied_twice(their_pages):
    adapter.pytest_configure(None)          # повторный запуск в том же процессе
    once = their_pages.BasePage.assert_screenshot
    adapter.pytest_configure(None)
    assert their_pages.BasePage.assert_screenshot is not None
    assert once.__wrapped__.__name__ == "assert_screenshot"


def test_failed_interception_still_lands_in_the_report(their_pages):
    """Молча потерянный снимок — худший исход: прогон чистый, только короче."""
    adapter._STATE["results"].clear()
    their_pages.Login().assert_screenshot("login.png")   # страницы у объекта нет

    assert [(r["name"], r["verdict"]) for r in adapter._STATE["results"]] \
        == [("login.png", "error")]
    assert adapter._STATE["errors"]


def test_unrecognized_name_is_reported_not_swallowed(their_pages):
    adapter._STATE["errors"].clear()
    their_pages.Plain().assert_screenshot(object())      # имя — не строка

    assert any("snapshot name" in e for e in adapter._STATE["errors"])


@pytest.mark.parametrize("args, kwargs, expected", [
    (("login.png",), {}, "login.png"),
    ((), {"name": "a.png"}, "a.png"),
    ((), {"screenshot_name": "b.png"}, "b.png"),
    ((object(), "only-string.png"), {}, "only-string.png"),
    ((object(), object()), {}, ""),
])
def test_snapshot_name_resolution(args, kwargs, expected, monkeypatch):
    monkeypatch.delenv("VISTEST_ADAPTER_NAME_ARG", raising=False)
    assert adapter._snapshot_name(args, kwargs) == expected


def test_broken_name_arg_does_not_cost_the_run(monkeypatch):
    """Неверная позиция в настройках — не повод потерять снимок."""
    monkeypatch.setenv("VISTEST_ADAPTER_NAME_ARG", "не число")
    assert adapter._snapshot_name(("login.png",), {}) == "login.png"


def test_report_survives_values_that_do_not_serialize(tmp_path, monkeypatch):
    """Один нечитаемый float не должен уносить с собой весь отчёт."""
    monkeypatch.setenv("VISTEST_ADAPTER_RUN", str(tmp_path))
    adapter._reset_state()
    adapter._STATE["collected"] = 2
    adapter._STATE["results"].append({"name": "x.png", "verdict": "pass",
                                      "metrics": {"ssim": object()}})
    adapter.pytest_sessionfinish(None, 1)

    data = json.loads((tmp_path / "adapter.json").read_text("utf-8"))
    assert data["collected"] == 2
    assert data["results"][0]["name"] == "x.png"


_LONGREPR = '''context = <BrowserContext browser=<Browser name=chromium>>
user_login = 'Autotests', user_pass = None
base_url = 'https://stand.example/'

    @pytest.fixture(scope="function")
    def logged_in_page(context, user_login, user_pass, base_url) -> Page:
        page = context.new_page()
>       login_page.login(user_login, user_pass)

conftest.py:428:
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _
self = <Locator selector='input[name="password"]'>
value = None, timeout = None, noWaitAfter = None, force = None

    async def fill(self, value: str) -> None:
>       return await self._frame.fill(self._selector, strict=True, **params)
E       TypeError: Frame.fill() missing 1 required positional argument: 'value'

/usr/local/lib/python3.10/dist-packages/playwright/_impl/_locator.py:208: TypeError'''


def test_cause_is_one_line_not_the_whole_traceback():
    assert adapter._exception_line(_LONGREPR) == (
        "TypeError: Frame.fill() missing 1 required positional argument: 'value'")


def test_empty_fixtures_are_taken_from_the_outermost_frame_only():
    """`value = None` внутри Playwright — не фикстура проекта."""
    assert adapter._empty_arguments(_LONGREPR) == ["user_pass"]


def test_setup_failure_carries_the_diagnosis(tmp_path, monkeypatch):
    monkeypatch.setenv("VISTEST_ADAPTER_RUN", str(tmp_path))
    adapter._reset_state()

    class Report:
        nodeid, when, outcome = "t.py::a", "setup", "failed"
        longrepr = _LONGREPR

    adapter.pytest_runtest_logreport(Report())
    entry = adapter._STATE["tests"][0]
    assert entry["phase"] == "setup"
    assert entry["empty_args"] == ["user_pass"]
    assert entry["error"].startswith("TypeError: Frame.fill()")


def test_test_outcomes_are_recorded(tmp_path, monkeypatch):
    monkeypatch.setenv("VISTEST_ADAPTER_RUN", str(tmp_path))
    adapter._reset_state()

    class Report:
        def __init__(self, nodeid, when, outcome):
            self.nodeid, self.when, self.outcome = nodeid, when, outcome
            self.longrepr = "boom"

    adapter.pytest_runtest_logreport(Report("t.py::a", "setup", "passed"))
    adapter.pytest_runtest_logreport(Report("t.py::a", "call", "passed"))
    adapter.pytest_runtest_logreport(Report("t.py::b", "call", "failed"))
    adapter.pytest_runtest_logreport(Report("t.py::c", "setup", "failed"))

    ids = [(t["id"], t["outcome"]) for t in adapter._STATE["tests"]]
    assert ids == [("t.py::a", "passed"), ("t.py::b", "failed"),
                   ("t.py::c", "failed")]
    assert adapter._STATE["tests"][1]["error"] == "boom"
