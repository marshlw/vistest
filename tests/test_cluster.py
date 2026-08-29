# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Группировка одинаковых расхождений.

Проверяется главное свойство: когда одна причина проявилась в двадцати местах,
человек должен получить один вопрос, а не двадцать. И обратное — разные
причины схлопывать нельзя, иначе группировка начнёт прятать баги.
"""

from __future__ import annotations

from vistest.cluster import (
    Cluster,
    cluster_regions,
    normalize_selector,
    summarize,
)


def region(kind="moved", selector="header > nav > a", w=100, h=20,
           dx=0, dy=1, severity=30.0, text=None):
    return {"kind": kind, "selector": selector, "w": w, "h": h,
            "moved_dx": dx, "moved_dy": dy, "severity": severity,
            "element_text": text, "x": 10, "y": 20}


def comp(name, regions):
    return {"name": name, "regions": regions}


# --------------------------------------------------------------------------- #
#  Нормализация селектора
# --------------------------------------------------------------------------- #
def test_indexes_are_stripped():
    """Соседние элементы одного списка ломаются вместе — это одно место."""
    a = normalize_selector("ul > li:nth-of-type(3) > span")
    b = normalize_selector("ul > li:nth-of-type(7) > span")
    assert a == b == "ul > li > span"


def test_nth_child_is_stripped_too():
    assert normalize_selector("div:nth-child(2) > p") == "div > p"


def test_empty_selector_survives():
    assert normalize_selector(None) == ""
    assert normalize_selector("") == ""


# --------------------------------------------------------------------------- #
#  Схлопывание
# --------------------------------------------------------------------------- #
def test_one_cause_across_many_snapshots_becomes_one_group():
    """Двадцать снимков, одна причина — один вопрос."""
    comps = [comp(f"page{i}.png", [region(dy=1)]) for i in range(20)]
    clusters = cluster_regions(comps)

    assert len(clusters) == 1
    assert clusters[0].size == 20
    assert len(clusters[0].snapshots) == 20


def test_different_kinds_never_merge():
    """Сдвиг и исчезновение не могут быть одной причиной."""
    comps = [comp("a.png", [region(kind="moved"), region(kind="removed")])]
    clusters = cluster_regions(comps, min_size=1)
    assert len({c.kind for c in clusters}) == 2


def test_opposite_shifts_do_not_merge():
    """Уехало вверх и уехало вниз — разные причины."""
    comps = [comp("a.png", [region(dy=8)] * 3 + [region(dy=-8)] * 3)]
    clusters = cluster_regions(comps)
    assert len(clusters) == 2


def test_close_shifts_merge_within_tolerance():
    """Разница в пиксель — округление субпиксельного рендера, не причина."""
    comps = [comp("a.png", [region(dy=4), region(dy=5), region(dy=4)])]
    clusters = cluster_regions(comps)
    assert len(clusters) == 1
    assert clusters[0].size == 3


def test_different_containers_do_not_merge():
    comps = [comp("a.png", [
        region(selector="header > nav > a"),
        region(selector="header > nav > a"),
        region(selector="footer > ul > li"),
        region(selector="footer > ul > li"),
    ])]
    clusters = cluster_regions(comps)
    assert len(clusters) == 2


def test_singletons_are_not_groups():
    """Одно расхождение — не группа: обычная таблица покажет его лучше."""
    comps = [comp("a.png", [region(selector="a"), region(selector="b")])]
    assert cluster_regions(comps) == []


# --------------------------------------------------------------------------- #
#  Общий предок и формулировки
# --------------------------------------------------------------------------- #
def test_common_ancestor_points_at_the_place_to_fix():
    c = Cluster(kind="moved", key=())
    c.regions = [
        region(selector="main > header > nav > a"),
        region(selector="main > header > nav > button"),
        region(selector="main > header > h1"),
    ]
    assert c.common_ancestor() == "main > header"


def test_common_ancestor_is_empty_when_nothing_shared():
    c = Cluster(kind="moved", key=())
    c.regions = [region(selector="header > a"), region(selector="footer > b")]
    assert c.common_ancestor() == ""


def test_description_mentions_direction_and_count():
    c = Cluster(kind="moved", key=())
    c.regions = [region(dy=48, selector="main > div > p") for _ in range(3)]
    text = c.describe()

    assert "3" in text and "48px" in text and "down" in text


def test_tiny_shifts_get_the_right_advice():
    """Сдвиг на 1–2px во многих местах — одна причина выше по странице."""
    c = Cluster(kind="moved", key=())
    c.regions = [region(dy=1) for _ in range(10)]
    assert "higher up the page" in c.advice()


def test_text_group_advises_masking():
    c = Cluster(kind="text", key=())
    c.regions = [region(kind="text", dy=0) for _ in range(4)]
    assert "masked" in c.advice()


def test_single_region_advice_is_neutral():
    c = Cluster(kind="moved", key=())
    c.regions = [region()]
    assert "A single difference" in c.advice()


# --------------------------------------------------------------------------- #
#  Сводка
# --------------------------------------------------------------------------- #
def test_summary_counts_the_saved_work():
    comps = [comp(f"p{i}.png", [region(dy=1)]) for i in range(12)]
    s = summarize(comps)

    assert s["total_regions"] == 12
    assert s["grouped_regions"] == 12
    assert s["questions"] == 1, "двенадцать падений — один вопрос"
    assert s["saved"] == 11


def test_summary_keeps_ungrouped_as_separate_questions():
    comps = [
        comp("a.png", [region(dy=1), region(dy=1)]),
        comp("b.png", [region(kind="removed", selector="uniq", dy=0)]),
    ]
    s = summarize(comps)

    assert s["total_regions"] == 3
    assert s["grouped_regions"] == 2
    assert s["questions"] == 2, "группа плюс одиночка"


def test_summary_is_serializable():
    import json

    comps = [comp(f"p{i}.png", [region(dy=2)]) for i in range(4)]
    json.dumps(summarize(comps), ensure_ascii=False)


def test_biggest_group_comes_first():
    comps = [
        comp("a.png", [region(selector="rare > x", kind="color", dy=0)] * 2),
        comp("b.png", [region(selector="common > y", dy=3)] * 5),
        comp("c.png", [region(selector="common > y", dy=3)] * 5),
    ]
    clusters = cluster_regions(comps)
    assert clusters[0].size == 10, "сначала то, что сокращает больше работы"


def test_no_regions_gives_empty_summary():
    s = summarize([comp("a.png", [])])
    assert s["clusters"] == []
    assert s["questions"] == 0
