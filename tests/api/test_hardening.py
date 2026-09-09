# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Что защищает сервис, открытый в сеть.

Три темы: заголовки страницы, запрос со страницы чужого сайта и счётчики,
которые не дают перебирать пароли — и при этом не превращаются в способ
запереть человека из его же сервиса.
"""

from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VISTEST_ROOT", str(tmp_path / ".vistest"))
    monkeypatch.delenv("VISTEST_AUTH", raising=False)
    monkeypatch.delenv("VISTEST_CORS_ORIGINS", raising=False)

    import importlib

    from fastapi import APIRouter

    import vistest.api.auth as authmod
    import vistest.api.db as dbmod

    dbmod._local = threading.local()
    authmod.router = APIRouter()

    import vistest.api.check as checkmod
    importlib.reload(checkmod)

    import vistest.api.main as mainmod
    importlib.reload(mainmod)
    return TestClient(mainmod.app), mainmod


def _admin(mainmod, login="anna", password="password123"):
    from vistest.api.auth import create_user
    create_user(mainmod.db, login, password, role="admin")
    return login, password


# --------------------------------------------------------------------------- #
#  Заголовки
# --------------------------------------------------------------------------- #
def test_the_interface_is_not_framable_and_has_a_policy(service):
    """«Принять как эталон» необратимо — на такой кнопке кликджекинг стоит эталона."""
    client, _mainmod = service
    r = client.get("/ui/")
    assert r.status_code == 200, r.text

    csp = r.headers.get("content-security-policy", "")
    assert "frame-ancestors 'none'" in csp
    assert "script-src 'self'" in csp
    assert r.headers.get("x-frame-options") == "DENY"
    assert r.headers.get("x-content-type-options") == "nosniff"


def test_api_answers_carry_the_baseline_headers_too(service):
    client, _mainmod = service
    r = client.get("/api/health")
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.headers.get("referrer-policy") == "same-origin"


def test_hsts_is_not_invented_by_the_service(service):
    """Ошибочный HSTS на инсталляции без TLS лечится очисткой настроек у всех."""
    client, _mainmod = service
    assert "strict-transport-security" not in {
        k.lower() for k in client.get("/ui/").headers}


# --------------------------------------------------------------------------- #
#  Запрос со страницы чужого сайта
# --------------------------------------------------------------------------- #
def test_a_post_from_another_origin_is_refused(service):
    """Сейчас от этого спасает SameSite — свойство куки, а не решение сервиса."""
    client, mainmod = service
    login, password = _admin(mainmod)

    r = client.post("/api/auth/login",
                    json={"login": login, "password": password},
                    headers={"Origin": "https://evil.example.com"})
    assert r.status_code == 403, r.text
    assert "another origin" in r.text


def test_a_post_from_the_interface_itself_goes_through(service):
    client, mainmod = service
    login, password = _admin(mainmod)

    r = client.post("/api/auth/login",
                    json={"login": login, "password": password},
                    headers={"Origin": "http://testserver"})
    assert r.status_code == 200, r.text


def test_a_client_without_an_origin_is_not_affected(service):
    """CI, curl и наши клиенты на других языках Origin не присылают вовсе."""
    client, mainmod = service
    login, password = _admin(mainmod)

    assert client.post("/api/auth/login",
                       json={"login": login, "password": password}
                       ).status_code == 200


def test_an_allowed_origin_still_works(service, monkeypatch, tmp_path):
    """Администратор, задавший список, знает, что делает."""
    monkeypatch.setenv("VISTEST_CORS_ORIGINS", "https://ci.example.com")

    import importlib
    import threading as _threading

    import vistest.api.db as dbmod
    dbmod._local = _threading.local()
    import vistest.api.main as mainmod
    importlib.reload(mainmod)

    client = TestClient(mainmod.app)
    login, password = _admin(mainmod)
    r = client.post("/api/auth/login",
                    json={"login": login, "password": password},
                    headers={"Origin": "https://ci.example.com"})
    assert r.status_code == 200, r.text


# --------------------------------------------------------------------------- #
#  Счётчики попыток
# --------------------------------------------------------------------------- #
def test_guessing_one_password_from_one_address_gets_locked(service):
    client, mainmod = service
    login, password = _admin(mainmod)

    from vistest.api.auth import LOGIN_MAX_FAILURES_PAIR

    for _ in range(LOGIN_MAX_FAILURES_PAIR):
        client.post("/api/auth/login", json={"login": login, "password": "no"})

    r = client.post("/api/auth/login", json={"login": login, "password": password})
    assert r.status_code == 429, "перебор одной пары должен запираться"
    assert r.headers.get("Retry-After")


def test_a_stranger_cannot_lock_someone_out_of_their_own_service(service):
    """Порог по одному логину со всех адресов был равен порогу перебора.

    Это значило, что любой, кто знает чужой логин, запирает человека восемью
    запросами. Здесь неудачи приходят с РАЗНЫХ адресов, и владелец должен
    по-прежнему входить.
    """
    client, mainmod = service
    login, password = _admin(mainmod)

    from vistest.api import auth as authmod

    # Журнал заполняется напрямую: адрес соединения в тесте один, а нам нужно
    # именно «много разных адресов».
    for i in range(authmod.LOGIN_MAX_FAILURES_PAIR + 4):
        authmod.audit(mainmod.db, login, "login.failed", f"203.0.113.{i}")

    r = client.post("/api/auth/login", json={"login": login, "password": password})
    assert r.status_code == 200, "владельца запирать нельзя"


def test_access_requests_are_throttled_per_address(service):
    """Заявка стоит PBKDF2 в 480 000 раундов — то есть была бесплатным DoS."""
    client, mainmod = service
    _admin(mainmod)          # чтобы окно первого администратора закрылось

    from vistest.api.auth import REGISTER_MAX_PER_IP

    for i in range(REGISTER_MAX_PER_IP):
        r = client.post("/api/auth/register",
                        json={"login": f"user{i}", "password": "password123"})
        assert r.status_code == 202, r.text

    r = client.post("/api/auth/register",
                    json={"login": "one-more", "password": "password123"})
    assert r.status_code == 429, r.text
    assert not mainmod.db.one("SELECT id FROM user WHERE login='one-more'"), \
        "отказ должен случаться ДО создания записи и до хеширования"


def test_the_pending_queue_has_a_ceiling(service, monkeypatch):
    """Очередь разбирает человек, значит её можно засыпать."""
    client, mainmod = service
    _admin(mainmod)

    from vistest.api import auth as authmod
    monkeypatch.setattr(authmod, "MAX_PENDING_USERS", 2)

    for i in range(2):
        authmod.create_user(mainmod.db, f"waiting{i}", "password123",
                            status="pending")

    r = client.post("/api/auth/register",
                    json={"login": "late", "password": "password123"})
    assert r.status_code == 429, r.text
    assert "waiting for review" in r.text


# --------------------------------------------------------------------------- #
#  Метрики самого сервиса
# --------------------------------------------------------------------------- #
def test_metrics_report_the_service_not_only_the_pictures(service):
    """`/metrics` отвечал только про картинки.

    Когда ночью спрашивают «сервис жив?», нужны другие числа: сколько
    запросов, сколько из них пятисоток, сколько они идут.
    """
    client, mainmod = service
    login, password = _admin(mainmod)
    # `/metrics` закрыт ролью viewer: имена снимков и частота падений — не
    # публичные данные. Скрейперу для этого есть VISTEST_METRICS_TOKEN.
    client.post("/api/auth/login", json={"login": login, "password": password})

    client.get("/api/health")
    client.get("/api/runs")

    body = client.get("/metrics").text
    assert "vistest_http_requests_total" in body
    assert "vistest_http_seconds_total" in body
    assert 'method="GET"' in body
    assert "vistest_jobs" in body


def test_metric_labels_do_not_carry_identifiers(service):
    """Путь в метке — это десятки тысяч рядов через неделю."""
    client, mainmod = service
    login, password = _admin(mainmod)
    client.post("/api/auth/login", json={"login": login, "password": password})
    client.get("/api/runs/1481")

    body = client.get("/metrics").text
    assert "1481" not in body


# --------------------------------------------------------------------------- #
#  Адрес API и размер запроса
# --------------------------------------------------------------------------- #
def test_the_versioned_path_reaches_the_same_route(service):
    """`/api/v1/...` — стабильный адрес для клиентов на других языках.

    Не копия роутов: префикс снимается до маршрутизации, поэтому разойтись
    двум адресам нечем.
    """
    client, _mainmod = service
    plain = client.get("/api/health")
    versioned = client.get("/api/v1/health")

    assert versioned.status_code == plain.status_code == 200
    assert versioned.json() == plain.json()


def test_the_versioned_path_is_guarded_the_same_way(service):
    """Версия в адресе не должна быть обходом стража доступа."""
    client, mainmod = service
    _admin(mainmod)          # появился пользователь — значит нужен вход
    client.cookies.clear()

    assert client.get("/api/v1/runs").status_code == 401


def test_a_giant_json_body_is_refused(service):
    """У JSON-роутов потолка не было вовсе, а тело разбирается целиком."""
    client, mainmod = service

    from vistest.api import main as mainmodule

    body = {"run_id": "x", "project": "shop", "comparisons": []}
    r = client.post(
        "/api/runs", json=body,
        headers={"Content-Length": str(mainmodule.MAX_BODY_BYTES + 1)})
    assert r.status_code == 413, r.text


def test_uploads_are_not_affected_by_the_body_limit(service):
    """У загрузок свой предел, более строгий — общий им только мешал бы."""
    client, _mainmod = service
    import io as _io

    import numpy as _np
    from PIL import Image

    buf = _io.BytesIO()
    Image.fromarray(_np.zeros((10, 10, 3), _np.uint8)).save(buf, format="PNG")
    r = client.post("/api/check",
                    data={"name": "a.png", "project": "shop"},
                    files={"image": ("a.png", buf.getvalue(), "image/png")})
    assert r.status_code == 200, r.text


# --------------------------------------------------------------------------- #
#  Порядок проверок
# --------------------------------------------------------------------------- #
def test_deleting_something_that_does_not_exist_asks_for_rights_first(service):
    """Иначе по коду ответа перебором снимается карта чужих объектов."""
    client, mainmod = service
    _admin(mainmod)
    client.cookies.clear()

    assert client.delete("/api/comparisons/424242").status_code == 401
    assert client.delete("/api/snapshots/424242/history").status_code == 401


# --------------------------------------------------------------------------- #
#  Предел движка
# --------------------------------------------------------------------------- #
def test_the_engine_refuses_an_image_it_cannot_hold():
    """Убитый по памяти процесс не пишет в лог ничего — отказ должен быть явным.

    Предел приезжает полем конфига, как и всё остальное в движке, поэтому тест
    его задаёт, а не патчит модуль. Разница не косметическая: патч модульной
    переменной переживает тест, если что-то упало между присваиванием и
    `finally`, и следующий тест сравнивает картинки с чужим пределом.
    """
    import numpy as _np
    import pytest as _pytest

    from vistest.core import comparator
    from vistest.core.settings import DiffConfig

    small = _np.zeros((10, 10, 3), _np.uint8)
    with _pytest.raises(comparator.ImageTooLarge, match="Mpx"):
        comparator.compare(small, small, cfg=DiffConfig(max_pixels=50))


def test_the_engine_limit_can_be_switched_off():
    """Ноль — «не проверять»: у предела должен быть способ его снять."""
    import numpy as _np

    from vistest.core import comparator
    from vistest.core.settings import DiffConfig

    small = _np.zeros((10, 10, 3), _np.uint8)
    res = comparator.compare(small, small, cfg=DiffConfig(max_pixels=0))
    assert res is not None
