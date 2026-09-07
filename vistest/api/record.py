# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Recording baselines with the mouse — launched from the interface.

`vistest record <url>` opens a browser with a panel in which a person captures
pages by clicking, and produces baselines and a readable test as output. This is
the main entry point for QA without Python, and until now it was available only
from the command line — that is, to exactly the people who need it least.

Hence the button. But it has a quirk that must be understood: **the browser
will open on the service machine, not on the machine of whoever pressed it**.
Recording with the mouse is possible only where the service itself runs.

For the usual scenario (the service on an engineer's machine) this is exactly
what is needed. For an installation reachable by the team over the network it is
pointless and therefore forbidden by default — like everything else that starts
processes.

The process lives until a person presses «Done» in the browser panel. That is
why the task is a background one, and cancellation sends a termination signal
rather than killing it: killing the recorder mid-snapshot is a sure way to get a
half-written PNG instead of a baseline.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from urllib.parse import urlparse

from fastapi import APIRouter, Body, HTTPException, Request

from ..config import VisTestConfig
from . import net
from .jobs import runner

router = APIRouter()

#  «Локально» живёт в `api/net.py`: считается по адресу соединения,
#  который заголовком не подделать.

# One recorder at a time: two browsers with panels writing into a single store
# would only lead to confusion.
_LOCK = threading.Lock()
_ACTIVE: dict = {"proc": None}


def _guard(request: Request) -> None:
    # Recording opens a browser and writes into the baseline store — this is an
    # administrator action, and only from where it is allowed.
    from .auth import require
    from .main import db

    require(db, request.cookies.get("vistest_session"), "admin")

    mode = (os.getenv("VISTEST_RECORD_UI") or "local").strip().lower()
    if mode == "off":
        raise HTTPException(403, "Recording from the interface is turned off "
                                 "(VISTEST_RECORD_UI=off)")
    if mode == "all":
        return
    if not net.is_loopback(request):
        raise HTTPException(
            403,
            "Recording with the mouse is possible only from the machine where "
            "the service runs: the browser will open there, not on yours "
            f"(request from {net.client_ip(request)}). "
            "To allow this deliberately: VISTEST_RECORD_UI=all",
        )


@router.get("/api/record/status")
def record_status(request: Request):
    """Whether recording is in progress and whether it can be started.

    vnc_port != "" means the service is running with a virtual display and a
    recording window (noVNC): the browser opens on the service machine, and the
    team interacts with it from their own browser over this port. This way
    interactive recording with the mouse works even when the service is a
    container on a VM without a screen.
    """
    _guard(request)
    job = runner.active("record")
    vnc_port = (os.getenv("VISTEST_VNC_PORT") or "").strip()
    return {
        "running": job is not None,
        "job_id": job.id if job else None,
        "vnc_port": vnc_port,
        "hint": ("The browser will open on the service machine. Interact with it "
                 "in the recording window (button below), capture pages with the "
                 "panel in the bottom-right corner, and at the end press «Done»."),
    }


@router.post("/api/record/start")
def record_start(request: Request, payload: dict = Body(...)):
    _guard(request)

    url = (payload.get("url") or "").strip()
    cdp = (payload.get("cdp") or "").strip()
    if not url and not cdp:
        raise HTTPException(400, "Specify the page address")
    if url and urlparse(url).scheme not in ("http", "https", "file"):
        raise HTTPException(400, "The address must start with http:// or https://")

    if runner.active("record"):
        raise HTTPException(409, "Recording is already in progress — finish it in the browser")

    cfg = VisTestConfig.load()
    cmd = [sys.executable, "-m", "vistest.cli", "record"]
    if url:
        cmd.append(url)
    if cdp:
        cmd += ["--cdp", cdp]
    if payload.get("viewport"):
        cmd += ["--viewport", str(payload["viewport"])]
    if payload.get("browser"):
        cmd += ["--browser", str(payload["browser"])]
    if payload.get("project"):
        cmd += ["--project", str(payload["project"])]
    if payload.get("out"):
        cmd += ["--out", str(payload["out"])]
    if payload.get("pom"):
        cmd.append("--pom")
    if payload.get("no_code"):
        cmd.append("--no-code")
    if payload.get("shots"):
        cmd += ["--shots", str(int(payload["shots"]))]

    env = {**os.environ,
           "VISTEST_ROOT": str(cfg.root_path),
           "PYTHONUNBUFFERED": "1",
           "PYTHONIOENCODING": "utf-8"}

    def work(job):
        job.say("The browser will open on the service machine.")
        job.say("Capture pages with the panel in the bottom-right corner; "
                "at the end press «Done» — then the tests will be generated.")
        job.say("$ " + " ".join(cmd))

        with _LOCK:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                env=env, cwd=str(cfg.root_path.parent))
            _ACTIVE["proc"] = proc

        # The recorder stays silent in the log while the person clicks with the
        # mouse, and the loop below `for raw in proc.stdout` wakes up only on a
        # new line — so the «Stop» button would never reach an idle process. A
        # separate watcher waits for the cancel signal and terminates the
        # process itself, without waiting for output.
        def _watch() -> None:
            job._cancel.wait()
            if proc.poll() is None:
                job.say("stopping the recording on an external signal")
                try:
                    proc.terminate()
                except Exception:
                    pass
        threading.Thread(target=_watch, name="vistest-record-stop",
                         daemon=True).start()

        lines: list[str] = []
        try:
            assert proc.stdout is not None
            for raw in proc.stdout:
                line = raw.rstrip()
                lines.append(line)
                job.say(line)
                if job.cancelled:
                    job.say("stopping the recording — finishing the current snapshot")
                    proc.terminate()
                    break
            proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            proc.kill()
            job.say("the recorder did not finish within a minute — forcibly terminated",
                    "warn")
        finally:
            with _LOCK:
                _ACTIVE["proc"] = None

        return {"exit_code": proc.returncode, "output": lines[-200:]}

    job = runner.submit("record", "Recording baselines with the mouse", work)
    return {"job_id": job.id}


@router.post("/api/record/stop")
def record_stop(request: Request):
    """Finish recording from the outside.

    The usual path is the «Done» button in the panel itself: then the recorder
    will assemble the tests. This command is needed for the case where the
    browser was closed but the process remained.
    """
    _guard(request)
    job = runner.active("record")
    if not job:
        raise HTTPException(404, "Recording is not in progress")
    runner.cancel(job.id)
    return {"ok": True,
            "hint": "If the panel is still open, it is better to press «Done» in it — "
                    "then the tests will be generated."}
