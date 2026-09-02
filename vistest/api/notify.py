# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Дайджест непросмотренных падений — единственное, что уходит наружу само.

Всё остальное в VisTest устроено так, что человек приходит и смотрит. Это
работает ровно до первого дня, когда он не пришёл: двенадцать падений лежат
третьи сутки, эталон устаревает, а следующий прогон краснеет уже на них — и
никто не знает, что смотреть надо было позавчера. Инструмент, о котором надо
помнить, — это вкладка, а не часть процесса.

**Куда отправляем.** В вебхук. Один URL в настройках, обычный `POST` с JSON,
никаких клиентов на каждую площадку и никаких токенов у нас. Slack, Mattermost
и Telegram-мосты принимают `{"text": ...}` как есть; всё остальное разбирает
структурную часть того же тела. Почта сюда намеренно не входит: SMTP — это
сервер, логин, пароль и TLS в настройках, то есть вчетверо больше поверхности
ради канала, который в командах всё равно проигрывает чату.

**Когда молчим — и это важнее, чем когда говорим.**

* Разбирать нечего — не пишем. Ежедневное «ожидает 0» приучает не открывать
  канал, и в день, когда написать будет что, его тоже не откроют.
* Список не изменился — не пишем. Повторять один и тот же перечень каждые сутки
  значит превратить его в фон. Про застрявший разбор скажет строка «самое
  старое ждёт третьи сутки» в следующем письме, которое всё-таки уйдёт.
* Отправлять решает **одна** реплика: отметка о последней отправке ставится
  условным UPDATE, и второй воркер, проигравший гонку, молча ничего не делает.
  Иначе три реплики за общим томом дают три одинаковых сообщения.

**Когда не молчим о себе.** Сломавшийся вебхук — худший из отказов: канал тих,
и тишина читается как «всё разобрано». Поэтому причина последней неудачи
хранится и показывается в настройках, а «Отправить сейчас» существует, чтобы
проверить URL не через сутки, а сразу.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request

from .prefs import get_flag, get_text, set_flag, set_text

ENABLED = "notify_enabled"
WEBHOOK = "notify_webhook"
AFTER_HOURS = "notify_after_hours"
LAST_SENT = "notify_last_sent"
LAST_ERROR = "notify_last_error"
LAST_SET = "notify_last_set"

# Сколько ждать, прежде чем считать падение забытым. Сутки — не догма, а
# граница между «человек ещё не дошёл» и «человек уже не дойдёт»: разбор в тот
# же день это нормальная работа, разбор на третьи сутки это долг.
DEFAULT_AFTER_HOURS = 24.0
# Потолок на строки в сообщении. Сотню имён никто не прочитает, а сообщение на
# сотню строк в чате читается как сбой интеграции.
MAX_ROWS = 10
TIMEOUT_S = 10.0


def settings(db) -> dict:
    return {
        "enabled": get_flag(db, ENABLED, False),
        "webhook": get_text(db, WEBHOOK, ""),
        "after_hours": _hours(db),
        "last_sent": get_text(db, LAST_SENT, ""),
        "last_error": get_text(db, LAST_ERROR, ""),
    }


def _hours(db) -> float:
    try:
        return max(0.0, float(get_text(db, AFTER_HOURS, "") or DEFAULT_AFTER_HOURS))
    except (TypeError, ValueError):
        return DEFAULT_AFTER_HOURS


def configure(db, payload: dict, who: str = "") -> dict:
    """Сохранить настройки. Отсутствующий ключ не трогается."""
    if payload.get("enabled") is not None:
        set_flag(db, ENABLED, bool(payload["enabled"]), who)
    if payload.get("webhook") is not None:
        url = str(payload["webhook"]).strip()
        if url and not url.startswith(("http://", "https://")):
            raise ValueError("webhook must be an http(s) URL")
        set_text(db, WEBHOOK, url, who)
        # URL поменяли — прошлая ошибка относилась к прошлому адресу, и
        # показывать её дальше значит обвинять новый адрес в чужой беде.
        set_text(db, LAST_ERROR, "", who)
    if payload.get("after_hours") is not None:
        hours = float(payload["after_hours"])
        if hours < 0:
            raise ValueError("after_hours cannot be negative")
        set_text(db, AFTER_HOURS, str(hours), who)
    return settings(db)


# --------------------------------------------------------------------------- #
#  Что именно ждёт разбора
# --------------------------------------------------------------------------- #
def pending(db, after_hours: float | None = None, limit: int = 200) -> list[dict]:
    """Непросмотренные падения старше порога, самые старые первыми.

    Считается по каждому СНИМКУ, а не по прогону: двадцать прогонов подряд с
    одним и тем же красным `login.png` — это одно незакрытое решение, а не
    двадцать. Берётся последнее сравнение снимка, и только если оно всё ещё
    ждёт разбора: разобранное позже закрывает вопрос задним числом.
    """
    hours = _hours(db) if after_hours is None else float(after_hours)
    rows = db.query(
        "SELECT c.id, s.name AS name, c.max_severity, c.created_at,"
        "       p.name AS project, r.branch, r.run_key"
        "  FROM comparison c"
        "  JOIN snapshot s ON s.id = c.snapshot_id"
        "  JOIN run r ON r.id = c.run_id"
        "  JOIN project p ON p.id = r.project_id"
        " WHERE c.verdict='fail' AND c.review IS NULL"
        "   AND c.id = (SELECT c2.id FROM comparison c2"
        "                WHERE c2.snapshot_id = c.snapshot_id"
        "                ORDER BY c2.created_at DESC, c2.id DESC LIMIT 1)"
        "   AND c.created_at <= datetime('now', ?)"
        # `c.id` вторым ключом — иначе порядок сравнений одной секунды
        # решает движок, и «самое старое» в сообщении меняется от запуска
        # к запуску без единого изменения в данных.
        " ORDER BY c.created_at ASC, c.id ASC LIMIT ?",
        (f"-{hours} hours", limit))
    return [dict(r) for r in rows]


def _fingerprint(items: list[dict]) -> str:
    return ",".join(str(i["id"]) for i in sorted(items, key=lambda i: i["id"]))


def _age(created_at: str) -> str:
    """«третьи сутки» — то, ради чего сообщение и читают."""
    import datetime as _dt

    try:
        stamp = _dt.datetime.fromisoformat(str(created_at).replace("Z", ""))
    except (TypeError, ValueError):
        return ""
    hours = (_dt.datetime.utcnow() - stamp).total_seconds() / 3600
    if hours < 1:
        return "less than an hour"
    if hours < 48:
        return f"{int(hours)}h"
    return f"{int(hours // 24)} days"


def render_digest(items: list[dict], *, base_url: str = "") -> str:
    """Текст сообщения. Первая строка — ответ, как и в комментарии к PR."""
    if not items:
        return ""
    oldest = _age(items[0].get("created_at"))
    head = (f"VisTest: {len(items)} unreviewed "
            f"{'failure' if len(items) == 1 else 'failures'}")
    if oldest:
        head += f", the oldest waiting {oldest}"

    lines = [head + "."]
    for item in items[:MAX_ROWS]:
        where = " · ".join(x for x in [item.get("project") or "",
                                       item.get("branch") or ""] if x)
        age = _age(item.get("created_at"))
        bits = [f"severity {float(item.get('max_severity') or 0):.0f}"]
        if age:
            bits.append(f"waiting {age}")
        if where:
            bits.append(where)
        lines.append(f"• {item.get('name') or 'snapshot'} — " + ", ".join(bits))
    if len(items) > MAX_ROWS:
        lines.append(f"• …and {len(items) - MAX_ROWS} more")

    if base_url:
        lines.append("")
        lines.append(base_url.rstrip("/") + "/ui/#/runs")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
#  Отправка
# --------------------------------------------------------------------------- #
def _post(url: str, payload: dict) -> None:
    # Схема проверяется и здесь, а не только при сохранении. `urlopen` умеет не
    # только http: `file://` и `ftp://` для него такие же адреса, и вебхук —
    # единственное место, где строку из настроек отдают сетевой библиотеке. В
    # базу значение могло попасть до появления проверки в `configure()` или из
    # старого дампа; проверка на выходе не зависит от того, как оно туда легло.
    from urllib.parse import urlparse

    if urlparse(url).scheme not in ("http", "https"):
        raise ValueError("webhook must be an http(s) URL")
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "User-Agent": "vistest-notify"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
        if response.status >= 400:                       # pragma: no cover
            raise urllib.error.HTTPError(
                url, response.status, "webhook refused", response.headers, None)


def _claim(db, previous: str, stamp: str) -> bool:
    """Забрать право на отправку — сравнение-и-обмен, а не запись.

    `previous` — то, что реплика прочитала ДО того, как начала собирать
    дайджест. В этом весь смысл: окно гонки — это сборка дайджеста, а не
    миллисекунда перед записью. Три реплики за общим томом читают одну и ту же
    отметку, а обменять её на свою удаётся ровно одной; две остальные видят
    ноль изменённых строк и молчат, вместо того чтобы прислать в чат три
    одинаковых сообщения.

    Первый раз строки нет вовсе, и `INSERT OR IGNORE` играет ту же роль.
    """
    if not previous:
        return bool(db.execute(
            "INSERT OR IGNORE INTO setting(scope, project_key, name, value,"
            " updated_at, updated_by)"
            " VALUES('global','',?,?,datetime('now'),'notify')",
            (LAST_SENT, stamp)))
    return bool(db.execute(
        "UPDATE setting SET value=?, updated_at=datetime('now')"
        " WHERE scope='global' AND project_key='' AND name=? AND value=?",
        (stamp, LAST_SENT, previous)))


def run_once(db, *, base_url: str = "", force: bool = False,
             dry_run: bool = False) -> dict:
    """Решить, есть ли о чём написать, и написать.

    Возвращает описание того, что произошло, — им пользуются и кнопка
    «Отправить сейчас», и CLI, и тесты. `force` пропускает проверку «список не
    изменился», но не проверку «список пуст»: отправлять пустоту незачем даже
    по кнопке.
    """
    if not force and not get_flag(db, ENABLED, False):
        return {"sent": False, "reason": "notifications are off"}

    # Читается здесь, а не перед самой записью: право на отправку разыгрывается
    # на всё то время, пока реплика собирает дайджест, — иначе гонка остаётся
    # ровно там, где она и была.
    seen_stamp = get_text(db, LAST_SENT, "")

    url = get_text(db, WEBHOOK, "").strip()
    if not url:
        return {"sent": False, "reason": "no webhook configured"}

    items = pending(db)
    if not items:
        # Молчание — это ответ «разбирать нечего». Ежедневное «ожидает 0»
        # приучает не открывать канал.
        return {"sent": False, "reason": "nothing is waiting", "count": 0}

    mark = _fingerprint(items)
    if not force and mark == get_text(db, LAST_SET, ""):
        return {"sent": False, "reason": "same list as last time",
                "count": len(items)}

    text = render_digest(items, base_url=base_url)
    if dry_run:
        return {"sent": False, "reason": "dry run", "count": len(items),
                "text": text}

    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    if not force and not _claim(db, seen_stamp, stamp):
        return {"sent": False, "reason": "another replica is sending",
                "count": len(items)}

    try:
        _post(url, {"text": text, "count": len(items),
                    "snapshots": [i.get("name") for i in items[:MAX_ROWS]]})
    except Exception as e:
        # Тихо сломавшийся вебхук хуже отсутствующего: канал молчит, и тишина
        # читается как «всё разобрано». Причина остаётся видимой в настройках.
        set_text(db, LAST_ERROR, f"{type(e).__name__}: {e}", "notify")
        return {"sent": False, "reason": "webhook failed",
                "error": f"{type(e).__name__}: {e}", "count": len(items)}

    set_text(db, LAST_ERROR, "", "notify")
    set_text(db, LAST_SET, mark, "notify")
    if force:
        set_text(db, LAST_SENT, stamp, "notify")
    return {"sent": True, "count": len(items), "text": text}


# --------------------------------------------------------------------------- #
#  Часы
#
#  Отдельного планировщика в сервисе нет, и заводить его ради одной задачи
#  незачем — это поток, который спит и просыпается. Альтернатива (cron рядом с
#  сервисом) означала бы, что на Windows человек идёт в планировщик задач, а
#  функция «уведомления приходят сами» на деле требует настроить их доставку
#  вручную.
# --------------------------------------------------------------------------- #
TICK_S = float(os.getenv("VISTEST_NOTIFY_TICK_S", "300"))

_ticker: threading.Thread | None = None
_ticker_lock = threading.Lock()
# Куда смотреть на следующем такте. Отдельно от потока, потому что поток один
# на процесс, а база у него может смениться — так делает перезагрузка модуля
# сервиса в тестах. Замкнуть `db` внутри потока значило бы, что часы всю жизнь
# ходят вокруг первой попавшейся базы.
_target: dict = {"db": None, "base_url": ""}


def ensure_ticker(db, *, base_url: str = "") -> threading.Thread | None:
    """Один поток на процесс, сколько бы раз сюда ни зашли.

    Без этой оговорки повторный импорт (а в тестах модуль сервиса
    перезагружается десятками раз) оставлял бы за собой по потоку на каждый.
    """
    global _ticker
    _target["db"] = db
    _target["base_url"] = base_url
    if TICK_S <= 0:
        return None
    with _ticker_lock:
        if _ticker is not None and _ticker.is_alive():
            return _ticker

        def loop() -> None:
            # Сначала спим: смысл в том, чтобы не дёргать сеть на старте
            # сервиса, когда база могла ещё не догнать состояние.
            while True:
                time.sleep(TICK_S)
                target = _target["db"]
                if target is None:
                    continue
                try:
                    run_once(target, base_url=_target["base_url"])
                except Exception:
                    # Часы не могут стоить сервиса: они всего лишь напоминалка.
                    pass

        _ticker = threading.Thread(target=loop, name="vistest-notify", daemon=True)
        _ticker.start()
        return _ticker
