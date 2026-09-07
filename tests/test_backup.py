# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Резервная копия: то, ради чего инструмент вообще можно ставить в контур.

Эталон — накопленные решения людей, а не файл, который можно пересоздать.
Пересъёмка зафиксирует то, что есть сейчас, включая регресс, который ищут.
Поэтому здесь проверяется не «архив создался», а свойства, из-за отсутствия
которых копия однажды окажется бесполезной: полнота, безопасность распаковки,
отказ затирать живую инсталляцию и честность про секреты.
"""

from __future__ import annotations

import tarfile
from pathlib import Path

import numpy as np
import pytest

from vistest import backup
from vistest.api.db import Database
from vistest.storage import BaselineRecord, FileBaselineStore


@pytest.fixture
def install(tmp_path):
    """Инсталляция с базой, эталоном, подключённым проектом и секретами."""
    root = tmp_path / ".vistest"
    root.mkdir()
    db = Database(root / "vistest.db")
    db.execute("INSERT INTO project(name) VALUES('shop')")

    store = FileBaselineStore(root / "baselines")
    store.save(BaselineRecord(name="home.png",
                              image=np.zeros((12, 16, 3), np.uint8)))

    (root / "projects.yaml").write_text("projects: []\n", encoding="utf-8")
    (root / "secrets.env").write_text("STAND_PASSWORD=hunter2\n", encoding="utf-8")
    return root


def test_a_backup_holds_the_baselines_and_the_database(install, tmp_path):
    info = backup.create(tmp_path / "copy.tar.gz", root=install)

    assert "vistest.db" in info["contains"]
    assert "baselines" in info["contains"]
    assert "projects.yaml" in info["contains"]
    assert info["schema_version"] is not None

    names = set(tarfile.open(tmp_path / "copy.tar.gz").getnames())
    assert "vistest.db" in names
    assert any(n.startswith("baselines/") for n in names)


def test_secrets_stay_out_unless_asked_for(install, tmp_path):
    """Копия уезжает в тикет и на флешку — пароли от стендов с ней не едут."""
    plain = backup.create(tmp_path / "plain.tar.gz", root=install)
    assert "secrets.env" not in plain["contains"]
    assert "secrets.env" not in set(
        tarfile.open(tmp_path / "plain.tar.gz").getnames())

    asked = backup.create(tmp_path / "full.tar.gz", root=install,
                          with_secrets=True)
    assert "secrets.env" in asked["contains"]


def test_a_restore_brings_the_installation_back(install, tmp_path):
    backup.create(tmp_path / "copy.tar.gz", root=install)

    fresh = tmp_path / "fresh"
    info = backup.restore(tmp_path / "copy.tar.gz", root=fresh)

    assert Path(info["restored_to"]) == fresh.resolve()
    assert (fresh / "vistest.db").exists()
    # Каталог снимка называется по имени без расширения — см. `storage/fs`.
    assert (fresh / "baselines" / "home" / "baseline.png").exists()

    # База открывается и помнит то, что в ней было.
    db = Database(fresh / "vistest.db")
    assert db.one("SELECT name FROM project WHERE name='shop'")

    # И эталон читается движком, а не просто лежит файлом.
    record = FileBaselineStore(fresh / "baselines").load("home.png")
    assert record is not None and record.image.shape == (12, 16, 3)


def test_a_restore_refuses_to_overwrite_a_live_installation(install, tmp_path):
    """«Я думал, там пусто» — самая дорогая ошибка в этом файле."""
    backup.create(tmp_path / "copy.tar.gz", root=install)

    with pytest.raises(FileExistsError):
        backup.restore(tmp_path / "copy.tar.gz", root=install)

    # А с явным согласием — можно.
    info = backup.restore(tmp_path / "copy.tar.gz", root=install, force=True)
    assert Path(info["restored_to"]) == install.resolve()


def test_a_backup_from_a_newer_vistest_is_refused(install, tmp_path, monkeypatch):
    """Схема из будущего: развернуть можно, работать нельзя — говорим сразу."""
    backup.create(tmp_path / "copy.tar.gz", root=install)
    monkeypatch.setattr("vistest.api.db.SCHEMA_VERSION", -1)

    with pytest.raises(RuntimeError, match="newer VisTest"):
        backup.restore(tmp_path / "copy.tar.gz", root=tmp_path / "fresh")


def test_an_archive_cannot_write_outside_the_data_volume(tmp_path):
    """Копию могли передать по почте, а tar умеет пути вида `../../etc`."""
    evil = tmp_path / "evil.tar.gz"
    payload = tmp_path / "payload"
    payload.write_text("owned", encoding="utf-8")
    with tarfile.open(evil, "w:gz") as tar:
        tar.add(payload, arcname="../escaped.txt")
        manifest = tmp_path / backup.MANIFEST
        manifest.write_text('{"contains": []}', encoding="utf-8")
        tar.add(manifest, arcname=backup.MANIFEST)

    with pytest.raises(ValueError, match="Unsafe path"):
        backup.restore(evil, root=tmp_path / "dest")
    assert not (tmp_path / "escaped.txt").exists()


def test_inspect_tells_what_is_inside_without_unpacking(install, tmp_path):
    backup.create(tmp_path / "copy.tar.gz", root=install)
    info = backup.inspect(tmp_path / "copy.tar.gz")

    assert info["tool"] == "vistest"
    assert "baselines" in info["contains"]
    assert info["created_at"]
