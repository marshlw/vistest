# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Три вида запуска со стороны сервиса.

Разница между «по одному на браузер» и «общий по всем» не косметическая.
Первое — N прогонов в истории: у каждого свой вердикт, свой статус-чек в CI и
свой ключ сериализации, то есть они идут параллельно. Второе — один прогон и
один вопрос «эта страница сломалась?».

Проверяется здесь именно это: сколько задач заведено, какими ключами они
разведены и что происходит, когда браузером управлять нельзя.
"""

from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient


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

    import vistest.api.main as mainmod
    importlib.reload(mainmod)

    from vistest.api.jobs import runner
    # Задачи не выполняем: проверяется, что и с каким ключом заведено, а не
    # чем кончился чужой pytest. Настоящий запуск поднял бы браузер.
    started: list = []
    monkeypatch.setattr(
        runner, "submit",
        lambda kind, title, fn, **kw: started.append(
            {"kind": kind, "title": title, **kw}) or type(
                "J", (), {"id": f"job{len(started)}", "status": "queued"})())
    monkeypatch.setattr(runner, "busy", lambda _k: None)
    return TestClient(mainmod.app), mainmod, started


def _connect(tmp_path, mainmod, **over):
    """Подключённый проект на диске — тот же путь, каким его заводит интерфейс."""
    from vistest.config import VisTestConfig
    from vistest.projects import Project, ProjectRegistry

    root = tmp_path / "their-repo"
    (root / "tests").mkdir(parents=True, exist_ok=True)
    project = Project(key="shop", name="Shop", root=str(root), tests="tests",
                      baseline_store="vistest", **over)
    ProjectRegistry(VisTestConfig.load()).save(project)
    return project


def _admin(mainmod, client):
    from vistest.api.auth import create_user
    create_user(mainmod.db, "anna", "password123", role="admin")
    client.post("/api/auth/login",
                json={"login": "anna", "password": "password123"})


# --------------------------------------------------------------------------- #
#  Проекты
# --------------------------------------------------------------------------- #
def test_one_browser_is_one_ordinary_run(service, tmp_path):
    client, mainmod, started = service
    _connect(tmp_path, mainmod)
    _admin(mainmod, client)

    r = client.post("/api/projects/shop/run", json={"browser": "firefox"})
    assert r.status_code == 200, r.text
    assert r.json()["browser"] == "firefox"
    assert len(started) == 1


def test_each_browser_separately_starts_a_run_per_browser(service, tmp_path):
    """N прогонов, а не один: у каждого свой вердикт и свой статус-чек."""
    client, mainmod, started = service
    _connect(tmp_path, mainmod)
    _admin(mainmod, client)

    r = client.post("/api/projects/shop/run",
                    json={"browsers": ["chromium", "firefox", "webkit"],
                          "mode": "separate"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "separate"
    assert [j["browser"] for j in body["jobs"]] == ["chromium", "firefox", "webkit"]
    assert len(started) == 3


def test_separate_runs_are_not_serialised_against_each_other(service, tmp_path):
    """Ключ сериализации на браузер, а не на проект.

    Наборы эталонов у браузеров разные — писать друг другу они не могут, и
    потому идут параллельно. Ровно это человек и просит, когда говорит
    «одновременно во всех».
    """
    client, mainmod, started = service
    _connect(tmp_path, mainmod)
    _admin(mainmod, client)

    client.post("/api/projects/shop/run",
                json={"browsers": ["chromium", "firefox"], "mode": "separate"})
    keys = [j["lock_key"] for j in started]
    assert len(set(keys)) == 2, f"прогоны заперты одним ключом: {keys}"
    assert all(k.startswith("project:shop:") for k in keys)


def test_recapturing_project_pngs_stays_serialised(service, tmp_path):
    """Исключение: пересъёмка пишет в ИХ общий каталог PNG, один на все браузеры.

    Там параллель — это гонка записи, и прогон снова сериализуется по проекту.
    """
    client, mainmod, started = service
    _connect(tmp_path, mainmod)
    _admin(mainmod, client)

    r = client.post("/api/projects/shop/run",
                    json={"browsers": ["chromium", "firefox"], "mode": "separate",
                          "baselines": "project", "update_baselines": True})
    assert r.json()["parallel"] is False
    assert {j["lock_key"] for j in started} == {"project:shop"}


def test_all_browsers_together_is_one_job(service, tmp_path):
    client, mainmod, started = service
    _connect(tmp_path, mainmod)
    _admin(mainmod, client)

    r = client.post("/api/projects/shop/run",
                    json={"browsers": ["chromium", "firefox"], "mode": "together"})
    body = r.json()
    assert body["mode"] == "together" and body["browsers"] == ["chromium", "firefox"]
    assert len(started) == 1


def test_several_browsers_without_a_mode_mean_one_run(service, tmp_path):
    """Человек, перечисливший три браузера и не сказавший больше ничего,
    спрашивает про страницу, а не про три отчёта."""
    client, mainmod, started = service
    _connect(tmp_path, mainmod)
    _admin(mainmod, client)

    r = client.post("/api/projects/shop/run",
                    json={"browsers": ["chromium", "firefox"]})
    assert r.json()["mode"] == "together"


# --------------------------------------------------------------------------- #
#  Честный отказ
# --------------------------------------------------------------------------- #
def test_a_project_run_by_its_own_command_is_refused_with_a_reason(service, tmp_path):
    """Три одинаковых прогона chromium показали бы покрытие, которого нет."""
    client, mainmod, started = service
    _connect(tmp_path, mainmod, runner="command",
             command=["npx", "playwright", "test"])
    _admin(mainmod, client)

    r = client.post("/api/projects/shop/run",
                    json={"browsers": ["chromium", "firefox"], "mode": "separate"})
    assert r.status_code == 400
    assert "своей командой" in r.json()["detail"]
    assert not started, "отказали — значит ничего не запускали"


def test_the_same_project_still_runs_without_a_browser(service, tmp_path):
    """Отказ касается выбора браузера, а не прогона вообще."""
    client, mainmod, started = service
    _connect(tmp_path, mainmod, runner="command", command=["npx", "test"])
    _admin(mainmod, client)

    assert client.post("/api/projects/shop/run", json={}).status_code == 200
    assert len(started) == 1


def test_the_project_list_says_why_the_choice_is_unavailable(service, tmp_path):
    """Пункт меню, который заведомо ответит отказом, — не строгость, а неправда.

    Интерфейс обязан узнать причину до показа.
    """
    client, mainmod, _started = service
    _connect(tmp_path, mainmod, runner="command", command=["npx", "test"])
    _admin(mainmod, client)

    p = client.get("/api/projects").json()["projects"][0]
    assert p["browser_control"] == "none" and p["browser_refusal"]


# --------------------------------------------------------------------------- #
#  Наши собственные тесты
# --------------------------------------------------------------------------- #
def test_our_own_tests_take_the_same_three_shapes(service, tmp_path):
    """Здесь отказываться не от чего: тесты собраны нами, `page` — фикстура
    pytest-playwright, а она понимает `--browser`."""
    client, mainmod, started = service
    _admin(mainmod, client)
    (tmp_path / "tests").mkdir(exist_ok=True)
    (tmp_path / "tests" / "test_visual_a.py").write_text("def test_a():\n    pass\n")

    one = client.post("/api/tests/run",
                      json={"path": "tests/test_visual_a.py", "browser": "webkit"})
    assert one.status_code == 200, one.text
    assert one.json()["browser"] == "webkit"

    started.clear()
    many = client.post("/api/tests/run",
                       json={"path": "tests/test_visual_a.py",
                             "browsers": ["chromium", "firefox"],
                             "mode": "separate"})
    assert many.json()["mode"] == "separate" and len(started) == 2
    assert len({j["lock_key"] for j in started}) == 2

    started.clear()
    together = client.post("/api/tests/run",
                           json={"path": "tests/test_visual_a.py",
                                 "browsers": ["chromium", "firefox"],
                                 "mode": "together"})
    assert together.json()["mode"] == "together" and len(started) == 1


def test_the_known_browsers_come_from_the_service(service):
    """Список движков зашивать в интерфейс нельзя: их поддерживает Playwright,
    и знать про них обязан тот, кто его запускает."""
    client, mainmod, _started = service
    _admin(mainmod, client)
    body = client.get("/api/matrix").json()
    assert body["known_browsers"] == ["chromium", "firefox", "webkit"]


# --------------------------------------------------------------------------- #
#  Что сломалось на первом же настоящем прогоне
#
#  Прогон «во всех браузерах» на проекте, у которого набор эталонов VisTest снят
#  только под chromium: chromium отработал полторы минуты и записал прогон в
#  историю, firefox отказал — и задача умерла целиком. webkit не запустился
#  вовсе. Обещание «остальные досчитаются» осталось в комментарии, потому что
#  ловился только `JobFailure`, а `run_project` бросает обычное исключение.
# --------------------------------------------------------------------------- #
class _Job:
    def __init__(self):
        self.lines: list[tuple[str, str]] = []
        self.cancelled = False
        self.progress = 0.0

    def say(self, text, level="info"):
        self.lines.append((level, text))

    def text(self) -> str:
        return "\n".join(t for _lvl, t in self.lines)


@pytest.fixture
def run_all(service, tmp_path, monkeypatch):
    """Роут, у которого задачи выполняются прямо здесь, а не в фоне.

    Проверяется поведение ВНУТРИ задачи — кто досчитался, кто нет, — и гонять
    для этого настоящий раннер значило бы проверять раннер.
    """
    client, mainmod, _started = service
    _connect(tmp_path, mainmod)
    _admin(mainmod, client)

    from vistest.api.jobs import runner

    jobs: list[_Job] = []

    def run_now(kind, title, fn, **kw):
        job = _Job()
        jobs.append(job)
        try:
            job.result, job.error = fn(job), None
        except Exception as e:
            job.result, job.error = None, str(e)
        return type("J", (), {"id": f"job{len(jobs)}", "status": "done"})()

    monkeypatch.setattr(runner, "submit", run_now)
    monkeypatch.setattr(runner, "busy", lambda _k: None)
    return client, mainmod, jobs


def test_a_browser_without_baselines_does_not_kill_the_others(run_all, monkeypatch):
    """Ровно то, что случилось: firefox уронил задачу и webkit не запустился.

    Проверяется на обычном `RuntimeError`, а не на `JobFailure`: ловился раньше
    только второй, и именно поэтому обещание не работало.
    """
    client, _mainmod, jobs = run_all
    from vistest import external

    seen: list[str] = []

    def fake_run(project, **kw):
        browser = kw.get("browser") or ""
        seen.append(browser)
        if browser == "firefox":
            raise external.ExternalRunRefused(
                "The VisTest baseline set for docker-firefox-1x has not been captured")
        raise RuntimeError("boom") if browser == "webkit" else None

    monkeypatch.setattr("vistest.external.run_project", fake_run)

    client.post("/api/projects/shop/run",
                json={"browsers": ["chromium", "firefox", "webkit"],
                      "mode": "together"})

    assert seen == ["chromium", "firefox", "webkit"], \
        f"после падения браузера остальные не запустились: {seen}"


class _FakeRun:
    """Прогон, который состоялся. Ровно та поверхность, которой пользуется роут."""

    errors: list = []
    output: list = []
    setup_failures: list = []

    def summary(self):
        return {"total": 3, "failed": 0, "new": 0, "errored": 0,
                "tests": 3, "tests_failed": 0, "tests_not_started": 0}

    def to_dict(self):
        return {"results": [], "run_dir": "", "baseline_scope": "vistest"}


def test_the_summary_says_in_how_many_browsers_the_run_happened(run_all, monkeypatch):
    """Лог к этому моменту в сотни строк чужого pytest.

    «В скольких браузерах прогон на самом деле состоялся» — то, ради чего в
    него и полезут. Строка появляется только когда состоялся хоть один: если не
    состоялся ни один, это не «не досчитались», это провал, и ответ другой.
    """
    client, _mainmod, jobs = run_all
    from vistest import external

    def fake_run(project, **kw):
        if kw.get("browser") == "firefox":
            raise external.ExternalRunRefused("the set is not captured")
        return _FakeRun()

    monkeypatch.setattr("vistest.external.run_project", fake_run)
    client.post("/api/projects/shop/run",
                json={"browsers": ["chromium", "firefox"], "mode": "together"})

    log = jobs[0].text()
    assert "did not finish" in log and "firefox" in log, log
    assert "1 of 2 browsers" in log, log


def test_when_no_browser_survived_it_is_a_failure_not_a_remark(run_all, monkeypatch):
    """Прогон, в котором не состоялся ни один браузер, обязан быть красным.

    «Не досчитались всех» — это не замечание, это ноль результата, и в историю
    он уходить не должен зелёным.
    """
    from vistest.api.jobs import JobFailure

    client, _mainmod, jobs = run_all
    from vistest import external

    monkeypatch.setattr(
        "vistest.external.run_project",
        lambda *a, **k: (_ for _ in ()).throw(
            external.ExternalRunRefused("набор не снят")))
    client.post("/api/projects/shop/run",
                json={"browsers": ["chromium", "firefox"], "mode": "together"})
    assert jobs[0].error and "not a single browser" in jobs[0].error
    assert JobFailure  # тип, которым это выражается


def test_missing_sets_are_named_before_the_run_starts(run_all, monkeypatch):
    """Иначе про firefox человек узнаёт после полутора минут chromium."""
    client, _mainmod, jobs = run_all
    from vistest import external

    monkeypatch.setattr(external, "browsers_with_baselines",
                        lambda *a, **k: {"chromium": True, "firefox": False,
                                         "webkit": False})
    monkeypatch.setattr("vistest.external.run_project",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))

    r = client.post("/api/projects/shop/run",
                    json={"browsers": ["chromium", "firefox"],
                          "baselines": "vistest", "mode": "together"})
    assert r.json()["missing_baselines"] == ["firefox"]

    warned = [t for lvl, t in jobs[0].lines if lvl == "warn"]
    assert warned and "firefox" in warned[0], jobs[0].lines
    # И первой строкой, а не после первого браузера.
    assert jobs[0].lines[0][0] == "warn"


def test_an_explained_refusal_does_not_drag_a_traceback_with_it(run_all, monkeypatch):
    """На объяснённом отказе трейс говорит «инструмент сломался» там, где он
    отработал как задумано, — и человек читает пятнадцать строк `File "..."`
    вместо одной строки с ответом."""
    from vistest.api.jobs import JobFailure

    client, _mainmod, jobs = run_all
    from vistest import external

    monkeypatch.setattr(
        "vistest.external.run_project",
        lambda *a, **k: (_ for _ in ()).throw(
            external.ExternalRunRefused("the baseline set is not captured yet")))

    with pytest.raises(JobFailure) as e:
        # Один браузер: отказ обязан долететь до задачи как объяснённый, а не
        # как поломка.
        client.post("/api/projects/shop/run", json={"browser": "firefox"})
        raise JobFailure(jobs[0].error or "")
    assert "not captured" in str(e.value)


def test_the_refusal_names_the_browser_and_what_already_exists(tmp_path,
                                                               monkeypatch):
    """Совет обязан вести туда, где помогает.

    Здесь стояло «снимите набор через ⋯ → Capture VisTest baselines», и для
    многобраузерного прогона это тупик: то действие снимает набор браузера по
    умолчанию — chromium, который как раз и работал.
    """
    from vistest.config import VisTestConfig, platform_key
    from vistest.external import ExternalRunRefused, run_project
    from vistest.projects import Project

    cfg = VisTestConfig()
    cfg.paths.root = str(tmp_path / ".vistest")
    root = tmp_path / "their-repo"
    (root / "tests").mkdir(parents=True)
    project = Project(key="shop", name="Shop", root=str(root), tests="tests",
                      baseline_store="vistest")

    # У chromium набор есть, у firefox — нет.
    have = project.vistest_baselines_path(cfg, platform_key("chromium"))
    (have / "a.png").mkdir(parents=True)
    (have / "a.png" / "baseline.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    with pytest.raises(ExternalRunRefused) as e:
        run_project(project, cfg=cfg, browser="firefox", log=lambda *_a: None)

    text = str(e.value)
    assert "firefox" in text, "в отказе не назван браузер"
    assert "chromium" in text, "не сказано, у каких браузеров набор уже есть"
    assert "Snap VisTest baselines" in text
