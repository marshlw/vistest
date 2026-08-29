# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Куда попадает принятый эталон.

Здесь закрыт дефект, который человек видит так: «принял скриншот с ошибкой как
новый эталон — следующий прогон падает на том же самом месте».

Причина была в том, что хранилище эталонов выбиралось **не по прогону**, а по
сохранённой настройке проекта (или, в сервисном `approve`, вообще безусловно —
глобальное хранилище сервиса). Один и тот же снимок `login.png` существует в
трёх местах сразу, и апрув уходил не в то, откуда читает следующий прогон.

Тесты ниже фиксируют три вещи:

* `store_for` разводит три scope по трём разным каталогам и раскладкам;
* `external.approve(scope=...)` слушает источник прогона, а не настройку;
* `_store_for(own=...)` в observe-режиме не путает раскладку хранилища —
  именно эта путаница давала «no baseline in ...» на каждом снимке.
"""

from __future__ import annotations

import numpy as np
import pytest

from vistest.api.stores import ScopeError, store_for
from vistest.config import VisTestConfig, platform_key
from vistest.external import _store_for, approve
from vistest.projects import BaselineDir, Project
from vistest.storage import (
    BaselineRecord,
    ExternalBaselineStore,
    FileBaselineStore,
)


def _png(value: int = 40) -> np.ndarray:
    return np.full((8, 8, 3), value, dtype=np.uint8)


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "their-repo"
    (root / "screens").mkdir(parents=True)
    (root / "snapshots").mkdir(parents=True)
    return Project(key="theirs", name="Theirs", root=str(root), tests="screens",
                   baseline_store="project",
                   baselines=[BaselineDir(dir="snapshots", label="local")])


# --------------------------------------------------------------------------- #
#  store_for: три scope — три разных места
# --------------------------------------------------------------------------- #
def test_three_scopes_are_three_different_directories(project, tmp_path,
                                                      monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = VisTestConfig.load()
    pf = platform_key()

    _, global_dir = store_for("global", cfg=cfg, platform=pf)
    _, vistest_dir = store_for("vistest", cfg=cfg, platform=pf, project=project)
    _, project_dir = store_for("project", cfg=cfg, platform=pf, project=project)

    assert len({global_dir, vistest_dir, project_dir}) == 3
    # Набор VisTest лежит вне чужого репозитория, набор проекта — внутри.
    assert project.root_path in project_dir.parents or project_dir == project.root_path
    assert project.root_path not in vistest_dir.parents


def test_scope_picks_the_matching_layout(project, tmp_path, monkeypatch):
    """Раскладка обязана соответствовать каталогу.

    Раньше каталог брался из источника прогона, а тип хранилища — из настройки
    проекта. `FileBaselineStore` держит `<имя>/baseline.png`,
    `ExternalBaselineStore` — плоский `<имя>.png`. Несовпадение давало
    `exists() == False` на каждом снимке.
    """
    monkeypatch.chdir(tmp_path)
    cfg = VisTestConfig.load()

    vistest_store, _ = store_for("vistest", cfg=cfg, platform=platform_key(),
                                 project=project)
    project_store, _ = store_for("project", cfg=cfg, platform=platform_key(),
                                 project=project)

    assert isinstance(vistest_store, FileBaselineStore)
    assert isinstance(project_store, ExternalBaselineStore)


def test_recorded_directory_wins_over_recomputation(project, tmp_path,
                                                    monkeypatch):
    """Каталог прогона важнее правил проекта.

    Правила выбора папки эталонов срабатывают по окружению, а окружение через
    месяц другое. Апрув обязан попасть туда, с чем сравнивался прогон.
    """
    monkeypatch.chdir(tmp_path)
    cfg = VisTestConfig.load()
    recorded = tmp_path / "recorded-elsewhere"

    _, directory = store_for("vistest", cfg=cfg, platform=platform_key(),
                             project=project, baseline_dir=recorded)
    assert directory == recorded


def test_project_scope_without_project_is_an_explained_error(tmp_path,
                                                             monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = VisTestConfig.load()
    with pytest.raises(ScopeError, match="no longer connected"):
        store_for("vistest", cfg=cfg, platform=platform_key(), project=None)


def test_unknown_scope_is_rejected(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ScopeError, match="Unknown baseline scope"):
        store_for("whatever", cfg=VisTestConfig.load())


# --------------------------------------------------------------------------- #
#  approve: слушает прогон, а не настройку проекта
# --------------------------------------------------------------------------- #
def test_approve_from_a_vistest_run_lands_in_the_vistest_set(project, tmp_path,
                                                             monkeypatch):
    """Главный сценарий дефекта.

    Настройка проекта — «их PNG». Прогон запущен кнопкой «Run on VisTest
    baselines». Апрув обязан изменить набор VisTest, иначе следующий такой же
    прогон снова упадёт на том же снимке.
    """
    monkeypatch.chdir(tmp_path)
    cfg = VisTestConfig.load()
    assert not project.uses_own_baselines()      # настройка проекта — «их PNG»

    actual = tmp_path / "actual.png"
    from vistest.capture.playwright_capture import _write_png
    _write_png(actual, _png(200))

    path = approve(project, "login.png", cfg=cfg, actual_png=actual,
                   scope="vistest")

    expected_dir = project.vistest_baselines_path(cfg, platform_key())
    assert expected_dir in path.parents
    # И ровно то, чего не должно было произойти: чужой репозиторий не тронут.
    assert not (project.root_path / "snapshots" / "login.png").exists()

    # Следующий прогон на эталонах VisTest действительно видит новый эталон.
    store, _ = store_for("vistest", cfg=cfg, platform=platform_key(),
                         project=project)
    assert store.exists("login.png")
    assert int(store.load("login.png").image[0, 0, 0]) == 200


def test_approve_from_a_project_run_lands_in_the_repository(project, tmp_path,
                                                            monkeypatch):
    """Обратное направление: настройка «набор VisTest», прогон — на их PNG."""
    monkeypatch.chdir(tmp_path)
    cfg = VisTestConfig.load()
    project.baseline_store = "vistest"
    assert project.uses_own_baselines()

    actual = tmp_path / "actual.png"
    from vistest.capture.playwright_capture import _write_png
    _write_png(actual, _png(120))

    path = approve(project, "login.png", cfg=cfg, actual_png=actual,
                   scope="project")

    assert project.root_path in path.parents
    # Набор VisTest не тронут — решение было не про него.
    assert not (project.vistest_baselines_path(cfg, platform_key())
                / "login.png").exists()


def test_approve_without_scope_falls_back_to_the_project_setting(project,
                                                                 tmp_path,
                                                                 monkeypatch):
    """Совместимость: без указания scope поведение прежнее."""
    monkeypatch.chdir(tmp_path)
    cfg = VisTestConfig.load()

    actual = tmp_path / "actual.png"
    from vistest.capture.playwright_capture import _write_png
    _write_png(actual, _png(90))

    path = approve(project, "login.png", cfg=cfg, actual_png=actual)
    assert project.root_path in path.parents


def test_approve_rejects_an_unknown_scope(project, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    actual = tmp_path / "actual.png"
    from vistest.capture.playwright_capture import _write_png
    _write_png(actual, _png())
    with pytest.raises(RuntimeError, match="Unknown baseline scope"):
        approve(project, "login.png", cfg=VisTestConfig.load(),
                actual_png=actual, scope="global")


# --------------------------------------------------------------------------- #
#  observe-режим: раскладка хранилища берётся из прогона
# --------------------------------------------------------------------------- #
def test_observe_store_follows_the_run_not_the_setting(project, tmp_path,
                                                       monkeypatch):
    """`_store_for(own=True)` при настройке «их PNG».

    Раньше здесь возвращался `ExternalBaselineStore` поверх раскладки
    `FileBaselineStore`, и `exists()` отвечал False на каждом снимке.
    """
    monkeypatch.chdir(tmp_path)
    cfg = VisTestConfig.load()
    baseline_dir = project.vistest_baselines_path(cfg, platform_key())
    baseline_dir.mkdir(parents=True, exist_ok=True)

    store = _store_for(project, cfg, baseline_dir, own=True)
    assert isinstance(store, FileBaselineStore)

    store.save(BaselineRecord(name="login.png", image=_png(77)))
    # Именно этот вызов раньше давал False → «no baseline in ...».
    assert store.exists("login.png")


def test_observe_store_without_own_keeps_the_old_behaviour(project, tmp_path,
                                                           monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = VisTestConfig.load()
    store = _store_for(project, cfg, project.root_path / "snapshots")
    assert isinstance(store, ExternalBaselineStore)
