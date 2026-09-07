# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Перенос эталонов через интерфейс.

Отдельно от `tests/test_transfer.py`, где проверяется само слияние. Здесь —
только то, что добавляет HTTP: кто имеет право, что уезжает в файле и что
происходит с присланным архивом до того, как он что-то изменит.
"""

from __future__ import annotations

import io
import tarfile
import threading

import numpy as np
import pytest
from fastapi.testclient import TestClient

from vistest.storage import BaselineRecord, FileBaselineStore


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

    import vistest.api.check as checkmod
    importlib.reload(checkmod)
    import vistest.api.license as licmod
    importlib.reload(licmod)
    import vistest.api.main as mainmod
    importlib.reload(mainmod)
    licmod.forget()

    root = tmp_path / ".vistest" / "baselines"
    store = FileBaselineStore(root / "linux-chromium-1x")
    img = np.zeros((8, 12, 3), np.uint8)
    store.save(BaselineRecord(name="shop/checkout.png", image=img))
    store.save(BaselineRecord(name="crm/login.png", image=img + 90))
    return TestClient(mainmod.app), mainmod, root


def _sign_in(client, mainmod, role="reviewer"):
    from vistest.api.auth import create_user

    create_user(mainmod.db, "anna", "password123", role=role)
    client.post("/api/auth/login",
                json={"login": "anna", "password": "password123"})


def test_the_export_hands_back_a_real_archive(service):
    client, mainmod, _root = service
    _sign_in(client, mainmod)

    r = client.get("/api/baselines/export")
    assert r.status_code == 200, r.text
    assert r.headers["X-VisTest-Snapshots"] == "2"

    names = set(tarfile.open(fileobj=io.BytesIO(r.content)).getnames())
    assert "vistest-baselines.json" in names
    assert any(n.startswith("baselines/") for n in names)


def test_one_project_can_be_exported_alone(service):
    client, mainmod, _root = service
    _sign_in(client, mainmod)

    r = client.get("/api/baselines/export", params={"project": "crm"})
    assert r.status_code == 200, r.text
    assert r.headers["X-VisTest-Snapshots"] == "1"


def test_an_empty_selection_is_a_404_not_an_empty_file(service):
    """Пустой архив выглядит как успех — и обнаруживается уже на той стороне."""
    client, mainmod, _root = service
    _sign_in(client, mainmod)

    r = client.get("/api/baselines/export", params={"project": "nothing-here"})
    assert r.status_code == 404, r.text


def test_a_viewer_cannot_download_the_whole_set(service):
    """Эталоны — это содержимое снятых страниц, а не картинка на экране."""
    client, mainmod, _root = service
    _sign_in(client, mainmod, role="viewer")

    assert client.get("/api/baselines/export").status_code == 403


def test_a_dry_run_changes_nothing_and_says_what_would_happen(service, tmp_path):
    client, mainmod, root = service
    _sign_in(client, mainmod)

    exported = client.get("/api/baselines/export").content
    # Стираем один снимок, чтобы в плане было что «добавить».
    import shutil
    shutil.rmtree(root / "linux-chromium-1x" / "crm")

    r = client.post("/api/baselines/import",
                    data={"mode": "new", "dry_run": "true"},
                    files={"file": ("in.tar.gz", exported, "application/gzip")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dry_run"] is True
    actions = {s["name"]: s["action"] for s in body["plan"]}
    assert actions["crm/login.png"] == "add"
    assert actions["shop/checkout.png"] == "skip"
    assert not (root / "linux-chromium-1x" / "crm").exists(), "ничего не изменилось"


def test_an_import_merges_and_is_written_to_the_audit(service, tmp_path):
    client, mainmod, root = service
    _sign_in(client, mainmod)

    exported = client.get("/api/baselines/export").content
    import shutil
    shutil.rmtree(root / "linux-chromium-1x" / "crm")

    r = client.post("/api/baselines/import",
                    data={"mode": "new"},
                    files={"file": ("in.tar.gz", exported, "application/gzip")})
    assert r.status_code == 200, r.text
    assert r.json()["counts"]["added"] == 1
    assert (root / "linux-chromium-1x" / "crm" / "login" / "meta.json").exists()

    rows = mainmod.db.query(
        "SELECT who, action FROM audit WHERE action='baselines.imported'")
    assert rows and rows[0]["who"] == "anna"


def test_something_that_is_not_our_archive_is_a_clear_400(service):
    client, mainmod, _root = service
    _sign_in(client, mainmod)

    r = client.post("/api/baselines/import",
                    data={"mode": "new"},
                    files={"file": ("x.tar.gz", b"not an archive at all",
                                    "application/gzip")})
    assert r.status_code == 400, r.text


def test_an_unknown_mode_is_refused_before_anything_is_read(service):
    client, mainmod, _root = service
    _sign_in(client, mainmod)

    r = client.post("/api/baselines/import",
                    data={"mode": "obliterate"},
                    files={"file": ("x.tar.gz", b"", "application/gzip")})
    assert r.status_code == 400, r.text
    assert "mode must be" in r.text
