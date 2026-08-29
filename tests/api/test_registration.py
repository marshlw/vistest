# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Первый вход, регистрация и одобрение заявок.

Свежая инсталляция, открытая по сети, показывала стену: «пользователей нет,
зайдите на машину сервиса и выполните `vistest user add`». Стена стояла не зря —
пока активных пользователей нет, `current_user()` раздаёт роль admin каждому,
кто дотянулся до порта, — но выйти из этого состояния изнутри интерфейса было
нельзя вовсе. Человек, раскативший сервис в докере, упирался в неё первым же
экраном.

Проверяется здесь прежде всего то, что легко сделать неправильно:

* форма первого администратора закрывается **навсегда** после первого же
  успеха, и второй раз этим путём не пройти;
* заявка на доступ — это не доступ: по ней нельзя войти, пока её не одобрили;
* заявка не переводит инсталляцию в режим «требуется вход» — иначе первая же
  чужая регистрация запирала бы сервис от всех, включая владельца;
* локальный режим на машине инженера остаётся локальным.
"""

from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VISTEST_ROOT", str(tmp_path / ".vistest"))
    for name in ("VISTEST_AUTH", "VISTEST_ALLOW_OPEN", "VISTEST_SETUP_TOKEN"):
        monkeypatch.delenv(name, raising=False)

    import importlib

    from fastapi import APIRouter

    import vistest.api.auth as authmod
    import vistest.api.db as dbmod

    dbmod._local = threading.local()
    authmod.router = APIRouter()

    import vistest.api.main as mainmod
    importlib.reload(mainmod)
    return mainmod


@pytest.fixture
def client(service):
    """Запросы «с петли» — как на машине инженера."""
    return TestClient(service.app)


@pytest.fixture
def remote(service):
    """Запросы по сети — как из докера или с чужого ноутбука."""
    return TestClient(service.app, client=("10.1.2.3", 40000))


ADMIN = {"login": "anna", "password": "password123", "name": "Анна"}


def _setup(cl, **over):
    return cl.post("/api/auth/setup", json={**ADMIN, **over})


# --------------------------------------------------------------------------- #
#  Первый администратор
# --------------------------------------------------------------------------- #
def test_a_fresh_installation_offers_setup_over_the_network(remote):
    """Вместо стены — форма. Стена не давала выйти из состояния изнутри."""
    state = remote.get("/api/auth/state").json()
    assert state["needs_setup"] is True
    assert state["local_mode"] is False


def test_on_the_service_machine_nothing_is_demanded(client):
    """Локальный режим работал так всегда, и ломать его обновлением нельзя.

    Инженеру на своей машине форма первого администратора не нужна: разделять
    некого.
    """
    state = client.get("/api/auth/state").json()
    assert state["needs_setup"] is True     # завести админа всё ещё можно…
    assert state["local_mode"] is True      # …но никто не заставляет


def test_the_first_account_becomes_an_administrator_and_signs_in(remote):
    r = _setup(remote)
    assert r.status_code == 200
    assert r.json()["role"] == "admin"
    # Кука выдана сразу: заставлять входить логином, который ты завёл секунду
    # назад, — лишний шаг ради ничего.
    assert remote.get("/api/auth/me").json()["login"] == "anna"


def test_the_setup_form_closes_for_good(remote):
    """Второй раз этим путём не пройти — иначе это дверь без замка."""
    assert _setup(remote).status_code == 200
    second = _setup(remote, login="mallory")
    assert second.status_code == 409
    assert remote.get("/api/auth/state").json()["needs_setup"] is False


def test_it_stays_closed_even_if_the_only_admin_is_disabled(remote, service):
    """Отключить админа и занять инсталляцию заново — это не сценарий.

    Иначе достаточно было бы дождаться, пока единственного администратора
    отключат, и дверь открылась бы сама.
    """
    _setup(remote)
    service.db.execute("UPDATE user SET active=0, status='disabled'")
    assert remote.get("/api/auth/state").json()["needs_setup"] is False
    assert _setup(remote, login="mallory").status_code == 409


def test_a_setup_token_is_demanded_when_it_is_set(remote, monkeypatch):
    """В открытой сети «кто первый успел» не годится, и для этого есть токен."""
    monkeypatch.setenv("VISTEST_SETUP_TOKEN", "s3cr3t")
    assert remote.get("/api/auth/state").json()["setup_token_required"] is True
    assert _setup(remote).status_code == 403
    assert _setup(remote, token="nope").status_code == 403
    assert _setup(remote, token="s3cr3t").status_code == 200


def test_a_failed_setup_does_not_close_the_door(remote):
    """Иначе упавшее создание оставляет инсталляцию без администратора — и без
    способа его завести."""
    assert _setup(remote, login="").status_code == 400
    assert remote.get("/api/auth/state").json()["needs_setup"] is True
    assert _setup(remote).status_code == 200


def test_data_is_still_refused_until_an_administrator_exists(remote):
    """Пока активных пользователей нет, роль admin достаётся каждому, кто
    дотянулся до порта. Форма первого входа этого не отменяет."""
    assert remote.get("/api/runs").status_code == 503
    _setup(remote)
    assert remote.get("/api/runs").status_code == 200


# --------------------------------------------------------------------------- #
#  Заявка на доступ
# --------------------------------------------------------------------------- #
def test_a_request_is_not_access(remote):
    """Ровно поэтому открытая регистрация безопасна: это очередь, а не доступ."""
    _setup(remote)
    remote.post("/api/auth/logout")

    r = remote.post("/api/auth/register",
                    json={"login": "vic", "password": "password123"})
    assert r.status_code == 202

    signin = remote.post("/api/auth/login",
                         json={"login": "vic", "password": "password123"})
    assert signin.status_code == 403
    assert "approve" in signin.json()["detail"]


def test_a_pending_request_does_not_lock_everyone_out(client, service):
    """Первая же чужая заявка не должна переводить сервис в «требуется вход».

    Войти было бы некому: сам заявитель не одобрен, а администратора нет —
    сервис запер бы себя от всех, включая владельца.
    """
    from vistest.api.auth import create_user

    create_user(service.db, "vic", "password123", status="pending")
    assert client.get("/api/auth/state").json()["local_mode"] is True
    assert client.get("/api/runs").status_code == 200


def test_a_wrong_password_on_a_pending_account_still_says_nothing(remote):
    """Сообщение про ожидание показывается только тому, кто знает пароль.

    Иначе форма превращается в способ выяснить, какие логины заведены.
    """
    _setup(remote)
    remote.post("/api/auth/logout")
    remote.post("/api/auth/register",
                json={"login": "vic", "password": "password123"})

    r = remote.post("/api/auth/login",
                    json={"login": "vic", "password": "wrong-one"})
    assert r.status_code == 401
    assert "approve" not in r.json()["detail"]


def test_registering_on_an_empty_installation_is_refused(remote):
    """Заявка ушла бы в очередь, которую некому разобрать."""
    r = remote.post("/api/auth/register",
                    json={"login": "vic", "password": "password123"})
    assert r.status_code == 409
    assert "administrator" in r.json()["detail"]


def test_registration_can_be_closed(remote):
    _setup(remote)
    assert remote.put("/api/settings/registration",
                      json={"open": False}).status_code == 200
    assert remote.get("/api/auth/state").json()["registration"] == "closed"

    remote.post("/api/auth/logout")
    r = remote.post("/api/auth/register",
                    json={"login": "vic", "password": "password123"})
    assert r.status_code == 403
    assert "invite" in r.json()["detail"]


def test_a_duplicate_login_is_a_400_not_a_500(remote):
    _setup(remote)
    remote.post("/api/auth/logout")
    remote.post("/api/auth/register",
                json={"login": "vic", "password": "password123"})
    again = remote.post("/api/auth/register",
                        json={"login": "vic", "password": "password123"})
    assert again.status_code == 400


# --------------------------------------------------------------------------- #
#  Одобрение
# --------------------------------------------------------------------------- #
def _with_request(cl):
    _setup(cl)
    cl.post("/api/auth/logout")
    cl.post("/api/auth/register",
            json={"login": "vic", "password": "password123", "name": "Витя"})
    cl.post("/api/auth/login", json={"login": "anna", "password": "password123"})


def test_an_administrator_sees_the_queue(remote):
    _with_request(remote)
    pending = remote.get("/api/users/pending").json()["pending"]
    assert [p["login"] for p in pending] == ["vic"]
    assert pending[0]["name"] == "Витя"


def test_approving_grants_the_role_in_the_same_step(remote):
    """Одобрить «как-нибудь», а потом сходить выставить роль — два шага, второй
    из которых забывают."""
    _with_request(remote)
    r = remote.post("/api/users/vic/approve", json={"role": "reviewer"})
    assert r.status_code == 200

    remote.post("/api/auth/logout")
    me = remote.post("/api/auth/login",
                     json={"login": "vic", "password": "password123"})
    assert me.status_code == 200
    assert me.json()["role"] == "reviewer"


def test_rejecting_lets_the_same_person_apply_again(remote):
    """Отклонённая заявка — след о человеке, которого в системе не было.

    Держать её вечно значит мешать подать заявку заново, если отказ был по
    ошибке.
    """
    _with_request(remote)
    assert remote.post("/api/users/vic/reject").status_code == 200
    assert remote.get("/api/users/pending").json()["pending"] == []

    remote.post("/api/auth/logout")
    assert remote.post("/api/auth/register",
                       json={"login": "vic", "password": "password123"}
                       ).status_code == 202


def test_an_unknown_request_is_a_404(remote):
    _with_request(remote)
    assert remote.post("/api/users/nobody/approve").status_code == 404
    assert remote.post("/api/users/nobody/reject").status_code == 404


def test_an_already_approved_user_cannot_be_approved_twice(remote):
    """Иначе «одобрить» превращается в тихую смену роли мимо журнала ролей."""
    _with_request(remote)
    remote.post("/api/users/vic/approve", json={"role": "viewer"})
    assert remote.post("/api/users/vic/approve",
                       json={"role": "admin"}).status_code == 404


def test_the_queue_is_administrator_only(remote):
    _with_request(remote)
    remote.post("/api/users/vic/approve", json={"role": "reviewer"})
    remote.post("/api/auth/logout")
    remote.post("/api/auth/login", json={"login": "vic", "password": "password123"})

    assert remote.get("/api/users/pending").status_code == 403
    assert remote.post("/api/users/x/approve").status_code == 403
    assert remote.put("/api/settings/registration",
                      json={"open": False}).status_code == 403


def test_a_disabled_user_is_not_a_pending_one(remote, service):
    """`active` не различал «ещё не одобрен» и «отключён администратором».

    Первое ждёт действия админа, второе уже является его действием — и
    отключённый человек не должен всплыть в очереди на одобрение.
    """
    _with_request(remote)
    remote.post("/api/users/vic/approve", json={"role": "viewer"})
    from vistest.api.auth import deactivate

    deactivate(service.db, "vic")
    assert remote.get("/api/users/pending").json()["pending"] == []
    assert service.db.one("SELECT status FROM user WHERE login='vic'"
                          )["status"] == "disabled"
