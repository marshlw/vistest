# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Структурный лог запросов и сквозной идентификатор.

Зачем. Диагностика «почему прогон встал» держалась целиком на логе задачи, а
про сам сервис не было известно ничего: ни какой запрос пришёл, ни сколько он
шёл, ни чем кончился. Единственное, что видел человек при отказе, — текст
ошибки в интерфейсе, и связать его с чем-либо в выводе процесса было нечем.
Отсюда разговор вида «у меня не сохранилось» — «а когда?» — «ну, недавно».

Что здесь есть:

* **`X-Request-Id`** на каждом ответе. Его же несёт `detail` любой пятисотки,
  так что человек может процитировать строчку из интерфейса, а не пересказывать
  события. Если идентификатор пришёл снаружи (прокси, чужой конвейер) — берём
  его: своя нумерация поверх чужой рвёт трассировку ровно там, где она нужна.
* **Одна строка на запрос**: метод, путь, код, длительность, кто спрашивал.
  JSON или текст — по `VISTEST_LOG_FORMAT`; по умолчанию текст, потому что
  сервис чаще смотрят глазами в терминале, чем собирают в хранилище.

Чего здесь намеренно нет.

**Тела запроса.** В нём проезжают пароли из формы входа, значения переменных
окружения со стенда и содержимое `secrets.env`. Лог, в который однажды попал
чужой пароль, — это утечка, которая живёт ровно столько, сколько живут ротации.

**Строки запроса целиком.** Путь пишется как есть, `?query` отбрасывается: там
живут имена снимков и ключи проектов, и это ещё полбеды, но там же — токен
метрик (`/metrics?token=…`).

**Логина в анонимном режиме.** Пока пользователей нет, автор действий — «local»,
и писать в лог «local» на каждую строку значит делать вид, что мы что-то знаем.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from contextvars import ContextVar

log = logging.getLogger("vistest.access")

# Идентификатор текущего запроса — чтобы до него дотянулся обработчик ошибок, не
# получая его параметром через десять слоёв.
current_request_id: ContextVar[str] = ContextVar("vistest_request_id", default="")

HEADER = "X-Request-Id"
# Заголовки, которые ставят прокси и чужие конвейеры. Свой идентификатор поверх
# чужого рвёт трассировку ровно там, где она и нужна.
INBOUND = ("x-request-id", "x-correlation-id", "x-amzn-trace-id")
JSON_FORMAT = (os.getenv("VISTEST_LOG_FORMAT", "").strip().lower() == "json")
# Пути, о которых писать нечего: опрос статуса задачи ходит раз в 700 мс, и одна
# открытая вкладка даёт полторы тысячи строк в час — в них тонет всё остальное.
QUIET_PREFIXES = ("/ui", "/favicon.ico", "/api/health", "/api/presence")


def new_id(inbound: str = "") -> str:
    text = (inbound or "").strip()
    if text:
        # Чужую строку не переписываем, но и не пускаем в лог какой угодно
        # длины и с какими угодно символами: она попадёт в текст ответа.
        return "".join(c for c in text if c.isalnum() or c in "-_.")[:64] or _fresh()
    return _fresh()


def _fresh() -> str:
    return uuid.uuid4().hex[:12]


def _quiet(path: str) -> bool:
    return path.startswith(QUIET_PREFIXES)


def emit(*, method: str, path: str, status: int, ms: float, request_id: str,
         who: str = "", extra: dict | None = None) -> None:
    """Одна строка на запрос."""
    if JSON_FORMAT:
        payload = {"ts": round(time.time(), 3), "id": request_id,
                   "method": method, "path": path, "status": status,
                   "ms": round(ms, 1)}
        if who:
            payload["who"] = who
        if extra:
            payload.update(extra)
        log.info(json.dumps(payload, ensure_ascii=False))
        return
    tail = f" {who}" if who else ""
    log.info("%s %s %s %s %.0fms%s", request_id, method, path, status, ms, tail)


async def middleware(request, call_next):
    """ASGI-прослойка: идентификатор, замер, строка в лог, заголовок в ответ."""
    request_id = new_id(next(
        (request.headers.get(h) for h in INBOUND if request.headers.get(h)), ""))
    token = current_request_id.set(request_id)
    started = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers[HEADER] = request_id
        return response
    finally:
        current_request_id.reset(token)
        # `?query` отбрасывается намеренно: там живёт токен метрик.
        path = request.url.path
        if not _quiet(path):
            emit(method=request.method, path=path, status=status,
                 ms=(time.perf_counter() - started) * 1000,
                 request_id=request_id, who=_who(request))


def _who(request) -> str:
    """Кто спрашивал — по сессии, если она есть.

    В анонимном режиме возвращается пустая строка, а не «local»: писать в лог
    имя, которого нет, значит делать вид, что мы что-то знаем.
    """
    token = request.cookies.get("vistest_session")
    if not token:
        return ""
    try:
        from .auth import session_user
        from .main import db

        user = session_user(db, token)
        return (user or {}).get("login") or ""
    except Exception:
        return ""
