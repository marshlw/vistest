# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Вход через корпоративный каталог.

Живого LDAP здесь нет и не будет: тест, которому нужен поднятый сервер,
гоняется один раз при написании и никогда — в CI. Подменяется самый нижний
слой (`find_user` и `check_password`), а проверяется всё, что выше: маппинг
групп на роли, появление человека при первом входе, пересчёт роли, поведение
при недоступном каталоге и — отдельно — экранирование фильтра.

Главное свойство, которое здесь закреплено: локальный вход продолжает работать
всегда. Инсталляция, в которую можно попасть только через лежащий сервис, —
это инсталляция, которую некому чинить.
"""

from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient

from vistest.api import directory

# --------------------------------------------------------------------------- #
#  Подставной каталог
# --------------------------------------------------------------------------- #
PEOPLE = {
    "anna": {
        "dn": "CN=Anna,OU=QA,DC=acme,DC=local",
        "name": "Anna Petrova",
        "mail": "anna@acme.local",
        "groups": ["CN=QA Leads,OU=Groups,DC=acme,DC=local",
                   "CN=Everyone,OU=Groups,DC=acme,DC=local"],
        "password": "directory-secret",
    },
    "boris": {
        "dn": "CN=Boris,OU=QA,DC=acme,DC=local",
        "name": "Boris Ivanov",
        "mail": "boris@acme.local",
        "groups": ["CN=QA,OU=Groups,DC=acme,DC=local"],
        "password": "another-secret",
    },
    "clara": {
        "dn": "CN=Clara,OU=Sales,DC=acme,DC=local",
        "name": "Clara Weiss",
        "mail": "clara@acme.local",
        "groups": ["CN=Sales,OU=Groups,DC=acme,DC=local"],
        "password": "sales-secret",
    },
}

ROLE_MAP = ("CN=QA Leads,OU=Groups,DC=acme,DC=local=admin\n"
            "CN=QA,OU=Groups,DC=acme,DC=local=reviewer")


@pytest.fixture
def fake_directory(monkeypatch):
    """Каталог, который отвечает из словаря. `down` роняет его на лету."""
    state = {"down": False, "asked": []}

    def find_user(cfg, login):
        if state["down"]:
            raise directory.DirectoryError("the server is not answering")
        state["asked"].append(login)
        person = PEOPLE.get(login)
        return None if person is None else {k: v for k, v in person.items()
                                            if k != "password"}

    def check_password(cfg, dn, password):
        if state["down"]:
            raise directory.DirectoryError("the server is not answering")
        if not password:
            return False
        return any(p["dn"] == dn and p["password"] == password
                   for p in PEOPLE.values())

    monkeypatch.setattr(directory, "find_user", find_user)
    monkeypatch.setattr(directory, "check_password", check_password)
    return state


def _cfg(**over) -> directory.Settings:
    cfg = directory.Settings(
        enabled=True, server="ldaps://dc.acme.local", base_dn="DC=acme,DC=local",
        role_map=directory.parse_role_map(ROLE_MAP), default_role="viewer")
    for k, v in over.items():
        setattr(cfg, k, v)
    return cfg


# --------------------------------------------------------------------------- #
#  Разбор настроек
# --------------------------------------------------------------------------- #
def test_the_role_map_is_case_insensitive_about_group_names():
    """Каталоги не различают регистр в DN, а человек напишет как придётся."""
    mapping = directory.parse_role_map("CN=QA Leads,DC=acme=admin")
    assert mapping["cn=qa leads,dc=acme"] == "admin"


def test_a_line_with_an_unknown_role_is_ignored():
    """Опечатка в роли не должна тихо выдавать права."""
    assert directory.parse_role_map("CN=QA,DC=acme=administrator") == {}
    assert directory.parse_role_map("# комментарий\n\nCN=X,DC=y=viewer") == {
        "cn=x,dc=y": "viewer"}


def test_the_highest_matching_role_wins():
    cfg = _cfg()
    assert directory.role_for(cfg, PEOPLE["anna"]["groups"]) == "admin"
    assert directory.role_for(cfg, PEOPLE["boris"]["groups"]) == "reviewer"
    # Ни одной знакомой группы — роль по умолчанию.
    assert directory.role_for(cfg, PEOPLE["clara"]["groups"]) == "viewer"


def test_a_login_cannot_rewrite_the_search_filter():
    """LDAP-инъекция: `*` иначе совпадает со всем каталогом сразу."""
    assert directory._escape("*") == "\\2a"
    assert directory._escape(")(uid=admin") == "\\29\\28uid=admin"
    assert directory._escape("anna") == "anna"


# --------------------------------------------------------------------------- #
#  Аутентификация
# --------------------------------------------------------------------------- #
def test_the_directory_confirms_a_correct_password(fake_directory):
    person = directory.authenticate(_cfg(), "anna", "directory-secret")
    assert person and person["role"] == "admin"
    assert person["name"] == "Anna Petrova"


def test_a_wrong_password_is_refused(fake_directory):
    assert directory.authenticate(_cfg(), "anna", "nope") is None


def test_an_empty_password_is_refused(fake_directory):
    """Пустой пароль в LDAP — это анонимный bind, и он УДАЁТСЯ."""
    assert directory.authenticate(_cfg(), "anna", "") is None


def test_an_unknown_person_is_refused(fake_directory):
    assert directory.authenticate(_cfg(), "nobody", "whatever") is None


def test_a_disabled_integration_is_not_asked(fake_directory):
    assert directory.authenticate(_cfg(enabled=False), "anna",
                                  "directory-secret") is None
    assert fake_directory["asked"] == []


# --------------------------------------------------------------------------- #
#  Сервис целиком
# --------------------------------------------------------------------------- #
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

    import vistest.api.check as checkmod
    importlib.reload(checkmod)
    import vistest.api.main as mainmod
    importlib.reload(mainmod)
    return TestClient(mainmod.app), mainmod


def _enable_ldap(db):
    directory.save_settings(db, {
        "enabled": True,
        "server": "ldaps://dc.acme.local",
        "base_dn": "DC=acme,DC=local",
        "role_map": ROLE_MAP,
        "default_role": "viewer",
    }, "test")


def _local_admin(mainmod, login="root", password="password123"):
    from vistest.api.auth import create_user
    create_user(mainmod.db, login, password, role="admin")
    return login, password


def test_a_person_from_the_directory_appears_on_first_sign_in(
        service, fake_directory):
    """Ни импорта, ни задания синхронизации: человек появляется, когда пришёл."""
    client, mainmod = service
    _local_admin(mainmod)
    _enable_ldap(mainmod.db)

    r = client.post("/api/auth/login",
                    json={"login": "anna", "password": "directory-secret"})
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "admin", "роль берётся из групп"

    row = mainmod.db.one("SELECT * FROM user WHERE login='anna'")
    assert row["source"] == "ldap"
    assert row["external_dn"] == PEOPLE["anna"]["dn"]
    assert row["name"] == "Anna Petrova"


def test_a_directory_account_cannot_be_opened_with_a_local_password(
        service, fake_directory):
    """«Отключили в AD» должно означать «войти нельзя», а не «одним из двух»."""
    client, mainmod = service
    _local_admin(mainmod)
    _enable_ldap(mainmod.db)
    client.post("/api/auth/login",
                json={"login": "boris", "password": "another-secret"})

    # Пытаемся подсунуть локальный хеш тому же логину.
    from vistest.api.auth import hash_password
    mainmod.db.execute("UPDATE user SET password=? WHERE login='boris'",
                       (hash_password("local-backdoor"),))

    r = client.post("/api/auth/login",
                    json={"login": "boris", "password": "local-backdoor"})
    assert r.status_code == 401, r.text


def test_the_role_follows_group_membership_on_every_sign_in(
        service, fake_directory, monkeypatch):
    client, mainmod = service
    _local_admin(mainmod)
    _enable_ldap(mainmod.db)

    client.post("/api/auth/login",
                json={"login": "anna", "password": "directory-secret"})
    assert mainmod.db.one("SELECT role FROM user WHERE login='anna'")["role"] \
        == "admin"

    # Роль, поднятая руками в VisTest, переживает вход: иначе «сделай Анну
    # администратором здесь» отменялось бы само.
    mainmod.db.execute("UPDATE user SET role='admin' WHERE login='boris'")
    client.post("/api/auth/login",
                json={"login": "boris", "password": "another-secret"})


def test_a_local_administrator_still_gets_in_when_the_directory_is_down(
        service, fake_directory):
    """Ради этого каталог и спрашивается вторым."""
    client, mainmod = service
    login, password = _local_admin(mainmod)
    _enable_ldap(mainmod.db)
    fake_directory["down"] = True

    r = client.post("/api/auth/login",
                    json={"login": login, "password": password})
    assert r.status_code == 200, r.text


def test_a_directory_outage_looks_like_a_refusal_not_a_stack_trace(
        service, fake_directory):
    """Анониму не рассказывают про устройство внутренней сети."""
    client, mainmod = service
    _local_admin(mainmod)
    _enable_ldap(mainmod.db)
    fake_directory["down"] = True

    r = client.post("/api/auth/login",
                    json={"login": "anna", "password": "directory-secret"})
    assert r.status_code == 401
    assert "acme" not in r.text.lower()

    # Но в журнале причина есть — иначе разбираться будет не с чем.
    rows = mainmod.db.query(
        "SELECT action, target FROM audit WHERE action='ldap.unavailable'")
    assert rows and "not answering" in rows[0]["target"]


def test_the_settings_screen_never_returns_the_service_password(
        service, monkeypatch):
    client, mainmod = service
    login, password = _local_admin(mainmod)
    client.post("/api/auth/login", json={"login": login, "password": password})
    monkeypatch.setenv(directory.ENV_BIND_PASSWORD, "very-secret")

    state = client.get("/api/ldap").json()
    assert state["bind_password_set"] is True
    assert "very-secret" not in str(state)


def test_the_licence_limit_applies_to_directory_users(
        service, fake_directory, monkeypatch):
    """Учётная запись из каталога занимает место так же, как заведённая руками."""
    client, mainmod = service
    _local_admin(mainmod)
    _enable_ldap(mainmod.db)

    from vistest import licensing
    from vistest.api import license as licmod

    lic = licensing.community()
    lic.users = 1
    monkeypatch.setattr(licmod, "_cached", lic)

    r = client.post("/api/auth/login",
                    json={"login": "anna", "password": "directory-secret"})

    # 402, а не 401, и это разница по существу. Пароль ВЕРНЫЙ — человек прошёл
    # каталог. Ответить ему «неверный логин или пароль» значит отправить его
    # менять пароль, который в порядке, и получить обращение в поддержку не по
    # адресу. Утечкой это не является: так отвечают только тому, кто уже
    # доказал, что он этот человек.
    assert r.status_code == 402, r.text
    assert "active users" in r.text
    assert not mainmod.db.one("SELECT id FROM user WHERE login='anna'"), \
        "и записи не появляется: место занимать нечем"
