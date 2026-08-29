# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Долги бэкенда: тихие ошибки, каждая из которых портит данные.

Общее у них одно — ни одна не падает и не пишет в лог. Хранилище схлопывает два
снимка в один, удаление эталона уносит чужую историю, метрика игнорирует фильтр
над собой, внешний прогон приезжает без коммита. Всё это выглядит как рабочая
система, пока не начнёшь сверять числа.
"""

from __future__ import annotations

import threading

import pytest


# --------------------------------------------------------------------------- #
#  Хранилище: два разных снимка не могут быть одним
# --------------------------------------------------------------------------- #
def test_deep_names_with_a_common_tail_do_not_collide(tmp_path):
    """`"/".join(parts[-4:])` молча отбрасывал НАЧАЛО имени.

    Значит и различаться имена должны именно в начале — там, где различие
    выбрасывалось. Два биллинга разных продуктов ложились в один каталог и
    перезаписывали эталон друг друга; человек видел дифф между чужими
    страницами и не понимал, откуда он взялся.
    """
    from vistest.storage.fs import _safe

    billing = _safe("billing/eu/checkout/payment/form.png")
    shop = _safe("shop/eu/checkout/payment/form.png")
    assert billing != shop, "разные снимки не могут делить один каталог эталонов"


def test_ordinary_names_are_untouched(tmp_path):
    """Иначе правка утащила бы за собой все существующие эталоны."""
    from vistest.storage.fs import _safe

    assert _safe("login.png") == "login"
    assert _safe("shop.example/login.png") == "shop.example/login"
    assert _safe("a/b/c/d.png") == "a/b/c/d"


def test_the_path_still_cannot_escape_and_stays_bounded():
    from vistest.storage.fs import MAX_DEPTH, _safe

    assert ".." not in _safe("../../../etc/passwd.png")
    deep = _safe("/".join(f"s{i}" for i in range(20)) + ".png")
    assert len(deep.split("/")) == MAX_DEPTH


def test_two_stores_of_deep_names_keep_separate_baselines(tmp_path):
    """Проверка не на строке, а на диске: эталон один не затирает другой."""
    import numpy as np

    from vistest.storage import BaselineRecord, FileBaselineStore

    store = FileBaselineStore(tmp_path / "baselines")
    store.save(BaselineRecord(name="billing/eu/checkout/payment/form.png",
                              image=np.full((8, 8, 3), 10, dtype=np.uint8)))
    store.save(BaselineRecord(name="shop/eu/checkout/payment/form.png",
                              image=np.full((8, 8, 3), 250, dtype=np.uint8)))

    billing = store.load("billing/eu/checkout/payment/form.png")
    shop = store.load("shop/eu/checkout/payment/form.png")
    assert int(billing.image[0, 0, 0]) == 10
    assert int(shop.image[0, 0, 0]) == 250


# --------------------------------------------------------------------------- #
#  Внешний прогон и его коммит
# --------------------------------------------------------------------------- #
def test_git_info_asks_the_repository_it_was_given(tmp_path, monkeypatch):
    """Переменные CI описывают сборку сервиса, а не репозиторий проекта.

    Поэтому при явном каталоге окружение не спрашивается вовсе: лучше пустое
    поле, чем уверенно неверное.
    """
    import subprocess

    from vistest.runner import git_info

    monkeypatch.setenv("GITHUB_SHA", "0000000000000000000000000000000000000000")
    monkeypatch.setenv("GITHUB_REF_NAME", "ci-branch-of-the-service")

    repo = tmp_path / "their-repo"
    repo.mkdir()
    for cmd in (["git", "init", "-q", "-b", "their-main"],
                ["git", "config", "user.email", "a@b.c"],
                ["git", "config", "user.name", "T"],
                ["git", "commit", "-q", "--allow-empty", "-m", "first"]):
        subprocess.run(cmd, cwd=repo, check=True, capture_output=True)

    theirs = git_info(repo)
    assert theirs["branch"] == "their-main"
    assert theirs["sha"] and theirs["sha"] != "0" * 40

    # А без каталога — прежнее поведение, окружение сильнее.
    assert git_info()["branch"] == "ci-branch-of-the-service"


def test_a_published_external_run_carries_the_commit(tmp_path, monkeypatch):
    """В историю подключённые проекты приезжали с пустым git.

    В списке «—», в отчёте «—», сопоставить с их сборкой нечем, и эталон на
    ветку строить не на чем.
    """
    from vistest.api.db import Database
    from vistest.api.publish import publish_external_run

    db = Database(tmp_path / "vistest.db")
    run_id = publish_external_run(
        db, tmp_path / "artifacts",
        {"project": "acme", "run_dir": str(tmp_path / "run-1"),
         "baseline_scope": "vistest",
         "git": {"branch": "feature/pay", "sha": "abc1234def"},
         "results": [{"name": "login.png", "verdict": "pass"}]},
        project="acme", log=lambda *a: None)

    row = db.one("SELECT branch, git_sha FROM run WHERE id=?", (run_id,))
    assert row["branch"] == "feature/pay"
    assert row["git_sha"] == "abc1234def"


# --------------------------------------------------------------------------- #
#  Метрики
# --------------------------------------------------------------------------- #
def test_baseline_age_respects_the_selected_project(tmp_path):
    """Фильтр вычислялся и не использовался: числа были по всей инсталляции.

    Метрика, молча игнорирующая фильтр над собой, хуже отсутствующей — её
    читают как ответ на вопрос, которого ей не задавали.
    """
    from vistest.api.db import Database
    from vistest.api.metrics import baseline_age

    db = Database(tmp_path / "vistest.db")
    for project, name in (("shop", "login.png"), ("blog", "post.png")):
        pid = db.project_id(project)
        sid = db.snapshot_id(pid, name, "linux-chromium-1x", "chromium")
        db.execute(
            "INSERT INTO baseline(snapshot_id, version, approved_by, is_current,"
            " scope, platform, name) VALUES(?,?,?,1,'global',?,?)",
            (sid, 1, "anna", "linux-chromium-1x", name))

    assert {r["name"] for r in baseline_age(db, "*")["oldest"]} == {
        "login.png", "post.png"}
    assert [r["name"] for r in baseline_age(db, "shop")["oldest"]] == ["login.png"]
    assert baseline_age(db, "blog")["tracked"] == 1


def test_the_dashboard_no_longer_ships_two_hundred_runs(tmp_path):
    """Полные строки прогонов уезжали в каждый ответ и не читались никем."""
    from vistest.api.db import Database
    from vistest.api.metrics import summary

    db = Database(tmp_path / "vistest.db")
    data = summary(db, "*", 30)
    assert "runs" not in data
    assert "totals" in data and "severity" in data


def test_the_histogram_draws_the_threshold_that_is_actually_in_force(tmp_path):
    """Гистограмма отвечает «куда ставить порог» и рисует вертикаль «вот он».

    Взяв значение из конфига после того, как порог подвинули в настройках,
    картинка врала бы ровно в том месте, ради которого её и смотрят.
    """
    from vistest.api.db import Database
    from vistest.api.metrics import summary
    from vistest.api.thresholds import put

    db = Database(tmp_path / "vistest.db")
    assert summary(db, "*", 30)["severity"]["threshold"] == 25.0

    put(db, {"fail_severity": 61}, who="anna")
    assert summary(db, "*", 30)["severity"]["threshold"] == 61.0


# --------------------------------------------------------------------------- #
#  Удаление эталона не должно уносить чужую историю
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


def test_purging_history_stays_inside_its_project(service):
    """`name + platform` — это не личность снимка.

    Два подключённых набора могут иметь свой `login.png` на одной платформе, и
    удаление эталона одного проекта уносило историю другого — молча, ведь
    удаление это ровно то место, где побочный урон никто не ищет.
    """
    from vistest.api.baselines import _purge_history

    _, mainmod = service
    db = mainmod.db
    for project in ("shop", "blog"):
        pid = db.project_id(project)
        db.snapshot_id(pid, "login.png", "linux-chromium-1x", "chromium")

    removed = _purge_history("linux-chromium-1x", ["login.png"], "shop")
    assert removed >= 0

    left = db.query(
        "SELECT p.name FROM snapshot s JOIN project p ON p.id=s.project_id"
        " WHERE s.name='login.png'")
    assert [r["name"] for r in left] == ["blog"], "чужая история не должна пострадать"


# --------------------------------------------------------------------------- #
#  Запуск pytest из интерфейса
# --------------------------------------------------------------------------- #
def test_dangerous_pytest_flags_are_refused(service):
    """`-p` подключает произвольный плагин, то есть исполняет чужой код.

    Спасал только замок «запросы с машины сервиса», а он снимается одной
    переменной окружения.
    """
    client, _ = service
    for args in (["-p", "evil_plugin"], ["-pevil"], ["--rootdir=/"],
                 ["--import-mode=importlib"], ["-c", "/tmp/pytest.ini"]):
        r = client.post("/api/tests/run", json={"args": args})
        assert r.status_code == 400, args
        assert "not allowed" in r.json()["detail"]


def test_a_path_outside_the_tests_directory_is_refused(service):
    client, _ = service
    r = client.post("/api/tests/run", json={"path": "/etc"})
    assert r.status_code == 400
    assert "outside the tests directory" in r.json()["detail"]


def test_ordinary_arguments_still_work(service, monkeypatch):
    """Закрутить гайку так, чтобы перестал работать обычный запуск, нельзя."""
    from vistest.api.baselines import _pytest_args, _pytest_path, _tests_dir

    assert _pytest_args(["-k", "login", "-x", "--maxfail=2"]) == [
        "-k", "login", "-x", "--maxfail=2"]
    root = _tests_dir()
    root.mkdir(parents=True, exist_ok=True)
    assert _pytest_path("") == str(root)
    assert _pytest_path("test_a.py").endswith("test_a.py")
    assert _pytest_path("test_a.py::test_one").endswith("test_a.py::test_one")
