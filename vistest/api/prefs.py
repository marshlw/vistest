# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Настройки инсталляции, которые правятся из интерфейса.

Та же таблица `setting`, что и у порогов, но значения здесь не числа: флаги и
строки. Отдельный модуль, потому что `thresholds.py` про пороги вердикта и
знает про них диапазоны и единицы — складывать туда «включён ли эталон на
ветку» значило бы делать вид, что это тоже порог.

Почему БД, а не `vistest.yaml`: конфиг лежит в git и описывает проект, а эти
значения принадлежат инсталляции. Сервис, переписывающий чужой версионируемый
файл, — источник конфликтов на ровном месте.
"""

from __future__ import annotations

TRUE = ("1", "true", "yes", "on")


def _read(db, name: str) -> str | None:
    try:
        row = db.one(
            "SELECT value FROM setting WHERE scope='global' AND project_key=''"
            "   AND name=?", (name,))
    except Exception:
        # Отсутствие таблицы не должно ронять прогон: значение просто останется
        # значением по умолчанию.
        return None
    return (row or {}).get("value")


def _write(db, name: str, value: str, who: str = "") -> None:
    db.execute(
        "INSERT INTO setting(scope, project_key, name, value, updated_at,"
        " updated_by) VALUES('global','',?,?,datetime('now'),?)"
        " ON CONFLICT(scope, project_key, name) DO UPDATE SET"
        " value=excluded.value, updated_at=excluded.updated_at,"
        " updated_by=excluded.updated_by",
        (name, value, who))


def get_flag(db, name: str, default: bool = False) -> bool:
    raw = _read(db, name)
    if raw is None:
        return default
    return str(raw).strip().lower() in TRUE


def set_flag(db, name: str, value: bool, who: str = "") -> None:
    _write(db, name, "1" if value else "0", who)


def get_text(db, name: str, default: str = "") -> str:
    raw = _read(db, name)
    return default if raw is None else str(raw)


def set_text(db, name: str, value: str, who: str = "") -> None:
    _write(db, name, str(value), who)


# --------------------------------------------------------------------------- #
#  Эталон на ветку
# --------------------------------------------------------------------------- #
BRANCH_BASELINES = "branch_baselines"
DEFAULT_BRANCH = "default_branch"


def branch_baselines_on(db) -> bool:
    """Выключено по умолчанию — и это не осторожность, а честность.

    Команде с одной веткой режим не даёт ничего, а вопросов добавляет: «почему
    мой апрув не изменил эталон, который я вижу». Включается осознанно.
    """
    return get_flag(db, BRANCH_BASELINES, False)


def default_branch(db) -> str:
    """Ветка, чей набор считается основным. Всё остальное — наложения."""
    return (get_text(db, DEFAULT_BRANCH, "main") or "main").strip()


def is_base_branch(db, branch: str | None) -> bool:
    """Прогон без ветки тоже основной.

    Ветку знает не всякий прогон: снятие эталона по URL из интерфейса делается
    вне репозитория вовсе. Считать такой прогон «веткой с пустым именем» и
    заводить ему отдельное наложение — верный способ развести два набора там,
    где человек видел один.
    """
    text = (branch or "").strip()
    return not text or text == default_branch(db)
