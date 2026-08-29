# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Чем снят снимок — и чем его перепроверять.

В паспорте эталона был `url`, и по нему в интерфейсе появлялись «Check» и
«Re-capture». Работали они честно ровно для страниц, открытых с улицы: грузили
адрес и снимали. Всё, что лежит за входом — а это большинство страниц любого
внутреннего продукта, — они снимали уже как страницу входа. Человек получал
прогон, который «ничего не нашёл», или эталон, переписанный формой логина, и
заметить это можно было, только открыв картинку.

Причина: снимок не помнил, откуда взялся. А взяться он мог из теста, который
знает и про логин, и про переходы, — и этот тест был известен ровно в момент
съёмки.
"""

from __future__ import annotations

import pytest

from vistest import source


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("VISTEST_PROJECT_KEY", raising=False)


def _outside_a_test(monkeypatch):
    """Убрать `PYTEST_CURRENT_TEST` — и убрать именно здесь, в теле теста.

    В фикстуре бесполезно: pytest ставит эту переменную заново на каждой фазе, и
    к моменту `call` она снова на месте. Это же — доказательство того, почему
    сервис обязан получать источник явно, а не вынюхивать его у себя: сервис,
    запущенный под pytest (а в этом прогоне так и есть), приписал бы снимку
    совершенно посторонний тест и выглядел бы убедительно.
    """
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)


# --------------------------------------------------------------------------- #
#  Опознание теста
# --------------------------------------------------------------------------- #
def test_the_test_is_taken_from_pytest_itself(monkeypatch):
    """`PYTEST_CURRENT_TEST` — единственный канал, который работает и у чужих.

    Адаптер подключается к чужому pytest снаружи и не знает ничего про их
    фикстуры и базовые классы. Переменную при этом ставит сам pytest.
    """
    monkeypatch.setenv("PYTEST_CURRENT_TEST",
                       "tests/test_login.py::test_form (call)")
    got = source.detect()
    assert got["kind"] == "test"
    assert got["test"] == "tests/test_login.py::test_form"
    assert got["file"] == "tests/test_login.py"
    assert got["case"] == "test_form"


def test_the_phase_is_not_part_of_the_test(monkeypatch):
    """Снимок из фикстуры и снимок из тела — это один и тот же тест.

    Оставить хвост `(setup)` значило бы завести два разных источника у одного
    теста и не суметь запустить ни один: такого идентификатора pytest не знает.
    """
    for phase in ("setup", "call", "teardown"):
        monkeypatch.setenv("PYTEST_CURRENT_TEST", f"a.py::test_b ({phase})")
        assert source.detect()["test"] == "a.py::test_b"


def test_a_parametrised_test_keeps_its_parameters(monkeypatch):
    """`test_page[mobile]` и `test_page[desktop]` снимают разные картинки."""
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "t.py::test_page[mobile] (call)")
    assert source.detect()["test"] == "t.py::test_page[mobile]"


def test_outside_a_test_the_source_is_the_address(monkeypatch):
    _outside_a_test(monkeypatch)
    assert source.detect()["kind"] == "url"
    assert "test" not in source.detect()


def test_the_project_key_travels_with_the_test(monkeypatch):
    """Идентификатора теста мало: надо знать, ГДЕ его искать.

    Чужой тест запускается чужим интерпретатором, из чужого корня и с их
    conftest — без ключа проекта «перепроверить» снова превращается в «загрузить
    адрес».
    """
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "t.py::test_a (call)")
    monkeypatch.setenv("VISTEST_PROJECT_KEY", "acme-ui-tests")
    assert source.detect()["project_key"] == "acme-ui-tests"


def test_a_recorded_snapshot_is_neither(monkeypatch):
    """Запись мышью идёт не из pytest, но и голым адресом не является."""
    got = source.detect("recorded")
    assert got["kind"] == "recorded"
    assert source.label(got) == "recorded with the mouse"


def test_an_unknown_kind_is_refused():
    with pytest.raises(ValueError):
        source.detect("whatever")


def test_only_a_test_source_can_be_repeated_by_a_test():
    assert source.runnable_by_test({"kind": "test", "test": "a.py::b"}) is True
    assert source.runnable_by_test({"kind": "test"}) is False
    assert source.runnable_by_test({"kind": "url"}) is False
    assert source.runnable_by_test(None) is False


def test_garbage_in_the_variable_is_not_a_test(monkeypatch):
    """Без `::` это не идентификатор теста, и делать вид, что это он, нельзя.

    Запуск по такому «идентификатору» pytest не поймёт, а кнопка уже пообещала.
    """
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "something odd")
    assert source.detect()["kind"] == "url"


# --------------------------------------------------------------------------- #
#  Запись в паспорт эталона
# --------------------------------------------------------------------------- #
def test_our_runner_records_the_test_that_captured_the_snapshot(monkeypatch,
                                                                tmp_path):
    """Это и есть связь, ради которой всё написано."""
    monkeypatch.setenv("VISTEST_ROOT", str(tmp_path / ".vistest"))
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/test_shop.py::test_cart (call)")

    from vistest.runner import VisualTester

    tester = VisualTester.__new__(VisualTester)
    tester.platform = "linux-chromium-1x"
    tester.browser = "chromium"
    meta = tester._baseline_meta()

    assert meta["source"]["kind"] == "test"
    assert meta["source"]["test"] == "tests/test_shop.py::test_cart"


def test_a_snapshot_captured_by_url_says_so(monkeypatch, tmp_path):
    _outside_a_test(monkeypatch)
    monkeypatch.setenv("VISTEST_ROOT", str(tmp_path / ".vistest"))
    from vistest.runner import VisualTester

    tester = VisualTester.__new__(VisualTester)
    tester.platform = "linux-chromium-1x"
    tester.browser = "chromium"
    assert tester._baseline_meta()["source"]["kind"] == "url"


# --------------------------------------------------------------------------- #
#  Запуск одного теста в чужом проекте
# --------------------------------------------------------------------------- #
def _project(tmp_path):
    from vistest.projects import Project

    return Project(key="shop", name="shop", root=str(tmp_path), tests="tests")


def test_one_test_replaces_the_suite_rather_than_joining_it(tmp_path):
    """Приписанный сбоку идентификатор дал бы pytest две цели.

    То есть «перепроверить один снимок» означало бы прогнать весь набор — и
    объяснить человеку, почему проверка одной картинки идёт двадцать минут,
    было бы нечем.
    """
    from vistest.external import _build_command

    project = _project(tmp_path)
    whole = _build_command(project, "adapter", None, exe="python")
    one = _build_command(project, "adapter", None, exe="python",
                         only="tests/test_shop.py::test_cart")

    assert "tests" in whole
    assert "tests" not in one
    assert "tests/test_shop.py::test_cart" in one


def test_the_project_key_reaches_the_run_environment(tmp_path):
    """Без него адаптер запишет тест, но не запишет, где его искать."""
    from vistest.config import VisTestConfig
    from vistest.external import _build_env

    env = _build_env(_project(tmp_path), VisTestConfig.load(), None)
    assert env["VISTEST_PROJECT_KEY"] == "shop"
