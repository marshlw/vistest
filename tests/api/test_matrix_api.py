# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Матрица со стороны сервиса: прогон, база, подписи.

Самое важное здесь — второй тест. Прогон по матрице кладёт в базу шесть кадров
под ОДНИМ именем снимка, и различает их только ключ платформы. Пока ключ брался
с уровня прогона, все шесть складывались в одну строку `snapshot`: шесть
браузеров и размеров считались одним снимком, история каждого затирала
предыдущую, а «принять как эталон» уезжало в набор соседнего варианта.
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
    return TestClient(mainmod.app), mainmod


def _matrix_run(mainmod, run_id="ui-matrix-1"):
    """Прогон одного снимка по трём вариантам — как его пишет `_run_suite`."""
    def one(platform, browser, viewport, severity):
        return {"name": "shop/checkout.png", "verdict":
                "fail" if severity else "pass",
                "platform": platform, "browser": browser, "viewport": viewport,
                "metrics": {"max_severity": severity, "ssim_global": 0.99,
                            "changed_area_pct": 0.2}}

    payload = {
        "run_id": run_id,
        "platform": "linux-chromium-1x", "browser": "chromium",
        "created_at": "2026-01-01T00:00:00",
        "git": {"branch": "main", "sha": "abc1234"},
        "totals": {"total": 3, "passed": 1, "failed": 2},
        "variants": [
            {"browser": "chromium", "viewport": "1440x900",
             "platform": "linux-chromium-1x", "label": "chromium · 1440×900"},
            {"browser": "chromium", "viewport": "390x844",
             "platform": "linux-chromium-1x-390x844",
             "label": "chromium · 390×844"},
            {"browser": "firefox", "viewport": "1440x900",
             "platform": "linux-firefox-1x", "label": "firefox · 1440×900"},
        ],
        "comparisons": [
            one("linux-chromium-1x", "chromium", "1440x900", 0.0),
            one("linux-chromium-1x-390x844", "chromium", "390x844", 61.0),
            one("linux-firefox-1x", "firefox", "1440x900", 44.0),
        ],
    }
    return mainmod.db.ingest_run(payload, "demo")


# --------------------------------------------------------------------------- #
def test_the_matrix_expands_before_anyone_runs_it(service, tmp_path):
    """Шесть строк в конфиге — это восемнадцать прогонов и восемнадцать наборов.

    Узнать это заранее дешевле, чем из времени ожидания.
    """
    client, _ = service
    (tmp_path / "vistest.yaml").write_text(
        "matrix:\n"
        "  browsers: [chromium, firefox]\n"
        '  viewports: ["1440x900", "390x844"]\n', encoding="utf-8")

    r = client.get("/api/matrix")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["declared"] is True
    assert len(body["variants"]) == 4
    # Базовый вариант хранится под прежним ключом — без него обновление
    # обесценило бы весь уже снятый набор.
    base = [v for v in body["variants"] if v["base"]]
    assert base and all("-1440x900" not in v["platform"] for v in base)


def test_variants_of_one_snapshot_are_separate_rows_in_the_database(service):
    """Шесть кадров под одним именем — это шесть снимков, а не один.

    Иначе история каждого варианта затирает предыдущую, а «принять как эталон»
    уезжает в набор соседнего варианта — то есть меняет то, с чем сравнивают
    совсем другие кадры.
    """
    client, mainmod = service
    run_id = _matrix_run(mainmod)

    rows = mainmod.db.query(
        "SELECT platform, browser FROM snapshot WHERE name='shop/checkout.png'")
    assert len(rows) == 3, f"варианты слиплись в одну строку: {rows}"
    assert {r["platform"] for r in rows} == {
        "linux-chromium-1x", "linux-chromium-1x-390x844", "linux-firefox-1x"}

    comps = mainmod.db.query(
        "SELECT id FROM comparison WHERE run_id=?", (run_id,))
    assert len(comps) == 3


def test_a_plain_run_still_takes_the_platform_from_the_run(service):
    """Обычный прогон одного варианта не должен был ничего заметить."""
    client, mainmod = service
    mainmod.db.ingest_run({
        "run_id": "ci-plain", "platform": "linux-chromium-1x",
        "browser": "chromium", "created_at": "2026-01-01T00:00:00",
        "git": {}, "totals": {"total": 1},
        "comparisons": [{"name": "a.png", "verdict": "pass", "metrics": {}}],
    }, "demo")
    row = mainmod.db.one("SELECT platform, browser FROM snapshot WHERE name='a.png'")
    assert row["platform"] == "linux-chromium-1x" and row["browser"] == "chromium"


def test_the_run_page_labels_each_variant(service):
    """Ключ платформы — адрес каталога; человеку нужен браузер и размер."""
    client, mainmod = service
    run_id = _matrix_run(mainmod)

    body = client.get(f"/api/runs/{run_id}").json()
    assert sorted(body["variants"]) == [
        "chromium · 1440×900", "chromium · 390×844", "firefox · 1440×900"]
    labels = {c["variant"] for c in body["comparisons"]}
    assert labels == set(body["variants"])


def test_the_run_list_says_how_many_variants_there_were(service):
    """Иначе «2 to decide» читается как две сломанные страницы.

    А это одна страница, сломанная на двух вариантах, — и чинить её один раз.
    """
    client, mainmod = service
    _matrix_run(mainmod)
    runs = client.get("/api/runs?project=demo").json()
    assert runs and runs[0]["variants"] == 3


def test_one_comparison_carries_its_variant(service):
    client, mainmod = service
    _matrix_run(mainmod)
    comp = mainmod.db.one(
        "SELECT c.id FROM comparison c JOIN snapshot s ON s.id=c.snapshot_id"
        " WHERE s.platform='linux-chromium-1x-390x844'")
    body = client.get(f"/api/comparisons/{comp['id']}").json()
    assert body["variant"] == "chromium · 390×844"
    assert body["viewport"] == "390x844"


# --------------------------------------------------------------------------- #
#  Разбор тела запроса
# --------------------------------------------------------------------------- #
def test_a_broken_matrix_in_the_body_is_a_400_not_a_500(service):
    """Человек опечатался в размере окна, а не сломал сервис."""
    client, mainmod = service
    from vistest.api.auth import create_user
    create_user(mainmod.db, "anna", "password123", role="admin")
    client.post("/api/auth/login", json={"login": "anna", "password": "password123"})

    r = client.post("/api/suite/run",
                    json={"matrix": {"viewports": ["1440*900"]}})
    assert r.status_code == 400
    assert "1440*900" in r.text


def test_an_unknown_browser_in_the_body_is_refused_by_name(service):
    client, mainmod = service
    from vistest.api.auth import create_user
    create_user(mainmod.db, "anna", "password123", role="admin")
    client.post("/api/auth/login", json={"login": "anna", "password": "password123"})

    r = client.post("/api/suite/run", json={"matrix": {"browsers": ["safari"]}})
    assert r.status_code == 400 and "safari" in r.text
