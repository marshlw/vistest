# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Фоновые задачи: очередь, сериализация по ключу и инкрементальный лог."""

from __future__ import annotations

import pathlib
import threading
import time

from vistest.api.jobs import LOG_WINDOW, JobFailure, JobRunner


def _wait(*jobs, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if all(j.status in ("done", "failed", "cancelled") for j in jobs):
            return
        time.sleep(0.02)
    raise AssertionError("задача не завершилась: "
                         + ", ".join(j.status for j in jobs))


def test_log_offset_survives_window_eviction():
    """Клиент дочитывает лог по абсолютному смещению, окно — ограничено.

    Раньше окно резали, а смещение оставляли абсолютным: после первой же
    обрезки интерфейс терял середину вывода — ровно ту, где написана причина.
    """
    runner = JobRunner()
    job = runner.submit("t", "spam", lambda j: [j.say(f"line {i}")
                                                for i in range(LOG_WINDOW + 500)])
    _wait(job)

    seen, offset = [], 0
    while True:
        chunk, offset = job.log_since(offset)
        if not chunk:
            break
        seen += [c["text"] for c in chunk]

    assert job.dropped == 500
    assert len(seen) == LOG_WINDOW
    assert seen[0] == "line 500" and seen[-1] == f"line {LOG_WINDOW + 499}"
    assert offset == LOG_WINDOW + 500


def test_explained_failure_carries_no_traceback():
    """Разобранная причина не нуждается в питоновском трейсбеке.

    Он повторял бы тот же текст третий раз и выглядел как поломка инструмента
    там, где инструмент как раз отработал правильно.
    """
    runner = JobRunner()

    def work(job):
        job.say("9 of 11 tests never started — the cause is above", "error")
        raise JobFailure("9 of 11 tests never started")

    job = runner.submit("t", "explained", work)
    _wait(job)

    assert job.status == "failed"
    assert job.error == "9 of 11 tests never started"   # без префикса класса
    assert not any(entry["level"] == "debug" for entry in job.log)
    assert sum("never started" in entry["text"] for entry in job.log) == 1


def test_unexpected_failure_still_gets_a_traceback():
    """Неожиданная поломка — наоборот, без трейсбека неразбираема."""
    runner = JobRunner()
    job = runner.submit("t", "boom", lambda j: 1 / 0)
    _wait(job)

    assert job.status == "failed"
    assert job.error.startswith("ZeroDivisionError")
    assert any(entry["level"] == "debug" for entry in job.log)


def test_one_lock_key_means_one_at_a_time():
    """Два прогона одного проекта пишут в одни эталоны — только по очереди."""
    runner = JobRunner()
    live, peak, guard = [], 0, threading.Lock()

    def work(job):
        nonlocal peak
        with guard:
            live.append(job.id)
            peak = max(peak, len(live))
        time.sleep(0.3)
        with guard:
            live.remove(job.id)

    a = runner.submit("p", "a", work, lock_key="project:x")
    b = runner.submit("p", "b", work, lock_key="project:x")
    _wait(a, b)

    assert peak == 1


def test_different_projects_run_in_parallel():
    runner = JobRunner()
    started = threading.Barrier(2, timeout=5)

    def work(job):
        started.wait()          # без параллельности здесь будет таймаут

    a = runner.submit("p", "a", work, lock_key="project:a")
    b = runner.submit("p", "b", work, lock_key="project:b")
    _wait(a, b)

    assert (a.status, b.status) == ("done", "done")


def test_queued_job_is_active_for_the_lock():
    runner = JobRunner()
    release = threading.Event()

    a = runner.submit("p", "a", lambda j: release.wait(5), lock_key="project:x")
    b = runner.submit("p", "b", lambda j: None, lock_key="project:x")

    assert runner.busy("project:x") is not None
    assert b.status == "queued"
    release.set()
    _wait(a, b)


# --------------------------------------------------------------------------- #
#  Задача переживает перезапуск
#
#  Задача жила только в памяти процесса, и это было честно ровно до первого
#  перезапуска. Дальше сценарий такой: человек нажал «Прогнать», ушёл пить чай,
#  в это время выкатили обновление образа — и задача **исчезла**. Не «упала», не
#  «прервана», а перестала существовать: опрос получал 404, а интерфейс писал
#  «connection to the task lost», то есть винил сеть в том, к чему сеть
#  отношения не имеет.
#
#  Воскресить её нельзя и не нужно: внутри крутится чужой pytest или браузер.
#  Нужно, чтобы она не пропадала бесследно.
# --------------------------------------------------------------------------- #
import pytest  # noqa: E402

from vistest.api.db import Database  # noqa: E402
from vistest.api.jobs import STALE_S, Job  # noqa: E402


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "vistest.db")


def _rows(db):
    return db.query("SELECT * FROM job")


def _settle(db, job, timeout=5.0):
    """Дождаться, пока поток задачи допишет свою строку."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if db.one("SELECT id FROM job WHERE id=? AND finished_at IS NOT NULL",
                  (job.id,)):
            return
        time.sleep(0.02)
    raise AssertionError("задача не доехала до базы")


def test_a_finished_job_is_written_down(db):
    runner = JobRunner()
    runner.bind(db)
    job = runner.submit("t", "done thing", lambda j: j.say("hello"))
    _wait(job)
    _settle(db, job)          # запись идёт из потока задачи

    row = db.one("SELECT * FROM job WHERE id=?", (job.id,))
    assert row["status"] == "done"
    assert row["title"] == "done thing"
    assert "hello" in row["log"]


def test_a_running_job_is_already_in_the_database(db):
    """Ради этого всё и написано.

    Строка появляется НЕ в конце: задача, убитая перезапуском, до конца не
    доходит никогда. Если писать только результат, прерванная задача — то есть
    ровно та, которую надо объяснить, — не оставит следа вообще.
    """
    runner = JobRunner()
    runner.bind(db)
    release = threading.Event()
    job = runner.submit("t", "long one", lambda j: release.wait(5))
    try:
        deadline = time.time() + 5
        row = None
        while time.time() < deadline and row is None:
            row = db.one("SELECT * FROM job WHERE id=?", (job.id,))
            time.sleep(0.02)
        assert row is not None, "идущая задача не попала в базу"
        assert row["title"] == "long one"
        assert row["finished_at"] is None
    finally:
        release.set()
    _wait(job)


def test_a_job_from_a_previous_process_is_still_findable(db):
    """Тот самый прогон, на который человек хочет посмотреть назавтра."""
    fresh = JobRunner()
    fresh.bind(db)
    db.execute(
        "INSERT INTO job(id,kind,title,status,queued_at,finished_at,log)"
        " VALUES('old','p','yesterday run','done',1,2,'[]')")

    job = fresh.get("old")
    assert job is not None
    assert job.title == "yesterday run"
    assert job.stored is True
    assert any(item["id"] == "old" for item in fresh.list())


def test_a_job_interrupted_by_a_restart_says_so(db):
    """Не «висит running», не пропадает — говорит, что произошло.

    «running» после перезапуска было бы враньём: за этой строкой не стоит ни
    одного живого потока, и человек ждал бы окончания, которое не наступит.
    """
    db.execute(
        "INSERT INTO job(id,kind,title,status,queued_at,heartbeat_at)"
        " VALUES('gone','p','interrupted','running',1,?)",
        (time.time() - STALE_S - 10,))

    runner = JobRunner()
    runner.bind(db)

    job = runner.get("gone")
    assert job.status == "failed"
    assert "restarted" in job.error
    assert "start it again" in job.error


def test_a_queued_job_is_reaped_too(db):
    """Очередь после перезапуска пуста: разбирать её некому."""
    db.execute("INSERT INTO job(id,kind,status,queued_at) VALUES('q','p','queued',1)")
    runner = JobRunner()
    runner.bind(db)
    assert runner.get("q").status == "failed"


def test_a_job_alive_in_another_process_is_not_touched(db):
    """Второй воркер, стартовав, не имеет права хоронить задачи первого.

    Проверять по PID нельзя: на Windows `os.kill(pid, 0)` не спрашивает, а
    завершает процесс. Поэтому отметка времени.
    """
    db.execute(
        "INSERT INTO job(id,kind,status,queued_at,heartbeat_at)"
        " VALUES('live','p','running',1,?)", (time.time(),))

    JobRunner().bind(db)
    assert db.one("SELECT status FROM job WHERE id='live'")["status"] == "running"


def test_reaping_leaves_finished_jobs_alone(db):
    db.execute("INSERT INTO job(id,kind,status,queued_at) VALUES('ok','p','done',1)")
    db.execute("INSERT INTO job(id,kind,status,queued_at,error)"
               " VALUES('bad','p','failed',1,'disk full')")
    JobRunner().bind(db)

    assert db.one("SELECT status FROM job WHERE id='ok'")["status"] == "done"
    assert db.one("SELECT error FROM job WHERE id='bad'")["error"] == "disk full"


def test_a_running_job_is_read_from_memory_not_from_the_row(db):
    """Идущая задача знает о себе больше, чем её последняя запись.

    Зеркало пишется раз в несколько секунд, а не на каждую строку.
    """
    runner = JobRunner()
    runner.bind(db)
    release = threading.Event()
    job = runner.submit("t", "live", lambda j: release.wait(5))
    try:
        time.sleep(0.2)
        job.say("a line the row has not seen yet")
        assert runner.get(job.id) is job
    finally:
        release.set()
    _wait(job)


def test_a_stored_job_cannot_be_cancelled(db):
    """За ней не стоит ни одного потока — «отменено» было бы враньём."""
    runner = JobRunner()
    runner.bind(db)
    db.execute("INSERT INTO job(id,kind,status,queued_at) VALUES('z','p','running',1)"
               )
    db.execute("UPDATE job SET heartbeat_at=? WHERE id='z'", (time.time(),))
    assert runner.cancel("z") is False


def test_the_stored_log_keeps_its_offset_honest(db):
    """Хвост лога без счётчика отрезанного сдвинул бы все смещения.

    Не та строка хуже отсутствующей: в ней ничто не выглядит неправильным.
    """
    from vistest.api.jobs import PERSIST_LOG_LINES

    runner = JobRunner()
    runner.bind(db)
    job = runner.submit("t", "spam", lambda j: [j.say(f"line {i}")
                                                for i in range(PERSIST_LOG_LINES + 50)])
    _wait(job)
    _settle(db, job)

    row = db.one("SELECT * FROM job WHERE id=?", (job.id,))
    stored = Job.from_row(dict(row))
    assert stored.dropped == 50
    chunk, offset = stored.log_since(0)
    assert chunk[0]["text"] == "line 50"
    assert offset == PERSIST_LOG_LINES + 50


def test_an_unusual_result_is_written_down_as_text(db):
    """Не JSON — не повод потерять его целиком: пишем как строку."""
    runner = JobRunner()
    runner.bind(db)
    job = runner.submit("t", "weird", lambda j: {"path": pathlib.Path("/tmp/x")})
    _wait(job)
    _settle(db, job)

    row = db.one("SELECT * FROM job WHERE id=?", (job.id,))
    assert row["status"] == "done"
    assert "/tmp/x" in row["result"]


def test_a_result_that_cannot_be_written_does_not_lose_the_job(db):
    """Ради неупаковываемого результата терять статус и лог незачем.

    Строка существует, чтобы человек узнал, что произошло, а «что произошло» —
    это статус и лог, а не полезная нагрузка.
    """
    runner = JobRunner()
    runner.bind(db)

    def work(job):
        job.say("worked")
        loop = {}
        loop["self"] = loop          # json так не умеет, и не должен
        return loop

    job = runner.submit("t", "weird", work)
    _wait(job)
    _settle(db, job)

    row = db.one("SELECT * FROM job WHERE id=?", (job.id,))
    assert row["status"] == "done"
    assert "worked" in row["log"]
    assert row["result"] is None


def test_a_runner_without_a_database_still_runs(db):
    """CLI и тесты поднимают задачи без сервиса — зеркало необязательно."""
    runner = JobRunner()
    job = runner.submit("t", "no db", lambda j: 42)
    _wait(job)
    assert job.result == 42
    assert runner.get(job.id) is job
