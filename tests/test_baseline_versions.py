# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Версии эталона, откат и атомарность записи.

Апрув — единственное необратимое действие в интерфейсе, и до сих пор оно было
необратимо буквально: предыдущие версии складывались в `history/v3.png` с
самого начала, а достать их было нечем.

Здесь же зафиксирована атомарность: картинка и паспорт писались подряд, и
падение между ними оставляло новую картинку со старой версией в паспорте — то
есть ломало ровно ту защиту от гонки решений, которая по этой версии и
работает.
"""

from __future__ import annotations

import json
import threading

import numpy as np
import pytest

from vistest.storage import BaselineRecord, ExternalBaselineStore, FileBaselineStore


def _img(value: int) -> np.ndarray:
    return np.full((6, 6, 3), value, dtype=np.uint8)


@pytest.fixture
def store(tmp_path):
    return FileBaselineStore(tmp_path / "baselines")


# --------------------------------------------------------------------------- #
#  Версии
# --------------------------------------------------------------------------- #
def test_first_save_is_version_one(store):
    store.save(BaselineRecord(name="login.png", image=_img(10)))
    versions = store.versions("login.png")
    assert [v["version"] for v in versions] == [1]
    assert versions[0]["current"] is True


def test_every_save_adds_a_version(store):
    for value in (10, 20, 30):
        store.save(BaselineRecord(name="login.png", image=_img(value)))

    versions = store.versions("login.png")
    assert [v["version"] for v in versions] == [3, 2, 1]
    assert sum(v["current"] for v in versions) == 1


def test_unknown_snapshot_has_no_versions(store):
    assert store.versions("never-seen.png") == []


def test_version_image_returns_the_right_picture(store):
    store.save(BaselineRecord(name="login.png", image=_img(11)))
    store.save(BaselineRecord(name="login.png", image=_img(22)))

    from vistest.capture.playwright_capture import read_png

    assert int(read_png(store.version_image("login.png", 1))[0, 0, 0]) == 11
    assert int(read_png(store.version_image("login.png", 2))[0, 0, 0]) == 22
    assert store.version_image("login.png", 99) is None


# --------------------------------------------------------------------------- #
#  Откат
# --------------------------------------------------------------------------- #
def test_restore_brings_back_the_old_picture(store):
    store.save(BaselineRecord(name="login.png", image=_img(11)))
    store.save(BaselineRecord(name="login.png", image=_img(99)))   # ошибочный апрув

    new_version = store.restore("login.png", 1, who="anna")

    assert new_version == 3
    assert int(store.load("login.png").image[0, 0, 0]) == 11


def test_restore_does_not_rewrite_history(store):
    """Откат — новое событие, а не стирание прошлого."""
    store.save(BaselineRecord(name="login.png", image=_img(11)))
    store.save(BaselineRecord(name="login.png", image=_img(99)))
    store.restore("login.png", 1, who="anna")

    versions = store.versions("login.png")
    assert [v["version"] for v in versions] == [3, 2, 1]
    # Ошибочная версия 2 никуда не делась — её видно в истории.
    from vistest.capture.playwright_capture import read_png
    assert int(read_png(store.version_image("login.png", 2))[0, 0, 0]) == 99


def test_restore_records_where_it_came_from(store):
    store.save(BaselineRecord(name="login.png", image=_img(11)))
    store.save(BaselineRecord(name="login.png", image=_img(99)))
    store.restore("login.png", 1, who="anna")

    current = store.versions("login.png")[0]
    assert current["restored_from"] == 1
    assert current["approved_by"] == "anna"


def test_restore_keeps_ignore_zones(store):
    """Откат картинки не должен снимать настроенные игнор-зоны."""
    store.save(BaselineRecord(name="login.png", image=_img(11)))
    store.add_ignore_box("login.png", {"x": 1, "y": 1, "w": 2, "h": 2})
    store.save(BaselineRecord(name="login.png", image=_img(99)))

    store.restore("login.png", 1, who="anna")
    assert store.load("login.png").ignore_boxes == [{"x": 1, "y": 1, "w": 2, "h": 2}]


def test_restore_of_a_missing_version_is_explicit(store):
    store.save(BaselineRecord(name="login.png", image=_img(11)))
    with pytest.raises(FileNotFoundError):
        store.restore("login.png", 42)


def test_external_store_sends_you_to_git(tmp_path):
    """Эталон в чужом репозитории откатывается git-ом, а не нами."""
    store = ExternalBaselineStore(tmp_path / "snapshots", tmp_path / "side")
    (tmp_path / "snapshots").mkdir()
    store.save(BaselineRecord(name="login.png", image=_img(10)))

    assert store.versions("login.png")[0]["vcs"] == "git"
    with pytest.raises(NotImplementedError, match="git"):
        store.restore("login.png", 1)


# --------------------------------------------------------------------------- #
#  Атомарность и блокировка
# --------------------------------------------------------------------------- #
def test_picture_and_passport_stay_in_step(store):
    store.save(BaselineRecord(name="login.png", image=_img(10)))
    d = store.dir_for("login.png")
    meta = json.loads((d / "meta.json").read_text("utf-8"))

    assert meta["version"] == 1
    assert meta["width"] == 6 and meta["height"] == 6
    # Временных файлов после записи остаться не должно.
    assert not list(d.glob("*.tmp*"))
    assert not (d / ".vistest.lock").exists()


def test_concurrent_saves_do_not_lose_a_version(store):
    """«Прочитал версию → записал версию+1» двумя потоками.

    Без блокировки каталога обе записи читали одну и ту же версию, и одна из
    них терялась: на диске оказывалась v2 вместо v4.
    """
    store.save(BaselineRecord(name="login.png", image=_img(1)))

    errors: list[Exception] = []

    def write(value: int):
        try:
            store.save(BaselineRecord(name="login.png", image=_img(value)))
        except Exception as e:                                # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=write, args=(v,)) for v in (2, 3, 4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert store.versions("login.png")[0]["version"] == 4
