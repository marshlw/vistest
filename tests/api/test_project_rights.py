# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Роль, которая действует не везде.

`user.role` была одна на инсталляцию, и `reviewer` означало «может утверждать
эталоны» — все эталоны, во всех наборах. Пока проект был один, это и было
верно. Как только к сервису подключили второй набор тестов, утверждение стало
действием над чужим кодом: человек, отвечающий за витрину, одним нажатием
переписывал точку отсчёта биллинга. Не со зла — кнопка была активной, а снимок
красным, и на экране свой набор от чужого ничем не отличается.

Здесь проверяются три вещи, и первая из них важнее двух остальных:

* **обновление ничего не отнимает** — пока прав не выдано, поведение совпадает
  с прежним до мелочей. Модель, которая при накатке образа оставляет команду
  без единого человека, способного утвердить эталон, хуже дыры, которую она
  закрывает;
* право **добавляет и не отнимает**: глобальный `reviewer` не понижается
  выданным `viewer`, иначе администратора можно было бы лишить доступа, а
  чинить это стало бы некому;
* проверка идёт **на каждый объект**, а не на запрос: массовое решение по
  группе причин может собрать снимки нескольких проектов.
"""

from __future__ import annotations

import importlib
import threading

import pytest
from fastapi.testclient import TestClient

from vistest.api import rights
from vistest.api.db import Database


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "vistest.db")


def _user(login: str, role: str = "viewer", anonymous: bool = False) -> dict:
    return {"id": 1, "login": login, "role": role, "anonymous": anonymous}


# --------------------------------------------------------------------------- #
#  Действующая роль
# --------------------------------------------------------------------------- #
def test_without_any_grants_nothing_changes(db):
    """Главная проверка файла: обновление не понижает никого.

    Модель прав, которая при накатке образа оставляет команду без человека,
    способного утвердить эталон, хуже той дыры, которую она закрывает.
    """
    for role in ("viewer", "reviewer", "admin"):
        assert rights.effective(db, _user("u", role), "shop") == role
        assert rights.effective(db, _user("u", role), "") == role


def test_a_grant_raises_the_role_in_one_project_only(db):
    """Ровно тот сценарий, ради которого всё написано."""
    rights.grant(db, "vic", "shop", "reviewer", by="anna")
    vic = _user("vic", "viewer")

    assert rights.allows(db, vic, "reviewer", "shop") is True
    assert rights.allows(db, vic, "reviewer", "billing") is False


def test_a_grant_never_lowers(db):
    """Понижающего права нет намеренно.

    Иначе администратора можно было бы лишить доступа к проекту — а чинить это
    стало бы некому.
    """
    rights.grant(db, "anna", "shop", "viewer", by="anna")
    assert rights.effective(db, _user("anna", "admin"), "shop") == "admin"
    assert rights.effective(db, _user("anna", "reviewer"), "shop") == "reviewer"


def test_a_grant_cannot_be_used_to_bypass_the_global_role(db):
    """Право поднимает, но не выдаёт больше, чем в нём написано."""
    rights.grant(db, "vic", "shop", "reviewer", by="anna")
    assert rights.allows(db, _user("vic", "viewer"), "admin", "shop") is False


def test_a_repeated_grant_replaces_rather_than_piles_up(db):
    rights.grant(db, "vic", "shop", "reviewer", by="anna")
    rights.grant(db, "vic", "shop", "admin", by="anna")
    assert rights.grants_of(db, "vic") == {"shop": "admin"}


def test_a_revoked_grant_is_gone(db):
    rights.grant(db, "vic", "shop", "reviewer", by="anna")
    assert rights.revoke(db, "vic", "shop") is True
    assert rights.allows(db, _user("vic", "viewer"), "reviewer", "shop") is False
    assert rights.revoke(db, "vic", "shop") is False


def test_a_grant_needs_both_a_person_and_a_project(db):
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        rights.grant(db, "vic", "", "reviewer")
    with pytest.raises(HTTPException):
        rights.grant(db, "", "shop", "reviewer")
    with pytest.raises(HTTPException):
        rights.grant(db, "vic", "shop", "owner")


def test_the_refusal_says_which_project_and_what_to_do(db):
    """«Not enough rights» без проекта — самое бесполезное из сообщений.

    Человек видит его на кнопке, которая для соседнего проекта работает, и
    решает, что сломался сервис.
    """
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as e:
        rights.check(db, _user("vic", "viewer"), "reviewer", "billing")
    text = str(e.value.detail)
    assert "billing" in text
    assert "reviewer" in text
    assert "Team" in text, "сказано, кто и где это выдаёт"


# --------------------------------------------------------------------------- #
#  Чей это объект
# --------------------------------------------------------------------------- #
def _run(db, key: str, *, project: str = "demo",
         project_key: str | None = None) -> dict:
    pid = db.project_id(project)
    db.execute("INSERT INTO run(project_id, run_key, platform, project_key)"
               " VALUES(?,?,?,?)", (pid, key, "linux", project_key))
    return db.one("SELECT * FROM run WHERE run_key=?", (key,))


def test_a_run_of_a_connected_suite_belongs_to_that_suite(db):
    """Иначе прогон чужого набора считался бы нашим — и утверждался бы всеми.

    В базе он лежит под своим именем проекта, но право выдают на ключ
    подключённого набора: это одна и та же вещь, названная в двух местах
    по-разному, и выбрать надо ту, которую видит администратор.
    """
    # Имя проекта в базе и ключ подключённого набора нарочно разные: именно
    # так они и расходятся в жизни, и молча взять первое попавшееся значит
    # спрашивать право не на тот проект.
    run = _run(db, "r1", project="Acme API", project_key="acme-ui-tests")
    assert rights.of_run(db, run["id"]) == "acme-ui-tests"


def test_our_own_run_belongs_to_our_own_project(db):
    run = _run(db, "r1", project="demo")
    assert rights.of_run(db, run["id"]) == "demo"


def test_a_comparison_belongs_to_the_project_of_its_run(db):
    run = _run(db, "r1", project="shop")
    sid = db.snapshot_id(run["project_id"], "p.png", "linux", "chromium")
    db.execute("INSERT INTO comparison(run_id, snapshot_id, verdict)"
               " VALUES(?,?,'fail')", (run["id"], sid))
    comp = db.one("SELECT id FROM comparison")
    assert rights.of_comparison(db, comp["id"]) == "shop"


def test_a_missing_object_has_no_project(db):
    """Пустой ключ означает «проект неизвестен» и права не спрашивает.

    Здесь это безопасно: несуществующий прогон всё равно кончится 404, а
    выдумывать проект для того, чего нет, значит запретить действие по
    неправильной причине.
    """
    assert rights.of_run(db, 999) == ""
    assert rights.of_comparison(db, 999) == ""
    assert rights.of_snapshot(db, 999) == ""


def test_the_services_own_set_belongs_to_the_services_own_project(db):
    """Иначе «global» был бы единственным местом, где право не спрашивают.

    А в большинстве инсталляций основной набор лежит именно там — то есть
    изоляция ломалась бы ровно посередине.
    """
    assert rights.of_scope("global", None, own="default") == "default"
    assert rights.of_scope("vistest", "acme", own="default") == "acme"


# --------------------------------------------------------------------------- #
#  Список проектов для выдачи
# --------------------------------------------------------------------------- #
def test_projects_with_history_are_offered_even_if_disconnected(db):
    """Набор отключили, а разбирать его прогоны — нет.

    Списка без них хватило бы ровно до первого отключённого проекта: админ
    ищет его глазами и не находит, а прогоны в истории лежат.
    """
    _run(db, "r1", project="old-suite")
    assert "old-suite" in rights.known(db)


def test_the_list_has_no_duplicates(db):
    _run(db, "r1", project="shop", project_key="shop")
    names = rights.known(db)
    assert len(names) == len(set(names))


# --------------------------------------------------------------------------- #
#  Маршруты
# --------------------------------------------------------------------------- #
@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VISTEST_ROOT", str(tmp_path / ".vistest"))
    monkeypatch.delenv("VISTEST_AUTH", raising=False)

    from fastapi import APIRouter

    import vistest.api.auth as authmod
    import vistest.api.db as dbmod

    dbmod._local = threading.local()
    authmod.router = APIRouter()

    import vistest.api.main as mainmod
    importlib.reload(mainmod)

    authmod.create_user(mainmod.db, "anna", "password123", role="admin")
    authmod.create_user(mainmod.db, "vic", "password123", role="viewer")
    return TestClient(mainmod.app), mainmod.db


def _as(client, login: str) -> None:
    r = client.post("/api/auth/login",
                    json={"login": login, "password": "password123"})
    assert r.status_code == 200, r.text


def _comparison(db, project: str, *, project_key: str | None = None) -> int:
    run = _run(db, f"run-{project}", project=project, project_key=project_key)
    sid = db.snapshot_id(run["project_id"], f"{project}.png", "linux", "chromium")
    db.execute("INSERT INTO comparison(run_id, snapshot_id, verdict, artifacts)"
               " VALUES(?,?,'fail','{}')", (run["id"], sid))
    return db.one("SELECT id FROM comparison ORDER BY id DESC")["id"]


def test_a_reviewer_of_one_project_cannot_decide_for_another(service):
    """Дефект, ради которого всё это написано, — одним запросом."""
    client, sdb = service
    mine = _comparison(sdb, "shop")
    theirs = _comparison(sdb, "billing")

    from vistest.api import rights as r
    r.grant(sdb, "vic", "shop", "reviewer", by="anna")
    _as(client, "vic")

    assert client.post(f"/api/comparisons/{theirs}/reject").status_code == 403
    assert client.post(f"/api/comparisons/{mine}/reject").status_code == 200


def test_a_global_viewer_without_grants_still_decides_nothing(service):
    client, sdb = service
    comp = _comparison(sdb, "shop")
    _as(client, "vic")
    assert client.post(f"/api/comparisons/{comp}/reject").status_code == 403


def test_a_global_reviewer_keeps_working_everywhere(service):
    """Обратная сторона первой проверки, и её надо держать явно.

    Установка, где прав не выдавали, обязана вести себя как раньше — иначе
    закрытие дыры превращается в остановку работы.
    """
    client, sdb = service
    from vistest.api.auth import set_role
    set_role(sdb, "vic", "reviewer")
    comp = _comparison(sdb, "billing")
    _as(client, "vic")
    assert client.post(f"/api/comparisons/{comp}/reject").status_code == 200


def test_a_bulk_decision_is_split_per_project(service):
    """Своё принято, чужое отказано с объяснением.

    «403 на всё» наказало бы за чужой снимок в группе, а «принято всё» — это
    та самая дыра, только через другую кнопку. Группы причин собираются по
    похожести диффа и границ проекта не знают.
    """
    client, sdb = service
    mine = _comparison(sdb, "shop")
    theirs = _comparison(sdb, "billing")

    from vistest.api import rights as r
    r.grant(sdb, "vic", "shop", "reviewer", by="anna")
    _as(client, "vic")

    body = client.post("/api/comparisons/bulk",
                       json={"ids": [mine, theirs], "action": "reject"}).json()
    assert body["done"] == [mine]
    assert [f["id"] for f in body["failed"]] == [theirs]
    assert "billing" in body["failed"][0]["error"]


def test_deleting_someone_elses_run_is_refused(service):
    client, sdb = service
    theirs = _run(sdb, "r-billing", project="billing")
    from vistest.api import rights as r
    r.grant(sdb, "vic", "shop", "reviewer", by="anna")
    _as(client, "vic")

    mine = _run(sdb, "r-shop", project="shop")

    assert client.delete(f"/api/runs/{theirs['id']}").status_code == 403
    # И обратная половина: свой прогон он удалить МОЖЕТ. Без неё проверка
    # проходила бы и на глобальной роли — глобально vic всего лишь viewer,
    # и отказ был бы получен по совсем другой причине.
    assert client.delete(f"/api/runs/{mine['id']}").status_code == 200
    # Прогона нет вовсе — это 404, а не 403: иначе «нет такого» неотличимо от
    # «не пущу», и человек ищет права там, где кончилась ретенция.
    assert client.delete("/api/runs/99999").status_code == 404


# --------------------------------------------------------------------------- #
#  Управление правами
# --------------------------------------------------------------------------- #
def test_only_an_administrator_grants_rights(service):
    client, _sdb = service
    _as(client, "vic")
    r = client.put("/api/users/vic/rights/shop", json={"role": "admin"})
    assert r.status_code == 403


def test_a_right_for_a_person_who_does_not_exist_is_refused(service):
    """Иначе опечатка в логине выглядит как выданное право.

    Строка появляется в списке, человек уверен, что выдал доступ, а коллега
    по-прежнему не может ничего — и виноват в этом «сервис».
    """
    client, _sdb = service
    _as(client, "anna")
    r = client.put("/api/users/nobody/rights/shop", json={"role": "reviewer"})
    assert r.status_code == 404


def test_granting_and_revoking_are_written_into_the_journal(service):
    """Право — это то, о чём спрашивают «кто это выдал» после происшествия."""
    client, sdb = service
    _as(client, "anna")
    client.put("/api/users/vic/rights/shop", json={"role": "reviewer"})
    client.delete("/api/users/vic/rights/shop")

    actions = [r["action"] for r in sdb.query("SELECT action FROM audit")]
    assert "rights.granted" in actions
    assert "rights.revoked" in actions


def test_the_person_sees_their_own_rights(service):
    """Интерфейс гасит кнопки по ним.

    Кнопка, которая не может сработать, — это не строгий бэкенд, это неправда
    в интерфейсе: человек нажимает и получает 403 без объяснения.
    """
    client, sdb = service
    from vistest.api import rights as r
    r.grant(sdb, "vic", "shop", "reviewer", by="anna")
    _as(client, "vic")

    me = client.get("/api/auth/me").json()
    assert me["grants"] == {"shop": "reviewer"}


def test_the_team_list_carries_the_rights_of_everyone(service):
    client, sdb = service
    from vistest.api import rights as r
    r.grant(sdb, "vic", "shop", "reviewer", by="anna")
    _as(client, "anna")

    body = client.get("/api/users").json()
    people = {u["login"]: u for u in body["users"]}
    assert people["vic"]["grants"] == {"shop": "reviewer"}
    assert people["anna"]["grants"] == {}
    assert isinstance(body["projects"], list)


def test_an_installation_without_users_is_still_open(tmp_path, monkeypatch):
    """Одиночный режим — это то, как VisTest работал до появления входа.

    Пока пользователей не завели, доступ полный, а автор решений записывается
    как «local». Права по проектам не должны это трогать: инсталляция на
    ноутбуке инженера не имеет второго человека, от которого надо защищаться,
    а сломать ей работу одним обновлением очень легко.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VISTEST_ROOT", str(tmp_path / ".vistest"))
    monkeypatch.delenv("VISTEST_AUTH", raising=False)

    from fastapi import APIRouter

    import vistest.api.auth as authmod
    import vistest.api.db as dbmod

    dbmod._local = threading.local()
    authmod.router = APIRouter()

    import vistest.api.main as mainmod
    importlib.reload(mainmod)

    client, sdb = TestClient(mainmod.app), mainmod.db
    comp = _comparison(sdb, "billing")
    # Никто не вошёл, пользователей нет — и решение принимается.
    r = client.post(f"/api/comparisons/{comp}/reject")
    assert r.status_code == 200, r.text
    assert r.json()["by"] == "local"


def test_approval_asks_about_the_project_of_the_comparison(service):
    """Утверждение — необратимое действие, и оно проверяется отдельно.

    Отказ чужому проекту сам по себе ничего не доказывает: глобально vic
    всего лишь viewer, и 403 он получил бы в любом случае. Доказывает пара:
    своё он утвердить МОЖЕТ, чужое — нет.
    """
    client, sdb = service
    mine = _comparison(sdb, "shop")
    theirs = _comparison(sdb, "billing")

    from vistest.api import rights as r
    r.grant(sdb, "vic", "shop", "reviewer", by="anna")
    _as(client, "vic")

    assert client.post(f"/api/comparisons/{theirs}/approve",
                       json={}).status_code == 403
    assert client.post(f"/api/comparisons/{mine}/approve",
                       json={}).status_code == 200


# --------------------------------------------------------------------------- #
#  Наборы эталонов
#
#  Эталон — это и есть точка отсчёта. Переписать чужой значит поменять то, с
#  чем чужая команда сравнивается дальше, и заметить это по прогону нельзя:
#  он станет зелёным.
# --------------------------------------------------------------------------- #
def _connect(tmp_path, key: str):
    """Подключить набор так, как это делает интерфейс."""
    from vistest.config import VisTestConfig
    from vistest.projects import Project, ProjectRegistry

    repo = tmp_path / key
    (repo / "tests").mkdir(parents=True, exist_ok=True)
    ProjectRegistry(VisTestConfig.load()).save(
        Project(key=key, name=key, root=str(repo), tests="tests"))


def test_a_baseline_spec_can_be_edited_only_in_your_own_project(service,
                                                                tmp_path):
    client, sdb = service
    _connect(tmp_path, "shop")
    _connect(tmp_path, "billing")

    from vistest.api import rights as r
    r.grant(sdb, "vic", "shop", "reviewer", by="anna")
    _as(client, "vic")

    body = {"platform": "linux", "name": "p.png", "url": "https://e.test/"}
    theirs = client.post("/api/baselines/meta",
                         json={**body, "scope": "project:billing"})
    assert theirs.status_code == 403
    assert "billing" in theirs.json()["detail"]

    # Свой набор: право есть, и отказ — если он будет — уже не про права.
    mine = client.post("/api/baselines/meta",
                       json={**body, "scope": "project:shop"})
    assert mine.status_code != 403, mine.text


def test_the_services_own_set_is_not_a_hole_in_the_middle(service):
    """`global` — самое частое место основного набора.

    Если бы право там не спрашивали, изоляция ломалась бы ровно в том месте,
    где у большинства инсталляций и лежат эталоны.
    """
    client, sdb = service
    from vistest.api import rights as r
    r.grant(sdb, "vic", "shop", "reviewer", by="anna")
    _as(client, "vic")

    body = {"platform": "linux", "name": "p.png", "url": "https://e.test/"}
    assert client.post("/api/baselines/meta", json=body).status_code == 403

    # И обратная половина: право на собственный проект сервиса открывает
    # именно этот набор. Без неё проверка проходила бы и в мире, где «global»
    # просто не спрашивает права ни у кого, — отказ был бы получен по
    # глобальной роли viewer, а не по проекту.
    from vistest.config import VisTestConfig
    r.grant(sdb, "vic", VisTestConfig.load().service.project, "reviewer",
            by="anna")
    assert client.post("/api/baselines/meta", json=body).status_code != 403


# --------------------------------------------------------------------------- #
#  Командная строка
#
#  Тот же набор действий с машины сервиса. Нужен ровно тогда, когда интерфейс
#  недоступен, — и когда права раздаются скриптом при развёртывании.
# --------------------------------------------------------------------------- #
def _cli(argv, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VISTEST_ROOT", str(tmp_path / ".vistest"))
    from vistest.cli import main

    code = main(argv)
    return code, capsys.readouterr()


@pytest.fixture
def cli_db(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VISTEST_ROOT", str(tmp_path / ".vistest"))
    import vistest.api.db as dbmod
    dbmod._local = threading.local()

    from vistest.api.auth import create_user
    from vistest.config import VisTestConfig

    db = Database(VisTestConfig.load().root_path / "vistest.db")
    create_user(db, "vic", "password123", role="viewer")
    return db


def test_a_right_is_granted_from_the_service_machine(cli_db, tmp_path,
                                                     monkeypatch, capsys):
    code, out = _cli(["user", "grant", "vic", "shop", "reviewer"],
                     tmp_path, monkeypatch, capsys)
    assert code == 0
    assert rights.grants_of(cli_db, "vic") == {"shop": "reviewer"}
    assert "raises" in out.out, "сказано, что право не понижает"


def test_the_cli_refuses_a_right_for_a_ghost(cli_db, tmp_path, monkeypatch,
                                             capsys):
    """Опечатка в логине не должна выглядеть как выданное право."""
    code, out = _cli(["user", "grant", "nobody", "shop", "reviewer"],
                     tmp_path, monkeypatch, capsys)
    assert code == 1
    assert "No such user" in out.err


def test_the_cli_lists_who_may_what(cli_db, tmp_path, monkeypatch, capsys):
    rights.grant(cli_db, "vic", "shop", "reviewer", by="cli")
    code, out = _cli(["user", "rights"], tmp_path, monkeypatch, capsys)
    assert code == 0
    assert "vic" in out.out and "shop" in out.out


def test_an_empty_list_says_what_to_do_and_where(cli_db, tmp_path, monkeypatch,
                                                 capsys):
    """Пустой вывод здесь читается как «не работает».

    А это нормальное состояние: прав нет, действует глобальная роль. Заодно
    показываются имена проектов — без них следующая команда пишется наугад.
    """
    code, out = _cli(["user", "rights"], tmp_path, monkeypatch, capsys)
    assert code == 0
    assert "global role applies everywhere" in out.out
    assert "vistest user grant" in out.out


def test_the_cli_revokes_and_says_when_there_was_nothing(cli_db, tmp_path,
                                                         monkeypatch, capsys):
    rights.grant(cli_db, "vic", "shop", "reviewer", by="cli")
    code, _ = _cli(["user", "revoke", "vic", "shop"], tmp_path, monkeypatch,
                   capsys)
    assert code == 0
    assert rights.grants_of(cli_db, "vic") == {}

    code, out = _cli(["user", "revoke", "vic", "shop"], tmp_path, monkeypatch,
                     capsys)
    assert code == 1
    assert "had no right" in out.err
