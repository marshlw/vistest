# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Чем снят снимок — тестом или адресом.

Зачем это вообще понадобилось. В паспорте эталона был `url`, и по нему в
интерфейсе появлялись кнопки «Check» и «Re-capture». Работали они честно ровно
для страниц, открытых с улицы: грузили адрес и снимали. Всё, что лежит за
входом — а это большинство страниц в любом внутреннем продукте, — они снимали
уже как страницу входа, и человек получал прогон, который «ничего не нашёл»,
или эталон, переписанный формой логина.

Причина в том, что снимок не помнил, откуда взялся. А взяться он мог из теста,
который знает и про логин, и про переходы, и про подготовку данных, — и этот
тест был известен ровно в момент съёмки и терялся сразу после.

Здесь он не теряется. `PYTEST_CURRENT_TEST` ставит сам pytest на каждый тест, и
это единственный канал, который одинаково работает и для наших тестов, и для
чужих: адаптер подключается к чужому pytest снаружи, ничего не зная про их
фикстуры и базовые классы, — но переменная есть и там.

Чего здесь нет намеренно: определения источника «по догадке». Функция читает
окружение своего процесса, и вызывают её только там, где этот процесс —
действительно процесс теста (наш раннер, адаптер). Сервис, у которого своё
окружение, передаёт источник явно; иначе прогон, запущенный кнопкой в
интерфейсе во время нашего же тестового прогона, приписал бы снимку чужой тест
и выглядел бы при этом совершенно убедительно.
"""

from __future__ import annotations

import os

# Что породило снимок:
#   test      — прогон теста (наш или чужой, через адаптер)
#   url       — съёмка по адресу из интерфейса или CLI
#   recorded  — запись мышью
KINDS = ("test", "url", "recorded")


def current_test() -> dict:
    """Тест, внутри которого мы сейчас находимся, — или пустой словарь.

    `PYTEST_CURRENT_TEST` выглядит как `tests/test_login.py::test_form (call)`.
    Хвост в скобках — фаза (`setup`, `call`, `teardown`); в идентификаторе теста
    ей не место: снимок, снятый в фикстуре, и снимок, снятый в теле, — это один
    и тот же тест.
    """
    raw = (os.getenv("PYTEST_CURRENT_TEST") or "").strip()
    if not raw:
        return {}
    node = raw.rsplit(" (", 1)[0].strip() if raw.endswith(")") else raw
    if "::" not in node:
        return {}
    file_part, _, case = node.partition("::")
    return {"test": node, "file": file_part, "case": case}


def detect(kind: str = "", **extra) -> dict:
    """Паспорт источника для записи в `meta`.

    `kind` можно не указывать: если мы внутри теста, это `test`, иначе `url`.
    Явное указание нужно записи мышью — она идёт не из pytest, но и адресом в
    чистом виде не является: у неё есть шаги.
    """
    found = current_test()
    if not kind:
        kind = "test" if found else "url"
    if kind not in KINDS:
        raise ValueError(f"kind must be one of: {', '.join(KINDS)}")

    out: dict = {"kind": kind}
    if kind == "test" and found:
        out.update(found)
    # Ключ проекта приходит из окружения прогона: снимок чужого набора надо
    # уметь запустить ЧУЖИМ интерпретатором, из ЧУЖОГО корня и с их conftest —
    # без ключа мы знаем идентификатор теста, но не знаем, где его искать.
    key = (os.getenv("VISTEST_PROJECT_KEY") or "").strip()
    if key:
        out["project_key"] = key
    out.update({k: v for k, v in extra.items() if v not in (None, "", [])})
    return out


def label(source: dict | None) -> str:
    """Одна строка для интерфейса и лога."""
    src = source or {}
    if src.get("kind") == "test" and src.get("test"):
        return str(src["test"])
    if src.get("kind") == "recorded":
        return "recorded with the mouse"
    return "captured by URL"


def runnable_by_test(source: dict | None) -> bool:
    return bool((source or {}).get("kind") == "test" and (source or {}).get("test"))
