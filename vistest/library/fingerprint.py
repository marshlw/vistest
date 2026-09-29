# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""The renderer's fingerprint in the library: drawn only where it decides something.

The canary (`canary.py`) costs a tab, a page and two frames — about a
quarter of a second — and pytest-playwright opens a context per test. So it
is drawn only when it can change an outcome (`compare_lazily`):

1. **writing a baseline** — created or updated — so that its passport can
   name the renderer that drew it (`renderer`: the PNG's sha256 and the
   canary's version); the PNG is stored once per sha in
   `<baseline root>/.renderers/<sha>.png`, next to the baselines;
2. **a comparison that failed without it, against a baseline whose passport
   names a canary** — then this run's canary is drawn and the pair is
   compared again with `compare(..., renderer=(the baseline's, this run's))`.

Nothing else can move a verdict: the renderer only switches on a rule that
takes regions out, so a check that passes without the canary passes with
it. A passing check says «renderer: not checked (the check passed)».

A canary, once drawn, is kept for the browser it was drawn in and the
device scale factor it was drawn at — not per context: every context of one
browser at one scale draws the same pixels, and a context per test would
otherwise pay for it every time. It is drawn in a tab of the context the
screenshot came from, through the same capture as the screenshot.

Anything missing makes its side `None` and the answer «unknown», with the
reason: a picture handed in as bytes has no browser behind it; a store that
is not a directory has nowhere to keep a canary; a baseline accepted before
the canary existed names none; a canary file that was never committed
cannot be read.
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

__all__ = ["NOT_DRAWN", "RENDERERS_DIR", "RunCanary", "compare_lazily", "keep",
           "not_checked", "of_baseline", "of_target", "status"]

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


#: The canary nobody has drawn yet — nothing so far needed it.
NOT_DRAWN = RunCanary(why="not drawn")

#: One canary per browser and device scale factor, for the life of the
#: browser. A context without a browser (a persistent context) is its own key.
_BY_BROWSER: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _cache_key(page, context):
    """(the object the canary is kept on, the device scale factor)."""
    try:
        browser = context.browser
    except Exception:
        browser = None
    return (browser if browser is not None else context), _targets._device_ratio(page)


def of_target(target, *, stable_timeout_ms: int) -> RunCanary:
    """The canary of the browser `target` was drawn by; drawn at most once
    per browser and device scale factor."""
    if not callable(getattr(target, "screenshot", None)):
        return RunCanary(why="the screenshot was handed in, not taken from a page")
    page = _targets._page_of(target)
    context = getattr(page, "context", None) if page is not None else None
    if context is None or not callable(getattr(context, "new_page", None)):
        return RunCanary(why="the page belongs to no browser context that can "
                             "open a tab")
    owner, scale = _cache_key(page, context)
    try:
        cached = _BY_BROWSER.get(owner, {}).get(scale)
    except TypeError:                   # an owner that cannot be weakly held
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
        _BY_BROWSER.setdefault(owner, {})[scale] = run
    except TypeError:
        pass
    return run


def compare_lazily(compare_with, baseline_canary, this_run):
    """Compare without the renderer; with it only when that can change the verdict.

    `compare_with(renderer)` compares the pair; `baseline_canary()` reads
    the canary the baseline's passport names (or `None`); `this_run()` draws
    this run's (a `RunCanary`). Returns `(result, renderer)` — the renderer
    pair the result was computed with, `None` when it was computed without.

    A pass is final: the renderer only switches on a rule that takes regions
    out, so what passes without it passes with it. A failure is looked at
    again only when the baseline names a canary and this run can draw one,
    and compared again only when the two differ: with the same renderer the
    rule stays off, and the second comparison would be the first.
    """
    result = compare_with(None)
    if not result.failed:
        return result, None
    base = baseline_canary()
    if base is None:
        return result, None
    run = this_run()
    if run.png is None:
        return result, None
    renderer = (base, run.png)
    if not _renderer.check(renderer).changed:
        return result, renderer
    return compare_with(renderer), renderer


def not_checked() -> _renderer.RendererCheck:
    """The line of a check that passed without the canary."""
    return _renderer.RendererCheck(_renderer.NOT_CHECKED, why="the check passed")


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
                                run.why if run.png is None and run is not NOT_DRAWN
                                else "") if w)
    return _renderer.RendererCheck(_renderer.UNKNOWN, why=why or check.why)
