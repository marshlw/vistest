# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Корпус для бенчмарка: пары «эталон / прогон» с известным правильным ответом.

Зачем синтетика. На настоящих скриншотах неизвестно, что «правда»: разметка
делается человеком, а человек в спорных случаях размечает так, как ему удобно.
Здесь ответ задан построением: мы сами решили, где регресс, а где шум, и это
решение можно оспорить, глядя на код, а не на нашу добросовестность.

Две группы, и ошибки в них стоят по-разному:

* **NOISE** — страница не менялась по существу. Падение здесь — ложное.
  Именно ложные падения убивают доверие: команда через месяц ставит
  `--update-snapshots` в CI, и визуальное тестирование заканчивается.
* **SIGNAL** — реальный регресс. Пропуск здесь — пропущенный баг.

Корпус экспортируется на диск (`export_corpus`), чтобы чужие инструменты
считали ровно те же пары. Без этого сравнение движков не значит ничего.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import synthetic as syn


@dataclass
class Case:
    name: str
    group: str          # NOISE | SIGNAL
    expected_fail: bool
    expected: np.ndarray
    actual: np.ndarray
    why: str = ""       # почему ответ именно такой
    # Маска настоящего изменения — только для SIGNAL. Нужна, чтобы размечать
    # регионы поштучно: рядом с настоящим регрессом на странице попадаются и
    # шумовые регионы, и называть их сигналом значит учить модель неправде.
    change_mask: np.ndarray | None = None
    family: str = ""    # явление, а не случай: «jpeg» для всех вёрсток сразу


def build() -> list[Case]:
    base = syn.page()

    noise = [
        ("identical", base.copy(),
         "байт в байт: любое падение здесь — дефект движка"),
        ("sensor noise σ=1.6", syn.add_sensor_noise(base, 1.6, seed=1),
         "±2 единицы яркости, ΔE00 < 1 — человек не различает в принципе"),
        ("sensor noise σ=3.0", syn.add_sensor_noise(base, 3.0, seed=2),
         "верхняя граница кодекового шума"),
        ("antialias 0.4px", syn.resample_antialias(base, 0.4),
         "другой субпиксельный рендер текста: смена версии браузера или hinting"),
        ("jpeg q=88", syn.recompress(base, 88),
         "пережатие в конвейере скриншотов"),
        ("jpeg q=75", syn.recompress(base, 75),
         "агрессивное пережатие — всё ещё не регресс вёрстки"),
        ("global shift 1px", syn.shift(base, 1, 1),
         "страница целиком уехала на пиксель: полоса прокрутки, округление"),
        ("global shift 3px", syn.shift(base, 3, 2),
         "то же, но крупнее — всё ещё сдвиг целиком, а не правка лейаута"),
        ("combined", syn.recompress(
            syn.add_sensor_noise(syn.resample_antialias(syn.shift(base, 1, 0)), 1.2),
            90),
         "всё сразу — так выглядит реальный прогон в чужом CI"),
        # Ниже — шум, который классические фильтры проходят труднее. Первые
        # девять случаев движок гасит на дефолтных порогах начисто, и это
        # правильно; но тогда на них не видно ни разницы между движками, ни
        # материала для обучающегося гейта.
        ("font fallback", syn.font_fallback(base),
         "подставился другой шрифт: буквы те же, штрихи толще"),
        ("shadow radius", syn.blur_shadows(base),
         "тень пересчитана с другим радиусом размытия"),
        ("gradient dither", syn.dither_gradient(base),
         "другой дизеринг градиента — математически видно, глазом нет"),
        ("scrollbar", syn.scrollbar(base),
         "появилась полоса прокрутки: это среда, а не вёрстка"),
        ("caret", syn.caret(base),
         "мигающая каретка в поле ввода поймана в кадр"),
        ("lazy placeholder", syn.lazy_placeholder(base),
         "картинка не успела догрузиться: снимок сделан рано, страница цела"),
        ("subpixel text", syn.subpixel_text(base),
         "субпиксельный сдвиг текстовых строк, а не всей страницы"),
    ]

    signal = [
        ("button color", syn.regress_button_color(base),
         "кнопка покупки стала серой: цвет изменился, структура нет"),
        ("button removed", syn.regress_button_removed(base),
         "кнопка исчезла — худший из возможных визуальных багов"),
        ("promo removed", syn.regress_promo_gone(base),
         "блок пропал: крупное изменение, пропускать нечем оправдаться"),
        ("layout +40px", syn.regress_layout_moved(base, 40),
         "контент сместился на 40px — поехала вёрстка"),
        ("page taller +160", syn.regress_taller_page(base, 160),
         "высота страницы выросла: частый и дорогой регресс"),
        ("tiny icon 22px", syn.regress_tiny_icon(base),
         "значок 22×22 — проверка, что мелочь не тонет в фильтрах"),
        ("price changed", syn.regress_price_changed(base),
         "цена другая: маленькая площадь, большая цена ошибки"),
        ("button shrunk", syn.regress_button_shrunk(base),
         "кнопка стала уже — изменился размер, а не цвет"),
        ("text overflow", syn.regress_text_overflow(base),
         "текст вылез за карточку: обычный итог смены шрифта в вёрстке"),
        ("header color", syn.regress_header_color(base),
         "шапка сменила цвет: плоская заливка, структура та же"),
        ("promo text", syn.regress_promo_text(base),
         "промо-код другой — одна строка, но ошибка содержательная"),
    ]

    cases = [Case(n, "NOISE", False, base, a, w) for n, a, w in noise]
    cases += [Case(n, "SIGNAL", True, base, a, w) for n, a, w in signal]
    return cases


# --------------------------------------------------------------------------- #
def export_corpus(cases: list[Case], out: str | Path) -> Path:
    """Разложить корпус на диск: PNG-пары + manifest.json.

    Нужен, чтобы pixelmatch, Playwright и кто угодно ещё считали ровно те же
    изображения. Сравнение движков на разных данных — это не сравнение.
    """
    from vistest.capture.playwright_capture import _write_png

    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    manifest = []

    for c in cases:
        slug = _slug(c.name)
        d = out / slug
        d.mkdir(exist_ok=True)
        _write_png(d / "expected.png", c.expected)
        _write_png(d / "actual.png", c.actual)
        manifest.append({
            "name": c.name,
            "slug": slug,
            "group": c.group,
            "expected_fail": c.expected_fail,
            "why": c.why,
            "expected": f"{slug}/expected.png",
            "actual": f"{slug}/actual.png",
        })

    (out / "manifest.json").write_text(
        json.dumps({"cases": manifest}, indent=2, ensure_ascii=False),
        encoding="utf-8")
    return out


def _slug(s: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in s).strip("_")


# --------------------------------------------------------------------------- #
#  Генератор: вёрстки × явления
#
#  Отобранный корпус выше остаётся тем, что показывают человеку: пятнадцать
#  строк, каждую можно прочитать и оспорить. Учить на нём нельзя — по одному
#  примеру на явление и одна-единственная вёрстка. Генератор перемножает
#  параметризованные макеты на те же явления и даёт сотни пар.
# --------------------------------------------------------------------------- #
def _change_mask(expected: np.ndarray, actual: np.ndarray) -> np.ndarray:
    """Где страница изменилась по существу.

    Порог грубый и такой и должен быть: настоящий регресс — это другой цвет,
    другой текст, сдвинутый блок, то есть десятки единиц яркости. Тонкая
    градация здесь не нужна, нужна граница «здесь менялось / здесь нет».
    """
    h = max(expected.shape[0], actual.shape[0])
    w = max(expected.shape[1], actual.shape[1])
    a = np.zeros((h, w, 3), np.int16)
    b = np.zeros((h, w, 3), np.int16)
    a[:expected.shape[0], :expected.shape[1]] = expected
    b[:actual.shape[0], :actual.shape[1]] = actual
    return (np.abs(a - b).max(axis=2) > 8)


def generate(layout_count: int = 6) -> list[Case]:
    """Обучающий корпус: каждое явление на каждой вёрстке."""
    cases: list[Case] = []
    for lay in syn.layouts(layout_count):
        base = syn.render(lay)

        for family, make in syn.NOISE_TRANSFORMS.items():
            actual = make(lay, base)
            if actual.shape == base.shape and not (actual != base).any():
                continue                      # преобразование ничего не сделало
            cases.append(Case(f"{lay.name}/{family}", "NOISE", False,
                              base, actual, family=family))

        for family, make in syn.SIGNAL_TRANSFORMS.items():
            actual = make(lay, base)
            if actual.shape == base.shape and not (actual != base).any():
                # На этой вёрстке менять нечего: промо-блока нет, карточки нет.
                continue
            cases.append(Case(f"{lay.name}/{family}", "SIGNAL", True,
                              base, actual, family=family,
                              change_mask=_change_mask(base, actual)))
    return cases
