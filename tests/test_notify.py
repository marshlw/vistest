# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Дайджест непросмотренных падений.

Единственное, что уходит из VisTest наружу само. Всё остальное работает через
«человек придёт и посмотрит», и это верно ровно до первого дня, когда он не
пришёл: двенадцать падений лежат третьи сутки, эталон устаревает, следующий
прогон краснеет уже на них — и никто не знает, что смотреть надо было
позавчера.

Проверяется прежде всего **молчание**, а не отправка. Канал, который пишет
каждый день одно и то же, перестают читать — и в день, когда написать будет
что, его тоже не откроют. Поэтому здесь больше тестов на «не отправили», чем на
«отправили», и это не перекос.
"""

from __future__ import annotations

import pytest

from vistest.api import notify
from vistest.api.db import Database


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "vistest.db")


@pytest.fixture
def sink(monkeypatch):
    """Перехваченный вебхук: сеть в тестах не трогаем."""
    sent: list[tuple[str, dict]] = []
    monkeypatch.setattr(notify, "_post",
                        lambda url, payload: sent.append((url, payload)))
    return sent


def _ingest(db, *, run_key="r1", names=("login.png",), age_hours=48.0,
            branch="main", project="demo", verdict="fail"):
    run_id = db.ingest_run({
        "run_id": run_key, "platform": "linux-chromium-1x", "browser": "chromium",
        "created_at": "2026-01-01T00:00:00",
        "git": {"branch": branch, "sha": "abc1234"},
        "totals": {"total": len(names), "failed": len(names)},
        "comparisons": [
            {"name": n, "verdict": verdict,
             "metrics": {"max_severity": 44.0, "changed_area_pct": 0.4}}
            for n in names
        ],
    }, project)
    # Возраст сравнения — часть сценария: «ждёт третьи сутки» иначе не выразить.
    db.execute("UPDATE comparison SET created_at=datetime('now', ?)"
               " WHERE run_id=?", (f"-{age_hours} hours", run_id))
    return run_id


def _on(db, url="https://chat.example/hook"):
    notify.configure(db, {"enabled": True, "webhook": url}, "anna")


# --------------------------------------------------------------------------- #
#  Что считается ждущим разбора
# --------------------------------------------------------------------------- #
def test_a_fresh_failure_is_not_yet_a_debt(db):
    """Разбор в тот же день — это нормальная работа, а не долг.

    Написать про падение через минуту после прогона значит написать человеку,
    который в эту же минуту на него и смотрит.
    """
    _ingest(db, age_hours=1.0)
    assert notify.pending(db) == []


def test_an_old_unreviewed_failure_is(db):
    _ingest(db, age_hours=48.0)
    items = notify.pending(db)
    assert [i["name"] for i in items] == ["login.png"]


def test_a_reviewed_failure_is_not_waiting_for_anyone(db):
    _ingest(db, age_hours=48.0)
    db.execute("UPDATE comparison SET review='approved', reviewed_by='anna'")
    assert notify.pending(db) == []


def test_a_passing_snapshot_is_not_waiting(db):
    _ingest(db, age_hours=48.0, verdict="pass")
    assert notify.pending(db) == []


def test_one_snapshot_red_in_twenty_runs_is_one_question(db):
    """Двадцать прогонов подряд с одним и тем же красным `login.png` — это одно
    незакрытое решение, а не двадцать.

    Считать по сравнениям значило бы прислать двадцать строк об одном снимке и
    заставить человека складывать их обратно самому.
    """
    for i in range(20):
        _ingest(db, run_key=f"r{i}", age_hours=48.0)
    assert len(notify.pending(db)) == 1


def test_a_later_review_closes_the_question_retroactively(db):
    """Смотрим на ПОСЛЕДНЕЕ сравнение снимка, а не на любое.

    Иначе разобранное сегодня падение всё равно висело бы в долгах из-за своей
    вчерашней копии.
    """
    _ingest(db, run_key="old", age_hours=72.0)
    _ingest(db, run_key="new", age_hours=48.0)
    newest = db.one("SELECT id FROM comparison ORDER BY created_at DESC, id DESC"
                    " LIMIT 1")
    db.execute("UPDATE comparison SET review='approved' WHERE id=?",
               (newest["id"],))
    assert notify.pending(db) == []


def test_the_threshold_is_configurable(db):
    _ingest(db, age_hours=3.0)
    assert notify.pending(db) == []
    notify.configure(db, {"after_hours": 1}, "anna")
    assert len(notify.pending(db)) == 1


# --------------------------------------------------------------------------- #
#  Текст
# --------------------------------------------------------------------------- #
def test_the_first_line_says_how_long_the_oldest_has_waited(db):
    """«Третьи сутки» — то, ради чего сообщение и читают."""
    _ingest(db, names=("a.png", "b.png"), age_hours=72.0)
    text = notify.render_digest(notify.pending(db))
    assert text.startswith("VisTest: 2 unreviewed failures, the oldest waiting 3 days")


def test_one_failure_is_not_called_failures(db):
    _ingest(db, age_hours=48.0)
    assert "1 unreviewed failure," in notify.render_digest(notify.pending(db))


def test_the_message_is_cut_off_and_says_so(db):
    """Сотню строк в чате никто не прочитает, а молчаливая обрезка врёт."""
    _ingest(db, names=tuple(f"p{i}.png" for i in range(notify.MAX_ROWS + 4)),
            age_hours=48.0)
    text = notify.render_digest(notify.pending(db))
    named = [ln for ln in text.splitlines() if ln.startswith("• ") and "…" not in ln]
    assert len(named) == notify.MAX_ROWS
    assert "…and 4 more" in text


def test_the_link_appears_only_with_a_public_url(db):
    _ingest(db, age_hours=48.0)
    items = notify.pending(db)
    assert "http" not in notify.render_digest(items)
    assert "https://vt.local/ui/#/runs" in notify.render_digest(
        items, base_url="https://vt.local/")


# --------------------------------------------------------------------------- #
#  Молчание
# --------------------------------------------------------------------------- #
def test_nothing_waiting_means_nothing_is_sent(db, sink):
    """Ежедневное «ожидает 0» приучает не открывать канал."""
    _on(db)
    result = notify.run_once(db)
    assert result["sent"] is False
    assert result["reason"] == "nothing is waiting"
    assert sink == []


def test_the_same_list_is_not_repeated(db, sink):
    """Один и тот же перечень каждые сутки превращается в фон."""
    _ingest(db, age_hours=48.0)
    _on(db)
    assert notify.run_once(db)["sent"] is True
    assert notify.run_once(db)["sent"] is False
    assert len(sink) == 1


def test_a_new_failure_breaks_the_silence(db, sink):
    _ingest(db, run_key="r1", age_hours=48.0)
    _on(db)
    notify.run_once(db)
    _ingest(db, run_key="r2", names=("cart.png",), age_hours=48.0)
    assert notify.run_once(db)["sent"] is True
    assert len(sink) == 2


def test_switched_off_means_switched_off(db, sink):
    _ingest(db, age_hours=48.0)
    notify.configure(db, {"enabled": False,
                          "webhook": "https://chat.example/hook"}, "anna")
    assert notify.run_once(db)["reason"] == "notifications are off"
    assert sink == []


def test_without_a_webhook_there_is_nowhere_to_send(db, sink):
    _ingest(db, age_hours=48.0)
    notify.configure(db, {"enabled": True}, "anna")
    assert notify.run_once(db)["reason"] == "no webhook configured"


def test_force_still_refuses_to_send_an_empty_digest(db, sink):
    """Кнопка «отправить сейчас» не должна учить канал говорить ни о чём."""
    _on(db)
    assert notify.run_once(db, force=True)["sent"] is False
    assert sink == []


def test_force_ignores_the_unchanged_list(db, sink):
    """Проверить настроенный вебхук надо уметь, не дожидаясь новых падений."""
    _ingest(db, age_hours=48.0)
    _on(db)
    notify.run_once(db)
    assert notify.run_once(db, force=True)["sent"] is True
    assert len(sink) == 2


def test_a_dry_run_shows_the_text_and_sends_nothing(db, sink):
    _ingest(db, age_hours=48.0)
    _on(db)
    result = notify.run_once(db, dry_run=True)
    assert result["sent"] is False
    assert "login.png" in result["text"]
    assert sink == []


def test_a_dry_run_does_not_count_as_having_told_anyone(db, sink):
    """Иначе первый настоящий дайджест был бы съеден как «список тот же»."""
    _ingest(db, age_hours=48.0)
    _on(db)
    notify.run_once(db, dry_run=True)
    assert notify.run_once(db)["sent"] is True


# --------------------------------------------------------------------------- #
#  Отправка и её отказы
# --------------------------------------------------------------------------- #
def test_the_payload_carries_both_text_and_structure(db, sink):
    """`{"text": ...}` понимают Slack и Mattermost как есть; остальным — поля."""
    _ingest(db, age_hours=48.0)
    _on(db)
    notify.run_once(db)
    url, payload = sink[0]
    assert url == "https://chat.example/hook"
    assert "login.png" in payload["text"]
    assert payload["count"] == 1
    assert payload["snapshots"] == ["login.png"]


def test_a_broken_webhook_leaves_its_reason_visible(db, monkeypatch):
    """Тихо сломавшийся вебхук хуже отсутствующего.

    Канал молчит, и тишина читается как «всё разобрано».
    """
    _ingest(db, age_hours=48.0)
    _on(db)

    def boom(url, payload):
        raise OSError("connection refused")

    monkeypatch.setattr(notify, "_post", boom)
    result = notify.run_once(db)
    assert result["sent"] is False
    assert "connection refused" in result["error"]
    assert "connection refused" in notify.settings(db)["last_error"]


def test_a_failed_send_is_retried_next_time(db, monkeypatch, sink):
    """Неудачная попытка не должна засчитываться как «уже рассказали»."""
    _ingest(db, age_hours=48.0)
    _on(db)
    monkeypatch.setattr(notify, "_post", lambda u, p: (_ for _ in ()).throw(
        OSError("down")))
    notify.run_once(db)

    monkeypatch.setattr(notify, "_post",
                        lambda url, payload: sink.append((url, payload)))
    assert notify.run_once(db)["sent"] is True


def test_a_new_webhook_does_not_inherit_the_old_ones_error(db, monkeypatch):
    _ingest(db, age_hours=48.0)
    _on(db)
    monkeypatch.setattr(notify, "_post", lambda u, p: (_ for _ in ()).throw(
        OSError("down")))
    notify.run_once(db)
    assert notify.settings(db)["last_error"]

    notify.configure(db, {"webhook": "https://other.example/hook"}, "anna")
    assert notify.settings(db)["last_error"] == ""


def test_two_replicas_starting_from_scratch_and_only_one_wins(db):
    """Строки ещё нет — `INSERT OR IGNORE` играет роль сравнения-и-обмена.

    Три реплики за общим томом стартуют одинаково пустыми; если бы каждая
    просто записала отметку, все три отправили бы одно и то же сообщение.
    """
    assert notify._claim(db, "", "replica-a") is True
    assert notify._claim(db, "", "replica-b") is False


def test_a_replica_that_read_a_stale_mark_does_not_send(db):
    """Обменять можно только то, что прочитал.

    Проигравшая гонку реплика держит в руках отметку, которой в базе уже нет, —
    и обмен ей не удаётся.
    """
    notify._claim(db, "", "first")
    stale = "first"
    assert notify._claim(db, stale, "second") is True    # честный обмен
    assert notify._claim(db, stale, "third") is False    # опоздавшая реплика


def test_the_loser_of_the_race_stays_quiet(db, sink):
    """Дайджест собран, но право на отправку ушло другому — молчим."""
    _ingest(db, age_hours=48.0)
    _on(db)
    notify.set_text(db, notify.LAST_SENT, "someone-elses-mark", "other")

    real_claim = notify._claim
    notify._claim = lambda db_, previous, stamp: False
    try:
        result = notify.run_once(db)
    finally:
        notify._claim = real_claim

    assert result["sent"] is False
    assert result["reason"] == "another replica is sending"
    assert sink == []


# --------------------------------------------------------------------------- #
#  Настройки
# --------------------------------------------------------------------------- #
def test_a_webhook_must_be_a_url(db):
    """`chat.example/hook` без схемы — самая частая опечатка, и она молчит."""
    with pytest.raises(ValueError):
        notify.configure(db, {"webhook": "chat.example/hook"}, "anna")


def test_an_empty_webhook_clears_it(db):
    _on(db)
    notify.configure(db, {"webhook": ""}, "anna")
    assert notify.settings(db)["webhook"] == ""


def test_a_negative_threshold_is_refused(db):
    with pytest.raises(ValueError):
        notify.configure(db, {"after_hours": -1}, "anna")


def test_a_missing_key_is_left_alone(db):
    _on(db)
    notify.configure(db, {"after_hours": 6}, "anna")
    current = notify.settings(db)
    assert current["enabled"] is True
    assert current["webhook"] == "https://chat.example/hook"
    assert current["after_hours"] == 6.0


def test_a_corrupt_threshold_falls_back_to_the_default(db):
    """Мусор в настройке не должен превращаться в «слать всё подряд»."""
    notify.set_text(db, notify.AFTER_HOURS, "не число", "test")
    assert notify.settings(db)["after_hours"] == notify.DEFAULT_AFTER_HOURS


# --------------------------------------------------------------------------- #
#  Часы
# --------------------------------------------------------------------------- #
def test_the_ticker_is_one_thread_per_process(db):
    """Перезагрузка модуля сервиса не должна оставлять за собой по потоку."""
    first = notify.ensure_ticker(db)
    second = notify.ensure_ticker(db)
    assert first is second


def test_the_ticker_follows_the_current_database(db, tmp_path):
    """Замкнуть базу внутри потока значило бы ходить вокруг первой попавшейся."""
    notify.ensure_ticker(db)
    other = Database(tmp_path / "second.db")
    notify.ensure_ticker(other, base_url="https://vt.local")
    assert notify._target["db"] is other
    assert notify._target["base_url"] == "https://vt.local"
