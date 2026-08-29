# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Подключение существующих наборов тестов.

Тесты строят игрушечный «чужой репозиторий» во временном каталоге и проверяют
три вещи: автоопределение находит то, что действительно есть; правила выбора
папки эталонов работают; эталоны читаются и пишутся на месте, а служебные
данные VisTest в чужой репозиторий не попадают.

Запуск чужого pytest здесь не проверяется — это интеграционный сценарий,
которому нужен настоящий проект.
"""

from __future__ import annotations

import sys

import numpy as np
import pytest

from vistest.config import VisTestConfig
from vistest.projects import (
    Adapter,
    BaselineDir,
    Project,
    ProjectRegistry,
    baseline_name_of,
    classify_png,
    discover,
    suggest,
)
from vistest.storage import BaselineRecord, ExternalBaselineStore

from . import synthetic as syn


# --------------------------------------------------------------------------- #
#  Игрушечный чужой репозиторий
# --------------------------------------------------------------------------- #
@pytest.fixture
def foreign(tmp_path):
    """Репозиторий, похожий на реальный: два комплекта эталонов и base_page."""
    root = tmp_path / "their-repo"
    tests = root / "UiTests" / "tests" / "screenshot_tests"
    pages = root / "UiTests" / "pages"
    tests.mkdir(parents=True)
    pages.mkdir(parents=True)

    (pages / "__init__.py").write_text("", encoding="utf-8")
    (pages / "base_page.py").write_text(
        "class BasePage:\n"
        "    def __init__(self, page):\n"
        "        self.page = page\n\n"
        "    def assert_screenshot(self, name, threshold=0.98,\n"
        "                          mask_selectors=None):\n"
        "        raise NotImplementedError\n",
        encoding="utf-8")

    (tests / "__init__.py").write_text("", encoding="utf-8")
    (tests / "login_page_screen_test.py").write_text(
        "import pytest\n"
        "import os\n"
        "pytestmark = pytest.mark.screenshot\n\n"
        "def test_login(page):\n"
        "    os.environ.get('ACME_TEST_USER_PASSWORD')\n",
        encoding="utf-8")

    for d in ("snapshots", "snapshots_ci"):
        (tests / d).mkdir()
        _png(tests / d / "login_filled.png", syn.page())

    return root


def _png(path, rgb):
    from vistest.capture.playwright_capture import _write_png

    _write_png(path, rgb)


# --------------------------------------------------------------------------- #
#  Автоопределение
# --------------------------------------------------------------------------- #
def test_discover_finds_both_baseline_dirs(foreign):
    info = discover(foreign)
    dirs = {b["dir"]: b for b in info["baselines"]}

    assert any(d.endswith("snapshots") for d in dirs)
    assert any(d.endswith("snapshots_ci") for d in dirs)
    assert all(b["count"] == 1 for b in dirs.values())


def test_ci_baselines_get_a_condition(foreign):
    """`snapshots_ci` — это эталоны для контейнера, а не вторая копия обычных."""
    info = discover(foreign)
    ci = next(b for b in info["baselines"] if b["dir"].endswith("snapshots_ci"))
    plain = next(b for b in info["baselines"] if b["dir"].endswith("/snapshots"))

    assert ci["when"] == {"CI": "true"}
    assert plain["when"] == {}


def test_conditional_rule_comes_first(foreign):
    """Безусловное правило, стоящее выше, перехватило бы всё."""
    info = discover(foreign)
    assert info["baselines"][0]["when"], "правило с условием должно быть первым"


def test_discover_finds_the_comparison_method(foreign):
    info = discover(foreign)
    targets = [c["target"] for c in info["adapter_candidates"]]
    assert "UiTests.pages.base_page.BasePage.assert_screenshot" in targets


def test_discover_finds_markers_and_env_vars(foreign):
    info = discover(foreign)
    assert "screenshot" in info["markers"]
    assert "ACME_TEST_USER_PASSWORD" in info["env_vars"]
    # Служебные маркеры pytest не должны попадать в предложение.
    assert "parametrize" not in info["markers"]


def test_discover_picks_the_visual_tests_directory(foreign):
    info = discover(foreign)
    assert info["tests"].endswith("screenshot_tests")


def test_suggest_builds_a_usable_project(foreign):
    p = suggest(foreign)

    assert p.root == str(foreign.resolve())
    assert p.mode() == "adapter"
    assert p.adapter.target.endswith("BasePage.assert_screenshot")
    assert p.pytest_args == ["-m", "screenshot"]
    assert len(p.baselines) == 2


# --------------------------------------------------------------------------- #
#  Интерпретатор: наш по умолчанию
# --------------------------------------------------------------------------- #
def test_own_interpreter_is_the_default(foreign):
    """Чужой венв — обязательство, а не актив.

    Его может не быть, он может быть под другую версию Python, в нём может не
    оказаться pytest, и в нём точно нет vistest. Требовать починить его ради
    знакомства с инструментом — верный способ не получить пилот.
    """
    p = suggest(foreign)
    assert p.python == ""
    assert p.uses_own_interpreter()
    assert p.resolve_python() == sys.executable


def test_project_venv_is_offered_but_not_chosen(foreign):
    """Венв проекта находим и показываем — но не выбираем за человека."""
    venv = foreign / ".venv" / ("Scripts" if sys.platform == "win32" else "bin")
    venv.mkdir(parents=True)
    exe = venv / ("python.exe" if sys.platform == "win32" else "python")
    exe.write_text("", encoding="utf-8")

    info = discover(foreign)
    assert info["project_venv"] == str(exe)

    p = suggest(foreign)
    assert p.uses_own_interpreter(), "по умолчанию всё равно наш"
    assert p.detect_venv() == str(exe), "но их венв должен быть виден"


def test_explicit_interpreter_is_respected(foreign):
    p = Project(key="t", root=str(foreign), python="/usr/bin/python3.11")
    assert p.resolve_python() == "/usr/bin/python3.11"
    assert not p.uses_own_interpreter()


def test_project_keyword_selects_their_venv(foreign):
    venv = foreign / ".venv" / ("Scripts" if sys.platform == "win32" else "bin")
    venv.mkdir(parents=True)
    exe = venv / ("python.exe" if sys.platform == "win32" else "python")
    exe.write_text("", encoding="utf-8")

    p = Project(key="t", root=str(foreign), python="project")
    assert p.resolve_python() == str(exe)


def test_missing_interpreter_is_reported(foreign, tmp_path):
    p = Project(key="t", root=str(foreign),
                python=str(tmp_path / "нет" / "python.exe"),
                baselines=[BaselineDir(dir="UiTests/tests/screenshot_tests/snapshots")])
    assert any("interpreter not found" in x for x in p.validate())


def test_project_root_goes_into_pythonpath(foreign):
    """`from UiTests.pages...` должно работать без установки их пакета."""
    p = _project(foreign)
    entries = p.python_path_entries()

    assert str(foreign.resolve()) in entries
    assert any(e.endswith("screenshot_tests") for e in entries)


def test_requirements_are_discovered(foreign):
    (foreign / "requirements.txt").write_text("playwright\nallure-pytest\n",
                                              encoding="utf-8")
    assert [p.name for p in suggest(foreign).requirements_files()] == \
           ["requirements.txt"]
    assert discover(foreign)["requirements"]


def test_discover_on_missing_dir_does_not_explode(tmp_path):
    info = discover(tmp_path / "нет-такого")
    assert not info["exists"]
    assert info["notes"]


def test_project_without_comparison_point_falls_back_to_observe(tmp_path):
    root = tmp_path / "plain"
    (root / "snapshots").mkdir(parents=True)
    _png(root / "snapshots" / "a.png", syn.page())
    (root / "test_x.py").write_text("def test_x():\n    pass\n", encoding="utf-8")

    p = suggest(root)
    assert p.mode() == "observe"


# --------------------------------------------------------------------------- #
#  Выбор папки эталонов
# --------------------------------------------------------------------------- #
def _project(root) -> Project:
    return Project(
        key="t", root=str(root), tests="UiTests/tests/screenshot_tests",
        baselines=[
            BaselineDir(dir="UiTests/tests/screenshot_tests/snapshots_ci",
                        when={"CI": "true"}),
            BaselineDir(dir="UiTests/tests/screenshot_tests/snapshots"),
        ],
        adapter=Adapter(target="UiTests.pages.base_page.BasePage.assert_screenshot"),
    )


def test_baseline_dir_switches_on_env(foreign):
    p = _project(foreign)
    assert p.baseline_dir({"CI": "true"}).name == "snapshots_ci"
    assert p.baseline_dir({}).name == "snapshots"
    assert p.baseline_dir({"CI": "false"}).name == "snapshots"


def test_baseline_dir_is_none_when_nothing_matches(foreign):
    p = Project(key="t", root=str(foreign),
                baselines=[BaselineDir(dir="x", when={"CI": "true"})])
    assert p.baseline_dir({}) is None


def test_validate_reports_what_blocks_the_run(tmp_path):
    p = Project(key="t", root=str(tmp_path / "нет"))
    assert any("not found" in x for x in p.validate())

    p2 = Project(key="t", root=str(tmp_path))
    assert any("baseline" in x for x in p2.validate())


def test_own_baselines_are_platform_scoped(foreign, tmp_path, monkeypatch):
    """Рендер текста в Windows и в контейнере разный — общего эталона нет."""
    monkeypatch.chdir(tmp_path)
    cfg = VisTestConfig.load()
    p = Project(key="t", root=str(foreign), baseline_store="vistest")

    base = p.vistest_baselines_path(cfg)
    win = p.vistest_baselines_path(cfg, "win-chromium-1x")
    docker = p.vistest_baselines_path(cfg, "docker-chromium-1x")

    assert p.uses_own_baselines()
    assert win.parent == base and docker.parent == base
    assert win != docker


def test_own_baselines_live_outside_the_project(foreign, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = VisTestConfig.load()
    p = Project(key="t", root=str(foreign), baseline_store="vistest")

    path = p.vistest_baselines_path(cfg, "win-chromium-1x")
    assert foreign not in path.parents, \
        "свой комплект не должен попадать в чужой репозиторий"


def test_own_baselines_do_not_require_project_folders(foreign):
    """Свой комплект снимается сам — чужие папки ему не нужны."""
    p = Project(key="t", root=str(foreign), baseline_store="vistest")
    assert not any("baseline" in x for x in p.validate())

    q = Project(key="t", root=str(foreign))
    assert any("baseline" in x for x in q.validate())


def test_unknown_baseline_store_is_reported(foreign):
    p = Project(key="t", root=str(foreign), baseline_store="magic")
    assert any("unknown baseline store" in x for x in p.validate())


def test_baseline_store_survives_the_registry(foreign, tmp_path, monkeypatch):
    pytest.importorskip("yaml")
    monkeypatch.chdir(tmp_path)
    reg = ProjectRegistry(VisTestConfig.load())

    reg.save(Project(key="t", root=str(foreign), baseline_store="vistest"))
    assert reg.get("t").baseline_store == "vistest"


def test_missing_project_venv_is_not_a_problem(foreign):
    """Отсутствие венва у проекта больше не мешает: гоняем своим."""
    p = _project(foreign)
    assert not any("окружение" in x for x in p.validate())


# --------------------------------------------------------------------------- #
#  Реестр
# --------------------------------------------------------------------------- #
def test_registry_roundtrip(foreign, tmp_path, monkeypatch):
    pytest.importorskip("yaml")
    monkeypatch.chdir(tmp_path)
    cfg = VisTestConfig.load()
    reg = ProjectRegistry(cfg)

    reg.save(_project(foreign))
    back = reg.get("t")

    assert back is not None
    assert back.root == str(foreign)
    assert back.adapter.target.endswith("assert_screenshot")
    assert [b.dir for b in back.baselines] == \
           [b.dir for b in _project(foreign).baselines]
    assert [p.key for p in reg.list()] == ["t"]

    assert reg.delete("t")
    assert reg.get("t") is None
    assert not reg.delete("t")


# --------------------------------------------------------------------------- #
#  Эталоны на месте
# --------------------------------------------------------------------------- #
def test_external_store_reads_project_png(foreign, tmp_path):
    snaps = foreign / "UiTests" / "tests" / "screenshot_tests" / "snapshots"
    store = ExternalBaselineStore(snaps, tmp_path / "side")

    assert store.exists("login_filled.png")
    assert store.list_names() == ["login_filled.png"]

    rec = store.load("login_filled.png")
    assert rec is not None
    assert rec.image.shape[0] > 0


def test_approve_rewrites_the_project_file(foreign, tmp_path):
    """Апрув должен попасть в git проекта, а не в наше хранилище."""
    snaps = foreign / "UiTests" / "tests" / "screenshot_tests" / "snapshots"
    side = tmp_path / "side"
    store = ExternalBaselineStore(snaps, side)

    before = (snaps / "login_filled.png").read_bytes()
    store.save(BaselineRecord(name="login_filled.png",
                              image=syn.page(show_promo=False)))
    after = (snaps / "login_filled.png").read_bytes()

    assert before != after, "эталон в репозитории проекта должен обновиться"
    assert sorted(p.name for p in snaps.glob("*")) == ["login_filled.png"], \
        "в чужой папке не должно появиться ничего лишнего"


def test_service_data_stays_out_of_the_project(foreign, tmp_path):
    snaps = foreign / "UiTests" / "tests" / "screenshot_tests" / "snapshots"
    side = tmp_path / "side"
    store = ExternalBaselineStore(snaps, side)

    mask = np.zeros(syn.page().shape[:2], dtype=bool)
    mask[10:20, 10:20] = True
    store.save(BaselineRecord(name="login_filled.png", image=syn.page(),
                              stability_mask=mask, dom={"nodes": []}))

    assert (side / "login_filled" / "meta.json").exists()
    assert (side / "login_filled" / "stability.png").exists()
    assert (side / "login_filled" / "dom.json").exists()
    assert not list(snaps.glob("*.json")), "паспорта в чужом репозитории не место"


def test_ignore_boxes_survive_in_sidecar(foreign, tmp_path):
    snaps = foreign / "UiTests" / "tests" / "screenshot_tests" / "snapshots"
    store = ExternalBaselineStore(snaps, tmp_path / "side")

    store.add_ignore_box("login_filled.png", {"x": 1, "y": 2, "w": 3, "h": 4})
    rec = store.load("login_filled.png")

    assert rec.ignore_boxes == [{"x": 1, "y": 2, "w": 3, "h": 4}]


def test_store_does_not_escape_its_directory(foreign, tmp_path):
    """Имя приходит из чужого кода — выйти за пределы папки через него нельзя."""
    snaps = foreign / "UiTests" / "tests" / "screenshot_tests" / "snapshots"
    store = ExternalBaselineStore(snaps, tmp_path / "side")

    path = store.png_for("../../../etc/passwd")
    assert snaps in path.parents


# --------------------------------------------------------------------------- #
#  Классификация PNG для режима разбора
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name,kind", [
    ("login_filled.png", "baseline"),
    ("actual_login_filled.png", "actual"),
    ("diff_login_filled.png", "diff"),
    ("combined_login_filled.png", "diff"),
])
def test_classify_png(tmp_path, name, kind):
    assert classify_png(tmp_path / name) == kind


def test_baseline_name_strips_the_prefix(tmp_path):
    assert baseline_name_of(tmp_path / "actual_login.png") == "login.png"
    assert baseline_name_of(tmp_path / "login.png") == "login.png"


# --------------------------------------------------------------------------- #
#  Зависимости: ставим только недостающее
# --------------------------------------------------------------------------- #
REQUIREMENTS = """\
pytest==8.3.5
requests==2.32.4
allure-pytest==2.13.3
python-dotenv==1.0.0
PyYAML==6.0.1
psycopg2-binary==2.9.9   # драйвер базы, визуальным тестам не нужен
playwright==1.58.0
# комментарий целиком
opencv-python>=4.8.0
factory_boy==3.3.3
-r other.txt
"""


@pytest.fixture
def reqs(tmp_path):
    from vistest.external import parse_requirements

    path = tmp_path / "requirements.txt"
    path.write_text(REQUIREMENTS, encoding="utf-8")
    return parse_requirements([path])


def test_requirements_are_parsed_with_pins(reqs):
    assert reqs["pytest"] == "pytest==8.3.5"
    assert reqs["allure-pytest"] == "allure-pytest==2.13.3"
    assert reqs["opencv-python"] == "opencv-python>=4.8.0"
    assert reqs["factory-boy"] == "factory_boy==3.3.3", "имя нормализуется"


def test_comments_and_directives_are_skipped(reqs):
    assert not any(k.startswith("#") for k in reqs)
    assert "-r other.txt" not in reqs.values()
    assert "psycopg2-binary" in reqs
    assert reqs["psycopg2-binary"] == "psycopg2-binary==2.9.9", \
        "хвостовой комментарий не должен попасть в требование"


@pytest.mark.parametrize("module,expected", [
    ("dotenv", "python-dotenv==1.0.0"),
    ("yaml", "PyYAML==6.0.1"),
    ("allure", "allure-pytest==2.13.3"),
    ("psycopg2", "psycopg2-binary==2.9.9"),
    ("cv2", "opencv-python>=4.8.0"),
    ("playwright", "playwright==1.58.0"),
])
def test_module_names_map_to_pinned_packages(reqs, module, expected):
    """Имя модуля в ошибке импорта и имя пакета на PyPI совпадают не всегда."""
    from vistest.external import packages_for

    assert packages_for([module], reqs) == [expected]


def test_unknown_module_falls_back_to_its_own_name(reqs):
    from vistest.external import packages_for

    assert packages_for(["somelib"], reqs) == ["somelib"]


def test_duplicate_packages_are_collapsed(reqs):
    """Два модуля одного пакета не должны ставиться дважды."""
    from vistest.external import packages_for

    assert packages_for(["allure", "allure"], reqs) == ["allure-pytest==2.13.3"]


def test_fixture_names_map_to_plugins():
    """«fixture 'context' not found» ничего не говорит про pytest-playwright.

    Это второй по частоте способ не запустить чужие тесты, и по симптомам он
    не читается совсем: импортов нет, сборка проходит, а на setup фикстуру
    некому отдать.
    """
    from vistest.external import _FIXTURE_TO_PACKAGE

    for fixture in ("page", "context", "browser", "browser_context_args"):
        assert _FIXTURE_TO_PACKAGE[fixture] == "pytest-playwright"
    assert _FIXTURE_TO_PACKAGE["mocker"] == "pytest-mock"


def test_missing_fixture_is_parsed_from_pytest_output():
    from vistest.external import _FIXTURE_RE

    text = ("E       fixture 'context' not found\n"
            ">       available fixtures: cache, capfd, page\n"
            "E       fixture 'mocker' not found\n")
    assert sorted(set(_FIXTURE_RE.findall(text))) == ["context", "mocker"]


def test_missing_module_is_parsed_from_pytest_output():
    from vistest.external import _MISSING_RE

    text = "E   ModuleNotFoundError: No module named 'dotenv'\n"
    assert _MISSING_RE.findall(text) == ["dotenv"]


def test_plugins_are_added_to_install_list(reqs):
    """Плагин приходит именем пакета, а не модуля — версия всё равно из пина."""
    from vistest.external import packages_for

    specs = packages_for([], reqs)
    assert specs == []

    # Проверяем ту же логику, что применяется к plugins в install_requirements.
    from vistest.external import _normalize

    assert reqs.get(_normalize("allure-pytest")) == "allure-pytest==2.13.3"
    assert reqs.get(_normalize("pytest-playwright")) is None, \
        "если пакета нет в requirements — ставим по имени, без версии"


def test_only_missing_is_installed_not_everything(reqs):
    """Главное свойство: драйвер базы не тянется ради визуальных тестов.

    Именно на нём установка и падала: у `psycopg2-binary` нет колеса под
    свежий Python, а к скриншотам он отношения не имеет.
    """
    from vistest.external import packages_for

    specs = packages_for(["playwright", "allure"], reqs)
    assert "psycopg2-binary==2.9.9" not in specs
    assert len(specs) == 2
