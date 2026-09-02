# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Гонки на одноразовых дверях и судьба сессий после смены пароля.

Здесь проверяется то, что на однопользовательской машине не воспроизводится
никогда, а на сервере внутри периметра случается само: два запроса, пришедшие
одновременно на действие, которое обязано случиться ровно один раз.

Все три двери в этом сервисе одноразовы по смыслу — приглашение, окно первого
администратора, — и все три были написаны как «проверить, потом сделать, потом
пометить». Между «проверить» и «пометить» лежит `create_user`, а он считает
PBKDF2 в 480 000 раундов: окно гонки открыто не миллисекунду, а сотни. Ссылку
на приглашение при этом кидают в общий чат.
"""

from __future__ import annotations

import threading

from conftest import login, make_admin

from vistest.api import auth as authmod


def _in_parallel(fn, count):
    """Запустить `fn(i)` в нескольких потоках и собрать результаты."""
    out: list = [None] * count
    start = threading.Barrier(count)

    def run(i):
        start.wait()
        try:
            out[i] = fn(i)
        except Exception as e:                            # pragma: no cover
            out[i] = e

    threads = [threading.Thread(target=run, args=(i,)) for i in range(count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return out


# --------------------------------------------------------------------------- #
#  Приглашение
# --------------------------------------------------------------------------- #
def test_one_invite_makes_one_account_even_under_a_race(app_client):
    """Ссылка одноразовая — значит и учётная запись из неё одна.

    Раньше `accept-invite` проверял `valid_invite()`, потом создавал
    пользователя, и только потом помечал ссылку использованной. Два человека,
    нажавшие ссылку из чата одновременно, оба проходили проверку и оба
    заводили себе учётку с ролью, которую администратор выдал ОДНОМУ.
    Последовательный второй приём отказывал — и именно поэтому дыру не было
    видно ни в одном тесте.
    """
    client, db = app_client
    lg, pw = make_admin(db)
    login(client, lg, pw)
    token = client.post("/api/invites", json={"role": "admin"}).json()["token"]

    def accept(i):
        return client.post("/api/auth/accept-invite", json={
            "token": token, "login": f"guest{i}", "password": "password123"},
        ).status_code

    codes = _in_parallel(accept, 6)
    assert codes.count(200) == 1, f"приглашение приняли несколько раз: {codes}"
    created = db.query("SELECT login FROM user WHERE login LIKE 'guest%'")
    assert len(created) == 1, f"учёток из одной ссылки: {created}"


def test_a_failed_accept_gives_the_invite_back(app_client):
    """Опечатка в логине не должна сжигать ссылку.

    Пометка теперь стоит ПЕРВОЙ — значит на всех путях, где создать
    пользователя не удалось, приглашение обязано вернуться в оборот. Иначе
    короткий пароль превращается в поход к администратору за новой ссылкой.
    """
    client, db = app_client
    lg, pw = make_admin(db)
    login(client, lg, pw)
    token = client.post("/api/invites", json={"role": "viewer"}).json()["token"]

    bad = client.post("/api/auth/accept-invite", json={
        "token": token, "login": "petr", "password": "short"})
    assert bad.status_code == 400
    assert client.get(f"/api/auth/invite/{token}").json()["valid"] is True

    ok = client.post("/api/auth/accept-invite", json={
        "token": token, "login": "petr", "password": "password123"})
    assert ok.status_code == 200


# --------------------------------------------------------------------------- #
#  Окно первого администратора
# --------------------------------------------------------------------------- #
def test_the_setup_window_admits_exactly_one_administrator(app_client):
    """«Занять инсталляцию» — ровно один раз, а не «примерно один».

    Свежая инсталляция, открытая по сети, показывает форму первого
    администратора. Проверка «ещё можно занять» и закрытие флага стояли по
    краям `create_user`, и в окно между ними проходили оба запроса: два
    администратора там, где обещан один.
    """
    client, db = app_client

    def setup(i):
        return client.post("/api/auth/setup", json={
            "login": f"boss{i}", "password": "password123", "name": "Boss"},
        ).status_code

    codes = _in_parallel(setup, 5)
    assert codes.count(200) == 1, f"окно сработало несколько раз: {codes}"
    admins = db.query("SELECT login FROM user WHERE role='admin'")
    assert len(admins) == 1, f"администраторов заведено: {admins}"


def test_a_failed_setup_leaves_the_window_open(app_client):
    """Иначе неудачная попытка запирает инсталляцию навсегда.

    Флаг закрывается ДО создания пользователя — значит упавшее создание обязано
    открыть его обратно. Без этого короткий пароль в форме первого
    администратора оставлял бы сервис без администратора и без единого способа
    его завести.
    """
    client, db = app_client
    bad = client.post("/api/auth/setup", json={
        "login": "boss", "password": "short", "name": "Boss"})
    assert bad.status_code == 400
    assert client.get("/api/auth/state").json()["needs_setup"] is True

    ok = client.post("/api/auth/setup", json={
        "login": "boss", "password": "password123", "name": "Boss"})
    assert ok.status_code == 200
    assert client.get("/api/auth/state").json()["needs_setup"] is False


# --------------------------------------------------------------------------- #
#  Сессии и пароль
# --------------------------------------------------------------------------- #
def test_changing_a_password_closes_the_other_sessions(app_client):
    """Пароль меняют срочно ровно затем, чтобы выгнать чужого.

    Сессии здесь серверные и о пароле ничего не знают, поэтому смена пароля
    сама по себе не закрывала ни одной: человек, у которого пароль увели, менял
    его — и чужая сессия работала ещё две недели. Своя сессия при этом обязана
    выжить: выкидывать человека из его же браузера за правильный поступок
    незачем.
    """
    client, db = app_client
    authmod.create_user(db, "anna", "password123", role="reviewer")
    stolen = authmod.open_session(db, db.one("SELECT id FROM user WHERE login='anna'")["id"])
    login(client, "anna", "password123")

    r = client.post("/api/auth/password",
                    json={"old": "password123", "new": "password4567"})
    assert r.status_code == 200

    assert authmod.session_user(db, stolen) is None, "чужая сессия пережила смену пароля"
    assert client.get("/api/auth/me").status_code == 200, "своя сессия не должна закрываться"


def test_an_admin_reset_closes_every_session_of_that_user(app_client):
    """Сброс пароля администратором — это «закрыть дверь», а не «сменить табличку»."""
    client, db = app_client
    lg, pw = make_admin(db)
    authmod.create_user(db, "petr", "password123", role="viewer")
    uid = db.one("SELECT id FROM user WHERE login='petr'")["id"]
    theirs = authmod.open_session(db, uid)
    login(client, lg, pw)

    r = client.patch("/api/users/petr", json={"password": "password4567"})
    assert r.status_code == 200
    assert authmod.session_user(db, theirs) is None
