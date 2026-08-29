# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Переменные окружения и аргументы pytest подключённого проекта.

Правка настроек прогона из интерфейса и подстановка `${ИМЯ}` — две половины
одной задачи: завести переменную, которую назвала диагностика, не открывая
`projects.yaml` на сервере и не кладя в него пароль.
"""

from __future__ import annotations

import pytest

from vistest.config import VisTestConfig
from vistest.external import _build_env, _expand
from vistest.projects import Adapter, BaselineDir, Project, ProjectRegistry

fastapi = pytest.importorskip("fastapi")


# --------------------------------------------------------------------------- #
#  Подстановка секретов
# --------------------------------------------------------------------------- #
def _cfg(tmp_path, secrets: str = "") -> VisTestConfig:
    if secrets:
        (tmp_path / "secrets.env").write_text(secrets, encoding="utf-8")
    cfg = VisTestConfig()
    cfg.paths.root = str(tmp_path)
    return cfg


def test_reference_is_taken_from_secrets(tmp_path):
    """Пароль лежит в secrets.env, в описании проекта — только ссылка на него."""
    cfg = _cfg(tmp_path, "VISTEST_PASSWORD=s3cret\n")
    project = Project(key="k", root=str(tmp_path),
                      env={"ACME_PASSWORD": "${VISTEST_PASSWORD}"})

    env = _build_env(project, cfg, None)

    assert env["ACME_PASSWORD"] == "s3cret"


def test_reference_can_sit_inside_a_longer_value(tmp_path):
    cfg = _cfg(tmp_path, "HOST=stand.local\n")
    project = Project(key="k", root=str(tmp_path),
                      env={"BASE_URL": "https://${HOST}/login"})

    assert _build_env(project, cfg, None)["BASE_URL"] == "https://stand.local/login"


def test_unresolved_reference_removes_the_variable_and_is_reported(tmp_path):
    """Строка «${ACME_PASSWORD}» в качестве пароля падает нечитаемо.

    Отсутствующая переменная падает понятно, и прогон говорит, какая ссылка
    осталась без источника.
    """
    cfg = _cfg(tmp_path)
    project = Project(key="k", root=str(tmp_path),
                      env={"ACME_PASSWORD": "${NOWHERE}"})

    unresolved: list[str] = []
    env = _build_env(project, cfg, None, unresolved)

    assert "ACME_PASSWORD" not in env
    assert unresolved == ["ACME_PASSWORD=${NOWHERE}"]


def test_plain_values_are_untouched(tmp_path):
    cfg = _cfg(tmp_path)
    project = Project(key="k", root=str(tmp_path), env={"CI": "true"})

    assert _build_env(project, cfg, None)["CI"] == "true"


def test_expand_does_not_recurse():
    """Подстановка одноуровневая: `${A}` берёт значение, а не разбирает его дальше."""
    out = _expand({"X": "${A}"}, {"A": "${B}"})

    assert out["X"] == "${B}"


# --------------------------------------------------------------------------- #
#  Частичное редактирование проекта
# --------------------------------------------------------------------------- #
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VISTEST_ROOT", str(tmp_path / ".vistest"))
    monkeypatch.setenv("VISTEST_PROJECTS_UI", "all")

    import importlib

    from fastapi.testclient import TestClient

    from vistest.api import main as api_main
    importlib.reload(api_main)
    return TestClient(api_main.app)


@pytest.fixture
def connected(tmp_path, monkeypatch):
    """Проект со всем, что форма редактирования не показывает."""
    monkeypatch.setenv("VISTEST_ROOT", str(tmp_path / ".vistest"))
    root = tmp_path / "their-repo"
    (root / "screens").mkdir(parents=True)
    (root / "snapshots").mkdir()

    cfg = VisTestConfig()
    cfg.paths.root = str(tmp_path / ".vistest")
    project = Project(
        key="demo", name="demo", root=str(root), tests="screens",
        pytest_args=["-m", "screenshot"],
        baselines=[BaselineDir(dir="snapshots", label="local")],
        adapter=Adapter(target="pages.Base.assert_screenshot", name_arg=1),
        baseline_store="vistest",
    )
    ProjectRegistry(cfg).save(project)
    return project


def test_patch_changes_only_what_was_sent(client, connected):
    """Форма не знает про adapter и baselines — и не имеет права их стереть."""
    r = client.patch("/api/projects/demo",
                     json={"env": {"ACME_PASSWORD": "${VISTEST_PASSWORD}"}})
    assert r.status_code == 200, r.text

    project = r.json()["project"]
    assert project["env"] == {"ACME_PASSWORD": "${VISTEST_PASSWORD}"}
    assert project["adapter"]["target"] == "pages.Base.assert_screenshot"
    assert project["adapter"]["name_arg"] == 1
    assert [b["dir"] for b in project["baselines"]] == ["snapshots"]
    assert project["pytest_args"] == ["-m", "screenshot"]
    assert project["baseline_store"] == "vistest"


def test_patch_survives_a_reread(client, connected):
    client.patch("/api/projects/demo", json={"pytest_args": ["-m", "visual", "-x"]})

    again = client.get("/api/projects").json()["projects"]
    demo = next(p for p in again if p["key"] == "demo")
    assert demo["pytest_args"] == ["-m", "visual", "-x"]


def test_bad_variable_name_is_rejected(client, connected):
    r = client.patch("/api/projects/demo", json={"env": {"2BAD": "x"}})
    assert r.status_code == 400
    assert "2BAD" in r.text


def test_line_break_in_a_value_is_rejected(client, connected):
    """Перенос строки разорвал бы переменную на две, вторая — мусорная."""
    r = client.patch("/api/projects/demo", json={"env": {"A": "one\ntwo"}})
    assert r.status_code == 400


def test_fields_outside_the_form_are_refused(client, connected):
    """Через эту дверь нельзя переставить корень проекта или точку перехвата."""
    r = client.patch("/api/projects/demo", json={"root": "/etc"})
    assert r.status_code == 400
    assert "root" in r.text


def test_empty_arguments_are_dropped(client, connected):
    r = client.patch("/api/projects/demo",
                     json={"pytest_args": ["-m", "", "  ", "screenshot"]})
    assert r.json()["project"]["pytest_args"] == ["-m", "screenshot"]


def test_patching_an_unknown_project_is_404(client):
    assert client.patch("/api/projects/nope", json={"env": {}}).status_code == 404


def test_runner_and_command_are_editable(client, connected):
    r = client.patch("/api/projects/demo",
                     json={"runner": "command",
                           "command": ["npx", "playwright", "test"]})
    assert r.status_code == 200, r.text

    project = r.json()["project"]
    assert project["runner"] == "command"
    assert project["command"] == ["npx", "playwright", "test"]
    # Точка перехвата осталась записанной, но режим честно стал observe:
    # плагин pytest в чужой процесс на Node не попадёт.
    assert project["adapter"]["target"] == "pages.Base.assert_screenshot"
    assert project["mode"] == "observe"


def test_unknown_runner_is_refused(client, connected):
    r = client.patch("/api/projects/demo", json={"runner": "gradle"})
    assert r.status_code == 400
    assert "gradle" in r.text
