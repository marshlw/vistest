# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""The renderer's fingerprint in the library: once per browser context, kept by sha.

`expect_screenshot` draws the canary (`canary.py`) once for every browser
context it sees — in a tab of its own, through the same capture as the
screenshot — and keeps it for as long as the context lives. A baseline's
passport names the canary of the renderer that drew it (`renderer`: the
PNG's sha256 and the canary's version), and the PNG itself is stored once
per sha in `<baseline root>/.renderers/<sha>.png`, next to the baselines, to
be committed and reviewed with them.

On a comparison both canaries go to the engine —
`compare(..., renderer=(the baseline's, this run's))` — and the verdict on
them is one line of the report and of the failure message: the same
renderer, a different one (by so many pixels), or unknown. Anything missing
makes its side `None` and the answer «unknown», with the reason: a picture
handed in as bytes has no browser behind it; a store that is not a directory
has nowhere to keep a canary; a baseline accepted before the canary existed
names none; a canary file that was never committed cannot be read.
"""

from __future__ import annotations

import hashlib
import time
import weakref
from dataclasses import dataclass, replace
from pathlib import Path

from ..core import renderer as _renderer
from ..storage import atomic
from . import canary as _canary
from . import targets as _targets

__all__ = ["RENDERERS_DIR", "RunCanary", "keep", "of_baseline", "of_target", "status"]

#: Under the baseline root: one PNG per canary, named by its sha256.
RENDERERS_DIR = ".renderers"


@dataclass(frozen=True)
class RunCanary:
    """This run's canary, or why there is none."""

    png: bytes | None = None
    sha256: str = ""
    why: str = ""
    #: Milliseconds spent drawing it in this call; `None` when it came from
    #: the context's cache or was never drawn.
    drawn_ms: int | None = None


#: One canary per browser context, for the life of the context.
_BY_CONTEXT: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def of_target(target, *, stable_timeout_ms: int) -> RunCanary:
    """The canary of the browser context `target` lives in; drawn at most once."""
    if not callable(getattr(target, "screenshot", None)):
        return RunCanary(why="the screenshot was handed in, not taken from a page")
    page = _targets._page_of(target)
    context = getattr(page, "context", None) if page is not None else None
    if context is None or not callable(getattr(context, "new_page", None)):
        return RunCanary(why="the page belongs to no browser context that can "
                             "open a tab")
    try:
        cached = _BY_CONTEXT.get(context)
    except TypeError:                   # a context that cannot be weakly held
        cached = None
    if cached is not None:
        return replace(cached, drawn_ms=None)
    started = time.perf_counter()
    try:
        png = _canary.draw(context, stable_timeout_ms=stable_timeout_ms)
        run = RunCanary(png=png, sha256=_sha(png))
    except Exception as e:  # the canary must never fail somebody's test
        run = RunCanary(why=f"the canary could not be drawn ({type(e).__name__}: {e})")
    run = replace(run, drawn_ms=int((time.perf_counter() - started) * 1000))
    try:
        _BY_CONTEXT[context] = run
    except TypeError:
        pass
    return run


def _dir(store) -> Path | None:
    root = getattr(store, "root", None)
    return Path(root) / RENDERERS_DIR if root is not None else None


def keep(store, run: RunCanary) -> dict | None:
    """Store this run's canary once per sha; the passport's `renderer` for it.

    `None` when there is nothing to keep or nowhere to keep it.
    """
    folder = _dir(store)
    if run.png is None or folder is None:
        return None
    path = folder / f"{run.sha256}.png"
    if not path.is_file():
        atomic.write_bytes(path, run.png)
    return {"sha256": run.sha256, "canary_version": _canary.CANARY_VERSION}


def of_baseline(store, passport) -> tuple[bytes | None, str]:
    """The canary the baseline's passport names, or `None` and why."""
    rec = dict(getattr(passport, "renderer", None) or {})
    if not rec:
        return None, ("the baseline names no canary — accepted before the canary "
                      "existed, or not from a page")
    if rec.get("canary_version") != _canary.CANARY_VERSION:
        return None, (f"the baseline's canary is of version {rec.get('canary_version')}, "
                      f"this one is {_canary.CANARY_VERSION}")
    folder = _dir(store)
    if folder is None:
        return None, "this store keeps no canaries"
    path = folder / f"{rec['sha256']}.png"
    try:
        data = path.read_bytes()
    except OSError:
        return None, f"{path} is not there — commit it with the baseline"
    if _sha(data) != rec["sha256"]:
        return None, f"{path} is not the canary the passport names"
    return data, ""


def status(baseline: bytes | None, baseline_why: str,
           run: RunCanary) -> _renderer.RendererCheck:
    """The engine's answer on the two canaries, with the reason when unknown."""
    check = _renderer.check((baseline, run.png))
    if check.status != _renderer.UNKNOWN:
        return check
    why = "; ".join(w for w in (baseline_why if baseline is None else "",
                                run.why if run.png is None else "") if w)
    return _renderer.RendererCheck(_renderer.UNKNOWN, why=why or check.why)
