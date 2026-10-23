# VisTest - visual regression testing for Python and Playwright.
# Copyright 2026 Kirill Kulagin
# SPDX-License-Identifier: Apache-2.0
#
# This file is part of VisTest, licensed under the Apache License, Version 2.0.
# You may not use it except in compliance with the License. See LICENSE for
# the terms and NOTICE for attribution and the trademark.

"""Зоны игнорирования, привязанные к элементу.

Зона из четырёх чисел ломается при первом же переносе блока — а переносится он
как раз во время редизайна, то есть когда масок больше всего. Ломается дважды и
оба раза тихо: прямоугольник остаётся на месте и глушит то, что туда переехало,
а блок, ради которого маску ставили, уезжает из-под неё и красит прогон каждый
день.

Здесь проверяется, что зона переезжает вместе с элементом, что при этом ничего
не сломалось у зон старого формата, и что потерянная цель не проходит молча.
"""

from __future__ import annotations

import numpy as np
import pytest

from vistest import zones


def _dom(*nodes) -> dict:
    return {"nodes": [{"x": 0, "y": 0, "w": 10, "h": 10, **n} for n in nodes]}


AVATAR = {"x": 800, "y": 40, "w": 60, "h": 60, "tag": "img",
          "testid": "user-avatar", "id": None, "cls": "avatar round",
          "selector": "header.site > img.avatar"}


# --------------------------------------------------------------------------- #
#  Формат
# --------------------------------------------------------------------------- #
def test_coordinates_are_required_even_with_a_selector():
    """Элемент может исчезнуть — и тогда прямоугольник всё, что от зоны осталось.

    Зона без запасных координат в этот момент превратилась бы в ничто, и маска
    пропала бы молча.
    """
    with pytest.raises(ValueError, match="required"):
        zones.normalize({"selector": "div.avatar"})


@pytest.mark.parametrize("bad,expect", [
    ({"x": 1, "y": 1, "w": 0, "h": 5}, "non-zero"),
    ({"x": -1, "y": 1, "w": 5, "h": 5}, "inside the frame"),
    ({"x": 1, "y": 1, "w": 5}, "required"),
])
def test_a_nonsense_zone_is_refused_by_name(bad, expect):
    with pytest.raises(ValueError, match=expect):
        zones.normalize(bad)


def test_the_old_format_passes_through_untouched():
    """Зона старого формата — это зона без селектора, и путь у неё прежний."""
    z = zones.normalize({"x": 5, "y": 6, "w": 7, "h": 8})
    assert z == {"x": 5, "y": 6, "w": 7, "h": 8}
    assert zones.held_by(z) == "coordinates"


# --------------------------------------------------------------------------- #
#  Опознание
# --------------------------------------------------------------------------- #
def test_a_zone_follows_its_element_when_the_layout_moves():
    """Ради этого всё и делалось."""
    z = zones.normalize({"x": 800, "y": 40, "w": 60, "h": 60,
                         "selector": "header.site > img.avatar",
                         "match": {"testid": "user-avatar"}})
    moved = _dom({**AVATAR, "x": 300, "y": 900})

    res = zones.resolve([z], actual_dom=moved)
    assert res.by_selector == 1 and not res.lost
    box = res.boxes[0]
    assert (box["x"], box["y"]) == (300 - zones.PAD_PX, 900 - zones.PAD_PX)


def test_both_positions_are_masked_when_the_element_moved():
    """Дифф живёт в координатах текущего кадра, и переехавший элемент даёт два следа.

    Замаскировать только новое положение — значит оставить красный
    прямоугольник на пустом месте, объяснить который нельзя ничем.
    """
    z = zones.normalize({"x": 1, "y": 1, "w": 5, "h": 5,
                         "match": {"testid": "user-avatar"}})
    res = zones.resolve([z],
                        baseline_dom=_dom({**AVATAR, "x": 800, "y": 40}),
                        actual_dom=_dom({**AVATAR, "x": 300, "y": 900}))
    tops = sorted(b["y"] for b in res.boxes)
    assert len(res.boxes) == 2 and tops == [40 - zones.PAD_PX, 900 - zones.PAD_PX]


def test_testid_beats_a_css_path():
    """Путь ломается от любой перестройки дерева, `data-testid` — нет.

    Смешивать уровни нельзя: к точному попаданию добавились бы случайные.
    """
    z = zones.normalize({"x": 1, "y": 1, "w": 5, "h": 5,
                         "selector": "header.site > img.avatar",
                         "match": {"testid": "user-avatar"}})
    # Путь изменился (блок переехал в другого родителя), testid остался.
    dom = _dom({**AVATAR, "selector": "main > aside > img.avatar",
                "x": 10, "y": 20})
    res = zones.resolve([z], actual_dom=dom)
    assert res.by_selector == 1 and res.boxes[0]["x"] == 10 - zones.PAD_PX


def test_extra_classes_do_not_lose_the_element():
    """Вёрстка дописывает модификаторы — `is-open`, `theme-dark`.

    Побайтовое совпадение строки классов теряло бы цель от переключения темы.
    """
    z = zones.normalize({"x": 1, "y": 1, "w": 5, "h": 5,
                         "match": {"cls": "avatar"}})
    dom = _dom({**AVATAR, "testid": None, "id": None,
                "cls": "avatar round theme-dark", "x": 7, "y": 7})
    assert zones.resolve([z], actual_dom=dom).by_selector == 1


def test_several_matches_are_all_masked():
    """Карусель, список аватарок, реклама в каждой карточке — маскируются все."""
    z = zones.normalize({"x": 1, "y": 1, "w": 5, "h": 5,
                         "match": {"cls": "slide"}})
    dom = _dom({"cls": "slide", "x": 0, "y": 0, "w": 50, "h": 50},
               {"cls": "slide", "x": 60, "y": 0, "w": 50, "h": 50},
               {"cls": "slide", "x": 120, "y": 0, "w": 50, "h": 50})
    assert len(zones.resolve([z], actual_dom=dom).boxes) == 3


# --------------------------------------------------------------------------- #
#  Потерянная цель
# --------------------------------------------------------------------------- #
def test_a_lost_element_falls_back_to_coordinates_and_says_so():
    """Маска, тихо переставшая работать, не отличима от работающей.

    Ни на картинке, ни по вердикту. Поэтому она говорит о себе сама.
    """
    z = zones.normalize({"x": 800, "y": 40, "w": 60, "h": 60,
                         "selector": "header.site > img.avatar",
                         "match": {"testid": "user-avatar"}})
    res = zones.resolve([z], actual_dom=_dom({"cls": "something-else"}))
    assert res.boxes == [{"x": 800, "y": 40, "w": 60, "h": 60}]
    assert len(res.lost) == 1
    assert "no longer finds" in res.notes[0]


def test_without_a_dom_a_zone_just_works_by_coordinates():
    """Старое сравнение, чужой набор PNG, вызов без снепшота — это не потеря цели.

    Врать про такую зону «маска сломана» значило бы отправить человека чинить
    работающее.
    """
    z = zones.normalize({"x": 5, "y": 5, "w": 20, "h": 20,
                         "match": {"testid": "user-avatar"}})
    res = zones.resolve([z])
    assert res.boxes == [{"x": 5, "y": 5, "w": 20, "h": 20}]
    assert res.lost, "без DOM опознать нельзя — и об этом честно сказано"


# --------------------------------------------------------------------------- #
#  Маска
# --------------------------------------------------------------------------- #
def test_the_mask_covers_the_element_where_it_is_now():
    z = zones.normalize({"x": 0, "y": 0, "w": 4, "h": 4,
                         "match": {"testid": "user-avatar"}})
    dom = _dom({**AVATAR, "x": 40, "y": 40, "w": 20, "h": 20})
    mask, res = zones.mask((100, 100), [z], actual_dom=dom)
    assert isinstance(mask, np.ndarray) and mask.dtype == bool
    assert mask[50, 50], "центр элемента не замаскирован"
    assert not mask[2, 2], "маска осталась на старых координатах"
    assert res.by_selector == 1


def test_a_zone_that_walks_off_the_frame_is_clipped_not_crashed():
    z = zones.normalize({"x": 1, "y": 1, "w": 5, "h": 5,
                         "match": {"testid": "user-avatar"}})
    dom = _dom({**AVATAR, "x": 90, "y": 90, "w": 500, "h": 500})
    mask, _ = zones.mask((100, 100), [z], actual_dom=dom)
    assert mask.shape == (100, 100) and mask[99, 99]
