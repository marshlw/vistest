# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Тот же интерфейс, но со включённым входом.

Дымовой прогон рядом гоняет UI с `VISTEST_AUTH=off` — то есть в режиме, где
сервис не спрашивает никого ни о чём. Ровно поэтому он не заметил бы, что
чтение закрыли: единственный экран, который сломался бы от новой проверки, —
это экран после входа.

А сломаться там есть чему. Аутентификация стала свойством приложения, а не
отдельных маршрутов: всё под `/api/`, `/files/`, `/local/` и `/metrics` требует
роль `viewer`, и любой маршрут, который интерфейс дёргает до входа или не той
ролью, теперь отвечает 401. Такое расхождение фронта и API — ровно тот класс
дефектов, ради которого этот набор и написан.

Проверяется три вещи:

* до входа интерфейс показывает форму, а не пустой экран с ошибкой;
* после входа каждый экран отрисовывается без ошибок в консоли и без единого
  неудачного запроса к API;
* инсталляция без администратора, к которой обратились не с петли, отказывает
  внятным текстом, а не формой входа, которую невозможно пройти.
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

ADMIN, PASSWORD = "anna", "password123"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def service(tmp_path_factory):
    """Живой сервис с заведённым администратором — то есть со входом."""
    import os

    root = tmp_path_factory.mktemp("vistest-auth-root")
    saved = {k: os.environ.get(k) for k in ("VISTEST_ROOT", "VISTEST_AUTH")}
    os.environ["VISTEST_ROOT"] = str(root / ".vistest")
    os.environ.pop("VISTEST_AUTH", None)

    import numpy as np
    import uvicorn

    app, db = _rebind_service()

    from vistest.api.auth import create_user
    from vistest.config import VisTestConfig, platform_key
    from vistest.storage import BaselineRecord, FileBaselineStore

    create_user(db, ADMIN, PASSWORD, role="admin", name="Анна")

    cfg = VisTestConfig.load()
    pf = platform_key()
    store = FileBaselineStore(cfg.baselines_path(pf))
    store.save(BaselineRecord(
        name="dashboard.png", image=np.full((40, 60, 3), 200, dtype=np.uint8),
        meta={"url": "https://example.test/dashboard"}))
    # Снимок, снятый ТЕСТОМ и без всякого адреса — тот самый случай, ради
    # которого связь и заведена: страница за входом, и адрес для неё бесполезен.
    store.save(BaselineRecord(
        name="behind-login.png", image=np.full((40, 60, 3), 190, dtype=np.uint8),
        meta={"source": {"kind": "test", "test": "tests/test_app.py::test_inbox",
                         "file": "tests/test_app.py", "case": "test_inbox"}}))

    db.ingest_run({
        "run_id": "auth-smoke-1", "platform": pf, "browser": "chromium",
        "created_at": "2026-01-01T00:00:00",
        "git": {"branch": "main", "sha": "abc1234"},
        "totals": {"total": 2, "passed": 1, "failed": 1},
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
        ],
    }, "demo")

    # Второй прогон — СВЕЖЕЕ и чистый. Без него старое и новое поведение
    # счётчика совпадают, и тест на бейдж ничего не проверяет. А ловить он
    # должен ровно этот случай: последний прогон зелёный, неразобранное
    # падение лежит позади — и из бейджа оно пропадало.
    db.ingest_run({
        "run_id": "auth-smoke-2", "platform": pf, "browser": "chromium",
        "created_at": "2026-01-02T00:00:00",
        "git": {"branch": "main", "sha": "def5678"},
        "totals": {"total": 1, "passed": 1},
        "baseline_scope": "global",
        "comparisons": [
            {"name": "ok.png", "verdict": "pass",
             "metrics": {"ssim_global": 1.0, "max_severity": 0.0}},
        ],
    }, "demo")
    # `ingest_run` ставит started_at = datetime('now'), и два прогона одной
    # секунды упорядочились бы как придётся. Здесь порядок — часть сценария.
    db.execute("UPDATE run SET started_at=? WHERE run_key=?",
               ("2026-01-01T00:00:00", "auth-smoke-1"))
    db.execute("UPDATE run SET started_at=? WHERE run_key=?",
               ("2026-01-02T00:00:00", "auth-smoke-2"))

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
        pg.external_failures = []
        pg.on("console", lambda m: pg.errors.append(m.text)
              if m.type == "error" else None)
        pg.on("pageerror", lambda e: pg.errors.append(f"pageerror: {e}"))

        def _watch(response):
            if "/api/" in response.url and response.status >= 400:
                pg.bad_requests.append(f"{response.status} {response.url}")

        pg.on("response", _watch)

        def _failed(request):
            same_origin = request.url.startswith(service)
            (pg.errors if same_origin else pg.external_failures).append(
                f"requestfailed {request.url}")

        pg.on("requestfailed", _failed)
        yield pg
        browser.close()


OFFLINE = ("ERR_TUNNEL_CONNECTION_FAILED", "ERR_NAME_NOT_RESOLVED",
           "ERR_INTERNET_DISCONNECTED", "ERR_PROXY_CONNECTION_FAILED")


def _clean(page, allow: tuple[str, ...] = ()) -> None:
    allow = allow + OFFLINE
    errors = [e for e in page.errors if not any(a in e for a in allow)]
    bad = [b for b in page.bad_requests if not any(a in b for a in allow)]
    assert not errors, f"console errors: {errors}"
    assert not bad, f"failed API calls: {bad}"


def _sign_in(page, service):
    """Войти, если ещё не вошли.

    Контекст браузера один на модуль, и кука живёт между тестами — значит
    второй вызов увидит уже открытый сервис, а не форму. Идемпотентность здесь
    не удобство, а условие: иначе каждый следующий тест ждёт форму, которой не
    будет, и падает по таймауту вместо того, чтобы проверить свой экран.
    """
    page.goto(f"{service}/ui/#/runs", wait_until="networkidle")
    page.wait_for_timeout(500)
    if page.locator("#login:not(.hidden)").count():
        page.fill("#lgLogin", ADMIN)
        page.fill("#lgPass", PASSWORD)
        page.click("#loginForm button[type=submit]")
        # Именно wait_for_function, а не wait_for_selector: форма прячется
        # классом `hidden` c `display:none`, а селектор ждёт ВИДИМОСТИ — то
        # есть ждал бы вечно ровно того состояния, которое означает успех.
        page.wait_for_function(
            "() => document.querySelector('#login').classList.contains('hidden')",
            timeout=10_000)
    page.wait_for_timeout(900)


def _visit(page, service, hash_route: str):
    page.errors.clear()
    page.bad_requests.clear()
    page.goto(f"{service}/ui/#{hash_route}", wait_until="networkidle")
    page.wait_for_timeout(800)


def _open_run(page, service, key: str = "auth-smoke-1"):
    """Открыть прогон страницей.

    Список прогонов больше не master/detail: строка ведёт на `#/runs/<id>`, и
    на карточку прогона теперь приходится вся ширина, а не половина.
    """
    _visit(page, service, "/runs")
    page.locator(".run-item", has_text=key).first.click()
    page.wait_for_selector("#screen .h1.mono")
    page.wait_for_timeout(500)


# --------------------------------------------------------------------------- #
def test_a_stranger_is_shown_the_form_not_a_broken_screen(page, service):
    page.goto(f"{service}/ui/#/runs", wait_until="networkidle")
    page.wait_for_timeout(700)
    assert page.locator("#login:not(.hidden)").count() == 1
    # 401 на /api/auth/me — это и есть штатный ответ «представьтесь», а не сбой.
    assert page.locator("#blockNotice").count() == 0


def test_sign_in_opens_the_service(page, service):
    _sign_in(page, service)
    assert page.locator("#login:not(.hidden)").count() == 0
    assert page.locator(".run-item").count() >= 1


@pytest.mark.parametrize(
    "route", ["/runs", "/baselines", "/dash", "/tests", "/doctor", "/settings"])
def test_every_screen_survives_the_access_gate(page, service, route):
    """Каждый экран после входа — без ошибок консоли и без 4xx на API.

    Именно здесь ловится расхождение: маршрут, который интерфейс дёргает не той
    ролью или до входа, теперь отвечает 401, и экран остаётся недорисованным
    молча — промис падает, а место падения не видно ниоткуда.
    """
    _sign_in(page, service)
    _visit(page, service, route)
    _clean(page)
    assert page.locator("#screen").inner_text().strip()


def test_the_review_screen_still_opens_with_pictures(page, service):
    _sign_in(page, service)
    _open_run(page, service)
    page.wait_for_selector(".snap-row")
    page.locator(".snap-row").first.click()
    page.wait_for_timeout(900)
    _clean(page)
    assert page.locator(".triage .left").count() == 1


def test_signing_out_returns_to_the_form(page, service):
    _sign_in(page, service)
    # Возвращаем промис из evaluate — иначе выход не успевает дойти до сервера
    # раньше, чем навигация отменит запрос, и тест меряет не то.
    status = page.evaluate(
        "() => fetch('/api/auth/logout',{method:'POST'}).then(r => r.status)")
    assert status == 200
    page.reload(wait_until="networkidle")
    page.wait_for_timeout(900)
    assert page.locator("#login:not(.hidden)").count() == 1


# --------------------------------------------------------------------------- #
#  «Интерфейс не врёт»
# --------------------------------------------------------------------------- #
def test_the_threshold_slider_actually_saves(page, service):
    """Раньше ползунок не отправлял никуда ни одного запроса.

    Он двигался, показывал число и умирал при следующей перерисовке экрана;
    начальное значение (35) даже не совпадало с настоящим дефолтом (25). Это
    хуже отсутствия настройки: отсутствующей не пользуются, а этой пользовались
    и уходили уверенные, что настроили.
    """
    _sign_in(page, service)
    _visit(page, service, "/settings")
    page.wait_for_selector("#thresholdCard .slider")
    _clean(page)

    card = page.locator("#thresholdCard")
    assert "from vistest.yaml" in card.inner_text()

    slider = card.locator(".slider").first
    slider.evaluate(
        "el => { el.value = 42; el.dispatchEvent(new Event('input')); }")
    card.locator("button", has_text="Save").first.click()
    page.wait_for_timeout(1200)
    _clean(page)

    # Не «показалось в интерфейсе», а действительно сохранилось на сервере.
    saved = page.evaluate(
        "() => fetch('/api/settings/thresholds').then(r => r.json())")
    assert saved["values"]["fail_severity"] == 42.0
    assert saved["sources"]["fail_severity"] == "global"

    # И экран честно говорит, что значение теперь не из конфига.
    _visit(page, service, "/settings")
    page.wait_for_selector("#thresholdCard .slider")
    assert "set here, for everyone" in page.locator("#thresholdCard").inner_text()

    # Прибираем за собой, иначе следующий тест увидит чужой порог.
    page.evaluate(
        "() => fetch('/api/settings/thresholds',{method:'PUT',"
        "headers:{'Content-Type':'application/json'},"
        "body:JSON.stringify({values:{fail_severity:null,"
        "max_changed_area_pct:null}})}).then(r => r.status)")


def test_the_sidebar_badge_counts_unreviewed_failures(page, service):
    """Бейдж читается как «сколько ждёт меня».

    Стояло же там поле `failed` самого свежего прогона: последний прогон
    зелёный — бейдж пуст, сколько бы падений ни лежало неразобранными позади.
    """
    _sign_in(page, service)
    _visit(page, service, "/runs")
    # Бейдж «сколько ждёт меня» переехал на «Decisions» вместе с очередью:
    # у «Runs» теперь стоит длина истории, и это разные числа.
    badge = page.locator('#nav a[data-v="decisions"] .count')
    counts = page.evaluate(
        "() => fetch('/api/runs/counts?project=*').then(r => r.json())")
    assert counts["unreviewed"] == 1          # один fail в прогоне фикстуры
    assert badge.inner_text().strip() == "1"
    # Подсказка объясняет, что означает число: бейдж без объяснения читают
    # как «что-то сломалось» и перестают замечать через неделю.
    assert "answered" in badge.evaluate(
        "el => el.closest('a').getAttribute('title')")


def test_filter_counts_come_from_the_whole_history(page, service):
    """Считались по ЗАГРУЖЕННОЙ странице — «3» там, где триста.

    Поэтому здесь список сознательно сужается до одной записи: если счётчики
    по-прежнему считаются на клиенте, они честно покажут «All 1», и это ровно
    та ошибка, которую видел человек на длинной истории.
    """
    _sign_in(page, service)
    _visit(page, service, "/runs")
    page.evaluate("() => { state.runLimit = 1; return SCREENS.runs(); }")
    page.wait_for_timeout(800)
    try:
        assert page.locator(".run-item").count() == 1
        # Счётчик стоит отдельным элементом рядом с подписью, поэтому
        # `all_inner_texts` разделяет их переводом строки — сравниваем по словам.
        labels = [" ".join(t.split())
                  for t in page.locator(".bar button").all_inner_texts()]
        assert labels[0] == "All 2"
        assert labels[1] == "Needs decisions 1"
        assert labels[4] == "Clean 1"
    finally:
        # Восстановление в `finally`: провал этой проверки не должен уносить с
        # собой половину модуля — следующие тесты рисуют список из одной записи
        # и падают по совсем другой причине.
        page.evaluate("() => { state.runLimit = 60; state.runFilter = 'all';"
                      "         return SCREENS.runs(); }")
        page.wait_for_timeout(600)


def test_live_refresh_leaves_a_filtered_list_alone(page, service):
    """Живое обновление перерисовывало список каждые двенадцать секунд зря.

    Оно сравнивало НЕОТФИЛЬТРОВАННЫЙ свежий набор идентификаторов с тем, что
    нарисовано на экране, — а на экране отфильтрованный. Стоило включить любой
    фильтр, кроме «All», и наборы переставали совпадать навсегда: каждые
    двенадцать секунд экран пересобирался, теряя прокрутку и открытый прогон.

    Метка в DOM переживает обновление тогда и только тогда, когда пересборки
    не было.
    """
    _sign_in(page, service)
    _visit(page, service, "/runs")
    page.locator(".bar button").nth(1).click()      # «Needs decisions»
    page.wait_for_timeout(600)
    assert page.locator(".run-item").count() == 1

    page.evaluate("() => { document.querySelector('.run-item').dataset.mark = 'x'; }")
    page.evaluate("() => liveTick()")
    page.wait_for_timeout(1000)

    assert page.locator(".run-item").count() == 1
    assert page.evaluate(
        "() => document.querySelector('.run-item').dataset.mark") == "x", \
        "список пересобрали, хотя ничего не изменилось"

    page.locator(".bar button").nth(0).click()      # обратно на «All»
    page.wait_for_timeout(500)


def test_a_run_with_errors_does_not_heal_itself_on_refresh(page, service):
    """У живого обновления была вторая копия правил статуса, и она отстала.

    Про `errored` копия не знала, поэтому прогон с ошибками через двенадцать
    секунд сам собой становился «clean».
    """
    _sign_in(page, service)
    _visit(page, service, "/runs")
    # Помечаем прошедший снимок как ошибку — прогон обязан стать «1 error».
    page.evaluate("() => liveTick()")
    page.wait_for_timeout(700)
    badges = page.locator(".run-item .tag").all_inner_texts()
    assert "1 TO DECIDE" in [b.strip() for b in badges]


def test_admin_only_buttons_are_not_offered_to_a_reviewer(page, service, mainmod=None):
    """Кнопка, которую бэкенд заведомо отвергнет, — неправда в интерфейсе."""
    _sign_in(page, service)
    _visit(page, service, "/runs")
    # Администратор её видит.
    assert page.locator("button", has_text="Clear old ones").count() == 1


def test_the_run_offers_a_junit_download(page, service):
    """JUnit нужен не столько для скачивания руками, сколько чтобы его нашли.

    Без кнопки о существовании выгрузки узнают из документации, то есть никогда.
    """
    _sign_in(page, service)
    _open_run(page, service)
    _clean(page)

    btn = page.locator("#screen button", has_text="JUnit for CI")
    assert btn.count() == 1

    # И он действительно отдаётся — вместе с уже принятыми решениями.
    xml = page.evaluate(
        "() => fetch('/api/runs/1/junit.xml').then(r => r.text())")
    assert xml.startswith("<?xml")
    assert "testsuite" in xml


# --------------------------------------------------------------------------- #
#  Страница снимка
# --------------------------------------------------------------------------- #
def _snapshot_url(service, name="dashboard.png"):
    import urllib.parse as up
    pf = up.quote("linux-chromium-1x", safe="")
    return f"{service}/ui/#/snapshot/global/{pf}/{up.quote(name, safe='')}"


def _open_snapshot(page, service, name="dashboard.png"):
    _sign_in(page, service)
    page.errors.clear()
    page.bad_requests.clear()
    # Платформа фикстуры вычисляется на бэкенде — берём её из карточки эталона,
    # а не угадываем: на другой машине ключ был бы другим.
    platform = page.evaluate(
        "() => fetch('/api/baselines/scopes').then(r => r.json())"
        "  .then(d => d.current_platform)")
    import urllib.parse as up
    page.goto(f"{service}/ui/#/snapshot/global/{up.quote(platform, safe='')}"
              f"/{up.quote(name, safe='')}", wait_until="networkidle")
    page.wait_for_timeout(1000)
    return platform


def test_the_snapshot_page_gathers_everything_in_one_place(page, service):
    """Снимок был строкой-ключом, размазанной по трём экранам."""
    _open_snapshot(page, service)
    _clean(page)
    # `.rail-h` в стилях — uppercase, а inner_text() отдаёт то, что видно.
    text = page.locator("#screen").inner_text().lower()
    assert "dashboard.png" in text
    assert "how this snapshot is taken" in text
    assert "ignore zones" in text
    assert "checks of this snapshot" in text


def test_a_snapshot_can_be_reached_from_the_baselines_tab(page, service):
    _sign_in(page, service)
    _visit(page, service, "/baselines")
    page.wait_for_selector(".snap-card .nm")
    page.locator(".snap-card .nm").first.click()
    page.wait_for_timeout(1000)
    _clean(page)
    assert "#/snapshot/" in page.url


def test_the_spec_editor_saves_more_than_the_url(page, service):
    """Меню предлагало «Edit the spec», а правился один URL через prompt()."""
    platform = _open_snapshot(page, service)
    page.wait_for_selector("#screen textarea.inp")

    inputs = page.locator("#screen input.inp")
    inputs.nth(1).fill("1280x800")          # Window
    inputs.nth(2).fill(".dashboard-main")   # Selector
    inputs.nth(3).fill("250")               # Pause
    page.locator("#screen button", has_text="Save spec").first.click()
    page.wait_for_timeout(1200)
    _clean(page)

    import urllib.parse as up
    saved = page.evaluate(
        "() => fetch('/api/baselines/card?platform="
        + up.quote(platform, safe="") + "&name=dashboard.png')"
        "  .then(r => r.json())")
    assert saved["spec"]["viewport"] == "1280x800"
    assert saved["spec"]["selector"] == ".dashboard-main"
    assert saved["spec"]["wait"] == 250


def test_an_ignore_zone_is_drawn_with_the_mouse_and_saved(page, service):
    """Кнопка называлась «Ignore zone», а брала `regions[0]`.

    То есть «игнорируй то, что движок нашёл первым», — тогда как человек в этот
    момент хочет выделить область.
    """
    platform = _open_snapshot(page, service)
    page.wait_for_selector("#screen img[src*='/api/baselines/image']")
    page.wait_for_timeout(600)

    img = page.locator("#screen img[src*='/api/baselines/image']").first
    box = img.bounding_box()
    assert box and box["width"] > 20

    page.mouse.move(box["x"] + 5, box["y"] + 5)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] * 0.6,
                    box["y"] + box["height"] * 0.6, steps=8)
    page.mouse.up()
    page.wait_for_timeout(400)

    assert "not saved" in page.locator("#screen").inner_text()
    page.locator("#screen button", has_text="Save zones").first.click()
    page.wait_for_timeout(1000)
    _clean(page)

    import urllib.parse as up
    saved = page.evaluate(
        "() => fetch('/api/baselines/card?platform="
        + up.quote(platform, safe="") + "&name=dashboard.png')"
        "  .then(r => r.json())")
    assert len(saved["ignore_boxes"]) == 1
    zone = saved["ignore_boxes"][0]
    assert zone["w"] > 0 and zone["h"] > 0


def test_a_stray_click_does_not_create_an_empty_zone(page, service):
    """Клик без протяжки — это промах, а не зона нулевого размера."""
    _open_snapshot(page, service)
    page.wait_for_selector("#screen img[src*='/api/baselines/image']")
    page.wait_for_timeout(600)
    before = page.locator("#screen [style*='rgba(216, 63, 38']").count()

    img = page.locator("#screen img[src*='/api/baselines/image']").first
    box = img.bounding_box()
    page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.wait_for_timeout(400)

    assert page.locator("#screen [style*='rgba(216, 63, 38']").count() == before


def test_the_review_screen_links_to_the_snapshot_page(page, service):
    """Выйти из разбора падения к самому снимку было нельзя вообще."""
    _sign_in(page, service)
    # `dashboard.png` лежит в первом прогоне — выбираем его явно, иначе тест
    # зависит от того, какой прогон новее.
    _open_run(page, service, "auth-smoke-1")
    page.wait_for_selector(".snap-row")
    # Именно упавший снимок: у него есть эталон в этом хранилище. У `ok.png`
    # в фикстуре только строка в истории, файла под ним нет.
    page.locator(".snap-row", has_text="dashboard.png").first.click()
    page.wait_for_timeout(1000)

    link = page.locator(".side button", has_text="Snapshot page")
    assert link.count() == 1
    link.click()
    page.wait_for_timeout(1000)
    _clean(page)
    assert "#/snapshot/" in page.url


# --------------------------------------------------------------------------- #
#  Эталон на ветку
# --------------------------------------------------------------------------- #
def test_branch_baselines_are_off_until_switched_on(page, service):
    """Команде с одной веткой режим не даёт ничего, а вопросов добавляет."""
    _sign_in(page, service)
    _visit(page, service, "/settings")
    page.wait_for_selector("#branchCard")
    _clean(page)

    card = page.locator("#branchCard")
    assert "overlay" in card.inner_text()
    assert card.locator("input[type=checkbox]").first.is_checked() is False


def test_the_mode_and_the_base_branch_are_saved(page, service):
    _sign_in(page, service)
    _visit(page, service, "/settings")
    page.wait_for_selector("#branchCard input[type=checkbox]")

    card = page.locator("#branchCard")
    card.locator("input[type=checkbox]").first.check()
    card.locator("input.inp").first.fill("trunk")
    card.locator("button", has_text="Save").first.click()
    page.wait_for_timeout(1200)
    _clean(page)

    saved = page.evaluate(
        "() => fetch('/api/settings/branches').then(r => r.json())")
    assert saved == {"enabled": True, "default_branch": "trunk"}

    # Прибираем: следующий тест не должен зависеть от этого.
    page.evaluate(
        "() => fetch('/api/settings/branches',{method:'PUT',"
        "headers:{'Content-Type':'application/json'},"
        "body:JSON.stringify({enabled:false,default_branch:'main'})})"
        "  .then(r => r.status)")


def test_the_decision_panel_names_the_branch_it_writes_to(page, service):
    """«Собственный набор сервиса» и запись в наложение ветки — снова обещание,
    которое нельзя проверить."""
    _sign_in(page, service)
    page.evaluate(
        "() => fetch('/api/settings/branches',{method:'PUT',"
        "headers:{'Content-Type':'application/json'},"
        "body:JSON.stringify({enabled:true,default_branch:'trunk'})})"
        "  .then(r => r.status)")
    try:
        _open_run(page, service, "auth-smoke-1")
        page.locator(".snap-row", has_text="dashboard.png").first.click()
        page.wait_for_timeout(1100)
        _clean(page)

        # Куда уедет решение — отдельная панель правой колонки.
        where = page.locator(".side", has_text="WHERE IT LANDS").inner_text()
        assert "branch" in where
        assert "main" in where             # ветка прогона из фикстуры
        assert "inherited" in where        # снимок ещё не переопределён веткой
    finally:
        page.evaluate(
            "() => fetch('/api/settings/branches',{method:'PUT',"
            "headers:{'Content-Type':'application/json'},"
            "body:JSON.stringify({enabled:false,default_branch:'main'})})"
            "  .then(r => r.status)")


# --------------------------------------------------------------------------- #
#  Ссылка на прогон
#
#  Комментарий в PR/MR ссылается на конкретный прогон. До этого ссылаться было
#  не на что: адрес был один на весь список, а список показывает последние
#  шестьдесят — то есть назавтра ссылка из позавчерашнего MR открывала чужой
#  прогон, выглядящий как свой.
# --------------------------------------------------------------------------- #
def test_a_run_has_its_own_address(page, service):
    """`#/runs/1` открывает первый прогон, а не последний.

    Проверять надо именно не-последний: на последнем старое поведение (открыть
    верхний в списке) и новое совпадают, и тест ничего не ловит.
    """
    _sign_in(page, service)
    _visit(page, service, "/runs/1")
    page.wait_for_selector("#screen .h1.mono")
    assert "auth-smoke-1" in page.text_content("#screen .h1.mono")
    _clean(page)


def test_the_plain_run_list_is_a_list(page, service):
    """Адрес без номера показывает историю, а не карточку прогона.

    Раньше список и карточка делили экран пополам, и `#/runs` молча открывал
    верхний прогон. Это было удобно ровно один раз — при первом заходе; дальше
    половина ширины уходила на список, в котором уже не было нужды, а карточке
    её не хватало. Теперь список отвечает на «когда и что», а прогон
    открывается страницей.
    """
    _sign_in(page, service)
    _visit(page, service, "/runs")
    page.wait_for_selector(".run-item")
    assert page.locator(".run-item").count() >= 2
    # Ни один прогон не открыт: заголовка страницы прогона на экране нет.
    assert page.locator("#screen .h1.mono").count() == 0


def test_a_link_to_a_deleted_run_explains_itself(page, service):
    """Пустая панель — единственное, что видел пришедший по устаревшей ссылке.

    Ни ошибки, ни объяснения: прогон удалили или его унесла чистка истории, а
    интерфейс выглядел сломанным.
    """
    _sign_in(page, service)
    _visit(page, service, "/runs/9999")
    page.wait_for_selector("#screen .panel")
    text = page.text_content("#screen")
    assert "9999" in text
    assert "not here" in text
    # 404 здесь — штатный ответ «такого прогона нет», а не сбой интерфейса;
    # браузер всё равно пишет о нём в консоль.
    _clean(page, allow=("/api/runs/9999", "404 (Not Found)"))


# --------------------------------------------------------------------------- #
#  Уведомления
# --------------------------------------------------------------------------- #
def test_notifications_are_off_until_a_webhook_is_given(page, service):
    """По умолчанию сервис наружу не ходит вовсе."""
    _sign_in(page, service)
    _visit(page, service, "/settings")
    page.wait_for_selector("#notifyCard")
    _clean(page)

    card = page.locator("#notifyCard")
    assert "webhook" in card.inner_text().lower()
    assert card.locator("input[type=checkbox]").first.is_checked() is False


def test_the_webhook_is_saved_from_the_form(page, service):
    _sign_in(page, service)
    _visit(page, service, "/settings")
    page.wait_for_selector("#notifyCard input[type=checkbox]")

    card = page.locator("#notifyCard")
    card.locator("input[type=checkbox]").first.check()
    card.locator("input.inp").first.fill("https://chat.example/hook")
    card.locator("button", has_text="Save").first.click()
    page.wait_for_timeout(1200)
    _clean(page)

    saved = page.evaluate(
        "() => fetch('/api/settings/notifications').then(r => r.json())")
    assert saved["enabled"] is True
    assert saved["webhook"] == "https://chat.example/hook"

    page.evaluate(
        "() => fetch('/api/settings/notifications',{method:'PUT',"
        "headers:{'Content-Type':'application/json'},"
        "body:JSON.stringify({enabled:false,webhook:''})}).then(r => r.status)")


def test_a_broken_webhook_is_shown_and_not_hidden(page, service):
    """Тихо сломавшийся вебхук хуже отсутствующего.

    Канал молчит, и тишина читается как «всё разобрано» — поэтому причина
    последней неудачи видна прямо в настройках.
    """
    _sign_in(page, service)
    page.evaluate(
        "() => fetch('/api/settings/notifications',{method:'PUT',"
        "headers:{'Content-Type':'application/json'},"
        "body:JSON.stringify({enabled:true,after_hours:0,"
        "webhook:'http://127.0.0.1:9/nope'})}).then(r => r.status)")
    # Порт 9 — discard: соединение не встанет, и это ровно тот случай.
    # `after_hours: 0` — иначе разбирать «нечего» и отправки не будет вовсе.
    result = page.evaluate(
        "() => fetch('/api/settings/notifications/test',{method:'POST'})"
        "  .then(r => r.json())")
    assert result["sent"] is False and result["error"]

    _visit(page, service, "/settings")
    page.wait_for_selector("#notifyCard")
    text = page.locator("#notifyCard").inner_text()
    assert "The last attempt failed" in text

    page.evaluate(
        "() => fetch('/api/settings/notifications',{method:'PUT',"
        "headers:{'Content-Type':'application/json'},"
        "body:JSON.stringify({enabled:false,webhook:''})}).then(r => r.status)")


# --------------------------------------------------------------------------- #
#  Доступность и адаптив
#
#  Половина навигации была кликабельными <div>: с клавиатуры недостижимы, для
#  скринридера невидимы. Ни одного `@media`, сетка жёстко `248px 1fr`.
# --------------------------------------------------------------------------- #
def test_a_run_can_be_opened_from_the_keyboard(page, service):
    """Список прогонов проходился только мышью.

    Tab перепрыгивал его целиком, потому что попасть в `<div>` было некуда.
    """
    _sign_in(page, service)
    _visit(page, service, "/runs")
    page.wait_for_selector(".run-item")

    item = page.locator('.run-item').first
    assert item.get_attribute("tabindex") == "0"
    item.focus()
    page.keyboard.press("Enter")
    page.wait_for_selector("#screen .h1.mono")
    assert "auth-smoke" in page.text_content("#screen .h1.mono")
    _clean(page)


def test_a_snapshot_row_answers_the_space_bar(page, service):
    """Пробел на кнопке — второй способ нажать, и его ждут ровно так же."""
    _sign_in(page, service)
    _open_run(page, service)
    page.wait_for_selector(".snap-row")

    row = page.locator(".snap-row[role=button]").first
    row.focus()
    page.keyboard.press(" ")
    page.wait_for_timeout(700)
    assert "#/compare/" in page.url


def test_the_space_bar_does_not_scroll_instead_of_pressing(page, service):
    """Пробел прокручивает контейнер — ровно то, чего от нажатия не ждут.

    Прокручивается здесь `#screen`, а не окно: `#app` держит `overflow:hidden`.
    Мерить `window.scrollY` значило бы мерить то, что не двигается никогда, —
    такой тест зелёный и без всякого `preventDefault`.
    """
    _sign_in(page, service)
    _visit(page, service, "/runs")
    page.wait_for_selector(".run-item[role=button]")
    # Списку нужно быть длиннее экрана, иначе прокручивать нечего и проверять
    # тоже нечего.
    page.evaluate(
        "() => { const b = document.createElement('div');"
        "  b.style.height = '3000px'; document.querySelector('#screen').append(b); }")
    page.locator(".run-item[role=button]").first.focus()
    before = page.evaluate("() => document.querySelector('#screen').scrollTop")
    page.keyboard.press(" ")
    page.wait_for_timeout(400)
    assert page.evaluate("() => document.querySelector('#screen').scrollTop") == before


def test_the_navigation_says_which_page_you_are_on(page, service):
    """Подсветка говорит «вы здесь» глазами; скринридеру не говорил никто."""
    _sign_in(page, service)
    _visit(page, service, "/baselines")
    page.wait_for_timeout(600)
    current = page.locator('#nav a[aria-current="page"]')
    assert current.count() == 1
    assert current.first.get_attribute("data-v") == "baselines"


def test_the_navigation_itself_is_reachable_by_tab(page, service):
    """`<a>` без href фокус не принимает — вся навигация была вне Tab."""
    _sign_in(page, service)
    _visit(page, service, "/runs")
    hrefs = page.eval_on_selector_all(
        "#nav a", "els => els.map(e => e.getAttribute('href'))")
    assert hrefs and all(h and h.startswith("#/") for h in hrefs)


def test_skip_to_content_moves_focus_without_changing_the_screen(page, service):
    """Ссылка `href="#screen"` увела бы на «Runs»: адрес экрана — это роут."""
    _sign_in(page, service)
    _visit(page, service, "/baselines")
    page.wait_for_timeout(600)
    page.evaluate("() => document.querySelector('#skipLink').click()")
    page.wait_for_timeout(300)
    assert page.evaluate("() => document.activeElement.id") == "screen"
    assert "#/baselines" in page.url


def test_the_toast_is_announced_not_only_drawn(page, service):
    """Тост — единственный ответ на действие. Без aria-live его просто нет."""
    _sign_in(page, service)
    _visit(page, service, "/runs")
    box = page.locator("#toast")
    assert box.get_attribute("role") == "status"
    assert box.get_attribute("aria-live") == "polite"


def test_the_layout_survives_a_narrow_screen(page, service):
    """Сетка была жёстко `248px 1fr` без единого @media.

    Проверять переполнение документа тут бесполезно: `#app` держит
    `overflow:hidden`, и на узком экране правая колонка не «уезжала за край», а
    молча **обрезалась** — то есть тест на `scrollWidth` был бы зелёным и до
    единой правки. Смотрим на то, что видно: во что разложилась сетка и
    помещается ли содержимое в окно.

    Боковая панель на узком экране не исчезает, а сжимается до полосы значков:
    убрать навигацию целиком значило бы, что с телефона по интерфейсу нельзя
    ходить вовсе.
    """
    _sign_in(page, service)
    _visit(page, service, "/runs")
    page.set_viewport_size({"width": 600, "height": 900})
    page.wait_for_timeout(600)
    try:
        columns = page.evaluate(
            "() => getComputedStyle(document.querySelector('#app'))"
            "        .gridTemplateColumns")
        rail = float(columns.split()[0].replace("px", ""))
        assert rail <= 80, f"боковая панель не сжалась: {columns}"

        box = page.locator("#screen .page").bounding_box()
        assert box and box["width"] > 200, "страница схлопнулась"
        assert box["x"] + box["width"] <= 601, "страница не влезает в окно"
    finally:
        page.set_viewport_size({"width": 1440, "height": 900})
        page.wait_for_timeout(300)


# --------------------------------------------------------------------------- #
#  Автоматическая уборка
# --------------------------------------------------------------------------- #
def test_automatic_cleanup_is_off_and_says_what_it_will_not_touch(page, service):
    """Единственное фоновое действие, которое удаляет данные."""
    _sign_in(page, service)
    _visit(page, service, "/settings")
    page.wait_for_selector("#retentionCard")
    _clean(page)

    text = page.locator("#retentionCard").inner_text()
    assert "unreviewed failure is never deleted" in text
    assert "Baselines are not touched" in text
    assert page.locator("#retentionCard input[type=checkbox]").first \
               .is_checked() is False


def test_the_preview_answers_before_deleting_anything(page, service):
    """Включить автоудаление вслепую — узнать, что оно означало, назавтра."""
    _sign_in(page, service)
    _visit(page, service, "/settings")
    page.wait_for_selector("#retentionCard button")

    card = page.locator("#retentionCard")
    card.locator("input.inp").first.fill("1")           # «старше одного дня»
    card.locator("button", has_text="Show what would go").click()
    page.wait_for_timeout(1200)
    text = card.inner_text()
    # В фикстуре все кандидаты держатся неразобранным падением — и панель
    # обязана назвать причину. «Ничего не уйдёт» без неё читается как «политика
    # не работает», и следующим действием человек выкручивает срок в единицу.
    assert "Nothing would be deleted" in text
    assert "still unreviewed" in text

    # Прогоны фикстуры на месте: это был предпросмотр, а не уборка.
    runs = page.evaluate(
        "() => fetch('/api/runs?project=*').then(r => r.json()).then(x => x.length)")
    assert runs >= 2
    _clean(page)


def test_the_preview_explains_what_it_spared(page, service):
    """«Ничего не уйдёт» без причины читается как «политика не работает»."""
    _sign_in(page, service)
    page.evaluate(
        "() => fetch('/api/settings/retention',{method:'PUT',"
        "headers:{'Content-Type':'application/json'},"
        "body:JSON.stringify({days:1,keep_last:1,max_gb:0})}).then(r => r.status)")
    body = page.evaluate(
        "() => fetch('/api/settings/retention/preview',{method:'POST'})"
        "  .then(r => r.json())")
    # В фикстуре есть непросмотренное падение — политика обязана его назвать.
    assert body["kept_unreviewed"] >= 1
    assert body["dry_run"] is True


# --------------------------------------------------------------------------- #
#  Чем снят снимок
#
#  «Check» и «Re-capture» появлялись, как только у снимка был адрес, и грузили
#  этот адрес. Для страницы за входом это означало снять форму логина.
# --------------------------------------------------------------------------- #
def _cards(page):
    return {page.locator(".snap-card .sn").nth(i).inner_text().strip():
            page.locator(".snap-card").nth(i)
            for i in range(page.locator(".snap-card").count())}


def test_a_snapshot_says_what_captured_it(page, service):
    _sign_in(page, service)
    _visit(page, service, "/baselines")
    page.wait_for_selector(".snap-card")
    text = page.locator(".snap-card", has_text="behind-login.png").inner_text()
    assert "tests/test_app.py::test_inbox" in text
    _clean(page)


def test_a_snapshot_without_a_url_is_still_runnable_through_its_test(page, service):
    """Раньше у такого снимка кнопки были мертвы: адреса нет — значит нечего."""
    _sign_in(page, service)
    _visit(page, service, "/baselines")
    page.wait_for_selector(".snap-card")
    card = page.locator(".snap-card", has_text="behind-login.png")
    check = card.locator("button", has_text="Check").first
    assert check.is_disabled() is False
    assert "test" in (check.get_attribute("title") or "")


def test_the_button_says_which_way_it_will_go(page, service):
    """«Проверить» через тест и «проверить» по адресу — разные действия.

    Одна и та же подпись у обоих означала бы, что человек узнаёт разницу только
    по результату — то есть по картинке формы входа в эталоне.
    """
    _sign_in(page, service)
    _visit(page, service, "/baselines")
    page.wait_for_selector(".snap-card")
    by_url = page.locator(".snap-card", has_text="dashboard.png") \
                 .locator("button", has_text="Check").first
    assert "address" in (by_url.get_attribute("title") or "")


def test_the_snapshot_page_links_to_its_test(page, service):
    """Назвать тест и не дать его открыть — половина ответа."""
    import urllib.parse as up

    _sign_in(page, service)
    platform = page.evaluate(
        "() => fetch('/api/baselines/scopes').then(r => r.json())"
        "  .then(d => d.current_platform)")
    page.goto(f"{service}/ui/#/snapshot/global/{up.quote(platform, safe='')}"
              f"/{up.quote('behind-login.png', safe='')}",
              wait_until="networkidle")
    page.wait_for_timeout(900)

    link = page.locator("#snapSource a")
    assert link.count() == 1
    assert link.first.inner_text().strip() == "tests/test_app.py::test_inbox"
    assert "#/tests/" in (link.first.get_attribute("href") or "")


# --------------------------------------------------------------------------- #
#  Порог на отдельный снимок
#
#  Двух уровней не хватало: один шумный дашборд заставлял ослаблять порог для
#  всего набора — чинить один снимок ценой чувствительности всех остальных.
# --------------------------------------------------------------------------- #
def test_a_snapshot_shows_where_its_thresholds_come_from(page, service):
    """Число без источника не отвечает на «это я тут выставил или так везде»."""
    _open_snapshot(page, service)
    text = page.text_content("#screen")
    assert "Thresholds for this snapshot" in text
    # Обе ручки, а не одна: третий уровень должен управлять тем же, чем первые
    # два, иначе это не третий уровень, а отдельная система с теми же словами.
    assert "Fail at severity" in text
    assert "Changed area" in text
    assert text.count("from the config") >= 2
    _clean(page)


def test_an_empty_threshold_field_means_inherited(page, service):
    """Подставить в поле унаследованное число было бы удобнее на вид и хуже.

    Тогда «здесь ничего не задано» и «здесь задано ровно то же» выглядят
    одинаково — а второе не поедет за общей настройкой, когда её изменят.
    """
    _open_snapshot(page, service)
    field = page.locator('#screen input[type=number]').first
    assert field.input_value() == ""
    # JS печатает 25.0 как «25» — это его формат числа, не наш.
    assert field.get_attribute("placeholder") == "25"


def test_a_threshold_set_here_is_saved_and_marked(page, service):
    _open_snapshot(page, service)
    page.locator('#screen input[type=number]').first.fill("60")
    page.locator("#screen button", has_text="Save spec").click()
    page.wait_for_timeout(1400)

    text = page.text_content("#screen")
    assert "set here" in text
    assert "the set says 25" in text, "унаследованное значение видно рядом"
    _clean(page)

    # Прибираем: следующие тесты не должны зависеть от этого.
    page.locator('#screen input[type=number]').first.fill("")
    page.locator("#screen button", has_text="Save spec").click()
    page.wait_for_timeout(1200)


# --------------------------------------------------------------------------- #
#  Что изменилось между двумя прогонами
#
#  Прогон отвечает на «что красное сейчас». Перед мержем спрашивают другое —
#  «что сломала эта ветка», — и до сих пор ответ добывался глазами по двум
#  вкладкам. В этой инсталляции два прогона: в первом `dashboard.png` красный,
#  во втором его нет вовсе — то есть ровно тот случай, который не видно ни в
#  одном списке падений.
# --------------------------------------------------------------------------- #
def _run_ids(page, service):
    """Идентификаторы прогонов, от старого к новому."""
    rows = page.evaluate(
        "() => fetch('/api/runs?project=*&limit=50').then(r => r.json())"
        "  .then(d => d.map(r => [r.id, r.run_key]))")
    ids = {key: rid for rid, key in rows}
    return ids["auth-smoke-1"], ids["auth-smoke-2"]


def test_the_run_card_offers_the_comparison(page, service):
    """Без кнопки о разборе узнают из документации, то есть никогда."""
    _sign_in(page, service)
    _open_run(page, service)
    assert page.locator("#screen button", has_text="What changed").count() == 1
    _clean(page)


def test_the_comparison_falls_back_to_the_previous_run(page, service):
    _sign_in(page, service)
    old, new = _run_ids(page, service)
    _visit(page, service, f"/diff/{new}")
    text = page.text_content("#screen")
    assert "auth-smoke-1" in text, "база подставлена сама"
    assert "auth-smoke-2" in text
    _clean(page)


def test_a_snapshot_that_stopped_being_checked_is_shown(page, service):
    """Самое тихое из всего, что здесь показывается.

    `dashboard.png` был красным в первом прогоне и во втором отсутствует. Ни
    один список падений его не покажет: падать нечему. «Во втором прогоне всё
    чисто» при этом — правда, которая ничего не значит.
    """
    _sign_in(page, service)
    old, new = _run_ids(page, service)
    _visit(page, service, f"/diff/{new}/{old}")
    text = page.text_content("#screen")
    assert "No longer checked" in text
    assert "dashboard.png" in text
    _clean(page)


def test_the_comparison_is_addressable_by_a_link(page, service):
    """Ссылку на разбор кладут в MR — значит адрес обязан её пережить."""
    _sign_in(page, service)
    old, new = _run_ids(page, service)
    page.goto(f"{service}/ui/#/diff/{new}/{old}", wait_until="networkidle")
    page.wait_for_timeout(900)
    assert page.locator(".dchip").count() == 2
    assert page.text_content("#crumb").strip() == "runs / what changed"


def test_the_base_can_be_switched_without_leaving(page, service):
    """Иначе «сравнить не с предыдущим, а с мастером» — это уйти в список,
    найти номер глазами и собрать адрес руками, то есть не сделать.
    """
    _sign_in(page, service)
    old, new = _run_ids(page, service)
    _visit(page, service, f"/diff/{new}")
    options = page.locator("#screen select option")
    assert options.count() >= 2, "в выборе базы есть и прогоны, а не только «предыдущий»"
    _clean(page)


def test_nothing_to_compare_with_explains_itself(page, service):
    """Пустой экран здесь читается как поломка интерфейса, а это не она."""
    _sign_in(page, service)
    old, _new = _run_ids(page, service)
    _visit(page, service, f"/diff/{old}")
    text = page.text_content("#screen")
    assert "Nothing to compare" in text
    assert "no earlier run" in text


def test_the_chosen_base_is_the_one_used(page, service):
    """Иначе выбор базы — это селектор, который ничего не делает.

    Сравнение берётся нарочно наоборот: у первого прогона предыдущего нет
    вовсе, поэтому если адресный `base` не доедет до запроса, экран ответит
    «сравнивать не с чем» вместо разбора. Заодно проверяется, что перевёрнутое
    сравнение не запрещается, а объясняется: список в нём читается наоборот, и
    молчать об этом нельзя.
    """
    _sign_in(page, service)
    old, new = _run_ids(page, service)
    _visit(page, service, f"/diff/{old}/{new}")
    text = page.text_content("#screen")
    assert "Nothing to compare" not in text
    assert "auth-smoke-2" in text
    assert "backwards" in text
    _clean(page)


# --------------------------------------------------------------------------- #
#  Права по проектам
#
#  Роль была одна на инсталляцию, и `reviewer` означало «может переписать
#  эталон любого набора». Утверждение необратимо, а на экране свой набор от
#  чужого ничем не отличается — поэтому интерфейс обязан считать роль так же,
#  как сервер: максимум из глобальной и выданной на проект.
# --------------------------------------------------------------------------- #
def _with_grants(page, grants: dict, role: str = "viewer"):
    """Посчитать права так, как их считает интерфейс, для подставленного «я»."""
    return page.evaluate(
        """([grants, role]) => {
             const saved = state.me;
             state.me = {login:'x', role, grants, own_project:'demo'};
             const out = {
               own_project:   can('reviewer', 'demo'),
               granted:       can('reviewer', 'shop'),
               other:         can('reviewer', 'billing'),
               global_scope:  can('reviewer', scopeProject('')),
               project_scope: can('reviewer', scopeProject('project:shop')),
               above_grant:   can('admin', 'shop'),
             };
             state.me = saved;
             return out;
           }""", [grants, role])


def test_the_screen_counts_rights_the_way_the_server_does(page, service):
    """Иначе интерфейс гасит кнопки, которые на самом деле работают.

    Право выдано на один проект — значит в нём человек `reviewer`, а рядом
    по-прежнему `viewer`. Экран, который знает только глобальную роль, покажет
    ему «только чтение» там, где сервер пустит, и человек решит, что права не
    выдали.
    """
    _sign_in(page, service)
    _visit(page, service, "/runs")
    got = _with_grants(page, {"shop": "reviewer"})

    assert got["granted"] is True
    assert got["other"] is False
    assert got["project_scope"] is True, "scope=project:shop — это проект shop"
    assert got["above_grant"] is False, "право не выдаёт больше, чем в нём есть"


def test_the_services_own_set_is_a_project_on_the_screen_too(page, service):
    """`scope=global` — это набор собственного проекта сервиса.

    Не знай интерфейс его имени, «свой набор» стал бы единственным местом, где
    выданное право не действует, — а в большинстве инсталляций основные эталоны
    лежат именно там.
    """
    _sign_in(page, service)
    _visit(page, service, "/runs")
    got = _with_grants(page, {"demo": "reviewer"})
    assert got["global_scope"] is True
    assert got["own_project"] is True


def test_a_grant_never_lowers_on_the_screen_either(page, service):
    """Глобальный reviewer не понижается выданным viewer.

    Расхождение с сервером в эту сторону хуже любого другого: экран прячет
    кнопку, сервер бы пустил, и человек уверен, что у него отняли доступ.
    """
    _sign_in(page, service)
    _visit(page, service, "/runs")
    got = _with_grants(page, {"shop": "viewer"}, role="reviewer")
    assert got["granted"] is True
    assert got["other"] is True


def test_a_snapshot_of_another_project_offers_no_decision(page, service):
    """Кнопка, которую сервер заведомо отвергнет, — неправда на экране.

    Раньше «Принять как эталон» показывалась всем, и человек без прав узнавал
    об этом из голого 403 после нажатия.
    """
    _sign_in(page, service)
    _visit(page, service, "/runs")
    text = page.evaluate(
        """() => {
             const saved = state.me;
             state.me = {login:'x', role:'viewer',
                         grants:{shop:'reviewer'}, own_project:'demo'};
             const card = buildDecision({id: 1, project: 'billing'});
             state.me = saved;
             return card.textContent;
           }""")
    assert "Accept as baseline" not in text
    assert "billing" in text, "сказано, в каком проекте не хватает роли"
    assert "Team" in text, "и где это выдаётся"


def test_the_same_snapshot_in_your_own_project_still_decides(page, service):
    """Обратная половина: без неё проверка выше проходила бы и у того, кто
    просто спрятал кнопки от всех.
    """
    _sign_in(page, service)
    _visit(page, service, "/runs")
    text = page.evaluate(
        """() => {
             const saved = state.me;
             state.me = {login:'x', role:'viewer',
                         grants:{shop:'reviewer'}, own_project:'demo'};
             const card = buildDecision({id: 1, project: 'shop'});
             state.me = saved;
             return card.textContent;
           }""")
    assert "Accept as baseline" in text


def test_rights_are_granted_and_revoked_from_the_team_tab(page, service):
    """Право, которое можно выдать только запросом к API, — не выданное право."""
    _sign_in(page, service)
    page.evaluate(
        """() => fetch('/api/users', {method:'POST',
             headers:{'Content-Type':'application/json'},
             body: JSON.stringify({login:'vic', password:'password123',
                                   role:'viewer'})}).then(r => r.status)""")
    _visit(page, service, "/settings")
    page.wait_for_timeout(900)

    row = page.locator("button", has_text="+ right")
    assert row.count() >= 1, "у каждого человека есть чем выдать право"

    granted = page.evaluate(
        """() => fetch('/api/users/vic/rights/demo', {method:'PUT',
             headers:{'Content-Type':'application/json'},
             body: JSON.stringify({role:'reviewer'})}).then(r => r.json())""")
    assert granted["role"] == "reviewer"

    # Не `_visit`: адрес уже `#/settings`, и переход на тот же хеш ничего не
    # перерисовывает — экран остался бы прежним, а тест «проверил» бы старый DOM.
    page.errors.clear()
    page.bad_requests.clear()
    page.reload(wait_until="networkidle")
    page.wait_for_timeout(1200)

    chips = page.locator(".grant").all_inner_texts()
    assert any("demo" in c and "reviewer" in c for c in chips), chips
    _clean(page)

    # И обратно: право снимается той же строкой, а не только запросом.
    page.locator(".grant button").first.click()
    page.wait_for_timeout(1200)
    left = page.evaluate(
        "() => fetch('/api/users').then(r => r.json())"
        "  .then(d => d.users.filter(u => u.login === 'vic')[0].grants)")
    assert left == {}


# --------------------------------------------------------------------------- #
#  Очередь решений
#
#  Экран прогона отвечает на «что сломалось здесь». Человек, открывающий VisTest
#  утром, спрашивает «что от меня требуется», и это не список из двадцати трёх
#  картинок. Очередь строится по проекту и по причинам — одна правка
#  line-height даёт один вопрос, а не одиннадцать.
# --------------------------------------------------------------------------- #
def test_the_queue_is_where_the_interface_opens(page, service):
    """Первый экран отвечает на первый вопрос.

    Раньше это был список прогонов — то есть история. История отвечает на
    «что было», а с утра спрашивают «что делать».
    """
    _sign_in(page, service)
    # Пустой адрес — то, что видит человек, открывший сервис по голой ссылке.
    page.evaluate("() => { location.hash = ''; }")
    page.wait_for_timeout(1200)
    assert page.text_content("#crumb").strip() == "decisions"
    assert page.locator('#nav a[data-v="decisions"].active').count() == 1


def test_the_queue_counts_add_up(page, service):
    """«Чисто 117 из 142» рядом с красными обязано сходиться.

    Два разных правила счёта на одном экране дают строку, которую нечем
    объяснить, — и человек перестаёт верить всем числам сразу, а не одному.
    """
    _sign_in(page, service)
    _visit(page, service, "/decisions")
    page.wait_for_selector("#screen .strip")

    got = page.evaluate(
        "() => fetch('/api/decisions?project=' + encodeURIComponent(state.project))"
        "  .then(r => r.json()).then(d => d.counts)")
    assert got["snapshots"] + got["blocked"] + got["clean"] == got["total"]
    text = page.text_content("#screen")
    assert "TO DECIDE" in text and "BLOCKED" in text and "CLEAN" in text
    _clean(page)


def test_a_cause_says_how_many_snapshots_one_answer_closes(page, service):
    """Без этого числа «принять» — это кнопка с неизвестными последствиями."""
    _sign_in(page, service)
    _visit(page, service, "/decisions")
    page.wait_for_selector(".cause")
    card = page.locator(".cause").first.inner_text()
    assert "ANSWER ONCE" in card
    assert "SNAPSHOT" in card
    assert "Accept as the new normal" in card
    _clean(page)


def test_triage_opens_from_the_queue_with_the_queue_in_view(page, service):
    """Разбор подряд: человек не возвращается в список после каждого ответа.

    Полоса очереди наверху отвечает на «сколько ещё» — тот, кто видит, что
    осталось три, дорабатывает до конца.
    """
    _sign_in(page, service)
    _visit(page, service, "/decisions")
    page.wait_for_selector(".cause")
    page.locator("button", has_text="Start triage").first.click()
    page.wait_for_selector(".triage")
    page.wait_for_timeout(700)

    assert "#/compare/" in page.url
    assert page.locator(".qbar .dots i").count() >= 1
    assert page.locator(".side", has_text="DECISION").count() == 1
    _clean(page)


def test_the_decision_keys_are_written_on_the_buttons(page, service):
    """Горячая клавиша, о которой знает только автор, не существует."""
    _sign_in(page, service)
    _visit(page, service, "/decisions")
    page.wait_for_selector(".cause")
    page.locator("button", has_text="Start triage").first.click()
    page.wait_for_selector(".triage")
    page.wait_for_timeout(600)

    # Проверяются именно значки клавиш, а не буквы в тексте: «A» найдётся в
    # слове «Accept», и такая проверка была бы зелёной и без единой клавиши.
    keys = [t.strip() for t in page.locator(".side .kbd").all_inner_texts()]
    assert set("ABDM") <= set(keys), keys
    _clean(page)


def test_an_empty_queue_says_so_instead_of_showing_nothing(page, service):
    """Пустой экран читается как поломка. Пустая очередь — это хорошая новость,
    и сказать об этом надо словами.
    """
    _sign_in(page, service)
    # Отвечаем на всё, что ждёт, — и смотрим, во что превратился экран.
    page.evaluate(
        "() => fetch('/api/decisions?project=' + encodeURIComponent(state.project))"
        "  .then(r => r.json())"
        "  .then(d => { const ids = [].concat(...d.causes.map(c => c.comparisons));"
        "    return fetch('/api/comparisons/bulk', {method:'POST',"
        "      headers:{'Content-Type':'application/json'},"
        "      body: JSON.stringify({ids, action:'reject'})}).then(r => r.status); })")
    page.wait_for_timeout(500)
    _visit(page, service, "/decisions")
    page.wait_for_timeout(900)

    # Заголовок, а не любое место экрана: пустая панель ниже говорит примерно
    # то же самое, и проверка «есть где-то на странице» прошла бы даже с
    # заголовком, которого нет.
    assert "Nothing is waiting" in page.text_content("#screen .h1")
    assert "answer" in page.text_content("#screen").lower()
    _clean(page)
