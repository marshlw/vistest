# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Интерфейс собран из файлов — и они обязаны сойтись.

Пока весь фронт был одним `index.html` на четыре тысячи строк, поломки такого
рода не существовало в принципе: код либо есть, либо нет. После разрезания
появилась новая, и она из самых неприятных — **тихая**.

Забыть подключить модуль означает интерфейс, который загрузился, нарисовал
навигацию и молчит: экрана просто нет, в консоли — `SCREENS.baselines is not a
function` по клику, а до клика ничего не происходит. Оставить в каталоге файл,
который никуда не подключён, — то же самое с другой стороны: правишь код,
перезагружаешь, ничего не меняется, и полчаса уходит на поиск причины в
браузерном кэше.

Поэтому здесь проверяется не поведение, а **сходимость набора**: что подключено
ровно то, что лежит, и в том порядке, который обязателен.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
INDEX = FRONTEND / "index.html"
JS_DIR = FRONTEND / "js"

SRC = re.compile(r'<script src="js/([^"]+)"></script>')


@pytest.fixture(scope="module")
def listed() -> list[str]:
    return SRC.findall(INDEX.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def present() -> set[str]:
    return {p.name for p in JS_DIR.glob("*.js")}


def test_every_listed_module_exists(listed):
    """Отсутствующий файл — 404, который браузер покажет только в консоли."""
    missing = [n for n in listed if not (JS_DIR / n).exists()]
    assert not missing, f"подключены, но не существуют: {missing}"


def test_every_module_is_listed(listed, present):
    """Файл, который никуда не подключён, — код, который не выполняется.

    Правишь, перезагружаешь, ничего не меняется — и причину ищешь где угодно,
    только не здесь.
    """
    orphans = sorted(present - set(listed))
    assert not orphans, f"лежат, но не подключены: {orphans}"


def test_nothing_is_listed_twice(listed):
    """Второе подключение выполнит файл дважды.

    Для объявлений функций это безобидно, а для того, что вешает обработчики на
    верхнем уровне, — нет: один клик начинает срабатывать дважды.
    """
    twice = sorted({n for n in listed if listed.count(n) > 1})
    assert not twice, f"подключены дважды: {twice}"


def test_the_basis_comes_first_and_the_boot_last(listed):
    """Два правила порядка, и других нет.

    `core.js` даёт `$`, `el`, `api` и тосты — на них опирается всё остальное, а
    он ни на что. `boot.js` вызывает то, что определено выше, и потому обязан
    идти последним. Между ними порядок свободный: файлы объявляют функции, а не
    выполняют работу.
    """
    assert listed[0] == "core.js"
    assert listed[-1] == "boot.js"


def test_the_styles_are_linked(listed):
    text = INDEX.read_text(encoding="utf-8")
    assert '<link rel="stylesheet" href="ui.css">' in text
    assert (FRONTEND / "ui.css").exists()


def test_no_code_stayed_behind_in_the_page(listed):
    """В `index.html` осталась разметка — и только она.

    Один забытый `<script>` с логикой внутри — и половина правил живёт в файле,
    в который за ней никто не пойдёт.
    """
    text = INDEX.read_text(encoding="utf-8")
    assert "<style>" not in text
    inline = re.findall(r"<script(?![^>]*\ssrc=)[^>]*>", text)
    assert not inline, f"в странице остался код: {inline}"


def test_every_module_says_what_is_in_it(present):
    """Разрезание без карты — это те же четыре тысячи строк, только в мешках."""
    silent = []
    for name in sorted(present):
        head = (JS_DIR / name).read_text(encoding="utf-8").lstrip()
        if not head.startswith("/*"):
            silent.append(name)
    assert not silent, f"без заголовка: {silent}"


def test_no_module_grew_back_into_a_monolith(present):
    """Предел не догма, а напоминание: резали как раз от того, что не читается.

    Порог с запасом — самый большой экран сегодня около шестисот строк, и это
    честный размер для экрана разбора. Речь о том, чтобы не приползти обратно к
    четырём тысячам, не заметив.
    """
    limit = 900
    big = {n: len((JS_DIR / n).read_text(encoding="utf-8").splitlines())
           for n in present
           if len((JS_DIR / n).read_text(encoding="utf-8").splitlines()) > limit}
    assert not big, f"пора резать дальше: {big}"


def test_no_two_modules_define_the_same_function(present):
    """Разрез на файлы завёл поломку, которой в одном файле быть не могло.

    Область видимости у всех файлов одна: две функции с одним именем — это не
    «переопределение», а тихая замена одной другой в зависимости от порядка
    подключения. Так уже ломались два экрана. Таблица окружения в диагностике
    рисовала список секретов, потому что `envRow` называлась и строка редактора
    секретов в настройках; метки вердикта на странице прогона брались из
    сравнения прогонов, потому что обе назывались `verdictTag`.

    Ошибка тихая вдвойне: экран не падает и не пустеет — он показывает НЕ ТО,
    и выглядит при этом совершенно правдоподобно.
    """
    seen: dict[str, str] = {}
    clashes = []
    for name in sorted(present):
        text = (JS_DIR / name).read_text(encoding="utf-8")
        for m in re.finditer(r"^(?:async\s+)?function\s+([A-Za-z_$][\w$]*)",
                             text, re.M):
            fn = m.group(1)
            if fn in seen and seen[fn] != name:
                clashes.append(f"{fn}: {seen[fn]} и {name}")
            seen.setdefault(fn, name)
    assert not clashes, "одно имя в двух файлах: " + "; ".join(clashes)


def test_no_two_modules_define_the_same_top_level_constant(present):
    """То же самое, но злее: `const` в двух файлах — это уже не подмена.

    Второе объявление того же имени через `const` в общей области видимости —
    `SyntaxError` на этапе разбора файла, и падает при этом ВЕСЬ файл целиком.
    Экран, который в нём объявлен, просто перестаёт существовать, а в консоли
    одна строка про redeclaration, по которой не видно, чего именно не стало.
    """
    seen: dict[str, str] = {}
    clashes = []
    for name in sorted(present):
        text = (JS_DIR / name).read_text(encoding="utf-8")
        for m in re.finditer(r"^(?:const|let)\s+([A-Za-z_$][\w$]*)\s*=",
                             text, re.M):
            fn = m.group(1)
            if fn in seen and seen[fn] != name:
                clashes.append(f"{fn}: {seen[fn]} и {name}")
            seen.setdefault(fn, name)
    assert not clashes, "одно имя в двух файлах: " + "; ".join(clashes)


def test_the_page_itself_stays_small(listed):
    """Ради этого всё и делалось: `index.html` — разметка, а не приложение."""
    assert len(INDEX.read_text(encoding="utf-8").splitlines()) < 200
