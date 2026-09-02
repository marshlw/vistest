# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Транзакция обязана быть одна на всю операцию, а не на каждый её шаг.

Соединение здесь одно на поток, и `commit()` у sqlite про вложенность ничего не
знает: он завершает транзакцию целиком, кто бы его ни позвал. Поэтому
`with db.connect()` внутри другого `with db.connect()` фиксировал всё, что успел
записать внешний, — на первом же вложенном вызове.

Заметно это становится только тогда, когда что-то падает посередине. Для приёма
прогона это значит полупринятый прогон: он выглядит настоящим и уходит в
метрики, в очередь решений и в сравнение прогонов, а половины сравнений в нём
нет. Ошибка при этом не молчит — молчат её последствия.
"""

from __future__ import annotations

import sqlite3

import pytest


def test_a_nested_block_does_not_commit_the_outer_one(db):
    """Внешний блок упал — не должно остаться НИЧЕГО, включая записанное вложенным."""
    with pytest.raises(RuntimeError):
        with db.connect() as c:
            c.execute("INSERT INTO project(name) VALUES('outer')")
            # Вложенный блок: раньше его выход коммитил обе строки.
            with db.connect() as inner:
                inner.execute("INSERT INTO project(name) VALUES('inner')")
            raise RuntimeError("что-то пошло не так на середине")

    names = {r["name"] for r in db.query("SELECT name FROM project")}
    assert not names & {"outer", "inner"}, f"пережило откат: {names}"


def test_helpers_called_inside_a_block_join_its_transaction(db):
    """`project_id()` и `snapshot_id()` зовутся из середины `ingest_run`.

    Каждый из них открывает «свой» `with connect()`. Если он фиксирует
    транзакцию, то приём прогона перестаёт быть атомарным ровно там, где это
    важнее всего: сам прогон уже записан, а сравнения к нему — ещё нет.
    """
    with pytest.raises(RuntimeError):
        with db.connect() as c:
            c.execute("INSERT INTO project(name) VALUES('half')")
            pid = db.project_id("half-nested")     # свой connect() внутри
            db.snapshot_id(pid, "a.png", "linux", "chromium")
            raise RuntimeError("падение после вложенных помощников")

    assert not db.query("SELECT id FROM project WHERE name LIKE 'half%'")
    assert not db.query("SELECT id FROM snapshot")


def test_a_successful_block_still_commits(db):
    """Проверка «не сломали ли обратное»: без ошибки всё обязано сохраниться."""
    with db.connect() as c:
        c.execute("INSERT INTO project(name) VALUES('kept')")
        with db.connect() as inner:
            inner.execute("INSERT INTO project(name) VALUES('kept-inner')")
    names = {r["name"] for r in db.query("SELECT name FROM project")}
    assert {"kept", "kept-inner"} <= names


def test_an_ingested_run_is_all_or_nothing(db):
    """Прогон с битым сравнением не должен оседать в базе половиной.

    `ingest_run` пишет прогон, потом сравнения, потом регионы. Сравнение без
    обязательного `name` роняет вставку — и до этой правки в базе оставался
    прогон без единого сравнения: в списке он выглядит зелёным и пустым, а на
    деле не проверено ничего.
    """
    payload = {
        "run_id": "run-broken",
        "platform": "linux", "browser": "chromium",
        "totals": {"total": 2, "passed": 1, "failed": 1},
        "comparisons": [
            {"name": "ok.png", "verdict": "pass", "metrics": {}},
            {"verdict": "fail", "metrics": {}},          # без `name` — упадёт
        ],
    }
    # KeyError на отсутствующем `name` — sqlite тут ни при чём, но ловим оба:
    # смысл теста в том, ЧТО осталось в базе, а не чем именно упало.
    with pytest.raises((KeyError, sqlite3.Error)):
        db.ingest_run(payload, "demo")

    assert not db.query("SELECT id FROM run WHERE run_key='run-broken'"), \
        "прогон осел в базе половиной"
    assert not db.query("SELECT id FROM comparison")
