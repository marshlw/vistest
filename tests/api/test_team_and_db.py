# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Профиль команды, схема БД и мягкий лок ревью (claim)."""

from __future__ import annotations

from conftest import login, make_admin


# ------------------------------ профиль команды ---------------------------- #
def test_team_profile(app_client):
    client, db = app_client
    # по умолчанию пусто, GET публичный. Цвет — акцент интерфейса: команда,
    # которая своего не выбрала, не должна выглядеть как чужая.
    assert client.get("/api/team").json() == {"name": "", "brand_color": "#C2410C"}

    lg, pw = make_admin(db)
    # без входа менять нельзя
    assert client.put("/api/team", json={"name": "X"}).status_code == 401
    login(client, lg, pw)
    r = client.put("/api/team", json={"name": "Команда shop-web",
                                      "brand_color": "#123456"})
    assert r.status_code == 200
    got = client.get("/api/team").json()
    assert got["name"] == "Команда shop-web" and got["brand_color"] == "#123456"


# -------------------------------- схема БД --------------------------------- #
def test_schema_has_new_tables(db):
    names = {r["name"] for r in db.query(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    for t in ("user", "session", "audit", "team", "invite",
              "presence", "review_claim"):
        assert t in names, f"нет таблицы {t}"


# ------------------------- claim: истечение по времени --------------------- #
def _claim_fresh(db, comp_id):
    return db.one("SELECT * FROM review_claim WHERE comparison_id=?"
                  " AND expires_at > datetime('now')", (comp_id,))


def test_review_claim_expiry(db):
    # свежий claim виден
    db.execute("INSERT INTO review_claim(comparison_id,user_id,login,expires_at)"
               " VALUES(?,?,?,datetime('now','+120 seconds'))", (1, 7, "petr"))
    assert _claim_fresh(db, 1) is not None

    # протухший — уже нет
    db.execute("UPDATE review_claim SET expires_at=datetime('now','-1 second')"
               " WHERE comparison_id=1")
    assert _claim_fresh(db, 1) is None


def test_presence_online_window(db):
    db.execute("INSERT INTO presence(user_id,login,last_seen)"
               " VALUES(1,'a',datetime('now'))")
    db.execute("INSERT INTO presence(user_id,login,last_seen)"
               " VALUES(2,'b',datetime('now','-5 minutes'))")
    online = db.query("SELECT login FROM presence"
                      " WHERE last_seen > datetime('now','-60 seconds')")
    assert {r["login"] for r in online} == {"a"}
