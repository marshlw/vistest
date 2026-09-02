"""Связь «этот снимок — этот тест», и формат кадра как часть спеки.

Две жалобы, обе про одно и то же место — паспорт эталона.

Первая: страница теста подключённого проекта говорила «снимков нет» при
снимках, снятых именно им. Причина оказалась текстовой: в паспорте лежит путь,
каким его видел pytest ТОГО прогона (относительно корня, из которого его
запускали), а спрашиваем мы путём от корня проекта. Для набора, который гоняют
из подкаталога, это `tests/…/login_test.py` против
`UiTests/tests/…/login_test.py` — один файл, записанный двумя способами, и
сравнение строк отвечало «нет».

Вторая: кнопка «Assemble» собирала тест и оставляла две половины, которые друг
о друге не знают — карточка снимка «теста нет», страница теста «снимков нет».
Связь теперь пишется в момент сборки, но отдельным полем: `source` отвечает на
«чем снимок БЫЛ СНЯТ», и переписать его сборкой значило бы соврать про тест,
который ещё ни разу не выполнялся.

Плюс третье, из того же паспорта: что именно попадает в кадр. Это и есть
формат снимка — страница целиком, экран, элемент, — и до сих пор он был одной
настройкой на всю установку.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from vistest.api.baselines import (
    capture_config_for,
    declaration_key,
    declared_in,
    normalize_area,
    same_test_file,
)


# --------------------------------------------------------------------------- #
#  Один ли это файл теста
# --------------------------------------------------------------------------- #
def test_the_same_path_matches_itself():
    assert same_test_file("tests/test_login.py", "tests/test_login.py")


def test_a_longer_prefix_is_still_the_same_file():
    """Ровно та пара, на которой связь и терялась."""
    assert same_test_file("tests/screenshot_tests/login_test.py",
                          "UiTests/tests/screenshot_tests/login_test.py")
    assert same_test_file("UiTests/tests/screenshot_tests/login_test.py",
                          "tests/screenshot_tests/login_test.py")


def test_windows_separators_are_the_same_path():
    """Путь мог быть записан прогоном на windows, а спрошен из docker."""
    assert same_test_file("UiTests\\tests\\login_test.py",
                          "tests/login_test.py")


def test_the_same_name_in_different_directories_is_not_the_same_file():
    """Иначе `api/health_test.py` и `ui/health_test.py` схлопнутся в один."""
    assert not same_test_file("api/health_test.py", "ui/health_test.py")


def test_a_bare_name_still_matches_its_file():
    """Совпадение по одному сегменту — тоже совпадение, если сегмент один."""
    assert same_test_file("login_test.py", "UiTests/tests/login_test.py")


def test_nothing_matches_an_empty_side():
    """У снимка без источника связи нет, и придумывать её нельзя."""
    assert not same_test_file("", "tests/test_login.py")
    assert not same_test_file(None, "tests/test_login.py")
    assert not same_test_file("tests/test_login.py", "")


# --------------------------------------------------------------------------- #
#  Связь в интерфейсе
# --------------------------------------------------------------------------- #
@pytest.fixture
def wired(tmp_path, monkeypatch):
    """Сервис, подключённый проект и его снимок, снятый его же тестом."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VISTEST_ROOT", str(tmp_path / ".vistest"))
    monkeypatch.setenv("VISTEST_AUTH", "off")

    from vistest.api import baselines as mod
    from vistest.config import VisTestConfig, platform_key
    from vistest.projects import Project, ProjectRegistry
    from vistest.storage import BaselineRecord, FileBaselineStore

    cfg = VisTestConfig.load()
    mod._cfg = cfg

    repo = tmp_path / "their-repo"
    suite = repo / "UiTests" / "tests" / "screenshot_tests"
    suite.mkdir(parents=True)
    (suite / "login_test.py").write_text("def test_login(): pass\n",
                                         encoding="utf-8")
    project = Project(key="aeron", name="Aeron", root=str(repo),
                      tests="UiTests/tests/screenshot_tests")
    ProjectRegistry(cfg).save(project)

    pf = platform_key()
    store = FileBaselineStore(project.vistest_baselines_path(cfg, pf))
    store.save(BaselineRecord(
        name="login.png", image=np.full((10, 10, 3), 130, dtype=np.uint8),
        meta={"url": "https://example.test/login",
              # Путь ровно такой, каким его видел ИХ pytest: от корня, из
              # которого его запускали, а не от корня проекта.
              "source": {"kind": "test",
                         "file": "tests/screenshot_tests/login_test.py",
                         "test": "tests/screenshot_tests/login_test.py::test_login",
                         "project_key": "aeron"}}))

    app = FastAPI()
    app.include_router(mod.router)
    return TestClient(app), mod, cfg, pf


def test_a_projects_test_finds_the_snapshots_it_captured(wired):
    """Именно то, что показывало «No snapshots are linked to this file yet»."""
    client, _, _, _ = wired
    body = client.get("/api/tests/source", params={
        "name": "UiTests/tests/screenshot_tests/login_test.py",
        "project": "aeron"}).json()
    assert [s["name"] for s in body["snapshots"]] == ["login.png"]
    assert body["snapshots"][0]["via"] == "captured"


# --------------------------------------------------------------------------- #
#  Сборка связывает тест с эталоном
# --------------------------------------------------------------------------- #
def test_assembling_writes_the_link_into_the_passport(wired):
    """Без этого «Assemble» оставляет две половины, не знающие друг о друге."""
    client, mod, cfg, pf = wired

    said: list[str] = []

    class _Job:
        progress = 0.0

        def say(self, text, level="info"):
            said.append(text)

    out = mod._run_codegen(_Job(), pf, None, "tests", "project:aeron")
    assert out["files"], said
    assert out["linked"] == 1

    meta = json.loads((cfg.root_path / "external" / "aeron" / "baselines" / pf
                       / "login" / "meta.json").read_text("utf-8"))
    link = meta["generated_by"]
    assert link["file"] == "tests/test_visual_recorded.py"
    assert link["test"].startswith("test_")
    # Чем снимок был снят — не переписано: собранный тест ещё не выполнялся.
    assert meta["source"]["kind"] == "test"
    assert meta["source"]["file"] == "tests/screenshot_tests/login_test.py"


def test_the_assembled_test_lists_its_baselines_right_away(wired):
    """Ответ на жалобу целиком: связь видна сразу, без прогона."""
    client, mod, _, pf = wired

    class _Job:
        progress = 0.0

        def say(self, text, level="info"):
            pass

    mod._run_codegen(_Job(), pf, None, "tests", "project:aeron")
    body = client.get("/api/tests/source",
                      params={"name": "test_visual_recorded.py"}).json()
    assert [s["name"] for s in body["snapshots"]] == ["login.png"]
    # И названо честно: собран из эталона, а не снял его.
    assert body["snapshots"][0]["via"] == "generated"


def test_codegen_reports_the_function_name_for_every_snapshot(tmp_path):
    """Имя функции известно только генератору — снаружи его не восстановить."""
    from vistest.record.codegen import write_test_files

    class _Rec:
        def __init__(self, meta):
            self.meta = meta

    class _Store:
        metas = {"login": {"url": "https://app.local/login",
                           "viewport": "1440x900"},
                 "cart": {"url": "https://app.local/cart",
                          "viewport": "1440x900"}}

        def list_names(self):
            return list(self.metas)

        def load(self, name):
            return _Rec(self.metas[name])

    seen: list[tuple[str, list]] = []
    write_test_files(_Store(), tmp_path, platform="linux-chromium",
                     on_link=lambda path, links: seen.append((path, links)))
    assert len(seen) == 1
    links = {item["name"]: item["test"] for item in seen[0][1]}
    assert set(links) == {"login", "cart"}
    # Разные страницы — разные функции, иначе связь указывает не туда.
    assert len(set(links.values())) == 2
    text = (tmp_path / "test_visual_recorded.py").read_text("utf-8")
    for func in links.values():
        assert f"def {func}(" in text


# --------------------------------------------------------------------------- #
#  Формат кадра
# --------------------------------------------------------------------------- #
def test_the_whole_page_and_the_window_are_different_answers():
    assert normalize_area({"area": "full"})["full_page"] is True
    assert normalize_area({"area": "viewport"})["full_page"] is False


def test_capturing_one_element_needs_a_selector():
    """`element` без селектора — это съёмка неизвестно чего."""
    with pytest.raises(HTTPException) as e:
        normalize_area({"area": "element"})
    assert "selector" in str(e.value.detail)


def test_an_area_that_is_not_an_area_is_rejected():
    with pytest.raises(HTTPException):
        normalize_area({"area": "everything"})


def test_a_selector_left_over_from_before_does_not_survive_a_page_shot():
    """Иначе «вся страница» тихо осталась бы съёмкой прошлого элемента."""
    out = normalize_area({"area": "full", "selector": "header"})
    assert "selector" not in out


def test_a_target_without_an_area_is_left_exactly_as_it_was():
    """Появление поля не должно молча переснять весь накопленный набор."""
    target = {"url": "https://x.test", "selector": "header"}
    assert normalize_area(target) == target


def test_the_installation_default_applies_when_the_shot_says_nothing():
    from vistest.config import CaptureConfig

    base = CaptureConfig(full_page=True)
    assert capture_config_for(base, {"url": "x"}) is base


def test_the_shot_overrides_the_installation_default():
    from vistest.config import CaptureConfig

    base = CaptureConfig(full_page=True)
    assert capture_config_for(base, {"full_page": False}).full_page is False
    assert base.full_page is True, "настройка установки не должна меняться"


def test_an_element_shot_does_not_touch_the_page_setting():
    """В кадре узел, а не страница: `full_page` здесь не значит ничего."""
    from vistest.config import CaptureConfig

    base = CaptureConfig(full_page=True)
    same = capture_config_for(base, {"full_page": False, "selector": "header"})
    assert same is base


# --------------------------------------------------------------------------- #
#  Паспорт снимка воспроизводит кадр
# --------------------------------------------------------------------------- #
def test_the_passport_keeps_everything_the_frame_depends_on():
    """Снимок воспроизводится кнопкой — значит, в паспорте лежит всё."""
    from vistest.api.baselines import snapshot_meta

    meta = snapshot_meta(normalize_area({
        "url": "https://app.local/feed", "area": "viewport",
        "viewport": "390x844", "wait": 500,
        "steps": [{"action": "flow", "name": "login"}]}))
    assert meta["url"] == "https://app.local/feed"
    assert meta["viewport"] == "390x844"
    assert meta["wait"] == 500
    assert meta["steps"]
    # Без этих двух пересъёмка вернёт другой кадр того же адреса, и разница
    # прочитается как изменение страницы.
    assert meta["area"] == "viewport"
    assert meta["full_page"] is False


def test_the_passport_does_not_forget_which_test_captured_the_shot():
    from vistest.api.baselines import snapshot_meta

    was = {"kind": "test", "file": "tests/test_login.py"}
    assert snapshot_meta({"url": "https://x.test"}, was)["source"] == was


def test_recapture_reproduces_the_area_from_the_passport(wired, monkeypatch):
    """Иначе спека есть, а пересъёмка снимает по-другому."""
    client, mod, cfg, pf = wired
    mod._write_meta(pf, "login.png",
                    {"area": "viewport", "full_page": False}, "project:aeron")

    seen: list[dict] = []
    monkeypatch.setattr(mod, "_run_snap",
                        lambda job, targets, *a, **k: seen.extend(targets) or {})
    r = client.post("/api/baselines/resnap", json={
        "platform": pf, "names": ["login.png"], "scope": "project:aeron"})
    assert r.status_code == 200, r.text
    for _ in range(200):
        if seen:
            break
        time.sleep(0.01)
    assert seen and seen[0]["area"] == "viewport"
    assert seen[0]["full_page"] is False


# --------------------------------------------------------------------------- #
#  Связь, написанная в коде
# --------------------------------------------------------------------------- #
def test_the_test_that_captures_a_snapshot_is_named_in_the_code():
    """Связь существовала только как СОБЫТИЕ — её писал прогон или сборка.

    Набор, снятый чужими тестами, поэтому стоял и молчал: код прямым текстом
    говорит, какой тест какой снимок снимает, и никто этого текста не читал.
    """
    decls = declared_in(
        "import pytest\n"
        "\n"
        "def test_login(page, visual):\n"
        "    page.goto('/login')\n"
        "    visual.assert_screenshot('login.png')\n"
        "\n"
        "def test_cart(page, visual):\n"
        "    visual.assert_screenshot(\"cart.png\")\n"
        "    visual.assert_screenshot('cart_empty.png')\n")
    assert decls == [
        {"name": "login.png", "test": "test_login"},
        {"name": "cart.png", "test": "test_cart"},
        {"name": "cart_empty.png", "test": "test_cart"},
    ]


def test_a_name_assembled_at_runtime_is_not_guessed():
    """Догадка привязала бы снимок не к тому тесту — это хуже, чем не привязать."""
    assert declared_in(
        "def test_x(visual):\n"
        "    visual.assert_screenshot(f'page_{n}.png')\n") == []


def test_a_call_outside_any_test_is_still_reported_without_a_test():
    """Помощник в conftest — не тест, и приписывать ему имя теста нельзя."""
    decls = declared_in("visual.assert_screenshot('orphan.png')\n")
    assert decls == [{"name": "orphan.png", "test": ""}]


def test_the_name_in_the_code_matches_the_name_in_the_store():
    """В коде пишут «login», в хранилище лежит «shop.example/login.png»."""
    assert declaration_key("login") == declaration_key("login.png")
    assert declaration_key("shop.example/login.png") == declaration_key("login")
    assert declaration_key("Login.PNG") == declaration_key("login")
    assert declaration_key("a/login.png") != declaration_key("a/logout.png")


@pytest.fixture
def declared(tmp_path, monkeypatch):
    """Проект, чьи эталоны сняты его тестами — и ничего об этом не записано."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VISTEST_ROOT", str(tmp_path / ".vistest"))
    monkeypatch.setenv("VISTEST_AUTH", "off")

    from vistest.api import baselines as mod
    from vistest.config import VisTestConfig, platform_key
    from vistest.projects import Project, ProjectRegistry
    from vistest.storage import BaselineRecord, FileBaselineStore

    cfg = VisTestConfig.load()
    mod._cfg = cfg
    mod._DECL_CACHE.clear()

    repo = tmp_path / "their-repo"
    suite = repo / "UiTests" / "tests" / "screenshot_tests"
    suite.mkdir(parents=True)
    (suite / "login_page_screen_test.py").write_text(
        "def test_login_page(logged_in_page, visual):\n"
        "    visual.assert_screenshot('login_page.png')\n", encoding="utf-8")
    (suite / "mailbox_page_screen_test.py").write_text(
        "def test_mailbox(logged_in_page, visual):\n"
        "    visual.assert_screenshot('mailbox.png')\n"
        "    visual.assert_screenshot('mailbox_empty.png')\n", encoding="utf-8")
    project = Project(key="aeron", name="Aeron", root=str(repo),
                      tests="UiTests/tests/screenshot_tests")
    ProjectRegistry(cfg).save(project)

    pf = platform_key()
    store = FileBaselineStore(project.vistest_baselines_path(cfg, pf))
    for shot in ("login_page.png", "mailbox.png", "mailbox_empty.png"):
        # Ни адреса, ни источника: ровно так выглядит набор, снятый их же
        # прогоном до того, как источник вообще стали записывать.
        store.save(BaselineRecord(
            name=shot, image=np.full((8, 8, 3), 40, dtype=np.uint8), meta={}))

    app = FastAPI()
    app.include_router(mod.router)
    return TestClient(app), mod, pf


def test_every_snapshot_finds_its_test_without_a_single_run(declared):
    """Жалоба целиком: связался один снимок, остальные — нет."""
    client, _, _ = declared
    body = client.get("/api/baselines/detail",
                      params={"scope": "project:aeron"}).json()
    items = {i["name"]: i for p in body["platforms"] for i in p["items"]}
    assert len(items) == 3
    for name, item in items.items():
        assert item["source"]["kind"] == "test", name
        assert item["source"]["from_code"] is True
        assert item["source"]["case"]


def test_a_snapshot_bound_to_a_test_can_be_rechecked_by_it(declared):
    """«Перепроверить» для страницы за входом — это прогнать тест, не адрес.

    Без источника кнопки не было вовсе: адреса нет, значит нечем. С адресом
    вместо теста было бы хуже — снялась бы форма логина.
    """
    client, _, _ = declared
    body = client.get("/api/baselines/detail",
                      params={"scope": "project:aeron"}).json()
    items = [i for p in body["platforms"] for i in p["items"]]
    assert all(i["runnable"] for i in items)


def test_a_recorded_source_is_never_overwritten_by_the_code(declared):
    """Что снимок был снят, знает прогон. Код знает только, что объявлен."""
    client, mod, pf = declared
    mod._write_meta(pf, "login_page.png",
                    {"source": {"kind": "url"}}, "project:aeron")
    body = client.get("/api/baselines/detail",
                      params={"scope": "project:aeron"}).json()
    item = next(i for p in body["platforms"] for i in p["items"]
                if i["name"] == "login_page.png")
    assert item["source"]["kind"] == "url"
    assert "from_code" not in item["source"]


def test_a_snapshot_named_by_two_tests_is_left_unbound(declared):
    """Два теста на один снимок — это вопрос, а не связь.

    Отвечать на него догадкой значит показать человеку тест, который к делу
    может не иметь отношения, и он этому поверит.
    """
    client, mod, _ = declared
    suite = Path(mod._cfg.root_path).parent / "their-repo" / "UiTests" / \
        "tests" / "screenshot_tests"
    (suite / "duplicate_test.py").write_text(
        "def test_also_mailbox(visual):\n"
        "    visual.assert_screenshot('mailbox.png')\n", encoding="utf-8")
    mod._DECL_CACHE.clear()
    body = client.get("/api/baselines/detail",
                      params={"scope": "project:aeron"}).json()
    item = next(i for p in body["platforms"] for i in p["items"]
                if i["name"] == "mailbox.png")
    assert item["source"]["kind"] == "unknown"


def test_the_test_page_lists_the_snapshots_the_code_says_it_takes(declared):
    """Обратная сторона той же связи, и та же жалоба с другого экрана."""
    client, _, _ = declared
    body = client.get("/api/tests/source", params={
        "name": "UiTests/tests/screenshot_tests/mailbox_page_screen_test.py",
        "project": "aeron"}).json()
    assert sorted(s["name"] for s in body["snapshots"]) == [
        "mailbox.png", "mailbox_empty.png"]
    assert {s["via"] for s in body["snapshots"]} == {"declared"}


def test_editing_a_test_changes_the_links_at_once(declared):
    """Связь считается по коду, а не по событию, — значит и живёт как код.

    Карта снимков кэшируется по временам файлов, и кэш, который не замечает
    правку, — это ровно тот случай, когда интерфейс уверенно показывает
    позавчерашнюю связь. Поэтому карта здесь сначала строится, и только потом
    файл меняется.
    """
    client, mod, _ = declared
    ask = {"name": "UiTests/tests/screenshot_tests/mailbox_page_screen_test.py",
           "project": "aeron"}
    first = client.get("/api/tests/source", params=ask).json()
    assert len(first["snapshots"]) == 2, "карта не построилась до правки"

    suite = Path(mod._cfg.root_path).parent / "their-repo" / "UiTests" / \
        "tests" / "screenshot_tests"
    time.sleep(0.01)
    (suite / "mailbox_page_screen_test.py").write_text(
        "def test_mailbox(visual):\n"
        "    visual.assert_screenshot('mailbox.png')\n", encoding="utf-8")
    body = client.get("/api/tests/source", params=ask).json()
    assert [s["name"] for s in body["snapshots"]] == ["mailbox.png"]
