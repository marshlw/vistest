# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Что сервис отдаёт наружу и кого пускает.

Три дыры, закрытые здесь, объединяет одно: они были не «недоработкой», а
прямым следствием того, что маршрут проверял путь, но не проверял, кто
спрашивает.

* `GET /local/{path}` отдавал ЛЮБОЙ файл под `.vistest` без авторизации.
  Внутри лежат `vistest.db` — с хешами паролей и таблицей сессий в открытом
  виде — и `secrets.env` со всеми секретами проектов. Один GET, и учётная
  запись администратора чужая.
* `/api/jobs*` были открыты целиком: логи чужого pytest читал кто угодно, и
  отменить чужой прогон мог кто угодно.
* Приём прогонов (`POST /api/runs`) не имел ни токена, ни лимита размера.
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
    monkeypatch.delenv("VISTEST_INGEST_TOKEN", raising=False)

    import vistest.api.db as dbmod
    dbmod._local = threading.local()

    import importlib

    from fastapi import APIRouter

    # `auth.build_router` вешает маршруты на МОДУЛЬНЫЙ router и замыкает в них
    # конкретную базу. Без сброса перезагрузка main.py добавляла второй
    # комплект маршрутов, а срабатывал первый — с базой предыдущего теста.
    import vistest.api.auth as authmod
    authmod.router = APIRouter()

    import vistest.api.main as mainmod
    importlib.reload(mainmod)

    root = tmp_path / ".vistest"
    root.mkdir(parents=True, exist_ok=True)
    # Имя нарочно не пересекается с переменными других тестов:
    # `VisTestConfig.load()` подмешивает secrets.env в os.environ процесса.
    (root / "secrets.env").write_text(
        "VISTEST_DEMO_SECRET=hunter2\n", encoding="utf-8")
    (root / "runs").mkdir(exist_ok=True)
    (root / "runs" / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    (root / "projects").mkdir(exist_ok=True)
    (root / "projects" / "leak.json").write_text("{}", encoding="utf-8")

    return TestClient(mainmod.app), mainmod, root


def _make_admin(mainmod, login="anna", password="password123"):
    from vistest.api.auth import create_user
    create_user(mainmod.db, login, password, role="admin")
    return login, password


def _make_viewer(mainmod, login="vic", password="password123"):
    from vistest.api.auth import create_user
    create_user(mainmod.db, login, password, role="viewer")
    return login, password


# --------------------------------------------------------------------------- #
#  Файловые маршруты
# --------------------------------------------------------------------------- #
def test_database_is_never_served(service):
    """Самая тяжёлая из находок: база отдавалась целиком по одному GET."""
    client, mainmod, root = service
    _make_admin(mainmod)
    assert (root / "vistest.db").exists()

    r = client.get("/local/vistest.db")
    assert r.status_code in (401, 403), r.text
    assert b"pbkdf2" not in r.content


def test_secrets_file_is_never_served(service):
    client, mainmod, root = service
    _make_admin(mainmod)
    r = client.get("/local/secrets.env")
    assert r.status_code in (401, 403)
    assert b"hunter2" not in r.content


def test_uploaded_project_sources_are_not_served(service):
    client, mainmod, _ = service
    _make_admin(mainmod)
    r = client.get("/local/projects/leak.json")
    assert r.status_code in (401, 403)


def test_file_routes_require_a_session(service):
    client, mainmod, _ = service
    _make_admin(mainmod)
    assert client.get("/local/runs/shot.png").status_code == 401


def test_a_signed_in_viewer_still_gets_pictures(service):
    """Закрутить гайку так, чтобы перестал работать просмотр диффов, нельзя."""
    client, mainmod, _ = service
    login, password = _make_viewer(mainmod)
    client.post("/api/auth/login", json={"login": login, "password": password})

    r = client.get("/local/runs/shot.png")
    assert r.status_code == 200
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-disposition"].startswith("inline")


def test_html_from_a_project_is_never_rendered_inline(service):
    """Иначе загруженный .html — это stored XSS поверх сессионной куки."""
    client, mainmod, root = service
    login, password = _make_viewer(mainmod)
    client.post("/api/auth/login", json={"login": login, "password": password})
    (root / "runs" / "evil.html").write_text("<script>alert(1)</script>",
                                             encoding="utf-8")

    r = client.get("/local/runs/evil.html")
    assert r.status_code == 403


def test_path_traversal_is_still_refused(service):
    client, mainmod, _ = service
    login, password = _make_viewer(mainmod)
    client.post("/api/auth/login", json={"login": login, "password": password})
    assert client.get("/local/../../etc/passwd").status_code in (403, 404)


def test_local_mode_without_users_keeps_working(service):
    """Одиночная работа на своей машине не должна сломаться от обновления."""
    client, _, _ = service          # пользователей нет вообще
    assert client.get("/local/runs/shot.png").status_code == 200


# --------------------------------------------------------------------------- #
#  Задачи
# --------------------------------------------------------------------------- #
def test_job_list_requires_a_session(service):
    client, mainmod, _ = service
    _make_admin(mainmod)
    assert client.get("/api/jobs").status_code == 401


def test_job_log_requires_a_session(service):
    """В логе задачи — вывод чужого pytest, пути и диагностика окружения."""
    client, mainmod, _ = service
    _make_admin(mainmod)
    assert client.get("/api/jobs/whatever").status_code == 401


def test_cancelling_someone_elses_job_is_refused(service):
    client, mainmod, _ = service
    _make_admin(mainmod, "anna")
    _make_viewer(mainmod, "vic")
    from vistest.api.auth import set_role
    set_role(mainmod.db, "vic", "reviewer")

    from vistest.api.jobs import runner
    job = runner.submit("test", "long one", lambda j: __import__("time").sleep(5),
                        owner="anna")

    client.post("/api/auth/login", json={"login": "vic", "password": "password123"})
    r = client.post(f"/api/jobs/{job.id}/cancel")
    assert r.status_code == 403
    assert "anna" in r.json()["detail"]
    job._cancel.set()


# --------------------------------------------------------------------------- #
#  Приём прогонов
# --------------------------------------------------------------------------- #
def _run_payload():
    return {"run_id": "ci-1", "project": "demo",
            "totals": {"total": 1, "passed": 1},
            "comparisons": []}


def test_intake_works_without_a_token_while_there_are_no_users(service):
    """Одиночная работа на своей машине не ломается от закручивания гаек."""
    client, _, _ = service
    assert client.post("/api/runs", json=_run_payload()).status_code == 200


def test_intake_is_refused_to_a_stranger_once_users_exist(service):
    """«Не настроил токен» больше не значит «открыто».

    Раньше отсутствие VISTEST_INGEST_TOKEN означало приём чего угодно от кого
    угодно: любой в периметре писал прогоны в чужую историю и забивал диск.
    Теперь без токена работает обычная проверка сессии — на машине инженера
    ничего не изменилось, а на общем сервере аноним получает 401.
    """
    client, mainmod, _ = service
    _make_admin(mainmod)
    assert client.post("/api/runs", json=_run_payload()).status_code == 401


def test_intake_can_be_reopened_deliberately(service, monkeypatch):
    client, mainmod, _ = service
    _make_admin(mainmod)
    monkeypatch.setenv("VISTEST_INGEST_OPEN", "1")
    assert client.post("/api/runs", json=_run_payload()).status_code == 200


def test_intake_requires_the_token_once_configured(service, monkeypatch):
    client, mainmod, _ = service
    _make_admin(mainmod)
    monkeypatch.setenv("VISTEST_INGEST_TOKEN", "s3cret")

    assert client.post("/api/runs", json=_run_payload()).status_code == 401
    ok = client.post("/api/runs", json=_run_payload(),
                     headers={"X-VisTest-Token": "s3cret"})
    assert ok.status_code == 200


def test_a_wrong_token_is_refused(service, monkeypatch):
    client, mainmod, _ = service
    _make_admin(mainmod)
    monkeypatch.setenv("VISTEST_INGEST_TOKEN", "s3cret")
    r = client.post("/api/runs", json=_run_payload(),
                    headers={"X-VisTest-Token": "wrong"})
    assert r.status_code == 401


def test_bearer_spelling_also_works(service, monkeypatch):
    client, mainmod, _ = service
    _make_admin(mainmod)
    monkeypatch.setenv("VISTEST_INGEST_TOKEN", "s3cret")
    r = client.post("/api/runs", json=_run_payload(),
                    headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 200


def test_health_says_whether_intake_is_protected(service, monkeypatch):
    """Инсталляция не должна молча считать себя защищённой — и наоборот.

    Дефолт перевернулся: приём закрыт, пока его не открыли явно, — и `/api/health`
    обязан говорить именно то, что есть, а не то, что было в прошлой версии.
    """
    client, _, _ = service
    assert client.get("/api/health").json()["ingest_protected"] is True

    monkeypatch.setenv("VISTEST_INGEST_OPEN", "1")
    assert client.get("/api/health").json()["ingest_protected"] is False


def test_artifact_upload_refuses_unknown_types(service):
    client, mainmod, _ = service
    run_id = client.post("/api/runs", json=_run_payload()).json()["id"]
    r = client.post(f"/api/runs/{run_id}/artifacts",
                    data={"snapshot": "a", "kind": "actual"},
                    files={"file": ("payload.html", b"<script>", "text/html")})
    assert r.status_code == 400


def test_artifact_upload_refuses_a_missing_run(service):
    client, _, _ = service
    r = client.post("/api/runs/999/artifacts",
                    data={"snapshot": "a", "kind": "actual"},
                    files={"file": ("a.png", b"x", "image/png")})
    assert r.status_code == 404


def test_artifact_size_is_capped(service, monkeypatch):
    client, mainmod, _ = service
    monkeypatch.setattr(mainmod, "MAX_ARTIFACT_BYTES", 1024)
    run_id = client.post("/api/runs", json=_run_payload()).json()["id"]
    r = client.post(f"/api/runs/{run_id}/artifacts",
                    data={"snapshot": "a", "kind": "actual"},
                    files={"file": ("a.png", b"x" * 5000, "image/png")})
    assert r.status_code == 413


# --------------------------------------------------------------------------- #
#  Вход
# --------------------------------------------------------------------------- #
def test_repeated_failures_lock_the_login_out(service):
    """PBKDF2 на 480k итераций — это ещё и вектор на отказ в обслуживании."""
    client, mainmod, _ = service
    login, password = _make_admin(mainmod)

    for _ in range(8):
        client.post("/api/auth/login", json={"login": login, "password": "nope"})

    r = client.post("/api/auth/login", json={"login": login, "password": "nope"})
    assert r.status_code == 429
    assert "Retry-After" in r.headers
    # И даже верный пароль пока не пройдёт — счётчик считает попытки, не пароли.
    assert client.post("/api/auth/login",
                       json={"login": login, "password": password}).status_code == 429


def test_a_successful_sign_in_resets_the_counter(service):
    client, mainmod, _ = service
    login, password = _make_admin(mainmod)

    for _ in range(3):
        client.post("/api/auth/login", json={"login": login, "password": "nope"})
    assert client.post("/api/auth/login",
                       json={"login": login, "password": password}).status_code == 200

    from vistest.api.auth import _recent_failures
    assert _recent_failures(mainmod.db, who=login) == 0


def test_project_approve_refuses_a_path_outside_our_directories(service):
    """`actual` приходит по HTTP и не может быть просто «путём к файлу»."""
    client, mainmod, _ = service
    from vistest.config import VisTestConfig
    from vistest.projects import BaselineDir, Project, ProjectRegistry

    cfg = VisTestConfig.load()
    repo = cfg.root_path.parent / "their-repo"
    (repo / "snapshots").mkdir(parents=True, exist_ok=True)
    ProjectRegistry(cfg).save(Project(
        key="p1", name="P1", root=str(repo), tests="snapshots",
        baselines=[BaselineDir(dir="snapshots")]))

    outsider = cfg.root_path.parent / "outside.png"
    outsider.write_bytes(b"\x89PNG\r\n\x1a\n")

    r = client.post("/api/projects/p1/approve",
                    json={"name": "login.png", "actual": str(outsider)})
    assert r.status_code == 403
    assert "outside" in r.json()["detail"]


# --------------------------------------------------------------------------- #
#  Кто вообще может читать
#
#  Роль `viewer` существовала и закрывала картинки, а всё, что просто читает —
#  список прогонов, сравнение с регионами, метрики, размер хранилища, отчёт для
#  тикета, — отвечало любому, кто открыл порт. Причём `report.html` встраивает
#  картинки внутрь HTML: один анонимный GET отдавал ровно то, ради чего
#  закрывали `/files`.
# --------------------------------------------------------------------------- #
def _login(client, login="anna", password="password123"):
    r = client.post("/api/auth/login", json={"login": login, "password": password})
    assert r.status_code == 200, r.text
    return r


def _remote(mainmod, host="10.0.0.7"):
    """Тот же сервис, но запрос пришёл не с петли."""
    return TestClient(mainmod.app, client=(host, 51234))


READ_ROUTES = [
    "/api/runs",
    "/api/run-projects",
    "/api/storage",
    "/api/metrics/summary",
    "/api/metrics/flaky",
    "/api/metrics/evidence",
    "/api/runs/1/report.html",
    "/api/runs/1/clusters",
    "/api/comparisons/1",
    "/api/snapshots/1/history",
    "/metrics",
]


@pytest.mark.parametrize("path", READ_ROUTES)
def test_reading_routes_require_a_session(service, path):
    client, mainmod, _ = service
    _make_admin(mainmod)
    assert client.get(path).status_code == 401, path


def test_a_viewer_reads_what_a_viewer_should(service):
    """Закрутить гайку так, чтобы перестал работать просмотр, нельзя."""
    client, mainmod, _ = service
    _make_viewer(mainmod)
    _login(client, "vic")
    for path in ("/api/runs", "/api/run-projects", "/api/metrics/summary",
                 "/api/storage", "/metrics"):
        assert client.get(path).status_code == 200, path


def test_health_is_public_but_says_almost_nothing(service):
    """Оператор должен уметь спросить «жив ли», ещё не имея учётной записи.

    Но путь к данным, каталог модуля и полная таблица маршрутов — подсказки
    тому, кто внутрь только собирается.
    """
    client, mainmod, _ = service
    _make_admin(mainmod)

    anon = client.get("/api/health")
    assert anon.status_code == 200
    assert anon.json()["ok"] is True
    for leak in ("root", "module", "routes"):
        assert leak not in anon.json(), leak

    _login(client)
    inside = client.get("/api/health").json()
    assert "routes" in inside and "root" in inside


def test_metrics_accept_a_scraper_token(service, monkeypatch):
    """У Prometheus нет куки — значит, ему нужен собственный ключ."""
    client, mainmod, _ = service
    _make_admin(mainmod)
    monkeypatch.setenv("VISTEST_METRICS_TOKEN", "scrape-me")

    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"X-VisTest-Token": "nope"}
                      ).status_code == 401
    assert client.get("/metrics", headers={"X-VisTest-Token": "scrape-me"}
                      ).status_code == 200


def test_the_sign_in_path_stays_reachable(service):
    client, mainmod, _ = service
    _make_admin(mainmod)
    assert client.get("/api/auth/me").status_code == 401     # честный ответ, не 503
    _login(client)


# --------------------------------------------------------------------------- #
#  Инсталляция без администратора
# --------------------------------------------------------------------------- #
def test_an_installation_without_users_refuses_remote_callers(service):
    """Пока пользователей нет, `current_user` раздаёт роль admin.

    На машине инженера это правильно и так задумано. В контейнере, который
    стартует с `--host 0.0.0.0`, ровно то же самое означает «администратор —
    любой, кто дотянулся до порта», и единственное место, где об этом сказано,
    — поле в `/api/health`.
    """
    client, mainmod, _ = service
    assert client.get("/api/runs").status_code == 200        # петля — как было

    remote = _remote(mainmod)
    r = remote.get("/api/runs")
    assert r.status_code == 503
    assert "administrator" in r.json()["detail"]


def test_an_open_installation_refuses_remote_intake_too(service):
    client, mainmod, _ = service
    assert _remote(mainmod).post("/api/runs", json=_run_payload()
                                 ).status_code == 503


def test_it_can_be_left_open_deliberately(service, monkeypatch):
    client, mainmod, _ = service
    monkeypatch.setenv("VISTEST_ALLOW_OPEN", "1")
    assert _remote(mainmod).get("/api/runs").status_code == 200


def test_an_installation_with_an_admin_serves_remote_callers(service):
    client, mainmod, _ = service
    _make_admin(mainmod)
    assert _remote(mainmod).get("/api/runs").status_code == 401   # спросили, кто ты


# --------------------------------------------------------------------------- #
#  Ключ платформы — это имя каталога
# --------------------------------------------------------------------------- #
def test_platform_key_cannot_walk_out_of_the_baselines_directory(service):
    """Имя снимка санировали с первого дня, платформу — никогда.

    Она подставлялась в путь как есть, поэтому `?platform=../../..` читала
    `baseline.png` и писала `meta.json` за пределами каталога эталонов.
    """
    client, mainmod, _ = service
    for platform in ("../../..", "..", "a/b", "\\..\\..", ""):
        r = client.get("/api/baselines/detail", params={"platform": platform})
        # detail фильтрует по имени каталога и просто ничего не найдёт,
        # а вот адресные роуты обязаны отказать явно.
        r = client.get("/api/baselines/image",
                       params={"platform": platform, "name": "login.png"})
        assert r.status_code == 400, (platform, r.status_code)
        assert "platform" in r.json()["detail"].lower()


def test_a_normal_platform_key_still_works(service):
    client, mainmod, _ = service
    r = client.get("/api/baselines/image",
                   params={"platform": "linux-chromium-1x", "name": "nope.png"})
    assert r.status_code == 404          # каталог валиден, эталона просто нет


# --------------------------------------------------------------------------- #
#  Потолки и санитайзеры
# --------------------------------------------------------------------------- #
def test_run_list_limit_has_a_ceiling(service):
    client, _, _ = service
    assert client.get("/api/runs", params={"limit": 10_000_000}).status_code == 422


def test_artifact_name_cannot_climb_a_directory(service):
    """`_safe` разрешала точку — значит, разрешала и `..` целиком."""
    client, mainmod, root = service
    assert mainmod._safe("..") == "_"
    assert mainmod._safe(".") == "_"
    assert mainmod._safe("...") == "_"
    assert mainmod._safe("shop.example") == "shop.example"


# --------------------------------------------------------------------------- #
#  Журнал входов
# --------------------------------------------------------------------------- #
def test_a_successful_sign_in_does_not_erase_the_audit(service):
    """Сброс счётчика был DELETE по журналу.

    То есть удачный подбор пароля заметал собственные следы, и аудит оказывался
    бесполезен ровно в том случае, ради которого он существует.
    """
    client, mainmod, _ = service
    login, password = _make_admin(mainmod)

    for _ in range(3):
        client.post("/api/auth/login", json={"login": login, "password": "nope"})
    _login(client, login, password)

    rows = mainmod.db.query(
        "SELECT COUNT(*) AS n FROM audit WHERE action='login.failed' AND who=?",
        (login,))
    assert rows[0]["n"] == 3, "записи о неудачных попытках должны остаться"

    from vistest.api.auth import _recent_failures
    assert _recent_failures(mainmod.db, who=login) == 0, "но счётчик обнулён"


def test_the_cookie_is_secure_behind_a_tls_terminating_proxy(service):
    """За nginx схема запроса — http, и кука никогда не получала Secure."""
    client, mainmod, _ = service
    login, password = _make_admin(mainmod)

    plain = client.post("/api/auth/login",
                        json={"login": login, "password": password})
    assert "secure" not in plain.headers["set-cookie"].lower()

    fwd = client.post("/api/auth/login",
                      json={"login": login, "password": password},
                      headers={"X-Forwarded-Proto": "https"})
    assert "secure" in fwd.headers["set-cookie"].lower()
