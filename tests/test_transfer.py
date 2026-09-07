# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Перенос эталонов между инсталляциями.

Отличается от бэкапа тем, что здесь всё сливается с ЖИВЫМ набором на той
стороне, и цена ошибки — чужие решения, стёртые молча. Поэтому проверяется не
«архив создался», а именно слияние: что сохраняется, что заменяется и что
можно откатить.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from vistest import transfer
from vistest.storage import BaselineRecord, FileBaselineStore


def _image(shade: int) -> np.ndarray:
    img = np.zeros((10, 16, 3), np.uint8)
    img[:] = (shade, shade, shade)
    return img


@pytest.fixture
def source(tmp_path):
    """Инсталляция-источник: два проекта на одной платформе."""
    root = tmp_path / "src" / "baselines"
    store = FileBaselineStore(root / "linux-chromium-1x")
    store.save(BaselineRecord(name="shop/checkout.png", image=_image(40)))
    store.save(BaselineRecord(name="shop/cart.png", image=_image(60)))
    store.save(BaselineRecord(name="crm/login.png", image=_image(80)))
    return root


@pytest.fixture
def target(tmp_path):
    """Инсталляция-приёмник: свой набор, свои решения."""
    root = tmp_path / "dst" / "baselines"
    store = FileBaselineStore(root / "linux-chromium-1x")
    store.save(BaselineRecord(name="shop/checkout.png", image=_image(200)))
    store.add_ignore_box("shop/checkout.png",
                         {"x": 1, "y": 1, "w": 4, "h": 4, "reason": "clock"})
    return root


# --------------------------------------------------------------------------- #
#  Экспорт
# --------------------------------------------------------------------------- #
def test_everything_is_exported_by_default(source, tmp_path):
    info = transfer.export(tmp_path / "all.tar.gz", baselines_root=source)

    assert info["count"] == 3
    names = {s["name"] for s in info["snapshots"]}
    assert names == {"shop/checkout.png", "shop/cart.png", "crm/login.png"}
    assert all(s["platform"] == "linux-chromium-1x" for s in info["snapshots"])


def test_one_project_can_be_taken_alone(source, tmp_path):
    """Ровно тот случай, ради которого всё написано: отдать заказчику его набор."""
    info = transfer.export(tmp_path / "shop.tar.gz", baselines_root=source,
                           selection=transfer.Selection(project="shop"))

    assert {s["name"] for s in info["snapshots"]} == {"shop/checkout.png",
                                                     "shop/cart.png"}


def test_names_can_be_selected_by_glob(source, tmp_path):
    info = transfer.export(tmp_path / "some.tar.gz", baselines_root=source,
                           selection=transfer.Selection(names=("*/c*.png",)))
    assert {s["name"] for s in info["snapshots"]} == {"shop/checkout.png",
                                                     "shop/cart.png"}


def test_history_stays_out_unless_asked_for(source, tmp_path):
    """История прошлых версий в разы больше самой картинки."""
    store = FileBaselineStore(source / "linux-chromium-1x")
    store.save(BaselineRecord(name="shop/cart.png", image=_image(61)))

    import tarfile

    transfer.export(tmp_path / "plain.tar.gz", baselines_root=source)
    names = set(tarfile.open(tmp_path / "plain.tar.gz").getnames())
    assert not any("/history/" in n for n in names)

    transfer.export(tmp_path / "full.tar.gz", baselines_root=source,
                    with_history=True)
    names = set(tarfile.open(tmp_path / "full.tar.gz").getnames())
    assert any("/history/" in n for n in names)


def test_inspect_reads_the_manifest_without_unpacking(source, tmp_path):
    transfer.export(tmp_path / "a.tar.gz", baselines_root=source)
    manifest = transfer.inspect(tmp_path / "a.tar.gz")

    assert manifest["kind"] == "baselines"
    assert len(manifest["snapshots"]) == 3


# --------------------------------------------------------------------------- #
#  План
# --------------------------------------------------------------------------- #
def test_the_plan_says_what_would_happen_before_it_is_irreversible(
        source, target, tmp_path):
    transfer.export(tmp_path / "a.tar.gz", baselines_root=source)
    steps = {s["name"]: s["action"] for s in transfer.plan(
        tmp_path / "a.tar.gz", baselines_root=target, mode="new")}

    assert steps["shop/checkout.png"] == "skip", "здесь уже есть свой"
    assert steps["shop/cart.png"] == "add"
    assert steps["crm/login.png"] == "add"


def test_the_plan_changes_with_the_mode(source, target, tmp_path):
    transfer.export(tmp_path / "a.tar.gz", baselines_root=source)
    for mode, expected in (("update", "new version"), ("replace", "replace")):
        steps = {s["name"]: s["action"] for s in transfer.plan(
            tmp_path / "a.tar.gz", baselines_root=target, mode=mode)}
        assert steps["shop/checkout.png"] == expected


# --------------------------------------------------------------------------- #
#  Импорт
# --------------------------------------------------------------------------- #
def test_the_safe_default_never_touches_what_is_already_here(
        source, target, tmp_path):
    transfer.export(tmp_path / "a.tar.gz", baselines_root=source)
    before = FileBaselineStore(target / "linux-chromium-1x").load(
        "shop/checkout.png")

    result = transfer.import_(tmp_path / "a.tar.gz", baselines_root=target,
                              mode="new")

    assert result["counts"]["added"] == 2
    assert result["counts"]["skipped"] == 1
    after = FileBaselineStore(target / "linux-chromium-1x").load(
        "shop/checkout.png")
    assert np.array_equal(before.image, after.image), "чужое решение не тронуто"
    assert after.ignore_boxes, "и маска на месте"


def test_update_keeps_the_previous_version_to_roll_back_to(
        source, target, tmp_path):
    """«Принять обновление» не должно означать «потерять то, что было»."""
    transfer.export(tmp_path / "a.tar.gz", baselines_root=source)
    store = FileBaselineStore(target / "linux-chromium-1x")
    was = store.load("shop/checkout.png").image.copy()

    result = transfer.import_(tmp_path / "a.tar.gz", baselines_root=target,
                              mode="update", who="anna")

    assert result["counts"]["updated"] == 1
    now = store.load("shop/checkout.png")
    assert not np.array_equal(was, now.image), "картинка приехала"

    versions = store.versions("shop/checkout.png")
    assert len(versions) >= 2, "предыдущая версия осталась в истории"
    assert store.version_image("shop/checkout.png", 1) is not None


def test_update_does_not_lose_the_masks_this_team_set(source, target, tmp_path):
    """Маски ставила ЭТА команда под свои условия съёмки."""
    transfer.export(tmp_path / "a.tar.gz", baselines_root=source)
    transfer.import_(tmp_path / "a.tar.gz", baselines_root=target,
                     mode="update")

    record = FileBaselineStore(target / "linux-chromium-1x").load(
        "shop/checkout.png")
    assert record.ignore_boxes, "зона игнорирования пережила импорт"


def test_replace_is_the_mode_that_discards(source, target, tmp_path):
    transfer.export(tmp_path / "a.tar.gz", baselines_root=source)
    result = transfer.import_(tmp_path / "a.tar.gz", baselines_root=target,
                              mode="replace")

    assert result["counts"]["replaced"] == 1
    record = FileBaselineStore(target / "linux-chromium-1x").load(
        "shop/checkout.png")
    assert not record.ignore_boxes, "режим и назван «replace»"


def test_a_selection_applies_to_the_import_too(source, target, tmp_path):
    transfer.export(tmp_path / "a.tar.gz", baselines_root=source)
    result = transfer.import_(tmp_path / "a.tar.gz", baselines_root=target,
                              mode="new",
                              selection=transfer.Selection(project="crm"))

    assert result["counts"]["added"] == 1
    assert not (target / "linux-chromium-1x" / "shop" / "cart").exists()


def test_an_unknown_mode_is_refused_loudly(source, target, tmp_path):
    transfer.export(tmp_path / "a.tar.gz", baselines_root=source)
    with pytest.raises(ValueError, match="mode must be"):
        transfer.import_(tmp_path / "a.tar.gz", baselines_root=target,
                         mode="overwrite-everything")


def test_an_archive_cannot_write_outside_the_baselines(tmp_path):
    """Архив мог приехать по почте, а tar умеет пути вида `../..`."""
    import tarfile

    evil = tmp_path / "evil.tar.gz"
    payload = tmp_path / "payload"
    payload.write_text("owned", encoding="utf-8")
    manifest = tmp_path / transfer.MANIFEST
    manifest.write_text(json.dumps({"kind": "baselines", "snapshots": []}),
                        encoding="utf-8")
    with tarfile.open(evil, "w:gz") as tar:
        tar.add(payload, arcname="../escaped.txt")
        tar.add(manifest, arcname=transfer.MANIFEST)

    with pytest.raises(ValueError, match="Unsafe path"):
        transfer.import_(evil, baselines_root=tmp_path / "dst")
    assert not (tmp_path / "escaped.txt").exists()


def test_a_snapshot_without_a_passport_is_not_exported(source, tmp_path):
    """Эталон без версии на той стороне выключил бы защиту от гонки решений."""
    orphan = source / "linux-chromium-1x" / "orphan"
    orphan.mkdir(parents=True)
    (orphan / "baseline.png").write_bytes(b"not a real png")

    info = transfer.export(tmp_path / "a.tar.gz", baselines_root=source)
    assert "orphan" not in {s["name"] for s in info["snapshots"]}
