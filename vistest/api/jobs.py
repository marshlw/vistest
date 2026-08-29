# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Background jobs with a live log.

Capturing a baseline by URL or running a suite of checks takes tens of seconds,
and holding an HTTP connection open for that is wrong. A job runs in a thread;
the UI polls its status and shows the log as it arrives.

The live view is deliberately plain: a dictionary in memory. Alongside it every
job is mirrored into the database — see «Surviving a restart» below.

**About several people working at once.** A second job of the same kind used to
be rejected outright: the assumption was one engineer per installation, so if
they were busy, the whole service was busy. On a shared server that is wrong —
a run of one project does not disturb a run of another. What must not happen is
two runs of the *same* project at once: they write into the same baselines and
the same directory.

So instead of a global «busy» there is a **serialization key**. Jobs with
different keys run in parallel, jobs with the same key queue up. Plus an
overall concurrency limit, so ten people cannot flatten the machine.

**Surviving a restart.** A job used to live only in this process's memory, and
that was honest exactly until the first restart. The scenario: someone pressed
«Run», went for coffee, the image was updated meanwhile — and the job *vanished*.
Not «failed», not «interrupted»: it stopped existing. Polling got a 404 and the
interface said «connection to the task lost», blaming the network for something
the network had nothing to do with.

Resurrecting a job is neither possible nor desirable: inside it runs someone
else's pytest or a browser, and once the process is dead there is nothing left
to continue. What is needed is the opposite — that it *does not disappear*: it
stays in the list, gets an honest status, and keeps the tail of its log, because
the cause is written there.

Hence the mirror in the database and `heartbeat_at`. The process that owns a job
refreshes the mark while the job runs; on startup the service marks as
interrupted only those jobs whose mark has gone stale. Anything simpler is
wrong: reaping every unfinished row would mean a second worker killing the first
worker's live jobs, and checking by PID is not portable — on Windows
`os.kill(pid, 0)` does not ask a process whether it is alive, it terminates it.

Every string here reaches a person: it lands in the job log the interface
shows. That is why they are worded, not abbreviated.
"""

from __future__ import annotations

import json
import os
import threading
import time
import traceback
import uuid
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


class JobFailure(Exception):
    """The job failed for a reason that has already been explained.

    Different from any other exception in that the cause is already spelled out
    in the job log, in words. For such a case a Python traceback is not
    diagnostics but noise: it repeats the same thing a third time and looks like
    the tool broke, in the one place where the tool worked exactly right.
    """


def _dump(value) -> str | None:
    """JSON for a column, or nothing at all.

    A result that will not serialize must not take the job's row down with it:
    the row exists so that a person can find out what happened, and «what
    happened» is mostly the status and the log, not the payload.
    """
    if value is None:
        return None
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return None


def _load(text, default=None):
    if not text:
        return default
    try:
        return json.loads(text)
    except Exception:
        return default


MAX_JOBS = 50
# Concurrent jobs. Each one is a browser or someone else's pytest — a visible
# slice of the machine; ten parallel runs do not speed work up, they stop it.
MAX_PARALLEL = 3
# How many log lines we keep in memory per job.
LOG_WINDOW = 2000
# Ceiling per job. Without it a hung foreign pytest held the project's
# serialization key forever: the «Run» button answered «this project is already
# running» until the service was restarted, and there was no way to see why.
DEFAULT_TIMEOUT_S = int(os.getenv("VISTEST_JOB_TIMEOUT_S", "3600"))
# How long to wait in the queue before giving up. A job that has stood in line
# for an hour is almost certainly of no use to anyone any more.
QUEUE_TIMEOUT_S = int(os.getenv("VISTEST_QUEUE_TIMEOUT_S", "1800"))
# How often the owning process refreshes `heartbeat_at`, and how long a mark may
# be stale before another process may call the job interrupted. The gap between
# them is deliberate: one missed beat under load must not declare a live job
# dead, because that message is a lie the person cannot check.
HEARTBEAT_S = 5.0
STALE_S = float(os.getenv("VISTEST_JOB_STALE_S", "60"))
# How much of the log is kept in the database. The window in memory holds two
# thousand lines; storing all of them on every heartbeat would rewrite a
# megabyte every five seconds for a row nobody is reading. The tail is where the
# cause is written, and it is the tail that has to survive the restart.
PERSIST_LOG_LINES = 400


@dataclass
class Job:
    id: str
    kind: str
    title: str
    status: str = "queued"           # queued | running | done | failed | cancelled
    progress: float = 0.0            # 0..1
    log: list[dict] = field(default_factory=list)
    result: Any = None
    error: str | None = None
    owner: str = ""                  # who started it
    lock_key: str = ""               # what must not happen at the same time
    queued_at: float = field(default_factory=time.time)
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    # How many log lines have already been dropped from the window. The client
    # asks for the log by absolute offset («give me from line 137»), while the
    # window keeps only the last LOG_WINDOW lines. Without this counter the
    # offset started pointing to the wrong place after the very first trim, and
    # the run lost the middle of its output — which is exactly where the cause
    # is usually written.
    dropped: int = 0
    # When the job must be over. Needed by the watchdog and by the interface
    # alike: a person has to see that a run has a limit rather than hanging
    # forever.
    deadline: float | None = None
    _cancel: threading.Event = field(default_factory=threading.Event, repr=False)
    _log_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _watchdog: Any = field(default=None, repr=False)
    # Read back from the database rather than running here. Nothing stands
    # behind such a job — the process that owned it is gone — so it can be shown
    # but not cancelled.
    stored: bool = False

    def say(self, text: str, level: str = "info") -> None:
        with self._log_lock:
            self.log.append({"t": round(time.time() - self.started_at, 1),
                             "level": level, "text": text})
            extra = len(self.log) - LOG_WINDOW
            if extra > 0:            # the log must not grow without bound
                del self.log[:extra]
                self.dropped += extra

    def log_since(self, offset: int) -> tuple[list[dict], int]:
        """Lines from an absolute offset, plus the new offset."""
        with self._log_lock:
            start = max(0, min(len(self.log), offset - self.dropped))
            return list(self.log[start:]), self.dropped + len(self.log)

    @classmethod
    def from_row(cls, row: dict) -> Job:
        """A job as the database remembers it — read-only by nature.

        Nothing runs behind it: the thread that owned it is gone. It exists so
        that the person who started it can find out what happened, which is why
        `cancel()` on such a job refuses instead of pretending.
        """
        job = cls(id=row["id"], kind=row["kind"] or "", title=row["title"] or "",
                  status=row["status"] or "failed",
                  progress=float(row["progress"] or 0.0),
                  owner=row["owner"] or "", lock_key=row["lock_key"] or "",
                  queued_at=float(row["queued_at"] or 0.0),
                  started_at=float(row["started_at"] or row["queued_at"] or 0.0),
                  finished_at=(float(row["finished_at"])
                               if row["finished_at"] is not None else None))
        job.log = _load(row["log"], []) or []
        job.dropped = int(row["dropped"] or 0)
        job.result = _load(row["result"])
        job.error = row["error"]
        job.stored = True
        return job

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    @property
    def active(self) -> bool:
        return self.status in ("queued", "running")

    def to_dict(self) -> dict:
        # The log is read under the same lock it is written under: without
        # that, `list(self.log)` could collide with a window trim in another
        # thread.
        with self._log_lock:
            log = list(self.log)
        return {
            "id": self.id, "kind": self.kind, "title": self.title,
            "status": self.status, "progress": round(self.progress, 3),
            "log": log, "result": self.result, "error": self.error,
            "owner": self.owner, "lock_key": self.lock_key,
            "elapsed": round((self.finished_at or time.time()) - self.started_at, 1),
            "waited": round(max(0.0, self.started_at - self.queued_at), 1),
            "time_left": (round(self.deadline - time.time(), 1)
                          if self.deadline and self.active else None),
        }


class JobRunner:
    def __init__(self) -> None:
        self._jobs: OrderedDict[str, Job] = OrderedDict()
        self._lock = threading.Lock()
        # The database is optional on purpose. The runner is imported by the
        # CLI and by tests that have no service at all; making it a hard
        # dependency would mean a background job could not exist without one.
        self._db = None

    # ------------------------------------------------------------------ #
    #  The mirror in the database
    # ------------------------------------------------------------------ #
    def bind(self, db) -> None:
        """Attach storage and settle the fate of what the previous run left.

        Called once, when the service starts. Everything a database write can
        raise is caught here for one reason: an unwritable job row must not stop
        the service from starting. History is worth less than the service.
        """
        self._db = db
        try:
            self.reap(db)
        except Exception:
            pass

    def reap(self, db) -> int:
        """Mark as interrupted the jobs nobody is running any more.

        Only the stale ones: a fresh heartbeat means another process is running
        this job right now, and telling its owner it died would be a lie they
        cannot check against anything.
        """
        cutoff = time.time() - STALE_S
        rows = db.query(
            "SELECT id FROM job WHERE status IN ('queued','running')"
            " AND (heartbeat_at IS NULL OR heartbeat_at < ?)", (cutoff,))
        if not rows:
            return 0
        # The update goes by the ids just selected rather than repeating the
        # predicate. Written twice, the two copies decide who is dead
        # separately — and the day they disagree, the second one buries jobs the
        # first one had already cleared as alive.
        ids = [r["id"] for r in rows]
        db.execute(
            "UPDATE job SET status='failed', finished_at=?,"
            " error='the service restarted while this job was running —"
            " nothing is running now, start it again'"
            f" WHERE id IN ({','.join('?' * len(ids))})",
            (time.time(), *ids))
        return len(ids)

    def _persist(self, job: Job, *, beat: bool = False) -> None:
        db = self._db
        if db is None:
            return
        with job._log_lock:
            log = list(job.log[-PERSIST_LOG_LINES:])
            # The client asks for the log by absolute offset. Storing the tail
            # without saying how much was cut would make that offset point at
            # the wrong line after a restart — and the wrong line is worse than
            # a missing one, because nothing about it looks wrong.
            dropped = job.dropped + max(0, len(job.log) - PERSIST_LOG_LINES)
        try:
            db.execute(
                "INSERT INTO job(id,kind,title,status,progress,owner,lock_key,"
                " error,result,log,dropped,queued_at,started_at,finished_at,"
                " heartbeat_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(id) DO UPDATE SET"
                " status=excluded.status, progress=excluded.progress,"
                " error=excluded.error, result=excluded.result,"
                " log=excluded.log, dropped=excluded.dropped,"
                " started_at=excluded.started_at,"
                " finished_at=excluded.finished_at,"
                " heartbeat_at=excluded.heartbeat_at",
                (job.id, job.kind, job.title, job.status, job.progress,
                 job.owner, job.lock_key, job.error,
                 _dump(job.result), _dump(log), dropped,
                 job.queued_at, job.started_at, job.finished_at,
                 time.time() if (beat or job.active) else None))
        except Exception:
            # A job that cannot be written down is still a job that runs. The
            # mirror is for after the crash; losing it must not cause one.
            pass

    def _beat(self, job: Job) -> threading.Thread:
        """Refresh the mark while the job runs — that is the whole thread.

        It doubles as the periodic save: progress and the log tail reach the
        database as they change, so a restart keeps what was known a few seconds
        before it, not what was known at submit time.
        """
        def loop() -> None:
            while job.active:
                time.sleep(HEARTBEAT_S)
                if not job.active:
                    break
                self._persist(job, beat=True)

        thread = threading.Thread(target=loop, name=f"vistest-beat-{job.id}",
                                  daemon=True)
        thread.start()
        return thread

    def submit(self, kind: str, title: str, fn: Callable[[Job], Any],
               *, owner: str = "", lock_key: str | None = None,
               timeout: float | None = None) -> Job:
        """Queue a job.

        `lock_key` is what must not happen at the same time. It defaults to
        `kind`, i.e. the old «one run at a time» behaviour. For projects the
        project key is passed here: different projects then run in parallel
        while the same one queues up, because they write into the same
        baselines.
        """
        timeout = float(timeout or DEFAULT_TIMEOUT_S)
        job = Job(id=uuid.uuid4().hex[:12], kind=kind, title=title,
                  owner=owner, lock_key=lock_key or kind)
        with self._lock:
            self._jobs[job.id] = job
            while len(self._jobs) > MAX_JOBS:
                oldest = next(iter(self._jobs.values()))
                if oldest.status in ("queued", "running"):
                    break          # never evict a job that is still going
                self._jobs.popitem(last=False)
        self._persist(job)

        def wrapper() -> None:
            # Wait our turn: on the serialization key and on the overall limit.
            queue_deadline = time.time() + QUEUE_TIMEOUT_S
            while not job.cancelled and not self._claim(job):
                if time.time() > queue_deadline:
                    job.error = (
                        f"gave up after {QUEUE_TIMEOUT_S // 60} min in the queue "
                        f"— «{job.lock_key}» was busy the whole time")
                    job.say(job.error, "error")
                    job.status = "failed"
                    job.finished_at = time.time()
                    self._persist(job)
                    return
                time.sleep(0.25)

            if job.cancelled:
                job.status = "cancelled"
                job.finished_at = time.time()
                self._persist(job)
                return

            job.started_at = time.time()
            job.deadline = job.started_at + timeout
            self._beat(job)
            if job.started_at - job.queued_at > 1.0:
                job.say(f"waited in the queue: "
                        f"{job.started_at - job.queued_at:.0f}s", "warn")
            # The time watchdog. A job executes inside someone else's code (a
            # browser, a foreign pytest) and cannot be interrupted from the
            # outside — but the same cancellation flag that code already has to
            # check can be raised, and the person can be told what happened.
            self._arm_watchdog(job)
            try:
                job.result = fn(job)
                job.status = "cancelled" if job.cancelled else "done"
                job.progress = 1.0
            except JobFailure as e:
                # The cause is already in the log — repeating it and appending a
                # traceback would drown the diagnosis in itself.
                job.error = str(e)
                job.status = "failed"
            except Exception as e:
                # The status is set last, after the log is written: the client
                # polls the status and stops reading the log once it sees
                # «failed». It used to manage that between the status change and
                # the cause being recorded — and showed a failure with no
                # explanation.
                job.error = f"{type(e).__name__}: {e}"
                job.say(job.error, "error")
                job.say(traceback.format_exc()[-1500:], "debug")
                job.status = "failed"
            finally:
                job.finished_at = time.time()
                job.deadline = None
                # The last word, and the only one that matters after a crash:
                # the row stops being «running» and stops being reaped.
                self._persist(job)

        threading.Thread(target=wrapper, name=f"vistest-{kind}", daemon=True).start()
        return job

    def _arm_watchdog(self, job: Job) -> None:
        """Arm a cancellation for when the limit runs out.

        A cancellation, not killing the thread: interrupting someone else's code
        at an arbitrary instruction leaves half-written files and a
        half-dead browser behind. The `cancelled` flag is already checked in
        `_spawn` and in the loops over snapshots, so the job ends by itself and
        in a place that makes sense.
        """
        def fire():
            if job.active and not job.cancelled:
                job.say(f"time limit exceeded "
                        f"({DEFAULT_TIMEOUT_S // 60} min) — the job is being "
                        "cancelled so it stops holding the queue", "error")
                job._cancel.set()

        timer = threading.Timer(max(1.0, (job.deadline or 0) - time.time()), fire)
        timer.daemon = True
        timer.start()
        job._watchdog = timer

    def _claim(self, job: Job) -> bool:
        """Take a slot: is the key free and is there room under the limit.

        The check and the move to `running` happen under one lock. The status
        used to be set outside it, so two jobs with the same key waking at the
        same moment both saw the key free and started together. Two runs of one
        project write into the same baselines and the same directory —
        serialization by key exists precisely so that cannot happen.
        """
        with self._lock:
            running = [j for j in self._jobs.values() if j.status == "running"]
            if any(j.lock_key == job.lock_key for j in running):
                return False
            if len(running) >= MAX_PARALLEL:
                return False
            job.status = "running"
            return True

    def queued(self, lock_key: str | None = None) -> list[Job]:
        with self._lock:
            return [j for j in self._jobs.values()
                    if j.status == "queued"
                    and (lock_key is None or j.lock_key == lock_key)]

    def busy(self, lock_key: str) -> Job | None:
        """Who is holding this key right now — needed to explain the wait."""
        with self._lock:
            for j in self._jobs.values():
                if j.lock_key == lock_key and j.active:
                    return j
        return None

    def get(self, job_id: str) -> Job | None:
        """Memory first, then the database.

        The order matters: a job running right now knows more about itself than
        its last saved row — the mirror is written every few seconds, not on
        every line.
        """
        with self._lock:
            job = self._jobs.get(job_id)
        if job is not None:
            return job
        if self._db is None:
            return None
        try:
            row = self._db.one("SELECT * FROM job WHERE id=?", (job_id,))
        except Exception:
            return None
        return Job.from_row(dict(row)) if row else None

    def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        if not job or not job.active or job.stored:
            return False
        job._cancel.set()
        job.say("cancellation requested", "warn")
        return True

    def list(self, limit: int = 20) -> list[dict]:
        """This process's jobs plus what earlier ones left behind.

        Without the second half the list restarted with the service: yesterday's
        run, the one somebody wants to look at today, simply was not there.
        """
        with self._lock:
            jobs = list(self._jobs.values())[-limit:]
        items = [{k: v for k, v in j.to_dict().items() if k != "log"}
                 for j in reversed(jobs)]
        if self._db is None or len(items) >= limit:
            return items

        seen = {i["id"] for i in items}
        try:
            rows = self._db.query(
                "SELECT * FROM job ORDER BY COALESCE(queued_at,0) DESC LIMIT ?",
                (limit,))
        except Exception:
            return items
        for row in rows:
            if len(items) >= limit:
                break
            if row["id"] in seen:
                continue
            stored = Job.from_row(dict(row))
            items.append({k: v for k, v in stored.to_dict().items() if k != "log"})
        return items

    def active(self, kind: str | None = None) -> Job | None:
        """A running or waiting job of this kind.

        Waiting counts as active: from the point of view of the person who
        pressed the button, the job has been accepted even if it has not started
        yet.

        The snapshot of the dictionary is taken under the lock: without it a
        concurrent `submit()` could mutate the OrderedDict mid-iteration — a
        RuntimeError out of nowhere, and this method is called on hot paths
        (record/status, starting a project).
        """
        with self._lock:
            jobs = list(self._jobs.values())
        for j in reversed(jobs):
            if j.active and (kind is None or j.kind == kind):
                return j
        return None


runner = JobRunner()
