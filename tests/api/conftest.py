# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Общие фикстуры для тестов API.

Тесты бьют по auth-роутеру напрямую (вход, роли, пользователи, приглашения,
профиль команды) на временной базе — без браузера и тяжёлых модулей, поэтому
гоняются быстро и в любом окружении.
"""

from __future__ import annotations

import threading

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from vistest.api import auth as authmod
from vistest.api import db as dbmod
from vistest.api.db import Database


@pytest.fixture(autouse=True)
def _fresh_connections():
    # В проде Database живёт весь процесс, и потоко-локальное соединение — плюс.
    # В тестах же на каждый тест своя временная база, поэтому кэш соединений
    # надо сбрасывать, иначе следующий тест увидит файл предыдущего.
    dbmod._local = threading.local()
    yield
    dbmod._local = threading.local()


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "vistest.db")


@pytest.fixture
def app_client(db):
    # build_router вешает маршруты на модульный router — перед каждой сборкой
    # даём ему чистый, иначе между тестами накапливались бы дубли.
    authmod.router = APIRouter()
    app = FastAPI()
    app.include_router(authmod.build_router(db))
    return TestClient(app), db


@pytest.fixture
def client(app_client):
    return app_client[0]


def make_admin(db, login="anna", password="password123"):
    authmod.create_user(db, login, password, role="admin", name="Анна")
    return login, password


def login(client, login_name, password):
    r = client.post("/api/auth/login",
                    json={"login": login_name, "password": password})
    assert r.status_code == 200, r.text
    return r
