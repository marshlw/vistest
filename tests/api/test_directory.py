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

    _sign_in(client, "anna")
    assert _role(mainmod.db, "anna") == "admin"

    # A role raised by hand in VisTest survives the sign-in: otherwise "make
    # Boris an admin here" would undo itself. Boris has to exist first — the
    # version of this test that raised him before his first sign-in updated no
    # row and asserted nothing.
    _sign_in(client, "boris")
    assert _role(mainmod.db, "boris") == "reviewer"
    _as_root(client)
    assert client.patch("/api/users/boris", json={"role": "admin"}).status_code == 200

    _sign_in(client, "boris")
    assert _role(mainmod.db, "boris") == "admin"


# --------------------------------------------------------------------------- #
#  The role follows the directory both ways; a hand-set role is pinned
# --------------------------------------------------------------------------- #
def _sign_in(client, login, password=None):
    password = password or PEOPLE[login]["password"]
    r = client.post("/api/auth/login", json={"login": login, "password": password})
    assert r.status_code == 200, r.text
    return r


def _as_root(client):
    _sign_in(client, "root", "password123")


def _role(db, login):
    return db.one("SELECT role FROM user WHERE login=?", (login,))["role"]


def _pinned(db, login):
    return db.one("SELECT role_manual FROM user WHERE login=?", (login,))["role_manual"]


def _audit(db, action):
    return [(r["who"], r["target"], r["details"]) for r in db.query(
        "SELECT who, target, details FROM audit WHERE action=? ORDER BY id", (action,))]


def _leave_groups(monkeypatch, login, groups):
    monkeypatch.setitem(PEOPLE, login, {**PEOPLE[login], "groups": groups})


QA = "CN=QA,OU=Groups,DC=acme,DC=local"


def test_leaving_the_admins_group_takes_the_role_at_the_next_sign_in(
        service, fake_directory, monkeypatch):
    """(a) Down as well as up: removed from QA Leads, Anna is no longer an admin."""
    import json

    client, mainmod = service
    _local_admin(mainmod)
    _enable_ldap(mainmod.db)
    _sign_in(client, "anna")
    assert _role(mainmod.db, "anna") == "admin"

    # Still in QA: the role map says reviewer.
    _leave_groups(monkeypatch, "anna", [QA])
    r = _sign_in(client, "anna")
    assert r.json()["role"] == "reviewer"
    assert _role(mainmod.db, "anna") == "reviewer"

    # In no mapped group at all: the default role.
    _leave_groups(monkeypatch, "anna", ["CN=Everyone,OU=Groups,DC=acme,DC=local"])
    _sign_in(client, "anna")
    assert _role(mainmod.db, "anna") == "viewer"

    changes = [(who, target, json.loads(details))
               for who, target, details in _audit(mainmod.db, "user.role")]
    assert changes == [("ldap", "anna", {"from": "admin", "to": "reviewer"}),
                       ("ldap", "anna", {"from": "reviewer", "to": "viewer"})]


def test_the_demotion_reaches_a_session_that_is_already_open(
        service, fake_directory, monkeypatch):
    """The role is read from the row on each request, so an open session sees it."""
    client, mainmod = service
    _local_admin(mainmod)
    _enable_ldap(mainmod.db)
    _sign_in(client, "anna")
    other = TestClient(mainmod.app)
    _sign_in(other, "anna")
    assert other.get("/api/users").status_code == 200

    _leave_groups(monkeypatch, "anna", [QA])
    _sign_in(client, "anna")

    assert other.get("/api/users").status_code == 403


def test_a_role_set_by_hand_survives_the_sign_in_both_ways(
        service, fake_directory):
    """(b) Raised by hand stays raised; lowered by hand is not raised back."""
    client, mainmod = service
    _local_admin(mainmod)
    _enable_ldap(mainmod.db)
    _sign_in(client, "anna")          # admin by groups
    _sign_in(client, "boris")         # reviewer by groups

    _as_root(client)
    assert client.patch("/api/users/boris", json={"role": "admin"}).status_code == 200
    assert client.patch("/api/users/anna", json={"role": "viewer"}).status_code == 200
    assert _pinned(mainmod.db, "boris") == 1
    assert _pinned(mainmod.db, "anna") == 1

    _sign_in(client, "boris")
    _sign_in(client, "anna")

    assert _role(mainmod.db, "boris") == "admin"
    assert _role(mainmod.db, "anna") == "viewer"
    assert [who for who, _target, _details in _audit(mainmod.db, "user.role")
            if who == "ldap"] == [], "the directory changed nothing, so nothing recorded"


def test_handing_the_role_back_to_the_directory(service, fake_directory):
    """(c) `{"role": "directory"}` unpins; the next sign-in recomputes the role."""
    client, mainmod = service
    _local_admin(mainmod)
    _enable_ldap(mainmod.db)
    _sign_in(client, "boris")
    _as_root(client)
    client.patch("/api/users/boris", json={"role": "viewer"})
    _sign_in(client, "boris")
    assert _role(mainmod.db, "boris") == "viewer", "pinned"

    _as_root(client)
    r = client.patch("/api/users/boris", json={"role": "directory"})
    assert r.status_code == 200, r.text
    assert _pinned(mainmod.db, "boris") == 0
    assert _role(mainmod.db, "boris") == "viewer", \
        "nothing changes until the directory is asked, at the next sign-in"

    _sign_in(client, "boris")
    assert _role(mainmod.db, "boris") == "reviewer"


def test_a_local_account_has_no_directory_to_hand_its_role_to(service):
    client, mainmod = service
    _local_admin(mainmod)
    from vistest.api.auth import create_user
    create_user(mainmod.db, "dora", "local-password-2", role="reviewer")
    _as_root(client)

    r = client.patch("/api/users/dora", json={"role": "directory"})

    assert r.status_code == 400, r.text
    assert "local account" in r.text
    assert _role(mainmod.db, "dora") == "reviewer"


def test_an_administrator_cannot_hand_their_own_role_to_the_directory(
        service, fake_directory):
    """The same guard as "cannot remove the administrator role from yourself"."""
    client, mainmod = service
    _local_admin(mainmod)
    _enable_ldap(mainmod.db)
    _sign_in(client, "anna")
    client.patch("/api/users/anna", json={"role": "admin"})    # anna pins herself

    r = client.patch("/api/users/anna", json={"role": "directory"})

    assert r.status_code == 400, r.text
    assert _pinned(mainmod.db, "anna") == 1


def test_the_last_active_administrator_is_not_demoted(
        service, fake_directory, monkeypatch):
    """(d) A broken role map must not leave the installation without an admin."""
    import json

    client, mainmod = service
    _local_admin(mainmod)
    _enable_ldap(mainmod.db)
    _sign_in(client, "anna")
    # Root switched off: Anna is now the only active administrator.
    mainmod.db.execute("UPDATE user SET active=0, status='disabled' WHERE login='root'")

    _leave_groups(monkeypatch, "anna", [QA])
    r = _sign_in(client, "anna")

    assert r.json()["role"] == "admin"
    assert _role(mainmod.db, "anna") == "admin"
    kept = _audit(mainmod.db, "auth.role_kept")
    assert [(who, target) for who, target, _ in kept] == [("ldap", "anna")]
    details = json.loads(kept[0][2])
    assert details["role"] == "admin" and details["directory_role"] == "reviewer"
    assert "last active administrator" in details["reason"]
    assert _audit(mainmod.db, "user.role") == [], "no change was made, none recorded"

    # With a second active administrator, the same sign-in does demote her.
    mainmod.db.execute("UPDATE user SET active=1, status='active' WHERE login='root'")
    _sign_in(client, "anna")
    assert _role(mainmod.db, "anna") == "reviewer"


def test_an_old_database_carries_hand_set_directory_roles_over(tmp_path):
    """(e) Opened with this code, a database made by d048b25 keeps the decisions.

    A directory account whose role an administrator set by hand (there is a
    `user.role` row for it in the audit) comes out pinned; one without such a
    row follows the directory; local accounts are left as they were.
    """
    import sqlite3

    import schema_d048b25 as old

    from vistest.api.db import SCHEMA_VERSION, Database

    path = tmp_path / "vistest.db"
    conn = sqlite3.connect(path)
    conn.executescript(old.SCHEMA)
    conn.execute(f"PRAGMA user_version = {old.USER_VERSION}")
    conn.executemany(
        "INSERT INTO user(login, password, role, source) VALUES(?,?,?,?)",
        [("root", "x", "admin", "local"),
         ("dora", "x", "reviewer", "local"),
         ("anna", "ldap", "viewer", "ldap"),
         ("boris", "ldap", "reviewer", "ldap")])
    conn.executemany(
        "INSERT INTO audit(who, action, target, details) VALUES(?,?,?,?)",
        [("root", "user.role", "Anna", '{"role": "viewer"}'),   # as typed in the URL
         ("root", "user.role", "dora", '{"role": "reviewer"}'),
         ("root", "user.disabled", "boris", None)])
    conn.commit()
    conn.close()

    db = Database(path)

    assert db.schema_version() == SCHEMA_VERSION
    rows = {r["login"]: (r["role"], r["role_manual"]) for r in db.query(
        "SELECT login, role, role_manual FROM user")}
    assert rows == {"root": ("admin", 0), "dora": ("reviewer", 0),
                    "anna": ("viewer", 1), "boris": ("reviewer", 0)}

    # The backfill runs once. Handed back to the directory, Anna stays handed
    # back after the service restarts, although the audit row is still there.
    db.execute("UPDATE user SET role_manual=0 WHERE login='anna'")
    Database(path)
    assert db.one("SELECT role_manual FROM user WHERE login='anna'")["role_manual"] == 0


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
        "SELECT action, target FROM audit WHERE action='auth.provider_unavailable'")
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


# --------------------------------------------------------------------------- #
#  A directory entry is not a claim on a local account
# --------------------------------------------------------------------------- #
def _in_directory(monkeypatch, login, password):
    """Put an entry called `login` into the fake directory."""
    monkeypatch.setitem(PEOPLE, login, {
        "dn": f"CN={login},OU=QA,DC=acme,DC=local",
        "name": f"Directory {login}",
        "mail": f"{login}@acme.local",
        "groups": ["CN=QA Leads,OU=Groups,DC=acme,DC=local"],
        "password": password,
    })


def _account(db, login):
    return db.one("SELECT role, password, active, status, source, external_dn"
                  "  FROM user WHERE login=?", (login,))


def test_a_directory_admin_does_not_sign_in_as_the_local_admin(
        service, fake_directory, monkeypatch):
    """(a) Whoever controls "admin" in the directory is not the admin here."""
    client, mainmod = service
    _local_admin(mainmod, "admin", "local-password-1")
    _enable_ldap(mainmod.db)
    _in_directory(monkeypatch, "admin", "directory-password")
    before = dict(_account(mainmod.db, "admin"))

    r = client.post("/api/auth/login",
                    json={"login": "admin", "password": "directory-password"})

    assert r.status_code == 401, r.text
    assert dict(_account(mainmod.db, "admin")) == before, "the row is not touched"
    refused = mainmod.db.query(
        "SELECT who, target FROM audit WHERE action='auth.external_refused'")
    assert [(a["who"], a["target"]) for a in refused] == \
        [("admin", "ldap: a local account has this login")]
    assert "admin" not in fake_directory["asked"], \
        "a password typed for a local account does not travel to the directory"


def test_a_disabled_local_account_is_not_switched_back_on_by_the_directory(
        service, fake_directory, monkeypatch):
    """(b) Switched off here stays off, whatever the directory answers."""
    client, mainmod = service
    _local_admin(mainmod)
    from vistest.api.auth import create_user
    create_user(mainmod.db, "dora", "local-password-2", role="reviewer")
    mainmod.db.execute(
        "UPDATE user SET active=0, status='disabled' WHERE login='dora'")
    _enable_ldap(mainmod.db)
    _in_directory(monkeypatch, "dora", "directory-password")
    before = dict(_account(mainmod.db, "dora"))

    for password in ("directory-password", "local-password-2"):
        r = client.post("/api/auth/login",
                        json={"login": "dora", "password": password})
        assert r.status_code == 401, (password, r.text)

    after = _account(mainmod.db, "dora")
    assert dict(after) == before
    assert (after["active"], after["status"], after["source"]) == (0, "disabled", "local")


def test_the_local_admin_still_signs_in_after_the_directory_tried(
        service, fake_directory, monkeypatch):
    """(c) The refused attempt leaves the local password working."""
    client, mainmod = service
    _local_admin(mainmod, "admin", "local-password-1")
    _enable_ldap(mainmod.db)
    _in_directory(monkeypatch, "admin", "directory-password")

    r = client.post("/api/auth/login",
                    json={"login": "admin", "password": "directory-password"})
    assert r.status_code == 401, r.text

    r = client.post("/api/auth/login",
                    json={"login": "admin", "password": "local-password-1"})
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "admin"
    assert _account(mainmod.db, "admin")["source"] == "local"


def test_a_directory_account_disabled_here_stays_disabled(
        service, fake_directory):
    """The same rule for an account that did come from the directory."""
    client, mainmod = service
    _local_admin(mainmod)
    _enable_ldap(mainmod.db)
    r = client.post("/api/auth/login",
                    json={"login": "boris", "password": "another-secret"})
    assert r.status_code == 200, r.text
    mainmod.db.execute(
        "UPDATE user SET active=0, status='disabled' WHERE login='boris'")

    r = client.post("/api/auth/login",
                    json={"login": "boris", "password": "another-secret"})

    assert r.status_code == 401, r.text
    row = _account(mainmod.db, "boris")
    assert (row["active"], row["status"], row["source"]) == (0, "disabled", "ldap")
