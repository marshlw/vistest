# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Генератор синтетических «страниц» для бенчмарка движка.

Настоящие скриншоты в тестах движка не годятся: неизвестно, что в них
«правда». Синтетика позволяет задать эталонный ответ точно — мы сами решаем,
где изменение, а где шум.
"""

from __future__ import annotations

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

W, H = 900, 1200
BG = (250, 250, 252)
INK = (33, 37, 41)
ACCENT = (52, 120, 246)


def blank(w: int = W, h: int = H) -> np.ndarray:
    img = np.zeros((h, w, 3), np.uint8)
    img[:] = BG
    return img


def page(*, button_color=ACCENT, show_promo=True, y_offset=0,
         extra_height=0, title="Оформление заказа") -> np.ndarray:
    """Макет «страницы»: шапка, текст, карточка, кнопка, промо-блок."""
    img = blank(W, H + extra_height)

    # шапка
    cv2.rectangle(img, (0, 0), (W, 64), (255, 255, 255), -1)
    cv2.line(img, (0, 64), (W, 64), (225, 228, 234), 1)
    cv2.putText(img, "SHOP", (28, 42), 0, 0.9, INK, 2, cv2.LINE_AA)
    for i, item in enumerate(["Catalog", "Cart", "Account"]):
        cv2.putText(img, item, (330 + i * 130, 40), 0, 0.55, (90, 96, 108), 1, cv2.LINE_AA)

    y = 110 + y_offset
    cv2.putText(img, title, (40, y), 0, 0.95, INK, 2, cv2.LINE_AA)

    # абзацы текста (много мелких краёв — главный источник AA-шума)
    for row in range(9):
        yy = y + 44 + row * 26
        width = 640 - (row % 3) * 90
        cv2.rectangle(img, (40, yy), (40 + width, yy + 11), (198, 203, 212), -1)

    # карточка товара
    cy = y + 320
    cv2.rectangle(img, (40, cy), (560, cy + 180), (255, 255, 255), -1)
    cv2.rectangle(img, (40, cy), (560, cy + 180), (222, 226, 233), 1)
    cv2.rectangle(img, (60, cy + 24), (190, cy + 156), (232, 236, 242), -1)
    cv2.putText(img, "Nikon Z6 II", (215, cy + 60), 0, 0.7, INK, 2, cv2.LINE_AA)
    cv2.putText(img, "159 990 RUB", (215, cy + 104), 0, 0.6, (90, 96, 108), 1, cv2.LINE_AA)

    # кнопка
    by = cy + 220
    cv2.rectangle(img, (40, by), (300, by + 52), button_color, -1)
    cv2.putText(img, "Buy now", (105, by + 34), 0, 0.65, (255, 255, 255), 2, cv2.LINE_AA)

    if show_promo:
        py = by + 90
        cv2.rectangle(img, (40, py), (860, py + 120), (255, 248, 225), -1)
        cv2.rectangle(img, (40, py), (860, py + 120), (245, 216, 140), 1)
        cv2.putText(img, "Free delivery until Friday", (64, py + 52), 0, 0.65,
                    (140, 108, 20), 2, cv2.LINE_AA)
        cv2.putText(img, "Promo code: FRIDAY24", (64, py + 88), 0, 0.5,
                    (160, 130, 50), 1, cv2.LINE_AA)

    # футер
    fy = img.shape[0] - 90
    cv2.rectangle(img, (0, fy), (W, img.shape[0]), (243, 244, 247), -1)
    cv2.putText(img, "(c) 2026 SHOP", (40, fy + 50), 0, 0.5, (140, 146, 158), 1, cv2.LINE_AA)
    return img


# --------------------------------------------------------------------------- #
#  Виды «шума», которые НЕ должны валить тест
# --------------------------------------------------------------------------- #
def add_sensor_noise(img: np.ndarray, sigma: float = 1.6, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = rng.normal(0, sigma, img.shape)
    return np.clip(img.astype(np.float32) + n, 0, 255).astype(np.uint8)


def resample_antialias(img: np.ndarray, amount: float = 0.35) -> np.ndarray:
    """Имитация другого субпиксельного рендера: сдвиг на долю пикселя.

    Именно так выглядит различие между двумя прогонами одного текста на
    разных версиях браузера или при другом hinting'е.
    """
    m = np.float32([[1, 0, amount], [0, 1, amount * 0.5]])
    return cv2.warpAffine(img, m, (img.shape[1], img.shape[0]),
                          flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def recompress(img: np.ndarray, quality: int = 82) -> np.ndarray:
    """Артефакты JPEG — заметны математически, почти не заметны глазом."""
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    return cv2.imdecode(buf, cv2.IMREAD_COLOR) if ok else img


def shift(img: np.ndarray, dx: int = 1, dy: int = 1) -> np.ndarray:
    m = np.float32([[1, 0, dx], [0, 1, dy]])
    return cv2.warpAffine(img, m, (img.shape[1], img.shape[0]),
                          borderMode=cv2.BORDER_REPLICATE)


def add_clock(img: np.ndarray, text: str = "12:41:07") -> np.ndarray:
    """Живой элемент — часы. Должен быть отловлен stability-маской."""
    out = img.copy()
    cv2.rectangle(out, (740, 20), (880, 48), (255, 255, 255), -1)
    cv2.putText(out, text, (748, 41), 0, 0.55, (90, 96, 108), 1, cv2.LINE_AA)
    return out


# --------------------------------------------------------------------------- #
#  Настоящие регрессы, которые ДОЛЖНЫ быть найдены
# --------------------------------------------------------------------------- #
def regress_button_color(img: np.ndarray) -> np.ndarray:
    return page(button_color=(150, 152, 158))


def regress_button_removed(img: np.ndarray) -> np.ndarray:
    out = page()
    cy = 110 + 320
    by = cy + 220
    out[by:by + 52, 40:300] = BG
    return out


def regress_promo_gone(img: np.ndarray) -> np.ndarray:
    return page(show_promo=False)


def regress_layout_moved(img: np.ndarray, dy: int = 40) -> np.ndarray:
    return page(y_offset=dy)


def regress_taller_page(img: np.ndarray, extra: int = 160) -> np.ndarray:
    return page(extra_height=extra)


def regress_tiny_icon(img: np.ndarray) -> np.ndarray:
    """Изменение размером 24×24 — проверка, что мелочь не теряется."""
    out = img.copy()
    cv2.circle(out, (858, 34), 11, (231, 76, 60), -1)
    return out


# --------------------------------------------------------------------------- #
#  Шум, который классические фильтры проходят труднее
#
#  Первые девять случаев корпуса движок гасит на дефолтных порогах начисто —
#  ноль регионов. Это хорошо для бенчмарка и бесполезно для обучения: гейту
#  не на чем учиться, если шум до него не доходит. Ниже — то, что реально
#  доходит: перерисованный текст, пересчитанные тени, дизеринг градиента.
# --------------------------------------------------------------------------- #
def font_fallback(img: np.ndarray, weight: int = 1) -> np.ndarray:
    """Текст перерисован другим начертанием.

    Подстановка шрифта после обновления системы: буквы те же, штрихи другой
    толщины. Математически это заметная разница на всей текстовой площади.
    """
    out = page()
    y = 110
    cv2.putText(out, "Оформление заказа", (40, y), 0, 0.95, INK, 2 + weight,
                cv2.LINE_AA)
    return out


def blur_shadows(img: np.ndarray, radius: int = 3) -> np.ndarray:
    """Тень пересчитана с другим радиусом размытия."""
    out = img.copy()
    cy = 110 + 320
    band = out[cy + 180:cy + 190, 40:560]
    if band.size:
        out[cy + 180:cy + 190, 40:560] = cv2.GaussianBlur(band, (0, 0), radius)
    return out


def dither_gradient(img: np.ndarray, seed: int = 7) -> np.ndarray:
    """Другой дизеринг градиента — классика «математически видно, глазом нет»."""
    rng = np.random.default_rng(seed)
    out = img.astype(np.int16)
    grad = np.linspace(0, 6, out.shape[0], dtype=np.float32)[:, None, None]
    out = out + grad + rng.integers(-2, 3, out.shape)
    return np.clip(out, 0, 255).astype(np.uint8)


def scrollbar(img: np.ndarray) -> np.ndarray:
    """Полоса прокрутки появилась справа: не регресс вёрстки, а среда."""
    out = img.copy()
    out[:, -12:] = (238, 240, 244)
    cv2.rectangle(out, (out.shape[1] - 10, 120), (out.shape[1] - 3, 460),
                  (196, 200, 208), -1)
    return out


def caret(img: np.ndarray) -> np.ndarray:
    """Мигающая каретка в поле ввода."""
    out = img.copy()
    cv2.line(out, (220, 96), (220, 122), INK, 2)
    return out


def lazy_placeholder(img: np.ndarray) -> np.ndarray:
    """Картинка товара ещё не догрузилась — серый плейсхолдер вместо неё."""
    out = img.copy()
    cy = 110 + 320
    out[cy + 24:cy + 156, 60:190] = (238, 240, 244)
    return out


def subpixel_text(img: np.ndarray, amount: float = 0.25) -> np.ndarray:
    """Субпиксельный сдвиг только текстовых строк, а не всей страницы."""
    out = img.copy()
    y = 110
    band = out[y + 30:y + 290, 30:700]
    if band.size:
        m = np.float32([[1, 0, amount], [0, 1, 0]])
        out[y + 30:y + 290, 30:700] = cv2.warpAffine(
            band, m, (band.shape[1], band.shape[0]),
            flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    return out


# --------------------------------------------------------------------------- #
#  Дополнительные регрессы
# --------------------------------------------------------------------------- #
def regress_price_changed(img: np.ndarray) -> np.ndarray:
    """Цена другая — маленькая площадь, большая цена ошибки."""
    out = page()
    cy = 110 + 320
    cv2.rectangle(out, (215, cy + 84), (470, cy + 116), BG, -1)
    cv2.putText(out, "259 990 RUB", (215, cy + 104), 0, 0.6, (90, 96, 108), 1,
                cv2.LINE_AA)
    return out


def regress_button_shrunk(img: np.ndarray) -> np.ndarray:
    """Кнопка стала уже: изменился размер, а не цвет и не позиция."""
    out = page()
    cy = 110 + 320
    by = cy + 220
    out[by:by + 52, 40:300] = BG
    cv2.rectangle(out, (40, by), (190, by + 52), ACCENT, -1)
    cv2.putText(out, "Buy", (72, by + 34), 0, 0.65, (255, 255, 255), 2, cv2.LINE_AA)
    return out


def regress_text_overflow(img: np.ndarray) -> np.ndarray:
    """Текст вылез за карточку — типичный результат смены шрифта в вёрстке."""
    out = page()
    cy = 110 + 320
    cv2.putText(out, "Nikon Z6 II Body Kit Pro Max", (215, cy + 60), 0, 0.7,
                INK, 2, cv2.LINE_AA)
    return out


def regress_header_color(img: np.ndarray) -> np.ndarray:
    """Шапка сменила цвет: плоская заливка, структура та же."""
    out = page()
    cv2.rectangle(out, (0, 0), (W, 64), (232, 238, 250), -1)
    cv2.line(out, (0, 64), (W, 64), (225, 228, 234), 1)
    cv2.putText(out, "SHOP", (28, 42), 0, 0.9, INK, 2, cv2.LINE_AA)
    for i, item in enumerate(["Catalog", "Cart", "Account"]):
        cv2.putText(out, item, (330 + i * 130, 40), 0, 0.55, (90, 96, 108), 1,
                    cv2.LINE_AA)
    return out


def regress_promo_text(img: np.ndarray) -> np.ndarray:
    """Промо-код другой: одна строка текста, но это содержательная ошибка."""
    out = page()
    cy = 110 + 320
    py = cy + 220 + 90
    cv2.rectangle(out, (60, py + 70), (500, py + 100), (255, 248, 225), -1)
    cv2.putText(out, "Promo code: MONDAY01", (64, py + 88), 0, 0.5,
                (160, 130, 50), 1, cv2.LINE_AA)
    return out


# --------------------------------------------------------------------------- #
#  Параметризованные вёрстки
#
#  Одна страница — одна вёрстка, один шрифт, одна палитра. Модель, обученная на
#  ней, знает не «как выглядит шум», а «как выглядит шум вот на этой картинке».
#  Здесь страница становится параметром: палитра, масштаб текста, плотность
#  строк, наличие блоков. Виды шума и регрессов перемножаются на вёрстки, и
#  корпус из списка превращается в генератор.
# --------------------------------------------------------------------------- #
from dataclasses import dataclass  # noqa: E402
from dataclasses import replace as _replace  # noqa: E402


@dataclass(frozen=True)
class Layout:
    """Параметры страницы. Всё, что отличает один макет от другого."""

    width: int = W
    height: int = H
    bg: tuple = BG
    ink: tuple = INK
    accent: tuple = ACCENT
    font_scale: float = 1.0
    rows: int = 9                  # абзацев текста
    row_gap: int = 26
    card: bool = True
    promo: bool = True
    header_bg: tuple = (255, 255, 255)
    name: str = "default"


def layouts(count: int = 6) -> list[Layout]:
    """Детерминированный набор непохожих друг на друга макетов."""
    palettes = [
        ((250, 250, 252), (33, 37, 41), (52, 120, 246), (255, 255, 255)),
        ((255, 255, 255), (24, 24, 27), (16, 163, 127), (248, 250, 252)),
        ((243, 244, 246), (17, 24, 39), (220, 38, 38), (255, 255, 255)),
        ((250, 247, 240), (41, 37, 36), (120, 53, 190), (253, 250, 245)),
        ((248, 250, 252), (15, 23, 42), (234, 88, 12), (241, 245, 249)),
        ((255, 252, 250), (28, 25, 23), (13, 148, 136), (255, 255, 255)),
    ]
    out = []
    for i in range(count):
        bg, ink, accent, header = palettes[i % len(palettes)]
        out.append(Layout(
            width=W if i % 3 else 1180,
            height=H if i % 2 else 980,
            bg=bg, ink=ink, accent=accent, header_bg=header,
            font_scale=[1.0, 0.85, 1.15, 1.0, 0.9, 1.1][i % 6],
            rows=[9, 5, 13, 7, 11, 6][i % 6],
            row_gap=[26, 22, 30, 24, 28, 20][i % 6],
            card=i % 4 != 3,
            promo=i % 5 != 4,
            name=f"layout{i}",
        ))
    return out


def render(lay: Layout, *, button_color=None, promo=None, y_offset=0,
           extra_height=0, title="Оформление заказа", card_title="Nikon Z6 II",
           price="159 990 RUB", promo_code="FRIDAY24",
           button_width=260, button_text="Buy now") -> np.ndarray:
    """Отрисовать макет. Все содержательные строки — параметры.

    Регрессы делаются подстановкой другого значения в тот же рендер, а не
    правкой пикселей поверх: так изменение получается таким же, каким его сделал
    бы фронтенд, а не аппликацией.
    """
    w = lay.width
    h = lay.height + extra_height
    img = np.zeros((h, w, 3), np.uint8)
    img[:] = lay.bg
    fs = lay.font_scale
    accent = lay.accent if button_color is None else button_color
    show_promo = lay.promo if promo is None else promo

    cv2.rectangle(img, (0, 0), (w, 64), lay.header_bg, -1)
    cv2.line(img, (0, 64), (w, 64), (225, 228, 234), 1)
    cv2.putText(img, "SHOP", (28, 42), 0, 0.9 * fs, lay.ink, 2, cv2.LINE_AA)
    for i, item in enumerate(["Catalog", "Cart", "Account"]):
        cv2.putText(img, item, (330 + i * 130, 40), 0, 0.55 * fs,
                    (90, 96, 108), 1, cv2.LINE_AA)

    y = 110 + y_offset
    cv2.putText(img, title, (40, y), 0, 0.95 * fs, lay.ink, 2, cv2.LINE_AA)

    for row in range(lay.rows):
        yy = y + 44 + row * lay.row_gap
        width = min(w - 90, 640 - (row % 3) * 90)
        cv2.rectangle(img, (40, yy), (40 + width, yy + 11), (198, 203, 212), -1)

    cy = y + 44 + lay.rows * lay.row_gap + 40
    if lay.card:
        cv2.rectangle(img, (40, cy), (560, cy + 180), (255, 255, 255), -1)
        cv2.rectangle(img, (40, cy), (560, cy + 180), (222, 226, 233), 1)
        cv2.rectangle(img, (60, cy + 24), (190, cy + 156), (232, 236, 242), -1)
        cv2.putText(img, card_title, (215, cy + 60), 0, 0.7 * fs, lay.ink, 2,
                    cv2.LINE_AA)
        cv2.putText(img, price, (215, cy + 104), 0, 0.6 * fs, (90, 96, 108), 1,
                    cv2.LINE_AA)

    by = cy + (220 if lay.card else 40)
    cv2.rectangle(img, (40, by), (40 + button_width, by + 52), accent, -1)
    cv2.putText(img, button_text, (65, by + 34), 0, 0.65 * fs,
                (255, 255, 255), 2, cv2.LINE_AA)

    if show_promo:
        py = by + 90
        right = min(w - 40, 860)
        cv2.rectangle(img, (40, py), (right, py + 120), (255, 248, 225), -1)
        cv2.rectangle(img, (40, py), (right, py + 120), (245, 216, 140), 1)
        cv2.putText(img, "Free delivery until Friday", (64, py + 52), 0,
                    0.65 * fs, (140, 108, 20), 2, cv2.LINE_AA)
        cv2.putText(img, f"Promo code: {promo_code}", (64, py + 88), 0,
                    0.5 * fs, (160, 130, 50), 1, cv2.LINE_AA)

    fy = h - 90
    cv2.rectangle(img, (0, fy), (w, h), (243, 244, 247), -1)
    cv2.putText(img, "(c) 2026 SHOP", (40, fy + 50), 0, 0.5 * fs,
                (140, 146, 158), 1, cv2.LINE_AA)
    return img


# --------------------------------------------------------------------------- #
#  Преобразования, привязанные к макету
# --------------------------------------------------------------------------- #
NOISE_TRANSFORMS = {
    "sensor noise": lambda lay, img: add_sensor_noise(img, 2.2, seed=11),
    "antialias": lambda lay, img: resample_antialias(img, 0.4),
    "jpeg q=80": lambda lay, img: recompress(img, 80),
    "shift 1px": lambda lay, img: shift(img, 1, 1),
    "shift 3px": lambda lay, img: shift(img, 3, 2),
    "gradient dither": lambda lay, img: dither_gradient(img, seed=13),
    "scrollbar": lambda lay, img: scrollbar(img),
    "caret": lambda lay, img: caret(img),
    "shadow radius": lambda lay, img: blur_shadows(img, 3),
    "subpixel text": lambda lay, img: subpixel_text(img, 0.25),
    "combined": lambda lay, img: recompress(
        add_sensor_noise(resample_antialias(shift(img, 1, 0)), 1.2), 90),
    "font weight": lambda lay, img: render(_replace(lay, font_scale=lay.font_scale * 1.02)),
}

SIGNAL_TRANSFORMS = {
    "button color": lambda lay, img: render(lay, button_color=(150, 152, 158)),
    "button shrunk": lambda lay, img: render(lay, button_width=150,
                                             button_text="Buy"),
    "promo removed": lambda lay, img: render(lay, promo=False),
    "layout moved": lambda lay, img: render(lay, y_offset=40),
    "page taller": lambda lay, img: render(lay, extra_height=160),
    "price changed": lambda lay, img: render(lay, price="259 990 RUB"),
    "promo code": lambda lay, img: render(lay, promo_code="MONDAY01"),
    "card title": lambda lay, img: render(lay, card_title="Nikon Z6 II Pro Max"),
    "header color": lambda lay, img: render(_replace(lay, header_bg=(232, 238, 250))),
    "tiny icon": lambda lay, img: regress_tiny_icon(img),
}
