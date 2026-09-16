# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Moving baselines between installations, over HTTP.

The routes exist only when a `BaselineSyncBackend` is active — `main` mounts
this router conditionally. Without one there is nothing to answer, and the
paths are simply unknown to the server, the same as any path that was never
there. The interface reads `/api/capabilities` and does not offer the action.

What stays here is what is the server's business whatever the backend does:
who may call it, how much may be uploaded, and the audit record.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from ..plugins.api import BaselineSelection

MAX_IMPORT_BYTES = 500 * 1024 * 1024


def _baselines_root() -> Path:
    from ..config import VisTestConfig

    root = Path(os.getenv("VISTEST_ROOT", ".vistest"))
    return root / VisTestConfig.load().paths.baselines


def _backend():
    from ..plugins.loader import active_registry

    reg = active_registry().sync_backend()
    if reg is None:
        #  The backend was active when the router was mounted and is gone now
        #  (a test replaced the registry). Answer as an unknown path would.
        raise HTTPException(404, "Not Found")
    return reg


def _names(raw: str) -> tuple[str, ...]:
    return tuple(n.strip() for n in raw.split(",") if n.strip())


def build_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/baselines/export")
    def baselines_export(request: Request, project: str = "", platform: str = "",
                         name: str = "", with_history: bool = False):
        """Download the selected baselines as one archive.

        `reviewer`, not `viewer`: baselines are the content of the captured
        pages — the data of the system under test. Handing them over as one
        file to someone who was given access to look at the metrics is not the
        same as showing a picture on screen.
        """
        from fastapi.responses import FileResponse

        from .main import _require

        _require(request, "reviewer", project or None)
        backend = _backend()

        selection = BaselineSelection(project=project.strip(),
                                      platform=platform.strip(),
                                      names=_names(name))
        tmp = Path(tempfile.mkdtemp(prefix="vistest-export-")) / "baselines.tar.gz"
        try:
            info = backend.impl.export(tmp, baselines_root=_baselines_root(),
                                       selection=selection,
                                       with_history=with_history)
        except FileNotFoundError as e:
            raise HTTPException(404, str(e)) from None
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        if not info.get("count"):
            raise HTTPException(404, "Nothing matched the selection")

        stamp = str(info.get("created_at", "")).replace(":", "").replace("-", "")
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
        """Merge an uploaded set. `dry_run` answers "what would happen"."""
        from .auth import audit
        from .main import _require, db

        user = _require(request, "reviewer", project or None)
        backend = _backend()
        modes = tuple(backend.impl.modes)
        if mode not in modes:
            raise HTTPException(400, f"mode must be one of: {', '.join(modes)}")

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

            selection = BaselineSelection(project=project.strip(),
                                          platform=platform.strip())
            try:
                manifest = backend.impl.inspect(archive)
                steps = backend.impl.plan(archive, baselines_root=_baselines_root(),
                                          mode=mode, selection=selection)
            except (OSError, ValueError) as e:
                raise HTTPException(400, str(e)) from None

            if dry_run:
                return {"dry_run": True, "made_by": manifest.get("version"),
                        "created_at": manifest.get("created_at"), "plan": steps}

            try:
                result = backend.impl.apply(archive, baselines_root=_baselines_root(),
                                            mode=mode, selection=selection,
                                            who=user.get("login", ""))
            except ValueError as e:
                raise HTTPException(400, str(e)) from None

            audit(db, user.get("login", ""), "baselines.imported",
                  manifest.get("created_at", ""), mode=mode,
                  **(result.get("counts") or {}))
            return {"dry_run": False, "plan": steps, **result}
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    return router
