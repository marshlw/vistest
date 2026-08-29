# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Дымовой прогон собственного интерфейса.

Платформа визуального тестирования не проверяла визуально сама себя: на 2000
строк `frontend/index.html` не было ни одного теста. Поэтому расхождения между
фронтом и API жили месяцами и обнаруживались руками — «Переснять» всегда
отвечала 400, «Снять эталон» уходила без `targets`, панель «что шумит чаще
всего» разваливалась на пустом списке.

Здесь поднимается настоящее приложение с временной базой, открывается в
Chromium и проверяется, что каждый экран отрисовался без единой ошибки в
консоли и без единого неудачного запроса к API.

Тесты пропускаются, если Playwright или браузер недоступны, — чтобы не ронять
основной прогон в окружении без них.
"""

from __future__ import annotations

import socket
import threading
import time

import pytest

pytest.importorskip("playwright.sync_api")

from playwright.sync_api import sync_playwright  # noqa: E402

from ._service import rebind_service as _rebind_service  # noqa: E402

# Модуль поднимает настоящий сервис и импортирует `vistest.api.main`, который
# читает VISTEST_ROOT на импорте. Держим его последним в сессии.
pytestmark = pytest.mark.e2e


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def service(tmp_path_factory):
    """Живой сервис с одним прогоном, одним эталоном и одним проектом."""
    import os

    root = tmp_path_factory.mktemp("vistest-root")
    # Окружение восстанавливается на выходе: без этого VISTEST_ROOT утекал в
    # остальные тесты сессии, и они начинали видеть чужой реестр проектов.
    saved = {k: os.environ.get(k) for k in ("VISTEST_ROOT", "VISTEST_AUTH")}
    os.environ["VISTEST_ROOT"] = str(root / ".vistest")
    os.environ["VISTEST_AUTH"] = "off"

    import numpy as np
    import uvicorn

    # `vistest.api.main` читает VISTEST_ROOT и открывает базу НА ИМПОРТЕ, а
    # модулей e2e теперь два и у каждого свой каталог. Обычный `import` отдал бы
    # второму модулю приложение первого — с чужой базой и чужими эталонами, что
    # выглядит как «тесты падают по очереди, а поодиночке проходят».
    app, db = _rebind_service()

    from vistest.config import VisTestConfig, platform_key
    from vistest.projects import BaselineDir, Project, ProjectRegistry
    from vistest.storage import BaselineRecord, FileBaselineStore

    cfg = VisTestConfig.load()
    pf = platform_key()

    # Эталон в собственном наборе — чтобы вкладка «Baselines» была не пустой.
    FileBaselineStore(cfg.baselines_path(pf)).save(BaselineRecord(
        name="dashboard.png", image=np.full((40, 60, 3), 200, dtype=np.uint8),
        meta={"url": "https://example.test/dashboard"}))

    # Подключённый проект со своим набором VisTest.
    repo = root / "their-repo"
    (repo / "screens").mkdir(parents=True)
    (repo / "snapshots").mkdir(parents=True)
    project = Project(key="acme", name="Acme", root=str(repo), tests="screens",
                      baseline_store="project",
                      baselines=[BaselineDir(dir="snapshots")])
    ProjectRegistry(cfg).save(project)
    FileBaselineStore(project.vistest_baselines_path(cfg, pf)).save(
        BaselineRecord(name="login.png",
                       image=np.full((40, 60, 3), 90, dtype=np.uint8),
                       meta={"url": "https://example.test/login"}))

    # Прогон с падением, ошибкой и новым эталоном — все ветки отрисовки.
    db.ingest_run({
        "run_id": "ui-smoke-1", "platform": pf, "browser": "chromium",
        "created_at": "2026-01-01T00:00:00", "git": {"branch": "main", "sha": "abc1234"},
        "totals": {"total": 3, "passed": 1, "failed": 1, "new": 1},
        "baseline_scope": "global",
        "comparisons": [
            {"name": "dashboard.png", "verdict": "fail",
             "metrics": {"ssim_global": 0.97, "de_mean": 3.1, "de_p95": 9.0,
                         "changed_area_pct": 0.4, "max_severity": 44.0},
             "regions": [{"x": 1, "y": 2, "w": 10, "h": 8, "kind": "content",
                          "severity": 44.0, "region_index": 0}],
             "artifacts": {}},
            {"name": "ok.png", "verdict": "pass",
             "metrics": {"ssim_global": 1.0, "max_severity": 0.0}},
            {"name": "broken.png", "verdict": "error",
             "error": "page did not answer"},
        ],
    }, "demo")

    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.time() + 30
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    if not server.started:                                   # pragma: no cover
        pytest.skip("service did not start")

    yield f"http://127.0.0.1:{port}"

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
            # В контейнере настроен прокси; без явного обхода Chromium
            # пытается ходить через него даже на 127.0.0.1 и получает
            # ERR_TUNNEL_CONNECTION_FAILED на собственный же сервис.
            browser = p.chromium.launch(
                headless=True,
                args=["--no-proxy-server",
                      "--proxy-bypass-list=<-loopback>",
                      "--disable-dev-shm-usage"])
        except Exception as e:                               # pragma: no cover
            pytest.skip(f"chromium unavailable: {e}")
        ctx = browser.new_context(viewport={"width": 1440, "height": 1000})
        pg = ctx.new_page()

        pg.errors = []
        pg.bad_requests = []
        pg.on("console", lambda m: pg.errors.append(m.text)
              if m.type == "error" else None)
        pg.on("pageerror", lambda e: pg.errors.append(f"pageerror: {e}"))

        def _watch(response):
            if "/api/" in response.url and response.status >= 400:
                pg.bad_requests.append(f"{response.status} {response.url}")

        pg.on("response", _watch)

        # Внешние ресурсы (шрифты, favicon) в закрытом контейнере недоступны и
        # к качеству интерфейса отношения не имеют — записываем отдельно.
        def _failed(request):
            same_origin = request.url.startswith(service)
            (pg.errors if same_origin else pg.external_failures).append(
                f"requestfailed {request.url}")

        pg.external_failures = []
        pg.on("requestfailed", _failed)
        yield pg
        browser.close()


def _visit(page, service, hash_route: str):
    page.errors.clear()
    page.bad_requests.clear()
    page.goto(f"{service}/ui/#{hash_route}", wait_until="networkidle")
    page.wait_for_timeout(700)


def _open_run(page, service, key: str = "ui-smoke-1"):
    """Открыть прогон страницей: список больше не master/detail."""
    _visit(page, service, "/runs")
    page.locator(".run-item", has_text=key).first.click()
    page.wait_for_selector("#screen .h1.mono")
    page.wait_for_timeout(400)


# Ошибки, вызванные отсутствием внешней сети в контейнере, а не кодом.
OFFLINE = ("ERR_TUNNEL_CONNECTION_FAILED", "ERR_NAME_NOT_RESOLVED",
           "ERR_INTERNET_DISCONNECTED", "ERR_PROXY_CONNECTION_FAILED")


def _clean(page, allow: tuple[str, ...] = ()) -> None:
    allow = allow + OFFLINE
    errors = [e for e in page.errors if not any(a in e for a in allow)]
    bad = [b for b in page.bad_requests if not any(a in b for a in allow)]
    assert not errors, f"console errors: {errors}"
    assert not bad, f"failed API calls: {bad}"


# --------------------------------------------------------------------------- #
def test_runs_screen_renders(page, service):
    _visit(page, service, "/runs")
    _clean(page)
    assert page.locator(".run-item").count() >= 1
    assert "ui-smoke-1" in page.locator(".run-item").first.inner_text()


def test_every_run_has_a_delete_button(page, service):
    """Жалоба №1: удалить один прогон было нечем.

    Роут `DELETE /api/runs/{id}` существовал, кнопки не было ни одной.
    """
    _visit(page, service, "/runs")
    item = page.locator(".run-item").first
    assert item.locator("button[title='Delete this run']").count() == 1


def test_run_filters_are_present_and_work(page, service):
    _visit(page, service, "/runs")
    filters = page.locator(".bar button")
    assert filters.count() >= 4
    filters.nth(1).click()                      # «With failures»
    page.wait_for_timeout(400)
    _clean(page)
    assert page.locator(".run-item").count() >= 1


def test_error_snapshots_are_clickable(page, service):
    """Строка с verdict=error не открывалась вообще: onclick не навешивался."""
    _open_run(page, service)
    page.wait_for_selector(".snap-row")
    err = page.locator(".snap-row.is-err").first
    assert err.count() == 1
    err.click()
    page.wait_for_timeout(600)
    assert "/compare/" in page.url


def test_compare_screen_renders(page, service):
    _open_run(page, service)
    page.wait_for_selector(".snap-row")
    page.locator(".snap-row").first.click()
    page.wait_for_timeout(800)
    _clean(page)
    assert page.locator(".triage .left").count() == 1
    # Решение обязано говорить, в какое хранилище уйдёт апрув.
    assert "own set" in page.locator(".side", has_text="WHERE IT LANDS").inner_text()


def test_dashboard_renders_without_nan(page, service):
    """Панель «что шумит чаще всего» разваливалась на списке by_kind."""
    _visit(page, service, "/dash")
    _clean(page)
    text = page.locator("#screen").inner_text()
    assert "NaN" not in text
    assert "undefined" not in text
    assert "[object Object]" not in text


def test_dashboard_shows_what_backend_computed(page, service):
    """Гистограмма severity, медленные и мёртвые снимки считались и не рисовались."""
    _visit(page, service, "/dash")
    # Регистр меток здесь неважен и нарочно не проверяется: тест про то, что
    # число вообще показано, а не про то, как оно набрано.
    text = page.locator("#screen").inner_text().lower()
    assert "severity of failures against the threshold" in text
    assert "slowest snapshots" in text
    assert "not run for a long time" in text
    assert "errors" in text          # verdict=error не попадал в сводку вовсе


def test_baselines_screen_has_a_store_picker(page, service):
    """Жалоба №2: набор VisTest подключённого проекта не был виден нигде."""
    _visit(page, service, "/baselines")
    _clean(page)
    # Набор и платформа — строкой над сеткой, а не колонкой слева: колонка
    # забирала двести пикселей ширины на каждом заходе, включая те, где их не
    # переключают неделями.
    bar = page.locator("#screen .bar").first.inner_text().lower()
    assert "set" in bar
    assert "acme" in bar


def test_project_snapshots_are_visible_and_runnable(page, service):
    _visit(page, service, "/baselines")
    page.get_by_text("Acme — VisTest set").click()
    page.wait_for_timeout(800)
    _clean(page)
    cards = page.locator(".snap-card")
    assert cards.count() == 1
    assert "login" in cards.first.inner_text()
    # Именно то, чего не было: по такому снимку можно запустить проверку.
    check = cards.first.locator("button", has_text="Check").first
    assert check.is_enabled()


def test_projects_screen_shows_the_vistest_set(page, service):
    _visit(page, service, "/projects")
    _clean(page)
    card = page.locator(".pj-card").first.inner_text()
    assert "VisTest set" in card
    assert "not captured yet" not in card


def test_settings_and_doctor_render(page, service):
    for route in ("/settings", "/doctor"):
        _visit(page, service, route)
        _clean(page)
        assert page.locator("#screen").inner_text().strip()
