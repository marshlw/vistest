# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Токены CI: доступ для пайплайна, а не для человека.

Токен приёма прогонов был один на всю инсталляцию — `VISTEST_INGEST_TOKEN`.
Пока писал один пайплайн, этого хватало; дальше арифметика перестаёт сходиться.
Подрядчику надо выдать доступ, а есть только общий секрет. Человек уходит из
команды — менять надо во ВСЕХ пайплайнах разом и одномоментно, иначе половина
сборок краснеет. Узнать, кто именно писал прогоны, нельзя вовсе: в журнале
стоит «ci-token» — то есть все.

Здесь у токена есть имя, проект, роль, срок и отметка последнего использования.
Отзыв одного не трогает остальные. Перекат пайплайнов делается двумя
действующими токенами сразу — и это не отдельная функция, а следствие того, что
токенов у проекта может быть несколько; «когда старый можно гасить» отвечает
`last_used_at`.

--------------------------------------------------------------------------
Три решения, которые стоит объяснить

**Хранится хеш, а не токен.** База лежит рядом с эталонами и попадает в
бэкапы; секрет в открытом виде пережил бы там любую ротацию.

**Хеш — SHA-256, а не PBKDF2, которым хешируются пароли.** PBKDF2 в 480 000
раундов существует потому, что пароль придумал человек: его перебирают, и
каждая попытка должна быть дорогой. Токен — 24 случайных байта; перебирать его
нечем, а предъявляется он на КАЖДЫЙ запрос приёма. Полмиллиона раундов на
запрос — это отказ в обслуживании, устроенный самому себе.

**Префикс лежит отдельно и не секретен.** По нему идёт индексированный поиск
одной строки: иначе пришлось бы сравнивать хеш со всеми токенами базы на каждый
запрос. Он же опознаёт токен в списке, не будучи самим токеном, — «vt_a1b2c3d4…»
достаточно, чтобы понять, какой из четырёх пайплайнов это.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from .rights import RANK, normalize, project_key

# `vt_` в начале — чтобы токен, случайно попавший в лог или в issue, опознавался
# как секрет VisTest с первого взгляда, а не выглядел случайной строкой.
PREFIX = "vt_"
PREFIX_LEN = len(PREFIX) + 8
SECRET_BYTES = 24


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(raw) -> datetime | None:
    """Время из базы → осознанный UTC. `None` — прочитать не удалось.

    Две формы записи в одной колонке, и это не небрежность, а как оно есть:
    мы пишем `isoformat()` с зоной (`…+00:00`), а сам sqlite в `datetime('now')`
    — наивную строку через пробел. Сравнить их напрямую нельзя: Python на
    вычитании наивного из осознанного бросает TypeError.

    Стоило это дорого бы: `_expired` ловил этот TypeError и отвечал «просрочен».
    То есть токен, чей срок проставили запросом к базе — миграцией, скриптом,
    руками в консоли, — переставал работать молча и без единого следа.

    Наивное время здесь всегда UTC: другого `datetime('now')` в sqlite не бывает.
    """
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _expired(row: dict) -> bool:
    raw = (row or {}).get("expires_at")
    if not raw:
        return False
    when = _parse(raw)
    if when is None:
        # Нечитаемый срок — это испорченная строка, а не бессрочный токен.
        # Считать её «действует вечно» значило бы, что повреждение базы
        # РАСШИРЯЕТ доступ.
        return True
    return when < _now()


def status(row: dict) -> str:
    if row.get("revoked_at"):
        return "revoked"
    if _expired(row):
        return "expired"
    return "active"


# --------------------------------------------------------------------------- #
#  Выпуск
# --------------------------------------------------------------------------- #
def issue(db, *, name: str, project: str = "", role: str = "reviewer",
          days: int | None = None, created_by: str = "") -> dict:
    """Выпустить токен. Сам токен возвращается ЕДИНСТВЕННЫЙ раз — здесь.

    Дальше в базе только его хеш, и показать его повторно невозможно ни нам, ни
    администратору. Это не строгость ради строгости: секрет, который сервис
    умеет показать, — секрет, который утечёт вместе с доступом к сервису.
    """
    role = normalize(role)
    key = project_key(project)
    name = (str(name or "").strip() or (f"{key} CI" if key else "CI"))[:80]

    expires = None
    if days is not None:
        try:
            days = int(days)
        except (TypeError, ValueError):
            raise HTTPException(400, "days must be a number") from None
        if days <= 0:
            raise HTTPException(400, "days must be greater than zero")
        expires = (_now() + timedelta(days=days)).isoformat(timespec="seconds")

    # Коллизия префикса невозможна практически, но unique-индекс на него есть,
    # и молча падать на нём нельзя: секрет уже сгенерирован и никуда не попал.
    for _ in range(5):
        token = PREFIX + secrets.token_urlsafe(SECRET_BYTES)
        prefix = token[:PREFIX_LEN]
        if not db.one("SELECT id FROM ci_token WHERE prefix=?", (prefix,)):
            break
    else:                                                  # pragma: no cover
        raise HTTPException(500, "could not allocate a token prefix")

    db.execute(
        "INSERT INTO ci_token(name, prefix, token_hash, project_key, role,"
        " created_by, expires_at) VALUES(?,?,?,?,?,?,?)",
        (name, prefix, _hash(token), key, role, created_by, expires))
    row = db.one("SELECT * FROM ci_token WHERE prefix=?", (prefix,))
    return {**view(row), "token": token}


def rotate(db, token_id: int, *, created_by: str = "") -> dict:
    """Выпустить сменщика с теми же правами. Старый остаётся действующим.

    В этом и смысл: пока пайплайны перекатываются на новый секрет, старый обязан
    работать. Гасить его надо тогда, когда `last_used_at` перестанет двигаться,
    — и это видно в списке.
    """
    old = db.one("SELECT * FROM ci_token WHERE id=?", (token_id,))
    if not old:
        raise HTTPException(404, "no such token")
    days = None
    if old["expires_at"]:
        # У сменщика тот же СРОК ЖИЗНИ, что был у исходного, — считая от
        # сегодня. Унаследовать саму дату значило бы выдать токен, который
        # истекает завтра, потому что оригиналу остался день; а ротацию как раз
        # тогда и делают, и назавтра всё началось бы заново.
        created, expires = _parse(old["created_at"]), _parse(old["expires_at"])
        if created and expires:
            days = max(1, (expires - created).days)
    name = old["name"] or "CI"
    return issue(db, name=f"{name} (new)"[:80], project=old["project_key"],
                 role=old["role"], days=days, created_by=created_by)


def revoke(db, token_id: int) -> bool:
    return db.execute(
        "UPDATE ci_token SET revoked_at=datetime('now')"
        " WHERE id=? AND revoked_at IS NULL", (token_id,)) > 0


def view(row: dict) -> dict:
    """Токен для показа. Ни хеша, ни самого секрета — только то, что опознаёт."""
    return {
        "id": row["id"], "name": row["name"], "prefix": row["prefix"],
        "project": row["project_key"], "role": row["role"],
        "created_by": row["created_by"], "created_at": row["created_at"],
        "expires_at": row["expires_at"], "last_used_at": row["last_used_at"],
        "revoked_at": row["revoked_at"], "status": status(row),
    }


def listing(db, project: str = "") -> list[dict]:
    key = project_key(project)
    sql = "SELECT * FROM ci_token"
    params: tuple = ()
    if key:
        sql += " WHERE project_key=?"
        params = (key,)
    sql += " ORDER BY revoked_at IS NOT NULL, created_at DESC"
    return [view(r) for r in db.query(sql, params)]


# --------------------------------------------------------------------------- #
#  Проверка
# --------------------------------------------------------------------------- #
def resolve(db, supplied: str) -> dict | None:
    """Токен из заголовка → что он даёт. `None` — не наш токен.

    Отличать «не наш» от «наш, но просроченный» здесь нельзя и не нужно: оба
    ответа для вызывающего одинаковы, а разница между ними — это подсказка тому,
    кто подбирает.
    """
    supplied = (supplied or "").strip()
    if not supplied.startswith(PREFIX) or len(supplied) <= PREFIX_LEN:
        return None
    row = db.one("SELECT * FROM ci_token WHERE prefix=?",
                 (supplied[:PREFIX_LEN],))
    if not row:
        return None
    # Сравнение постоянного времени: обычное `==` на строках выходит раньше на
    # первом несовпавшем символе, и по времени ответа хеш подбирается посимвольно.
    if not hmac.compare_digest(_hash(supplied), row["token_hash"]):
        return None
    if status(row) != "active":
        return None

    # Отметка последнего использования — единственный ответ на вопрос «можно ли
    # уже гасить старый токен». Без неё ротация превращается в гадание, и
    # старый секрет живёт до конца времён на всякий случай.
    with db.connect():
        db.execute("UPDATE ci_token SET last_used_at=datetime('now') WHERE id=?",
                   (row["id"],))
        # Строка перечитывается: `row` прочитан ДО отметки, и вернуть его как
        # есть значило бы отдать вызывающему `last_used_at` предыдущего раза.
        # Поле маленькое, а неверное поле в ответе про доступ — плохая цена за
        # сэкономленный запрос.
        fresh = db.one("SELECT * FROM ci_token WHERE id=?", (row["id"],))
    return view(fresh or row)


def allows(token: dict, role: str, project: str = "") -> bool:
    """Хватает ли токену прав на это действие в этом проекте.

    Токен инсталляции (`project_key=''`) действует везде — это путь
    совместимости со старой переменной окружения. Токен проекта — только в
    своём: в этом и была вся задача.
    """
    if RANK.get(token.get("role", ""), -1) < RANK.get(normalize(role), 99):
        return False
    bound = project_key(token.get("project"))
    return not bound or bound == project_key(project)


def who(token: dict) -> str:
    """Как токен подписывается в журнале.

    Раньше здесь стояло «ci-token» — то есть все токены сразу, а по журналу
    нельзя было ответить, чей пайплайн залил прогон. Имя и префикс дают ответ,
    не показывая секрета.
    """
    name = (token.get("name") or "").strip()
    return f"ci:{name} ({token.get('prefix')})" if name \
        else f"ci:{token.get('prefix')}"
