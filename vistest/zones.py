# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Зоны игнорирования: «не сравнивай ЭТОТ блок», а не «этот прямоугольник».

Зона игнорирования была четырьмя числами: `{x: 340, y: 120, w: 200, h: 80}`.
Работает это ровно до первого переноса блока — а переносится он как раз тогда,
когда идёт редизайн, то есть когда масок больше всего и ошибиться дороже всего.

Ломается при этом дважды, и оба раза тихо. Прямоугольник остаётся на прежнем
месте: там теперь другой элемент, и его изменения перестают проверяться —
маска, поставленная на аватарку, глушит цену. А блок, ради которого маску и
ставили, уезжает из-под неё и начинает красить прогон каждый день. Человек
видит и то и другое как «VisTest врёт».

Здесь зона привязывается к ЭЛЕМЕНТУ. Координаты при этом никуда не деваются —
они остаются запасным вариантом, и в этом весь смысл конструкции:

* есть DOM и элемент нашёлся — маска встаёт туда, где элемент сейчас;
* DOM нет (старое сравнение, чужой набор PNG, вызов без снепшота) — работают
  координаты, ровно как раньше;
* элемент пропал — работают координаты, и об этом **говорится вслух**, потому
  что молча переставшая работать маска не отличима от работающей.

Обратной совместимости здесь не «стараемся не сломать», а по построению: зона
старого формата — это зона без селектора, и путь для неё тот же самый, что был.

--------------------------------------------------------------------------
Как элемент опознаётся

CSS-движка у нас нет: на руках JSON-снепшот из `capture/dom.py`, где у каждого
видимого узла лежат геометрия, `selector`, `id`, `testid`, тег и классы.
Поэтому опознание — лесенка приоритетов, а не «выполнить селектор»:

  1. `data-testid` — самое устойчивое, что бывает в вёрстке: он ставится
     руками и ровно для того, чтобы за элемент можно было зацепиться;
  2. `id` — почти так же устойчив, но чаще генерируется;
  3. точный `selector` — путь целиком, ломается от любой перестройки дерева;
  4. тег и классы — последний рубеж, срабатывает на всех похожих сразу.

Первый уровень, давший хоть одно совпадение, побеждает: смешивать `testid` с
совпадением по классам значило бы к точному попаданию добавлять случайные.

Совпадений может быть несколько, и это не ошибка. Карусель из пяти слайдов,
список аватарок, строка с рекламой в каждой карточке — маскируются все, и
именно этого от «не сравнивай такие блоки» и ждут.

--------------------------------------------------------------------------
Почему маскируются ОБА положения

Маска накладывается на дифф, а дифф живёт в координатах текущего кадра, куда
эталон приводится выравниванием. Если элемент переехал, различия появляются в
двух местах: там, где он был на эталоне, и там, где он теперь. Замаскировать
только новое положение значит оставить след старого — красный прямоугольник на
пустом месте, объяснить который нельзя ничем.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Рамки элемента впритык, а сглаживание на границе даёт различие на пиксель-два
# наружу. Пара пикселей запаса дешевле, чем регулярное «маска стоит, а тонкая
# полоска по краю всё равно красная».
PAD_PX = 2

# Ключи опознания, в порядке убывания устойчивости. Порядок здесь — и есть
# лесенка приоритетов из заголовка файла.
IDENTITY = ("testid", "id", "selector", "cls")


@dataclass
class Resolved:
    """Что получилось из списка зон."""

    boxes: list[dict] = field(default_factory=list)
    # Зоны, у которых селектор перестал находиться. Работают по координатам, но
    # человеку про это надо сказать: маска, тихо переставшая ловить свою цель,
    # не отличима от работающей.
    lost: list[dict] = field(default_factory=list)
    # Сколько зон встали по элементу, а не по координатам.
    by_selector: int = 0

    @property
    def notes(self) -> list[str]:
        out = []
        for z in self.lost:
            out.append(
                f"the ignore zone «{z.get('selector') or z.get('reason') or 'unnamed'}»"
                " no longer finds its element — it is holding by coordinates,"
                " which stop meaning the same thing as soon as the layout moves")
        return out


# --------------------------------------------------------------------------- #
#  Формат
# --------------------------------------------------------------------------- #
def normalize(raw) -> dict:
    """Одна зона к каноническому виду. Координаты обязательны всегда.

    Даже у зоны с селектором: элемент может исчезнуть, и тогда единственное, что
    остаётся, — прямоугольник, где он был. Зона без запасных координат в этот
    момент превратилась бы в ничто, и маска исчезла бы молча.
    """
    if not isinstance(raw, dict):
        raise ValueError("a zone must be an object")
    try:
        zone = {k: int(raw[k]) for k in ("x", "y", "w", "h")}
    except (KeyError, TypeError, ValueError):
        raise ValueError("x, y, w and h are required, as integers") from None
    if zone["w"] <= 0 or zone["h"] <= 0:
        raise ValueError("a zone must have a non-zero size")
    if zone["x"] < 0 or zone["y"] < 0:
        raise ValueError("a zone starts inside the frame")

    selector = str(raw.get("selector") or "").strip()
    if selector:
        zone["selector"] = selector[:400]
    match = raw.get("match")
    if isinstance(match, dict):
        clean = {k: (str(v)[:200] if v not in (None, "") else None)
                 for k, v in match.items() if k in IDENTITY}
        clean = {k: v for k, v in clean.items() if v}
        if clean:
            zone["match"] = clean
    if raw.get("reason"):
        zone["reason"] = str(raw["reason"])[:200]
    return zone


def normalize_all(raw) -> list[dict]:
    if not isinstance(raw, list):
        raise ValueError("boxes must be a list")
    out = []
    for i, item in enumerate(raw):
        # Формулировки здесь — то, что человек прочитает в тосте, и менять их
        # без нужды не стоит: они уже описаны тестами и уже кем-то видены.
        if not isinstance(item, dict):
            raise ValueError(f"boxes[{i}] is not an object")
        try:
            out.append(normalize(item))
        except ValueError as e:
            raise ValueError(f"boxes[{i}]: {e}") from None
    return out


def held_by(zone: dict) -> str:
    """Чем зона держится — одним словом, для интерфейса и отчётов."""
    return "element" if (zone.get("selector") or zone.get("match")) else "coordinates"


# --------------------------------------------------------------------------- #
#  Опознание
# --------------------------------------------------------------------------- #
def _classes(value) -> list[str]:
    return [c for c in str(value or "").split() if c]


def _level_of(zone: dict) -> list[tuple[str, str]]:
    """Признаки зоны по лесенке приоритетов, сверху вниз."""
    match = zone.get("match") or {}
    out = []
    for key in IDENTITY:
        value = match.get(key) or (zone.get("selector") if key == "selector" else None)
        if value:
            out.append((key, value))
    return out


def _node_matches(node: dict, key: str, value: str) -> bool:
    if key == "testid":
        return bool(node.get("testid")) and node["testid"] == value
    if key == "id":
        return bool(node.get("id")) and node["id"] == value
    if key == "selector":
        return node.get("selector") == value
    if key == "cls":
        # Классы зоны обязаны найтись ВСЕ, а лишние у узла допустимы: вёрстка
        # добавляет модификаторы (`is-open`, `theme-dark`), и требовать
        # побайтового совпадения строки классов значило бы терять цель от
        # переключения темы.
        want = set(_classes(value))
        return bool(want) and want <= set(_classes(node.get("cls")))
    return False


def find_nodes(dom, zone: dict) -> list[dict]:
    """Узлы снепшота, которыми зона считает себя.

    Возвращается первый непустой уровень лесенки. Несколько узлов — норма:
    карусель, список аватарок, реклама в каждой карточке.
    """
    nodes = (dom or {}).get("nodes") or []
    if not nodes:
        return []
    for key, value in _level_of(zone):
        hit = [n for n in nodes if _node_matches(n, key, value)]
        if hit:
            return hit
    return []


def _box_of(node: dict) -> dict | None:
    try:
        x, y = int(node["x"]), int(node["y"])
        w, h = int(node["w"]), int(node["h"])
    except (KeyError, TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    return {"x": max(0, x - PAD_PX), "y": max(0, y - PAD_PX),
            "w": w + PAD_PX * 2, "h": h + PAD_PX * 2}


# --------------------------------------------------------------------------- #
#  Разрешение
# --------------------------------------------------------------------------- #
def resolve(zones, *, baseline_dom=None, actual_dom=None) -> Resolved:
    """Зоны → прямоугольники, которые надо погасить в этом сравнении.

    `baseline_dom` и `actual_dom` — снепшоты эталона и текущего кадра. Оба
    необязательны: без них зона работает по координатам, как работала всегда.
    Оба вместе — потому что переехавший элемент даёт различия в двух местах
    сразу (см. заголовок файла).
    """
    out = Resolved()
    for zone in (zones or []):
        if not isinstance(zone, dict):
            continue
        fallback = {k: int(zone.get(k) or 0) for k in ("x", "y", "w", "h")}
        if held_by(zone) == "coordinates":
            out.boxes.append(fallback)
            continue

        found = []
        for dom in (actual_dom, baseline_dom):
            for node in find_nodes(dom, zone):
                box = _box_of(node)
                if box and box not in found:
                    found.append(box)

        if found:
            out.boxes.extend(found)
            out.by_selector += 1
        else:
            # Цель потеряна. Координаты — не «тоже вариант», а последнее, что
            # осталось: они описывают, где элемент БЫЛ. Прогон от этого не
            # покраснеет, но и правдой это быть перестало, поэтому зона едет в
            # `lost`, а оттуда — человеку в заметки сравнения и на страницу
            # снимка.
            out.boxes.append(fallback)
            out.lost.append(zone)
    return out


def mask(shape, zones, *, baseline_dom=None, actual_dom=None):
    """Готовая bool-маска кадра. Обёртка над `resolve` + `noise.mask_from_boxes`."""
    from .core import noise as _noise

    res = resolve(zones, baseline_dom=baseline_dom, actual_dom=actual_dom)
    return _noise.mask_from_boxes(shape, res.boxes), res
