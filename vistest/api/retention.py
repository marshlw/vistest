# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Ретеншен: единственное фоновое действие, которое **удаляет данные**.

Дифф на полную страницу — это несколько мегабайт PNG. Без уборки `.vistest`
съедает десятки гигабайт за пару месяцев, список из тысяч прогонов начинает
тормозить, а кончается всё «на диске нет места» посреди прогона. Кнопка
«почистить старое» была с самого начала, но нажимать её надо помнить — то есть
не нажимают.

Отсюда весь тон этого модуля. Автоматика, которая молча удаляет чужую работу,
должна быть параноидально осторожной, и осторожность здесь выражена тремя
правилами:

1. **Выключено по умолчанию.** Инсталляция на ноутбуке одного инженера не имеет
   проблемы с диском, а сюрприз «куда делась история» имеет.

2. **Неразобранное падение не удаляется никогда.** Это главное правило и
   единственное, которое нельзя отключить переключателем. Удалить прогон с
   непросмотренным падением значит уничтожить вопрос, на который никто не
   ответил: человек не увидит его ни в интерфейсе, ни в дайджесте — он просто
   исчезнет вместе с картинками. Разобранное падение — другое дело: решение
   принято, и его текст остаётся в истории снимка.

3. **Последние N прогонов остаются всегда**, даже если они старше срока. Когда
   сегодня всё сломалось, точка отсчёта нужна именно старая.

Квота по размеру существует отдельно от срока и работает жёстче: срок отвечает
на вопрос «что уже не нужно», квота — на вопрос «что делать, когда место
кончается». Поэтому при переполнении удаляются самые старые сверх `keep_last`
независимо от их возраста — но и здесь правило 2 сильнее квоты. Диск можно
докупить, а разбор, которого никто не видел, восстановить нельзя.
"""

from __future__ import annotations

import os
import threading
import time

from .prefs import get_flag, get_text, set_flag, set_text

ENABLED = "retention_enabled"
DAYS = "retention_days"
KEEP_LAST = "retention_keep_last"
MAX_GB = "retention_max_gb"
LAST_RUN = "retention_last_run"
LAST_RESULT = "retention_last_result"

DEFAULT_DAYS = 60.0
DEFAULT_KEEP_LAST = 50
# Ноль — «без квоты». Квота на диск это ответ на «место кончается», и придумывать
# за человека, сколько ему не жалко, мы не будем.
DEFAULT_MAX_GB = 0.0


def _num(db, name: str, default: float) -> float:
    try:
        raw = get_text(db, name, "")
        return float(raw) if raw != "" else default
    except (TypeError, ValueError):
        return default


def settings(db) -> dict:
    return {
        "enabled": get_flag(db, ENABLED, False),
        "days": _num(db, DAYS, DEFAULT_DAYS),
        "keep_last": int(_num(db, KEEP_LAST, DEFAULT_KEEP_LAST)),
        "max_gb": _num(db, MAX_GB, DEFAULT_MAX_GB),
        "last_run": get_text(db, LAST_RUN, ""),
        "last_result": get_text(db, LAST_RESULT, ""),
    }


def configure(db, payload: dict, who: str = "") -> dict:
    """Сохранить политику. Отсутствующий ключ не трогается."""
    if payload.get("enabled") is not None:
        set_flag(db, ENABLED, bool(payload["enabled"]), who)
    if payload.get("days") is not None:
        days = float(payload["days"])
        if days < 1:
            raise ValueError("days must be at least 1")
        set_text(db, DAYS, str(days), who)
    if payload.get("keep_last") is not None:
        keep = int(payload["keep_last"])
        if keep < 1:
            # Ноль означал бы «можно снести всю историю разом», и это не
            # настройка, а способ выстрелить себе в ногу одним полем формы.
            raise ValueError("keep_last must be at least 1")
        set_text(db, KEEP_LAST, str(keep), who)
    if payload.get("max_gb") is not None:
        gb = float(payload["max_gb"])
        if gb < 0:
            raise ValueError("max_gb cannot be negative")
        set_text(db, MAX_GB, str(gb), who)
    return settings(db)


# --------------------------------------------------------------------------- #
#  Кого можно трогать
# --------------------------------------------------------------------------- #
def _unreviewed_ids(db) -> set[int]:
    """Прогоны, где осталось хоть одно неразобранное падение.

    Их не удаляет ни срок, ни квота, ни кнопка «почистить». Это не осторожность
    ради осторожности: непросмотренное падение — вопрос, на который никто не
    ответил, и удалить его значит сделать так, что уже и не ответит.
    """
    return {r["run_id"] for r in db.query(
        "SELECT DISTINCT run_id FROM comparison"
        " WHERE verdict='fail' AND review IS NULL")}


def candidates(db, *, days: float | None, keep_last: int | None,
               only_passed: bool = False, project: str | None = None,
               keep_unreviewed: bool = True) -> list[dict]:
    """Прогоны, которые политика разрешает удалить, самые старые последними."""
    sql = ("SELECT r.id, r.run_key, r.started_at, r.failed, p.name AS project"
           "  FROM run r JOIN project p ON p.id = r.project_id WHERE 1=1")
    params: tuple = ()
    if project and project != "*":
        sql += " AND p.name = ?"
        params += (project,)
    if days is not None:
        sql += " AND r.started_at < datetime('now', ?)"
        params += (f"-{float(days)} days",)
    if only_passed:
        sql += " AND COALESCE(r.failed, 0) = 0"
    sql += " ORDER BY r.started_at DESC"
    rows = [dict(r) for r in db.query(sql, params)]

    if keep_last is not None:
        # Последние N — по каждому проекту отдельно. Общий список означал бы,
        # что активный проект вытесняет из-под защиты весь редкий: у того
        # прогонов мало, они старые, и «последние пятьдесят» до них не доходят.
        protected: set[int] = set()
        names = ([project] if project and project != "*"
                 else [r["name"] for r in db.query("SELECT name FROM project")])
        for name in names:
            protected |= {r["id"] for r in db.query(
                "SELECT r.id FROM run r JOIN project p ON p.id = r.project_id"
                " WHERE p.name = ? ORDER BY r.started_at DESC LIMIT ?",
                (name, int(keep_last)))}
        rows = [r for r in rows if r["id"] not in protected]

    if keep_unreviewed:
        waiting = _unreviewed_ids(db)
        rows = [r for r in rows if r["id"] not in waiting]
    return rows


def over_quota(db, *, max_gb: float, keep_last: int, used_kb: int,
               keep_unreviewed: bool = True) -> list[dict]:
    """Самые старые прогоны, которые придётся снести ради места.

    Возраст здесь не спрашивается: квота отвечает не на «что уже не нужно», а
    на «место кончается». Правило про неразобранное падение сильнее и тут —
    диск можно докупить, а разбор, которого никто не видел, нельзя.
    """
    if max_gb <= 0:
        return []
    limit_kb = max_gb * 1024 * 1024
    if used_kb <= limit_kb:
        return []
    return list(reversed(candidates(
        db, days=None, keep_last=keep_last, project="*",
        keep_unreviewed=keep_unreviewed)))


# --------------------------------------------------------------------------- #
#  Уборка
# --------------------------------------------------------------------------- #
def sweep(db, drop_files, *, days: float | None, keep_last: int | None,
          only_passed: bool = False, project: str | None = None,
          max_gb: float = 0.0, used_kb: int = 0,
          keep_unreviewed: bool = True, dry_run: bool = False) -> dict:
    """Применить политику. `drop_files(run_id, run_key) -> освобождено байт`."""
    waiting = len(_unreviewed_ids(db)) if keep_unreviewed else 0
    freed = 0
    deleted: list[dict] = []

    def drop(row: dict) -> None:
        nonlocal freed
        if not dry_run:
            freed += drop_files(row["id"], row["run_key"])
            db.execute("DELETE FROM run WHERE id=?", (row["id"],))
        deleted.append(row)

    # Сначала срок: он говорит «это уже не нужно», и тут удаляется всё, что под
    # него попало.
    by_age = list(reversed(candidates(          # самые старые первыми
        db, days=days, keep_last=keep_last, only_passed=only_passed,
        project=project, keep_unreviewed=keep_unreviewed)))
    for row in by_age:
        drop(row)

    # Потом квота, и она устроена иначе. Срок отвечает на «что уже не нужно»,
    # квота — на «место кончается», поэтому удаляется ровно столько, сколько
    # нужно, чтобы влезть, и ни одним прогоном больше. «Раз уж чистим — почистим
    # с запасом» — это чужие данные, удалённые без причины.
    # Решает, надо ли вообще что-то сносить ради места, ОДНА функция —
    # `over_quota`. Второе такое же условие здесь читалось бы как страховка, а
    # на деле было бы вторым мнением о том же вопросе: в день, когда они
    # разойдутся, разберётся с этим никто.
    left = max(0, used_kb - round(freed / 1024))
    seen = {r["id"] for r in deleted}
    limit_kb = max_gb * 1024 * 1024
    for row in over_quota(db, max_gb=max_gb, keep_last=keep_last or 0,
                          used_kb=left, keep_unreviewed=keep_unreviewed):
        if row["id"] in seen:
            continue
        before = freed
        drop(row)
        left -= round((freed - before) / 1024)
        if left <= limit_kb:
            break

    return {"deleted": len(deleted), "freed_kb": round(freed / 1024),
            "run_ids": [r["id"] for r in deleted][:200],
            "kept_unreviewed": waiting,
            "dry_run": dry_run}


def run_once(db, drop_files, *, used_kb: int = 0, force: bool = False) -> dict:
    """Плановая уборка — то, что вызывают часы."""
    if not force and not get_flag(db, ENABLED, False):
        return {"skipped": "retention is off", "deleted": 0}

    conf = settings(db)
    result = sweep(db, drop_files, days=conf["days"],
                   keep_last=conf["keep_last"], project="*",
                   max_gb=conf["max_gb"], used_kb=used_kb)
    set_text(db, LAST_RUN, time.strftime("%Y-%m-%dT%H:%M:%S"), "retention")
    set_text(db, LAST_RESULT,
             f"{result['deleted']} runs, {result['freed_kb']} KB freed",
             "retention")
    return result


# --------------------------------------------------------------------------- #
#  Часы
#
#  Раз в сутки достаточно: политика измеряется в днях, и чаще просыпаться значит
#  просыпаться зря. Один поток на процесс, по тем же соображениям, что и у
#  уведомлений, — модуль сервиса в тестах перезагружается десятками раз.
# --------------------------------------------------------------------------- #
TICK_S = float(os.getenv("VISTEST_RETENTION_TICK_S", "86400"))

_ticker: threading.Thread | None = None
_ticker_lock = threading.Lock()
_target: dict = {"db": None, "drop_files": None, "used_kb": None}


def ensure_ticker(db, drop_files, used_kb) -> threading.Thread | None:
    """`used_kb` — вызываемое: размер считается на момент уборки, а не сейчас."""
    global _ticker
    _target.update(db=db, drop_files=drop_files, used_kb=used_kb)
    if TICK_S <= 0:
        return None
    with _ticker_lock:
        if _ticker is not None and _ticker.is_alive():
            return _ticker

        def loop() -> None:
            while True:
                time.sleep(TICK_S)
                target, drop = _target["db"], _target["drop_files"]
                if target is None or drop is None:
                    continue
                try:
                    size = _target["used_kb"]
                    run_once(target, drop, used_kb=size() if callable(size) else 0)
                except Exception:
                    # Уборка не может стоить сервиса.
                    pass

        _ticker = threading.Thread(target=loop, name="vistest-retention",
                                   daemon=True)
        _ticker.start()
        return _ticker
