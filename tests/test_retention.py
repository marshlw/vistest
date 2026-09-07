# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Ретеншен — единственное фоновое действие, которое удаляет данные.

Поэтому тестов на то, что оно **не** удаляет, здесь больше, чем на то, что
удаляет. Автоматика, молча уносящая чужую работу, ошибается ровно один раз, и
исправить эту ошибку нечем: истории уже нет.

Главное правило, ради которого написан весь модуль: неразобранное падение не
удаляется никогда — ни сроком, ни квотой, ни кнопкой. Это вопрос, на который
никто не ответил, и удалить его значит сделать так, что уже и не ответит.
"""

from __future__ import annotations

import pytest

from vistest.api import retention
from vistest.api.db import Database


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "vistest.db")


@pytest.fixture
def dropped():
    """Что удалили бы файлы. Настоящий диск здесь ни при чём."""
    seen: list[tuple[int, str]] = []

    def drop(run_id, run_key):
        seen.append((run_id, run_key))
        return 1024 * 1024                    # мегабайт на прогон
    drop.seen = seen
    return drop


def _run(db, key, *, age_days=100.0, verdict="pass", review="approved",
         project="demo"):
    run_id = db.ingest_run({
        "run_id": key, "platform": "linux-chromium-1x", "browser": "chromium",
        "created_at": "2026-01-01T00:00:00",
        "git": {"branch": "main", "sha": "abc1234"},
        "totals": {"total": 1, "failed": 1 if verdict == "fail" else 0},
        "comparisons": [{"name": "login.png", "verdict": verdict,
                         "metrics": {"max_severity": 44.0}}],
    }, project)
    db.execute("UPDATE run SET started_at=datetime('now', ?) WHERE id=?",
               (f"-{age_days} days", run_id))
    if verdict == "fail" and review:
        db.execute("UPDATE comparison SET review=? WHERE run_id=?",
                   (review, run_id))
    return run_id


def _ids(db):
    return {r["id"] for r in db.query("SELECT id FROM run")}


# --------------------------------------------------------------------------- #
#  Чего нельзя трогать
# --------------------------------------------------------------------------- #
def test_an_unreviewed_failure_is_never_deleted(db, dropped):
    """Главное правило модуля, и единственное без переключателя.

    Прогон старый, срок вышел, `keep_last` его не защищает — и всё равно он
    остаётся, потому что в нём висит вопрос, на который никто не ответил.
    """
    waiting = _run(db, "old-fail", age_days=400, verdict="fail", review=None)
    _run(db, "old-pass", age_days=400)

    retention.sweep(db, dropped, days=30, keep_last=0)
    assert waiting in _ids(db)
    assert len(_ids(db)) == 1


def test_a_reviewed_failure_may_go(db, dropped):
    """Решение принято, и его текст остаётся в истории снимка."""
    _run(db, "old-fail", age_days=400, verdict="fail", review="approved")
    retention.sweep(db, dropped, days=30, keep_last=0)
    assert _ids(db) == set()


def test_the_last_runs_stay_even_when_they_are_older_than_the_term(db, dropped):
    """Когда сегодня всё сломалось, точка отсчёта нужна именно старая."""
    for i in range(5):
        _run(db, f"r{i}", age_days=400)
    retention.sweep(db, dropped, days=30, keep_last=3)
    assert len(_ids(db)) == 3


def test_a_fresh_run_is_not_touched(db, dropped):
    _run(db, "today", age_days=0.1)
    retention.sweep(db, dropped, days=30, keep_last=0)
    assert len(_ids(db)) == 1


def test_keep_last_protects_each_project_separately(db, dropped):
    """Общий список означал бы, что активный проект вытесняет редкий.

    У редкого прогонов мало, они старые, и «последние пятьдесят» до них просто
    не доходят — то есть его историю сносит целиком, а он ни в чём не виноват.
    """
    for i in range(10):
        _run(db, f"busy{i}", age_days=400, project="busy")
    quiet = _run(db, "quiet1", age_days=400, project="quiet")

    retention.sweep(db, dropped, days=30, keep_last=3, project="*")
    assert quiet in _ids(db)


def test_a_dry_run_deletes_nothing(db, dropped):
    """Включать автоудаление вслепую значит узнать, что оно означало, назавтра."""
    for i in range(5):
        _run(db, f"r{i}", age_days=400)
    result = retention.sweep(db, dropped, days=30, keep_last=1, dry_run=True)
    assert result["deleted"] == 4
    assert result["dry_run"] is True
    assert len(_ids(db)) == 5
    assert dropped.seen == []


# --------------------------------------------------------------------------- #
#  Квота
# --------------------------------------------------------------------------- #
def test_the_quota_ignores_age(db, dropped):
    """Срок отвечает на «что уже не нужно», квота — на «место кончается»."""
    for i in range(5):
        _run(db, f"r{i}", age_days=0.1)      # все свежие
    result = retention.sweep(db, dropped, days=365, keep_last=1,
                             max_gb=0.001, used_kb=5 * 1024)
    assert result["deleted"] > 0


def test_the_quota_stops_as_soon_as_it_fits(db, dropped):
    """«Раз уж чистим — почистим с запасом» — это чужие данные без причины.

    Каждый прогон в фикстуре освобождает мегабайт; лимит превышен на два, значит
    удалить надо два прогона, а не всё, до чего дотянулись.
    """
    for i in range(10):
        _run(db, f"r{i}", age_days=0.1)
    limit_kb = 10 * 1024                      # 10 МБ
    result = retention.sweep(db, dropped, days=365, keep_last=1,
                             max_gb=limit_kb / 1024 / 1024,
                             used_kb=limit_kb + 2 * 1024)
    assert result["deleted"] == 2


def test_the_quota_still_will_not_touch_an_unreviewed_failure(db, dropped):
    """Диск можно докупить, разбор — нельзя."""
    waiting = _run(db, "waiting", age_days=400, verdict="fail", review=None)
    for i in range(5):
        _run(db, f"r{i}", age_days=0.1)
    retention.sweep(db, dropped, days=365, keep_last=0,
                    max_gb=0.0000001, used_kb=10 * 1024 * 1024)
    assert waiting in _ids(db)


def test_no_quota_means_no_quota(db, dropped):
    for i in range(5):
        _run(db, f"r{i}", age_days=0.1)
    result = retention.sweep(db, dropped, days=365, keep_last=1,
                             max_gb=0, used_kb=999 * 1024 * 1024)
    assert result["deleted"] == 0


# --------------------------------------------------------------------------- #
#  Расписание
# --------------------------------------------------------------------------- #
def test_it_is_off_until_switched_on(db, dropped):
    """Инсталляция на ноутбуке не имеет проблемы с диском.

    А сюрприз «куда делась история» — имеет.
    """
    _run(db, "old", age_days=400)
    result = retention.run_once(db, dropped)
    assert result["deleted"] == 0
    assert "off" in result["skipped"]
    assert len(_ids(db)) == 1


def test_a_scheduled_sweep_records_what_it_did(db, dropped):
    """Невидимый процесс, удаляющий данные, — то, чего в интерфейсе быть не должно."""
    for i in range(3):
        _run(db, f"r{i}", age_days=400)
    retention.configure(db, {"enabled": True, "days": 30, "keep_last": 1}, "anna")
    retention.run_once(db, dropped)

    conf = retention.settings(db)
    assert conf["last_run"]
    assert "2 runs" in conf["last_result"]


def test_the_ticker_is_one_thread_per_process(db, dropped):
    first = retention.ensure_ticker(db, dropped, lambda: 0)
    assert retention.ensure_ticker(db, dropped, lambda: 0) is first


# --------------------------------------------------------------------------- #
#  Настройки
# --------------------------------------------------------------------------- #
def test_keep_last_zero_is_refused(db):
    """Ноль — не настройка, а способ снести историю одним полем формы."""
    with pytest.raises(ValueError):
        retention.configure(db, {"keep_last": 0}, "anna")


def test_a_term_shorter_than_a_day_is_refused(db):
    with pytest.raises(ValueError):
        retention.configure(db, {"days": 0}, "anna")


def test_a_negative_quota_is_refused(db):
    with pytest.raises(ValueError):
        retention.configure(db, {"max_gb": -1}, "anna")


def test_a_missing_key_is_left_alone(db):
    retention.configure(db, {"enabled": True, "days": 10, "keep_last": 5}, "anna")
    retention.configure(db, {"days": 20}, "anna")
    conf = retention.settings(db)
    assert conf["enabled"] is True and conf["keep_last"] == 5
    assert conf["days"] == 20.0


def test_a_corrupt_setting_falls_back_to_the_default(db):
    """Мусор в настройке не должен превращаться в «удалить всё»."""
    retention.set_text(db, retention.KEEP_LAST, "не число", "test")
    assert retention.settings(db)["keep_last"] == retention.DEFAULT_KEEP_LAST


# --------------------------------------------------------------------------- #
#  Артефакты одиночных проверок
# --------------------------------------------------------------------------- #
def test_check_artifacts_are_swept_by_age(tmp_path):
    """Единственный каталог, до которого уборка не доставала вовсе.

    Она ходит по прогонам в базе и удаляет `artifacts/<id>`. Проверка из
    чужого набора (`POST /api/check`) прогона не создаёт — её картинки ложатся
    в `artifacts/checks/<run_key>`, и в базе про них нет ничего. На активном CI
    это росло бесконечно, а заметить можно было только по кончившемуся диску.
    """
    import os
    import time

    from vistest.api import retention

    checks = tmp_path / "checks"
    old = checks / "ci-100"
    fresh = checks / "ci-200"
    for d in (old, fresh):
        d.mkdir(parents=True)
        (d / "actual.png").write_bytes(b"x" * 2048)
    long_ago = time.time() - 40 * 86400
    os.utime(old, (long_ago, long_ago))

    result = retention.sweep_checks(checks, days=30)

    assert result["deleted"] == 1
    assert not old.exists() and fresh.exists()


def test_sweeping_checks_off_deletes_nothing(tmp_path):
    """Политика «без срока» не должна означать «удалить всё»."""
    from vistest.api import retention

    checks = tmp_path / "checks"
    (checks / "ci-1").mkdir(parents=True)

    assert retention.sweep_checks(checks, days=None)["deleted"] == 0
    assert retention.sweep_checks(checks, days=0)["deleted"] == 0
    assert (checks / "ci-1").exists()
