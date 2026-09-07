# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Схема базы и строки, которые никто никогда не удалял.

Две темы в одном файле, потому что обе про одно: база живёт дольше, чем
процесс, и дольше, чем версия кода, который её открыл.
"""

from __future__ import annotations

import time

import pytest

from vistest.api import retention
from vistest.api.db import SCHEMA_VERSION, Database, SchemaTooNew


# --------------------------------------------------------------------------- #
#  Версия схемы
# --------------------------------------------------------------------------- #
def test_a_fresh_database_gets_the_current_schema_version(tmp_path):
    db = Database(tmp_path / "vistest.db")
    assert db.schema_version() == SCHEMA_VERSION


def test_a_database_from_the_future_is_refused(tmp_path):
    """Старый образ на томе, поработавшем под новой версией.

    Он читает чужую схему и молча пишет в неё половину нужного. Заметно это
    станет через неделю и по совсем другим симптомам — поэтому отказ здесь и
    сейчас, с текстом, объясняющим, что делать.
    """
    path = tmp_path / "vistest.db"
    Database(path)

    with Database(path).connect() as c:
        c.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 5}")

    import threading

    import vistest.api.db as dbmod
    dbmod._local = threading.local()          # соединение кешируется на поток

    with pytest.raises(SchemaTooNew, match="newer version"):
        Database(path)

    dbmod._local = threading.local()


def test_an_older_database_is_migrated_and_stamped(tmp_path):
    """База прошлой версии догоняет молча — так было и должно остаться."""
    path = tmp_path / "vistest.db"
    Database(path)

    import threading

    import vistest.api.db as dbmod
    with Database(path).connect() as c:
        c.execute("PRAGMA user_version = 0")
    dbmod._local = threading.local()

    db = Database(path)
    assert db.schema_version() == SCHEMA_VERSION
    # И миграции действительно применены, а не просто проставлен номер.
    with db.connect() as c:
        columns = {r[1] for r in c.execute("PRAGMA table_info(run)")}
    assert {"project_key", "baseline_scope", "baseline_dir"} <= columns
    dbmod._local = threading.local()


# --------------------------------------------------------------------------- #
#  Служебные таблицы
# --------------------------------------------------------------------------- #
def test_the_audit_log_is_swept_but_recent_entries_stay(tmp_path):
    """Журнал читается на КАЖДУЮ попытку входа и не чистился никогда."""
    db = Database(tmp_path / "vistest.db")
    db.execute("INSERT INTO audit(at, who, action) "
               "VALUES(datetime('now','-800 days'),'anna','login.ok')")
    db.execute("INSERT INTO audit(at, who, action) "
               "VALUES(datetime('now','-2 days'),'anna','baseline.approved')")

    result = retention.sweep_service_tables(db, audit_days=365)

    assert result["audit"] == 1
    rows = db.query("SELECT action FROM audit")
    assert [r["action"] for r in rows] == ["baseline.approved"]


def test_expired_sessions_and_claims_go_away(tmp_path):
    """Сессии удалялись только при чьём-то входе, заявки на разбор — никогда."""
    db = Database(tmp_path / "vistest.db")
    db.execute("INSERT INTO user(id, login, password) VALUES(1,'anna','x')")
    db.execute("INSERT INTO session(token, user_id, expires_at)"
               " VALUES('dead', 1, datetime('now','-1 day'))")
    db.execute("INSERT INTO session(token, user_id, expires_at)"
               " VALUES('alive', 1, datetime('now','+1 day'))")
    db.execute("INSERT INTO review_claim(comparison_id, user_id, expires_at)"
               " VALUES(1, 1, datetime('now','-1 hour'))")

    retention.sweep_service_tables(db)

    assert [r["token"] for r in db.query("SELECT token FROM session")] == ["alive"]
    assert db.query("SELECT comparison_id FROM review_claim") == []


def test_finished_jobs_are_swept_but_the_recent_ones_are_kept(tmp_path):
    """Задача хранит хвост лога — это не десяток байт на строку."""
    db = Database(tmp_path / "vistest.db")
    old = time.time() - 400 * 86400
    db.execute("INSERT INTO job(id, status, finished_at) VALUES('old','done',?)",
               (old,))
    db.execute("INSERT INTO job(id, status, finished_at) VALUES('new','done',?)",
               (time.time(),))
    db.execute("INSERT INTO job(id, status, queued_at) VALUES('busy','running',?)",
               (old,))

    retention.sweep_service_tables(db, audit_days=365)

    left = {r["id"] for r in db.query("SELECT id FROM job")}
    assert "old" not in left
    assert {"new", "busy"} <= left, "работающую задачу трогать нельзя"


def test_sweeping_service_tables_never_raises(tmp_path, monkeypatch):
    """Уборка мелочи не может отменить уборку прогонов, ради которой всё и вызвано."""
    db = Database(tmp_path / "vistest.db")

    def boom(*_a, **_k):
        raise RuntimeError("no")

    monkeypatch.setattr(db, "execute", boom)
    result = retention.sweep_service_tables(db)
    assert "error" in result
