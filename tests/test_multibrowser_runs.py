# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Прогон чужого набора в нескольких браузерах.

Главное здесь — первый тест. Ключ платформы у прогона проекта считался как
`platform_key()`, без аргументов, то есть всегда «…-chromium-…». Прогон того же
проекта в firefox складывал свои кадры в набор chromium и с ним же сравнивался.
Отрисовка шрифтов в этих движках физически разная, так что падало всё подряд; а
если эталоны сначала сняли под firefox, то chromium потом «чинил» их обратно.
Ни в одном логе это не написано: обе стороны уверены, что работают со своим
набором.

Дальше — про то, чем браузер вообще доходит до чужого кода. Своего браузера в
этом пути у нас нет, его поднимает их conftest, и единственный способ —
попросить их же код. Способов попросить два, и оба могут не сработать; тогда
честный ответ — отказ, а не три одинаковых прогона chromium с зелёным по всем.
"""

from __future__ import annotations

import pytest

from vistest.config import VisTestConfig, platform_key
from vistest.projects import Project


@pytest.fixture(autouse=True)
def _pretend_baselines_exist(monkeypatch):
    """Пустой набор эталонов — отдельный разговор, и он проверен в другом месте.

    Здесь проверяется, КАКОЙ набор выбран, а не что в нём лежит; без этой
    заглушки каждый тест падал бы на «эталоны ещё не сняты» и не доходил бы до
    собственного вопроса.
    """
    from vistest import external

    monkeypatch.setattr(external, "has_own_baselines", lambda *a, **k: True)


def _project(tmp_path, **over) -> Project:
    root = tmp_path / "their-repo"
    (root / "tests").mkdir(parents=True, exist_ok=True)
    return Project(key="shop", name="Shop", root=str(root), tests="tests",
                   baseline_store="vistest", **over)


# --------------------------------------------------------------------------- #
#  Ключ платформы
# --------------------------------------------------------------------------- #
def test_a_firefox_run_uses_the_firefox_baseline_set(tmp_path, monkeypatch):
    """Иначе firefox сравнивается с эталонами chromium — и оба об этом молчат."""
    monkeypatch.setenv("VISTEST_ROOT", str(tmp_path / ".vistest"))
    cfg = VisTestConfig()
    cfg.paths.root = str(tmp_path / ".vistest")
    project = _project(tmp_path)

    chromium = project.vistest_baselines_path(cfg, platform_key("chromium"))
    firefox = project.vistest_baselines_path(cfg, platform_key("firefox"))
    assert chromium != firefox, "наборы браузеров обязаны быть разными каталогами"

    from vistest import external

    # Прогон обрывается на сборе тестов (их conftest тут пустой каталог) — и
    # это не мешает: проверяется, КАКОЙ набор эталонов он выбрал, а выбор
    # сделан до всякого pytest.
    run = external.run_project(project, cfg=cfg, browser="firefox",
                               log=lambda *_a: None)
    assert run.browser == "firefox"
    assert run.baseline_dir == str(firefox), \
        "прогон firefox ушёл в набор другого браузера"
    assert str(chromium) not in run.baseline_dir


def test_a_run_without_a_browser_behaves_exactly_as_before(tmp_path, monkeypatch):
    """Не назвали браузер — ничего не изменилось: выбирает их conftest."""
    cfg = VisTestConfig()
    cfg.paths.root = str(tmp_path / ".vistest")
    project = _project(tmp_path)

    from vistest import external

    run = external.run_project(project, cfg=cfg, log=lambda *_a: None)
    assert run.browser == ""
    assert run.baseline_dir == str(
        project.vistest_baselines_path(cfg, platform_key("chromium")))
    # И ничего про браузер их pytest не получает: выбор остаётся за ними.
    env, args = external._browser_env_and_args(project, "", log=lambda *_a: None)
    assert args == []


def test_three_browsers_started_at_once_do_not_overwrite_each_other(tmp_path,
                                                                    monkeypatch):
    """`run_key` состоял из секунды, а приём сделан на `INSERT OR REPLACE`.

    Три прогона одного проекта стартуют в одну секунду штатно — это ровно то,
    что делает кнопка «по одному на браузер», — и второй молча стирал бы первый
    вместе со всеми его сравнениями.
    """
    cfg = VisTestConfig()
    cfg.paths.root = str(tmp_path / ".vistest")
    project = _project(tmp_path)

    from vistest import external

    keys = {external.run_project(project, cfg=cfg, browser=b,
                                 log=lambda *_a: None).run_dir
            for b in ("chromium", "firefox", "webkit")}
    assert len(keys) == 3, f"прогоны разных браузеров слиплись: {keys}"


# --------------------------------------------------------------------------- #
#  Чем браузер доходит до их кода
# --------------------------------------------------------------------------- #
def test_the_env_variable_is_always_set(tmp_path, monkeypatch):
    """Дешёвая страховка: проекту со своим разбором аргументов её достаточно.

    Ему не нужно ни нашего согласия, ни настройки — просто прочитать переменную.
    """
    from vistest import external

    project = _project(tmp_path, browser_option="env")
    env, args = external._browser_env_and_args(project, "webkit",
                                               log=lambda *_a: None)
    assert env["VISTEST_BROWSER"] == "webkit" and args == []


def test_the_flag_is_added_only_where_it_exists(tmp_path, monkeypatch):
    """`--browser` вводит pytest-playwright. Без него pytest падает на
    «unrecognized arguments» — то есть многобраузерный прогон убивал бы набор,
    который прекрасно работает."""
    from vistest import external

    project = _project(tmp_path)          # browser_option="auto"
    external._PW_PROBE.clear()
    monkeypatch.setattr(external, "_has_pytest_playwright", lambda _p: True)
    _env, args = external._browser_env_and_args(project, "firefox",
                                                log=lambda *_a: None)
    assert args == ["--browser", "firefox"]

    monkeypatch.setattr(external, "_has_pytest_playwright", lambda _p: False)
    _env, args = external._browser_env_and_args(project, "firefox",
                                                log=lambda *_a: None)
    assert args == [], "флаг добавлен туда, где его не существует"


def test_a_project_that_names_its_own_browser_is_not_argued_with(tmp_path):
    """Второй флаг спорил бы с их настройкой, и чья возьмёт — зависит от порядка."""
    from vistest import external

    project = _project(tmp_path, pytest_args=["--browser", "webkit"])
    _env, args = external._browser_env_and_args(project, "firefox",
                                                log=lambda *_a: None)
    assert args == []


# --------------------------------------------------------------------------- #
#  Честный отказ
# --------------------------------------------------------------------------- #
def test_a_project_run_by_its_own_command_refuses_instead_of_lying(tmp_path):
    """Прогон «во всех браузерах», который трижды гонит один, — худший исход.

    Он пишет три набора эталонов, показывает по ним зелёное и создаёт
    уверенность в покрытии, которого нет.
    """
    project = _project(tmp_path, runner="command",
                       command=["npx", "playwright", "test"])
    assert project.browser_control() == "none"
    assert "своей командой" in project.browser_refusal()


def test_control_can_be_switched_off_deliberately(tmp_path):
    project = _project(tmp_path, browser_option="none")
    assert project.browser_control() == "none"
    assert "browser_option=none" in project.browser_refusal()


def test_a_pytest_project_allows_it_by_default(tmp_path):
    project = _project(tmp_path)
    assert project.browser_control() == "auto"
    assert project.browser_refusal() == ""


def test_an_unknown_browser_option_is_reported_by_validate(tmp_path):
    project = _project(tmp_path, browser_option="magic")
    assert any("browser_option" in p for p in project.validate())


# --------------------------------------------------------------------------- #
#  Разбор тела запроса
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("payload,browsers,mode", [
    ({}, [], ""),
    ({"browser": "firefox"}, ["firefox"], ""),
    ({"browsers": ["chromium", "firefox"]}, ["chromium", "firefox"], "together"),
    ({"browsers": ["chromium", "firefox"], "mode": "separate"},
     ["chromium", "firefox"], "separate"),
    # Дубликаты схлопываются: повтор в списке из трёх строк — невнимательность,
    # а не ошибка описания.
    ({"browsers": ["firefox", "firefox"]}, ["firefox"], ""),
])
def test_the_request_body_reads_as_three_different_questions(payload, browsers, mode):
    from vistest.api.projects import parse_run_browsers

    assert parse_run_browsers(payload) == (browsers, mode)


def test_an_unknown_browser_in_the_body_is_a_400(tmp_path):
    from fastapi import HTTPException

    from vistest.api.projects import parse_run_browsers

    with pytest.raises(HTTPException) as e:
        parse_run_browsers({"browsers": ["safari"]})
    assert e.value.status_code == 400 and "safari" in str(e.value.detail)
