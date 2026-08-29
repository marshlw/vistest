# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Что изменил именно этот код — и почему это не то же самое, что «что красное».

Перед мержем задают один вопрос: сломала ли что-нибудь ветка. Прогон отвечает
на другой: что красное сейчас. В ветке двадцать падений, восемнадцать из них
были красными и на мастере — они не про эту ветку. Человек разбирает все
двадцать, устаёт на пятом и принимает остальные не глядя; ровно те два, ради
которых всё затевалось, уезжают в мастер вместе с чужими.

Здесь проверяется разложение двух прогонов по изменению состояния, и особое
внимание — двум местам, где легко соврать убедительно:

* снимок, который **перестали проверять**, не появится ни в одном списке
  падений, и отсутствие красного читается как «починили»;
* два прогона против **разных наборов эталонов** сравнивать нельзя: там
  «сломалось» означает «поменялась точка отсчёта».
"""

from __future__ import annotations

import pytest

from vistest.api import rundiff
from vistest.api.db import Database


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "vistest.db")


def _run(db, key: str, *, project: str = "demo", platform: str = "linux",
         branch: str = "main", started: str = "2026-01-01 10:00:00",
         scope: str = "global", baseline_dir: str | None = None) -> dict:
    pid = db.project_id(project)
    db.execute(
        "INSERT INTO run(project_id, run_key, platform, branch, started_at,"
        " baseline_scope, baseline_dir) VALUES(?,?,?,?,?,?,?)",
        (pid, key, platform, branch, started, scope, baseline_dir))
    return db.one("SELECT * FROM run WHERE run_key=?", (key,))


def _snap(db, run: dict, name: str, verdict: str, *, severity: float = 0.0,
          review: str | None = None, platform: str = "linux",
          browser: str = "chromium") -> None:
    sid = db.snapshot_id(run["project_id"], name, platform, browser)
    db.execute(
        "INSERT INTO comparison(run_id, snapshot_id, verdict, review,"
        " max_severity, changed_area_pct, ssim) VALUES(?,?,?,?,?,?,?)",
        (run["id"], sid, verdict, review, severity, 1.0, 0.99))


def _diff(db, base: dict, head: dict) -> dict:
    return rundiff.diff(base, rundiff.comparisons_of(db, base["id"]),
                        head, rundiff.comparisons_of(db, head["id"]))


def _names(result: dict, group: str) -> list[str]:
    return [i["name"] for i in result["groups"][group]]


# --------------------------------------------------------------------------- #
#  Раскладка
# --------------------------------------------------------------------------- #
def test_only_what_this_branch_broke_lands_in_broke(db):
    """Главное разделение: своё падение против унаследованного.

    Оба снимка красные в ветке, и в списке прогона они неотличимы. Но `old`
    был красным и до неё — разбирать в этой ветке надо ровно `new`.
    """
    base = _run(db, "master")
    head = _run(db, "branch", branch="feature", started="2026-01-01 12:00:00")

    _snap(db, base, "old.png", "fail", severity=40)
    _snap(db, base, "new.png", "pass")
    _snap(db, head, "old.png", "fail", severity=41)
    _snap(db, head, "new.png", "fail", severity=60)

    got = _diff(db, base, head)
    assert _names(got, "broke") == ["new.png"]
    assert _names(got, "still_failing") == ["old.png"]
    assert got["counts"]["broke"] == 1


def test_a_snapshot_that_stopped_being_checked_is_not_silence(db):
    """Самая тихая поломка из всех.

    Удалённый тест, переименованный снимок, упавшая до съёмки фикстура — во
    всех трёх случаях снимок просто исчезает из прогона. Ни одно падение при
    этом не показывается, и «в ветке ничего не красное» становится правдой,
    которая ничего не значит.
    """
    base = _run(db, "master")
    head = _run(db, "branch", started="2026-01-01 12:00:00")
    _snap(db, base, "checkout.png", "pass")
    _snap(db, head, "cart.png", "pass")

    got = _diff(db, base, head)
    assert _names(got, "gone") == ["checkout.png"]
    assert _names(got, "added") == ["cart.png"]
    assert got["clean"] is False, "исчезнувший снимок — не чистое сравнение"


def test_an_error_counts_as_red_not_as_missing_data(db):
    """Ошибка съёмки — это ухудшение, а не отсутствие данных.

    Снимок проверялся и был зелёным, теперь не проверяется вообще. Сложить
    `error` в «ни то ни сё» значило бы спрятать сломанную фикстуру — самый
    частый способ незаметно перестать что-либо проверять.
    """
    base = _run(db, "master")
    head = _run(db, "branch", started="2026-01-01 12:00:00")
    _snap(db, base, "page.png", "pass")
    _snap(db, head, "page.png", "error")

    assert _names(_diff(db, base, head), "broke") == ["page.png"]


def test_a_fixed_snapshot_is_reported_too(db):
    base = _run(db, "master")
    head = _run(db, "branch", started="2026-01-01 12:00:00")
    _snap(db, base, "page.png", "fail", severity=50)
    _snap(db, head, "page.png", "pass")

    got = _diff(db, base, head)
    assert _names(got, "fixed") == ["page.png"]
    assert got["clean"] is True


def test_a_new_snapshot_that_is_red_is_not_called_broken(db):
    """До него ничего не было — ломаться было нечему.

    Назвать его «сломалось» значит потребовать от человека найти виноватый
    коммит там, где виноватого нет: снимок просто появился.
    """
    base = _run(db, "master")
    head = _run(db, "branch", started="2026-01-01 12:00:00")
    _snap(db, head, "fresh.png", "fail", severity=70)

    got = _diff(db, base, head)
    assert _names(got, "broke") == []
    assert _names(got, "added") == ["fresh.png"]


def test_matching_snapshots_are_only_counted(db):
    """Сто совпавших снимков в списке — это сто строк, которые не читают."""
    base = _run(db, "master")
    head = _run(db, "branch", started="2026-01-01 12:00:00")
    for i in range(5):
        _snap(db, base, f"p{i}.png", "pass")
        _snap(db, head, f"p{i}.png", "pass")

    got = _diff(db, base, head)
    assert got["counts"]["same"] == 5
    assert all(not got["groups"][g] for g in rundiff.GROUPS)


# --------------------------------------------------------------------------- #
#  Стало хуже
# --------------------------------------------------------------------------- #
def test_a_failure_that_got_worse_is_marked(db):
    """«Ветка не хуже сломанного мастера» и «ветка ничего не трогала» — разное.

    Красное в обоих прогонах разбирать в этой ветке не надо. Но если severity
    выросла вдвое, значит ветка добавила к чужой поломке свою, и промолчать
    об этом — то же самое, что спрятать её.
    """
    base = _run(db, "master")
    head = _run(db, "branch", started="2026-01-01 12:00:00")
    _snap(db, base, "worse.png", "fail", severity=30)
    _snap(db, base, "steady.png", "fail", severity=30)
    _snap(db, head, "worse.png", "fail", severity=62)
    _snap(db, head, "steady.png", "fail", severity=30.2)

    items = {i["name"]: i for i in _diff(db, base, head)["groups"]["still_failing"]}
    assert items["worse.png"]["worse"] is True
    assert items["worse.png"]["delta_severity"] == 32.0
    # Разница в третьем знаке — это шум стенда, а не «стало хуже».
    assert items["steady.png"]["worse"] is False


def test_the_worst_comes_first(db):
    """Список читают сверху и до первого отвлечения."""
    base = _run(db, "master")
    head = _run(db, "branch", started="2026-01-01 12:00:00")
    for name, sev in (("a.png", 12), ("b.png", 88), ("c.png", 45)):
        _snap(db, base, name, "pass")
        _snap(db, head, name, "fail", severity=sev)

    assert _names(_diff(db, base, head), "broke") == ["b.png", "c.png", "a.png"]


# --------------------------------------------------------------------------- #
#  Требует внимания
# --------------------------------------------------------------------------- #
def test_a_reviewed_break_stays_in_the_list_but_not_in_the_question(db):
    """Решение не отменяет факта поломки — оно отменяет вопрос.

    Если убирать разобранное из `broke`, история сравнения меняется задним
    числом: вчера ветка сломала три снимка, сегодня — один, хотя код тот же.
    Если не считать `attention` отдельно, кнопка «можно мержить» никогда не
    станет зелёной.
    """
    base = _run(db, "master")
    head = _run(db, "branch", started="2026-01-01 12:00:00")
    _snap(db, base, "a.png", "pass")
    _snap(db, base, "b.png", "pass")
    _snap(db, head, "a.png", "fail", severity=50, review="approved")
    _snap(db, head, "b.png", "fail", severity=50)

    got = _diff(db, base, head)
    assert got["counts"]["broke"] == 2
    assert got["counts"]["attention"] == 1


# --------------------------------------------------------------------------- #
#  Когда сравнивать нельзя
# --------------------------------------------------------------------------- #
def test_different_baseline_sets_are_named_out_loud(db):
    """«Сломалось» здесь может означать «поменялась точка отсчёта».

    Красивый список без предупреждения в этом случае — убедительное враньё:
    человек пойдёт искать коммит, которого нет.
    """
    base = _run(db, "master", scope="global")
    head = _run(db, "branch", started="2026-01-01 12:00:00", scope="vistest")
    _snap(db, base, "p.png", "pass")
    _snap(db, head, "p.png", "fail", severity=40)

    warn = " ".join(_diff(db, base, head)["warnings"])
    assert "different baseline sets" in warn


def test_the_same_set_in_a_different_directory_is_also_named(db):
    """Один и тот же набор на бумаге — не одни и те же файлы на диске."""
    base = _run(db, "master", baseline_dir="/srv/a")
    head = _run(db, "branch", started="2026-01-01 12:00:00", baseline_dir="/srv/b")
    _snap(db, base, "p.png", "pass")
    _snap(db, head, "p.png", "pass")

    warn = " ".join(_diff(db, base, head)["warnings"])
    assert "different baseline directories" in warn


def test_different_platforms_are_named(db):
    """Рендеринг отличается сам по себе, без единой правки в коде."""
    base = _run(db, "master", platform="linux")
    head = _run(db, "branch", platform="mac", started="2026-01-01 12:00:00")
    _snap(db, base, "p.png", "pass", platform="linux")
    _snap(db, head, "p.png", "fail", severity=40, platform="mac")

    warn = " ".join(_diff(db, base, head)["warnings"])
    assert "Different platforms" in warn


def test_a_backwards_comparison_says_so(db):
    """База новее сравниваемого — человек перепутал местами.

    Список при этом читается наоборот: «сломалось» означает «потом починили».
    Сам разбор всё равно делается: запретить было бы хуже, чем объяснить.
    """
    base = _run(db, "later", started="2026-01-02 10:00:00")
    head = _run(db, "earlier", started="2026-01-01 10:00:00")
    _snap(db, base, "p.png", "pass")
    _snap(db, head, "p.png", "fail", severity=40)

    got = _diff(db, base, head)
    assert any("backwards" in w for w in got["warnings"])
    assert _names(got, "broke") == ["p.png"]


def test_two_clean_runs_of_the_same_shape_warn_about_nothing(db):
    """Предупреждение, которое видно всегда, перестают читать."""
    base = _run(db, "master")
    head = _run(db, "branch", started="2026-01-01 12:00:00")
    _snap(db, base, "p.png", "pass")
    _snap(db, head, "p.png", "pass")

    assert _diff(db, base, head)["warnings"] == []


# --------------------------------------------------------------------------- #
#  Опознание снимка
# --------------------------------------------------------------------------- #
def test_the_same_name_on_two_browsers_is_two_snapshots(db):
    """Иначе один браузер молча затирает другой.

    `page.png` в Chromium и `page.png` в Firefox — разные картинки с разными
    эталонами, и сведи мы их в одну строку, падение одного браузера выглядело
    бы как падение обоих или не выглядело бы никак.
    """
    base = _run(db, "master")
    head = _run(db, "branch", started="2026-01-01 12:00:00")
    _snap(db, base, "page.png", "pass", browser="chromium")
    _snap(db, base, "page.png", "pass", browser="firefox")
    _snap(db, head, "page.png", "pass", browser="chromium")
    _snap(db, head, "page.png", "fail", severity=50, browser="firefox")

    got = _diff(db, base, head)
    assert got["counts"]["broke"] == 1
    assert got["counts"]["same"] == 1
    assert got["groups"]["broke"][0]["browser"] == "firefox"


def test_a_duplicate_inside_one_run_is_resolved_and_reported(db):
    """Молча выбрать одно из двух — значит потерять снимок из списка.

    Человек своими глазами видел его красным в прогоне, а в «поехало» его
    нет, и объяснить это нечем.
    """
    base = _run(db, "master")
    head = _run(db, "branch", started="2026-01-01 12:00:00")
    _snap(db, base, "p.png", "pass")
    _snap(db, head, "p.png", "pass")
    # Второе сравнение того же снимка в том же прогоне — записано позже.
    _snap(db, head, "p.png", "fail", severity=40)

    got = _diff(db, base, head)
    assert _names(got, "broke") == ["p.png"]
    assert any("more than once" in w for w in got["warnings"])


def test_snapshots_are_matched_by_name_across_projects(db):
    """Сведение по `snapshot_id` развалило бы такое сравнение целиком.

    Идентификатор привязан к проекту, и прогон нашего набора против прогона
    подключённого выглядел бы как «всё исчезло, всё появилось».
    """
    base = _run(db, "ours", project="demo")
    head = _run(db, "theirs", project="shop", started="2026-01-01 12:00:00")
    _snap(db, base, "page.png", "pass")
    _snap(db, head, "page.png", "fail", severity=40)

    got = _diff(db, base, head)
    assert _names(got, "broke") == ["page.png"]
    assert any("different projects" in w for w in got["warnings"])


# --------------------------------------------------------------------------- #
#  База по умолчанию
# --------------------------------------------------------------------------- #
def test_the_default_base_is_the_previous_run(db):
    _run(db, "r1", started="2026-01-01 10:00:00")
    prev = _run(db, "r2", started="2026-01-01 11:00:00")
    head = _run(db, "r3", started="2026-01-01 12:00:00")

    assert rundiff.previous_run(db, head)["id"] == prev["id"]


def test_the_default_base_stays_on_the_same_platform(db):
    """Прогон под другой платформой отличается рендерингом, а не кодом.

    Подставить его молча значило бы выдать список «сломалось», который весь
    состоит из другого шрифта.
    """
    ours = _run(db, "linux-prev", platform="linux", started="2026-01-01 11:00:00")
    _run(db, "mac", platform="mac", started="2026-01-01 11:30:00")
    head = _run(db, "linux-head", platform="linux", started="2026-01-01 12:00:00")

    assert rundiff.previous_run(db, head)["id"] == ours["id"]


def test_the_first_run_has_nothing_to_compare_with(db):
    """Это нормальный ответ, а не ошибка."""
    head = _run(db, "only", started="2026-01-01 12:00:00")
    assert rundiff.previous_run(db, head) is None


def test_another_project_is_never_picked_by_default(db):
    _run(db, "other", project="shop", started="2026-01-01 11:00:00")
    head = _run(db, "ours", project="demo", started="2026-01-01 12:00:00")
    assert rundiff.previous_run(db, head) is None


# --------------------------------------------------------------------------- #
#  Маршрут
# --------------------------------------------------------------------------- #
@pytest.fixture
def service(tmp_path, monkeypatch):
    import importlib
    import threading

    from fastapi import APIRouter
    from fastapi.testclient import TestClient

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VISTEST_ROOT", str(tmp_path / ".vistest"))
    monkeypatch.delenv("VISTEST_AUTH", raising=False)

    import vistest.api.db as dbmod
    dbmod._local = threading.local()

    import vistest.api.auth as authmod
    authmod.router = APIRouter()

    import vistest.api.main as mainmod
    importlib.reload(mainmod)
    return TestClient(mainmod.app), mainmod.db


def test_the_route_falls_back_to_the_previous_run(service):
    client, sdb = service
    base = _run(sdb, "r1", started="2026-01-01 10:00:00")
    head = _run(sdb, "r2", started="2026-01-01 12:00:00")
    _snap(sdb, base, "p.png", "pass")
    _snap(sdb, head, "p.png", "fail", severity=40)

    r = client.get(f"/api/runs/{head['id']}/diff")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["base"]["id"] == base["id"]
    assert [i["name"] for i in body["groups"]["broke"]] == ["p.png"]


def test_the_route_takes_an_explicit_base(service):
    client, sdb = service
    master = _run(sdb, "master", started="2026-01-01 09:00:00")
    _run(sdb, "noise", started="2026-01-01 11:00:00")
    head = _run(sdb, "branch", started="2026-01-01 12:00:00")
    _snap(sdb, master, "p.png", "pass")
    _snap(sdb, head, "p.png", "fail", severity=40)

    body = client.get(f"/api/runs/{head['id']}/diff",
                      params={"base": master["id"]}).json()
    assert body["base"]["run_key"] == "master"


def test_nothing_to_compare_with_is_not_the_same_as_a_missing_run(service):
    """Два разных 404, и лечатся они разным.

    «Прогона нет» чинится другой ссылкой, «сравнивать не с чем» не чинится
    ничем — и человек должен понимать, в каком он случае.
    """
    client, sdb = service
    only = _run(sdb, "only", started="2026-01-01 12:00:00")

    r = client.get(f"/api/runs/{only['id']}/diff")
    assert r.status_code == 404
    assert "no earlier run" in r.json()["detail"]

    assert client.get("/api/runs/9999/diff").status_code == 404
    assert client.get(f"/api/runs/{only['id']}/diff",
                      params={"base": 9999}).status_code == 404


def test_a_run_compared_with_itself_is_refused(service):
    """Пустой ответ выглядел бы как «ничего не изменилось» — и это правда,
    которая ничего не значит: человек сравнил прогон сам с собой и не заметил.
    """
    client, sdb = service
    one = _run(sdb, "one", started="2026-01-01 12:00:00")
    r = client.get(f"/api/runs/{one['id']}/diff", params={"base": one["id"]})
    assert r.status_code == 400
