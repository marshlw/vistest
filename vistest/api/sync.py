# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Moving baselines between installations, over HTTP.

The archive is core, and so are these two routes: `main` mounts them in
every installation, next to the baselines router and outside the plugin
wiring, so a plugin that fails cannot take them away. They do what
`vistest baselines export|import` does on the command line, through
`vistest.transfer.ArchiveSyncBackend`.

A plugin that registers a `BaselineSyncBackend` replaces that default here —
that slot is how something beyond the archive plugs in. Orchestration
between running installations (schedules, promotion from staging to
production, conflict policies) is not these routes' business; it arrives as a
plugin's own routes. `/api/capabilities` reports `baseline_sync` only for
such a backend: it is a capability beyond the core, and the archive is not.

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
    """The plugin's backend if one is registered, the core archive otherwise.

    Asked on every request rather than fixed when the router is mounted: the
    registry can be replaced (tests do), and a registry that cannot be read at
    all means no plugin, not no route.
    """
    try:
        from ..plugins.loader import active_registry

        reg = active_registry().sync_backend()
    except Exception:
        reg = None
    if reg is not None:
        return reg.impl
    from ..transfer import ArchiveSyncBackend

    return ArchiveSyncBackend()


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
            info = backend.export(tmp, baselines_root=_baselines_root(),
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
        modes = tuple(backend.modes)
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
                manifest = backend.inspect(archive)
                steps = backend.plan(archive, baselines_root=_baselines_root(),
                                     mode=mode, selection=selection)
            except (OSError, ValueError) as e:
                raise HTTPException(400, str(e)) from None

            if dry_run:
                return {"dry_run": True, "made_by": manifest.get("version"),
                        "created_at": manifest.get("created_at"), "plan": steps}

            try:
                result = backend.apply(archive, baselines_root=_baselines_root(),
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
