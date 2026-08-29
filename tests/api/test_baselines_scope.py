# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Вкладка «Эталоны» умеет смотреть не только в своё хранилище.

Дефект, который здесь закрыт: снимки, снятые самим VisTest для подключённого
проекта, лежат в `.vistest/external/<key>/baselines/<platform>/` и не были
видны ниоткуда. Ни в списке, ни в превью — и, что важнее, их нельзя было
переснять и по ним нельзя было запустить сценарий: `resnap` и `/api/suite/run`
перечисляли совсем другую папку.

Плюс отдельно зафиксирован контракт `resnap`: интерфейс всегда слал `names`
списком, а роут требовал `name` строкой, поэтому кнопка «Переснять» отвечала
400 при любом нажатии.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from vistest.api.baselines import parse_scope


# --------------------------------------------------------------------------- #
#  Разбор scope
# --------------------------------------------------------------------------- #
def test_empty_scope_means_the_services_own_set():
    assert parse_scope(None) == ("global", None)
    assert parse_scope("") == ("global", None)
    assert parse_scope("global") == ("global", None)


def test_project_scope_names_the_project():
    assert parse_scope("project:acme") == ("vistest", "acme")


def test_broken_scope_is_rejected_with_an_explanation():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as e:
        parse_scope("nonsense")
    assert "project:<key>" in str(e.value.detail)

    with pytest.raises(HTTPException):
        parse_scope("project:")


# --------------------------------------------------------------------------- #
#  Роуты: проектный набор виден и переснимается
# --------------------------------------------------------------------------- #
@pytest.fixture
def wired(tmp_path, monkeypatch):
    """Сервис с одним подключённым проектом и снятым набором VisTest."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VISTEST_ROOT", str(tmp_path / ".vistest"))
    monkeypatch.setenv("VISTEST_AUTH", "off")

    import numpy as np

    from vistest.api import baselines as mod
    from vistest.config import VisTestConfig, platform_key
    from vistest.projects import BaselineDir, Project, ProjectRegistry
    from vistest.storage import BaselineRecord, FileBaselineStore

    cfg = VisTestConfig.load()
    mod._cfg = cfg

    repo = tmp_path / "their-repo"
    (repo / "screens").mkdir(parents=True)
    (repo / "snapshots").mkdir(parents=True)
    project = Project(key="acme", name="Acme", root=str(repo), tests="screens",
                      baseline_store="project",
                      baselines=[BaselineDir(dir="snapshots")])
    ProjectRegistry(cfg).save(project)

    pf = platform_key()
    store = FileBaselineStore(project.vistest_baselines_path(cfg, pf))
    store.save(BaselineRecord(
        name="login.png",
        image=np.full((10, 10, 3), 130, dtype=np.uint8),
        meta={"url": "https://example.test/login"}))

    # А в собственном наборе сервиса — совсем другой снимок.
    own = FileBaselineStore(cfg.baselines_path(pf))
    own.save(BaselineRecord(name="dashboard.png",
                            image=np.full((10, 10, 3), 20, dtype=np.uint8)))

    app = FastAPI()
    app.include_router(mod.router)
    return TestClient(app), pf, project, cfg


def test_project_set_is_invisible_without_scope(wired):
    """Поведение по умолчанию не меняется: без scope — своё хранилище."""
    client, pf, _, _ = wired
    r = client.get("/api/baselines/detail")
    assert r.status_code == 200, r.text
    names = [i["name"] for p in r.json()["platforms"] for i in p["items"]]
    assert "dashboard.png" in names
    assert "login.png" not in names


def test_project_set_is_listed_with_scope(wired):
    """Ровно то, чего не было: снимки проекта видны."""
    client, pf, _, _ = wired
    r = client.get("/api/baselines/detail", params={"scope": "project:acme"})
    assert r.status_code == 200, r.text
    body = r.json()
    names = [i["name"] for p in body["platforms"] for i in p["items"]]
    assert names == ["login.png"]
    assert body["scope"] == "project:acme"
    # Ссылка на картинку обязана нести scope, иначе превью уедет в чужой набор.
    thumb = body["platforms"][0]["items"][0]["thumb"]
    assert "scope=project%3Aacme" in thumb


def test_scopes_endpoint_advertises_every_store(wired):
    client, pf, _, _ = wired
    r = client.get("/api/baselines/scopes")
    assert r.status_code == 200, r.text
    scopes = {s["scope"]: s for s in r.json()["scopes"]}
    assert "global" in scopes
    assert "project:acme" in scopes
    assert scopes["project:acme"]["platforms"][0]["count"] == 1


def test_image_respects_scope(wired):
    client, pf, _, _ = wired
    ok = client.get("/api/baselines/image",
                    params={"platform": pf, "name": "login.png",
                            "scope": "project:acme"})
    assert ok.status_code == 200
    # Без scope того же снимка в своём наборе нет.
    missing = client.get("/api/baselines/image",
                         params={"platform": pf, "name": "login.png"})
    assert missing.status_code == 404


def test_snapshot_of_a_project_is_runnable(wired):
    """`/api/suite/run` обязан находить проектные снимки с привязанным адресом.

    Это и есть «нельзя вызвать прогон для такого сценария»: раньше роут
    перечислял только собственное хранилище и отвечал 400 «нет эталонов с
    привязанным адресом».
    """
    client, pf, _, _ = wired
    r = client.post("/api/suite/run",
                    json={"platform": pf, "scope": "project:acme",
                          "names": ["login.png"]})
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 1
    assert r.json()["scope"] == "project:acme"


def test_suite_run_without_scope_does_not_see_the_project(wired):
    client, pf, _, _ = wired
    r = client.post("/api/suite/run", json={"platform": pf})
    assert r.status_code == 400
    assert "own set" in r.json()["detail"]


# --------------------------------------------------------------------------- #
#  resnap: контракт с интерфейсом
# --------------------------------------------------------------------------- #
def test_resnap_accepts_the_list_the_interface_actually_sends(wired):
    """Интерфейс шлёт `names: [...]` — раньше это был безусловный 400."""
    client, pf, _, _ = wired
    r = client.post("/api/baselines/resnap",
                    json={"platform": pf, "names": ["login.png"],
                          "scope": "project:acme"})
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 1


def test_resnap_still_accepts_a_single_name(wired):
    client, pf, _, _ = wired
    r = client.post("/api/baselines/resnap",
                    json={"platform": pf, "name": "login.png",
                          "scope": "project:acme"})
    assert r.status_code == 200, r.text


def test_resnap_without_an_address_says_what_to_do(wired):
    client, pf, project, cfg = wired
    import numpy as np

    from vistest.storage import BaselineRecord, FileBaselineStore

    FileBaselineStore(project.vistest_baselines_path(cfg, pf)).save(
        BaselineRecord(name="no-url.png",
                       image=np.full((4, 4, 3), 5, dtype=np.uint8)))

    r = client.post("/api/baselines/resnap",
                    json={"platform": pf, "names": ["no-url.png"],
                          "scope": "project:acme"})
    assert r.status_code == 400
    assert "bind one first" in r.json()["detail"]


def test_meta_can_bind_an_address_inside_a_project_set(wired):
    """Без этого снимок проекта нельзя сделать запускаемым в принципе."""
    client, pf, project, cfg = wired
    import numpy as np

    from vistest.storage import BaselineRecord, FileBaselineStore

    FileBaselineStore(project.vistest_baselines_path(cfg, pf)).save(
        BaselineRecord(name="cart.png",
                       image=np.full((4, 4, 3), 5, dtype=np.uint8)))

    r = client.post("/api/baselines/meta",
                    json={"platform": pf, "name": "cart.png",
                          "scope": "project:acme",
                          "url": "https://example.test/cart"})
    assert r.status_code == 200, r.text

    meta = json.loads(
        (project.vistest_baselines_path(cfg, pf) / "cart" / "meta.json")
        .read_text("utf-8"))
    assert meta["url"] == "https://example.test/cart"


def test_unknown_project_scope_is_a_clear_404(wired):
    client, pf, _, _ = wired
    r = client.get("/api/baselines/detail", params={"scope": "project:ghost"})
    assert r.status_code == 404
    assert "not connected" in r.json()["detail"]
