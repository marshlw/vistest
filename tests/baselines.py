# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Эталонные конкуренты для бенчмарка.

Здесь важно быть честным до занудства, потому что цифру мы собираемся
публиковать.

**Что здесь настоящее.** `absdiff` — это и есть весь алгоритм самописных
скриптов, воспроизводить нечего.

**Что здесь порт.** `pixelmatch_numpy` — перенос ядра pixelmatch (цветовая
дельта в YIQ и порог `35215·t²`) на numpy. Детектор анти-алиасинга из
оригинала **не портирован**: он смотрит на восемь соседей каждого пикселя и
ошибка в переносе тихо испортила бы результат в нашу пользу. Поэтому порт
считает так же, как pixelmatch с `includeAA: true`, и в таблице помечается
как порт.

**Что настоящее и как это проверить.** Числа, которые публикуются, берутся
из нативного прогона: `node scripts/bench_pixelmatch.mjs <корпус>` запускает
подлинный npm-пакет pixelmatch на тех же PNG. Порт нужен только чтобы
бенчмарк что-то показывал без Node — и в таблице это видно.

Playwright здесь отдельной строкой не потому, что у него другой движок
(движок тот же pixelmatch), а потому, что у него другая **политика**:
`toHaveScreenshot()` по умолчанию валит тест, если отличается хотя бы один
пиксель. Политика решает не меньше, чем алгоритм.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np


@dataclass
class BaselineResult:
    failed: bool
    diff_pixels: int
    total_pixels: int

    @property
    def diff_ratio(self) -> float:
        return self.diff_pixels / max(self.total_pixels, 1)


def _reconcile(a: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, np.ndarray, bool]:
    """Разный размер — у всех сравниваемых инструментов это сразу «не равно».

    pixelmatch на разных размерах бросает исключение, Playwright валит тест,
    resemble обрезает. Единая трактовка: несовпадение размера = падение.
    """
    if a.shape == b.shape:
        return a, b, False
    h = min(a.shape[0], b.shape[0])
    w = min(a.shape[1], b.shape[1])
    return a[:h, :w], b[:h, :w], True


# --------------------------------------------------------------------------- #
#  Наивный absdiff — то, что пишут руками
# --------------------------------------------------------------------------- #
def absdiff(expected: np.ndarray, actual: np.ndarray,
            *, tolerance: int = 0) -> BaselineResult:
    """Классика самописа: «если пиксели не совпали — тест красный»."""
    a, b, size_changed = _reconcile(expected, actual)
    delta = np.abs(a.astype(np.int16) - b.astype(np.int16)).max(axis=2)
    diff = int((delta > tolerance).sum())
    return BaselineResult(failed=size_changed or diff > 0,
                          diff_pixels=diff, total_pixels=a.shape[0] * a.shape[1])


# --------------------------------------------------------------------------- #
#  pixelmatch — порт ядра, без AA-детектора
# --------------------------------------------------------------------------- #
_MAX_DELTA_AT_1 = 35215.0   # максимально возможная YIQ-дельта в pixelmatch


def _yiq(img: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    f = img.astype(np.float64)
    r, g, b = f[..., 0], f[..., 1], f[..., 2]
    y = r * 0.29889531 + g * 0.58662247 + b * 0.11448223
    i = r * 0.59597799 - g * 0.27417610 - b * 0.32180189
    q = r * 0.21147017 - g * 0.52261711 + b * 0.31114694
    return y, i, q


def pixelmatch_delta(expected: np.ndarray, actual: np.ndarray) -> np.ndarray:
    """Карта цветовой дельты pixelmatch (та же формула, что в оригинале)."""
    y1, i1, q1 = _yiq(expected)
    y2, i2, q2 = _yiq(actual)
    dy, di, dq = y1 - y2, i1 - i2, q1 - q2
    return 0.5053 * dy * dy + 0.299 * di * di + 0.1957 * dq * dq


def pixelmatch_numpy(expected: np.ndarray, actual: np.ndarray,
                     *, threshold: float = 0.1,
                     max_diff_pixels: int | None = 0,
                     max_diff_ratio: float | None = None) -> BaselineResult:
    """Порт ядра pixelmatch. AA-детектор не портирован — см. модульный docstring.

    threshold        — как в pixelmatch (0.1 по умолчанию)
    max_diff_pixels  — сколько отличий терпим; 0 = любое отличие валит
    max_diff_ratio   — то же в долях; если задано, имеет приоритет
    """
    a, b, size_changed = _reconcile(expected, actual)
    total = a.shape[0] * a.shape[1]
    delta = pixelmatch_delta(a, b)
    diff = int((delta > _MAX_DELTA_AT_1 * threshold * threshold).sum())

    if size_changed:
        return BaselineResult(True, diff, total)
    if max_diff_ratio is not None:
        return BaselineResult(diff / max(total, 1) > max_diff_ratio, diff, total)
    return BaselineResult(diff > (max_diff_pixels or 0), diff, total)


# --------------------------------------------------------------------------- #
#  Политика Playwright toHaveScreenshot()
# --------------------------------------------------------------------------- #
def playwright_screenshot(expected: np.ndarray, actual: np.ndarray,
                          *, threshold: float = 0.2,
                          max_diff_pixels: int = 0) -> BaselineResult:
    """`expect(page).toHaveScreenshot()` со значениями по умолчанию.

    Движок — pixelmatch с `threshold: 0.2`. Ключевое отличие от голого
    pixelmatch не в алгоритме, а в политике: `maxDiffPixels` по умолчанию не
    задан, то есть **один непрощённый пиксель роняет тест**. Отсюда и берётся
    репутация встроенных снапшотов как «сначала включили, через месяц
    выключили».
    """
    return pixelmatch_numpy(expected, actual, threshold=threshold,
                            max_diff_pixels=max_diff_pixels)


# --------------------------------------------------------------------------- #
#  Реестр
# --------------------------------------------------------------------------- #
@dataclass
class Engine:
    key: str
    title: str
    native: bool           # False = наш порт, а не оригинальный код
    run: Callable[[np.ndarray, np.ndarray], BaselineResult]
    note: str = ""


ENGINES: list[Engine] = [
    Engine("absdiff", "absdiff (самопис)", True, absdiff,
           "любой отличающийся пиксель валит тест"),
    Engine("pixelmatch", "pixelmatch t=0.1", False,
           lambda e, a: pixelmatch_numpy(e, a, threshold=0.1),
           "порт ядра, без AA-детектора → сверять с node"),
    Engine("playwright", "Playwright toHaveScreenshot()", False,
           playwright_screenshot,
           "pixelmatch t=0.2, падение от одного пикселя"),
]


def by_key(key: str) -> Engine | None:
    return next((e for e in ENGINES if e.key == key), None)
