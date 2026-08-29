# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Подавление субпиксельного анти-алиасинга.

Главный источник ложных срабатываний в реальных проектах. Один и тот же
текст, отрендеренный дважды, даёт границы букв, отличающиеся на десятки
единиц яркости — из-за subpixel hinting, GPU-растеризации, дробного DPR.

Критерий (обобщение эвристики pixelmatch, векторизованное):

    Пиксель — анти-алиасинг, если его новое значение лежит ВНУТРИ диапазона
    значений соседей 3×3 в другом изображении (и наоборот), И вокруг него
    действительно есть градиент.

Почему это верно:
  * АА-пиксель — результат смешения цветов границы, поэтому его значение
    всегда между цветами по обе стороны границы, то есть внутри локального
    диапазона.
  * Реальное изменение (кнопка сменила цвет, текст стал другим) даёт
    значение ВНЕ локального диапазона — такой пиксель не подавляется.
  * На плоской заливке min == max, диапазон нулевой → ничего не подавляется.
    То есть фильтр физически не может спрятать изменение цвета фона.
"""

from __future__ import annotations

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

_K3 = np.ones((3, 3), np.uint8)


def antialias_mask(
    gray_exp: np.ndarray,
    gray_act: np.ndarray,
    *,
    tolerance: float = 0.10,
    min_gradient: float = 12.0,
) -> np.ndarray:
    """bool-маска пикселей, объяснимых анти-алиасингом."""
    if cv2 is None:
        return np.zeros(gray_exp.shape, dtype=bool)

    a = gray_exp
    b = gray_act
    tol = np.float32(tolerance * 255.0)

    lo_a = cv2.erode(a, _K3).astype(np.float32)
    hi_a = cv2.dilate(a, _K3).astype(np.float32)
    lo_b = cv2.erode(b, _K3).astype(np.float32)
    hi_b = cv2.dilate(b, _K3).astype(np.float32)

    af = a.astype(np.float32)
    bf = b.astype(np.float32)

    # actual объясним соседями expected и наоборот — симметричность важна,
    # иначе исчезнувший тонкий элемент был бы принят за АА.
    b_in_a = (bf >= lo_a - tol) & (bf <= hi_a + tol)
    a_in_b = (af >= lo_b - tol) & (af <= hi_b + tol)

    # Подавляем только там, где есть настоящая граница.
    gradient = np.maximum(hi_a - lo_a, hi_b - lo_b)
    on_edge = gradient >= min_gradient

    return b_in_a & a_in_b & on_edge


def text_shift_mask(
    gray_exp: np.ndarray,
    gray_act: np.ndarray,
    *,
    radius: int = 1,
    tolerance: float = 10.0,
) -> np.ndarray:
    """Пиксели, совпадающие со сдвигом на ±radius.

    Ловит «текст переехал на пиксель из-за другого кернинга»: значение пикселя
    в actual встречается где-то в окне radius в expected. В отличие от
    глобального выравнивания работает локально — для отдельных строк текста.
    """
    if cv2 is None:
        return np.zeros(gray_exp.shape, dtype=bool)

    k = 2 * radius + 1
    kernel = np.ones((k, k), np.uint8)
    lo = cv2.erode(gray_exp, kernel).astype(np.float32) - tolerance
    hi = cv2.dilate(gray_exp, kernel).astype(np.float32) + tolerance
    bf = gray_act.astype(np.float32)

    lo2 = cv2.erode(gray_act, kernel).astype(np.float32) - tolerance
    hi2 = cv2.dilate(gray_act, kernel).astype(np.float32) + tolerance
    af = gray_exp.astype(np.float32)

    return ((bf >= lo) & (bf <= hi)) & ((af >= lo2) & (af <= hi2))
