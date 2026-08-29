# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Control-plane: операторская панель для изолированных инстансов VisTest.

Не заменяет управление командой внутри инстанса — это делает сам VisTest
(раздел «Команда»). Здесь операции НАД инстансами: список, здоровье, создать,
обновить, снять, пересобрать прокси. Вся логика провижининга — в
scripts/vistest_team.py, панель лишь запускает его, поэтому поведение из CLI и
из панели одинаковое.

Запуск (на хосте, где стоит docker — панели нужен доступ к нему):

    VISTEST_OP_TOKEN=$(openssl rand -hex 16) \
      uvicorn control.app:app --host 127.0.0.1 --port 8500

Доступ — по операторскому токену. Это привилегированная вещь (создаёт и сносит
инстансы): держите за TLS-прокси, на localhost, не светите в открытую сеть.
"""

from __future__ import annotations

import os
import secrets
import subprocess
import threading
import urllib.request
import uuid
from pathlib import Path

from fastapi import Body, Cookie, FastAPI, HTTPException, Response
from fastapi.responses import FileResponse

REPO = Path(os.getenv("VISTEST_REPO") or Path(__file__).resolve().parents[1])
DEPLOY = REPO / "deploy"
TEAMS = DEPLOY / "teams"
SCRIPT = REPO / "scripts" / "vistest_team.py"
HERE = Path(__file__).resolve().parent

OP_TOKEN = os.getenv("VISTEST_OP_TOKEN", "")
COOKIE = "vistest_op"

app = FastAPI(title="VisTest Control")


# --------------------------------------------------------------------------- #
#  Доступ оператора
# --------------------------------------------------------------------------- #
def op_ok(token: str | None) -> bool:
    return bool(OP_TOKEN) and secrets.compare_digest(token or "", OP_TOKEN)


def require_op(token: str | None) -> None:
    if not op_ok(token):
        raise HTTPException(401, "An operator token is required")


@app.get("/api/control/me")
def me(vistest_op: str | None = Cookie(default=None)):
    return {"authed": op_ok(vistest_op), "configured": bool(OP_TOKEN)}


@app.post("/api/control/login")
def login(response: Response, payload: dict = Body(...)):
    if not OP_TOKEN:
        raise HTTPException(400, "VISTEST_OP_TOKEN is not set on the server")
    if not op_ok(payload.get("token")):
        raise HTTPException(401, "Wrong token")
    response.set_cookie(COOKIE, OP_TOKEN, httponly=True, samesite="lax",
                        max_age=7 * 86400)
    return {"ok": True}


@app.post("/api/control/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE)
    return {"ok": True}


# --------------------------------------------------------------------------- #
#  Реестр и здоровье
# --------------------------------------------------------------------------- #
def read_env(path: Path) -> dict:
    d: dict[str, str] = {}
    if path.exists():
        for line in path.read_text("utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                d[k.strip()] = v.strip()
    return d


def registry() -> list[dict]:
    out = []
    if TEAMS.exists():
        for f in sorted(TEAMS.glob("*.env")):
            d = read_env(f)
            if d.get("TEAM"):
                out.append(d)
    return out


def is_healthy(port: str) -> bool:
    if not port:
        return False
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/health", timeout=1.5) as r:
            return r.status == 200
    except Exception:
        return False


def container_status(team: str) -> str:
    try:
        out = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Status}}", f"vistest-{team}"],
            capture_output=True, text=True, check=True)
        return out.stdout.strip()
    except Exception:
        return "not running"


@app.get("/api/control/teams")
def list_teams(vistest_op: str | None = Cookie(default=None)):
    require_op(vistest_op)
    res = []
    for d in registry():
        port = d.get("PORT", "")
        res.append({
            "team": d["TEAM"], "port": port, "domain": d.get("DOMAIN", ""),
            "full": d.get("FULL") == "1",
            "status": container_status(d["TEAM"]),
            "healthy": is_healthy(port),
        })
    return {"teams": res}


# --------------------------------------------------------------------------- #
#  Задачи (create/upgrade/remove/proxy) — фоном, с логом
# --------------------------------------------------------------------------- #
_JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()


def _py() -> str:
    return os.getenv("PYTHON") or "python3"


def run_job(title: str, cmd: list[str]) -> str:
    jid = uuid.uuid4().hex[:12]
    job = {"id": jid, "title": title, "status": "running", "log": [], "rc": None}
    with _LOCK:
        _JOBS[jid] = job
        for old in list(_JOBS.values())[:-30]:
            if old["status"] != "running":
                _JOBS.pop(old["id"], None)

    def work() -> None:
        try:
            p = subprocess.Popen(
                cmd, cwd=str(REPO), stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, bufsize=1)
            assert p.stdout is not None
            for line in p.stdout:
                job["log"].append(line.rstrip())
                del job["log"][:-500]
            job["rc"] = p.wait()
            job["status"] = "done" if job["rc"] == 0 else "failed"
        except Exception as e:  # noqa: BLE001
            job["log"].append(str(e))
            job["status"] = "failed"
            job["rc"] = 1

    threading.Thread(target=work, name="control-" + jid, daemon=True).start()
    return jid


@app.get("/api/control/jobs/{jid}")
def job_get(jid: str, since: int = 0,
            vistest_op: str | None = Cookie(default=None)):
    require_op(vistest_op)
    j = _JOBS.get(jid)
    if not j:
        raise HTTPException(404, "job not found")
    tail = j["log"][since:]
    return {"id": jid, "title": j["title"], "status": j["status"],
            "rc": j["rc"], "log": tail, "log_offset": since + len(tail)}


@app.post("/api/control/teams")
def create_team(payload: dict = Body(...),
                vistest_op: str | None = Cookie(default=None)):
    require_op(vistest_op)
    team = (payload.get("team") or "").strip().lower()
    if not team:
        raise HTTPException(400, "Team name is not set")
    cmd = [_py(), str(SCRIPT), "create", team]
    if payload.get("admin"):
        cmd += ["--admin", str(payload["admin"])]
    if payload.get("domain"):
        cmd += ["--domain", str(payload["domain"])]
    if payload.get("full"):
        cmd += ["--full"]
    if payload.get("force"):
        cmd += ["--force"]
    return {"job_id": run_job("Creating team " + team, cmd)}


@app.post("/api/control/teams/{team}/upgrade")
def upgrade_team(team: str, vistest_op: str | None = Cookie(default=None)):
    require_op(vistest_op)
    return {"job_id": run_job("Upgrading " + team,
                              [_py(), str(SCRIPT), "upgrade", team])}


@app.delete("/api/control/teams/{team}")
def remove_team(team: str, purge: bool = False,
                vistest_op: str | None = Cookie(default=None)):
    require_op(vistest_op)
    cmd = [_py(), str(SCRIPT), "remove", team] + (["--purge"] if purge else [])
    return {"job_id": run_job("Taking down " + team, cmd)}


@app.post("/api/control/proxy")
def rebuild_proxy(payload: dict = Body(default={}),
                  vistest_op: str | None = Cookie(default=None)):
    require_op(vistest_op)
    cmd = [_py(), str(SCRIPT), "proxy"]
    if payload.get("domain"):
        cmd += ["--domain", str(payload["domain"])]
    return {"job_id": run_job("Rebuilding the proxy", cmd)}


# --------------------------------------------------------------------------- #
#  UI
# --------------------------------------------------------------------------- #
@app.get("/")
@app.get("/control/")
def ui():
    return FileResponse(str(HERE / "index.html"))
