# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Первый экран свежей инсталляции, открытой по сети.

Это ровно тот случай, ради которого всё написано: человек раскатил сервис в
докере, открыл адрес — и упирался в стену «пользователей нет, зайдите на машину
сервиса и выполните `vistest user add`». Стена стояла не зря (пока активных
пользователей нет, роль admin достаётся каждому, кто дотянулся до порта), но
выйти из этого состояния изнутри интерфейса было нельзя вовсе.

Проверять это можно только по-настоящему **по сети**: с петли сервис работает в
локальном режиме и никакой формы не показывает — и правильно делает. Поэтому
здесь uvicorn слушает все интерфейсы, а браузер идёт на внешний адрес машины.
"""

from __future__ import annotations

import socket
import threading
import time

import pytest

pytest.importorskip("playwright.sync_api")

from playwright.sync_api import sync_playwright  # noqa: E402

from ._service import rebind_service as _rebind_service  # noqa: E402

pytestmark = pytest.mark.e2e


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _outbound_ip() -> str:
    """Адрес, по которому машина видна не с петли.

    Подключение никуда не идёт — UDP-сокет только выбирает исходящий интерфейс.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:                                      # pragma: no cover
        return ""
    finally:
        s.close()


@pytest.fixture(scope="module")
def service(tmp_path_factory):
    """Пустая инсталляция: ни одного пользователя, слушает все интерфейсы."""
    import os

    host = _outbound_ip()
    if not host or host.startswith("127."):              # pragma: no cover
        pytest.skip("нет адреса, по которому сервис видно не с петли")

    root = tmp_path_factory.mktemp("vistest-setup-root")
    saved = {k: os.environ.get(k)
             for k in ("VISTEST_ROOT", "VISTEST_AUTH", "VISTEST_ALLOW_OPEN",
                       "VISTEST_SETUP_TOKEN")}
    os.environ["VISTEST_ROOT"] = str(root / ".vistest")
    for key in ("VISTEST_AUTH", "VISTEST_ALLOW_OPEN", "VISTEST_SETUP_TOKEN"):
        os.environ.pop(key, None)

    import uvicorn

    app, db = _rebind_service()

    port = _free_port()
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.time() + 30
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    if not server.started:                               # pragma: no cover
        pytest.skip("service did not start")

    yield f"http://{host}:{port}", db

    server.should_exit = True
    thread.join(timeout=10)
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


@pytest.fixture(scope="module")
def page(service):
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as e:                           # pragma: no cover
            pytest.skip(f"chromium unavailable: {e}")
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        pg = ctx.new_page()
        pg.errors = []
        pg.on("pageerror", lambda e: pg.errors.append(str(e)))
        yield pg
        ctx.close()
        browser.close()


def _open(page, url):
    page.errors.clear()
    page.goto(f"{url}/ui/", wait_until="networkidle")
    page.wait_for_timeout(600)


# --------------------------------------------------------------------------- #
def test_a_fresh_installation_shows_a_form_not_a_wall(page, service):
    """Стена не давала выйти из состояния изнутри — теперь на её месте форма."""
    url, _ = service
    _open(page, url)

    assert page.locator("#blockNotice").count() == 0, "стена всё ещё показывается"
    assert page.locator("#login:not(.hidden)").count() == 1
    assert "Set up VisTest" in page.text_content("#lgTitle")
    assert not page.errors, page.errors


def test_the_form_says_what_the_first_account_will_be(page, service):
    """«Заведите пользователя» без слова «администратор» — половина ответа."""
    url, _ = service
    _open(page, url)
    text = page.text_content("#lgSub")
    assert "administrator" in text
    assert "closes for good" in text


def test_there_is_nothing_to_switch_to_before_the_first_account(page, service):
    """Вкладка «подать заявку» на пустой инсталляции ответила бы отказом.

    Показать её значит соврать: разбирать заявку ещё некому.
    """
    url, _ = service
    _open(page, url)
    assert page.locator("#lgTabs.hidden").count() == 1


def test_the_first_administrator_is_created_from_the_browser(page, service):
    """Тот самый сценарий целиком: открыл адрес — и работаешь."""
    url, db = service
    _open(page, url)

    page.fill("#lgName", "Анна")
    page.fill("#lgLogin", "anna")
    page.fill("#lgPass", "password123")
    page.click("#lgSubmit")
    page.wait_for_function("() => document.querySelector('#login')"
                           ".classList.contains('hidden')", timeout=15000)
    page.wait_for_timeout(800)

    assert db.one("SELECT role FROM user WHERE login='anna'")["role"] == "admin"
    # И это уже рабочий интерфейс, а не пустой экран за формой.
    assert page.locator("#nav a").count() > 0
    assert not page.errors, page.errors


def test_after_that_the_screen_offers_signing_in_and_requesting_access(
        page, service):
    """Дверь закрылась: заводить второго администратора этим путём нельзя."""
    url, _ = service
    page.evaluate("() => fetch('/api/auth/logout',{method:'POST'})"
                  "  .then(r => r.status)")
    _open(page, url)

    assert "Sign in" in page.text_content("#lgTitle")
    assert page.locator("#lgTabs.hidden").count() == 0
    assert page.locator("#lgTabs .lg-tab").count() == 2


def test_a_request_says_it_is_waiting_instead_of_signing_you_in(page, service):
    """Молча вернуть человека к форме входа значит отправить его пробовать
    пароль, который ещё не работает."""
    url, db = service
    _open(page, url)

    page.click('#lgTabs .lg-tab[data-tab="register"]')
    page.fill("#lgName", "Витя")
    page.fill("#lgLogin", "vic")
    page.fill("#lgPass", "password123")
    page.click("#lgSubmit")
    page.wait_for_timeout(1200)

    assert "approve" in page.text_content("#lgOk")
    assert page.locator("#login:not(.hidden)").count() == 1
    assert db.one("SELECT status FROM user WHERE login='vic'"
                  )["status"] == "pending"


def test_a_pending_account_is_told_why_it_cannot_get_in(page, service):
    """«Неверный логин или пароль» отправило бы человека менять пароль."""
    url, _ = service
    _open(page, url)
    page.fill("#lgLogin", "vic")
    page.fill("#lgPass", "password123")
    page.click("#lgSubmit")
    page.wait_for_timeout(1200)

    assert "approve" in page.text_content("#lgMsg")
    assert page.locator("#login:not(.hidden)").count() == 1
