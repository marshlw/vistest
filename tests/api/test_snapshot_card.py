# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Снимок как объект первого класса.

Снимок был строкой-ключом, размазанной по трём экранам: спека — в карточке на
вкладке «Baselines», история — только если повезёт наткнуться на нужное
сравнение, версии эталона — в модалке из меню «⋯», ignore-зоны видны одним
числом на чипе. Ответить на вопрос «что это за снимок и что с ним происходило»
было негде, а править из интерфейса можно было ровно один URL.

Здесь проверяется то, что делает снимок объектом: он отвечает о себе целиком,
его спеку можно изменить и — что важнее — можно СНЯТЬ, а зоны игнорирования
принадлежат ему, а не одному разбору падения.
"""

from __future__ import annotations

import threading

import numpy as np
import pytest
from fastapi.testclient import TestClient

PLATFORM = "linux-chromium-1x"
NAME = "shop.example/login.png"


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VISTEST_ROOT", str(tmp_path / ".vistest"))
    monkeypatch.delenv("VISTEST_AUTH", raising=False)

    import importlib

    from fastapi import APIRouter

    import vistest.api.auth as authmod
    import vistest.api.db as dbmod

    dbmod._local = threading.local()
    authmod.router = APIRouter()

    import vistest.api.main as mainmod
    importlib.reload(mainmod)

    from vistest.config import VisTestConfig
    from vistest.storage import BaselineRecord, FileBaselineStore

    cfg = VisTestConfig.load()
    FileBaselineStore(cfg.baselines_path(PLATFORM)).save(BaselineRecord(
        name=NAME, image=np.full((60, 90, 3), 180, dtype=np.uint8),
        meta={"url": "https://shop.example/login", "viewport": "1440x900"}))

    return TestClient(mainmod.app), mainmod


def _card(client, **extra):
    return client.get("/api/baselines/card",
                      params={"platform": PLATFORM, "name": NAME, **extra})


# --------------------------------------------------------------------------- #
def test_the_card_answers_about_the_snapshot_as_a_whole(service):
    client, _ = service
    d = _card(client).json()

    assert d["name"] == NAME
    assert d["short"] == "login.png"
    assert d["project"] == "shop.example"
    assert d["spec"]["url"] == "https://shop.example/login"
    assert d["spec"]["viewport"] == "1440x900"
    assert d["baseline"]["version"] == 1
    assert d["baseline"]["width"] == 90 and d["baseline"]["height"] == 60
    assert d["runnable"] is True
    assert d["ignore_boxes"] == []
    assert d["history"] == []


def test_a_missing_snapshot_is_a_404(service):
    client, _ = service
    r = client.get("/api/baselines/card",
                   params={"platform": PLATFORM, "name": "nope.png"})
    assert r.status_code == 404


def test_the_card_carries_the_history_of_every_run(service):
    """История снимка была доступна только через конкретное сравнение.

    То есть попасть в неё можно было, лишь уже находясь внутри неё.
    """
    client, mainmod = service
    for key, verdict, sev in (("r1", "pass", 0.0), ("r2", "fail", 44.0)):
        mainmod.db.ingest_run({
            "run_id": key, "platform": PLATFORM, "browser": "chromium",
            "git": {"branch": "main", "sha": "abc1234"},
            "totals": {"total": 1},
            "comparisons": [{"name": NAME, "verdict": verdict,
                             "metrics": {"max_severity": sev}}],
        }, "demo")

    history = _card(client).json()["history"]
    assert len(history) == 2
    assert {h["verdict"] for h in history} == {"pass", "fail"}
    assert history[0]["branch"] == "main"
    assert all(h["run_id"] for h in history)


def test_the_history_does_not_mix_in_a_namesake_from_another_platform(service):
    client, mainmod = service
    mainmod.db.ingest_run({
        "run_id": "other", "platform": "win-chromium-1x", "browser": "chromium",
        "totals": {"total": 1},
        "comparisons": [{"name": NAME, "verdict": "fail",
                         "metrics": {"max_severity": 90.0}}],
    }, "demo")
    assert _card(client).json()["history"] == []


# --------------------------------------------------------------------------- #
#  Спека правится целиком — и снимается
# --------------------------------------------------------------------------- #
def test_the_whole_spec_can_be_set_not_just_the_url(service):
    """Меню предлагало «Edit the spec», а правился один URL через prompt().

    Снимок за формой логина без шагов не переснимется вообще.
    """
    client, _ = service
    r = client.post("/api/baselines/meta", json={
        "platform": PLATFORM, "name": NAME,
        "url": "https://shop.example/checkout",
        "viewport": "1280x800", "selector": ".checkout", "wait": 300,
        "steps": [{"action": "flow", "name": "login"}]})
    assert r.status_code == 200, r.text

    spec = _card(client).json()["spec"]
    assert spec["url"] == "https://shop.example/checkout"
    assert spec["selector"] == ".checkout"
    assert spec["wait"] == 300
    assert spec["steps"] == [{"action": "flow", "name": "login"}]
    assert spec["steps_summary"]


def test_null_removes_a_field_rather_than_being_ignored(service):
    """`None` просто игнорировался: спека умела только расти.

    Снять однажды заданный селектор было нечем. Пустая строка для этого не
    годится — «селектор равен пустой строке» и «селектора нет» разные вещи, и
    первая ломает захват молча.
    """
    client, _ = service
    client.post("/api/baselines/meta", json={
        "platform": PLATFORM, "name": NAME, "selector": ".old"})
    assert _card(client).json()["spec"]["selector"] == ".old"

    client.post("/api/baselines/meta", json={
        "platform": PLATFORM, "name": NAME, "selector": None})
    assert _card(client).json()["spec"]["selector"] is None


def test_clearing_the_url_makes_the_snapshot_unrunnable_and_says_so(service):
    client, _ = service
    client.post("/api/baselines/meta",
                json={"platform": PLATFORM, "name": NAME, "url": None})
    assert _card(client).json()["runnable"] is False


def test_broken_steps_are_refused_with_a_reason(service):
    client, _ = service
    r = client.post("/api/baselines/meta", json={
        "platform": PLATFORM, "name": NAME,
        "steps": [{"action": "no_such_action"}]})
    assert r.status_code == 400
    assert "steps" in r.json()["detail"]


# --------------------------------------------------------------------------- #
#  Зоны принадлежат снимку
# --------------------------------------------------------------------------- #
def test_zones_are_set_as_a_whole_list(service):
    """Интерфейс рисует их мышью, и «удалить вторую из четырёх» добавлением
    не выражается."""
    client, _ = service
    r = client.put("/api/baselines/ignore-boxes", json={
        "platform": PLATFORM, "name": NAME,
        "boxes": [{"x": 10, "y": 20, "w": 30, "h": 40, "reason": "clock"},
                  {"x": 0, "y": 0, "w": 5, "h": 5}]})
    assert r.status_code == 200, r.text
    assert len(_card(client).json()["ignore_boxes"]) == 2

    client.put("/api/baselines/ignore-boxes", json={
        "platform": PLATFORM, "name": NAME,
        "boxes": [{"x": 10, "y": 20, "w": 30, "h": 40}]})
    boxes = _card(client).json()["ignore_boxes"]
    assert len(boxes) == 1 and boxes[0]["x"] == 10


def test_nonsense_zones_are_refused(service):
    client, _ = service
    for boxes, expect in (
            ([{"x": 1, "y": 1, "w": 0, "h": 10}], "non-zero"),
            ([{"x": -5, "y": 1, "w": 10, "h": 10}], "inside the frame"),
            ([{"x": 1, "y": 1, "w": 10}], "required"),
            (["not an object"], "not an object"),
    ):
        r = client.put("/api/baselines/ignore-boxes", json={
            "platform": PLATFORM, "name": NAME, "boxes": boxes})
        assert r.status_code == 400, boxes
        assert expect in r.json()["detail"], (boxes, r.json()["detail"])


def test_a_zone_actually_reaches_the_comparison_engine(service):
    """Иначе зона есть в интерфейсе и не действует — худший из исходов.

    Движок читает боксы из `meta.json` хранилища; проверяем оттуда же, откуда
    он их берёт, а не из ответа API.
    """
    client, _ = service
    client.put("/api/baselines/ignore-boxes", json={
        "platform": PLATFORM, "name": NAME,
        "boxes": [{"x": 5, "y": 5, "w": 20, "h": 20}]})

    from vistest.config import VisTestConfig
    from vistest.storage import FileBaselineStore

    store = FileBaselineStore(VisTestConfig.load().baselines_path(PLATFORM))
    record = store.load(NAME)
    assert record.ignore_boxes == [{"x": 5, "y": 5, "w": 20, "h": 20}]

    mask = store.ignore_mask(NAME, (60, 90))
    assert mask is not None
    assert bool(mask[10, 10]), "внутри зоны движок сравнивать не должен"
    assert not bool(mask[50, 80]), "снаружи зоны — должен"


def test_a_viewer_may_read_the_card_but_not_change_it(service):
    client, mainmod = service
    from vistest.api.auth import create_user
    create_user(mainmod.db, "vic", "password123", role="viewer")
    client.post("/api/auth/login", json={"login": "vic", "password": "password123"})

    assert _card(client).status_code == 200
    assert client.put("/api/baselines/ignore-boxes", json={
        "platform": PLATFORM, "name": NAME, "boxes": []}).status_code == 403
    assert client.post("/api/baselines/meta", json={
        "platform": PLATFORM, "name": NAME, "url": "https://x"}).status_code == 403


def test_the_card_is_not_public(service):
    client, mainmod = service
    from vistest.api.auth import create_user
    create_user(mainmod.db, "anna", "password123", role="admin")
    assert _card(client).status_code == 401


# --------------------------------------------------------------------------- #
#  Чем снят снимок — и чем его перепроверять.
#
#  «Check» и «Re-capture» появлялись, как только у снимка был адрес, и делали
#  ровно одно: грузили этот адрес. Для страницы за входом это означало снять
#  форму логина — прогон, который «ничего не нашёл», или эталон, переписанный
#  страницей входа. Заметить это можно было, только открыв картинку.
# --------------------------------------------------------------------------- #
def _bind_test(mainmod, node="tests/test_shop.py::test_login", project_key=""):
    """Сделать вид, что снимок сняли тестом (обычно это делает адаптер)."""
    from vistest.config import VisTestConfig
    from vistest.storage import FileBaselineStore

    cfg = VisTestConfig.load()
    store = FileBaselineStore(cfg.baselines_path(PLATFORM))
    path = store.dir_for(NAME) / "meta.json"
    import json

    meta = json.loads(path.read_text("utf-8"))
    meta["source"] = {"kind": "test", "test": node, "file": node.split("::")[0],
                      "case": node.split("::")[-1]}
    if project_key:
        meta["source"]["project_key"] = project_key
    path.write_text(json.dumps(meta), encoding="utf-8")


def test_a_snapshot_captured_by_url_says_so(service):
    client, _ = service
    d = _card(client).json()
    assert d["source"]["kind"] in ("url", "unknown")
    assert d["source"]["runnable"] is False


def test_the_card_names_the_test_that_captured_it(service):
    client, mainmod = service
    _bind_test(mainmod, project_key="acme-ui-tests")
    d = _card(client).json()

    assert d["source"]["kind"] == "test"
    assert d["source"]["test"] == "tests/test_shop.py::test_login"
    assert d["source"]["project_key"] == "acme-ui-tests"
    assert d["source"]["runnable"] is True


def test_an_old_snapshot_does_not_pretend_to_know(service):
    """Врать про них «снят по адресу» нельзя: половина снята тестами.

    Предложить проверить такой снимок адресом — предложить снять форму входа.
    """
    client, mainmod = service
    import json

    from vistest.config import VisTestConfig
    from vistest.storage import FileBaselineStore

    path = FileBaselineStore(VisTestConfig.load().baselines_path(PLATFORM)) \
        .dir_for(NAME) / "meta.json"
    meta = json.loads(path.read_text("utf-8"))
    meta.pop("source", None)
    path.write_text(json.dumps(meta), encoding="utf-8")

    assert _card(client).json()["source"]["kind"] == "unknown"


def test_a_snapshot_with_a_test_is_runnable_without_any_url(service):
    """Ради этого всё и написано: тест знает про логин, адрес — нет."""
    client, mainmod = service
    import json

    from vistest.config import VisTestConfig
    from vistest.storage import FileBaselineStore

    path = FileBaselineStore(VisTestConfig.load().baselines_path(PLATFORM)) \
        .dir_for(NAME) / "meta.json"
    meta = json.loads(path.read_text("utf-8"))
    meta.pop("url", None)
    path.write_text(json.dumps(meta), encoding="utf-8")
    _bind_test(mainmod)

    d = _card(client).json()
    assert d["spec"]["url"] is None
    assert d["runnable"] is True


def test_running_a_test_backed_snapshot_goes_through_the_test(service):
    client, mainmod = service
    _bind_test(mainmod)

    r = client.post("/api/baselines/run",
                    json={"platform": PLATFORM, "name": NAME})
    assert r.status_code == 200
    body = r.json()
    assert body["via"] == "test"
    assert body["test"] == "tests/test_shop.py::test_login"

    job = mainmod.runner.get(body["job_id"]) if hasattr(mainmod, "runner") else None
    if job:
        job._cancel.set()


def test_a_snapshot_with_only_a_url_is_still_checked_by_url(service):
    """Старый путь никуда не делся — он верен для страниц, открытых с улицы."""
    client, _ = service
    r = client.post("/api/baselines/run",
                    json={"platform": PLATFORM, "name": NAME})
    assert r.status_code == 200
    assert "via" not in r.json()          # ответ роута прогона по адресу


def test_a_snapshot_with_neither_says_what_is_missing(service):
    """Пустое место рядом с карточкой ответом не является."""
    client, _ = service
    import json

    from vistest.config import VisTestConfig
    from vistest.storage import FileBaselineStore

    path = FileBaselineStore(VisTestConfig.load().baselines_path(PLATFORM)) \
        .dir_for(NAME) / "meta.json"
    meta = json.loads(path.read_text("utf-8"))
    meta.pop("url", None)
    path.write_text(json.dumps(meta), encoding="utf-8")

    r = client.post("/api/baselines/run",
                    json={"platform": PLATFORM, "name": NAME})
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "neither a test nor an address" in detail
    assert "Run the test" in detail


def test_running_a_snapshot_that_does_not_exist_is_a_404(service):
    client, _ = service
    r = client.post("/api/baselines/run",
                    json={"platform": PLATFORM, "name": "nope.png"})
    assert r.status_code == 404


def test_the_list_carries_the_source_too(service):
    """Кнопки живут в списке — значит и решение про них принимается там же.

    Адрес у снимка при этом убран нарочно: иначе `runnable` был бы истинным по
    старой причине, и проверка ничего не сказала бы про связь с тестом.
    """
    client, mainmod = service
    import json

    from vistest.config import VisTestConfig
    from vistest.storage import FileBaselineStore

    path = FileBaselineStore(VisTestConfig.load().baselines_path(PLATFORM)) \
        .dir_for(NAME) / "meta.json"
    meta = json.loads(path.read_text("utf-8"))
    meta.pop("url", None)
    path.write_text(json.dumps(meta), encoding="utf-8")
    _bind_test(mainmod)

    detail = client.get("/api/baselines/detail").json()
    items = [i for p in detail["platforms"] for i in p["items"]]
    one = next(i for i in items if i["name"] == NAME)
    assert one["source"]["runnable"] is True
    assert one["source"]["test"] == "tests/test_shop.py::test_login"
    assert one["runnable"] is True


# --------------------------------------------------------------------------- #
#  Тесты подключённого проекта — видны и читаемы.
#
#  Вкладка «Tests» показывала ровно то, что сгенерировали мы сами, и для
#  подключённого набора отвечала «0» при одиннадцати снимках. Человек видел, что
#  снимки чем-то сняты, но чем именно — в интерфейсе не было нигде.
# --------------------------------------------------------------------------- #
@pytest.fixture
def connected(service, tmp_path):
    """Подключённый проект с одним файлом тестов."""
    client, mainmod = service
    root = tmp_path / "their-repo"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_shop.py").write_text(
        "def test_login(page, visual):\n"
        "    page.goto('https://shop.example/login')\n"
        "    visual.assert_screenshot('login.png')\n", encoding="utf-8")

    from vistest.config import VisTestConfig
    from vistest.projects import Project, ProjectRegistry

    reg = ProjectRegistry(VisTestConfig.load())
    reg.save(Project(key="shop", name="Shop tests", root=str(root),
                     tests="tests"))
    return client, mainmod, root


def test_the_tests_tab_shows_the_projects_files(connected):
    client, _, _ = connected
    data = client.get("/api/tests").json()
    theirs = data["project_tests"]
    assert [t["name"] for t in theirs] == ["tests/test_shop.py"]
    assert theirs[0]["project"] == "shop"
    assert theirs[0]["editable"] is False


def test_their_code_can_be_read(connected):
    """Назвать тест и не дать его прочитать — половина ответа."""
    client, _, _ = connected
    r = client.get("/api/tests/source",
                   params={"name": "tests/test_shop.py", "project": "shop"})
    assert r.status_code == 200
    body = r.json()
    assert "assert_screenshot" in body["text"]
    assert body["editable"] is False


def test_their_code_cannot_be_written(connected):
    """Чужой репозиторий, чужой git, чужое ревью.

    Роут записи вообще не знает про проекты — и правки, посланной под именем
    `tests/test_shop.py`, ему хватит, чтобы отказать: такого файла в нашем
    каталоге тестов нет, а имя со слэшем он не принимает вовсе.
    """
    client, _, _ = connected
    r = client.put("/api/tests/source",
                   json={"name": "tests/test_shop.py", "text": "x = 1"})
    assert r.status_code == 400


def test_a_missing_project_file_is_a_404(connected):
    client, _, _ = connected
    r = client.get("/api/tests/source",
                   params={"name": "tests/nope.py", "project": "shop"})
    assert r.status_code == 404


def test_the_path_is_the_one_pytest_understands(connected):
    """Путь от корня проекта, а не от каталога тестов.

    Два разных написания одного файла означали бы, что связь снимка с тестом не
    сходится по строке — и список «что снимает этот тест» всегда пуст.
    """
    client, _, _ = connected
    name = client.get("/api/tests").json()["project_tests"][0]["name"]
    assert name == "tests/test_shop.py"
    assert not name.startswith("/")


def test_a_test_lists_the_snapshots_it_captures(connected):
    """Связь в обратную сторону: карточка снимка называет тест, тест — снимки."""
    client, mainmod, _ = connected
    _bind_test(mainmod, node="tests/test_shop.py::test_login",
               project_key="shop")

    body = client.get("/api/tests/source",
                      params={"name": "tests/test_shop.py",
                              "project": "shop"}).json()
    assert [x["name"] for x in body["snapshots"]] == [NAME]
    assert body["snapshots"][0]["platform"] == PLATFORM


def test_snapshots_are_grouped_by_file_not_by_case(connected):
    """Параметризация даёт десяток идентификаторов на одну функцию.

    Разбивать их на десяток списков незачем: человек открыл файл и хочет видеть
    всё, что тот снимает. Рядом лежит снимок ЧУЖОГО файла — иначе проверка
    прошла бы и без всякой фильтрации.
    """
    client, mainmod, _ = connected
    import json

    import numpy as np

    from vistest.config import VisTestConfig
    from vistest.storage import BaselineRecord, FileBaselineStore

    _bind_test(mainmod, node="tests/test_shop.py::test_login[mobile]",
               project_key="shop")

    store = FileBaselineStore(VisTestConfig.load().baselines_path(PLATFORM))
    other = "shop.example/cart.png"
    store.save(BaselineRecord(name=other,
                              image=np.full((10, 10, 3), 200, dtype=np.uint8)))
    path = store.dir_for(other) / "meta.json"
    meta = json.loads(path.read_text("utf-8"))
    meta["source"] = {"kind": "test", "test": "tests/test_cart.py::test_cart",
                      "file": "tests/test_cart.py", "project_key": "shop"}
    path.write_text(json.dumps(meta), encoding="utf-8")

    body = client.get("/api/tests/source",
                      params={"name": "tests/test_shop.py",
                              "project": "shop"}).json()
    assert [x["name"] for x in body["snapshots"]] == [NAME]


# --------------------------------------------------------------------------- #
#  Порог на отдельный снимок — третий уровень после глобального и проектного.
#
#  Двух уровней не хватало: один шумный дашборд заставлял ослаблять порог для
#  всего набора, то есть чинить один снимок ценой чувствительности остальных.
# --------------------------------------------------------------------------- #
def _put_thresholds(client, **values):
    return client.post("/api/baselines/meta",
                       json={"platform": PLATFORM, "name": NAME,
                             "thresholds": values})


def test_the_card_says_where_each_threshold_came_from(service):
    """Число без источника не отвечает на вопрос «это я тут выставил или везде».

    А от ответа зависит, где чинить — в снимке или в наборе.
    """
    client, _ = service
    th = _card(client).json()["thresholds"]
    assert th["sources"]["fail_severity"] == "config"
    assert th["own"] == {}

    assert _put_thresholds(client, fail_severity=60).status_code == 200
    th = _card(client).json()["thresholds"]
    assert th["values"]["fail_severity"] == 60.0
    assert th["sources"]["fail_severity"] == "snapshot"
    # И то, что снимок перекрыл, остаётся видно — иначе «вернуть как у всех»
    # превращается в угадывание.
    assert th["inherited"]["fail_severity"] == 25.0


def test_only_the_named_threshold_is_overridden(service):
    """Задать один порог не должно означать оторваться от набора по второму."""
    client, _ = service
    _put_thresholds(client, fail_severity=60)
    th = _card(client).json()["thresholds"]
    assert th["sources"]["max_changed_area_pct"] == "config"


def test_a_threshold_can_be_taken_back(service):
    """Пустое поле — «как у всех», и это не то же самое, что ноль."""
    client, _ = service
    _put_thresholds(client, fail_severity=60)
    assert client.post("/api/baselines/meta",
                       json={"platform": PLATFORM, "name": NAME,
                             "thresholds": None}).status_code == 200
    th = _card(client).json()["thresholds"]
    assert th["own"] == {}
    assert th["sources"]["fail_severity"] == "config"


def test_zero_is_a_value_not_an_absence(service):
    """Ноль значит «падать на любом видимом различии» — самый строгий порог.

    Спутать его с «не задано» значит молча вернуть снимок к общей настройке
    ровно тогда, когда от него хотели предельной строгости.
    """
    client, _ = service
    _put_thresholds(client, fail_severity=0)
    th = _card(client).json()["thresholds"]
    assert th["values"]["fail_severity"] == 0.0
    assert th["sources"]["fail_severity"] == "snapshot"


def test_a_threshold_outside_the_range_is_refused_on_save(service):
    """Движок такое значение молча проигнорирует — а человек будет думать, что
    настроил. Ошибку надо назвать в момент сохранения."""
    client, _ = service
    r = _put_thresholds(client, fail_severity=500)
    assert r.status_code == 400
    assert "0" in r.json()["detail"] and "100" in r.json()["detail"]


def test_a_threshold_that_is_not_a_number_is_refused(service):
    client, _ = service
    assert _put_thresholds(client, fail_severity="strict").status_code == 400


def test_an_unknown_threshold_is_refused(service):
    """Опечатка в имени иначе сохранилась бы и не делала ничего."""
    client, _ = service
    r = _put_thresholds(client, fail_severety=40)
    assert r.status_code == 400
    assert "fail_severity" in r.json()["detail"]


def test_an_unknown_threshold_is_refused_even_when_it_clears_nothing(service):
    """`None` значит «снять порог», и проверку значения проходит мимо.

    Без отдельной проверки имён опечатка в таком виде принималась бы молча:
    человек «снял» порог, которого не существует, и остался с прежним.
    """
    client, _ = service
    r = client.post("/api/baselines/meta",
                    json={"platform": PLATFORM, "name": NAME,
                          "thresholds": {"fail_severety": None}})
    assert r.status_code == 400
