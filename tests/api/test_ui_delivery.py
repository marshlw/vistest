"""Обновление обязано доезжать до браузера, а поломка — быть слышной.

Сервис отдавал интерфейс обычным `StaticFiles` без единого заголовка про
кэш. Файлы подключены как `<script src="js/x.js">` — без сборщика и без хэша в
имени, — и Chrome в такой ситуации считает свежесть эвристикой по
`Last-Modified`: держит файл часами, не спрашивая сервер вовсе.

Ломается это молча и на редкость убедительно. Часть файлов свежая, часть из
кэша; экран рисуется, кнопка на месте, а её обработчик зовёт функцию, которой
в старом файле ещё нет. Получается «нажимаю — ничего не происходит»: ответ, не
отличимый ни от «задумалось», ни от «не нажалось», и искать по нему нечего.

Закрыто с трёх сторон, и здесь проверяются все три: адреса файлов несут
отпечаток сборки, ответы велят переспрашивать, а не пойманная ошибка в
браузере превращается в тост с именем файла.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from vistest.api.main import FRONTEND, frontend_stamp, stamp_assets

FRONTEND_JS = Path(__file__).resolve().parents[2] / "frontend" / "js"


# --------------------------------------------------------------------------- #
#  Отпечаток
# --------------------------------------------------------------------------- #
def test_the_stamp_changes_when_a_file_changes(tmp_path):
    """Иначе адрес прежний, и браузер имеет полное право не переспрашивать."""
    (tmp_path / "a.js").write_text("one", encoding="utf-8")
    before = frontend_stamp(tmp_path)
    (tmp_path / "a.js").write_text("one and a half", encoding="utf-8")
    assert frontend_stamp(tmp_path) != before


def test_the_stamp_notices_a_new_file(tmp_path):
    """Забытый в подключении файл — тоже изменение интерфейса."""
    (tmp_path / "a.js").write_text("one", encoding="utf-8")
    before = frontend_stamp(tmp_path)
    (tmp_path / "b.js").write_text("two", encoding="utf-8")
    assert frontend_stamp(tmp_path) != before


def test_the_stamp_is_stable_when_nothing_changed(tmp_path):
    """Иначе каждый заход выкачивает весь интерфейс заново."""
    (tmp_path / "a.js").write_text("one", encoding="utf-8")
    assert frontend_stamp(tmp_path) == frontend_stamp(tmp_path)


def test_pictures_and_data_do_not_shift_the_stamp(tmp_path):
    """Отпечаток про КОД интерфейса: иконка рядом ничего в нём не меняет."""
    (tmp_path / "a.js").write_text("one", encoding="utf-8")
    before = frontend_stamp(tmp_path)
    (tmp_path / "logo.svg").write_text("<svg/>", encoding="utf-8")
    assert frontend_stamp(tmp_path) == before


# --------------------------------------------------------------------------- #
#  Отпечаток в адресах
# --------------------------------------------------------------------------- #
def test_every_script_and_style_carries_the_stamp():
    """Версия в адресе — единственное, что действует через ЛЮБОЙ кэш.

    Заголовок работает только с тем кэшем, который согласился нас
    переспросить; адрес — со всеми, включая прокси по дороге.
    """
    html = stamp_assets((FRONTEND / "index.html").read_text("utf-8"), "abc123")
    refs = re.findall(r'(?:src|href)="(js/[\w.\-]+\.js|ui\.css)([^"]*)"', html)
    assert refs, "в странице не нашлось ни скриптов, ни стилей"
    for name, tail in refs:
        assert tail == "?v=abc123", f"{name} без отпечатка"


def test_external_addresses_are_left_alone():
    """Шрифты живут не у нас, и наш отпечаток для них — мусор в адресе."""
    html = stamp_assets(
        '<link href="https://fonts.gstatic.com/inter.css">'
        '<link href="https://fonts.googleapis.com/css2?family=X">'
        '<script src="js/core.js"></script>', "abc123")
    assert 'href="https://fonts.gstatic.com/inter.css"' in html
    assert 'href="https://fonts.googleapis.com/css2?family=X"' in html
    assert 'src="js/core.js?v=abc123"' in html


def test_stamping_twice_does_not_pile_up():
    """Страница отдаётся на каждый заход — второй отпечаток был бы поверх."""
    once = stamp_assets('<script src="js/core.js"></script>', "abc123")
    assert stamp_assets(once, "def456") == once


# --------------------------------------------------------------------------- #
#  Заголовки
# --------------------------------------------------------------------------- #
@pytest.fixture
def ui_client(tmp_path, monkeypatch):
    monkeypatch.setenv("VISTEST_ROOT", str(tmp_path / ".vistest"))
    monkeypatch.setenv("VISTEST_AUTH", "off")

    import importlib
    import threading

    from fastapi import APIRouter
    from fastapi.testclient import TestClient

    import vistest.api.auth as authmod
    import vistest.api.db as dbmod

    dbmod._local = threading.local()
    authmod.router = APIRouter()
    import vistest.api.main as mainmod
    importlib.reload(mainmod)
    return TestClient(mainmod.app)


def test_the_page_itself_is_never_taken_from_cache(ui_client):
    """В ней лежат отпечатки — старая страница подсунет старые адреса."""
    r = ui_client.get("/ui/")
    assert r.status_code == 200, r.text
    assert "no-store" in r.headers.get("cache-control", "")


def test_the_served_page_carries_the_stamp(ui_client):
    r = ui_client.get("/ui/")
    assert re.search(r'src="js/core\.js\?v=[0-9a-f]{6,}"', r.text)


def test_scripts_are_revalidated_rather_than_assumed_fresh(ui_client):
    """`no-cache` — это «кэшируй, но каждый раз спрашивай».

    С ETag это стоит один 304 на файл и снимает целый класс поломок, где
    правка есть на диске, а в браузере её нет.
    """
    r = ui_client.get("/ui/js/core.js")
    assert r.status_code == 200
    assert "no-cache" in r.headers.get("cache-control", "")


# --------------------------------------------------------------------------- #
#  Поломка слышна
# --------------------------------------------------------------------------- #
def test_an_uncaught_failure_becomes_a_toast():
    """«Ничего не происходит» — худший из возможных ответов интерфейса.

    Он не отличим ни от «задумалось», ни от «не нажалось», и искать по нему
    нечего. Поэтому и синхронная ошибка, и упавший промис становятся видимым
    сообщением.
    """
    core = (FRONTEND_JS / "core.js").read_text("utf-8")
    assert "function installErrorSurface" in core
    assert "'error'" in core and "'unhandledrejection'" in core
    assert "toast(" in core.split("function reportBreak")[1]


def test_a_file_that_did_not_load_is_named():
    """Не доехавший файл уносит с собой всё, что в нём объявлено."""
    core = (FRONTEND_JS / "core.js").read_text("utf-8")
    assert "SCRIPT" in core and "did not load" in core


def test_the_error_surface_is_installed_before_anything_else_runs():
    """Если сломано подключённое выше, услышать об этом надо, а не гадать."""
    boot = (FRONTEND_JS / "boot.js").read_text("utf-8")
    body = boot.split("async function boot(){", 1)[1]
    assert body.index("installErrorSurface()") < body.index("buildNav()")
