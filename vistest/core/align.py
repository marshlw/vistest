# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Субпиксельное выравнивание.

Самая частая причина «весь скриншот красный» — страница сдвинулась на 1–2 px
(другая высота шапки, скроллбар, округление лейаута). Пиксельное сравнение
после такого сдвига бессмысленно.

Стратегия:
  1. phaseCorrelate по L*-каналу — глобальный сдвиг с субпиксельной точностью,
     устойчив к шуму и локальным изменениям (работает в частотной области).
  2. Если сдвиг мал (< max_shift) — компенсируем варпом и сравниваем дальше.
  3. Если велик — НЕ компенсируем: это настоящий регресс лейаута, о нём надо
     сообщить, а не спрятать.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None


@dataclass
class Alignment:
    dx: float = 0.0
    dy: float = 0.0
    confidence: float = 0.0
    applied: bool = False
    reason: str = ""

    @property
    def magnitude(self) -> float:
        return float(np.hypot(self.dx, self.dy))


def estimate_shift(gray_exp: np.ndarray, gray_act: np.ndarray) -> Alignment:
    """Глобальный сдвиг actual относительно expected."""
    if cv2 is None:
        return Alignment(reason="cv2 unavailable")
    if gray_exp.shape != gray_act.shape:
        return Alignment(reason="different sizes")
    if min(gray_exp.shape[:2]) < 16:
        return Alignment(reason="image too small")

    a = gray_exp.astype(np.float32)
    b = gray_act.astype(np.float32)
    # Окно Ханна убирает краевые артефакты БПФ (иначе рамка изображения
    # сама по себе даёт сильный ложный пик).
    win = cv2.createHanningWindow((a.shape[1], a.shape[0]), cv2.CV_32F)
    (dx, dy), response = cv2.phaseCorrelate(a, b, win)
    return Alignment(dx=float(dx), dy=float(dy), confidence=float(response))


def apply_shift(img: np.ndarray, dx: float, dy: float) -> np.ndarray:
    """Сдвинуть actual на (-dx,-dy), чтобы совместить с expected.

    BORDER_REPLICATE, а не нули: чёрная кайма по краю дала бы гарантированный
    ложный дифф шириной в сдвиг.
    """
    if cv2 is None or (abs(dx) < 1e-3 and abs(dy) < 1e-3):
        return img
    m = np.array([[1.0, 0.0, -dx], [0.0, 1.0, -dy]], dtype=np.float32)
    h, w = img.shape[:2]
    return cv2.warpAffine(
        img, m, (w, h),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE,
    )


def align_images(
    rgb_exp: np.ndarray,
    rgb_act: np.ndarray,
    gray_exp: np.ndarray,
    gray_act: np.ndarray,
    *,
    enabled: bool = True,
    max_shift_px: float = 8.0,
    min_confidence: float = 0.05,
) -> tuple[np.ndarray, Alignment]:
    """Возвращает (выровненный actual, описание выравнивания)."""
    if not enabled:
        return rgb_act, Alignment(reason="disabled")

    al = estimate_shift(gray_exp, gray_act)
    if al.reason:
        return rgb_act, al
    if al.magnitude < 0.05:
        al.reason = "no shift"
        return rgb_act, al
    if al.confidence < min_confidence:
        al.reason = f"low confidence ({al.confidence:.3f})"
        return rgb_act, al
    if al.magnitude > max_shift_px:
        al.reason = (f"shift {al.magnitude:.1f}px > the {max_shift_px}px limit"
                     " — this is a layout regression")
        return rgb_act, al

    al.applied = True
    al.reason = f"compensated shift dx={al.dx:+.2f} dy={al.dy:+.2f}"
    return apply_shift(rgb_act, al.dx, al.dy), al


def find_local_shift(
    gray_exp: np.ndarray,
    gray_act: np.ndarray,
    bbox: tuple[int, int, int, int],
    *,
    search_px: int = 64,
) -> tuple[int, int, float]:
    """Ищет содержимое региона expected внутри окна поиска в actual.

    Отвечает на вопрос «этот блок изменился или просто уехал?».
    Возвращает (dx, dy, ncc). ncc близко к 1 → это MOVED, а не CONTENT.
    """
    if cv2 is None:
        return 0, 0, 0.0

    x, y, w, h = bbox
    H, W = gray_exp.shape[:2]
    # Регион почти во весь экран искать бессмысленно (и дорого), но лимит
    # в половину экрана отсекал бы легитимные крупные блоки.
    if w < 6 or h < 6 or w > W * 0.9 or h > H * 0.9:
        return 0, 0, 0.0

    patch = gray_exp[y:y + h, x:x + w]
    if patch.size == 0 or float(patch.std()) < 3.0:
        # Однородный блок совпадёт где угодно — результат бессмыслен.
        return 0, 0, 0.0

    sx0, sy0 = max(0, x - search_px), max(0, y - search_px)
    sx1, sy1 = min(W, x + w + search_px), min(H, y + h + search_px)
    window = gray_act[sy0:sy1, sx0:sx1]
    if window.shape[0] < h or window.shape[1] < w:
        return 0, 0, 0.0

    res = cv2.matchTemplate(window, patch, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(res)
    found_x, found_y = sx0 + max_loc[0], sy0 + max_loc[1]
    return int(found_x - x), int(found_y - y), float(max_val)


def reconcile_sizes(
    a: np.ndarray, b: np.ndarray, pad_value: int = 0
) -> tuple[np.ndarray, np.ndarray, bool]:
    """Приводит к общему размеру ДОПОЛНЕНИЕМ, а не обрезкой.

    Обрезка до min(h,w) — распространённая ошибка: она молча прячет самый
    частый реальный регресс (изменилась высота страницы). Дополняя, мы
    оставляем разницу видимой и в дифф-маске, и в артефактах.
    """
    if a.shape == b.shape:
        return a, b, False

    h = max(a.shape[0], b.shape[0])
    w = max(a.shape[1], b.shape[1])

    def pad(img):
        if img.shape[0] == h and img.shape[1] == w:
            return img
        pads = [(0, h - img.shape[0]), (0, w - img.shape[1])]
        if img.ndim == 3:
            pads.append((0, 0))
        return np.pad(img, pads, mode="constant", constant_values=pad_value)

    return pad(a), pad(b), True
