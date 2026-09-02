# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

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


# --------------------------------------------------------------------------- #
#  Через движок целиком
#
#  Всё выше проверяет разрешение зон само по себе. Здесь — что оно доезжает до
#  вердикта: зона, которую видно в интерфейсе и которая не действует на
#  сравнение, — худший из возможных исходов, потому что выглядит она рабочей.
# --------------------------------------------------------------------------- #
import cv2  # noqa: E402

from vistest.config import VisTestConfig  # noqa: E402
from vistest.models import Verdict  # noqa: E402
from vistest.service import CheckService  # noqa: E402

from . import synthetic as syn  # noqa: E402


@pytest.fixture
def svc(tmp_path):
    cfg = VisTestConfig.preset_of("balanced")
    cfg.paths.root = str(tmp_path / ".vistest")
    cfg.ai.attribution_enabled = False
    cfg.capture.retry_on_fail = False
    return CheckService(cfg, platform="test-chromium-1x", run_dir=tmp_path / "run")


def _dom_with_widget(x: int, y: int) -> dict:
    """Снепшот, в котором есть один опознаваемый блок — «живой» виджет."""
    return {"nodes": [
        {"x": 0, "y": 0, "w": 900, "h": 700, "tag": "body", "testid": None,
         "id": None, "cls": None, "selector": "body"},
        {"x": x, "y": y, "w": 40, "h": 40, "tag": "div",
         "testid": "live-widget", "id": None, "cls": "widget",
         "selector": "body > div.widget"},
    ]}


def _paint_widget(img, x: int, y: int, color):
    out = img.copy()
    cv2.rectangle(out, (x, y), (x + 39, y + 39), color, -1)
    return out


def test_a_zone_bound_to_an_element_survives_the_element_moving(svc):
    """Ради этого всё и делалось — и проверяется это на вердикте, а не на боксах.

    Виджет всегда разный (часы, карусель, случайный аватар) и потому
    замаскирован. В новой вёрстке он переехал. Координатная зона осталась бы на
    старом месте: новое положение виджета покраснело бы, а под старым перестало
    бы проверяться то, что туда переехало.
    """
    base = _paint_widget(syn.page(), 700, 60, (10, 200, 10))
    svc.check("home.png", base, dom=_dom_with_widget(700, 60), render=False)

    svc.store.add_ignore_box("home.png", {
        "x": 700, "y": 60, "w": 40, "h": 40,
        "selector": "body > div.widget",
        "match": {"testid": "live-widget"},
        "reason": "живой виджет",
    })

    # Виджет переехал И перекрасился — то есть даёт различие в обоих местах.
    moved = _paint_widget(syn.page(), 120, 400, (200, 10, 10))
    res = svc.check("home.png", moved, dom=_dom_with_widget(120, 400), render=False)
    assert res.verdict is Verdict.PASS, res.summary()


def test_the_same_move_fails_when_the_zone_is_only_coordinates(svc):
    """Контроль к предыдущему: без привязки зона переезд не переживает.

    Без этого теста первый доказывал бы только то, что сравнение вообще
    проходит, — а не то, что его вытянула привязка к элементу.
    """
    base = _paint_widget(syn.page(), 700, 60, (10, 200, 10))
    svc.check("home.png", base, dom=_dom_with_widget(700, 60), render=False)
    svc.store.add_ignore_box("home.png", {"x": 700, "y": 60, "w": 40, "h": 40})

    moved = _paint_widget(syn.page(), 120, 400, (200, 10, 10))
    res = svc.check("home.png", moved, dom=_dom_with_widget(120, 400), render=False)
    assert res.verdict is Verdict.FAIL


def test_an_element_gone_from_the_page_but_still_in_the_baseline_is_not_lost(svc):
    """Пропал с текущей страницы — но эталон его помнит, и маска работает.

    Это не поломка зоны, а обычное «блок убрали»: различие возникает ровно там,
    где он был на эталоне, и замаскировать надо именно это место. Кричать здесь
    «маска потеряла цель» значило бы отправить человека чинить работающее — а
    ложная тревога дороже молчания, потому что после третьей на них перестают
    смотреть.
    """
    base = _paint_widget(syn.page(), 700, 60, (10, 200, 10))
    svc.check("home.png", base, dom=_dom_with_widget(700, 60), render=False)
    svc.store.add_ignore_box("home.png", {
        "x": 700, "y": 60, "w": 40, "h": 40,
        "match": {"testid": "live-widget"}})

    gone = {"nodes": [{"x": 0, "y": 0, "w": 900, "h": 700, "tag": "body",
                       "testid": None, "id": None, "cls": None,
                       "selector": "body"}]}
    res = svc.check("home.png", syn.page(), dom=gone, render=False)
    assert not any("no longer finds" in n for n in res.notes), res.notes
    assert res.verdict is Verdict.PASS, res.summary()


def test_a_lost_element_reaches_the_person_as_a_note(svc):
    """Заметка — единственный способ отличить работающую маску от переставшей.

    На картинке и по вердикту они одинаковы. Цель считается потерянной, только
    когда её нет НИ В ОДНОМ снепшоте — ни в текущем, ни в эталонном: это тот
    случай, когда `data-testid` переименовали, эталон с тех пор пересняли, и
    зацепиться зоне больше не за что нигде.
    """
    base = _paint_widget(syn.page(), 700, 60, (10, 200, 10))
    renamed = {"nodes": [
        {"x": 0, "y": 0, "w": 900, "h": 700, "tag": "body", "testid": None,
         "id": None, "cls": None, "selector": "body"},
        {"x": 700, "y": 60, "w": 40, "h": 40, "tag": "div",
         "testid": "widget-live", "id": None, "cls": "widget",
         "selector": "body > div.widget"},
    ]}
    svc.check("home.png", base, dom=renamed, render=False)
    svc.store.add_ignore_box("home.png", {
        "x": 700, "y": 60, "w": 40, "h": 40,
        "match": {"testid": "live-widget"}})

    res = svc.check("home.png", _paint_widget(syn.page(), 700, 60, (200, 10, 10)),
                    dom=renamed, render=False)
    assert any("no longer finds" in n for n in res.notes), res.notes
    # И при этом маска не исчезла: запасные координаты держат прежнее место,
    # поэтому прогон не краснеет — он предупреждает.
    assert res.verdict is Verdict.PASS, res.summary()


def test_an_old_zone_keeps_working_exactly_as_before(svc):
    """Обратная совместимость здесь не «постарались», а по построению.

    Зона старого формата — это зона без селектора, и путь для неё тот же самый.
    """
    base = syn.page()
    svc.check("home.png", base, render=False)
    act = syn.regress_tiny_icon(base)
    assert svc.check("home.png", act, render=False).regions

    svc.store.add_ignore_box("home.png", {"x": 830, "y": 6, "w": 60, "h": 60})
    assert svc.check("home.png", act, render=False).verdict is Verdict.PASS
