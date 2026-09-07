# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Metrics: Prometheus exposition and product aggregates for the dashboard."""

from __future__ import annotations

from .db import Database


def _scope(project: str) -> tuple[str, tuple]:
    """Project condition. `*` means all projects at once.

    Needed because externally connected test suites are written under their
    own names. Without this the dashboard silently showed zeros: it looked in
    `default`, while the runs lived in `acme_ui_tests`.
    """
    if project == "*":
        return "1=1", ()
    return "p.name = ?", (project,)


def summary(db: Database, project: str, days: int = 30) -> dict:
    from ..config import VisTestConfig

    # Один разбор конфига на запрос. `severity_histogram` читала и парсила
    # vistest.yaml с диска сама — то есть на каждом открытии дашборда.
    #
    # И сразу с переопределениями из интерфейса: гистограмма рисует вертикаль
    # «вот здесь порог», и стоять она обязана там, где порог на самом деле.
    # Взяв значение из конфига после того, как его подвинули в настройках,
    # картинка начала бы врать ровно в том месте, ради которого её и смотрят.
    cfg = VisTestConfig.load()
    try:
        from .thresholds import apply
        cfg = apply(cfg, db, project if project != "*" else None)
    except Exception:
        pass
    since = f"-{int(days)} days"
    where, args = _scope(project)
    p = args + (since,)

    totals = db.one(
        "SELECT COUNT(*) AS comparisons,"
        "       SUM(verdict='pass')  AS passed,"
        "       SUM(verdict='fail')  AS failed,"
        "       SUM(verdict='new_baseline') AS new_baselines,"
        "       SUM(verdict='error') AS errored,"
        # Reviews counted ONLY on failures. `SUM(review='approved')` over all
        # verdicts used to include approvals of `new_baseline` and `pass` rows,
        # so the false-fail rate could exceed 100% — and the dashboard's
        # «not reviewed yet» bar, computed as failed-approved-rejected, went
        # negative and was silently clamped to zero.
        "       SUM(verdict='fail' AND review='approved') AS approved,"
        "       SUM(verdict='fail' AND review='rejected') AS rejected,"
        "       SUM(verdict='fail' AND review IS NULL)    AS unreviewed,"
        "       AVG(duration_ms)     AS avg_duration_ms,"
        "       AVG(changed_area_pct) AS avg_changed_area"
        "  FROM comparison c"
        "  JOIN run r ON r.id = c.run_id JOIN project p ON p.id = r.project_id"
        f" WHERE {where} AND c.created_at >= datetime('now', ?)", p) or {}

    comparisons = totals.get("comparisons") or 0
    failed = totals.get("failed") or 0
    approved = totals.get("approved") or 0
    rejected = totals.get("rejected") or 0
    reviewed = approved + rejected

    # False-fail rate is a direct measure of engine quality: the share of
    # failures that a human looked at and said «everything is fine».
    #
    # The denominator is REVIEWED failures, not all of them. Dividing by all
    # failures answers a different question — «how many of everything that
    # broke turned out to be noise» — and it answers it wrongly, because a
    # failure nobody has looked at yet is not evidence of anything. With a
    # backlog of untriaged failures the old number drifted towards zero and
    # read as «the engine is getting better».
    totals["false_fail_rate"] = round(approved / reviewed, 4) if reviewed else None
    totals["reviewed_failures"] = reviewed
    totals["review_coverage"] = round(reviewed / failed, 4) if failed else 1.0

    # Pass rate over comparisons that could pass or fail. `new_baseline` had
    # nothing to compare against and `error` never got as far as comparing —
    # counting them as «not passed» made the first run of a fresh set report
    # 0% and look like a catastrophe.
    judged = (totals.get("passed") or 0) + failed
    totals["judged"] = judged
    totals["pass_rate"] = round((totals.get("passed") or 0) / judged, 4) \
        if judged else None
    totals["error_rate"] = round((totals.get("errored") or 0) / comparisons, 4) \
        if comparisons else 0.0

    # How confident may anyone be in the number above. `evidence.py` has always
    # computed this and the dashboard has always ignored it — «0% false
    # failures» over eight reviewed comparisons is not a result, and presenting
    # it as one is a lie by omission.
    totals["confidence"] = _wilson(approved, reviewed)
    totals["enough_data"] = reviewed >= 30

    totals["duration_ms"] = _duration_percentiles(db, where, p)
    totals["time_to_review"] = _time_to_review(db, where, p)

    by_kind = db.query(
        "SELECT rg.kind, COUNT(*) AS n, AVG(rg.severity) AS avg_severity"
        "  FROM region rg JOIN comparison c ON c.id = rg.comparison_id"
        "  JOIN run r ON r.id = c.run_id JOIN project p ON p.id = r.project_id"
        f" WHERE {where} AND c.created_at >= datetime('now', ?)"
        " GROUP BY rg.kind ORDER BY n DESC", p)

    trend = db.query(
        "SELECT date(c.created_at) AS day, COUNT(*) AS total,"
        "       SUM(c.verdict='fail') AS failed,"
        "       SUM(c.review='approved') AS approved,"
        "       AVG(c.changed_area_pct) AS avg_changed"
        "  FROM comparison c JOIN run r ON r.id = c.run_id"
        "  JOIN project p ON p.id = r.project_id"
        f" WHERE {where} AND c.created_at >= datetime('now', ?)"
        " GROUP BY day ORDER BY day", p)

    # `runs` в ответе больше нет: двести полных строк прогонов уезжали в каждый
    # ответ дашборда и не читались ни одной строкой интерфейса. Список прогонов
    # берётся из `/api/runs`, у которого для этого есть и фильтры, и потолок.
    return {
        "project": project,
        "days": days,
        "totals": totals,
        "by_kind": by_kind,
        "trend": trend,
        "severity": severity_histogram(db, project, days, cfg=cfg),
        "slowest": slowest(db, project, days),
        "flaky": flaky(db, project, days),
        "valuable": valuable(db, project, days),
        "stale": stale_snapshots(db, project),
        "by_project": by_project(db, project, days),
        "baseline_age": baseline_age(db, project),
    }


def valuable(db: Database, project: str, days: int = 30,
             limit: int = 10) -> list[dict]:
    """Snapshots that caught real regressions — the mirror image of `flaky`.

    `flaky` is a list of what to switch off. This is the list of what pays for
    the whole thing, and without it the dashboard only ever argues against
    itself: every metric on the page describes a cost.
    """
    where, args = _scope(project)
    return db.query(
        "SELECT s.name, s.platform, COUNT(*) AS runs,"
        "       SUM(c.verdict='fail' AND c.review='rejected') AS caught,"
        "       ROUND(AVG(c.max_severity), 1) AS avg_severity"
        "  FROM comparison c"
        "  JOIN snapshot s ON s.id = c.snapshot_id"
        "  JOIN project p ON p.id = s.project_id"
        f" WHERE {where} AND c.created_at >= datetime('now', ?)"
        " GROUP BY s.id HAVING caught > 0"
        " ORDER BY caught DESC, avg_severity DESC LIMIT ?",
        args + (f"-{int(days)} days", limit))


def by_project(db: Database, project: str, days: int = 30) -> list[dict]:
    """Per-project breakdown.

    With `project='*'` — the default in the interface — everything used to be
    collapsed into one set of numbers, so a single noisy suite made the whole
    installation look broken and there was no way to see which one it was.
    """
    where, args = _scope(project)
    return db.query(
        "SELECT p.name AS project, COUNT(*) AS comparisons,"
        "       SUM(c.verdict='pass') AS passed,"
        "       SUM(c.verdict='fail') AS failed,"
        "       SUM(c.verdict='error') AS errored,"
        "       SUM(c.verdict='fail' AND c.review='approved') AS approved,"
        "       SUM(c.verdict='fail' AND c.review IS NULL) AS unreviewed"
        "  FROM comparison c JOIN run r ON r.id = c.run_id"
        "  JOIN project p ON p.id = r.project_id"
        f" WHERE {where} AND c.created_at >= datetime('now', ?)"
        " GROUP BY p.id ORDER BY failed DESC, comparisons DESC",
        args + (f"-{int(days)} days",))


def baseline_age(db: Database, project: str) -> dict:
    """How old the baselines are.

    A set nobody has re-approved in half a year is either perfectly stable or
    quietly detached from the product. Both are worth knowing, and the answer
    lives in the approval journal — which is exactly why that table stopped
    being dead weight.
    """
    where, args = _scope(project)
    # Фильтр по проекту здесь ВЫЧИСЛЯЛСЯ И НЕ ИСПОЛЬЗОВАЛСЯ: какой бы проект ни
    # был выбран, числа были по всей инсталляции. Метрика, молча игнорирующая
    # фильтр над собой, хуже отсутствующей — её читают как ответ на вопрос,
    # которого ей не задавали.
    #
    # Фильтруем через `snapshot`: это единственное место, где строка журнала
    # привязана к проекту. Строку, которую привязать не к чему (апрув до
    # появления журнала или снимок с тех пор удалён), не показываем ни под
    # каким проектом, кроме «все».
    rows = db.query(
        "SELECT b.name, b.platform, b.scope, b.approved_by,"
        "       MAX(b.approved_at) AS approved_at,"
        "       CAST(julianday('now') - julianday(MAX(b.approved_at)) AS INTEGER)"
        "         AS age_days"
        "  FROM baseline b"
        "  LEFT JOIN snapshot s ON s.id = b.snapshot_id"
        "  LEFT JOIN project p ON p.id = s.project_id"
        f" WHERE b.is_current = 1 AND {where}"
        " GROUP BY b.scope, b.project_key, b.platform, b.name"
        " ORDER BY age_days DESC LIMIT 25", args)
    ages = [r["age_days"] for r in rows if r["age_days"] is not None]
    return {
        "oldest": rows[:10],
        "median_days": _percentile([float(a) for a in ages], 0.50),
        "tracked": len(rows),
        # Said out loud: the journal starts filling only from the release that
        # introduced it, so an installation upgraded yesterday sees a small
        # number here and that is not a bug.
        "note": "Counted from the approval journal — baselines approved before "
                "the journal existed are not in it yet.",
    }


def _wilson(hits: int, total: int, z: float = 1.96) -> dict:
    """Wilson interval for a share.

    Not `hits/total ± something`: at the edges (0 of 8, 8 of 8) the normal
    approximation gives an interval that runs outside [0, 1] and looks like
    certainty where there is none.
    """
    if not total:
        return {"low": None, "high": None, "n": 0}
    phat = hits / total
    denom = 1 + z * z / total
    centre = phat + z * z / (2 * total)
    margin = z * ((phat * (1 - phat) / total + z * z / (4 * total * total)) ** 0.5)
    return {"low": round(max(0.0, (centre - margin) / denom), 4),
            "high": round(min(1.0, (centre + margin) / denom), 4),
            "n": total}


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 1)
    pos = q * (len(ordered) - 1)
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    return round(ordered[low] + (ordered[high] - ordered[low]) * (pos - low), 1)


def _duration_percentiles(db: Database, where: str, params: tuple) -> dict:
    """p50/p95 alongside the mean.

    The mean alone is the wrong statistic here: one full-page snapshot at
    8000px takes tens of times longer than a widget, and it drags the average
    somewhere no actual comparison lives.
    """
    rows = db.query(
        "SELECT c.duration_ms AS v FROM comparison c"
        "  JOIN run r ON r.id = c.run_id JOIN project p ON p.id = r.project_id"
        f" WHERE {where} AND c.created_at >= datetime('now', ?)"
        "   AND c.duration_ms IS NOT NULL", params)
    values = [float(r["v"]) for r in rows if r["v"] is not None]
    return {
        "p50": _percentile(values, 0.50),
        "p95": _percentile(values, 0.95),
        "max": round(max(values), 1) if values else None,
        "n": len(values),
    }


def _time_to_review(db: Database, where: str, params: tuple) -> dict:
    """How long a failure waits for a human.

    The metric nobody had, and the one that decides whether the false-fail rate
    means anything at all. If failures sit untouched for three days, «0% false»
    only says that nobody looked.
    """
    rows = db.query(
        "SELECT (julianday(c.reviewed_at) - julianday(c.created_at)) * 24 AS hours"
        "  FROM comparison c"
        "  JOIN run r ON r.id = c.run_id JOIN project p ON p.id = r.project_id"
        f" WHERE {where} AND c.created_at >= datetime('now', ?)"
        "   AND c.verdict='fail' AND c.reviewed_at IS NOT NULL", params)
    hours = [float(r["hours"]) for r in rows
             if r["hours"] is not None and r["hours"] >= 0]

    oldest = db.one(
        "SELECT MIN(c.created_at) AS since, COUNT(*) AS n FROM comparison c"
        "  JOIN run r ON r.id = c.run_id JOIN project p ON p.id = r.project_id"
        f" WHERE {where} AND c.created_at >= datetime('now', ?)"
        "   AND c.verdict='fail' AND c.review IS NULL", params) or {}

    return {
        "median_hours": _percentile(hours, 0.50),
        "p95_hours": _percentile(hours, 0.95),
        "reviewed": len(hours),
        "waiting": (oldest.get("n") or 0),
        "waiting_since": oldest.get("since"),
    }


def severity_histogram(db: Database, project: str, days: int = 30,
                       cfg=None) -> dict:
    """Distribution of severity across failures, and where the threshold sits.

    The most practical picture on the dashboard, because it answers the
    question that tuning starts with: «which threshold should I set». If
    failures cluster around 20–30 and the threshold is 25, half of them are a
    matter of taste rather than a regression. If there is a gap between noise
    and real breakages, the threshold should be placed in that gap.
    """
    where, args = _scope(project)
    rows = db.query(
        "SELECT c.max_severity AS v FROM comparison c"
        "  JOIN run r ON r.id = c.run_id JOIN project p ON p.id = r.project_id"
        f" WHERE {where} AND c.created_at >= datetime('now', ?)"
        "   AND c.max_severity IS NOT NULL AND c.max_severity > 0",
        args + (f"-{int(days)} days",))

    step = 10
    buckets = [{"from": i, "to": i + step, "n": 0, "approved": 0}
               for i in range(0, 100, step)]
    for r in rows:
        idx = min(int((r["v"] or 0) // step), len(buckets) - 1)
        buckets[idx]["n"] += 1

    # How many failures in each bucket a human reviewed and accepted: if the
    # approvals concentrate in the low buckets, the threshold is set too low.
    approved = db.query(
        "SELECT c.max_severity AS v FROM comparison c"
        "  JOIN run r ON r.id = c.run_id JOIN project p ON p.id = r.project_id"
        f" WHERE {where} AND c.created_at >= datetime('now', ?)"
        "   AND c.review = 'approved' AND c.max_severity > 0",
        args + (f"-{int(days)} days",))
    for r in approved:
        idx = min(int((r["v"] or 0) // step), len(buckets) - 1)
        buckets[idx]["approved"] += 1

    if cfg is None:
        from ..config import VisTestConfig
        cfg = VisTestConfig.load()
    return {"buckets": buckets, "total": len(rows),
            "threshold": cfg.diff.fail_severity}


def slowest(db: Database, project: str, days: int = 30, limit: int = 8) -> list[dict]:
    """The slowest snapshots. Usually full-page canvases at 8000px."""
    where, args = _scope(project)
    return db.query(
        "SELECT s.name, ROUND(AVG(c.duration_ms)) AS avg_ms, COUNT(*) AS runs"
        "  FROM comparison c JOIN snapshot s ON s.id = c.snapshot_id"
        "  JOIN run r ON r.id = c.run_id JOIN project p ON p.id = r.project_id"
        f" WHERE {where} AND c.created_at >= datetime('now', ?)"
        "   AND c.duration_ms IS NOT NULL"
        " GROUP BY s.id ORDER BY avg_ms DESC LIMIT ?",
        args + (f"-{int(days)} days", limit))


def flaky(db: Database, project: str, days: int = 30, limit: int = 15) -> list[dict]:
    """Snapshots that fail and get approved right away are noise, not a regression.

    Such a list is a working backlog: each row either asks for a mask,
    or asks to delete a useless snapshot.
    """
    where, args = _scope(project)
    return db.query(
        "SELECT s.name, s.platform,"
        "       COUNT(*) AS runs,"
        "       SUM(c.verdict='fail') AS fails,"
        "       SUM(c.review='approved') AS approved_fails,"
        "       ROUND(1.0*SUM(c.verdict='fail')/COUNT(*), 4) AS fail_rate,"
        "       ROUND(AVG(c.changed_area_pct), 5) AS avg_changed_area"
        "  FROM comparison c"
        "  JOIN snapshot s ON s.id = c.snapshot_id"
        "  JOIN project p ON p.id = s.project_id"
        f" WHERE {where} AND c.created_at >= datetime('now', ?)"
        " GROUP BY s.id HAVING runs >= 3 AND fails > 0"
        " ORDER BY approved_fails DESC, fail_rate DESC LIMIT ?",
        args + (f"-{int(days)} days", limit))


def stale_snapshots(db: Database, project: str, days: int = 30) -> list[dict]:
    """Snapshots without runs are dead weight that everyone is afraid to delete."""
    where, args = _scope(project)
    return db.query(
        "SELECT s.name, s.platform, MAX(c.created_at) AS last_run"
        "  FROM snapshot s JOIN project p ON p.id = s.project_id"
        "  LEFT JOIN comparison c ON c.snapshot_id = s.id"
        f" WHERE {where}"
        " GROUP BY s.id"
        " HAVING last_run IS NULL OR last_run < datetime('now', ?)"
        " ORDER BY last_run ASC LIMIT 50",
        args + (f"-{int(days)} days",))


# --------------------------------------------------------------------------- #
#  Prometheus
# --------------------------------------------------------------------------- #
def _label(value) -> str:
    """Escape a Prometheus label value.

    Backslash, double quote and newline must be escaped — a snapshot named
    `search "all"` or a Windows path in a label used to break the exposition
    format outright, and Prometheus dropped the whole scrape, not just that
    line.
    """
    return (str(value if value is not None else "")
            .replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n"))


def _service_lines() -> list[str]:
    """Запросы, ошибки и время — то, о чём спрашивают, когда что-то не так.

    `counter`, а не `gauge`: это накопленные с момента старта значения, и их
    сброс при перезапуске Prometheus понимает правильно. Отдельная функция —
    чтобы падение здесь не уносило с собой всю выдачу: метрики читает
    мониторинг, и «нет ответа» он покажет как «сервис лежит».
    """
    from . import logs as _logs

    lines: list[str] = []
    try:
        counts, seconds = _logs.counters()
    except Exception:                                        # pragma: no cover
        return lines

    lines.append("# HELP vistest_http_requests_total Requests served since start")
    lines.append("# TYPE vistest_http_requests_total counter")
    for (method, code), n in sorted(counts.items()):
        lines.append(f'vistest_http_requests_total{{method="{method}",'
                     f'code="{code}"}} {n}')

    lines.append("# HELP vistest_http_seconds_total Time spent serving them")
    lines.append("# TYPE vistest_http_seconds_total counter")
    for (method, code), value in sorted(seconds.items()):
        lines.append(f'vistest_http_seconds_total{{method="{method}",'
                     f'code="{code}"}} {value:.3f}')

    return lines


def _job_lines(db: Database) -> list[str]:
    """Очередь фоновых задач.

    Одно число, объясняющее «почему кнопка „Прогнать“ отвечает „подождите“» без
    похода в интерфейс. Считается по БАЗЕ, а не по памяти процесса: задачи
    зеркалятся туда, и при нескольких процессах на общем томе память знает
    только про свои.
    """
    try:
        rows = db.query(
            "SELECT status, COUNT(*) AS n FROM job"
            " WHERE status IN ('queued','running') GROUP BY status")
    except Exception:                                        # pragma: no cover
        return []
    counts = {r["status"]: r["n"] for r in rows}
    lines = ["# HELP vistest_jobs Background jobs right now",
             "# TYPE vistest_jobs gauge"]
    for status in ("queued", "running"):
        lines.append(f'vistest_jobs{{status="{status}"}} {counts.get(status, 0)}')
    return lines


def prometheus(db: Database) -> str:
    lines: list[str] = _service_lines() + _job_lines(db)

    def add(name: str, help_: str, type_: str, rows, fmt):
        lines.append(f"# HELP {name} {help_}")
        lines.append(f"# TYPE {name} {type_}")
        lines.extend(fmt(r) for r in rows)

    # `gauge`, not `counter`. These are absolute counts read out of the
    # database, and history cleanup makes them go DOWN — which for a counter
    # means «the process restarted» and makes `rate()` invent a spike out of
    # nothing. A gauge describes what this is honestly: the current number of
    # rows.
    add("vistest_comparisons",
        "Comparisons currently stored, by verdict", "gauge",
        db.query("SELECT p.name AS project, c.verdict, COUNT(*) AS n"
                 " FROM comparison c JOIN run r ON r.id=c.run_id"
                 " JOIN project p ON p.id=r.project_id GROUP BY p.name, c.verdict"),
        lambda r: f'vistest_comparisons{{project="{_label(r["project"])}",'
                  f'verdict="{_label(r["verdict"])}"}} {r["n"]}')

    add("vistest_regions", "Detected regions by class", "gauge",
        db.query("SELECT kind, COUNT(*) AS n FROM region GROUP BY kind"),
        lambda r: f'vistest_regions{{kind="{_label(r["kind"])}"}} {r["n"]}')

    add("vistest_comparison_duration_ms", "Average comparison time", "gauge",
        db.query("SELECT p.name AS project, AVG(c.duration_ms) AS v"
                 " FROM comparison c JOIN run r ON r.id=c.run_id"
                 " JOIN project p ON p.id=r.project_id GROUP BY p.name"),
        lambda r: f'vistest_comparison_duration_ms{{project="{_label(r["project"])}"}} '
                  f'{r["v"] or 0:.1f}')

    add("vistest_snapshot_fail_rate",
        "Failure share per snapshot for the last 30 days", "gauge",
        db.query("SELECT s.name, p.name AS project,"
                 " 1.0*SUM(c.verdict='fail')/COUNT(*) AS v"
                 " FROM comparison c JOIN snapshot s ON s.id=c.snapshot_id"
                 " JOIN project p ON p.id=s.project_id"
                 " WHERE c.created_at >= datetime('now','-30 days')"
                 " GROUP BY s.id"),
        lambda r: f'vistest_snapshot_fail_rate{{project="{_label(r["project"])}",'
                  f'snapshot="{_label(r["name"])}"}} {r["v"] or 0:.4f}')

    # Approvals counted on failures only, and divided by REVIEWED failures.
    # The old query took `SUM(review='approved')` across every verdict — an
    # approval on a `new_baseline` row inflated the numerator — and divided by
    # all failures, so an untriaged backlog pushed the number towards zero and
    # read as «the engine got better».
    add("vistest_false_fail_rate",
        "Share of REVIEWED failures a human accepted as normal", "gauge",
        db.query("SELECT p.name AS project,"
                 " 1.0*SUM(c.verdict='fail' AND c.review='approved')"
                 "   /NULLIF(SUM(c.verdict='fail' AND c.review IS NOT NULL),0) AS v"
                 " FROM comparison c JOIN run r ON r.id=c.run_id"
                 " JOIN project p ON p.id=r.project_id GROUP BY p.name"),
        lambda r: f'vistest_false_fail_rate{{project="{_label(r["project"])}"}} '
                  f'{r["v"] or 0:.4f}')

    add("vistest_unreviewed_failures",
        "Failures nobody has looked at yet — the context for the rate above",
        "gauge",
        db.query("SELECT p.name AS project, COUNT(*) AS n"
                 " FROM comparison c JOIN run r ON r.id=c.run_id"
                 " JOIN project p ON p.id=r.project_id"
                 " WHERE c.verdict='fail' AND c.review IS NULL"
                 " GROUP BY p.name"),
        lambda r: f'vistest_unreviewed_failures{{project="{_label(r["project"])}"}} '
                  f'{r["n"]}')

    return "\n".join(lines) + "\n"
