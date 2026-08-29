# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Эталон на ветку.

Эталон был один на платформу, а веток у команды много. Кто-то намеренно
переверстал форму в своей ветке, посмотрел дифф, принял его как новую норму — и
main начал падать на том же снимке. Обратный случай не лучше: main обновили, а в
ветке, где эта часть страницы ещё старая, всё покраснело. Оба раза виноватым
выглядит инструмент.

Главное свойство, ради которого всё это и написано, проверяется первым: **прогон
ветки не может изменить основной набор**. Всё остальное — удобства вокруг него.
"""

from __future__ import annotations

import threading

import numpy as np
import pytest

from vistest.storage import BaselineRecord, FileBaselineStore
from vistest.storage.branch import BranchBaselineStore, safe_branch

PLATFORM = "linux-chromium-1x"


def _img(value: int, shape=(20, 30, 3)) -> np.ndarray:
    return np.full(shape, value, dtype=np.uint8)


@pytest.fixture
def stores(tmp_path):
    base = FileBaselineStore(tmp_path / "base")
    base.save(BaselineRecord(name="login.png", image=_img(100)))
    base.save(BaselineRecord(name="shop/cart.png", image=_img(110)))
    overlay = FileBaselineStore(tmp_path / "branch")
    return BranchBaselineStore(overlay, base, branch="feature/pay",
                               base_label="main"), base, overlay


# --------------------------------------------------------------------------- #
#  Имя ветки как имя каталога
# --------------------------------------------------------------------------- #
def test_branch_names_do_not_collapse_into_one_directory():
    """`feature/pay` и `feature-pay` — разные ветки, делить эталоны им нельзя."""
    assert safe_branch("feature/pay") != safe_branch("feature-pay")
    assert safe_branch("") == ""


def test_a_branch_name_cannot_escape_the_store():
    slug = safe_branch("../../../etc")
    assert "/" not in slug and ".." not in slug


# --------------------------------------------------------------------------- #
#  Чтение: ветка наследует, пока не переопределила
# --------------------------------------------------------------------------- #
def test_a_fresh_branch_sees_everything_the_base_has(stores):
    branch, _, _ = stores
    assert branch.exists("login.png")
    assert int(branch.load("login.png").image[0, 0, 0]) == 100
    assert branch.owner_of("login.png") == "base"
    assert set(branch.list_names()) == {"login.png", "shop/cart.png"}


def test_an_override_shadows_the_base_for_this_branch_only(stores):
    branch, base, _ = stores
    branch.save(BaselineRecord(name="login.png", image=_img(200)))

    assert int(branch.load("login.png").image[0, 0, 0]) == 200
    assert branch.owner_of("login.png") == "branch"
    # …и главное:
    assert int(base.load("login.png").image[0, 0, 0]) == 100


def test_the_other_snapshots_stay_inherited(stores):
    branch, _, _ = stores
    branch.save(BaselineRecord(name="login.png", image=_img(200)))
    assert branch.owner_of("shop/cart.png") == "base"
    assert int(branch.load("shop/cart.png").image[0, 0, 0]) == 110


def test_an_override_records_where_it_forked_from(stores):
    """Иначе «v1» в ветке против «v7» в main выглядит откатом, а не форком."""
    branch, _, overlay = stores
    branch.save(BaselineRecord(name="login.png", image=_img(200)))
    meta = overlay.load("login.png").meta
    assert meta["forked_from"] == "main"
    assert meta["forked_from_version"] == 1
    assert meta["branch"] == "feature/pay"


# --------------------------------------------------------------------------- #
#  Запись: ветка не имеет права трогать main
# --------------------------------------------------------------------------- #
def test_the_write_directory_always_points_at_the_branch(stores):
    """Движок пишет накопленную маску шума в `dir_for`.

    Отдай этот метод каталог основного набора для унаследованного снимка — и
    прогон ветки правил бы шумовой профиль main, молча и не имея на то права.
    """
    branch, base, overlay = stores
    assert branch.dir_for("login.png") == overlay.dir_for("login.png")
    assert branch.dir_for("login.png") != base.dir_for("login.png")


def test_noise_accumulated_by_the_branch_is_read_back(stores):
    """Копить маску и не пользоваться ею — худший из вариантов."""
    from vistest.core import noise as _noise

    branch, _, overlay = stores
    mask = np.zeros((20, 30), dtype=bool)
    mask[2:5, 2:5] = True
    d = overlay.dir_for("login.png")
    d.mkdir(parents=True, exist_ok=True)
    _noise.save_mask(d / "stability.png", mask)

    record = branch.load("login.png")
    assert record.stability_mask is not None
    assert bool(record.stability_mask[3, 3])


def test_an_ignore_zone_materialises_the_snapshot_into_the_branch(stores):
    """`meta.json` без `baseline.png` рядом никто никогда не прочитает."""
    branch, base, overlay = stores
    branch.add_ignore_box("login.png", {"x": 1, "y": 1, "w": 5, "h": 5})

    assert overlay.exists("login.png"), "снимок должен переехать в ветку"
    assert branch.load("login.png").ignore_boxes == [
        {"x": 1, "y": 1, "w": 5, "h": 5}]
    # Основной набор про эту зону ничего не знает.
    assert not base.load("login.png").ignore_boxes


def test_a_branch_cannot_roll_back_the_base(stores):
    branch, _, _ = stores
    with pytest.raises(NotImplementedError) as e:
        branch.restore("login.png", 1)
    assert "inherited" in str(e.value)


def test_a_branch_rolls_back_its_own_versions(stores):
    branch, base, _ = stores
    branch.save(BaselineRecord(name="login.png", image=_img(200)))
    branch.save(BaselineRecord(name="login.png", image=_img(210)))

    branch.restore("login.png", 1, who="anna")
    assert int(branch.load("login.png").image[0, 0, 0]) == 200
    assert int(base.load("login.png").image[0, 0, 0]) == 100


# --------------------------------------------------------------------------- #
#  Резолвинг: режим выключен — ничего не меняется
# --------------------------------------------------------------------------- #
@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VISTEST_ROOT", str(tmp_path / ".vistest"))
    monkeypatch.delenv("VISTEST_AUTH", raising=False)

    import importlib

    from fastapi import APIRouter
    from fastapi.testclient import TestClient

    import vistest.api.auth as authmod
    import vistest.api.db as dbmod

    dbmod._local = threading.local()
    authmod.router = APIRouter()

    import vistest.api.main as mainmod
    importlib.reload(mainmod)
    return TestClient(mainmod.app), mainmod


def _ingest(mainmod, run_key: str, branch: str):
    return mainmod.db.ingest_run({
        "run_id": run_key, "platform": PLATFORM, "browser": "chromium",
        "git": {"branch": branch, "sha": "abc1234"},
        "totals": {"total": 1},
        "baseline_scope": "global",
        "comparisons": [{"name": "login.png", "verdict": "fail",
                         "metrics": {"max_severity": 40.0}}],
    }, "demo")


def test_with_the_mode_off_nothing_is_wrapped(service):
    """Команде с одной веткой режим не даёт ничего, а вопросов добавляет."""
    _, mainmod = service
    from vistest.api.stores import store_for_run

    run_id = _ingest(mainmod, "r1", "feature/pay")
    store, directory, info = store_for_run(mainmod.db, run_id, platform=PLATFORM)
    assert not isinstance(store, BranchBaselineStore)
    assert info["branch"] == ""


def test_with_the_mode_on_a_branch_run_resolves_to_an_overlay(service):
    _, mainmod = service
    from vistest.api.prefs import BRANCH_BASELINES, set_flag
    from vistest.api.stores import store_for_run

    set_flag(mainmod.db, BRANCH_BASELINES, True)
    run_id = _ingest(mainmod, "r1", "feature/pay")
    store, directory, info = store_for_run(mainmod.db, run_id, platform=PLATFORM)

    assert isinstance(store, BranchBaselineStore)
    assert info["branch"] == "feature/pay"
    assert "branch-baselines" in str(directory)


def test_the_base_branch_is_never_an_overlay(service):
    """Иначе основной набор стал бы наложением сам на себя."""
    _, mainmod = service
    from vistest.api.prefs import BRANCH_BASELINES, set_flag
    from vistest.api.stores import store_for_run

    set_flag(mainmod.db, BRANCH_BASELINES, True)
    for branch in ("main", ""):
        run_id = _ingest(mainmod, f"r-{branch or 'none'}", branch)
        store, _, info = store_for_run(mainmod.db, run_id, platform=PLATFORM)
        assert not isinstance(store, BranchBaselineStore), branch
        assert info["branch"] == ""


def test_the_base_branch_name_is_configurable(service):
    _, mainmod = service
    from vistest.api.prefs import BRANCH_BASELINES, DEFAULT_BRANCH, set_flag, set_text
    from vistest.api.stores import store_for_run

    set_flag(mainmod.db, BRANCH_BASELINES, True)
    set_text(mainmod.db, DEFAULT_BRANCH, "master")

    run_id = _ingest(mainmod, "r-master", "master")
    store, _, _ = store_for_run(mainmod.db, run_id, platform=PLATFORM)
    assert not isinstance(store, BranchBaselineStore)

    run_id = _ingest(mainmod, "r-main", "main")
    store, _, _ = store_for_run(mainmod.db, run_id, platform=PLATFORM)
    assert isinstance(store, BranchBaselineStore), "теперь main — обычная ветка"


def test_approving_a_branch_run_does_not_touch_the_main_baseline(service):
    """Ровно тот сценарий, ради которого всё это написано."""
    client, mainmod = service
    from vistest.api.prefs import BRANCH_BASELINES, set_flag
    from vistest.config import VisTestConfig

    set_flag(mainmod.db, BRANCH_BASELINES, True)
    cfg = VisTestConfig.load()
    base = FileBaselineStore(cfg.baselines_path(PLATFORM))
    base.save(BaselineRecord(name="login.png", image=_img(100)))

    run_id = _ingest(mainmod, "r1", "feature/pay")
    comp = mainmod.db.one(
        "SELECT c.id FROM comparison c WHERE c.run_id=?", (run_id,))

    # Кладём «снятую» картинку так, как это делает раннер.
    from vistest.capture.playwright_capture import _write_png
    art = cfg.root_path / "runs" / "r1" / "login" / "actual.png"
    art.parent.mkdir(parents=True, exist_ok=True)
    _write_png(art, _img(200))
    mainmod.db.execute(
        "UPDATE comparison SET artifacts=? WHERE id=?",
        ('{"actual": "/local/runs/r1/login/actual.png"}', comp["id"]))

    r = client.post(f"/api/comparisons/{comp['id']}/approve")
    assert r.status_code == 200, r.text
    assert r.json()["baseline_updated"] is True
    assert "branch-baselines" in r.json()["directory"]

    # Основной эталон не изменился — это и есть весь смысл.
    assert int(base.load("login.png").image[0, 0, 0]) == 100


# --------------------------------------------------------------------------- #
#  Влить, перечислить, выбросить
# --------------------------------------------------------------------------- #
def _seed_branch(mainmod, branch="feature/pay", value=200):
    """Наложение ветки с одним переопределённым снимком."""
    from vistest.api.prefs import BRANCH_BASELINES, set_flag
    from vistest.api.stores import branch_layer
    from vistest.config import VisTestConfig

    set_flag(mainmod.db, BRANCH_BASELINES, True)
    cfg = VisTestConfig.load()
    base = FileBaselineStore(cfg.baselines_path(PLATFORM))
    base.save(BaselineRecord(name="login.png", image=_img(100)))

    store, _, _ = branch_layer(base, cfg.baselines_path(PLATFORM), db=mainmod.db,
                               cfg=cfg, platform=PLATFORM, branch=branch,
                               scope="global")
    store.save(BaselineRecord(name="login.png", image=_img(value)))
    return base


def test_branches_are_listed_by_their_real_name_not_the_slug(service):
    """Каталог — это уже слаг; показывать его человеку значит заставить гадать."""
    client, mainmod = service
    _seed_branch(mainmod)

    data = client.get("/api/branches").json()
    assert data["enabled"] is True
    assert data["default_branch"] == "main"
    assert len(data["branches"]) == 1
    entry = data["branches"][0]
    assert entry["branch"] == "feature/pay"
    assert entry["resolved"] is True
    assert entry["snapshots"] == 1


def test_the_base_branch_never_shows_up_as_an_overlay(service):
    client, mainmod = service
    _seed_branch(mainmod)
    names = [b["branch"] for b in client.get("/api/branches").json()["branches"]]
    assert "main" not in names


def test_promoting_moves_the_branch_picture_into_the_main_set(service):
    """Без этого шага ветка — ловушка.

    Решение принято, ветка слита, а main падает на том же снимке: решение
    осталось в наложении, которое после слияния уже никто не читает.
    """
    client, mainmod = service
    base = _seed_branch(mainmod)
    from vistest.api.auth import create_user
    create_user(mainmod.db, "anna", "password123", role="admin")
    client.post("/api/auth/login", json={"login": "anna", "password": "password123"})

    assert int(base.load("login.png").image[0, 0, 0]) == 100
    r = client.post("/api/branches/promote", json={"branch": "feature/pay"})
    assert r.status_code == 200, r.text
    assert [p["name"] for p in r.json()["promoted"]] == ["login.png"]

    assert int(base.load("login.png").image[0, 0, 0]) == 200


def test_promoting_keeps_the_previous_picture_in_history(service):
    """«Влить» делают второпях — откат понадобится."""
    client, mainmod = service
    base = _seed_branch(mainmod)
    from vistest.api.auth import create_user
    create_user(mainmod.db, "anna", "password123", role="admin")
    client.post("/api/auth/login", json={"login": "anna", "password": "password123"})

    client.post("/api/branches/promote", json={"branch": "feature/pay"})
    versions = base.versions("login.png")
    assert len(versions) >= 2
    assert base.version_image("login.png", 1) is not None


def test_promoting_the_base_branch_is_refused_with_a_reason(service):
    client, mainmod = service
    _seed_branch(mainmod)
    from vistest.api.auth import create_user
    create_user(mainmod.db, "anna", "password123", role="admin")
    client.post("/api/auth/login", json={"login": "anna", "password": "password123"})

    r = client.post("/api/branches/promote", json={"branch": "main"})
    assert r.status_code == 400
    assert "base branch" in r.json()["detail"]


def test_dropping_a_branch_leaves_the_main_set_alone(service):
    """«Выбросить» и «влить» — противоположные решения, путать их нельзя."""
    client, mainmod = service
    base = _seed_branch(mainmod)
    from vistest.api.auth import create_user
    create_user(mainmod.db, "anna", "password123", role="admin")
    client.post("/api/auth/login", json={"login": "anna", "password": "password123"})

    r = client.request("DELETE", "/api/branches/feature/pay")
    assert r.status_code == 200, r.text
    assert client.get("/api/branches").json()["branches"] == []
    assert int(base.load("login.png").image[0, 0, 0]) == 100


def test_dropping_the_base_branch_is_refused(service):
    client, mainmod = service
    _seed_branch(mainmod)
    from vistest.api.auth import create_user
    create_user(mainmod.db, "anna", "password123", role="admin")
    client.post("/api/auth/login", json={"login": "anna", "password": "password123"})

    r = client.request("DELETE", "/api/branches/main")
    assert r.status_code == 400
    assert "base branch" in r.json()["detail"]


def test_only_an_admin_may_promote_or_drop(service):
    client, mainmod = service
    _seed_branch(mainmod)
    from vistest.api.auth import create_user
    create_user(mainmod.db, "vic", "password123", role="viewer")
    client.post("/api/auth/login", json={"login": "vic", "password": "password123"})

    assert client.get("/api/branches").status_code == 200
    assert client.post("/api/branches/promote",
                       json={"branch": "feature/pay"}).status_code == 403
    assert client.request("DELETE", "/api/branches/feature/pay").status_code == 403


def test_the_mode_is_switched_from_the_settings_api(service):
    client, mainmod = service
    from vistest.api.auth import create_user
    create_user(mainmod.db, "anna", "password123", role="admin")
    client.post("/api/auth/login", json={"login": "anna", "password": "password123"})

    assert client.get("/api/settings/branches").json()["enabled"] is False
    r = client.put("/api/settings/branches",
                   json={"enabled": True, "default_branch": "trunk"})
    assert r.status_code == 200
    assert r.json() == {"enabled": True, "default_branch": "trunk"}
    assert client.get("/api/settings/branches").json()["default_branch"] == "trunk"


def test_an_empty_base_branch_name_is_refused(service):
    client, mainmod = service
    from vistest.api.auth import create_user
    create_user(mainmod.db, "anna", "password123", role="admin")
    client.post("/api/auth/login", json={"login": "anna", "password": "password123"})
    assert client.put("/api/settings/branches",
                      json={"default_branch": "  "}).status_code == 400
