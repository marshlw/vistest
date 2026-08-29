# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Привязка регионов диффа к элементам DOM.

Работает без моделей и без сети — это просто пересечение геометрии. Но по
пользе это самая заметная «умность» системы: вместо «прямоугольник в
(412, 1880) 220×48» отчёт говорит

    REMOVED  button[data-testid="checkout-submit"]  "Оформить заказ"

Правило выбора элемента: среди всех, чей bbox пересекается с регионом,
берётся самый ГЛУБОКИЙ (в дереве) с достаточным перекрытием. Глубокий —
значит конкретный: не <body>, а именно кнопка.
"""

from __future__ import annotations

import re

from ..models import ChangeKind, DiffRegion


def _coverage(region: DiffRegion, node: dict) -> float:
    """Доля площади региона, накрытая элементом."""
    rx2, ry2 = region.x + region.w, region.y + region.h
    nx2, ny2 = node["x"] + node["w"], node["y"] + node["h"]
    ix = max(0, min(rx2, nx2) - max(region.x, node["x"]))
    iy = max(0, min(ry2, ny2) - max(region.y, node["y"]))
    inter = ix * iy
    if inter == 0:
        return 0.0
    return inter / float(max(region.w * region.h, 1))


def _area_ratio(region: DiffRegion, node: dict) -> float:
    """Насколько элемент «плотно сидит» в регионе: штрафуем гигантов."""
    return (region.w * region.h) / float(max(node["w"] * node["h"], 1))


def attribute(
    regions: list[DiffRegion],
    dom_expected: dict | None,
    dom_actual: dict | None,
    *,
    min_coverage: float = 0.25,
) -> list[DiffRegion]:
    nodes: list[dict] = []
    for d in (dom_actual, dom_expected):
        if d and d.get("nodes"):
            nodes = d["nodes"]
            break
    if not nodes:
        return regions

    for r in regions:
        best, best_score = None, 0.0
        for n in nodes:
            cov = _coverage(r, n)
            if cov < min_coverage:
                continue
            # Хотим: большое покрытие, глубокий узел, сопоставимый размер.
            fit = min(1.0, _area_ratio(r, n))
            score = cov * (1.0 + 0.06 * n.get("depth", 0)) * (0.4 + 0.6 * fit)
            if n.get("interactive"):
                score *= 1.25   # кнопки и ссылки важнее контейнеров
            if score > best_score:
                best, best_score = n, score

        if best is None:
            continue
        testid = best.get("testid")
        r.selector = (f'{best["tag"]}[data-testid="{testid}"]' if testid
                      else best.get("selector"))
        label = best.get("text") or best.get("aria")
        if label:
            r.element_text = label[:60]
    return regions


# Дата и время — источник ложных диффов номер два после анти-алиасинга.
# Регион с таким текстом почти наверняка не поломка вёрстки, и говорить об
# этом надо прямо: иначе человек полчаса ищет несуществующий баг.
_DATE_HINTS = re.compile(
    r"\b\d{1,2}[.:/-]\d{1,2}([.:/-]\d{2,4})?\b"                 # 07.08.2026, 12:41
    r"|\b\d{1,2}\s*(янв|фев|мар|апр|ма[йя]|июн|июл|авг|сен|окт|ноя|дек)"
    r"|\b(янв|фев|мар|апр|ма[йя]|июн|июл|авг|сен|окт|ноя|дек)\w*\s*\d{1,2}"
    r"|\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s*\d{1,2}"
    r"|\b(понедельник|вторник|сред[аы]|четверг|пятниц[аы]|суббот[аы]|воскресенье)"
    r"|\b(mon|tue|wed|thu|fri|sat|sun)day"
    r"|\b\d+\s*(сек|мин|час|дн|нед|мес|год|лет)\w*\s*назад"
    r"|\bago\b|\bсейчас\b|\bтолько что\b",
    re.I,
)

_DATE_SELECTOR_HINTS = re.compile(
    r"date|time|clock|calendar|today|now|timestamp|updated|created|greeting",
    re.I,
)


def looks_like_datetime(region: DiffRegion) -> bool:
    text = region.element_text or ""
    if text and _DATE_HINTS.search(text):
        return True
    return bool(region.selector and _DATE_SELECTOR_HINTS.search(region.selector))


def diagnose(regions: list[DiffRegion]) -> list[str]:
    """Подсказки по причинам диффа, а не только констатация факта."""
    notes: list[str] = []

    dated = [r for r in regions if looks_like_datetime(r)]
    if dated:
        where = ", ".join(
            filter(None, [r.selector or r.element_text for r in dated[:3]])
        )
        notes.append(
            f"Looks like a date or a time ({len(dated)} region(s): {where[:120]}). "
            "This is not a broken layout. The options: turn on capture.determinism "
            "(the baseline and the run will see the same date) or mask the block — "
            'with the data-vistest="ignore" attribute, a selector in mask_selectors '
            "or an ignore zone in the UI."
        )

    tiny_moves = [r for r in regions
                  if r.kind is ChangeKind.MOVED and abs(r.moved_dx) + abs(r.moved_dy) <= 2]
    if len(tiny_moves) >= 3:
        notes.append(
            f"{len(tiny_moves)} blocks shifted by 1–2 px. Usually this is "
            "a change of font or line spacing higher up the page, "
            "not an edit to each block on its own."
        )

    return notes


def dom_changes(dom_expected: dict | None, dom_actual: dict | None) -> dict:
    """Диф по DOM: что появилось/исчезло/переехало по селекторам.

    Дополняет пиксельный дифф: изменение, скрытое под маской или за пределами
    порога, всё равно будет замечено на уровне структуры.
    """
    if not dom_expected or not dom_actual:
        return {}

    def index(d):
        out = {}
        for n in d.get("nodes", []):
            key = n.get("selector")
            if key:
                out.setdefault(key, n)
        return out

    a, b = index(dom_expected), index(dom_actual)
    added = sorted(set(b) - set(a))
    removed = sorted(set(a) - set(b))
    moved = []
    for k in set(a) & set(b):
        dx, dy = b[k]["x"] - a[k]["x"], b[k]["y"] - a[k]["y"]
        dw, dh = b[k]["w"] - a[k]["w"], b[k]["h"] - a[k]["h"]
        if abs(dx) > 2 or abs(dy) > 2 or abs(dw) > 2 or abs(dh) > 2:
            moved.append({"selector": k, "dx": dx, "dy": dy, "dw": dw, "dh": dh})

    return {
        "added": added[:50],
        "removed": removed[:50],
        "moved": sorted(moved, key=lambda m: -(abs(m["dx"]) + abs(m["dy"])))[:50],
        "counts": {"added": len(added), "removed": len(removed), "moved": len(moved)},
    }
