# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""`POST /api/check` — checking a snapshot sent from anywhere.

This is the entry point for projects already written in Cypress, Playwright JS,
Java, C#, Go or anything else. The test takes a screenshot by its own means and
sends the PNG here; the comparison is performed by the VisTest engine, and the
response is a verdict, metrics, a list of regions and links to artifacts.

Why this way rather than «porting the tests»: nobody will rewrite an existing
test suite just for visual checks. A single HTTP call at the end of an existing
test is a realistic amount of change.

Additionally, `GET /api/stabilize.js` is served — the very same script for
freezing animations, determinism and DOM capture that the Python runner uses.
A client in any language injects it into the page and gets the same snapshot
stability.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import numpy as np
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse

from ..capture import dom as _dom
from ..capture import stabilize as _stab
from ..config import VisTestConfig
from ..models import Verdict
from ..service import CheckService, slug

router = APIRouter()

ROOT = Path(os.getenv("VISTEST_ROOT", ".vistest")).resolve()
ARTIFACTS = ROOT / "artifacts"
_cfg = VisTestConfig.load()


def _decode(data: bytes) -> np.ndarray:
    from PIL import Image

    try:
        return np.array(Image.open(io.BytesIO(data)).convert("RGB"))
    except Exception as e:
        raise HTTPException(400, f"could not read the image: {e}") from e


_ALLOWED_OVERRIDES = {
    "delta_e_threshold", "ssim_threshold", "fail_severity",
    "max_changed_area_pct", "min_region_px", "max_align_shift_px",
    "detect_moved", "ignore_kinds", "fail_on_size_change", "moved_severity_scale",
}


@router.post("/api/check")
async def check(
    name: str = Form(..., description="snapshot name, for example checkout.png"),
    image: UploadFile = File(..., description="current PNG screenshot"),
    frames: list[UploadFile] = File(
        default=[], description="extra frames of the same page for dynamics detection"),
    dom: UploadFile | None = File(default=None, description="DOM snapshot JSON"),
    project: str | None = Form(default=None),
    platform: str | None = Form(default=None),
    browser: str = Form(default="chromium"),
    run_key: str | None = Form(default=None),
    update_baseline: bool = Form(default=False),
    preset: str | None = Form(default=None),
    options: str | None = Form(default=None, description="JSON overriding the thresholds"),
):
    """Compare the sent snapshot with the baseline.

    Response:
        {
          "verdict": "pass" | "fail" | "new_baseline",
          "passed": true|false,
          "metrics": {...}, "regions": [...], "notes": [...],
          "artifacts": {"boxes": "/files/...", ...},
          "review_url": "/ui/"
        }
    """
    rgb = _decode(await image.read())
    extra = [_decode(await f.read()) for f in frames if f is not None]
    all_frames = [rgb, *extra] if extra else None

    dom_data = None
    if dom is not None:
        try:
            dom_data = json.loads(await dom.read())
        except Exception:
            dom_data = None

    overrides = {}
    if options:
        try:
            raw = json.loads(options)
        except Exception as e:
            raise HTTPException(400, f"options did not parse as JSON: {e}") from e
        unknown = set(raw) - _ALLOWED_OVERRIDES
        if unknown:
            raise HTTPException(400, f"unknown options: {sorted(unknown)}")
        overrides = raw

    # Порядок здесь и есть ответ на «чей порог сильнее»: пресет или конфиг —
    # основа, поверх ложится то, что выставили в интерфейсе, и только сверху —
    # `options` этого конкретного вызова. Ближе к вызывающему — сильнее.
    cfg = VisTestConfig.preset_of(preset) if preset else _cfg
    try:
        from .main import db
        from .thresholds import apply
        cfg = apply(cfg, db, project)
    except Exception:
        pass
    run_dir = ARTIFACTS / "checks" / (slug(run_key) if run_key else "adhoc")
    svc = CheckService(cfg, platform=platform, browser=browser, run_dir=run_dir)

    res = svc.check(
        name, rgb,
        frames=all_frames, dom=dom_data,
        diff_overrides=overrides or None,
        update_baseline=update_baseline or None,
    )

    body = res.to_dict()
    body["passed"] = res.verdict is not Verdict.FAIL
    body["artifacts"] = {k: _public_uri(v) for k, v in res.artifacts.items()}
    body["project"] = project or cfg.service.project
    body["platform"] = svc.platform
    body["review_url"] = "/ui/"
    return body


@router.get("/api/baselines")
def list_baselines(platform: str | None = None):
    """List of baselines — so the client can find out what has already been recorded."""
    from ..storage import FileBaselineStore

    root = _cfg.root_path / _cfg.paths.baselines
    if not root.exists():
        return []
    out = []
    for d in sorted(root.iterdir()):
        if not d.is_dir() or (platform and d.name != platform):
            continue
        out.append({"platform": d.name, "names": FileBaselineStore(d).list_names()})
    return out


@router.post("/api/baselines/upload")
async def put_baseline(
    name: str = Form(...),
    image: UploadFile = File(...),
    platform: str | None = Form(default=None),
    browser: str = Form(default="chromium"),
    url: str | None = Form(default=None),
):
    """Record a baseline directly, without comparison.

    `url` is optional, but with it the snapshot becomes «runnable»: it can be
    recreated and checked from the UI with a single button.
    """
    svc = CheckService(_cfg, platform=platform, browser=browser)
    svc.save_baseline(name, _decode(await image.read()),
                      meta={"approved_by": "api", "url": url})
    return {"ok": True, "name": name, "platform": svc.platform}


@router.get("/api/stabilize.js", response_class=PlainTextResponse)
def stabilize_js():
    """Stabilization script for clients not on Python.

    Injected into the page before the snapshot. Provides the same freezing of
    animations, deterministic Math.random/Date and the DOM snapshot capture
    function that the Python runner uses — meaning the snapshots are comparable.

    After injection the following is available:
        window.__vistest.freeze()        freeze CSS
        window.__vistest.settle()        promise: fonts, images, lazy-load
        window.__vistest.dom(dpr)        DOM snapshot for attribution
        window.__vistest.maskBoxes()     bboxes of dynamic elements
    """
    parts = [
        "(() => {",
        _stab.DETERMINISM_JS,
        "const FREEZE_CSS = " + json.dumps(_stab.FREEZE_CSS) + ";",
        "const MEDIA_SELECTORS = " + json.dumps(list(_stab.DEFAULT_MEDIA_SELECTORS)) + ";",
        "const findBoxes = " + _stab.FIND_BOXES_JS + ";",
        "const findAnimated = " + _stab.FIND_ANIMATED_JS + ";",
        "const waitMedia = " + _stab.WAIT_MEDIA_JS + ";",
        "const scrollThrough = " + _stab.SCROLL_THROUGH_JS + ";",
        "const domSnapshot = " + _dom.SNAPSHOT_JS + ";",
        """
        function freeze() {
          let s = document.getElementById('__vistest_freeze');
          if (!s) {
            s = document.createElement('style');
            s.id = '__vistest_freeze';
            (document.head || document.documentElement).appendChild(s);
          }
          s.textContent = FREEZE_CSS;
          return true;
        }

        async function settle(opts) {
          opts = opts || {};
          freeze();
          if (opts.scroll !== false) await scrollThrough(opts.stepRatio || 0.8);
          await waitMedia();
          return true;
        }

        function maskBoxes(extraSelectors) {
          const sels = MEDIA_SELECTORS.concat(extraSelectors || []);
          return findBoxes(sels).concat(
            findAnimated().map(b => Object.assign(b, { source: 'animated' })));
        }

        window.__vistest = {
          freeze, settle, maskBoxes,
          dom: (dpr) => domSnapshot(dpr || window.devicePixelRatio || 1),
          version: '0.1.0',
        };
        })();
        """,
    ]
    return "\n".join(parts)


def _public_uri(path: str) -> str:
    """Local artifact path → URL served by /files/."""
    try:
        rel = Path(path).resolve().relative_to(ARTIFACTS.resolve())
        return "/files/" + rel.as_posix()
    except Exception:
        return path
