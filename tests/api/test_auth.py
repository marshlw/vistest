# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Вход, роли, управление пользователями."""

from __future__ import annotations

import pytest
from conftest import login, make_admin
from fastapi import HTTPException

from vistest.api import auth as authmod
from vistest.api.auth import (
    hash_password,
    needs_rehash,
    require,
    verify_password,
)


# --------------------------------- пароли ---------------------------------- #
def test_password_hash_roundtrip():
    stored = hash_password("correct horse battery")
    assert verify_password("correct horse battery", stored)
    assert not verify_password("wrong", stored)


def test_password_too_short_rejected():
    with pytest.raises(ValueError):
        hash_password("short")


def test_needs_rehash_on_lower_iterations():
    weak = hash_password("longenough12", iterations=1000)
    assert needs_rehash(weak) is True
    assert needs_rehash(hash_password("longenough12")) is False


# --------------------------------- роли ------------------------------------ #
def test_require_rank(db):
    authmod.create_user(db, "v", "password123", role="viewer")
    tok = authmod.open_session(db, db.one("SELECT id FROM user WHERE login='v'")["id"])
    # viewer доходит до viewer, но не до reviewer/admin
    require(db, tok, "viewer")
    for role in ("reviewer", "admin"):
        with pytest.raises(HTTPException) as e:
            require(db, tok, role)
        assert e.value.status_code == 403


# ------------------------------- локальный режим --------------------------- #
def test_local_mode_until_first_user(client):
    me = client.get("/api/auth/me").json()
    assert me["anonymous"] is True and me["role"] == "admin"


def test_auth_enforced_after_admin(app_client):
    client, db = app_client
    make_admin(db)
    # без входа /me теперь 401
    assert client.get("/api/auth/me").status_code == 401


# ---------------------------------- вход ----------------------------------- #
def test_login_and_me(app_client):
    client, db = app_client
    lg, pw = make_admin(db)
    assert client.post("/api/auth/login",
                       json={"login": lg, "password": "nope"}).status_code == 401
    login(client, lg, pw)
    me = client.get("/api/auth/me").json()
    assert me["login"] == lg and me["role"] == "admin"
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401


# --------------------------- управление людьми ----------------------------- #
def test_only_admin_manages_users(app_client):
    client, db = app_client
    lg, pw = make_admin(db)
    authmod.create_user(db, "rev", "password123", role="reviewer")

    login(client, "rev", "password123")
    assert client.get("/api/users").status_code == 403      # reviewer не может

    client.post("/api/auth/logout")
    login(client, lg, pw)
    users = client.get("/api/users").json()
    assert {u["login"] for u in users["users"]} == {lg, "rev"}

    # админ меняет роль и отключает
    assert client.patch("/api/users/rev", json={"role": "admin"}).status_code == 200
    assert client.patch("/api/users/rev", json={"active": False}).status_code == 200
    assert authmod.session_user(db, "нет-такого-токена") is None


def test_admin_cannot_demote_self(app_client):
    client, db = app_client
    lg, pw = make_admin(db)
    login(client, lg, pw)
    r = client.patch(f"/api/users/{lg}", json={"role": "viewer"})
    assert r.status_code == 400
