# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Движка может не быть — и об этом надо сказать, а не оставить круг.

Как это выглядело у человека. Меню «▾» предлагало три движка, потому что
`known_browsers` — список того, что УМЕЕТ Playwright, а не того, что здесь
установлено. Он выбирал firefox, съёмка набора уходила в работу, pytest падал
строкой «Executable doesn't exist at …/firefox-1495/firefox/firefox» посреди
чужого вывода, набор оставался пустым. Следующий прогон отвечал «набор для
docker-firefox-1x ещё не снят — снимите его через ⋯ → Snap VisTest baselines»,
человек делал ровно это, и круг замыкался: снять набор в браузере, которого
нет, невозможно, а причина не была написана нигде.

Здесь проверяется, что круг разомкнут в трёх местах: отказ называет команду,
неудачная попытка не оставляет за собой пустой каталог платформы, и интерфейс
получает список того, что есть, а не только того, что бывает.
"""

from __future__ import annotations

import pytest

from vistest import matrix
from vistest.config import VisTestConfig, platform_key
from vistest.projects import Project


@pytest.fixture(autouse=True)
def _forget_probes():
    """Зонды помнят ответ на весь процесс — и это правильно в работе.

    В тестах это протекает: файл, подсунувший «firefox не установлен», молча
    ломал соседний файл, где firefox обязан работать. Поймано ровно так —
    два теста, зелёные поодиночке и красные в общем прогоне.
    """
    from vistest import external

    external._BROWSER_PROBE.clear()
    matrix._STATE.clear()
    yield
    external._BROWSER_PROBE.clear()
    matrix._STATE.clear()


def _project(tmp_path) -> Project:
    root = tmp_path / "their-repo"
    (root / "tests").mkdir(parents=True, exist_ok=True)
    return Project(key="shop", name="Shop", root=str(root), tests="tests",
                   baseline_store="vistest")


def _cfg(tmp_path) -> VisTestConfig:
    cfg = VisTestConfig()
    cfg.paths.root = str(tmp_path / ".vistest")
    return cfg


# --------------------------------------------------------------------------- #
#  Отказ вместо круга
# --------------------------------------------------------------------------- #
def test_a_run_in_an_engine_that_is_not_installed_says_so_and_says_the_command(
        tmp_path, monkeypatch):
    """Иначе причина остаётся в чужом выводе, а человеку достаётся круг.

    Проверяется именно ТЕКСТ: «engine is not installed» без команды — это та же
    загадка, вежливо сформулированная. `pip install playwright` уже выполнен,
    пакет на месте, и догадаться, что браузеры ставятся отдельной командой,
    можно только зная это заранее.
    """
    from vistest import external

    monkeypatch.setattr(external, "project_browser_state",
                        lambda p, b: matrix.NOT_INSTALLED)
    monkeypatch.setattr(external, "has_own_baselines", lambda *a, **k: True)

    with pytest.raises(external.ExternalRunRefused) as got:
        external.run_project(_project(tmp_path), cfg=_cfg(tmp_path),
                             browser="firefox", log=lambda *_a: None)

    said = str(got.value)
    assert "firefox" in said
    assert "playwright install firefox" in said, \
        "в отказе нет команды, которой это чинится"
    assert "not installed" in said


def test_a_project_without_playwright_at_all_is_not_refused(tmp_path, monkeypatch):
    """`no-playwright` — не то же самое, что «нет движка», и отказом быть не может.

    Чужой набор может гоняться selenium'ом или чем угодно ещё: адаптер
    цепляется за ИХ драйвер, а не за наш. Отказ здесь останавливал бы
    работающий набор по причине, которой у него нет.
    """
    from vistest import external

    monkeypatch.setattr(external, "project_browser_state",
                        lambda p, b: matrix.NO_PLAYWRIGHT)
    monkeypatch.setattr(external, "has_own_baselines", lambda *a, **k: True)

    run = external.run_project(_project(tmp_path), cfg=_cfg(tmp_path),
                               browser="firefox", log=lambda *_a: None)
    assert run.browser == "firefox"


def test_a_probe_that_could_not_answer_lets_the_run_through(tmp_path, monkeypatch):
    """Ложный отказ дороже молчания: не смогли спросить — запускаем."""
    from vistest import external

    project = _project(tmp_path)
    monkeypatch.setattr(external.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("no exe")))
    assert external.project_browser_state(project, "webkit") == matrix.INSTALLED


def test_the_probe_reads_the_projects_own_environment(tmp_path, monkeypatch):
    """Спрашивать надо ИХ интерпретатор, а не наш.

    Сервис вполне может стоять в образе без единого браузера (`Dockerfile.api`
    собран из `python:3.12-slim`), а проект при этом гоняться прекрасно — у
    него свой интерпретатор и своя установка Playwright.
    """
    from vistest import external

    project = _project(tmp_path)
    seen: list[list[str]] = []

    class Done:
        stdout = '{"chromium": true, "firefox": false, "webkit": false}'

    def fake(cmd, **kw):
        seen.append(cmd)
        return Done()

    monkeypatch.setattr(external.subprocess, "run", fake)

    assert external.project_browser_state(project, "chromium") == matrix.INSTALLED
    assert external.project_browser_state(project, "firefox") == matrix.NOT_INSTALLED
    assert len(seen) == 1, "зонд обязан спрашиваться один раз на интерпретатор"
    assert seen[0][0] == project.resolve_python()


# --------------------------------------------------------------------------- #
#  Фантомные наборы
# --------------------------------------------------------------------------- #
def test_a_refused_run_leaves_no_empty_platform_folder_behind(tmp_path, monkeypatch):
    """Каталог создавался ДО всех проверок — и переживал отказ.

    Дальше он попадал на карточку: «11 on docker-chromium-1x (11),
    docker-firefox-1x (0), docker-webkit-1x (0)». Читается это как «набор для
    firefox есть, но пуст», хотя его не снимали ни разу. Человек шёл
    разбираться, почему набор опустел, вместо того чтобы его снять.
    """
    from vistest import external

    cfg = _cfg(tmp_path)
    project = _project(tmp_path)
    firefox = project.vistest_baselines_path(cfg, platform_key("firefox"))

    monkeypatch.setattr(external, "project_browser_state",
                        lambda p, b: matrix.NOT_INSTALLED)
    with pytest.raises(external.ExternalRunRefused):
        external.run_project(project, cfg=cfg, browser="firefox",
                             log=lambda *_a: None)
    assert not firefox.exists(), "отказ оставил за собой пустой набор эталонов"

    # И отказ по пустому набору — тоже.
    monkeypatch.setattr(external, "project_browser_state",
                        lambda p, b: matrix.INSTALLED)
    with pytest.raises(external.ExternalRunRefused):
        external.run_project(project, cfg=cfg, browser="firefox",
                             log=lambda *_a: None)
    assert not firefox.exists()


# --------------------------------------------------------------------------- #
#  Что открывать на вкладке эталонов
# --------------------------------------------------------------------------- #
def test_the_card_points_at_a_set_that_has_something_in_it(tmp_path):
    """`current_platform` — платформа МАШИНЫ СЕРВИСА, то есть всегда chromium.

    Карточка выбирала по нему набор для «Open VisTest snapshots» и уводила на
    вкладку эталонов, прибив платформу к chromium. Снаружи это и было «снятые
    эталоны не появляются в эталонах»: они там были, просто открывался чужой
    набор — и `state.platform` держался после этого до конца сессии.
    """
    from vistest.external import baseline_status

    cfg = _cfg(tmp_path)
    project = _project(tmp_path)
    base = project.vistest_baselines_path(cfg)

    # Пустой каталог chromium (наследство прежних отказов) и настоящий firefox.
    (base / platform_key("chromium")).mkdir(parents=True)
    shot = base / platform_key("firefox") / "checkout"
    shot.mkdir(parents=True)
    (shot / "baseline.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    status = baseline_status(project, cfg)
    assert status["current_platform"] == platform_key(), "поведение не менялось"
    assert status["best_platform"] == platform_key("firefox"), \
        "карточка предлагает открыть пустой набор вместо снятого"


def test_with_nothing_captured_the_card_points_nowhere(tmp_path):
    """Пусто — значит пусто: выдумывать платформу нельзя."""
    from vistest.external import baseline_status

    status = baseline_status(_project(tmp_path), _cfg(tmp_path))
    assert status["best_platform"] == ""


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
