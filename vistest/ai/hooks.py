# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Точка расширения для внешнего аннотатора регионов.

В движке три AI-слоя и намеренно нет четвёртого. attribution, обучаемый гейт и
перцептивный фильтр работают офлайн, на CPU, и все трое умеют только подавлять.
Модель, которая смотрит на картинки и что-то про них говорит — vision-LLM,
локально поднятая VLM, классификатор триажа — в движок не входит: вердикт
принимает человек на ревью.

Здесь она подключается, если она кому-то понадобилась. Аннотатор регистрируется
из кода, а не из конфигурации. Путь к классу строкой в vistest.yaml или в
таблице настроек превратил бы «поменять настройку» в «выполнить произвольный
код», а таблица настроек пишется по HTTP.

Контракт — один метод и одно правило.

    class MyAnnotator:
        def annotate(self, regions, expected, actual, result) -> None:
            for r in regions:
                r.caption = "..."

    from vistest.ai import set_annotator
    set_annotator(MyAnnotator())

Правило: аннотатор добавляет слова, но не меняет исход. Ему принадлежат
`region.caption` и `result.notes`. Всё, что он сделает с `kind`, `severity` или
`suppressed_by`, пайплайн откатит до того, как результат уйдёт наружу — см.
`AIPipeline._apply_annotator`. Это не вопрос доверия: гейту разрешено подавлять,
потому что ложное подавление ограничено и видно в отчёте, а выдуманное падение —
нет; у говорящей модели обосновать его получится ещё хуже.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import numpy as np

from ..models import CompareResult, DiffRegion


@runtime_checkable
class RegionAnnotator(Protocol):
    """Описывает регионы словами. Не бросает исключений, не выносит вердикт."""

    def annotate(
        self,
        regions: list[DiffRegion],
        expected: np.ndarray,
        actual: np.ndarray,
        result: CompareResult,
    ) -> None:
        ...


_annotator: RegionAnnotator | None = None


def set_annotator(annotator: RegionAnnotator | None) -> None:
    """Зарегистрировать аннотатор на процесс. `None` — снять."""
    global _annotator
    _annotator = annotator


def get_annotator() -> RegionAnnotator | None:
    return _annotator
