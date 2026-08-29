# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Права по проектам: роль, которая действует не везде.

Роль была одна на инсталляцию. `reviewer` означало «может утверждать эталоны»
— все эталоны, во всех наборах. Пока проект был один, это и было верно. Как
только к сервису подключили второй набор тестов, утверждение стало действием
над чужим кодом: человек, отвечающий за витрину, одним нажатием переписывал
эталон биллинга — и делал это не со зла, а потому что кнопка была активной, а
снимок красным.

Причём заметить это нельзя: утверждение необратимо (оно перезаписывает то, с
чем сравнивают дальше), а в интерфейсе оно выглядит одинаково для своего и для
чужого проекта.

Модель здесь намеренно самая скромная из возможных.

**Глобальная роль — то, что человек может везде.** Она не меняет смысла и
работает ровно как раньше.

**Право по проекту — то, что человек может дополнительно в одном проекте.**
Действующая роль = максимум из двух. Права не *отнимают*: отнимающее право
означало бы, что администратор может перестать быть администратором, а
инсталляция — остаться без того, кто это чинит.

Отсюда сценарий изоляции, и он единственный: глобально все `viewer`, а
`reviewer` выдаётся на конкретные проекты. Это осознанное действие
администратора, а не то, что случится при обновлении: пока прав не выдано,
поведение совпадает с прежним до мелочей. Молча понизить всех при обновлении
означало бы прийти утром в команду, где никто не может утвердить ничего.

Чего здесь нет: групп, наследования, списков доступа. Три роли и три десятка
проектов — это таблица на три колонки, а не система прав; всё остальное
добавляется тогда, когда об этом попросят, а не заранее.
"""

from __future__ import annotations

from fastapi import HTTPException

ROLES = ("viewer", "reviewer", "admin")
RANK = {"viewer": 0, "reviewer": 1, "admin": 2}


def normalize(role: str) -> str:
    role = (role or "").strip().lower()
    if role not in ROLES:
        raise HTTPException(400, f"role must be one of: {', '.join(ROLES)}")
    return role


def project_key(value: str | None) -> str:
    """Ключ проекта в правах — строка, а не NULL.

    Пустая строка означает «инсталляция целиком» и в правах не используется:
    NULL в составном ключе ведёт себя в sqlite так, что две одинаковые записи
    перестают быть одинаковыми, и `INSERT OR REPLACE` тихо плодит дубли.
    """
    return (value or "").strip()


# --------------------------------------------------------------------------- #
#  Хранилище
# --------------------------------------------------------------------------- #
def grant(db, login: str, key: str, role: str, by: str = "") -> dict:
    """Выдать право на проект. Повторная выдача заменяет предыдущее."""
    key, role = project_key(key), normalize(role)
    if not key:
        raise HTTPException(400, "a project is required")
    if not (login or "").strip():
        raise HTTPException(400, "a login is required")
    db.execute(
        "INSERT OR REPLACE INTO project_grant(login, project_key, role,"
        " granted_by, granted_at) VALUES(?,?,?,?,datetime('now'))",
        (login, key, role, by))
    return {"login": login, "project_key": key, "role": role}


def revoke(db, login: str, key: str) -> bool:
    return db.execute(
        "DELETE FROM project_grant WHERE login=? AND project_key=?",
        (login, project_key(key))) > 0


def grants_of(db, login: str) -> dict[str, str]:
    return {r["project_key"]: r["role"] for r in db.query(
        "SELECT project_key, role FROM project_grant WHERE login=?", (login,))}


def list_grants(db, *, key: str | None = None,
                login: str | None = None) -> list[dict]:
    sql = ("SELECT login, project_key, role, granted_by, granted_at"
           "  FROM project_grant WHERE 1=1")
    params: tuple = ()
    if key is not None:
        sql += " AND project_key=?"
        params += (project_key(key),)
    if login is not None:
        sql += " AND login=?"
        params += (login,)
    return db.query(sql + " ORDER BY project_key, login", params)


# --------------------------------------------------------------------------- #
#  Проверка
# --------------------------------------------------------------------------- #
def effective(db, user: dict, key: str | None) -> str:
    """Роль этого человека в этом проекте — максимум из глобальной и выданной.

    Отдельной ветки для одиночного режима здесь нет и не нужно: пока
    пользователей не завели, `current_user` возвращает `admin`, а выше
    администратора выданное право не поднимает. Написать такую ветку значило
    бы завести второе место, где решается, кого пускать, — и рано или поздно
    эти два места разошлись бы.
    """
    base = (user or {}).get("role") or "viewer"
    key = project_key(key)
    if not key:
        return base
    extra = db.one(
        "SELECT role FROM project_grant WHERE login=? AND project_key=?",
        ((user or {}).get("login") or "", key))
    if not extra:
        return base
    return base if RANK.get(base, 0) >= RANK.get(extra["role"], 0) else extra["role"]


# --------------------------------------------------------------------------- #
#  Чей это объект
#
#  Право выдаётся на проект, а действия совершаются над прогоном, сравнением и
#  эталоном. Правило перевода одно и живёт здесь целиком: разложить его по
#  местам вызова значило бы завести несколько слегка разных ответов на вопрос
#  «чей это снимок» — а расходятся они всегда в сторону «ничей», то есть в
#  сторону разрешить.
# --------------------------------------------------------------------------- #
def of_run(db, run_id: int) -> str:
    """Проект прогона: ключ подключённого набора либо имя проекта в базе."""
    row = db.one(
        "SELECT r.project_key, p.name FROM run r"
        " JOIN project p ON p.id=r.project_id WHERE r.id=?", (run_id,))
    if not row:
        return ""
    return project_key(row.get("project_key")) or project_key(row.get("name"))


def of_comparison(db, comp_id: int) -> str:
    row = db.one("SELECT run_id FROM comparison WHERE id=?", (comp_id,))
    return of_run(db, row["run_id"]) if row else ""


def of_snapshot(db, snapshot_id: int) -> str:
    """Проект снимка — имя проекта, под которым он заведён.

    Ключа подключённого набора у снимка нет, и второго источника здесь быть не
    может. Расхождения это не создаёт: прогоны подключённых наборов пишутся в
    базу под именем, равным ключу (`publish_external_run(project=project.key)`),
    поэтому оба ответа совпадают.
    """
    row = db.one(
        "SELECT p.name FROM snapshot s JOIN project p ON p.id=s.project_id"
        " WHERE s.id=?", (snapshot_id,))
    return project_key(row.get("name")) if row else ""


def of_scope(scope: str, key: str | None, own: str = "") -> str:
    """Проект набора эталонов.

    `global` — собственный набор сервиса; он тоже чей-то, и его проект — тот,
    под именем которого сервис ведёт свою историю. Иначе «свой набор» оказался
    бы единственным местом, где право не спрашивают вовсе.
    """
    return project_key(key) if project_key(key) else project_key(own)


def known(db) -> list[str]:
    """Проекты, на которые вообще имеет смысл выдавать право.

    Три источника, и все три нужны: подключённые наборы (право выдают до
    первого прогона), проекты с историей (набор могли отключить, а разбирать
    его прогоны — нет) и собственный проект сервиса (в большинстве инсталляций
    основной набор лежит именно там). Список без одного из них выглядит как
    поломка: администратор ищет проект глазами и не находит.
    """
    out: list[str] = []

    def add(value):
        value = project_key(value)
        if value and value not in out:
            out.append(value)

    try:
        from ..config import VisTestConfig
        from ..projects import ProjectRegistry

        cfg = VisTestConfig.load()
        add(cfg.service.project)
        for project in ProjectRegistry(cfg).list():
            add(project.key)
    except Exception:
        # Реестр проектов и конфиг читаются с диска: их отсутствие не повод
        # уронить страницу команды — история в базе всё равно есть.
        pass

    for row in db.query("SELECT name FROM project ORDER BY name"):
        add(row.get("name"))
    for row in db.query("SELECT DISTINCT project_key FROM run"
                        " WHERE COALESCE(project_key,'') <> ''"):
        add(row.get("project_key"))
    return out


def allows(db, user: dict, role: str, key: str | None) -> bool:
    return RANK.get(effective(db, user, key), -1) >= RANK[normalize(role)]


def check(db, user: dict, role: str, key: str | None) -> dict:
    """Проверить и объяснить отказ так, чтобы было понятно, что делать.

    «Not enough rights» без указания проекта — самое бесполезное сообщение из
    возможных: человек видит его на кнопке, которая для соседнего проекта
    работает, и решает, что сломался сервис.
    """
    if allows(db, user, role, key):
        return user
    have = effective(db, user, key)
    key = project_key(key)
    where = f" in project «{key}»" if key else ""
    hint = (" A right on this project alone is granted by an administrator "
            "on the «Team» tab.") if key else ""
    raise HTTPException(
        403,
        f"Not enough rights{where}: role {normalize(role)} is required, "
        f"you have {have}.{hint}")
