# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Зоны игнорирования со стороны сервиса.

Правило зоны по элементу разобрано в `tests/test_zones.py`. Здесь — что оно
доезжает через API целиком: селектор сохраняется, карточка снимка честно
говорит, чем зона держится и находит ли ещё свою цель, а «что я обвёл мышью»
получает ответ, по которому можно выбрать уровень.
"""

from __future__ import annotations

import json
import threading

import numpy as np
import pytest
from fastapi.testclient import TestClient

PLATFORM = "linux-chromium-1x"
NAME = "shop.example/login.png"

DOM = {"dpr": 1, "nodes": [
    {"x": 0, "y": 0, "w": 90, "h": 60, "tag": "body", "testid": None,
     "id": None, "cls": None, "text": None, "selector": "body"},
    {"x": 10, "y": 10, "w": 30, "h": 20, "tag": "button", "testid": "submit",
     "id": None, "cls": "btn primary", "text": "Войти",
     "selector": "body > form > button.btn"},
    {"x": 12, "y": 14, "w": 20, "h": 10, "tag": "span", "testid": None,
     "id": None, "cls": "label", "text": "Войти",
     "selector": "body > form > button.btn > span.label"},
]}


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VISTEST_ROOT", str(tmp_path / ".vistest"))
    monkeypatch.delenv("VISTEST_AUTH", raising=False)

    import importlib

    from fastapi import APIRouter

    import vistest.api.auth as authmod
    import vistest.api.db as dbmod

    dbmod._local = threading.local()
    authmod.router = APIRouter()

    import vistest.api.main as mainmod
    importlib.reload(mainmod)

    from vistest.config import VisTestConfig
    from vistest.storage import BaselineRecord, FileBaselineStore

    cfg = VisTestConfig.load()
    store = FileBaselineStore(cfg.baselines_path(PLATFORM))
    store.save(BaselineRecord(
        name=NAME, image=np.full((60, 90, 3), 180, dtype=np.uint8),
        meta={"url": "https://shop.example/login"}))
    (store.dir_for(NAME) / "dom.json").write_text(
        json.dumps(DOM), encoding="utf-8")
    return TestClient(mainmod.app), mainmod, store


def _card(client):
    return client.get("/api/baselines/card",
                      params={"platform": PLATFORM, "name": NAME}).json()


# --------------------------------------------------------------------------- #
#  Сохранение
# --------------------------------------------------------------------------- #
def test_a_selector_survives_the_round_trip(service):
    """Селектор, потерянный по дороге в хранилище, — зона по координатам.

    Выглядит она при этом ровно так же, и узнать разницу можно только через
    месяц, когда блок переедет.
    """
    client, _mainmod, store = service
    r = client.put("/api/baselines/ignore-boxes", json={
        "platform": PLATFORM, "name": NAME,
        "boxes": [{"x": 10, "y": 10, "w": 30, "h": 20,
                   "selector": "body > form > button.btn",
                   "match": {"testid": "submit", "tag": "button"},
                   "reason": "живая кнопка"}]})
    assert r.status_code == 200, r.text
    assert r.json()["by_element"] == 1

    # Смотрим оттуда же, откуда читает движок, а не из ответа API.
    meta = json.loads((store.dir_for(NAME) / "meta.json").read_text("utf-8"))
    zone = meta["ignore_boxes"][0]
    assert zone["selector"] == "body > form > button.btn"
    assert zone["match"]["testid"] == "submit"
    assert zone["reason"] == "живая кнопка"


def test_a_zone_without_a_selector_is_stored_exactly_as_before(service):
    client, _mainmod, store = service
    client.put("/api/baselines/ignore-boxes", json={
        "platform": PLATFORM, "name": NAME,
        "boxes": [{"x": 1, "y": 2, "w": 3, "h": 4}]})
    meta = json.loads((store.dir_for(NAME) / "meta.json").read_text("utf-8"))
    assert meta["ignore_boxes"] == [{"x": 1, "y": 2, "w": 3, "h": 4}]


# --------------------------------------------------------------------------- #
#  Карточка снимка
# --------------------------------------------------------------------------- #
def test_the_card_says_what_holds_each_zone(service):
    """Две одинаковые рамки на картинке держатся по-разному, и это надо видеть."""
    client, _mainmod, _store = service
    client.put("/api/baselines/ignore-boxes", json={
        "platform": PLATFORM, "name": NAME,
        "boxes": [
            {"x": 10, "y": 10, "w": 30, "h": 20, "match": {"testid": "submit"}},
            {"x": 50, "y": 5, "w": 10, "h": 10},
        ]})
    held = [z["held_by"] for z in _card(client)["ignore_boxes"]]
    assert held == ["element", "coordinates"]


def test_the_card_marks_a_zone_that_lost_its_element(service):
    """Маска, тихо переставшая работать, не отличима от работающей."""
    client, _mainmod, _store = service
    client.put("/api/baselines/ignore-boxes", json={
        "platform": PLATFORM, "name": NAME,
        "boxes": [{"x": 10, "y": 10, "w": 30, "h": 20,
                   "match": {"testid": "no-such-thing"}}]})
    zone = _card(client)["ignore_boxes"][0]
    assert zone["lost"] is True and zone["matches"] == 0


def test_a_baseline_without_a_dom_is_not_called_broken(service, tmp_path):
    """Старый эталон снят без снепшота — про его зоны мы просто ничего не знаем.

    Сказать про такую «маска сломана» значит отправить человека чинить
    работающее.
    """
    client, _mainmod, store = service
    (store.dir_for(NAME) / "dom.json").unlink()
    client.put("/api/baselines/ignore-boxes", json={
        "platform": PLATFORM, "name": NAME,
        "boxes": [{"x": 10, "y": 10, "w": 30, "h": 20,
                   "match": {"testid": "submit"}}]})
    zone = _card(client)["ignore_boxes"][0]
    assert zone["lost"] is False and zone["matches"] is None


# --------------------------------------------------------------------------- #
#  «Что я обвёл»
# --------------------------------------------------------------------------- #
def test_the_circled_rectangle_resolves_to_an_element(service):
    client, _mainmod, _store = service
    r = client.post("/api/baselines/element-at", json={
        "platform": PLATFORM, "name": NAME,
        "x": 10, "y": 10, "w": 30, "h": 20})
    assert r.status_code == 200, r.text
    best = r.json()["nodes"][0]
    assert best["testid"] == "submit" and best["holds_by"] == "data-testid"


def test_the_answer_is_a_ladder_not_a_single_node(service):
    """Попасть мышью ровно в нужный уровень почти невозможно.

    Промахнулся на два пикселя — и выделил <span> внутри кнопки. Выбрать из
    списка «span → button» человек может осмысленно, угадать — нет.
    """
    client, _mainmod, _store = service
    nodes = client.post("/api/baselines/element-at", json={
        "platform": PLATFORM, "name": NAME,
        "x": 12, "y": 14, "w": 20, "h": 10}).json()["nodes"]
    tags = [n["tag"] for n in nodes]
    assert "span" in tags and "button" in tags


def test_a_click_without_dragging_picks_the_smallest_node_under_it(service):
    client, _mainmod, _store = service
    nodes = client.post("/api/baselines/element-at", json={
        "platform": PLATFORM, "name": NAME,
        "x": 20, "y": 18, "w": 0, "h": 0}).json()["nodes"]
    assert nodes and nodes[0]["tag"] == "span"


def test_a_baseline_without_a_dom_says_why_instead_of_answering_nothing(service):
    """Пустой список читается как «ничего не нашлось», а это другое состояние.

    Интерфейсу надо сказать «зацепиться не за что, остаются координаты», а не
    показать пустое меню.
    """
    client, _mainmod, store = service
    (store.dir_for(NAME) / "dom.json").unlink()
    body = client.post("/api/baselines/element-at", json={
        "platform": PLATFORM, "name": NAME,
        "x": 10, "y": 10, "w": 30, "h": 20}).json()
    assert body["nodes"] == [] and "DOM" in body["reason"]


def test_element_at_refuses_an_unknown_baseline(service):
    client, _mainmod, _store = service
    r = client.post("/api/baselines/element-at", json={
        "platform": PLATFORM, "name": "no/such.png",
        "x": 1, "y": 1, "w": 2, "h": 2})
    assert r.status_code == 404


# --------------------------------------------------------------------------- #
#  Из разбора падения
# --------------------------------------------------------------------------- #
def test_ignoring_a_region_keeps_the_selector_the_engine_found(service):
    """Движок уже назвал регион — записывать после этого голый прямоугольник
    значит выбросить единственное, что делает маску переносимой."""
    client, mainmod, store = service
    run_id = mainmod.db.ingest_run({
        "run_id": "ci-zone", "platform": PLATFORM, "browser": "chromium",
        "created_at": "2026-01-01T00:00:00", "git": {},
        "totals": {"total": 1, "failed": 1},
        "comparisons": [{
            "name": NAME, "verdict": "fail",
            "metrics": {"max_severity": 61.0},
            "regions": [{"x": 10, "y": 10, "w": 30, "h": 20, "kind": "content",
                         "severity": 61.0,
                         "selector": 'button[data-testid="submit"]'}],
        }],
    }, "demo")
    comp = mainmod.db.one("SELECT id FROM comparison WHERE run_id=?", (run_id,))

    r = client.post(f"/api/comparisons/{comp['id']}/ignore-region",
                    json={"x": 10, "y": 10, "w": 30, "h": 20, "reason": "via review"})
    assert r.status_code == 200, r.text
    assert r.json()["held_by"] == "element"

    meta = json.loads((store.dir_for(NAME) / "meta.json").read_text("utf-8"))
    assert meta["ignore_boxes"][0]["selector"] == 'button[data-testid="submit"]'
