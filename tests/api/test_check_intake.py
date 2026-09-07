# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""`POST /api/check` — вход для наборов на любом языке.

Роут задуман как дверь для тестов на Cypress, Playwright JS, Java и C#: их
тест снимает страницу своими средствами и присылает PNG сюда. Дверь при этом
была заперта на сессионную куку — то есть на браузер, которого у раннера в CI
нет и не будет. На одиночной установке это не всплывало (пользователей нет,
пускают всех), а на первой же командной инсталляции каждый снимок получал 401,
и выглядело это как «интеграция не работает».

Здесь закреплены оба конца: с токеном пускают, без токена на сетевой
инсталляции — нет.
"""

from __future__ import annotations

import io
import threading

import numpy as np
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VISTEST_ROOT", str(tmp_path / ".vistest"))
    monkeypatch.delenv("VISTEST_AUTH", raising=False)
    monkeypatch.delenv("VISTEST_INGEST_TOKEN", raising=False)
    monkeypatch.delenv("VISTEST_INGEST_OPEN", raising=False)

    import importlib

    from fastapi import APIRouter

    import vistest.api.auth as authmod
    import vistest.api.db as dbmod

    dbmod._local = threading.local()
    authmod.router = APIRouter()

    # `check` держит корень данных и конфигурацию в модульных переменных,
    # вычисленных на импорте. В бою это правильно — один процесс, один корень;
    # в тестах это означало бы, что все они пишут в один каталог и видят
    # эталоны друг друга. Перечитываем ДО `main`: он забирает роутер по
    # значению.
    import vistest.api.check as checkmod
    importlib.reload(checkmod)

    import vistest.api.main as mainmod
    importlib.reload(mainmod)
    return TestClient(mainmod.app), mainmod


def _admin(mainmod, client):
    from vistest.api.auth import create_user
    create_user(mainmod.db, "anna", "password123", role="admin")
    client.post("/api/auth/login",
                json={"login": "anna", "password": "password123"})


def _png(color=(30, 90, 200)) -> bytes:
    from PIL import Image

    image = np.zeros((40, 60, 3), np.uint8)
    image[:] = color
    buf = io.BytesIO()
    Image.fromarray(image).save(buf, format="PNG")
    return buf.getvalue()


def _send(client, headers=None, name="checkout.png", color=(30, 90, 200)):
    return client.post(
        "/api/check",
        data={"name": name, "project": "shop", "browser": "chromium"},
        files={"image": ("shot.png", _png(color), "image/png")},
        headers=headers or {},
    )


# --------------------------------------------------------------------------- #
def test_a_runner_with_a_token_is_let_in(service, monkeypatch):
    """Ровно тот путь, ради которого роут и существует."""
    client, mainmod = service
    _admin(mainmod, client)
    client.cookies.clear()                     # у пайплайна куки нет
    monkeypatch.setenv("VISTEST_INGEST_TOKEN", "s3cret")

    r = _send(client, {"X-VisTest-Token": "s3cret"})

    assert r.status_code == 200, r.text
    assert r.json()["verdict"] == "new_baseline"


def test_a_project_token_works_the_same_way(service):
    """Токен проекта — тот же вход, но без общего на всех секрета."""
    client, mainmod = service
    _admin(mainmod, client)

    from vistest.api import citokens

    token = citokens.issue(mainmod.db, name="ci", project="shop",
                           role="reviewer", created_by="anna")["token"]
    client.cookies.clear()

    assert _send(client, {"X-VisTest-Token": token}).status_code == 200


def test_a_token_issued_for_another_project_is_refused(service):
    """Иначе один подрядчик переписывает эталоны другого."""
    client, mainmod = service
    _admin(mainmod, client)

    from vistest.api import citokens

    token = citokens.issue(mainmod.db, name="ci", project="other",
                           role="reviewer", created_by="anna")["token"]
    client.cookies.clear()

    r = _send(client, {"X-VisTest-Token": token})

    assert r.status_code == 403
    assert "other" in r.json()["detail"]


def test_without_a_token_a_shared_installation_still_refuses(service, monkeypatch):
    """Открывая дверь для CI, нельзя открыть её для всех подряд."""
    client, mainmod = service
    _admin(mainmod, client)
    client.cookies.clear()
    monkeypatch.setenv("VISTEST_INGEST_TOKEN", "s3cret")

    assert _send(client).status_code == 401


def test_the_stabilization_script_is_reachable_before_any_check(service):
    """Клиент забирает его ПЕРВЫМ, до всякой проверки.

    Данных в скрипте нет: это тот же код заморозки анимаций, что уезжает в
    пакете на PyPI. Закрытый роут здесь означал бы, что снимки из чужого
    набора несопоставимы с нашими, — при том что скрывать нечего.
    """
    client, mainmod = service
    _admin(mainmod, client)
    client.cookies.clear()

    r = client.get("/api/stabilize.js")

    assert r.status_code == 200
    assert "__vistest" in r.text


def test_a_session_still_works(service):
    """Человек, толкающий прогон со своей машины, ничего не заметил."""
    client, mainmod = service
    _admin(mainmod, client)

    assert _send(client).status_code == 200


# --------------------------------------------------------------------------- #
#  Изоляция проектов
# --------------------------------------------------------------------------- #
def test_two_projects_do_not_share_one_baseline(service):
    """`project` возвращался в ответе и не участвовал в хранении.

    Две команды, приславшие `checkout.png`, писали в один эталон и молча
    переписывали друг друга — а по вердикту это выглядит как регресс, которого
    в приложении нет.
    """
    client, mainmod = service
    _admin(mainmod, client)

    first = client.post("/api/check", data={"name": "checkout.png",
                                            "project": "shop"},
                        files={"image": ("s.png", _png((30, 90, 200)),
                                         "image/png")})
    second = client.post("/api/check", data={"name": "checkout.png",
                                             "project": "billing"},
                         files={"image": ("s.png", _png((200, 40, 40)),
                                          "image/png")})

    assert first.json()["verdict"] == "new_baseline"
    # Второй проект — тоже первый раз, а не «регресс на весь экран».
    assert second.json()["verdict"] == "new_baseline"
    assert first.json()["name"] == "shop/checkout.png"
    assert second.json()["name"] == "billing/checkout.png"


def test_the_same_project_still_compares_with_itself(service):
    client, mainmod = service
    _admin(mainmod, client)

    _send(client, name="checkout.png", color=(30, 90, 200))
    again = _send(client, name="checkout.png", color=(30, 90, 200))

    assert again.json()["verdict"] == "pass"


def test_a_baseline_from_before_the_split_is_not_lost(service):
    """Молча объявить снимок новым — значит потерять точку отсчёта у всех, кто
    уже пользуется этим входом."""
    client, mainmod = service
    _admin(mainmod, client)

    # Как это лежало раньше: без префикса проекта.
    from vistest.config import VisTestConfig
    from vistest.service import CheckService

    cfg = VisTestConfig.load()
    CheckService(cfg, browser="chromium").save_baseline(
        "checkout.png", np.full((40, 60, 3), 90, np.uint8))

    r = _send(client, name="checkout.png", color=(90, 90, 90))

    assert r.json()["verdict"] == "pass", r.text
    assert any("legacy" in n for n in r.json()["notes"]), r.json()["notes"]


def test_a_name_that_already_names_its_project_is_left_alone(service):
    """Иначе получилось бы `shop/shop/checkout.png`."""
    client, mainmod = service
    _admin(mainmod, client)

    r = client.post("/api/check",
                    data={"name": "shop/checkout.png", "project": "shop"},
                    files={"image": ("s.png", _png(), "image/png")})

    assert r.json()["name"] == "shop/checkout.png"


def test_a_single_user_installation_is_untouched(service):
    """`default` — не проект, а подпись установки, где проектов нет."""
    client, mainmod = service
    _admin(mainmod, client)

    r = client.post("/api/check", data={"name": "checkout.png"},
                    files={"image": ("s.png", _png(), "image/png")})

    assert r.json()["name"] == "checkout.png"


# --------------------------------------------------------------------------- #
#  Посев эталона из их отчёта
# --------------------------------------------------------------------------- #
def _seed(client, headers=None, name="checkout.png", project="shop",
          color=(30, 90, 200)):
    return client.post("/api/baselines/seed",
                       data={"name": name, "project": project,
                             "browser": "chromium"},
                       files={"image": ("e.png", _png(color), "image/png")},
                       headers=headers or {})


def test_the_first_baseline_can_come_from_their_own_report(service):
    """Иначе первый прогон отвечает «новый эталон» на каждый снимок.

    Репортёр Playwright забирает из отчёта тройку expected/actual/diff — их
    собственный эталон приезжает вместе с фактом. Без посева день первый
    выглядел бы как «инструмент ничего не сравнивает».
    """
    client, mainmod = service
    _admin(mainmod, client)

    seeded = _seed(client)
    assert seeded.status_code == 200, seeded.text
    assert seeded.json()["name"] == "shop/checkout.png"

    # Тот же кадр — совпадение, а не «первый раз».
    r = client.post("/api/check",
                    data={"name": "checkout.png", "project": "shop"},
                    files={"image": ("a.png", _png((30, 90, 200)), "image/png")})
    assert r.json()["verdict"] == "pass", r.text


def test_seeding_never_overwrites_an_existing_baseline(service):
    """Их `expected` приезжает на каждом падении.

    «Просто записать» означало бы принимать регресс автоматически — ровно то,
    ради чего инструмент и стоит.
    """
    client, mainmod = service
    _admin(mainmod, client)
    _seed(client, color=(30, 90, 200))

    again = _seed(client, color=(200, 40, 40))

    assert again.status_code == 409
    # И эталон остался прежним: красный кадр по-прежнему регресс.
    r = client.post("/api/check",
                    data={"name": "checkout.png", "project": "shop"},
                    files={"image": ("a.png", _png((200, 40, 40)), "image/png")})
    assert r.json()["verdict"] == "fail"


def test_seeding_needs_the_same_token_as_everything_else(service, monkeypatch):
    client, mainmod = service
    _admin(mainmod, client)
    client.cookies.clear()
    monkeypatch.setenv("VISTEST_INGEST_TOKEN", "s3cret")

    assert _seed(client).status_code == 401
    assert _seed(client, {"X-VisTest-Token": "s3cret"}).status_code == 200
