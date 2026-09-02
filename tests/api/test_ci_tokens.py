# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Токены CI: доступ для пайплайна, а не для человека.

Токен приёма прогонов был один на всю инсталляцию — переменная окружения. Пока
писал один пайплайн, этого хватало; дальше арифметика перестаёт сходиться.
Подрядчику надо выдать доступ, а есть только общий секрет. Человек ушёл —
менять надо во всех пайплайнах одномоментно. А по журналу нельзя ответить, чей
пайплайн залил прогон: там у всех написано «ci-token».

Главные тесты здесь — про изоляцию (токен проекта не пишет в чужой проект) и
про то, что старая переменная окружения продолжает работать точно как раньше.
Второе не менее важно: обновление, которое ломает работающие пайплайны, — это
не улучшение доступа, это простой у заказчика.
"""

from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient

from vistest.api import citokens


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VISTEST_ROOT", str(tmp_path / ".vistest"))
    monkeypatch.delenv("VISTEST_AUTH", raising=False)
    monkeypatch.delenv("VISTEST_INGEST_TOKEN", raising=False)
    monkeypatch.delenv("VISTEST_INGEST_OPEN", raising=False)

    import importlib

    from fastapi import APIRouter

    import vistest.api.auth as authmod
    import vistest.api.db as dbmod

    dbmod._local = threading.local()
    authmod.router = APIRouter()

    import vistest.api.main as mainmod
    importlib.reload(mainmod)
    return TestClient(mainmod.app), mainmod


def _admin(mainmod, client):
    from vistest.api.auth import create_user
    create_user(mainmod.db, "anna", "password123", role="admin")
    client.post("/api/auth/login",
                json={"login": "anna", "password": "password123"})


def _run(project: str, key: str = "ci-1") -> dict:
    return {
        "run_id": key, "project": project,
        "platform": "linux-chromium-1x", "browser": "chromium",
        "created_at": "2026-01-01T00:00:00", "git": {},
        "totals": {"total": 1},
        "comparisons": [{"name": "a.png", "verdict": "pass", "metrics": {}}],
    }


# --------------------------------------------------------------------------- #
#  Хранение
# --------------------------------------------------------------------------- #
def test_the_token_is_shown_once_and_never_stored_in_the_open(service):
    """Секрет, который сервис умеет показать, утечёт вместе с доступом к сервису.

    База лежит рядом с эталонами и попадает в бэкапы: токен в открытом виде
    пережил бы там любую ротацию.
    """
    _client, mainmod = service
    out = citokens.issue(mainmod.db, name="shop CI", project="shop")
    token = out["token"]

    row = mainmod.db.one("SELECT * FROM ci_token WHERE id=?", (out["id"],))
    assert token not in str(dict(row)), "секрет лежит в базе в открытом виде"
    assert row["token_hash"] and row["token_hash"] != token
    # Префикс не секретен и нужен для опознания в списке — он же в токене.
    assert token.startswith(row["prefix"])
    assert "token" not in citokens.view(row)


def test_a_token_resolves_and_records_when_it_was_last_used(service):
    """Последнее использование — единственный ответ на «старый уже можно гасить».

    Без него ротация превращается в гадание, и старый секрет живёт до конца
    времён на всякий случай.
    """
    _client, mainmod = service
    out = citokens.issue(mainmod.db, name="shop CI", project="shop")
    assert out["last_used_at"] is None

    got = citokens.resolve(mainmod.db, out["token"])
    assert got and got["last_used_at"], "отметка не поставлена"


@pytest.mark.parametrize("bad", ["", "nonsense", "vt_", "vt_short",
                                 "Bearer vt_aaaaaaaaaaaa"])
def test_garbage_is_not_a_token(service, bad):
    _client, mainmod = service
    citokens.issue(mainmod.db, name="shop CI", project="shop")
    assert citokens.resolve(mainmod.db, bad) is None


def test_a_revoked_token_stops_working_immediately(service):
    _client, mainmod = service
    out = citokens.issue(mainmod.db, name="shop CI", project="shop")
    assert citokens.revoke(mainmod.db, out["id"]) is True
    assert citokens.resolve(mainmod.db, out["token"]) is None


def test_an_expired_token_stops_working(service):
    _client, mainmod = service
    out = citokens.issue(mainmod.db, name="shop CI", project="shop", days=30)
    mainmod.db.execute(
        "UPDATE ci_token SET expires_at=datetime('now','-1 day') WHERE id=?",
        (out["id"],))
    assert citokens.resolve(mainmod.db, out["token"]) is None


def test_an_expiry_written_by_sql_is_read_correctly(service):
    """Две формы записи в одной колонке — как оно есть, а не небрежность.

    Мы пишем `isoformat()` с зоной, sqlite в `datetime('now')` — наивную строку
    через пробел. Сравнение их напрямую бросает TypeError, и он ловился в
    «просрочен»: токен, чей срок проставили миграцией или скриптом, переставал
    работать молча и без следа.
    """
    _client, mainmod = service
    out = citokens.issue(mainmod.db, name="shop CI", project="shop")
    mainmod.db.execute(
        "UPDATE ci_token SET expires_at=datetime('now','+10 days') WHERE id=?",
        (out["id"],))
    assert citokens.resolve(mainmod.db, out["token"]) is not None, \
        "живой токен со сроком из SQL посчитан просроченным"


def test_an_unreadable_expiry_does_not_widen_access(service):
    """Повреждение базы не должно РАСШИРЯТЬ доступ.

    Считать нечитаемую дату «бессрочным» — именно это и означало бы.
    """
    _client, mainmod = service
    out = citokens.issue(mainmod.db, name="shop CI", project="shop", days=1)
    mainmod.db.execute("UPDATE ci_token SET expires_at='не дата' WHERE id=?",
                       (out["id"],))
    assert citokens.resolve(mainmod.db, out["token"]) is None


# --------------------------------------------------------------------------- #
#  Изоляция — то, ради чего всё затевалось
# --------------------------------------------------------------------------- #
def test_a_project_token_writes_runs_of_its_own_project(service):
    client, mainmod = service
    out = citokens.issue(mainmod.db, name="shop CI", project="shop")
    r = client.post("/api/runs", json=_run("shop"),
                    headers={"X-VisTest-Token": out["token"]})
    assert r.status_code == 200, r.text
    assert r.json()["project"] == "shop"


def test_a_project_token_cannot_write_runs_of_another_project(service):
    """Прежняя общая переменная давала доступ ко всему сразу."""
    client, mainmod = service
    out = citokens.issue(mainmod.db, name="shop CI", project="shop")
    r = client.post("/api/runs", json=_run("billing"),
                    headers={"X-VisTest-Token": out["token"]})
    assert r.status_code == 403
    assert "shop" in r.json()["detail"] and "billing" in r.json()["detail"]
    assert not mainmod.db.query("SELECT id FROM run"), "прогон всё же записался"


def test_an_installation_wide_token_still_writes_everywhere(service):
    """Путь совместимости: пустой проект — осознанный выбор администратора."""
    client, mainmod = service
    out = citokens.issue(mainmod.db, name="global CI", project="")
    for project in ("shop", "billing"):
        r = client.post("/api/runs", json=_run(project, key=f"ci-{project}"),
                        headers={"X-VisTest-Token": out["token"]})
        assert r.status_code == 200, r.text


def test_a_viewer_token_cannot_write_runs(service):
    """Роль у токена не декоративная: приём прогона — действие reviewer."""
    client, mainmod = service
    out = citokens.issue(mainmod.db, name="read only", project="shop",
                         role="viewer")
    r = client.post("/api/runs", json=_run("shop"),
                    headers={"X-VisTest-Token": out["token"]})
    assert r.status_code == 403


def test_artifacts_cannot_be_uploaded_into_another_projects_run(service):
    """Иначе изоляция кончается на первой же картинке."""
    client, mainmod = service
    run_id = mainmod.db.ingest_run(_run("billing"), "billing")
    out = citokens.issue(mainmod.db, name="shop CI", project="shop")
    r = client.post(f"/api/runs/{run_id}/artifacts",
                    data={"snapshot": "a.png", "kind": "actual"},
                    files={"file": ("a.png", b"\x89PNG\r\n\x1a\n", "image/png")},
                    headers={"X-VisTest-Token": out["token"]})
    assert r.status_code == 403


# --------------------------------------------------------------------------- #
#  Совместимость
# --------------------------------------------------------------------------- #
def test_the_old_environment_variable_keeps_working(service, monkeypatch):
    """Обновление, ломающее работающие пайплайны, — это не улучшение доступа."""
    client, mainmod = service
    monkeypatch.setenv("VISTEST_INGEST_TOKEN", "legacy-secret")
    # Пока в базе нет ни одного пользователя, сервис работает в локальном
    # режиме и пускает всех — это поведение старше токенов и трогать его тут
    # незачем. Проверяем отказ там, где он вообще существует.
    from vistest.api.auth import create_user
    create_user(mainmod.db, "anna", "password123", role="admin")

    ok = client.post("/api/runs", json=_run("shop"),
                     headers={"X-VisTest-Token": "legacy-secret"})
    assert ok.status_code == 200, ok.text

    client.cookies.clear()
    bad = client.post("/api/runs", json=_run("shop", key="ci-2"),
                      headers={"X-VisTest-Token": "wrong"})
    assert bad.status_code == 401


def test_a_project_token_works_alongside_the_old_variable(service, monkeypatch):
    """Переход не требует одномоментности: работают оба способа."""
    client, mainmod = service
    monkeypatch.setenv("VISTEST_INGEST_TOKEN", "legacy-secret")
    out = citokens.issue(mainmod.db, name="shop CI", project="shop")

    assert client.post("/api/runs", json=_run("shop"),
                       headers={"X-VisTest-Token": out["token"]}
                       ).status_code == 200
    assert client.post("/api/runs", json=_run("shop", key="ci-2"),
                       headers={"Authorization": "Bearer legacy-secret"}
                       ).status_code == 200


def test_the_bearer_spelling_works_for_project_tokens_too(service):
    client, mainmod = service
    out = citokens.issue(mainmod.db, name="shop CI", project="shop")
    r = client.post("/api/runs", json=_run("shop"),
                    headers={"Authorization": f"Bearer {out['token']}"})
    assert r.status_code == 200, r.text


# --------------------------------------------------------------------------- #
#  Журнал
# --------------------------------------------------------------------------- #
def test_the_audit_says_which_pipeline_wrote_the_run(service):
    """Раньше здесь стояло «ci-token» — то есть все токены сразу.

    По журналу нельзя было ответить, чей пайплайн залил прогон.
    """
    client, mainmod = service
    out = citokens.issue(mainmod.db, name="shop · GitLab", project="shop")
    client.post("/api/runs", json=_run("shop"),
                headers={"X-VisTest-Token": out["token"]})

    row = mainmod.db.one(
        "SELECT who FROM audit WHERE action='run.ingested' ORDER BY id DESC")
    assert "shop · GitLab" in row["who"] and out["prefix"] in row["who"]
    assert out["token"] not in row["who"], "секрет попал в журнал"


# --------------------------------------------------------------------------- #
#  Ротация
# --------------------------------------------------------------------------- #
def test_rotation_keeps_the_old_token_alive(service):
    """В этом и смысл: пока пайплайны перекатываются, старый обязан работать."""
    client, mainmod = service
    _admin(mainmod, client)
    old = citokens.issue(mainmod.db, name="shop CI", project="shop", days=30)

    new = client.post(f"/api/ci-tokens/{old['id']}/rotate").json()
    assert new["project"] == "shop" and new["role"] == old["role"]
    assert new["token"] != old["token"]

    for token in (old["token"], new["token"]):
        r = client.post("/api/runs", json=_run("shop", key=f"ci-{token[-4:]}"),
                        headers={"X-VisTest-Token": token})
        assert r.status_code == 200, r.text


def test_rotation_does_not_inherit_a_nearly_expired_date(service):
    """Сменщик получает тот же СРОК ЖИЗНИ, а не ту же дату.

    Ротацию делают тогда, когда токен подходит к концу. Унаследовать дату
    значило бы выдать сменщика, который истекает завтра, — и назавтра всё
    начинается заново.
    """
    _client, mainmod = service
    old = citokens.issue(mainmod.db, name="shop CI", project="shop", days=30)
    # Состарим оригинал: выпущен месяц назад, истекает завтра.
    mainmod.db.execute(
        "UPDATE ci_token SET created_at=datetime('now','-29 days'),"
        " expires_at=datetime('now','+1 day') WHERE id=?", (old["id"],))

    new = citokens.rotate(mainmod.db, old["id"])
    from datetime import datetime, timezone
    left = (datetime.fromisoformat(new["expires_at"])
            - datetime.now(timezone.utc)).days
    assert left >= 25, f"сменщику отмерено {left} дней вместо тридцати"


# --------------------------------------------------------------------------- #
#  Роуты
# --------------------------------------------------------------------------- #
def test_only_an_admin_manages_tokens(service):
    client, mainmod = service
    from vistest.api.auth import create_user
    create_user(mainmod.db, "petr", "password123", role="reviewer")
    client.post("/api/auth/login", json={"login": "petr",
                                         "password": "password123"})
    assert client.get("/api/ci-tokens").status_code == 403
    assert client.post("/api/ci-tokens", json={"project": "shop"}).status_code == 403


def test_the_listing_never_carries_the_secret(service):
    client, mainmod = service
    _admin(mainmod, client)
    out = client.post("/api/ci-tokens",
                      json={"name": "shop CI", "project": "shop"}).json()
    body = client.get("/api/ci-tokens").json()
    assert out["token"] not in str(body)
    assert body["tokens"][0]["prefix"] == out["prefix"]


def test_revoking_is_one_action(service):
    client, mainmod = service
    _admin(mainmod, client)
    out = client.post("/api/ci-tokens", json={"project": "shop"}).json()
    assert client.delete(f"/api/ci-tokens/{out['id']}").json()["revoked"] is True
    assert client.get("/api/ci-tokens").json()["tokens"][0]["status"] == "revoked"


def test_a_nonsense_lifetime_is_refused(service):
    client, mainmod = service
    _admin(mainmod, client)
    assert client.post("/api/ci-tokens",
                       json={"project": "shop", "days": 0}).status_code == 400
    assert client.post("/api/ci-tokens",
                       json={"project": "shop", "days": "неделя"}
                       ).status_code == 400
