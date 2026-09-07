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

#  Сколько живёт журнал. Отдельно от прогонов и заметно дольше: прогон
#  отвечает на «что было со снимком», журнал — на «кто изменил эталон», и
#  второй вопрос задают через полгода после первого. Год — это «прошлый
#  квартал ещё виден», и это же предел, после которого журнал начинает
#  заметно стоить: троттлинг входа читает его на КАЖДУЮ попытку.
DEFAULT_AUDIT_DAYS = 365.0
AUDIT_DAYS = "retention_audit_days"

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
        "audit_days": _num(db, AUDIT_DAYS, DEFAULT_AUDIT_DAYS),
        "last_run": get_text(db, LAST_RUN, ""),
        "last_result": get_text(db, LAST_RESULT, ""),
    }


def sweep_service_tables(db, audit_days: float = DEFAULT_AUDIT_DAYS) -> dict:
    """Строки, которые никто никогда не удалял.

    Каждая из них по отдельности мелочь, и именно поэтому их не замечали:
    уборка ходила по прогонам и артефактам — то есть по гигабайтам, — а рядом
    молча росли таблицы, которые читаются на горячих путях.

    * `audit` — не чистился никогда. При этом счётчик неудачных входов делает
      по нему два запроса на КАЖДУЮ попытку входа, включая неудачные. Индексы
      есть, но объём не ограничен ничем, а таблица растёт ровно от того, кто
      стучится.
    * `session` — протухшие сессии удалялись только при чьём-то входе. На
      инсталляции, где неделю никто не заходил, они просто лежат.
    * `review_claim` — «я разбираю этот снимок» живёт две минуты, а строка
      оставалась навсегда; индекс по `expires_at` был, DELETE — нет.
    * `job` — в памяти держится полсотни задач, в базе не удалялась ни одна.
      Задачи хранят хвост лога, то есть это не десяток байт на строку.
      Правило одно — возраст, и активные задачи неприкосновенны. Здесь
      сначала стояло ещё и «оставить последние двести», и тест показал, чего
      оно стоит: на инсталляции с тремя задачами под это правило попадали
      все, и не удалялось ничего, включая годовалые. Два правила, спорящие
      друг с другом, всегда выигрывает не то, которое имели в виду.

    Прогоны и эталоны здесь не трогаются вовсе: это делает `sweep`, и мешать
    две ответственности в одной функции — верный способ однажды удалить не то.
    """
    out = {}
    try:
        out["audit"] = db.execute(
            "DELETE FROM audit WHERE at < datetime('now', ?)",
            (f"-{float(audit_days)} days",))
        out["sessions"] = db.execute(
            "DELETE FROM session WHERE expires_at < datetime('now')")
        out["claims"] = db.execute(
            "DELETE FROM review_claim WHERE expires_at < datetime('now')")
        out["presence"] = db.execute(
            "DELETE FROM presence WHERE last_seen < datetime('now', '-7 days')")
        out["jobs"] = db.execute(
            "DELETE FROM job WHERE status NOT IN ('queued','running')"
            "   AND COALESCE(finished_at, queued_at, 0) < ?",
            (time.time() - float(audit_days) * 86400,))
    except Exception as e:                                   # pragma: no cover
        # Уборка служебных таблиц не может стоить сервиса и не может отменить
        # уборку прогонов, ради которой всё и вызвано.
        out["error"] = f"{type(e).__name__}: {e}"
    return out


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
    if payload.get("audit_days") is not None:
        audit_days = float(payload["audit_days"])
        # Нижняя граница — месяц, и она не про удобство. Журнал отвечает на
        # «кто переписал эталон», а спрашивают это не в тот же день; срок в
        # неделю превратил бы ответ в «данных нет» ровно тогда, когда вопрос
        # наконец задали.
        if audit_days < 30:
            raise ValueError("audit_days must be at least 30")
        set_text(db, AUDIT_DAYS, str(audit_days), who)
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


def sweep_checks(directory, days: float | None) -> dict:
    """Убрать артефакты одиночных проверок из `POST /api/check`.

    Единственный каталог, до которого уборка не доставала вовсе. Она ходит по
    прогонам в базе и удаляет `artifacts/<id>`; проверка из чужого набора
    прогона не создаёт — её картинки ложатся в `artifacts/checks/<run_key>`, и
    в базе про них не написано ничего. То есть на активном CI этот каталог рос
    бесконечно, и заметить это можно было только по кончившемуся диску.

    Возраст берётся по времени изменения каталога, а не по имени: имя прогона
    задаёт вызывающий, и парсить из него дату значило бы верить чужой строке.
    """
    from pathlib import Path as _Path

    directory = _Path(directory)
    out = {"deleted": 0, "freed_kb": 0}
    if days is None or days <= 0 or not directory.exists():
        return out

    import shutil
    import time as _time

    cutoff = _time.time() - days * 86400
    for child in sorted(directory.iterdir()):
        if not child.is_dir():
            continue
        try:
            if child.stat().st_mtime >= cutoff:
                continue
            size = sum(f.stat().st_size for f in child.rglob("*") if f.is_file())
            shutil.rmtree(child, ignore_errors=True)
        except OSError:
            continue
        out["deleted"] += 1
        out["freed_kb"] += round(size / 1024)
    return out


#  Право на уборку — одно на весь том, а не на процесс.
#
#  Часы заводятся при импорте `api.main`, то есть в КАЖДОМ воркере и в каждой
#  реплике за общим томом. Уведомления от этого защищены обменом отметки
#  (`api/notify.py::_claim`), а уборка — нет, и три процесса удаляли прогоны
#  одновременно: считали размер тома до чужого удаления, а удаляли после.
#  Лишнего они снесут ровно столько, сколько успеют, и восстановить это нечем.
#
#  Тот же приём: отметку последнего запуска меняет один UPDATE, и работает
#  только тот, чей UPDATE изменил строку.
CLAIM = "retention_claim"

#  Сигнал остановки часов. Один на модуль — часы тоже одни.
_stop = threading.Event()


def stop_ticker() -> None:
    """Остановить часы (вызывается при завершении сервиса)."""
    _stop.set()


def _claim_sweep(db, window_s: int) -> bool:
    """Забрать право на уборку. False — её уже делает кто-то другой."""
    stamp = str(time.time())
    try:
        db.execute(
            "INSERT OR IGNORE INTO setting(scope, project_key, name, value,"
            " updated_at, updated_by) VALUES('global','',?,?,datetime('now'),"
            " 'retention')", (CLAIM, "0"))
        return bool(db.execute(
            "UPDATE setting SET value=?, updated_at=datetime('now'),"
            " updated_by='retention'"
            " WHERE scope='global' AND project_key='' AND name=?"
            "   AND CAST(value AS REAL) < ?",
            (stamp, CLAIM, time.time() - window_s)))
    except Exception:
        # Сломанный счётчик не должен отменять уборку в однопроцессной
        # установке — там конкурентов нет по определению.
        return True


def run_once(db, drop_files, *, used_kb: int = 0, force: bool = False,
             checks_dir=None) -> dict:
    """Плановая уборка — то, что вызывают часы."""
    if not force and not get_flag(db, ENABLED, False):
        return {"skipped": "retention is off", "deleted": 0}
    # `force` — это нажатие кнопки человеком: он ждёт ответа, и отвечать ему
    # «сейчас убирается кто-то другой» незачем.
    if not force and not _claim_sweep(db, max(60, int(TICK_S * 0.9))):
        return {"skipped": "another process is sweeping", "deleted": 0}

    conf = settings(db)
    result = sweep(db, drop_files, days=conf["days"],
                   keep_last=conf["keep_last"], project="*",
                   max_gb=conf["max_gb"], used_kb=used_kb)
    if checks_dir is not None:
        checks = sweep_checks(checks_dir, conf["days"])
        result["checks_deleted"] = checks["deleted"]
        result["freed_kb"] = result.get("freed_kb", 0) + checks["freed_kb"]
    result["service_rows"] = sweep_service_tables(db, conf["audit_days"])
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
_target: dict = {"db": None, "drop_files": None, "used_kb": None,
           "checks_dir": None}


def ensure_ticker(db, drop_files, used_kb,
                  checks_dir=None) -> threading.Thread | None:
    """`used_kb` — вызываемое: размер считается на момент уборки, а не сейчас."""
    global _ticker
    _target.update(db=db, drop_files=drop_files, used_kb=used_kb,
                   checks_dir=checks_dir)
    if TICK_S <= 0:
        return None
    with _ticker_lock:
        if _ticker is not None and _ticker.is_alive():
            return _ticker

        def loop() -> None:
            # `wait`, а не `sleep`: при остановке сервиса поток должен уходить
            # сразу, а не досыпать сутки. Он daemon, то есть процесс его не
            # ждёт, — но в тестах и при перезапуске в одном процессе
            # досыпающие часы делают своё дело уже после того, как их отменили.
            while not _stop.wait(TICK_S):
                target, drop = _target["db"], _target["drop_files"]
                if target is None or drop is None:
                    continue
                try:
                    size = _target["used_kb"]
                    run_once(target, drop,
                             used_kb=size() if callable(size) else 0,
                             checks_dir=_target["checks_dir"])
                except Exception:
                    # Уборка не может стоить сервиса.
                    pass

        _ticker = threading.Thread(target=loop, name="vistest-retention",
                                   daemon=True)
        _ticker.start()
        return _ticker
