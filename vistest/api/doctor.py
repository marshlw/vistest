# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Diagnostics from the interface.

Two different questions under one name, and they must not be confused:

* `GET  /api/doctor/env`  — what is wrong with the **machine**: interpreter,
  dependencies, browsers, where the package was loaded from. Asked once after
  installation;
* `POST /api/doctor/run`  — what is wrong with the **project**: how much
  noise the page has of its own. Asked every time a new set of snapshots is
  created.

The second one is that «honest onboarding»: the page loads N times in a row,
and since nothing changed between loads, any discrepancy is false by
construction. A person learns the truth about their project before they trust
the tool, rather than two months into a red CI.

The command is heavy (several full page loads), so it runs as a background
job with a log — like a test run.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import FileResponse

from ..config import VisTestConfig, platform_key
from .jobs import runner

router = APIRouter()


# --------------------------------------------------------------------------- #
#  Environment
# --------------------------------------------------------------------------- #
@router.get("/api/doctor/env")
def env_report():
    """The same thing `run.py doctor` prints with no arguments."""
    import vistest

    cfg = VisTestConfig.load()
    where = str(Path(vistest.__file__).resolve().parent)
    installed_copy = "site-packages" in where.replace("\\", "/").split("/")

    modules = ["numpy", "cv2", "PIL", "playwright", "fastapi", "uvicorn",
               "onnxruntime", "pytest", "yaml", "requests", "allure"]
    required = {"numpy", "cv2", "PIL"}
    deps = [
        {"name": m,
         "present": importlib.util.find_spec(m) is not None,
         "required": m in required}
        for m in modules
    ]

    baselines = []
    root = cfg.baselines_path()
    if root.exists():
        for d in sorted(root.iterdir()):
            if d.is_dir():
                baselines.append({
                    "platform": d.name,
                    "count": sum(1 for _ in d.rglob("baseline.png")),
                })

    return {
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "platform_key": platform_key(),
        "root": str(cfg.root_path),
        "module": where,
        "editable": not installed_copy,
        "docker": shutil.which("docker") or "",
        "git": shutil.which("git") or "",
        "dependencies": deps,
        "baselines": baselines,
        "browsers_hint": (
            "Playwright browsers are installed with the command python run.py setup"
            if importlib.util.find_spec("playwright") is None else ""),
    }


# --------------------------------------------------------------------------- #
#  Project noise
# --------------------------------------------------------------------------- #
@router.post("/api/doctor/run")
def run_doctor_endpoint(payload: dict = Body(...)):
    url = (payload.get("url") or "").strip()
    if not url:
        raise HTTPException(400, "Please provide the page URL")
    if not url.startswith(("http://", "https://", "file://")):
        raise HTTPException(
            400, "The URL must start with http:// or https://")

    runs = int(payload.get("runs") or 3)
    if runs < 2:
        raise HTTPException(
            400, "At least 2 loads: nothing to compare against")
    if runs > 10:
        raise HTTPException(
            400, "More than 10 loads means minutes of waiting with no benefit")

    if runner.active("doctor"):
        raise HTTPException(409, "Diagnostics is already running")

    browser = payload.get("browser") or "chromium"
    viewport = payload.get("viewport") or "1440x900"
    selector = (payload.get("selector") or "").strip() or None

    def work(job):
        import json

        import numpy as np

        from ..core import noise as _noise
        from ..doctor import analyze, collect

        cfg = VisTestConfig.load()
        job.say(f"{url} — {runs} loads in clean contexts")
        frames = collect(url, runs=runs, browser=browser, viewport=viewport,
                         selector=selector, cfg=cfg, log=job.say)

        job.say("comparing loads against each other…")
        rep = analyze(frames, cfg=cfg, target=url)

        out = cfg.root_path / "doctor"
        out.mkdir(parents=True, exist_ok=True)
        if isinstance(rep.noise_mask, np.ndarray):
            _noise.save_mask(out / "noise.png", rep.noise_mask)
            rep.artifacts["noise map"] = str((out / "noise.png").resolve())
        data = rep.to_dict()
        (out / "report.json").write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

        job.say(f"false failures without suppression: {rep.raw_fails}/{rep.pairs}")
        job.say(f"with VisTest suppression: {rep.suppressed_fails}/{rep.pairs}")
        job.say(rep.verdict())
        return data

    job = runner.submit("doctor", f"Project noise: {url}", work)
    return {"job_id": job.id}


@router.get("/api/doctor/file")
def doctor_file(path: str = Query(...)):
    """Noise map. We serve only from the diagnostics directory."""
    cfg = VisTestConfig.load()
    root = (cfg.root_path / "doctor").resolve()
    target = Path(path).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise HTTPException(403, "Path is outside the diagnostics directory") from None
    if not target.is_file():
        raise HTTPException(404, "File not found")
    return FileResponse(str(target))


@router.get("/api/doctor/last")
def last_report():
    """The latest report — so the tab is not empty after a reload."""
    import json

    path = VisTestConfig.load().root_path / "doctor" / "report.json"
    if not path.exists():
        return {"report": None}
    try:
        return {"report": json.loads(path.read_text("utf-8")),
                "mtime": os.path.getmtime(path)}
    except Exception:
        return {"report": None}
