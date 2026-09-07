# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Интерфейс поднимается и доходит до первого экрана.

Остальные проверки фронта читают файлы: подключено ли то, что лежит, не
столкнулись ли имена, находит ли селектор узел. Ни одна из них не отвечает на
вопрос, который задаёт человек, открывший адрес: нарисовалось ли хоть
что-нибудь. Отвечает эта — она действительно выполняет весь интерфейс.

Нужен node и jsdom. Их может не быть на машине, где гоняют только Python, —
тогда тест пропускается, а не краснеет. В CI они есть, и это то место, где эта
проверка и обязана стоять: она ловит класс поломок, который чтением файлов не
ловится вовсе.

Подготовка: `npm install jsdom` в корне репозитория.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SMOKE = ROOT / "tests" / "ui" / "boot_smoke.mjs"
MATRIX = ROOT / "tests" / "ui" / "matrix_view.mjs"
ZONES = ROOT / "tests" / "ui" / "zones_view.mjs"
RUNMENU = ROOT / "tests" / "ui" / "run_menu_view.mjs"
CITOKENS = ROOT / "tests" / "ui" / "ci_tokens_view.mjs"
CARDMENU = ROOT / "tests" / "ui" / "project_card_menu.mjs"
BASELINES = ROOT / "tests" / "ui" / "baselines_view.mjs"
SUITEFORM = ROOT / "tests" / "ui" / "project_suite_view.mjs"
FRONTEND = ROOT / "frontend"


def _jsdom_available(node: str) -> bool:
    return subprocess.run(
        [node, "-e", "require.resolve('jsdom')"],
        cwd=ROOT, capture_output=True).returncode == 0


def test_the_interface_reaches_its_first_screen():
    node = shutil.which("node")
    if not node:
        pytest.skip("нет node — проверка запуска интерфейса пропущена")
    if not _jsdom_available(node):
        pytest.skip("нет jsdom (`npm install jsdom`) — проверка пропущена")

    done = subprocess.run([node, str(SMOKE), str(FRONTEND)],
                          cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, (
        "интерфейс не дошёл до первого экрана:\n"
        + (done.stderr or done.stdout).strip())


def test_the_run_page_shows_the_matrix_as_one_snapshot_with_variants():
    """Половина смысла матрицы — на экране, и проверять её надо на экране.

    Шесть строк `checkout.png` подряд отвечают на вопрос «что упало» словом
    «всё»: экономия внимания, ради которой матрица делалась, теряется в списке.
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("нет node — проверка показа матрицы пропущена")
    if not _jsdom_available(node):
        pytest.skip("нет jsdom (`npm install jsdom`) — проверка пропущена")

    done = subprocess.run([node, str(MATRIX), str(FRONTEND)],
                          cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, (
        "интерфейс показывает матрицу не так:\n"
        + (done.stderr or done.stdout).strip())


def test_the_zone_editor_shows_what_holds_each_zone():
    """Зона по элементу и зона по координатам — два одинаковых прямоугольника.

    Разница между ними в том, переживёт ли маска ближайший редизайн. Не
    показать её — значит оставить всё как было: человек рисует рамку и не
    знает, что получил.
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("нет node — проверка редактора зон пропущена")
    if not _jsdom_available(node):
        pytest.skip("нет jsdom (`npm install jsdom`) — проверка пропущена")

    done = subprocess.run([node, str(ZONES), str(FRONTEND)],
                          cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, (
        "редактор зон показывает не то:\n" + (done.stderr or done.stdout).strip())


def test_the_run_menu_offers_three_shapes_and_explains_the_refusal():
    """Три вида запуска — три разных вопроса, и различать их надо словами.

    А проект, который запускается своей командой, браузер назвать не даёт:
    пункты обязаны быть видимыми и отключёнными с причиной, иначе человек
    гадает, почему у соседнего проекта выбор есть, а у его нет.
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("нет node — проверка меню запуска пропущена")
    if not _jsdom_available(node):
        pytest.skip("нет jsdom (`npm install jsdom`) — проверка пропущена")

    done = subprocess.run([node, str(RUNMENU), str(FRONTEND)],
                          cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, (
        "меню запуска показывает не то:\n" + (done.stderr or done.stdout).strip())


def test_a_ci_token_is_shown_once_and_leaves_no_trace():
    """Секрет показывается один раз — значит показ обязан быть незакрываемым мимо.

    И он не должен оставаться на странице после закрытия: список
    перерисовывается, копируется в тикеты и открыт на чужом мониторе на
    планёрке. Токен в нём — это токен, который утёк.
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("нет node — проверка экрана токенов пропущена")
    if not _jsdom_available(node):
        pytest.skip("нет jsdom (`npm install jsdom`) — проверка пропущена")

    done = subprocess.run([node, str(CITOKENS), str(FRONTEND)],
                          cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, (
        "экран токенов показывает не то:\n" + (done.stderr or done.stdout).strip())


def test_the_browser_pick_dropdowns_on_a_project_card_open_where_they_were_clicked():
    """Меню собралось правильно и человек его не увидел — это одно и то же.

    `.menu` — `position:fixed` без координат: без явных `top`/`left` блок
    остаётся в потоке, за концом страницы, ниже экрана. Проверка «меню
    открылось» при этом проходит, а кнопка «перестала работать». Поэтому
    кнопки здесь нажимаются настоящим событием на настоящей карточке, а
    проверяется то, что отличает видимое меню от невидимого.

    Сюда же переехало всё остальное, что видно на карточке проекта и что
    ломалось молча: высота кнопок «▾» в строке действий, якорь подменю
    «⋯ → выбрать браузеры» (оно вставало по пункту, который к моменту показа
    уже исчез, — то есть в пустоте посреди страницы), выбор НЕПУСТОГО набора
    для «Open VisTest snapshots» и пометка движка, которого в окружении нет.
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("нет node — проверка выпадашек на карточке пропущена")
    if not _jsdom_available(node):
        pytest.skip("нет jsdom (`npm install jsdom`) — проверка пропущена")

    done = subprocess.run([node, str(CARDMENU), str(FRONTEND)],
                          cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, (
        "выпадашки выбора браузера на карточке проекта не раскрываются:\n"
        + (done.stderr or done.stdout).strip())


def test_the_baselines_screen_asks_browser_and_window_separately():
    """Ключ хранения — плохой переключатель, и это видно только на экране.

    В `docker-chromium-1x-390x844` склеены четыре независимых ответа, а
    спрашивают их по одному: «в каком движке» и «в каком размере окна» —
    разные вопросы. С матрицей таких строк шесть или восемнадцать, и выбрать
    из них «firefox на телефоне» можно было только вычитав каждую по буквам.

    Здесь же проверяется, что набор эталонов следует за закреплённым в шапке
    проектом (пока переключателей было два, человек выбирал проект наверху и
    продолжал видеть чужие эталоны внизу) и что сравнение по движкам
    объединяет варианты по ИМЕНИ снимка, а не по их порядку в наборе.
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("нет node — проверка экрана эталонов пропущена")
    if not _jsdom_available(node):
        pytest.skip("нет jsdom (`npm install jsdom`) — проверка пропущена")

    done = subprocess.run([node, str(BASELINES), str(FRONTEND)],
                          cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, (
        "экран эталонов показывает не то:\n"
        + (done.stderr or done.stdout).strip())


def test_the_connection_form_sends_the_profile_and_the_naming_rules():
    """Поля, которые заполняют один раз и на которые опирается каждый прогон.

    Ошибка здесь тихая по определению: форма сохранится, тост скажет «Saved»,
    а в описание проекта уедет не то — и выяснится это через день, на прогоне,
    который «почему-то не находит пар». Поэтому проверяется не наличие полей,
    а тело запроса.

    Проверка сразу окупилась: форма настроек падала на `TypeError` — то есть
    не открывалась ВООБЩЕ, ни у одного проекта, — потому что `append()`
    возвращает `undefined`, а не добавленный узел.
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("нет node — проверка формы подключения пропущена")
    if not _jsdom_available(node):
        pytest.skip("нет jsdom (`npm install jsdom`) — проверка пропущена")

    done = subprocess.run([node, str(SUITEFORM), str(FRONTEND)],
                          cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, (
        "подключение чужого набора настраивается не тем:\n"
        + (done.stderr or done.stdout).strip())
