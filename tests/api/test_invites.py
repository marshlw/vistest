# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Приглашения: создание, приём, истечение, отзыв."""

from __future__ import annotations

from conftest import login, make_admin

from vistest.api import auth as authmod


def test_invite_lifecycle(app_client):
    client, db = app_client
    lg, pw = make_admin(db)
    login(client, lg, pw)

    inv = client.post("/api/invites", json={"role": "reviewer"}).json()
    assert inv["role"] == "reviewer" and inv["token"]

    # публичная проверка — до приёма ссылка валидна
    check = client.get(f"/api/auth/invite/{inv['token']}").json()
    assert check["valid"] is True and check["role"] == "reviewer"

    # приём: создаётся пользователь нужной роли и открывается сессия
    r = client.post("/api/auth/accept-invite", json={
        "token": inv["token"], "login": "petr", "password": "password123"})
    assert r.status_code == 200 and r.json()["role"] == "reviewer"
    assert authmod.session_user is not None
    row = db.one("SELECT role FROM user WHERE login='petr'")
    assert row["role"] == "reviewer"

    # повторный приём той же ссылки — отказ (одноразовая)
    r2 = client.post("/api/auth/accept-invite", json={
        "token": inv["token"], "login": "petr2", "password": "password123"})
    assert r2.status_code == 400


def test_expired_invite_invalid(app_client):
    client, db = app_client
    lg, pw = make_admin(db)
    login(client, lg, pw)
    inv = client.post("/api/invites", json={"role": "viewer"}).json()
    # состарим приглашение вручную
    db.execute("UPDATE invite SET expires_at=datetime('now','-1 day') WHERE token=?",
               (inv["token"],))
    assert client.get(f"/api/auth/invite/{inv['token']}").json()["valid"] is False
    r = client.post("/api/auth/accept-invite", json={
        "token": inv["token"], "login": "late", "password": "password123"})
    assert r.status_code == 400


def test_only_admin_creates_invites(app_client):
    client, db = app_client
    make_admin(db)
    authmod.create_user(db, "rev", "password123", role="reviewer")
    login(client, "rev", "password123")
    assert client.post("/api/invites", json={"role": "viewer"}).status_code == 403


def test_revoke_invite(app_client):
    client, db = app_client
    lg, pw = make_admin(db)
    login(client, lg, pw)
    inv = client.post("/api/invites", json={"role": "viewer"}).json()
    assert client.delete(f"/api/invites/{inv['token']}").status_code == 200
    assert client.get(f"/api/auth/invite/{inv['token']}").json()["valid"] is False
