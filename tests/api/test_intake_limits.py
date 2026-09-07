# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Пределы приёма: сколько можно прислать и куда это может лечь.

Все проверки здесь про одно свойство: вход, открытый чужому CI, не должен
давать возможности занять память, том или файл за пределами каталога данных.
Раньше не было ни одного из трёх ограничений, и каждое отсутствие выглядело
одинаково — «сервис подвисает», то есть причину искали не там, где она есть.
"""

from __future__ import annotations

import io
import threading

import numpy as np
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VISTEST_ROOT", str(tmp_path / ".vistest"))
    monkeypatch.delenv("VISTEST_AUTH", raising=False)
    monkeypatch.delenv("VISTEST_INGEST_TOKEN", raising=False)
    monkeypatch.delenv("VISTEST_INGEST_OPEN", raising=False)
    monkeypatch.delenv("VISTEST_DOCS", raising=False)

    import importlib
    import threading as _threading

    from fastapi import APIRouter

    import vistest.api.auth as authmod
    import vistest.api.db as dbmod

    dbmod._local = _threading.local()
    authmod.router = APIRouter()

    import vistest.api.check as checkmod
    importlib.reload(checkmod)

    import vistest.api.main as mainmod
    importlib.reload(mainmod)
    return TestClient(mainmod.app), mainmod, checkmod


def _png(width=60, height=40) -> bytes:
    from PIL import Image

    image = np.zeros((height, width, 3), np.uint8)
    image[:] = (30, 90, 200)
    buf = io.BytesIO()
    Image.fromarray(image).save(buf, format="PNG")
    return buf.getvalue()


# --------------------------------------------------------------------------- #
#  POST /api/check
# --------------------------------------------------------------------------- #
def test_a_screenshot_over_the_limit_is_refused(service, monkeypatch):
    """Файл читался в память целиком, без всякого потолка.

    Проверяем не «сервис выжил» (это не проверить в тесте), а что отказ
    приходит и приходит внятным кодом.
    """
    client, _mainmod, checkmod = service
    monkeypatch.setattr(checkmod, "MAX_IMAGE_BYTES", 4096)

    r = client.post(
        "/api/check",
        data={"name": "checkout.png", "project": "shop"},
        files={"image": ("shot.png", b"x" * 20_000, "image/png")},
    )
    assert r.status_code == 413, r.text
    assert "larger than" in r.text


def test_too_many_frames_are_refused(service, monkeypatch):
    """Кадры для распознавания анимации: два-три, а не сколько прислали."""
    client, _mainmod, checkmod = service
    monkeypatch.setattr(checkmod, "MAX_FRAMES", 2)

    files = [("image", ("shot.png", _png(), "image/png"))]
    files += [("frames", (f"f{i}.png", _png(), "image/png")) for i in range(5)]

    r = client.post("/api/check",
                    data={"name": "checkout.png", "project": "shop"},
                    files=files)
    assert r.status_code == 413, r.text
    assert "frames" in r.text


def test_a_huge_image_is_refused_before_it_is_unpacked(service, monkeypatch):
    """Мегапиксели, а не байты.

    PNG в пару мегабайт разворачивается в гигабайты, и проверка размера файла
    об этом ничего не знает. Отказ считается по заголовку, до `convert`.
    """
    client, _mainmod, checkmod = service
    monkeypatch.setattr(checkmod, "MAX_PIXELS", 1000)

    r = client.post("/api/check",
                    data={"name": "checkout.png", "project": "shop"},
                    files={"image": ("shot.png", _png(200, 200), "image/png")})
    assert r.status_code == 413, r.text
    assert "Mpx" in r.text


def test_an_ordinary_snapshot_still_goes_through(service):
    """Контрольный: пределы не должны мешать обычной работе."""
    client, _mainmod, _checkmod = service
    r = client.post("/api/check",
                    data={"name": "checkout.png", "project": "shop"},
                    files={"image": ("shot.png", _png(), "image/png")})
    assert r.status_code == 200, r.text
    assert r.json()["verdict"] == "new_baseline"


# --------------------------------------------------------------------------- #
#  Путь артефакта
# --------------------------------------------------------------------------- #
def test_an_artifact_path_outside_the_data_volume_is_refused(service, tmp_path):
    """`_resolve` отдавал ЛЮБОЙ путь, а результат становился эталоном.

    Ссылка приходит из тела `POST /api/runs`, то есть её пишет чужой CI. Право
    писать историю не должно превращаться в право читать диск сервера.
    """
    _client, mainmod, _checkmod = service

    outsider = tmp_path / "secret.png"
    outsider.write_bytes(_png())

    assert mainmod._resolve(str(outsider)) is None
    assert mainmod._resolve("/files/../../../etc/passwd") is None
    assert mainmod._resolve("/local/../../etc/passwd") is None

    # А своё — на месте.
    inside = mainmod.ARTIFACTS / "7" / "shot.png"
    inside.parent.mkdir(parents=True, exist_ok=True)
    inside.write_bytes(_png())
    assert mainmod._resolve("/files/7/shot.png") == inside.resolve()


# --------------------------------------------------------------------------- #
#  Схема API
# --------------------------------------------------------------------------- #
def test_the_api_schema_is_closed_by_default(service):
    """`/docs` и `/openapi.json` — карта всех роутов, и она была публичной."""
    client, _mainmod, _checkmod = service
    assert client.get("/openapi.json").status_code in (401, 404)
    assert client.get("/docs").status_code in (401, 404)


# --------------------------------------------------------------------------- #
#  Паспорт эталона
# --------------------------------------------------------------------------- #
def test_parallel_ignore_zones_do_not_lose_each_other(tmp_path):
    """`add_ignore_box` читал, дописывал и записывал без замка.

    Кнопка «Ignore area» стоит на карточке региона, регионов у падения
    десяток, и жмут их подряд — гонка здесь обычное дело, а не редкость.
    """
    from vistest.storage import BaselineRecord, FileBaselineStore

    store = FileBaselineStore(tmp_path / "baselines")
    image = np.zeros((40, 60, 3), np.uint8)
    store.save(BaselineRecord(name="checkout.png", image=image))

    def add(i: int) -> None:
        store.add_ignore_box("checkout.png",
                             {"x": i, "y": i, "w": 5, "h": 5})

    threads = [threading.Thread(target=add, args=(i,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    record = store.load("checkout.png")
    assert len(record.ignore_boxes) == 12, "ни одна зона не должна потеряться"
    # И версия эталона на месте: битый meta.json обнулял её молча, после чего
    # защита от конкурентного утверждения переставала работать.
    assert store.versions("checkout.png")[0]["version"] == 1


# --------------------------------------------------------------------------- #
#  Пороги, приходящие с вызовом
# --------------------------------------------------------------------------- #
def test_a_threshold_of_the_wrong_type_is_a_clear_refusal(service):
    """Проверялись только имена, и `"abc"` доезжал до движка.

    Падало это пятисоткой в середине сравнения — то есть вызывающий узнавал
    «internal error» про СВОЮ опечатку.
    """
    client, _mainmod, _checkmod = service
    r = client.post("/api/check",
                    data={"name": "checkout.png", "project": "shop",
                          "options": '{"min_region_px": "abc"}'},
                    files={"image": ("shot.png", _png(), "image/png")})
    assert r.status_code == 400, r.text
    assert "min_region_px" in r.text


def test_a_threshold_outside_its_range_is_refused(service):
    """`fail_severity: -5` тихо превращал проверку в «всё красное»."""
    client, _mainmod, _checkmod = service
    r = client.post("/api/check",
                    data={"name": "checkout.png", "project": "shop",
                          "options": '{"fail_severity": -5}'},
                    files={"image": ("shot.png", _png(), "image/png")})
    assert r.status_code == 400, r.text
    assert "between" in r.text


def test_an_unknown_change_kind_is_refused(service):
    """Опечатка в классе изменения выключала бы не тот класс — и молча."""
    client, _mainmod, _checkmod = service
    r = client.post("/api/check",
                    data={"name": "checkout.png", "project": "shop",
                          "options": '{"ignore_kinds": ["antialiaas"]}'},
                    files={"image": ("shot.png", _png(), "image/png")})
    assert r.status_code == 400, r.text
    assert "antialiaas" in r.text


def test_sane_thresholds_still_work(service):
    """Контрольный: то, ради чего переопределение и существует."""
    client, _mainmod, _checkmod = service
    r = client.post("/api/check",
                    data={"name": "checkout.png", "project": "shop",
                          "options": '{"fail_severity": 40,'
                                     ' "ignore_kinds": ["antialias"],'
                                     ' "detect_moved": false}'},
                    files={"image": ("shot.png", _png(), "image/png")})
    assert r.status_code == 200, r.text


# --------------------------------------------------------------------------- #
#  Артефакты прогона по матрице
# --------------------------------------------------------------------------- #
def test_artifacts_of_different_variants_do_not_overwrite_each_other(service):
    """Шесть вариантов одного снимка писали в один каталог.

    Имя снимка в матрице одно на все варианты — это один и тот же экран. Пока
    платформа не участвовала в пути, `actual.png` от firefox при 390 ложился
    поверх chromium при 1440, и в разборе падения человек смотрел на картинку
    другого браузера, ничего об этом не зная.
    """
    client, mainmod, _checkmod = service

    payload = {
        "run_id": "matrix-1",
        "project": "shop",
        "totals": {"total": 2, "passed": 0, "failed": 2},
        "comparisons": [
            {"name": "checkout.png", "verdict": "fail",
             "platform": "linux-chromium-1x-1440x900", "metrics": {}},
            {"name": "checkout.png", "verdict": "fail",
             "platform": "linux-firefox-1x-390x844", "metrics": {}},
        ],
    }
    created = client.post("/api/runs", json=payload)
    assert created.status_code == 200, created.text
    run_id = created.json()["id"]

    for platform in ("linux-chromium-1x-1440x900", "linux-firefox-1x-390x844"):
        r = client.post(
            f"/api/runs/{run_id}/artifacts",
            data={"snapshot": "checkout.png", "kind": "actual",
                  "platform": platform},
            files={"file": ("actual.png", _png(), "image/png")})
        assert r.status_code == 200, r.text

    run = client.get(f"/api/runs/{run_id}").json()
    uris = {c["platform"]: c["artifacts"].get("actual")
            for c in run["comparisons"]}
    assert len(uris) == 2
    assert all(uris.values()), "у каждого варианта должна быть своя ссылка"
    assert len(set(uris.values())) == 2, "и ссылки должны быть РАЗНЫЕ"


def test_a_single_variant_run_keeps_the_old_layout(service):
    """Клиенты прежних версий платформу не присылают — им ничего не меняется."""
    client, _mainmod, _checkmod = service
    payload = {
        "run_id": "plain-1", "project": "shop",
        "totals": {"total": 1, "passed": 0, "failed": 1},
        "comparisons": [{"name": "checkout.png", "verdict": "fail",
                         "metrics": {}}],
    }
    run_id = client.post("/api/runs", json=payload).json()["id"]
    r = client.post(f"/api/runs/{run_id}/artifacts",
                    data={"snapshot": "checkout.png", "kind": "actual"},
                    files={"file": ("actual.png", _png(), "image/png")})
    assert r.status_code == 200, r.text
    assert r.json()["uri"] == f"/files/{run_id}/checkout.png/actual.png"
