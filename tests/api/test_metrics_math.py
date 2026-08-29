# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Арифметика дашборда.

Метрики были неинформативны не потому, что их мало, а потому, что часть из них
считалась неверно и подавалась как факт:

* доля ложных срабатываний бралась как «апрувы / все падения», причём апрувы
  считались по всем вердиктам сразу — число могло превысить 100%, а полоса
  «ещё не разобрано» на дашборде уходила в минус и молча обнулялась;
* `pass_rate` делился на все сравнения, включая `new_baseline` и `error`, —
  первый же прогон свежего набора показывал «0% проходов»;
* вердикт `error` не попадал в сводку вообще: ошибки капчи просто топили
  pass_rate, оставаясь невидимыми;
* доверительный интервал считался в `evidence.py` и не доезжал до дашборда —
  «0% ложных» на восьми сравнениях выглядело как результат.
"""

from __future__ import annotations

import pytest

from vistest.api import metrics as m
from vistest.api.db import Database


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "vistest.db")


def _seed(db, rows: list[tuple], project: str = "demo") -> None:
    """rows: (verdict, review, duration_ms, severity)."""
    pid = db.project_id(project)
    db.execute(
        "INSERT INTO run(project_id, run_key, platform, started_at)"
        " VALUES(?,?,?,datetime('now'))", (pid, f"r-{project}", "linux"))
    run_id = db.one("SELECT id FROM run WHERE run_key=?", (f"r-{project}",))["id"]
    for i, (verdict, review, duration, severity) in enumerate(rows):
        sid = db.snapshot_id(pid, f"snap{i}.png", "linux", "chromium")
        db.execute(
            "INSERT INTO comparison(run_id, snapshot_id, verdict, review,"
            " duration_ms, max_severity, changed_area_pct, created_at)"
            " VALUES(?,?,?,?,?,?,?,datetime('now'))",
            (run_id, sid, verdict, review, duration, severity, 0.5))


# --------------------------------------------------------------------------- #
#  false_fail_rate
# --------------------------------------------------------------------------- #
def test_approvals_on_other_verdicts_do_not_count_as_false_failures(db):
    """Апрув на `new_baseline` — не ложное срабатывание движка."""
    _seed(db, [
        ("fail", "approved", 100, 40),
        ("fail", "rejected", 100, 40),
        ("new_baseline", "approved", 100, 0),
        ("new_baseline", "approved", 100, 0),
    ])
    totals = m.summary(db, "demo")["totals"]

    assert totals["approved"] == 1
    assert totals["rejected"] == 1
    # Раньше здесь получалось 3/2 = 150%.
    assert totals["false_fail_rate"] == 0.5


def test_rate_is_taken_over_reviewed_failures_only(db):
    """Непросмотренное падение — не свидетельство ни за, ни против."""
    _seed(db, [
        ("fail", "approved", 100, 40),
        ("fail", None, 100, 40),
        ("fail", None, 100, 40),
        ("fail", None, 100, 40),
    ])
    totals = m.summary(db, "demo")["totals"]

    assert totals["false_fail_rate"] == 1.0      # разобрано одно, и оно ложное
    assert totals["unreviewed"] == 3
    assert totals["review_coverage"] == 0.25


def test_rate_is_none_when_nothing_was_reviewed(db):
    """Ноль просмотров — не «0% ложных», а «неизвестно»."""
    _seed(db, [("fail", None, 100, 40), ("fail", None, 100, 40)])
    totals = m.summary(db, "demo")["totals"]

    assert totals["false_fail_rate"] is None
    assert totals["review_coverage"] == 0.0


def test_unreviewed_never_goes_negative(db):
    """Полоса на дашборде считается из этих же чисел."""
    _seed(db, [
        ("fail", "approved", 100, 40),
        ("new_baseline", "approved", 100, 0),
        ("pass", "approved", 100, 0),
    ])
    t = m.summary(db, "demo")["totals"]
    assert t["failed"] - t["approved"] - t["rejected"] == t["unreviewed"] >= 0


# --------------------------------------------------------------------------- #
#  pass_rate и ошибки
# --------------------------------------------------------------------------- #
def test_new_baselines_do_not_count_as_failures_to_pass(db):
    """Первый прогон свежего набора — не катастрофа."""
    _seed(db, [("new_baseline", None, 50, 0)] * 5)
    totals = m.summary(db, "demo")["totals"]

    assert totals["new_baselines"] == 5
    # Раньше: 0/5 = 0% и красный дашборд на пустом месте.
    assert totals["pass_rate"] is None
    assert totals["judged"] == 0


def test_errors_are_visible_and_not_silently_drowning_pass_rate(db):
    _seed(db, [
        ("pass", None, 50, 0),
        ("pass", None, 50, 0),
        ("error", None, None, None),
    ])
    totals = m.summary(db, "demo")["totals"]

    assert totals["errored"] == 1
    assert totals["error_rate"] == round(1 / 3, 4)
    # Ошибка не считается «непройденным»: сравнения не было вовсе.
    assert totals["pass_rate"] == 1.0


# --------------------------------------------------------------------------- #
#  Доверие к числу
# --------------------------------------------------------------------------- #
def test_small_samples_are_marked_as_such(db):
    _seed(db, [("fail", "approved", 50, 40)] * 3)
    totals = m.summary(db, "demo")["totals"]

    assert totals["enough_data"] is False
    ci = totals["confidence"]
    assert ci["n"] == 3
    # На трёх наблюдениях интервал обязан быть широким, а не «100% и точка».
    assert ci["low"] < 0.6


def test_wilson_interval_stays_inside_zero_one():
    assert m._wilson(0, 8)["low"] == 0.0
    assert m._wilson(8, 8)["high"] == 1.0
    assert m._wilson(0, 0) == {"low": None, "high": None, "n": 0}


# --------------------------------------------------------------------------- #
#  Перцентили вместо среднего
# --------------------------------------------------------------------------- #
def test_duration_reports_percentiles_not_only_the_mean(db):
    _seed(db, [("pass", None, ms, 0) for ms in (10, 10, 10, 10, 5000)])
    d = m.summary(db, "demo")["totals"]["duration_ms"]

    assert d["p50"] == 10          # среднее здесь было бы 1008 мс
    assert d["max"] == 5000
    assert d["n"] == 5


def test_percentile_helper_handles_edges():
    assert m._percentile([], 0.5) is None
    assert m._percentile([7.0], 0.95) == 7.0


# --------------------------------------------------------------------------- #
#  Новые срезы
# --------------------------------------------------------------------------- #
def test_time_to_review_counts_the_backlog(db):
    _seed(db, [("fail", None, 50, 40), ("fail", None, 50, 40)])
    ttr = m.summary(db, "demo")["totals"]["time_to_review"]

    assert ttr["waiting"] == 2
    assert ttr["reviewed"] == 0
    assert ttr["median_hours"] is None


def test_per_project_breakdown_is_available(db):
    _seed(db, [("fail", None, 50, 40)], project="alpha")
    _seed(db, [("pass", None, 50, 0)], project="beta")

    rows = {r["project"]: r for r in m.summary(db, "*")["by_project"]}
    assert rows["alpha"]["failed"] == 1
    assert rows["beta"]["passed"] == 1


def test_valuable_lists_snapshots_that_caught_real_bugs(db):
    _seed(db, [("fail", "rejected", 50, 60), ("fail", "approved", 50, 20)])
    caught = m.summary(db, "demo")["valuable"]

    assert len(caught) == 1
    assert caught[0]["caught"] == 1


# --------------------------------------------------------------------------- #
#  Prometheus
# --------------------------------------------------------------------------- #
def test_label_values_are_escaped(db):
    """Имя снимка с кавычкой ломало весь ответ /metrics, а не одну строку."""
    pid = db.project_id('say "hi"')
    db.execute("INSERT INTO run(project_id, run_key) VALUES(?,?)", (pid, "r1"))
    run_id = db.one("SELECT id FROM run WHERE run_key='r1'")["id"]
    sid = db.snapshot_id(pid, 'weird\\name "x".png', "linux", "chromium")
    db.execute(
        "INSERT INTO comparison(run_id, snapshot_id, verdict, created_at)"
        " VALUES(?,?,'fail',datetime('now'))", (run_id, sid))

    text = m.prometheus(db)
    assert '\\"hi\\"' in text
    assert 'weird\\\\name' in text
    # Каждая строка-значение обязана иметь ровно один незаэкранированный конец.
    for line in text.splitlines():
        if line.startswith("vistest_") and "{" in line:
            assert line.count('"') % 2 == 0


def test_false_fail_rate_in_prometheus_uses_reviewed_failures(db):
    _seed(db, [
        ("fail", "approved", 50, 40),
        ("fail", None, 50, 40),
        ("new_baseline", "approved", 50, 0),
    ])
    text = m.prometheus(db)
    line = next(row for row in text.splitlines()
                if row.startswith("vistest_false_fail_rate{"))
    assert line.endswith(" 1.0000")            # 1 апрув из 1 разобранного
    assert "vistest_unreviewed_failures" in text
