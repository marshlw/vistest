# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Порог падения, выставленный из интерфейса.

До этого порог жил ровно в одном месте — `vistest.yaml`, — а в настройках стоял
ползунок, который не отправлял никуда ни одного запроса. Он двигался, показывал
число и на этом заканчивался; начальное значение (35) даже не совпадало с
настоящим дефолтом (25). Это хуже, чем отсутствие настройки: отсутствующей
функцией не пользуются, а этой пользовались и уходили уверенные, что настроили.

Проверяется четыре вещи, и третья — главная:

* значение сохраняется и возвращается вместе с ответом «откуда оно взялось»;
* переопределение проекта сильнее глобального, глобальное сильнее конфига;
* порог реально доезжает до сравнения — и до прогонов сервиса, и до чужого
  pytest, который про нашу базу ничего не знает;
* `null` снимает переопределение, и это не то же самое, что ноль.
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
    monkeypatch.delenv("VISTEST_FAIL_SEVERITY", raising=False)
    monkeypatch.delenv("VISTEST_MAX_CHANGED_AREA_PCT", raising=False)

    import importlib

    from fastapi import APIRouter

    import vistest.api.auth as authmod
    import vistest.api.db as dbmod

    dbmod._local = threading.local()
    authmod.router = APIRouter()

    import vistest.api.main as mainmod
    importlib.reload(mainmod)
    return TestClient(mainmod.app), mainmod


def _admin(mainmod, login="anna", password="password123"):
    from vistest.api.auth import create_user
    create_user(mainmod.db, login, password, role="admin")
    return login, password


def _sign_in(client, login="anna", password="password123"):
    r = client.post("/api/auth/login", json={"login": login, "password": password})
    assert r.status_code == 200, r.text


# --------------------------------------------------------------------------- #
def test_reading_says_where_each_value_came_from(service):
    """«35» без ответа на вопрос «почему 35» — непроверяемое обещание."""
    client, _ = service
    data = client.get("/api/settings/thresholds").json()

    assert data["sources"]["fail_severity"] == "config"
    assert data["values"]["fail_severity"] == data["config"]["fail_severity"]
    assert "fail_severity" in data["editable"]
    assert data["editable"]["fail_severity"]["max"] == 100.0


def test_a_saved_value_comes_back_and_is_marked_as_an_override(service):
    client, mainmod = service
    _admin(mainmod)
    _sign_in(client)

    r = client.put("/api/settings/thresholds",
                   json={"values": {"fail_severity": 42}})
    assert r.status_code == 200, r.text
    assert r.json()["values"]["fail_severity"] == 42.0
    assert r.json()["sources"]["fail_severity"] == "global"

    again = client.get("/api/settings/thresholds").json()
    assert again["values"]["fail_severity"] == 42.0


def test_validate_always_hands_back_a_float():
    """The contract the write site leans on, pinned rather than assumed.

    Everything that reaches the database goes through this function, and the
    stored shape depends on the type it returns: an int would be written as
    `42`, a float as `42.0`, and both read back the same — so the difference
    would surface as nothing at all until something stopped parsing.
    """
    import typing

    from vistest.core.thresholds import validate

    assert typing.get_type_hints(validate)["return"] is float

    for given in (42, 42.0, "42", "4.2e1", True):
        got = validate("fail_severity", given)
        assert type(got) is float, (given, type(got))
        assert got in (42.0, 1.0)          # True is 1.0, and that is a number


def test_the_value_is_stored_as_a_number_and_not_as_python_repr(service):
    """The column used to receive `repr(number)`.

    That puts Python's idea of what a float looks like into a database: the
    shape depends on the type that reached the write and on the interpreter
    that ran it, and everything on the way out goes through `float()`, so
    nothing would report the difference until something failed to parse. Today
    a float's `repr` and SQLite's own conversion agree — which is exactly why
    this is worth pinning: the agreement is a coincidence, not a rule.
    """
    client, mainmod = service
    _admin(mainmod)
    _sign_in(client)
    client.put("/api/settings/thresholds",
               json={"values": {"fail_severity": 42, "max_changed_area_pct": 0.5}})

    rows = mainmod.db.query(
        "SELECT name, value, typeof(value) AS kind FROM setting ORDER BY name")
    stored = {r["name"]: r["value"] for r in rows}
    assert stored == {"fail_severity": "42.0", "max_changed_area_pct": "0.5"}
    #  Text, because the column has TEXT affinity and that is not being
    #  migrated; the point is that SQLite wrote it, not `repr`.
    assert {r["kind"] for r in rows} == {"text"}


def test_rows_written_by_earlier_versions_are_still_read(service):
    """No migration, and this is what makes that safe.

    Everything ever written to this column was a decimal string, and the reader
    parses with `float()`, which takes all of the spellings below. A row from
    an installation upgraded mid-week has to keep working — silently going back
    to the config default there would be the exact failure this whole round is
    about.
    """
    client, mainmod = service
    _admin(mainmod)
    _sign_in(client)

    for value in ("42.0", "42", "4.2e1", " 42 "):
        mainmod.db.execute("DELETE FROM setting")
        mainmod.db.execute(
            "INSERT INTO setting(scope, project_key, name, value, updated_by)"
            " VALUES('global','','fail_severity',?,'legacy')", (value,))

        from vistest.api.thresholds import overrides

        assert overrides(mainmod.db) == {"fail_severity": 42.0}, value
        assert client.get("/api/settings/thresholds").json()[
            "values"]["fail_severity"] == 42.0


def test_a_project_override_beats_the_global_one(service):
    client, mainmod = service
    _admin(mainmod)
    _sign_in(client)

    client.put("/api/settings/thresholds", json={"values": {"fail_severity": 42}})
    client.put("/api/settings/thresholds",
               json={"project": "acme", "values": {"fail_severity": 12}})

    everyone = client.get("/api/settings/thresholds").json()
    theirs = client.get("/api/settings/thresholds", params={"project": "acme"}).json()

    assert everyone["values"]["fail_severity"] == 42.0
    assert theirs["values"]["fail_severity"] == 12.0
    assert theirs["sources"]["fail_severity"] == "project"


def test_null_clears_the_override_and_zero_does_not(service):
    """Ноль — осмысленная настройка «падать на любом видимом различии».

    Без отдельного способа сказать «верни как в конфиге» вернуться было бы
    нельзя: любое число — это значение, а не отсутствие значения.
    """
    client, mainmod = service
    _admin(mainmod)
    _sign_in(client)

    client.put("/api/settings/thresholds", json={"values": {"fail_severity": 0}})
    zeroed = client.get("/api/settings/thresholds").json()
    assert zeroed["values"]["fail_severity"] == 0.0
    assert zeroed["sources"]["fail_severity"] == "global"

    client.put("/api/settings/thresholds", json={"values": {"fail_severity": None}})
    back = client.get("/api/settings/thresholds").json()
    assert back["sources"]["fail_severity"] == "config"
    assert back["values"]["fail_severity"] == back["config"]["fail_severity"]


def test_nonsense_is_refused_with_a_readable_reason(service):
    client, mainmod = service
    _admin(mainmod)
    _sign_in(client)

    for values, expect in (
            ({"fail_severity": 500}, "outside"),
            ({"fail_severity": "high"}, "not a number"),
            ({"morph_open_px": 3}, "not editable"),
    ):
        r = client.put("/api/settings/thresholds", json={"values": values})
        assert r.status_code == 400, values
        assert expect in r.json()["detail"], (values, r.json()["detail"])


def test_a_viewer_may_look_but_not_change(service):
    """Порог общий: тихо сдвинутый, он меняет вердикты чужих прогонов."""
    client, mainmod = service
    from vistest.api.auth import create_user
    create_user(mainmod.db, "vic", "password123", role="viewer")
    _sign_in(client, "vic")

    assert client.get("/api/settings/thresholds").status_code == 200
    assert client.put("/api/settings/thresholds",
                      json={"values": {"fail_severity": 42}}).status_code == 403


def test_the_change_lands_in_the_audit(service):
    client, mainmod = service
    _admin(mainmod)
    _sign_in(client)
    client.put("/api/settings/thresholds", json={"values": {"fail_severity": 42}})

    rows = mainmod.db.query(
        "SELECT who, target, details FROM audit WHERE action='thresholds.changed'")
    assert len(rows) == 1
    assert rows[0]["who"] == "anna"
    assert "42" in rows[0]["details"]


# --------------------------------------------------------------------------- #
#  Самое важное: порог доезжает до сравнения
# --------------------------------------------------------------------------- #
def test_the_override_reaches_the_config_a_run_uses(service):
    """Иначе настройка есть, а вердикты прежние — и вывод будет «не работает»."""
    client, mainmod = service
    _admin(mainmod)
    _sign_in(client)
    client.put("/api/settings/thresholds", json={"values": {"fail_severity": 7}})

    from vistest.api.thresholds import apply
    from vistest.config import VisTestConfig

    plain = VisTestConfig.load()
    tuned = apply(plain, mainmod.db)
    assert tuned.diff.fail_severity == 7.0
    # Копия, а не правка общего объекта: конфиг кешируется в модулях и делится
    # между запросами, и чужой порог не должен разъезжаться по всей инсталляции.
    assert plain.diff.fail_severity != 7.0


def test_the_override_reaches_someone_elses_pytest(service):
    """Прогон подключённого проекта — отдельный процесс со своим vistest.yaml.

    Он про нашу базу не знает вовсе, поэтому единственный путь порога к нему —
    переменные окружения. Без этого настройка действовала бы на прогоны сервиса
    и молча не действовала на прогоны проектов.
    """
    client, mainmod = service
    _admin(mainmod)
    _sign_in(client)
    client.put("/api/settings/thresholds",
               json={"project": "acme", "values": {"fail_severity": 9}})

    from vistest.api.thresholds import env_for

    env = env_for(mainmod.db, "acme")
    assert env["VISTEST_FAIL_SEVERITY"] == "9.0"
    # …и другой процесс действительно его применит.
    import os

    from vistest.config import VisTestConfig
    os.environ.update(env)
    try:
        assert VisTestConfig.load().diff.fail_severity == 9.0
    finally:
        for key in env:
            os.environ.pop(key, None)


def test_a_broken_environment_variable_stops_the_run_and_names_itself(
        service, monkeypatch):
    """It used to leave the default in place without a word.

    That was the wrong half of the rule. The variable is set on purpose, by a
    pipeline, to override a threshold — so a value that cannot be read means
    the override is not happening, and the run that follows measures something
    other than what was asked for. Silence there produces a bug report about
    the engine ignoring a setting, months later, with nothing in any log.

    The library mode is what made it urgent: the variable is set in somebody
    else's CI, and this message is the only thing they will see.
    """
    from vistest.config import ConfigError, VisTestConfig

    monkeypatch.setenv("VISTEST_FAIL_SEVERITY", "не число")
    with pytest.raises(ConfigError) as e:
        VisTestConfig.load()
    assert "VISTEST_FAIL_SEVERITY" in str(e.value)
    assert "не число" in str(e.value)


def test_a_threshold_outside_its_range_is_refused_too(service, monkeypatch):
    """A number is not the same as a valid one; both layers check now."""
    from vistest.config import ConfigError, VisTestConfig

    monkeypatch.setenv("VISTEST_MAX_CHANGED_AREA_PCT", "900")
    with pytest.raises(ConfigError) as e:
        VisTestConfig.load()
    assert "VISTEST_MAX_CHANGED_AREA_PCT" in str(e.value)


# --------------------------------------------------------------------------- #
#  Выгрузка прогона для CI
#
#  Живёт рядом с порогами по одной причине: и то и другое — про то, чтобы
#  результат VisTest доезжал наружу, а не оставался вкладкой, в которую надо не
#  забыть зайти.
# --------------------------------------------------------------------------- #
def _ingest(mainmod, **over):
    payload = {
        "run_id": over.pop("run_id", "ci-1"),
        "platform": "linux-chromium-1x", "browser": "chromium",
        "created_at": "2026-01-01T00:00:00",
        "git": {"branch": "main", "sha": "abc1234"},
        "totals": {"total": 2, "passed": 1, "failed": 1},
        "comparisons": [
            {"name": "shop/login.png", "verdict": "fail",
             "metrics": {"max_severity": 44.0, "ssim_global": 0.97,
                         "changed_area_pct": 0.4},
             "regions": [{"x": 1, "y": 2, "w": 10, "h": 8, "kind": "content",
                          "severity": 44.0, "selector": ".header"}]},
            {"name": "shop/cart.png", "verdict": "pass",
             "metrics": {"max_severity": 0.0, "ssim_global": 1.0}},
        ],
    }
    payload.update(over)
    return mainmod.db.ingest_run(payload, "demo")


def test_a_run_can_be_downloaded_as_junit(service):
    from xml.etree import ElementTree as ET

    client, mainmod = service
    run_id = _ingest(mainmod)

    r = client.get(f"/api/runs/{run_id}/junit.xml")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/xml")
    assert "attachment" in r.headers["content-disposition"]

    root = ET.fromstring(r.text)
    assert root.get("tests") == "2"
    assert root.get("failures") == "1"
    names = {c.get("name") for c in root.iter("testcase")}
    assert names == {"login.png", "cart.png"}


def test_junit_honours_the_review_and_can_be_told_not_to(service):
    from xml.etree import ElementTree as ET

    client, mainmod = service
    _admin(mainmod)
    _sign_in(client)
    run_id = _ingest(mainmod)

    comp = mainmod.db.one(
        "SELECT c.id FROM comparison c JOIN snapshot s ON s.id=c.snapshot_id"
        " WHERE c.run_id=? AND c.verdict='fail'", (run_id,))
    assert client.post(f"/api/comparisons/{comp['id']}/reject").status_code == 200
    # Отклонённое падение остаётся падением…
    assert ET.fromstring(
        client.get(f"/api/runs/{run_id}/junit.xml").text).get("failures") == "1"

    assert client.post(f"/api/comparisons/{comp['id']}/approve").status_code == 200
    # …а принятое как новая норма — уже нет.
    assert ET.fromstring(
        client.get(f"/api/runs/{run_id}/junit.xml").text).get("failures") == "0"
    # Но картина на момент прогона доступна отдельно.
    raw = client.get(f"/api/runs/{run_id}/junit.xml?honor_review=false")
    assert ET.fromstring(raw.text).get("failures") == "1"


def test_the_gate_answers_go_or_no_go(service):
    client, mainmod = service
    run_id = _ingest(mainmod)

    verdict = client.get(f"/api/runs/{run_id}/gate").json()
    assert verdict["ok"] is False
    assert verdict["total"] == 2
    assert verdict["blocking"][0]["name"] == "shop/login.png"


def test_the_gate_and_junit_are_not_public(service):
    client, mainmod = service
    _admin(mainmod)
    run_id = _ingest(mainmod)
    client.post("/api/auth/logout")
    assert client.get(f"/api/runs/{run_id}/junit.xml").status_code == 401
    assert client.get(f"/api/runs/{run_id}/gate").status_code == 401


def test_a_missing_run_is_a_404_not_a_500(service):
    client, _ = service
    assert client.get("/api/runs/999/junit.xml").status_code == 404
    assert client.get("/api/runs/999/gate").status_code == 404


# --------------------------------------------------------------------------- #
#  Комментарий в PR/MR.
#
#  Раньше эту сводку собирал JavaScript прямо в `.github/workflows/visual.yml` —
#  двадцать строк, разбиравших `run.json` руками. Логика отображения жила в
#  YAML, ничем не проверялась и тихо ломалась при любом переименовании поля.
# --------------------------------------------------------------------------- #
def test_a_run_renders_as_a_markdown_comment(service):
    client, mainmod = service
    run_id = _ingest(mainmod)

    r = client.get(f"/api/runs/{run_id}/comment.md")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    body = r.text
    assert body.splitlines()[0].startswith("**VisTest: 1 of 2")
    assert "shop/login.png" in body
    assert "`main`" in body


def test_the_comment_honours_the_review(service):
    """Принятое падение уже разобрано человеком — спрашивать второй раз незачем."""
    client, mainmod = service
    _admin(mainmod)
    _sign_in(client)
    run_id = _ingest(mainmod)

    comp = mainmod.db.one(
        "SELECT c.id FROM comparison c JOIN snapshot s ON s.id=c.snapshot_id"
        " WHERE c.run_id=? AND c.verdict='fail'", (run_id,))
    assert client.post(f"/api/comparisons/{comp['id']}/approve").status_code == 200

    body = client.get(f"/api/runs/{run_id}/comment.md").text
    assert "no visual changes" in body.splitlines()[0]
    assert "1 already accepted as normal" in body


def test_the_comment_links_to_this_run_when_the_service_has_a_public_url(
        service, monkeypatch):
    """Без `VISTEST_PUBLIC_URL` ссылок нет вовсе.

    Сервис не знает, под каким адресом его видят снаружи; выдумать `localhost`
    значило бы положить в ленту PR ссылку, не работающую ни у одного читателя.
    """
    client, mainmod = service
    run_id = _ingest(mainmod)
    assert "http" not in client.get(f"/api/runs/{run_id}/comment.md").text

    monkeypatch.setenv("VISTEST_PUBLIC_URL", "https://vt.example/")
    body = client.get(f"/api/runs/{run_id}/comment.md").text
    assert f"https://vt.example/ui/#/runs/{run_id}" in body


def test_the_comment_warns_where_accepting_lands(service):
    """Эталон на ветку включён — комментарий обязан сказать, в чей набор уедет.

    Иначе человек принимает изменение в MR, ветка вливается, а базовый набор
    остаётся со старыми картинками и падает снова — уже без всякого MR.
    """
    client, mainmod = service
    _admin(mainmod)
    _sign_in(client)
    run_id = _ingest(mainmod, run_id="ci-branch", git={"branch": "feature/pay",
                                                       "sha": "def5678"})

    plain = client.get(f"/api/runs/{run_id}/comment.md").text
    assert "lands on branch" not in plain

    assert client.put("/api/settings/branches",
                      json={"enabled": True}).status_code == 200
    body = client.get(f"/api/runs/{run_id}/comment.md").text
    assert "lands on branch `feature/pay`" in body


def test_the_comment_is_not_public(service):
    client, mainmod = service
    _admin(mainmod)
    run_id = _ingest(mainmod)
    client.post("/api/auth/logout")
    assert client.get(f"/api/runs/{run_id}/comment.md").status_code == 401


def test_a_missing_run_has_no_comment(service):
    client, _ = service
    assert client.get("/api/runs/999/comment.md").status_code == 404


# --------------------------------------------------------------------------- #
#  Задача переживает перезапуск.
#
#  Задача жила в памяти процесса, и при перезапуске исчезала: опрос получал
#  404, а интерфейс писал «connection to the task lost» — то есть винил сеть в
#  том, к чему сеть отношения не имеет.
# --------------------------------------------------------------------------- #
def test_a_job_interrupted_by_a_restart_is_still_answered_for(service):
    """Не 404, а объяснение. 404 значит «такого не было», а она была."""
    import time

    client, mainmod = service
    _admin(mainmod)
    _sign_in(client)

    from vistest.api.jobs import STALE_S
    mainmod.db.execute(
        "INSERT INTO job(id,kind,title,status,queued_at,heartbeat_at)"
        " VALUES('gone','project','run of shop-tests','running',1,?)",
        (time.time() - STALE_S - 10,))
    from vistest.api.jobs import runner
    runner.bind(mainmod.db)

    r = client.get("/api/jobs/gone")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "failed"
    assert "restarted" in body["error"]
    assert body["title"] == "run of shop-tests"


def test_yesterdays_jobs_are_still_in_the_list(service):
    client, mainmod = service
    _admin(mainmod)
    _sign_in(client)

    mainmod.db.execute(
        "INSERT INTO job(id,kind,title,status,queued_at,finished_at,log)"
        " VALUES('old','project','yesterday','done',1,2,'[]')")
    from vistest.api.jobs import runner
    runner.bind(mainmod.db)

    ids = {item["id"] for item in client.get("/api/jobs").json()}
    assert "old" in ids


def test_a_job_that_never_existed_is_still_a_404(service):
    """Персистентность не должна превращать опечатку в правдоподобный ответ."""
    client, mainmod = service
    _admin(mainmod)
    _sign_in(client)
    from vistest.api.jobs import runner
    runner.bind(mainmod.db)
    assert client.get("/api/jobs/no-such-job").status_code == 404


# --------------------------------------------------------------------------- #
#  Уведомления.
#
#  Единственное, что уходит из сервиса наружу само. Всё остальное работает через
#  «человек придёт и посмотрит», и это верно ровно до первого дня, когда он не
#  пришёл.
# --------------------------------------------------------------------------- #
def test_notification_settings_round_trip(service):
    client, mainmod = service
    _admin(mainmod)
    _sign_in(client)

    r = client.put("/api/settings/notifications",
                   json={"enabled": True, "webhook": "https://chat.example/hook",
                         "after_hours": 6})
    assert r.status_code == 200
    assert r.json()["enabled"] is True

    read = client.get("/api/settings/notifications").json()
    assert read["webhook"] == "https://chat.example/hook"
    assert read["after_hours"] == 6.0


def test_a_webhook_without_a_scheme_is_refused_with_400(service):
    """`chat.example/hook` — самая частая опечатка, и молча она не работает."""
    client, mainmod = service
    _admin(mainmod)
    _sign_in(client)
    r = client.put("/api/settings/notifications",
                   json={"webhook": "chat.example/hook"})
    assert r.status_code == 400
    assert "http" in r.json()["detail"]


def test_send_now_reports_what_happened(service, monkeypatch):
    """Без этой кнопки первая проверка вебхука случается через сутки.

    А опечатка в адресе выглядит ровно как «разбирать нечего» — как тишина.
    """
    client, mainmod = service
    _admin(mainmod)
    _sign_in(client)
    client.put("/api/settings/notifications",
               json={"enabled": True, "webhook": "https://chat.example/hook",
                     "after_hours": 0})

    run_id = _ingest(mainmod)
    assert run_id

    from vistest.api import notify
    sent = []
    monkeypatch.setattr(notify, "_post", lambda u, p: sent.append((u, p)))

    body = client.post("/api/settings/notifications/test").json()
    assert body["sent"] is True
    assert body["count"] == 1
    assert sent and "shop/login.png" in sent[0][1]["text"]

    # И второй раз — тоже. Кнопка существует, чтобы проверить адрес; «список тот
    # же самый» для автоматической рассылки правило верное, а для нажатой руками
    # кнопки означало бы, что она молчит ровно тогда, когда её и нажимают.
    again = client.post("/api/settings/notifications/test").json()
    assert again["sent"] is True
    assert len(sent) == 2


def test_notification_settings_are_administrator_only(service):
    """В вебхук уезжают имена снимков и веток — это не общедоступная настройка."""
    client, mainmod = service
    _admin(mainmod)
    from vistest.api.auth import create_user, set_role
    create_user(mainmod.db, "vic", "password123", role="admin")
    set_role(mainmod.db, "vic", "reviewer")
    _sign_in(client, "vic")

    assert client.get("/api/settings/notifications").status_code == 403
    assert client.put("/api/settings/notifications",
                      json={"enabled": True}).status_code == 403
    assert client.post("/api/settings/notifications/test").status_code == 403


# --------------------------------------------------------------------------- #
#  Ретеншен по расписанию.
#
#  Кнопка «почистить старое» была с самого начала, но нажимать её надо помнить —
#  то есть не нажимают, и `.vistest` съедает десятки гигабайт за пару месяцев.
# --------------------------------------------------------------------------- #
def test_retention_settings_round_trip(service):
    client, mainmod = service
    _admin(mainmod)
    _sign_in(client)

    r = client.put("/api/settings/retention",
                   json={"enabled": True, "days": 30, "keep_last": 5,
                         "max_gb": 2})
    assert r.status_code == 200
    assert client.get("/api/settings/retention").json()["keep_last"] == 5


def test_retention_is_off_by_default(service):
    """Единственное фоновое действие, которое удаляет данные."""
    client, mainmod = service
    _admin(mainmod)
    _sign_in(client)
    assert client.get("/api/settings/retention").json()["enabled"] is False


def test_keep_last_zero_is_a_400_not_a_wiped_history(service):
    client, mainmod = service
    _admin(mainmod)
    _sign_in(client)
    r = client.put("/api/settings/retention", json={"keep_last": 0})
    assert r.status_code == 400


def test_the_preview_deletes_nothing(service):
    """Включать автоудаление вслепую — значит узнать, что оно означало, назавтра."""
    client, mainmod = service
    _admin(mainmod)
    _sign_in(client)
    old_run = _ingest(mainmod, run_id="ci-old")
    _ingest(mainmod, run_id="ci-new")
    mainmod.db.execute("UPDATE run SET started_at=datetime('now', '-400 days')"
                       " WHERE id=?", (old_run,))
    mainmod.db.execute("UPDATE comparison SET review='approved'")
    # `keep_last` меньше единицы роут не принимает — это не настройка, а способ
    # снести историю одним полем формы.
    client.put("/api/settings/retention", json={"days": 30, "keep_last": 1})

    body = client.post("/api/settings/retention/preview").json()
    assert body["dry_run"] is True
    assert body["deleted"] == 1
    assert mainmod.db.one("SELECT id FROM run WHERE id=?", (old_run,))


def test_manual_cleanup_also_spares_an_unreviewed_failure(service):
    """Одна политика на кнопку и на расписание.

    Два набора правил разошлись бы на первом же изменении, и разошлись бы молча.
    """
    client, mainmod = service
    _admin(mainmod)
    _sign_in(client)
    run_id = _ingest(mainmod)                       # падение без разбора
    _ingest(mainmod, run_id="ci-new")
    mainmod.db.execute("UPDATE run SET started_at=datetime('now', '-400 days')"
                       " WHERE id=?", (run_id,))

    # Проект указывается явно: без него роут чистит проект из конфигурации, а
    # прогоны фикстуры лежат в «demo» — тест был бы зелёным, ничего не проверив.
    r = client.post("/api/runs/cleanup",
                    json={"days": 30, "keep_last": 1, "project": "demo"})
    assert r.status_code == 200
    assert r.json()["deleted"] == 0
    assert mainmod.db.one("SELECT id FROM run WHERE id=?", (run_id,))

    # А разобранное — уходит: правило про неразобранное падение, а не про любое.
    mainmod.db.execute("UPDATE comparison SET review='approved' WHERE run_id=?",
                       (run_id,))
    r = client.post("/api/runs/cleanup",
                    json={"days": 30, "keep_last": 1, "project": "demo"})
    assert r.json()["deleted"] == 1
    assert mainmod.db.one("SELECT id FROM run WHERE id=?", (run_id,)) is None


def test_retention_settings_are_administrator_only(service):
    client, mainmod = service
    _admin(mainmod)
    from vistest.api.auth import create_user, set_role
    create_user(mainmod.db, "vic", "password123", role="admin")
    set_role(mainmod.db, "vic", "reviewer")
    _sign_in(client, "vic")

    assert client.get("/api/settings/retention").status_code == 403
    assert client.put("/api/settings/retention",
                      json={"enabled": True}).status_code == 403
    assert client.post("/api/settings/retention/preview").status_code == 403


# --------------------------------------------------------------------------- #
#  Структурный лог и сквозной идентификатор.
#
#  Диагностика держалась целиком на логе задачи; про сам сервис не было известно
#  ничего. Человек с ошибкой в интерфейсе мог только пересказать события своими
#  словами: «у меня не сохранилось» — «а когда?» — «ну, недавно».
# --------------------------------------------------------------------------- #
def test_every_answer_carries_a_request_id(service):
    client, _ = service
    from vistest.api.logs import HEADER

    r = client.get("/api/health")
    assert r.headers.get(HEADER)


def test_an_incoming_request_id_is_kept(service):
    """Своя нумерация поверх чужой рвёт трассировку ровно там, где она нужна."""
    client, _ = service
    from vistest.api.logs import HEADER

    r = client.get("/api/health", headers={"X-Request-Id": "from-the-proxy-1"})
    assert r.headers[HEADER] == "from-the-proxy-1"


def test_a_hostile_request_id_does_not_reach_the_answer_as_is(service):
    """Он попадёт в текст ответа и в лог — значит длину и алфавит режем мы."""
    client, _ = service
    from vistest.api.logs import HEADER

    r = client.get("/api/health",
                   headers={"X-Request-Id": "<script>x</script>" + "a" * 500})
    got = r.headers[HEADER]
    assert "<" not in got and ">" not in got
    assert len(got) <= 64


def test_the_log_line_carries_no_query_string(service, caplog):
    """В строке запроса живёт токен метрик, а лог переживает и ротацию, и почту."""
    import logging

    client, _ = service
    with caplog.at_level(logging.INFO, logger="vistest.access"):
        client.get("/api/runs?project=*&limit=1")
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "/api/runs" in text
    assert "project=" not in text


def test_polling_does_not_drown_the_log(service, caplog):
    """Опрос статуса ходит раз в 700 мс: одна вкладка — полторы тысячи строк в час."""
    import logging

    client, _ = service
    with caplog.at_level(logging.INFO, logger="vistest.access"):
        client.get("/api/health")
        client.get("/api/health")
    assert not [r for r in caplog.records if "/api/health" in r.getMessage()]
