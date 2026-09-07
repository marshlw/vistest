# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""The licence: what it permits, what is being used, and how to install one.

Kept in its own module because the answer has two halves that live apart. The
key says what was bought; the database says what is being used. Neither is
useful alone — «10 projects» means nothing without «7 connected», and that is
exactly the number someone needs before a renewal conversation.

Enforcement lives here too, as one function the rest of the service calls. Not
because it is elegant, but because a limit checked in five places is a limit
that means five slightly different things by the third release.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, File, Form, HTTPException, Request, UploadFile

from .. import licensing

router = APIRouter()

_cached: licensing.License | None = None


def current(refresh: bool = False) -> licensing.License:
    """The licence in force.

    Cached: reading it means a file read and an RSA verification, and the
    answer is needed on paths as hot as creating a user. Invalidated whenever
    a key is installed.
    """
    global _cached
    if _cached is None or refresh:
        _cached = licensing.load()
    return _cached


def forget() -> None:
    global _cached
    _cached = None


# --------------------------------------------------------------------------- #
#  Enforcement
# --------------------------------------------------------------------------- #
def usage(db) -> dict:
    """What is actually in use right now."""
    projects = 0
    try:
        from ..config import VisTestConfig
        from ..projects import ProjectRegistry

        projects = len(ProjectRegistry(VisTestConfig.load()).list())
    except Exception:
        projects = 0

    users = 0
    try:
        row = db.one("SELECT COUNT(*) AS n FROM user WHERE active=1")
        users = int((row or {}).get("n") or 0)
    except Exception:
        users = 0

    return {"projects": projects, "users": users}


def check_users(db, *, adding: int = 1) -> None:
    """May one more account exist. Raises 402 if not.

    402 rather than 403 on purpose: 403 says «you may not», and the honest
    answer here is «this installation may not, until someone pays or removes
    an account». The status code is the one place that distinction is cheap to
    make.
    """
    lic = current()
    used = usage(db)["users"]
    if not lic.allows_users(used + adding):
        raise HTTPException(
            402, f"The licence covers {lic.users} active users, and this "
                 f"installation already has {used}. Deactivate someone, or "
                 f"extend the licence.")


def check_projects(db, *, adding: int = 1) -> None:
    lic = current()
    used = usage(db)["projects"]
    if not lic.allows_projects(used + adding):
        raise HTTPException(
            402, f"The licence covers {lic.projects} connected projects, and "
                 f"this installation already has {used}. Disconnect one, or "
                 f"extend the licence.")


def check_runs() -> None:
    """May a new run be accepted.

    This is the only thing an expired licence actually stops, and the choice
    of *what* to stop is the whole design.

    Reading stays open forever: the baselines are the customer's data, and
    holding them hostage over an invoice is both wrong and, for a tool that
    lives inside a release pipeline, a fast route to being removed from it.
    Review stays open too — work already in flight should finish.

    New runs stop, and only after the grace period. By then the installation
    has been saying so on every screen for thirty days.
    """
    lic = current()
    if lic.blocked:
        raise HTTPException(
            402, f"The licence for «{lic.customer or 'this installation'}» "
                 f"ended on {lic.expires_at}, and the {licensing.GRACE_DAYS}-day "
                 "grace period is over. Existing runs and baselines stay "
                 "readable; new runs need a current licence key.")


# --------------------------------------------------------------------------- #
#  Routes
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
#  Перенос эталонов между инсталляциями
#
#  Живёт рядом с лицензией не по смыслу, а по расписанию: оба роутера
#  маленькие, и заводить третий модуль ради двух ручек — плодить файлы. Если
#  перенос обрастёт (выборочный импорт из интерфейса, предпросмотр картинок),
#  он переедет в свой.
# --------------------------------------------------------------------------- #
MAX_IMPORT_BYTES = 500 * 1024 * 1024


def _baselines_root():
    import os
    from pathlib import Path

    from ..config import VisTestConfig

    root = Path(os.getenv("VISTEST_ROOT", ".vistest"))
    return root / VisTestConfig.load().paths.baselines


@router.get("/api/baselines/export")
def baselines_export(request: Request, project: str = "", platform: str = "",
                     name: str = "", with_history: bool = False):
    """Скачать выбранные эталоны одним архивом.

    Право `reviewer`, а не `viewer`: эталоны — это содержимое снятых страниц,
    то есть данные той системы, которую тестируют. Отдавать их одним файлом
    тому, кому дали посмотреть на метрики, — не то же самое, что показать
    картинку на экране.
    """
    import tempfile
    from pathlib import Path

    from fastapi.responses import FileResponse

    from ..transfer import Selection, export
    from .main import _require

    _require(request, "reviewer", project or None)

    selection = Selection(project=project.strip(), platform=platform.strip(),
                          names=tuple(n for n in name.split(",") if n.strip()))
    tmp = Path(tempfile.mkdtemp(prefix="vistest-export-")) / "baselines.tar.gz"
    try:
        info = export(tmp, baselines_root=_baselines_root(),
                      selection=selection, with_history=with_history)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from None
    if not info["count"]:
        raise HTTPException(404, "Nothing matched the selection")

    stamp = info["created_at"].replace(":", "").replace("-", "")
    label = (project or "baselines").replace("/", "-")
    return FileResponse(
        tmp, media_type="application/gzip",
        filename=f"vistest-{label}-{stamp}.tar.gz",
        headers={"X-VisTest-Snapshots": str(info["count"])})


@router.post("/api/baselines/import")
async def baselines_import(request: Request,
                           file: UploadFile = File(...),
                           mode: str = Form("new"),
                           project: str = Form(""),
                           platform: str = Form(""),
                           dry_run: bool = Form(False)):
    """Влить присланный набор. `dry_run` отвечает «что будет», ничего не делая."""
    import shutil
    import tempfile
    from pathlib import Path

    from ..transfer import MODES, Selection, import_, inspect, plan
    from .auth import audit
    from .main import _require, db

    user = _require(request, "reviewer", project or None)
    if mode not in MODES:
        raise HTTPException(400, f"mode must be one of: {', '.join(MODES)}")

    tmpdir = Path(tempfile.mkdtemp(prefix="vistest-import-"))
    archive = tmpdir / "incoming.tar.gz"
    try:
        written = 0
        with archive.open("wb") as fh:
            while chunk := await file.read(1024 * 1024):
                written += len(chunk)
                if written > MAX_IMPORT_BYTES:
                    raise HTTPException(
                        413, f"The archive is larger than "
                             f"{MAX_IMPORT_BYTES // (1024 * 1024)} MB")
                fh.write(chunk)

        selection = Selection(project=project.strip(), platform=platform.strip())
        try:
            manifest = inspect(archive)
            steps = plan(archive, baselines_root=_baselines_root(), mode=mode,
                         selection=selection)
        except (OSError, ValueError) as e:
            raise HTTPException(400, str(e)) from None

        if dry_run:
            return {"dry_run": True, "made_by": manifest.get("version"),
                    "created_at": manifest.get("created_at"), "plan": steps}

        try:
            result = import_(archive, baselines_root=_baselines_root(),
                             mode=mode, selection=selection,
                             who=user.get("login", ""))
        except ValueError as e:
            raise HTTPException(400, str(e)) from None

        audit(db, user.get("login", ""), "baselines.imported",
              manifest.get("created_at", ""), mode=mode,
              **result["counts"])
        return {"dry_run": False, "plan": steps, **result}
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@router.get("/api/license")
def license_state(request: Request):
    """Everything the licence screen shows — and what the header banner reads."""
    from .main import db

    lic = current()
    used = usage(db)
    state = lic.to_dict()
    state["usage"] = used
    state["over_limit"] = {
        "projects": not lic.allows_projects(used["projects"]),
        "users": not lic.allows_users(used["users"]),
    }
    state["grace_days"] = licensing.GRACE_DAYS
    # Куда смотреть, если ключ не подхватился: переменная перебивает файл.
    state["source"] = ("environment" if licensing.read_key()
                       and lic.signed and _from_env() else
                       "file" if lic.signed else "none")
    return state


def _from_env() -> bool:
    import os

    return bool((os.getenv(licensing.ENV_KEY) or "").strip())


@router.post("/api/license")
def license_install(request: Request, body: dict = Body(...)):
    """Install a key. Admin only — this changes what the installation may do."""
    from .auth import audit, current_user
    from .main import _require, db

    _require(request, "admin")
    token = str(body.get("key") or "").strip()
    if not token:
        raise HTTPException(400, "Paste the licence key")

    try:
        lic = licensing.install(token)
    except licensing.LicenseError as e:
        raise HTTPException(400, str(e)) from None
    except OSError as e:
        raise HTTPException(500, f"The key could not be saved: {e}") from None

    forget()
    who = current_user(db, request.cookies.get("vistest_session"))["login"]
    audit(db, who, "license.installed", lic.customer or "—",
          edition=lic.edition, expires_at=lic.expires_at,
          projects=lic.projects, users=lic.users)
    return current(refresh=True).to_dict()
