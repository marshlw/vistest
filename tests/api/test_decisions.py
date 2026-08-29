# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Очередь решений: не «что красное», а «на что ответить».

Экран прогона отвечает на первый вопрос. Человек, открывающий VisTest утром,
задаёт второй, и это не список из двадцати трёх картинок.

Разница дорогая и уже описана в `cluster.py`: двадцать снимков покраснели от
одной правки line-height, человек разбирает их по одному, к пятому перестаёт
смотреть, к десятому принимает не глядя. Кластеризация внутри прогона лечила
это наполовину — надо было сначала найти нужный прогон. Падение, приехавшее три
прогона назад и с тех пор повторяющееся, не видно нигде: в последнем прогоне
оно есть, но выглядит как «ещё двадцать красных».

Здесь проверяется арифметика очереди — та, на которую человек смотрит, решая,
можно ли мержить. Ошибка в ней не выглядит как ошибка: счётчик просто показывает
не то число, и это замечают через неделю.
"""

from __future__ import annotations

import json

import pytest

from vistest.api import decisions
from vistest.api.db import Database


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "vistest.db")


def _run(db, key: str, *, project: str = "shop", branch: str = "main",
         started: str = "2026-01-01 10:00:00") -> dict:
    pid = db.project_id(project)
    db.execute(
        "INSERT INTO run(project_id, run_key, platform, branch, started_at)"
        " VALUES(?,?,?,?,?)", (pid, key, "linux", branch, started))
    return db.one("SELECT * FROM run WHERE run_key=?", (key,))


def _cmp(db, run: dict, name: str, verdict: str = "fail", *,
         severity: float = 50.0, review: str | None = None,
         regions: list[dict] | None = None, created: str | None = None,
         error: str = "") -> int:
    sid = db.snapshot_id(run["project_id"], name, "linux", "chromium")
    meta = json.dumps({"error": error}) if error else "{}"
    db.execute(
        "INSERT INTO comparison(run_id, snapshot_id, verdict, review,"
        " max_severity, changed_area_pct, ssim, meta, created_at)"
        " VALUES(?,?,?,?,?,?,?,?,?)",
        (run["id"], sid, verdict, review, severity, 1.0, 0.99, meta,
         created or "2026-01-01 10:00:00"))
    cid = db.one("SELECT id FROM comparison ORDER BY id DESC")["id"]
    for r in (regions or []):
        db.execute(
            "INSERT INTO region(comparison_id, x, y, w, h, kind, severity,"
            " moved_dx, moved_dy, selector) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (cid, r.get("x", 0), r.get("y", 0), r.get("w", 100),
             r.get("h", 40), r.get("kind", "moved"), r.get("severity", severity),
             r.get("dx", 0), r.get("dy", 14), r.get("selector", ".row")))
    return cid


def _shift(selector: str = "section.summary > .row", dy: int = 14) -> list[dict]:
    """Один и тот же сдвиг — то, что кластеризация обязана собрать в причину."""
    return [{"kind": "moved", "selector": selector, "dy": dy, "severity": 80}]


# --------------------------------------------------------------------------- #
#  Одна причина вместо двадцати вопросов
# --------------------------------------------------------------------------- #
def test_one_cause_covers_every_snapshot_behind_it(db):
    """То, ради чего экран и написан.

    Одна правка line-height — одиннадцать красных снимков и ОДИН вопрос.
    """
    run = _run(db, "r1")
    for i in range(11):
        _cmp(db, run, f"checkout/p{i}.png", regions=_shift())

    q = decisions.queue(db, "shop")
    assert q["counts"]["causes"] == 1
    assert q["counts"]["snapshots"] == 11
    assert q["causes"][0]["count"] == 11


def test_every_pending_snapshot_belongs_to_exactly_one_cause(db):
    """Иначе сумма причин больше числа снимков.

    Регион снимка может подойти двум кластерам сразу. Отдать снимок обоим
    значит показать человеку, что вопросов больше, чем красного, — и оставить
    счётчик ненулевым после того, как он ответил на всё.
    """
    run = _run(db, "r1")
    for i in range(4):
        _cmp(db, run, f"a/p{i}.png", regions=[
            {"kind": "moved", "selector": ".head > .row", "dy": 14},
            {"kind": "moved", "selector": ".foot > .row", "dy": 14},
        ])

    q = decisions.queue(db, "shop")
    counted = sum(c["count"] for c in q["causes"])
    assert counted == q["counts"]["snapshots"] == 4

    seen = [n for c in q["causes"] for n in c["snapshots"]]
    assert len(seen) == len(set(seen)), "снимок попал в две причины"


def test_a_snapshot_without_a_shared_cause_is_still_a_question(db):
    """Тихо потерять неразобранное падение — ровно та поломка, ради которой
    очередь и написана.

    Общей причины для него нет, но ответа оно требует ровно так же.
    """
    run = _run(db, "r1")
    for i in range(3):
        _cmp(db, run, f"a/p{i}.png", regions=_shift())
    _cmp(db, run, "lonely.png", regions=[
        {"kind": "color", "selector": ".unique-thing", "dy": 0}])

    q = decisions.queue(db, "shop")
    assert q["counts"]["snapshots"] == 4
    assert sum(c["count"] for c in q["causes"]) == 4
    solo = [c for c in q["causes"] if c["count"] == 1]
    assert [n for c in solo for n in c["snapshots"]] == ["lonely.png"]


def test_a_group_that_shrank_below_two_is_not_called_a_cause(db):
    """Иначе «причина» из одного снимка стоит рядом с причиной из одиннадцати.

    Соседние причины могли разобрать её снимки; то, что осталось, — уже не
    группа, и показывать его как группу значит уравнять их в глазах человека.
    """
    run = _run(db, "r1")
    for i in range(5):
        _cmp(db, run, f"a/p{i}.png", regions=[
            {"kind": "moved", "selector": ".head > .row", "dy": 14},
            {"kind": "moved", "selector": ".foot > .row", "dy": 14},
        ])
    q = decisions.queue(db, "shop")
    assert len(q["causes"]) == 1, [c["title"] for c in q["causes"]]


# --------------------------------------------------------------------------- #
#  Что попадает в очередь
# --------------------------------------------------------------------------- #
def test_an_answered_failure_leaves_the_queue(db):
    """Очередь — это то, что ЖДЁТ ответа. Разобранное в ней не место."""
    run = _run(db, "r1")
    for i in range(3):
        _cmp(db, run, f"a/p{i}.png", regions=_shift())
    _cmp(db, run, "done.png", review="approved", regions=_shift())

    q = decisions.queue(db, "shop")
    assert q["counts"]["snapshots"] == 3
    assert "done.png" not in [n for c in q["causes"] for n in c["snapshots"]]


def test_the_same_failure_across_five_runs_is_one_question(db):
    """Считать по сравнениям значило бы «115 решений» там, где их двадцать три.

    Счётчик, который врёт в пять раз, перестают читать в тот же день.
    """
    for n in range(5):
        run = _run(db, f"r{n}", started=f"2026-01-0{n + 1} 10:00:00")
        for i in range(3):
            _cmp(db, run, f"a/p{i}.png", regions=_shift(),
                 created=f"2026-01-0{n + 1} 10:00:00")

    q = decisions.queue(db, "shop")
    assert q["counts"]["snapshots"] == 3


def test_a_snapshot_that_went_green_again_asks_nothing(db):
    """Вчера падал, сегодня зелёный — вопроса нет.

    Брать «последнее КРАСНОЕ сравнение» вместо «последнего сравнения, если оно
    красное» значило бы держать в очереди то, что уже починилось само.
    """
    old = _run(db, "r1", started="2026-01-01 10:00:00")
    new = _run(db, "r2", started="2026-01-02 10:00:00")
    _cmp(db, old, "p.png", regions=_shift(), created="2026-01-01 10:00:00")
    _cmp(db, new, "p.png", verdict="pass", severity=0,
         created="2026-01-02 10:00:00")

    assert decisions.queue(db, "shop")["counts"]["snapshots"] == 0


def test_another_projects_failures_are_not_in_this_queue(db):
    """Одно решение не может закрывать снимки двух чужих друг другу наборов."""
    mine = _run(db, "r1", project="shop")
    theirs = _run(db, "r2", project="billing")
    _cmp(db, mine, "p.png", regions=_shift())
    _cmp(db, theirs, "p.png", regions=_shift())

    assert decisions.queue(db, "shop")["counts"]["snapshots"] == 1


# --------------------------------------------------------------------------- #
#  Заблокированные
# --------------------------------------------------------------------------- #
def test_a_snapshot_that_was_never_captured_is_not_a_decision(db):
    """Решать там нечего: картинки нет, сравнивать не с чем.

    Но и молчать нельзя — «ноль красных» при двух неснятых страницах это не
    хорошая новость, а отсутствие проверки.
    """
    run = _run(db, "r1")
    _cmp(db, run, "pay.png", verdict="error", error="net::ERR_CONNECTION_REFUSED")

    q = decisions.queue(db, "shop")
    assert q["counts"]["blocked"] == 1
    assert q["counts"]["causes"] == 0
    assert q["blocked"][0]["error"] == "net::ERR_CONNECTION_REFUSED"


def test_a_blocked_snapshot_without_a_reason_still_says_something(db):
    """Пустая строка рядом с именем читается как «непонятно, но не страшно»."""
    run = _run(db, "r1")
    _cmp(db, run, "pay.png", verdict="error")
    assert decisions.queue(db, "shop")["blocked"][0]["error"] == "not captured"


# --------------------------------------------------------------------------- #
#  Сводка
# --------------------------------------------------------------------------- #
def test_the_numbers_on_the_screen_add_up(db):
    """«117 из 142» рядом с 23 красными и 2 неснятыми обязано сходиться.

    Два разных правила счёта на одном экране дают строку, которую нечем
    объяснить, — и человек перестаёт верить всем числам сразу, а не одному.
    """
    run = _run(db, "r1")
    for i in range(6):
        _cmp(db, run, f"a/p{i}.png", regions=_shift())
    for i in range(2):
        _cmp(db, run, f"b/e{i}.png", verdict="error", error="boom")
    for i in range(9):
        _cmp(db, run, f"c/ok{i}.png", verdict="pass", severity=0)

    c = decisions.queue(db, "shop")["counts"]
    assert c["total"] == 17
    assert c["clean"] == 9
    assert c["snapshots"] + c["blocked"] + c["clean"] == c["total"]


def test_a_new_baseline_counts_as_clean(db):
    """Новый эталон — не падение и ответа не требует.

    Он уже записан; вопрос «принять или нет» для него не стоял.
    """
    run = _run(db, "r1")
    _cmp(db, run, "fresh.png", verdict="new_baseline", severity=0)
    c = decisions.queue(db, "shop")["counts"]
    assert c["clean"] == 1 and c["snapshots"] == 0


def test_an_empty_queue_is_a_normal_answer(db):
    """Ни одного прогона — это не ошибка, а первый день работы."""
    q = decisions.queue(db, "shop")
    assert q["counts"] == {"causes": 0, "snapshots": 0, "blocked": 0,
                           "clean": 0, "total": 0}
    assert q["run"] is None


def test_the_queue_names_the_run_it_is_looking_at(db):
    """Без прогона очередь висит в воздухе: непонятно, насколько она свежая."""
    _run(db, "old", started="2026-01-01 10:00:00")
    _run(db, "new", started="2026-01-02 10:00:00")
    assert decisions.queue(db, "shop")["run"]["key"] == "new"


# --------------------------------------------------------------------------- #
#  Что написано на карточке причины
# --------------------------------------------------------------------------- #
def test_a_cause_carries_the_comparisons_it_will_close(db):
    """«Принять все 11» — это массовое решение, и оно должно знать, по чему.

    Собирать список на клиенте по именам значило бы, что кнопка закрывает не то
    же самое, что показала карточка.
    """
    run = _run(db, "r1")
    ids = [_cmp(db, run, f"a/p{i}.png", regions=_shift()) for i in range(4)]
    cause = decisions.queue(db, "shop")["causes"][0]
    assert sorted(cause["comparisons"]) == sorted(ids)
    assert cause["open"] in ids


def test_a_cause_says_where_it_showed_up(db):
    """Двадцать три имени никто не читает; «checkout ×4» отвечает сразу."""
    run = _run(db, "r1")
    for i in range(4):
        _cmp(db, run, f"checkout/p{i}.png", regions=_shift())
    for i in range(2):
        _cmp(db, run, f"cart/p{i}.png", regions=_shift())

    chips = decisions.queue(db, "shop")["causes"][0]["chips"]
    assert chips[0] == "checkout ×4"
    assert "cart ×2" in chips


def test_a_cause_says_what_accepting_costs(db):
    """«Принять» здесь переписывает эталоны — сколько именно, надо знать до."""
    run = _run(db, "r1", branch="feature/x")
    for i in range(3):
        _cmp(db, run, f"a/p{i}.png", regions=_shift())
    cause = decisions.queue(db, "shop")["causes"][0]
    assert "3 baselines" in cause["risk"]
    assert "feature/x" in cause["risk"]


def test_the_heaviest_cause_comes_first(db):
    """Список читают сверху и до первого отвлечения."""
    run = _run(db, "r1")
    for i in range(2):
        _cmp(db, run, f"small/p{i}.png", regions=[
            {"kind": "color", "selector": ".a > .b", "dy": 0}])
    for i in range(7):
        _cmp(db, run, f"big/p{i}.png", regions=_shift())

    counts = [c["count"] for c in decisions.queue(db, "shop")["causes"]]
    assert counts == sorted(counts, reverse=True)
    assert counts[0] == 7
