# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Пустой ответ обязан называть непустое место.

Человек подключил проект с одиннадцатью тестами и одиннадцатью эталонами и
получил три нуля подряд: пусто на вкладке «Tests», «0 baselines» на вкладке
«Baselines» и «baselines on the platform: 0 / None of the baselines has an
address set» в сборке тестов. Все три ответа были формально верны и все три
описывали не то место, куда он смотрел.

Причин ровно две, и обе — «умолчание, взятое не оттуда»:

* тестовым считался только `test_*.py`, хотя у pytest ДВА стандартных шаблона,
  и проект вправе задать свои в конфигурации;
* платформой по умолчанию бралась платформа МАШИНЫ СЕРВИСА, хотя эталоны
  снимает не она: сервис на windows, эталоны под `docker-chromium-1x`.

Здесь закреплено и то, что умолчания стали шире, и то, что пустой ответ
теперь называет каталог, шаблоны и соседний непустой набор.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from vistest.api.baselines import (
    PYTEST_DEFAULT_FILES,
    _baselines_elsewhere,
    _python_files,
    pick_platform,
)


# --------------------------------------------------------------------------- #
#  Шаблоны имён тестовых файлов
# --------------------------------------------------------------------------- #
def test_pytest_has_two_default_patterns_not_one(tmp_path):
    """`login_test.py` — такой же тест pytest, как и `test_login.py`.

    Именно на этом наборы, выросшие из другого раннера, и пропадали из списка.
    """
    patterns, source = _python_files(tmp_path)
    assert set(patterns) == set(PYTEST_DEFAULT_FILES)
    assert "test_*.py" in patterns and "*_test.py" in patterns
    assert source == "pytest defaults"


@pytest.mark.parametrize("name,text", [
    ("pytest.ini", "[pytest]\npython_files = screenshot_*.py check_*.py\n"),
    ("tox.ini", "[pytest]\npython_files = screenshot_*.py check_*.py\n"),
    ("setup.cfg", "[tool:pytest]\npython_files = screenshot_*.py check_*.py\n"),
    ("pyproject.toml",
     '[tool.pytest.ini_options]\npython_files = ["screenshot_*.py", "check_*.py"]\n'),
])
def test_the_projects_own_configuration_is_believed(tmp_path, name, text):
    """Гадать о шаблонах не надо: их пишут в конфигурации.

    Прочитать её дешевле, чем ошибиться, — и ошибка здесь молчаливая: список
    просто пуст, и выглядит это как «тестов нет».
    """
    (tmp_path / name).write_text(text, encoding="utf-8")
    patterns, source = _python_files(tmp_path)
    assert patterns == ["screenshot_*.py", "check_*.py"]
    assert source == name


def test_a_broken_configuration_falls_back_to_the_defaults(tmp_path):
    """Сломанный ini не должен стоить человеку списка тестов."""
    (tmp_path / "pytest.ini").write_text("[pytest\nthis is not ini at all",
                                         encoding="utf-8")
    patterns, source = _python_files(tmp_path)
    assert set(patterns) == set(PYTEST_DEFAULT_FILES)
    assert source == "pytest defaults"


# --------------------------------------------------------------------------- #
#  Обход каталога тестов проекта и отчёт о нём
# --------------------------------------------------------------------------- #
@pytest.fixture
def connected(tmp_path, monkeypatch):
    """Сервис с подключённым проектом, чьи тесты названы по второму шаблону."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VISTEST_ROOT", str(tmp_path / ".vistest"))
    monkeypatch.setenv("VISTEST_AUTH", "off")

    from vistest.api import baselines as mod
    from vistest.config import VisTestConfig
    from vistest.projects import Project, ProjectRegistry

    cfg = VisTestConfig.load()
    mod._cfg = cfg

    repo = tmp_path / "their-repo"
    suite = repo / "UiTests" / "tests" / "screenshot_tests"
    suite.mkdir(parents=True)
    (suite / "test_old.py").write_text("def test_a(): pass\n", encoding="utf-8")
    # Два теста в одном файле — иначе «файлы» и «тесты» неразличимы, а вся
    # путаница ровно в том, что это разные числа.
    (suite / "cart_test.py").write_text(
        "def test_b(): pass\n\nasync def test_b2(page):\n    pass\n",
        encoding="utf-8")
    (suite / "login_test.py").write_text(
        "def test_c(): pass\n\ndef helper():\n    pass\n", encoding="utf-8")
    (suite / "helpers.py").write_text("X = 1\n", encoding="utf-8")

    ProjectRegistry(cfg).save(Project(
        key="acme", name="Acme", root=str(repo),
        tests="UiTests/tests/screenshot_tests"))

    app = FastAPI()
    app.include_router(mod.router)
    return TestClient(app), mod, repo


def test_files_named_by_the_second_pattern_are_found(connected):
    """То, чего не было: `cart_test.py` и `login_test.py` в списке."""
    _, mod, _ = connected
    found = [t["short"] for t in mod._project_tests()]
    assert sorted(found) == ["cart_test.py", "login_test.py", "test_old.py"]
    # Обычный модуль тестом не считается — иначе список станет файловым
    # менеджером, а он им быть не должен.
    assert "helpers.py" not in found


def test_the_scan_says_where_it_looked_and_with_what(connected):
    """Отчёт о поиске — не диагностика ради диагностики.

    Пустой список имеет три причины: каталог не тот, шаблоны не те, файлов
    правда нет. Лечатся по-разному, выглядят одинаково.
    """
    _, mod, repo = connected
    _, notes = mod._project_test_scan()
    assert len(notes) == 1
    note = notes[0]
    assert note["project"] == "acme"
    assert note["dir"].endswith("screenshot_tests")
    assert note["exists"] is True
    assert note["found"] == 3
    assert set(note["patterns"]) == {"test_*.py", "*_test.py"}
    assert note["patterns_from"] == "pytest defaults"


def test_a_wrong_directory_is_reported_as_wrong_not_as_empty(connected, tmp_path):
    """«Каталога нет» и «файлов нет» — разные беды с разными действиями."""
    _, mod, _ = connected
    from vistest.config import VisTestConfig
    from vistest.projects import Project, ProjectRegistry

    cfg = VisTestConfig.load()
    registry = ProjectRegistry(cfg)
    project = registry.get("acme")
    registry.save(Project(key=project.key, name=project.name,
                          root=project.root, tests="no/such/dir"))

    files, notes = mod._project_test_scan()
    assert files == []
    assert notes[0]["exists"] is False
    assert notes[0]["found"] == 0


def test_the_tests_route_carries_the_report_to_the_interface(connected):
    """Без этого поля экран может сказать только «No tests yet»."""
    client, _, _ = connected
    body = client.get("/api/tests").json()
    assert [t["short"] for t in body["project_tests"]]
    assert body["project_notes"] and body["project_notes"][0]["found"] == 3


# --------------------------------------------------------------------------- #
#  Файлы и тесты — разные числа
# --------------------------------------------------------------------------- #
def test_each_file_says_how_many_tests_are_in_it(connected):
    """Человек считает ТЕСТЫ, список показывает ФАЙЛЫ.

    Шесть строк против одиннадцати эталонов выглядят как потеря, хотя это
    одиннадцать тестов в шести файлах. Пока названо только одно из двух чисел,
    разницу не с чем сверить.
    """
    _, mod, _ = connected
    by_name = {t["short"]: t["tests"] for t in mod._project_tests()}
    assert by_name == {"test_old.py": 1, "cart_test.py": 2, "login_test.py": 1}


def test_a_function_that_is_not_a_test_is_not_counted(connected):
    """`helper()` рядом с тестом — не тест, и счётчик обязан это знать."""
    _, mod, _ = connected
    files = {t["short"]: t for t in mod._project_tests()}
    assert files["login_test.py"]["tests"] == 1


def test_the_scan_totals_the_tests_not_only_the_files(connected):
    _, mod, _ = connected
    _, notes = mod._project_test_scan()
    assert notes[0]["found"] == 3        # файлов
    assert notes[0]["tests"] == 4        # тестов в них


def test_the_projects_own_naming_of_test_functions_is_believed(connected):
    """`python_functions` настраивается так же, как `python_files`."""
    _, mod, repo = connected
    (repo / "pytest.ini").write_text(
        "[pytest]\npython_files = *_test.py\npython_functions = check_*\n",
        encoding="utf-8")
    suite = repo / "UiTests" / "tests" / "screenshot_tests"
    (suite / "extra_test.py").write_text(
        "def check_one(): pass\n\ndef test_ignored(): pass\n", encoding="utf-8")
    files = {t["short"]: t["tests"] for t in mod._project_tests()}
    assert files["extra_test.py"] == 1
    # А обычные `test_*` при таком правиле тестами уже не считаются.
    assert files["cart_test.py"] == 0


# --------------------------------------------------------------------------- #
#  «Мы смотрели только сюда»
# --------------------------------------------------------------------------- #
def test_matching_files_outside_the_configured_directory_are_reported(connected):
    """Подключение говорит, где тесты; догадкой его подменять нельзя.

    Но промолчать о соседнем каталоге — значит оставить человека наедине с
    разницей между тем, что он знает о своём наборе, и тем, что видит.
    """
    _, mod, repo = connected
    api_tests = repo / "UiTests" / "tests" / "api_tests"
    api_tests.mkdir(parents=True)
    (api_tests / "health_test.py").write_text("def test_h(): pass\n",
                                              encoding="utf-8")
    files, notes = mod._project_test_scan()
    # В список чужой каталог НЕ попадает.
    assert "health_test.py" not in [f["short"] for f in files]
    # Но назван — с путём, по которому его видно.
    assert notes[0]["outside"] == 1
    assert notes[0]["outside_sample"] == ["UiTests/tests/api_tests/health_test.py"]


def test_the_configured_directory_is_not_counted_as_outside(connected):
    """Иначе подсказка советует расширить каталог до самого себя."""
    _, mod, _ = connected
    _, notes = mod._project_test_scan()
    assert notes[0]["found"] == 3
    assert notes[0]["outside"] == 0


def test_heavy_directories_are_never_walked(connected):
    """Чужой `node_modules` — это секунды на запрос и тысячи чужих файлов.

    Проверяется именно на подкаталоге настроенного каталога тестов: обход
    без обрезания веток нашёл бы там «тест» и показал бы его человеку.
    """
    _, mod, repo = connected
    junk = repo / "UiTests" / "tests" / "screenshot_tests" / "node_modules" / "pkg"
    junk.mkdir(parents=True)
    (junk / "index_test.py").write_text("def test_x(): pass\n", encoding="utf-8")
    hidden = repo / "UiTests" / "tests" / "screenshot_tests" / ".tox" / "py311"
    hidden.mkdir(parents=True)
    (hidden / "cached_test.py").write_text("def test_y(): pass\n",
                                           encoding="utf-8")
    found = [t["short"] for t in mod._project_tests()]
    assert "index_test.py" not in found
    assert "cached_test.py" not in found
    assert len(found) == 3


def test_custom_patterns_reach_the_scan(connected):
    """Конфигурация проекта действует не только в разборе, но и в обходе."""
    _, mod, repo = connected
    (repo / "pytest.ini").write_text("[pytest]\npython_files = screenshot_*.py\n",
                                     encoding="utf-8")
    suite = repo / "UiTests" / "tests" / "screenshot_tests"
    (suite / "screenshot_home.py").write_text("def test_d(): pass\n",
                                              encoding="utf-8")
    files, notes = mod._project_test_scan()
    assert [f["short"] for f in files] == ["screenshot_home.py"]
    assert notes[0]["patterns_from"] == "pytest.ini"


# --------------------------------------------------------------------------- #
#  Платформа по умолчанию
# --------------------------------------------------------------------------- #
@pytest.fixture
def two_platforms(tmp_path, monkeypatch):
    """Собственный набор сервиса, где эталоны лежат НЕ на платформе сервиса."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VISTEST_ROOT", str(tmp_path / ".vistest"))
    monkeypatch.setenv("VISTEST_AUTH", "off")

    import numpy as np

    from vistest.api import baselines as mod
    from vistest.config import VisTestConfig, platform_key
    from vistest.storage import BaselineRecord, FileBaselineStore

    cfg = VisTestConfig.load()
    mod._cfg = cfg
    other = "docker-chromium-1x"
    assert platform_key() != other, "фикстура держится на том, что они разные"
    FileBaselineStore(cfg.baselines_path(other)).save(BaselineRecord(
        name="login.png", image=np.full((10, 10, 3), 130, dtype=np.uint8),
        meta={"url": "https://example.test/login"}))
    return mod, other, platform_key()


def test_an_empty_default_platform_is_not_an_answer(two_platforms):
    """Ровно тот случай: сервис на windows, эталоны под docker.

    Совпадения нет ни разу, и сборка честно отвечала «0» при одиннадцати
    эталонах в соседнем каталоге.
    """
    _, other, _ = two_platforms
    assert pick_platform(None) == other


def test_the_platform_a_person_chose_is_never_overridden(two_platforms):
    """Если человек смотрит на пустую платформу — он хочет именно её."""
    _, _, own = two_platforms
    assert pick_platform(None, own) == own


def test_a_non_empty_default_stays_the_default(tmp_path, monkeypatch):
    """Подменять непустое умолчание «более полным» нельзя.

    Это перекладывание экрана под ногами: человек ушёл и вернулся в другой
    набор, ничего не нажав.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VISTEST_ROOT", str(tmp_path / ".vistest"))

    import numpy as np

    from vistest.api import baselines as mod
    from vistest.config import VisTestConfig, platform_key
    from vistest.storage import BaselineRecord, FileBaselineStore

    cfg = VisTestConfig.load()
    mod._cfg = cfg
    own = platform_key()
    FileBaselineStore(cfg.baselines_path(own)).save(BaselineRecord(
        name="a.png", image=np.full((4, 4, 3), 9, dtype=np.uint8)))
    FileBaselineStore(cfg.baselines_path("docker-chromium-1x")).save(
        BaselineRecord(name="b.png", image=np.full((4, 4, 3), 9, dtype=np.uint8)))
    FileBaselineStore(cfg.baselines_path("docker-chromium-1x")).save(
        BaselineRecord(name="c.png", image=np.full((4, 4, 3), 9, dtype=np.uint8)))
    assert pick_platform(None) == own


# --------------------------------------------------------------------------- #
#  «Нечего собирать» называет место, где собирать есть что
# --------------------------------------------------------------------------- #
def test_elsewhere_names_the_other_non_empty_store(two_platforms):
    _, other, own = two_platforms
    lines = _baselines_elsewhere(None, own)
    assert any(other in line for line in lines)


def test_elsewhere_does_not_name_the_place_we_looked_at(two_platforms):
    """Иначе подсказка советует вернуться туда, откуда пришли."""
    _, other, _ = two_platforms
    assert _baselines_elsewhere(None, other) == []


def test_codegen_log_names_the_set_and_the_platform(two_platforms):
    """«0» без адреса, по которому его получили, ничего не сообщает."""
    from vistest.api import baselines as mod

    said: list[str] = []

    class _Job:
        progress = 0.0

        def say(self, text, level="info"):
            said.append(text)

    mod._run_codegen(_Job(), pick_platform(None), None, "tests", None)
    joined = "\n".join(said)
    assert "set:" in joined and "platform:" in joined
    assert "docker-chromium-1x" in joined
