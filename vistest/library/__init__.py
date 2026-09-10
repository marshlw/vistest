# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""VisTest as a library: one function, no server, no interface.

    from vistest import expect_screenshot

    def test_home(page):
        page.goto("https://example.com")
        expect_screenshot(page, "home.png")

That is the whole surface. Baselines are files in the repository under
`tests/__vistest__/`, reviewed in pull requests like anything else; artifacts
and the report go to `.vistest/`, which belongs in `.gitignore`. The first run
of a new check fails and says how to create the baseline, the same way
Playwright's own screenshot assertion does — a check that silently passes the
first time is a check that has never been looked at.

Everything below this module is the engine that was already here. This layer
does four things: turn whatever was passed in into a picture, decide which
baseline it belongs to, fold the thresholds in the right order, and write down
what happened in a form the report and the CI log can both use.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ..core import pngio
from ..core.thresholds import ThresholdError, clean, patch_for
from ..models import CompareResult, Verdict
from ..storage import atomic
from ..storage.base import (
    SnapshotKey,
    SnapshotMeta,
    SnapshotStore,
    platforms_with,
)
from . import context as _context
from . import targets as _targets
from .errors import (
    BaselineMissing,
    ScreenshotMismatch,
    VisTestWarning,
    VisualCheckError,
)

__all__ = [
    "BaselineMissing",
    "LibraryContext",
    "ScreenshotMismatch",
    "SnapshotKey",
    "SnapshotMeta",
    "SnapshotStore",
    "VisTestWarning",
    "VisualCheckError",
    "expect_screenshot",
]

LibraryContext = _context.LibraryContext


def _call_thresholds(threshold) -> dict[str, float]:
    """`threshold=` in the two spellings it is written in.

    A bare number is the common one and means the severity at which this check
    fails — the only threshold most people ever touch. A mapping is the
    complete form and goes through the same validation the interface uses, so
    a misspelled key or a value out of range is refused here, at the call, and
    not folded in as if it had been understood.
    """
    if threshold is None:
        return {}
    if isinstance(threshold, bool):
        raise TypeError("threshold must be a number or a mapping, not a bool")
    if isinstance(threshold, (int, float)):
        raw: Mapping[str, Any] = {"fail_severity": float(threshold)}
    elif isinstance(threshold, Mapping):
        raw = threshold
    else:
        raise TypeError(
            f"threshold must be a number or a mapping of thresholds, "
            f"got {type(threshold).__name__}")
    try:
        return clean(dict(raw))
    except ThresholdError as e:
        raise ThresholdError(f"expect_screenshot(threshold=...): {e}") from None


def _ignore_mask(shape, boxes):
    if not boxes:
        return None
    from ..core import noise

    return noise.mask_from_boxes(shape, boxes)


def _relative(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


# --------------------------------------------------------------------------- #
def expect_screenshot(
    target: Any,
    name: str,
    *,
    platform: str | None = None,
    threshold: float | Mapping[str, float] | None = None,
    mask: Sequence[Any] | None = None,
    full_page: bool | None = None,
    store: SnapshotStore | None = None,
) -> CompareResult:
    """Compare `target` against the baseline stored under `name`.

    `target` is a Playwright `Page` or `Locator`, PNG bytes, a `PIL.Image`, a
    numpy array or a path to a PNG. Playwright is never imported to find out
    which — a page is recognised by having a `screenshot` method, so the
    package installs without a browser and works with a project's own page
    wrapper.

    `platform` names the directory the baseline lives in — browser and window
    size, `chromium-1440x900`. Left out, it is taken from the page itself, and
    for a target that is not a live page there is no platform at all and the
    baselines sit flat under the root. Guessing one would write a browser name
    into somebody's repository on no evidence.

    It is `platform` and not `profile` because in this package a profile is
    already a thing: `NamingProfile` and `SuiteProfile` are the rules for
    reading somebody else's snapshot file names. One word, one meaning.

    `threshold` is the severity at which this check fails, or a mapping for the
    full form. It is the innermost layer: config, then the project, then the
    snapshot's own passport, then this.

    `mask` takes locators and CSS selectors — painted out during capture, as
    Playwright does it — and boxes `(x, y, w, h)`, which are not painted at all
    but excluded from the comparison. A painted rectangle would end up inside
    the committed baseline, where the decision can no longer be revisited.

    Returns the `CompareResult` so that a caller can read the metrics. Raises
    `BaselineMissing` when there is nothing to compare against yet, and
    `ScreenshotMismatch` when the difference is beyond the thresholds — both
    are `AssertionError`s, and both name every file involved.
    """
    started = time.perf_counter()
    ctx = _context.current()
    call_patch = _call_thresholds(threshold)

    shot = _targets.capture(target, mask=mask, full_page=full_page)
    platform = platform if platform is not None else ctx.platform_for(shot)
    key = ctx.key(name, platform)
    store = store or ctx.store

    actual_path = atomic.write_bytes(ctx.artifact_path("actual", key), shot.png)
    baseline_path = store.path_of(key)
    baseline = store.get(key)

    # ---- nothing to compare against yet ------------------------------- #
    if baseline is None:
        if not ctx.update:
            _record(ctx, key, verdict="new_baseline", action="missing",
                    reason="there is no baseline for this snapshot yet",
                    images={"actual": str(actual_path)},
                    duration_ms=_ms(started))
            raise BaselineMissing.build(
                name=key.name, platform=platform,
                baseline=baseline_path, actual=actual_path,
                elsewhere=platforms_with(store, key))

        meta = store.put(key, shot.png)
        _record(ctx, key, verdict="new_baseline", action="created",
                reason="baseline created by --vistest-update",
                images={"actual": str(actual_path),
                        "baseline": str(baseline_path)},
                duration_ms=_ms(started))
        return _fresh_result(key, meta, Verdict.NEW_BASELINE)

    # ---- compare ------------------------------------------------------ #
    expected_rgb = pngio.decode(baseline, source=baseline_path)
    actual_rgb = pngio.decode(shot.png, source=f"the screenshot of {key.name}")

    passport = store.meta(key)
    cfg = ctx.config.diff.merged(
        **patch_for(snapshot_meta=(passport.to_dict() if passport else None),
                    call=call_patch))

    boxes = [*(passport.ignore_boxes if passport else ()), *shot.boxes]
    from ..core.comparator import compare, strip_internal

    result = compare(expected_rgb, actual_rgb, cfg=cfg, name=key.name,
                     ignore_mask=_ignore_mask(expected_rgb.shape[:2], boxes),
                     ai_hooks=_ai_hooks(ctx))
    result.duration_ms = _ms(started)

    #  Let go of the full-frame maps. `compare` hands back four arrays the size
    #  of the screenshot for whoever is going to draw pictures from them;
    #  nothing here does — the diff is drawn from the regions — and this result
    #  is about to be returned to somebody's test and, on a failure, held
    #  inside the exception for as long as pytest keeps it. Two hundred
    #  snapshots' worth of that is an out-of-memory kill, not a leak nobody
    #  notices.
    strip_internal(result)

    from ..report.library import describe

    reason = describe(result)
    limits = {"fail_severity": cfg.fail_severity,
              "max_changed_area_pct": cfg.max_changed_area_pct}
    images = {"baseline": str(baseline_path), "actual": str(actual_path)}

    diff_path = None
    if result.verdict is Verdict.FAIL:
        diff_path = _write_diff(ctx, key, actual_rgb, result)
        if diff_path is not None:
            images["diff"] = str(diff_path)

    # ---- accepting -------------------------------------------------- #
    if ctx.update:
        meta = store.put(key, shot.png)
        _record(ctx, key, verdict="new_baseline",
                action="unchanged" if meta.sha256 == _sha_of(baseline)
                else "updated",
                reason=("the baseline already matched" if result.verdict
                        is not Verdict.FAIL else f"accepted: {reason}"),
                result=result, limits=limits, images=images,
                duration_ms=_ms(started))
        return _fresh_result(key, meta, Verdict.NEW_BASELINE)

    _record(ctx, key,
            verdict="fail" if result.verdict is Verdict.FAIL else "pass",
            action="compared", reason=reason, result=result, limits=limits,
            images=images, duration_ms=_ms(started))

    if result.verdict is Verdict.FAIL:
        raise ScreenshotMismatch.build(
            name=key.name, platform=platform, result=result, reason=reason,
            baseline=baseline_path, actual=actual_path, diff=diff_path,
            report=ctx.report, limits=limits)
    return result


# --------------------------------------------------------------------------- #
#  Bits the function above would only make longer
# --------------------------------------------------------------------------- #
def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _sha_of(png: bytes) -> str:
    import hashlib

    return hashlib.sha256(png).hexdigest()


def _fresh_result(key: SnapshotKey, meta: SnapshotMeta,
                  verdict: Verdict) -> CompareResult:
    result = CompareResult(name=key.name, verdict=verdict)
    result.size_expected = result.size_actual = (meta.width, meta.height)
    return result


def _ai_hooks(ctx):
    """The AI layer if it is available, nothing if it is not.

    An optional part: `vistest[ai]` may not be installed, the ONNX model may be
    absent, the gate's coefficients may fail to load. None of that is a reason
    to stop somebody's test run, so it warns once and the cascade runs without
    it — the verdict is then the engine's own, which is what it always was.
    """
    try:
        from ..ai.pipeline import AIPipeline
    except ImportError:
        return None
    try:
        return AIPipeline(ctx.config.ai)
    except Exception as e:  # pragma: no cover - depends on optional models
        import warnings

        warnings.warn(f"vistest: the AI layer is not available ({e}); "
                      "comparing without it", VisTestWarning, stacklevel=3)
        return None


def _write_diff(ctx, key: SnapshotKey, actual_rgb, result) -> Path | None:
    """The picture with the changed regions boxed. Best effort, never fatal."""
    try:
        from ..render.artifacts import draw_boxes

        drawn = draw_boxes(actual_rgb, result.regions)
        return atomic.write_bytes(ctx.artifact_path("diff", key),
                                  pngio.encode(drawn))
    except Exception as e:  # pragma: no cover - drawing must not hide the diff
        import warnings

        warnings.warn(f"vistest: could not draw the diff picture for "
                      f"{key.name} ({type(e).__name__}: {e}); the verdict and "
                      "the actual screenshot are unaffected",
                      VisTestWarning, stacklevel=3)
        return None


def _record(ctx, key: SnapshotKey, *, verdict: str, action: str, reason: str,
            images: dict, duration_ms: int, result=None,
            limits: dict | None = None) -> None:
    """One row for the report, written as this process's own file."""
    from ..report.library import write_part

    entry = {
        "key": key.as_str(),
        "name": key.name,
        "platform": key.platform,
        "verdict": verdict,
        "action": action,
        "reason": reason,
        "duration_ms": duration_ms,
        "images": images,
        "limits": limits or {},
        "nodeid": ctx_nodeid(),
    }
    if result is not None:
        entry["metrics"] = {
            "max_severity": round(float(result.max_severity), 2),
            "changed_area_pct": round(float(result.changed_area_pct), 4),
            "ssim_global": round(float(result.ssim_global), 6),
            "de_mean": round(float(result.de_mean), 4),
        }
        entry["size"] = {"expected": list(result.size_expected),
                         "actual": list(result.size_actual),
                         "changed": bool(result.size_changed)}
        entry["regions"] = [
            {"kind": getattr(r.kind, "value", str(r.kind)),
             "severity": round(float(r.severity), 2),
             "x": r.x, "y": r.y, "w": r.w, "h": r.h,
             "selector": r.selector or ""}
            for r in list(result.regions)[:12]]
    try:
        write_part(ctx.parts_dir, entry)
    except OSError as e:  # pragma: no cover - a report row is not the verdict
        import warnings

        warnings.warn(f"vistest: could not write the report entry for "
                      f"{key.name} ({e})", VisTestWarning, stacklevel=3)


def ctx_nodeid() -> str:
    """The test this check belongs to, when pytest is the one running us."""
    import os

    return os.environ.get("PYTEST_CURRENT_TEST", "").split(" (")[0]
