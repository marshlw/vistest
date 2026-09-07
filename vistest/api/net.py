# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Кто звонит: адрес соединения, адрес клиента и схема запроса.

Одно место на весь сервис, и появилось оно после находки, которую стоит
записать целиком, потому что она повторяется в каждом втором развёртывании.

Сервис запускался с `--forwarded-allow-ips *`. В uvicorn это значит
`always_trust`, а `always_trust` берёт из `X-Forwarded-For` **первый** элемент
(`uvicorn/middleware/proxy_headers.py`, `get_trusted_client_address`). Первый
элемент пишет тот, кто прислал запрос: обратный прокси свой адрес ДОПИСЫВАЕТ в
конец, а не заменяет строку. То есть `request.client.host` — это значение,
которое полностью выбирает вызывающий, и никакая правильная настройка Caddy или
nginx этого не меняет.

А на `request.client.host` держалась граница «только с машины сервиса»: правка
`secrets.env`, подключение проекта (то есть запуск произвольного процесса),
правка и запуск тестов, запись с мышью — и, до кучи, счётчик неудачных входов
по адресу. Один заголовок открывал всё перечисленное; на инсталляции, где
администратор ещё не заведён, — вообще без входа, потому что `current_user`
раздаёт роль admin, пока пользователей нет.

Поэтому здесь два РАЗНЫХ понятия, и путать их нельзя:

* `peer()` — адрес того, кто открыл TCP-соединение. Подделать его нельзя,
  сколько заголовков ни пришли. По нему решается «локально или нет».
* `client_ip()` — адрес человека с точки зрения журнала: если соединение
  пришло от доверенного прокси, берётся адрес из `X-Forwarded-For`, иначе тот
  же `peer()`. По нему считается троттлинг и пишется журнал.

Чтобы `peer()` был настоящим, ProxyHeaders в uvicorn отключён (см. Dockerfile
и `serve()`), а заголовки разбираются здесь — с явным списком доверенных
адресов в `VISTEST_TRUSTED_PROXIES`. Пусто — не доверяем никому, и это
правильное значение по умолчанию: «не настроил» не должно означать «доверяю».
"""

from __future__ import annotations

import ipaddress
import os
from functools import lru_cache

from fastapi import Request

# `testclient` — адрес, который подставляет starlette.testclient. Оставлен
# намеренно: тесты гоняют локальные сценарии, и без него половина из них
# проверяла бы не то поведение, которое получит человек на своей машине.
LOOPBACK_NAMES = {"localhost", "testclient"}

ENV_TRUSTED = "VISTEST_TRUSTED_PROXIES"


def _parse_trusted(raw: str) -> tuple[tuple, frozenset[str]]:
    """Разбор `VISTEST_TRUSTED_PROXIES` в сети и имена.

    Адрес или CIDR — обычный случай. Имя (`proxy`, `localhost`, в тестах
    `testclient`) остаётся строкой и сравнивается ТОЧНО с адресом соединения:
    в docker-compose прокси известен именно так, и заставлять администратора
    выяснять адрес контейнера, который меняется при пересоздании, значило бы
    подталкивать его к `0.0.0.0/0`.

    Доверия это не расширяет: сравнивается не заголовок, а то, откуда пришло
    соединение.
    """
    nets, names = [], set()
    for item in (raw or "").replace(";", ",").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            nets.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            names.add(item)
    return tuple(nets), frozenset(names)


@lru_cache(maxsize=8)
def _trusted(raw: str) -> tuple[tuple, frozenset[str]]:
    return _parse_trusted(raw)


def _trusted_now() -> tuple[tuple, frozenset[str]]:
    return _trusted((os.getenv(ENV_TRUSTED) or "").strip())


def trusted_networks() -> tuple:
    return _trusted_now()[0]


def peer(request: Request) -> str:
    """Адрес соединения. Заголовками не управляется."""
    return (request.client.host if request.client else "") or ""


def is_loopback(request: Request) -> bool:
    """Запрос пришёл с самой машины сервиса.

    Считается ТОЛЬКО по адресу соединения. Именно этим отличается от
    `client_ip()`: право запустить процесс на машине не должно зависеть от
    того, что написано в заголовке.
    """
    return _is_loopback_host(peer(request))


def _is_loopback_host(host: str) -> bool:
    host = (host or "").strip()
    if not host:
        return False
    if host in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def from_trusted_proxy(request: Request) -> bool:
    nets, names = _trusted_now()
    if not nets and not names:
        return False
    host = peer(request)
    if host in names:
        return True
    try:
        addr = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return any(addr in net for net in nets)


def client_ip(request: Request) -> str:
    """Адрес клиента для журнала и троттлинга.

    За доверенным прокси — правый недоверенный элемент `X-Forwarded-For`
    (каждый прокси дописывает в конец, поэтому идём справа налево и
    пропускаем адреса доверенных прокси). Без доверенного прокси заголовок не
    смотрим вовсе.
    """
    if not from_trusted_proxy(request):
        return peer(request)

    chain = [p.strip() for p in
             (request.headers.get("x-forwarded-for") or "").split(",") if p.strip()]
    nets, _names = _trusted_now()
    for candidate in reversed(chain):
        try:
            addr = ipaddress.ip_address(candidate.strip("[]").split("%")[0])
        except ValueError:
            continue
        if any(addr in net for net in nets):
            continue
        return str(addr)
    return peer(request)


def forwarded_proto(request: Request) -> str:
    """Схема, о которой сообщил ДОВЕРЕННЫЙ прокси. Иначе пусто."""
    if not from_trusted_proxy(request):
        return ""
    value = (request.headers.get("x-forwarded-proto") or "").split(",")[0]
    return value.strip().lower()
