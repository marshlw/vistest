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

import re
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

#  CAPTURE_VERSION — the way pictures of a live page are taken, as the
#  passport records it (`capture/ready.py` says what each number means).
from ..capture.ready import LIBRARY_CAPTURE_VERSION as CAPTURE_VERSION
from ..capture.ready import old_way
from ..core import pngio
from ..core.thresholds import ThresholdError, patch_for, validate
from ..models import CompareResult, Verdict
from ..storage import atomic
from ..storage.base import (
    SnapshotKey,
    SnapshotMeta,
    SnapshotStore,
    platforms_with,
)
from . import context as _context
from . import fingerprint as _fingerprint
from . import js as _js
from . import targets as _targets
from . import words as _words
from .errors import (
    BaselineMissing,
    CaptureError,
    ScreenshotMismatch,
    VisTestWarning,
    VisualCheckError,
    hide_for_verdicts,
)

__all__ = [
    "BaselineMissing",
    "CaptureError",
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


def _call_thresholds(fail_severity, max_changed_area_pct) -> dict[str, float]:
    """The call's two thresholds, checked here, at the call.

    Two numbers with the names they have everywhere else — vistest.yaml, the
    passport, the interface. A misspelt name is Python's own TypeError; a value
    out of range is refused here, and not folded in as if it had been understood.
    """
    out: dict[str, float] = {}
    for name, value in (("fail_severity", fail_severity),
                        ("max_changed_area_pct", max_changed_area_pct)):
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"expect_screenshot: {name} is a number from 0 to 100, "
                            f"not {type(value).__name__}")
        try:
            out[name] = validate(name, value)
        except ThresholdError as e:
            raise ThresholdError(f"expect_screenshot({name}=...): {e}") from None
    return out


def _ignore_mask(shape, boxes):
    if not boxes:
        return None
    from ..core import noise

    return noise.mask_from_boxes(shape, boxes)


# --------------------------------------------------------------------------- #
def expect_screenshot(
    target: Any,
    name: str,
    *,
    platform: str | None = None,
    fail_severity: float | None = None,
    max_changed_area_pct: float | None = None,
    mask: Sequence[Any] | None = None,
    full_page: bool | None = None,
    store: SnapshotStore | None = None,
    scale: str = "css",
    keep_pointer: bool | None = None,
    blur_focus: bool | None = None,
    timeout_ms: int | None = None,
) -> CompareResult:
    """Compare `target` against the baseline stored under `name`.

    `target` is a Playwright `Page` or `Locator`, PNG bytes, a `PIL.Image`, a
    numpy array or a path to a PNG. Playwright is never imported to find out
    which — a page is recognised by having a `screenshot` method, so the
    package installs without a browser and works with a project's own page
    wrapper. An array is H×W×3, RGB, uint8 (an integer array within 0–255 is
    taken as it is; a float one is refused, with the conversion to write —
    and OpenCV's `imread` gives BGR: `a[..., ::-1]`). Playwright's async API
    is refused by name: the check drives the sync one.

    `platform` names the directory the baseline lives in — browser and window
    size, `chromium-1440x900`. Left out, it is taken from the page itself, and
    for a target that is not a live page there is no platform at all and the
    baselines sit flat under the root. Guessing one would write a browser name
    into somebody's repository on no evidence.

    It is `platform` and not `profile` because in this package a profile is
    already a thing: `NamingProfile` and `SuiteProfile` are the rules for
    reading somebody else's snapshot file names. One word, one meaning.

    `fail_severity` is the severity (0–100) at which this check fails, and
    `max_changed_area_pct` the share of the picture (0–100 %) that fails it
    whatever the severity — the same names as in vistest.yaml (`diff:`). They
    are the innermost layer: config, then the snapshot's own passport, then
    this. A region no rule explained fails the check whatever its severity
    unless a threshold was set by one of those layers, and then a region below
    it is listed in the result, the report and the failure message, with where
    the threshold came from, and does not fail it.

    `mask` takes locators and CSS selectors — painted out during capture, as
    Playwright does it — and boxes `(x, y, w, h)`, which are not painted at all
    but excluded from the comparison. A painted rectangle would end up inside
    the committed baseline, where the decision can no longer be revisited.

    A live page is photographed as `toHaveScreenshot()` photographs it:
    animations stopped, the caret hidden, web fonts loaded, and frames taken
    until two in a row are identical — for at most `capture.stable_timeout_ms`
    (vistest.yaml; five seconds, 0 takes one frame). A page that does not
    settle in that time is compared on its last frame, and the reason says
    so. Before the frames the page is asked whether it is ready — loaded, no
    request in flight (counted when the pytest plugin is active), no loader in
    the area, its images in, nothing changing for a moment —, each step for at
    most `capture.ready_timeout_ms` (five seconds; 0 does not ask); a step
    that gives up is said in the reason.

    `timeout_ms` is the time this whole check may spend on the page: the
    readiness, the frames, the second look and the renderer's canary together.
    A page that keeps the check waiting past it raises `CaptureError` saying
    what it was waiting for; the two limits above are cut to it. A page that
    stopped answering takes up to 1.5 s more, to say why: whether it still
    answers at all (`js.ALIVE_MS`) and whether its tab crashed is asked after
    the deadline — `timeout_ms=2000` on a page stuck in a loop raises in about
    3.5 s. Left out, the deadline is made of those limits (library/js.py,
    `budget_ms`).

    A check that fails gets a second look (core/retry.py): more frames, until
    two in a row are identical. Nothing it sees is masked. When the page
    changed once and then held still — data that arrived late — the verdict is
    from the later frame; what keeps changing is named in the message, with
    the `mask=` that would hide it, and the check fails until that mask is
    written on purpose. On a page that was not ready there is no second look.

    The pointer is moved off the page before the picture, so that no element
    is under it and a hover left by the previous step is not photographed;
    the focus is left alone. `keep_pointer=True` (`capture.keep_pointer`) keeps
    the pointer where the test left it — for a hover captured on purpose —,
    `blur_focus=True` (`capture.blur_focus`) takes the focus off whatever has
    it. A Locator's element is put back where it was in the window when its
    baseline was taken, when the baseline's passport records that place; so is
    the window's scroll for a picture of the window
    (`capture.match_baseline_scroll`, on by default).

    `scale` is `"css"` (the default: one picture pixel
    per CSS pixel on any screen) or `"device"` (the screen's own pixels); the
    platform directory carries the scale of the picture, so the two never
    share a baseline.

    Returns the `CompareResult` so that a caller can read the metrics. Raises
    `BaselineMissing` when there is nothing to compare against yet, and
    `ScreenshotMismatch` when the difference is beyond the thresholds — both
    are `AssertionError`s, and both name every file involved. Raises
    `CaptureError` when the page is closed, crashed or stopped answering:
    everything asked of a live page shares one deadline (library/js.py), so a
    page whose main thread is stuck fails the check in seconds instead of
    hanging the run.
    """
    started = time.perf_counter()
    _check_name(name)
    ctx = _context.current()
    call_patch = _call_thresholds(fail_severity, max_changed_area_pct)
    total, ready_wait, wait = _deadline(ctx.config.capture, timeout_ms)
    try:
        with _js.budget(total, cap_ms=max(_js.CALL_CAP_MS, ready_wait)):
            return _check(ctx, target, name, started=started, call_patch=call_patch,
                          wait=wait, ready_wait=ready_wait, platform=platform,
                          mask=mask, full_page=full_page, store=store, scale=scale,
                          keep_pointer=keep_pointer, blur_focus=blur_focus)
    except CaptureError as e:
        raise CaptureError(f"vistest: could not check {name!r}: {e}") from None


def _deadline(capture, timeout_ms) -> tuple[int, int, int]:
    """(the whole check, each readiness step, the frames), in ms.

    Without `timeout_ms` the deadline is made of the two limits of vistest.yaml
    with room for everything else (`_js.budget_ms`). With it, it is the
    deadline, and neither limit may be longer than it.
    """
    ready, stable = capture.ready_timeout_ms, capture.stable_timeout_ms
    if timeout_ms is None:
        return _js.budget_ms(ready, stable), ready, stable
    if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int):
        raise TypeError(f"expect_screenshot: timeout_ms is a whole number of "
                        f"milliseconds, got {timeout_ms!r}")
    if timeout_ms <= 0:
        raise ValueError(f"expect_screenshot: timeout_ms must be more than 0, "
                         f"got {timeout_ms}")
    return timeout_ms, min(ready, timeout_ms), min(stable, timeout_ms)


def _check(ctx, target: Any, name: str, *, started: float, call_patch: dict,
           wait: int, ready_wait: int, platform: str | None, mask, full_page,
           store, scale: str, keep_pointer, blur_focus) -> CompareResult:
    """`expect_screenshot` once its arguments are read: inside the check's deadline."""
    scale = _targets.check_scale(scale)
    away, blur = _targets.pointer_plan(ctx.config.capture, keep_pointer, blur_focus)
    store = store or ctx.store
    #  A Locator goes back where its baseline had it: the passport is read
    #  before the picture, under the key the picture will have.
    place = _place_from_passport(ctx, store, target, name, platform, scale)
    restore = ctx.config.capture.match_baseline_scroll
    window = (_window_from_passport(ctx, store, target, name, platform, scale)
              if restore else None)
    shot = _targets.capture(target, mask=mask, full_page=full_page,
                            scale=scale,
                            stable_timeout_ms=wait, ready_timeout_ms=ready_wait,
                            quiet_ms=ctx.config.capture.quiet_ms,
                            ignore_requests=ctx.config.capture.ignore_requests,
                            pointer_away=away, blur_focus=blur, place=place, window=window)
    platform = platform if platform is not None else ctx.platform_for(shot)
    key = ctx.key(name, platform)
    #  From here on the check has a name, and it must leave a row in the
    #  report whatever happens to it. A row is what the report counts and what
    #  the collision check reads: a check that died between here and its
    #  verdict — the actual picture could not be written, the baseline could
    #  not be read, the comparison raised — used to leave nothing, so it was
    #  neither in the report nor seen as the second writer of its name.
    written: list[str] = []
    actual_path: Path | None = None
    captured: dict | None = None

    def record(**fields) -> None:
        _record(ctx, key, **fields)
        written.append(fields["verdict"])

    try:
        #  What the capture has to say for itself: a page that would not hold
        #  still, a font wait that failed. It goes into every reason below, so
        #  the report and the CI log carry it whatever the verdict.
        said = [*shot.notes, *filter(None, [
            shot.ready.text() if shot.ready is not None else "",
            shot.stability.unsettled_text()])]
        captured = _capture_row(shot)

        actual_path = atomic.write_bytes(ctx.artifact_path("actual", key), shot.png)
        baseline_path = store.path_of(key)
        baseline = store.get(key)

        mode = ctx.update_mode

        # ---- nothing to compare against yet ------------------------------- #
        if baseline is None:
            if mode is None:
                record(verdict="new_baseline", action="missing",
                       reason=_with(said, "there is no baseline for this snapshot yet"),
                       lines=[("reason", "there is no baseline for this snapshot yet"),
                              *(("capture", n) for n in said)],
                       images={"actual": str(actual_path)},
                       duration_ms=_ms(started), capture=captured)
                raise BaselineMissing.build(
                    name=_shown(key), asked=_asked(key), platform=platform,
                    baseline=baseline_path, actual=actual_path,
                    elsewhere=platforms_with(store, key), accept=ctx.accept_with)

            _refuse_too_large(ctx, shot.png, key)
            _warn_unsettled_accept(shot, key)
            run_canary = _fingerprint.of_target(target, stable_timeout_ms=wait)
            _canary_row(captured, run_canary)
            meta = store.put(key, shot.png,
                             meta=_passport_for(store, key, run_canary, shot))
            record(verdict="new_baseline", action="created",
                   reason=_with(said, f"baseline created by {ctx.updated_by(mode)}"),
                   lines=[("reason", f"baseline created by {ctx.updated_by(mode)}"),
                          *(("capture", n) for n in said)],
                   images={"actual": str(actual_path),
                           "baseline": str(baseline_path)},
                   duration_ms=_ms(started), capture=captured)
            return _fresh_result(key, meta, Verdict.NEW_BASELINE, notes=said)

        # ---- compare ------------------------------------------------------ #
        expected_rgb = pngio.decode(baseline, source=baseline_path)
        actual_rgb = pngio.decode(shot.png, source=f"the screenshot of {key.name}")

        passport = store.meta(key)
        launch_line = _launch_line(passport, shot)
        if launch_line:
            said.append(launch_line)
        dpr_line = _dpr_line(passport, shot)
        if dpr_line:
            said.append(dpr_line)
        cfg = ctx.config.diff.merged(
            **patch_for(snapshot_meta=(passport.to_dict() if passport else None),
                        call=call_patch))

        boxes = [*(passport.ignore_boxes if passport else ()), *shot.boxes]
        from ..core.comparator import compare, strip_internal

        ignore = _ignore_mask(expected_rgb.shape[:2], boxes)
        hooks = _ai_hooks(ctx)

        #  The renderer's canary is drawn only where it can decide something
        #  (fingerprint.compare_lazily): a check that passes without it passes
        #  with it too — the renderer only lets a rule take regions out.
        def compare_with(renderer):
            return compare(expected_rgb, actual_rgb, cfg=cfg, name=key.name,
                           ignore_mask=ignore, ai_hooks=hooks, renderer=renderer)

        run_canary = _fingerprint.NOT_DRAWN
        base_canary, base_why = None, ""

        def this_run():
            nonlocal run_canary
            run_canary = _fingerprint.of_target(target, stable_timeout_ms=wait)
            return run_canary

        def baseline_canary():
            nonlocal base_canary, base_why
            base_canary, base_why = _fingerprint.of_baseline(store, passport)
            return base_canary

        result, renderer = _fingerprint.compare_lazily(compare_with, baseline_canary,
                                                       this_run)
        rend = (_fingerprint.not_checked() if renderer is None and not result.failed
                else _fingerprint.status(base_canary, base_why, run_canary))
        _canary_row(captured, run_canary)
        run_sha = _fingerprint.keep_run(ctx.artifacts_root, run_canary)

        #  Failed: more frames, until two in a row are identical. Nothing is
        #  masked: what changed once and held still (late data) is judged on
        #  the later frame, what keeps changing is named and still fails. No
        #  second look on a page that was not ready. The same function the
        #  server uses — see core/retry.py for why nothing may be hidden here.
        final_png = shot.png
        live = None
        if result.failed and shot.retake is not None \
                and ctx.config.capture.retry_on_fail:
            result, frame, live = _second_look(shot, key, result, expected_rgb, actual_rgb,
                                               cfg, ignore, hooks, renderer, wait)
            if frame is not None and frame is not actual_rgb:
                #  The verdict is from a later frame: that is the picture a
                #  person opens, the one the diff is drawn on, and the one an
                #  accept would write.
                actual_rgb = frame
                final_png = pngio.encode(frame)
                atomic.write_bytes(actual_path, final_png)
        result.duration_ms = _ms(started)

        #  Let go of the full-frame maps. `compare` hands back four arrays the size
        #  of the screenshot for whoever is going to draw pictures from them;
        #  nothing here does — the diff is drawn from the regions — and this result
        #  is about to be returned to somebody's test and, on a failure, held
        #  inside the exception for as long as pytest keeps it. Two hundred
        #  snapshots' worth of that is an out-of-memory kill, not a leak nobody
        #  notices.
        strip_internal(result)

        hint = _scale_hint(shot, result)
        if hint:
            said.append(hint)
        #  A picture of a page, not one handed in: only a page has a renderer.
        page = shot.retake is not None or bool(shot.browser)
        result.notes[:0] = said

        #  What the second look found goes into the message too — a page that
        #  was not ready and got no masking, a later frame that was judged,
        #  something that keeps changing: the person reading the failure needs
        #  it more than the report does. It is already among the notes. Each
        #  on a line of its own, under its own label (review v1, 4.4): the
        #  hint with the mask used to be the tail of a 400-character reason.
        hints = _failure_hints(shot, result, live, passport, away, blur,
                               accept=ctx.accept_with) \
            if result.verdict is Verdict.FAIL else []
        what = _words.reason(result)
        notes = [*(("capture", n) for n in said),
                 *(("second look", _words.look(n)) for n in _look_notes(result)),
                 *(("hint", h) for h in hints)]
        reason = _with([text for _, text in notes], what)
        limits = _limits(cfg)
        images = {"baseline": str(baseline_path), "actual": str(actual_path)}

        diff_path = None
        if result.verdict is Verdict.FAIL:
            diff_path = _write_diff(ctx, key, actual_rgb, result)
            if diff_path is not None:
                images["diff"] = str(diff_path)

        # ---- accepting -------------------------------------------------- #
        #  `missing` never touches a baseline that exists. `changed` rewrites
        #  one only when its check failed — a passing check whose bytes differ
        #  (a re-encode, a subpixel shift the engine calls noise) keeps the
        #  baseline, so accepting two real changes does not produce a pull
        #  request that touches every PNG in the project. `all` is the old
        #  behaviour: anything that differs by a byte is written.
        failed = result.verdict is Verdict.FAIL
        if mode == "all" or (mode == "changed" and failed):
            _refuse_too_large(ctx, final_png, key)
            if _sha_of(final_png) != _sha_of(baseline):
                _warn_unsettled_accept(shot, key)
            if run_canary is _fingerprint.NOT_DRAWN:
                this_run()              # writing a baseline: its sha is kept
                _canary_row(captured, run_canary)
                run_sha = _fingerprint.keep_run(ctx.artifacts_root, run_canary)
            meta = store.put(key, final_png,
                             meta=_passport_for(store, key, run_canary, shot))
            accepted = result.verdict is Verdict.FAIL
            record(verdict="new_baseline",
                   action="unchanged" if meta.sha256 == _sha_of(baseline)
                   else "updated",
                   reason=(f"accepted: {reason}" if accepted
                           else _with(said, "the baseline already matched")),
                   lines=([("reason", f"accepted: {what}"), *notes] if accepted
                          else [("reason", "the baseline already matched"),
                                *(("capture", n) for n in said)]),
                   result=result, limits=limits, images=images,
                   duration_ms=_ms(started), capture=captured,
                   renderer=rend, run_canary=run_sha)
            return _fresh_result(key, meta, Verdict.NEW_BASELINE, notes=said)

        record(verdict="fail" if result.verdict is Verdict.FAIL else "pass",
               action="compared", reason=reason, lines=[("reason", what), *notes],
               result=result, limits=limits, images=images,
               duration_ms=_ms(started), capture=captured,
               renderer=rend, run_canary=run_sha)

        if result.verdict is Verdict.FAIL:
            #  The renderer only for a picture of a page: two files handed in
            #  have no canary, and «renderer: unknown» on every one of them
            #  said nothing (review v1, 4.8).
            raise ScreenshotMismatch.build(
                name=_shown(key), asked=_asked(key), platform=platform, result=result,
                reason=what, notes=notes,
                baseline=baseline_path, actual=actual_path, diff=diff_path,
                report=ctx.report, limits=limits,
                renderer=rend.line() if page else "", accept=ctx.accept_with)
        return result
    except Exception as exc:
        #  `BaselineMissing` and `ScreenshotMismatch` have written their own row
        #  by the time they are raised; one check, one row.
        if not written:
            _record_error(ctx, key, exc, started=started,
                          actual=actual_path, capture=captured)
        raise


# --------------------------------------------------------------------------- #
#  Bits the function above would only make longer
# --------------------------------------------------------------------------- #
def _check_name(name: Any) -> None:
    """The snapshot's name: a relative path inside the baseline directory.

    The store makes any name safe (storage/paths.py) — but quietly: `'..'` was
    cut out, an absolute path lost its root, an empty name became `snapshot`,
    and the message still showed the name as it was given, so the file it named
    was not where it said. These are refused here, by name.
    """
    if not isinstance(name, str):
        raise TypeError(f"expect_screenshot: the name is a string, like 'home.png', "
                        f"not {type(name).__name__}")
    bare = name.strip().removesuffix(".png")
    if not bare.strip(" ./\\"):
        raise ValueError("expect_screenshot: the snapshot's name is empty — give it "
                         "one, like 'home.png'")
    if name.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", name):
        raise ValueError(f"expect_screenshot: {name!r} is an absolute path; the name is "
                         "a path inside the baseline directory, like 'shop/checkout.png'")
    if ".." in re.split(r"[\\/]", name):
        raise ValueError(f"expect_screenshot: {name!r} leads out of the baseline "
                         "directory with '..'; the name is a path inside it, like "
                         "'shop/checkout.png'")


def _shown(key: SnapshotKey) -> str:
    """The name the baseline's file really has: `checkout_step_2.png`."""
    return f"{key.folder}.png"


def _asked(key: SnapshotKey) -> str:
    """The name as it was given, when the file's name differs from it; else ''."""
    given = key.name.replace("\\", "/")
    if not given.endswith(".png"):
        given += ".png"
    return "" if given == _shown(key) else key.name


def _limits(cfg) -> dict:
    """The thresholds this check ran under, as the report and the message say them.

    For v2 `fail_severity` is the threshold it applied — 0 unless a person set
    one, and then `threshold_source` says who — and `max_changed_area_pct`
    the area limit, the default unless a person set one, with `area_source`
    saying which; a preset's numbers are v1's.
    """
    v2 = cfg.engine == "v2"
    out = {"engine": cfg.engine,
           "fail_severity": cfg.v2_threshold if v2 else cfg.fail_severity,
           "max_changed_area_pct": cfg.v2_area_limit if v2 else cfg.max_changed_area_pct}
    if cfg.threshold_source:
        out["threshold_source"] = cfg.threshold_source
    if v2:
        out["area_source"] = cfg.v2_area_source
    return out


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _sha_of(png: bytes) -> str:
    import hashlib

    return hashlib.sha256(png).hexdigest()


def _fresh_result(key: SnapshotKey, meta: SnapshotMeta,
                  verdict: Verdict, notes: list[str] | None = None) -> CompareResult:
    result = CompareResult(name=key.name, verdict=verdict)
    result.size_expected = result.size_actual = (meta.width, meta.height)
    result.notes = list(notes or [])
    return result


def _second_look(shot, key: SnapshotKey, first, expected_rgb, actual_rgb,
                 cfg, ignore, hooks, renderer=None, wait: int = 5000):
    """core/retry.second_look for a live target: (the result, the verdict frame,
    the pixels that kept changing or None)."""
    from ..core import noise
    from ..core.comparator import compare
    from ..core.retry import Later, second_look

    def recapture():
        frames, settled = _targets.frames_after(shot.retake, shot.png,
                                                timeout_ms=max(wait, 1),
                                                calm=shot.calm)
        return Later([pngio.decode(f, source=f"a later screenshot of {key.name}")
                      for f in frames], settled=settled)

    def recompare(frame, live):
        #  The masks are in different frames of reference only when the
        #  sizes differ; `merge_masks` crops to the common part, and
        #  `compare` fits the result to the padded frame.
        wider = live if ignore is None else (
            noise.merge_masks(ignore, live, sticky=True) if live is not None else ignore)
        return compare(expected_rgb, frame, cfg=cfg, name=key.name,
                       ignore_mask=wider, ai_hooks=hooks, renderer=renderer)

    #  Why the page was not ready is in the reason already (the capture's own
    #  words); the second look only says what it did about it.
    look = second_look(first, actual_rgb, recapture, recompare,
                       ready=shot.was_ready())
    return look.result, look.frame, look.live


#: The heads of the notes `core/retry.second_look` leaves.
_LOOK_HEADS = ("Confirmed on a second capture", "A second look was taken",
               "Failed on the first capture and passed on a later one",
               "The page was not ready", "Could not take a second capture")


def _look_notes(result) -> list[str]:
    return [n for n in result.notes if n.startswith(_LOOK_HEADS)]


def _canary_row(captured: dict | None, run) -> None:
    """The cost of the canary, in the capture row of the check that drew it."""
    if captured is not None and run.drawn_ms is not None:
        captured["canary_ms"] = run.drawn_ms


def _passport_for(store, key: SnapshotKey, run, shot) -> SnapshotMeta | None:
    """The passport to write with a new picture: its renderer's canary, how it was taken.

    `None` — the store writes the passport as it always did — when there is
    nothing to add, or the passport already says all of it: an accept that
    changes nothing must not touch the file.
    """
    rec = _fingerprint.keep(store, run)
    taken = _capture_record(shot)
    current = store.meta(key)
    base = current or SnapshotMeta()
    new = base
    if rec is not None and base.renderer != rec:
        new = new.with_(renderer=rec)
    if taken and base.capture != taken:
        new = new.with_(capture=taken)
    return None if new == base else new


def _capture_record(shot) -> dict:
    """How a live page's picture was taken, for its passport: {} for a picture."""
    if shot.retake is None:
        return {}
    out: dict = {"version": CAPTURE_VERSION}
    launch = (shot.facts or {}).get("launch")
    if isinstance(launch, dict):
        out["launch"] = {"headless": launch.get("headless"),
                         "scrollbar_px": int(launch.get("scrollbar_px") or 0)}
    dpr = (shot.facts or {}).get("dpr")
    if isinstance(dpr, (int, float)) and dpr > 0:
        out["device_scale_factor"] = float(dpr)
    window = (shot.facts or {}).get("window")
    if isinstance(window, dict):
        out["window_scroll"] = {"x": int(window.get("x") or 0), "y": int(window.get("y") or 0)}
    place = (shot.facts or {}).get("place")
    if isinstance(place, dict):
        out["place"] = {k: int(place[k]) for k in ("x", "y", "scroll_x", "scroll_y")
                        if k in place}
    return out


def _place_from_passport(ctx, store, target, name: str, platform, scale: str):
    """Where a Locator's element was in the window when its baseline was taken."""
    if _targets.is_page_like(target) or not _targets.is_live(target):
        return None
    try:
        stub = _targets.describe(target, scale)
        key = ctx.key(name, platform if platform is not None else ctx.platform_for(stub))
        passport = store.meta(key)
    except Exception:  # noqa: BLE001 - no place is the old behaviour, not an error
        return None
    if passport is None or not passport.capture:
        return None
    from .page_facts import place

    return place(passport.capture.get("place"))


def _window_from_passport(ctx, store, target, name: str, platform, scale: str):
    """The window scroll of a live page's baseline: {x, y}, or None (nothing recorded)."""
    if not _targets.is_page_like(target) or not _targets.is_live(target):
        return None
    try:
        stub = _targets.describe(target, scale)
        key = ctx.key(name, platform if platform is not None else ctx.platform_for(stub))
        passport = store.meta(key)
    except Exception:  # noqa: BLE001 - no scroll is the old behaviour, not an error
        return None
    if passport is None or not passport.capture:
        return None
    from .page_facts import window_scroll

    return window_scroll(passport.capture.get("window_scroll"))


def _dpr_line(passport, shot) -> str:
    """One line when the baseline and this check had different device scale factors.

    The platform name does not carry it (`chromium-1440x900` is the same for a
    1x and a 2x screen at scale="css"), so a text that is drawn on a different
    grid fails with no sign of why.
    """
    if passport is None or not passport.capture or not shot.facts:
        return ""
    then = passport.capture.get("device_scale_factor")
    now = shot.facts.get("dpr")
    if not isinstance(then, (int, float)) or not isinstance(now, (int, float)):
        return ""
    if abs(float(then) - float(now)) < 1e-6:
        return ""
    return (f"the baseline was taken at device scale factor {float(then):g}, this check at "
            f"{float(now):g}: text and edges are drawn on a different pixel grid — take both "
            "at the same device_scale_factor (the browser context's)")


def _launch_line(passport, shot) -> str:
    """One line when the baseline and this check were taken in different launches."""
    if passport is None or not passport.capture or not shot.facts:
        return ""
    then = passport.capture.get("launch")
    now = shot.facts.get("launch")
    if not isinstance(then, dict) or not isinstance(now, dict):
        return ""
    if then.get("headless") == now.get("headless") and \
            int(then.get("scrollbar_px") or 0) == int(now.get("scrollbar_px") or 0):
        return ""
    from .page_facts import LAUNCH_HEADS

    def said(x):
        bar = int(x.get("scrollbar_px") or 0)
        return (f"{LAUNCH_HEADS.get(x.get('headless'), LAUNCH_HEADS[None])}, "
                + (f"{bar} px scroll bars" if bar else "no scroll bars"))

    return (f"the baseline was taken in a browser {said(then)}, this check in one "
            f"{said(now)}: take both the same way")


#: How far around the element under the pointer or in focus a failing region
#: still counts as its own: a hover's tooltip, a focus ring, an underline.
NEAR_PX = 16


#: Elements that are the page rather than a thing on it: under the pointer is
#: no information when it is one of these.
_WHOLE_PAGE = {"html", "body", "#root", "#app", "#__next", "#__nuxt", "#app-root", "#main"}
#: A box this much of the picture, in both directions, is a full-window container.
_WHOLE_SHARE = 0.9


def _concrete(item: dict, size: tuple[int, int] | None) -> bool:
    """A pointer or focus element that is a thing on the page, not the page itself.

    Not `html`, `body`, `#root` and its like, and not a box that covers (nearly)
    the whole picture: `#root` under the pointer is true of every pointer position.
    """
    sel = str(item.get("sel") or "").strip()
    last = sel.split(">")[-1].strip().split(":")[0]
    if not sel or sel.lower() in _WHOLE_PAGE or last.lower() in _WHOLE_PAGE:
        return False
    box = item.get("box")
    if size and box and size[0] > 0 and size[1] > 0:
        if box[2] >= _WHOLE_SHARE * size[0] and box[3] >= _WHOLE_SHARE * size[1]:
            return False
    return True


def _late_names(shot) -> list[str]:
    """What was still arriving after the picture, by name; [] when nothing or unknown."""
    if shot.late is None:
        return []
    try:
        return [n for n in shot.late() if not n.startswith("image data:")]
    except Exception:  # noqa: BLE001 - naming is help, not the verdict
        return []


def _failure_hints(shot, result, live, passport, away: bool, blur: bool,
                   accept: str = "pytest --vistest-update") -> list[str]:
    """What the page can tell about a failure: the element to blame, what to do."""
    out: list[str] = []
    facts = shot.facts or {}
    regions = list(result.regions)
    size = getattr(result, "size_actual", None)
    for what, words, off, switch in (
            ("hover", "is under the pointer, kept there by keep_pointer", away,
             "leave keep_pointer off so that the pointer is moved away before the picture"),
            ("focus", "has the focus", blur,
             "pass blur_focus=True (or capture.blur_focus: true in vistest.yaml) to take "
             "the focus off before the picture")):
        if off:
            continue
        item = facts.get(what)
        if not isinstance(item, dict) or not item.get("sel") or not _concrete(item, size):
            continue
        x, y, w, h = item.get("box") or (0, 0, 0, 0)
        near = (x - NEAR_PX, y - NEAR_PX, w + 2 * NEAR_PX, h + 2 * NEAR_PX)
        if any(_overlaps(near, (r.x, r.y, r.w, r.h)) for r in regions):
            out.append(f"the change is at {item['sel']}, which {words}: {switch}")
    #  Only for a page that did change after the picture (the second look says
    #  so, or the frames never held): an unrelated beacon that ended after the
    #  shot of a page that did not move is not the reason for anything.
    changing = shot.stability.stable is False or any(
        n.startswith(("A second look was taken",
                      "Failed on the first capture and passed on a later one"))
        for n in result.notes)
    late = _late_names(shot) if changing else []
    named = list(shot.moving)
    if live is not None and shot.namer is not None:
        from .targets import name_parts

        named += name_parts(shot.namer, live)
    seen = set()
    for item in named:
        sel = item.get("sel")
        if not sel or sel in seen:
            continue
        seen.add(sel)
        if late:
            #  Something was still on its way after the picture: that is what
            #  changed it, and a mask would only hide the next one.
            shown = ", ".join(late[:2])
            out.append(f"{sel} changed after the picture because {shown} "
                       "was still on its way: wait for it in the test (the element it "
                       "fills, or the response) before the check — a mask would hide it")
        elif item.get("tag") == "canvas":
            out.append(f"{sel} is a canvas a script redraws on every frame — "
                       'animations="disabled" stops CSS animations, not this: if it is '
                       f'meant to move, mask it: expect_screenshot(..., mask=["{sel}"])')
        else:
            out.append(f"{sel} changes by itself, and no request or picture was on its "
                       "way: if it is meant to, mask it — "
                       f'expect_screenshot(..., mask=["{sel}"]) — or mark it '
                       'data-vistest="ignore"')
        if len(seen) >= 2:
            break
    if late and not seen:
        out.append(f"after the picture {', '.join(late[:2])} was still on its way and the "
                   "page changed: wait for it in the test before the check")
    if shot.retake is not None and (passport is None or not passport.capture
                                    or int(passport.capture.get("version", 1))
                                    < CAPTURE_VERSION):
        out.append(old_way(accept, CAPTURE_VERSION))
    return out


def _overlaps(a, b) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def _with(said: list[str], reason: str) -> str:
    """The reason, followed by whatever the capture had to say."""
    return "; ".join([reason, *said]) if said else reason


def _capture_row(shot) -> dict:
    """How the picture was taken, for the report row."""
    row = {"source": shot.source, **shot.stability.as_dict()}
    if shot.ready is not None:
        row["ready"] = shot.ready.as_dict()
    if shot.scale_mode:
        row["scale"] = shot.scale_mode
        if shot.scale is not None:
            row["pixel_ratio"] = shot.scale
    return row


def _warn_unsettled_accept(shot, key: SnapshotKey) -> None:
    """Accepting a frame of a page that would not hold still, or was not ready, is
    allowed — and said.

    The next run takes its own frames of the same moving page, and the
    baseline written now is one arbitrary moment of it. That is a red run
    waiting to happen, and the person pressing «accept» is the one who can
    still do something about it. The same goes for a page a readiness step
    gave up on: a baseline of «Loading…» passes against every later
    «Loading…» and says nothing about the page.
    """
    import warnings

    text = shot.stability.unsettled_text()
    if text:
        warnings.warn(f"vistest: {key.as_str()}: {text}. It was accepted as the "
                      "baseline anyway; the next run will likely disagree with it. "
                      "Stop what moves on the page (or mask it) and accept again.",
                      VisTestWarning, stacklevel=4)
    ready = getattr(shot, "ready", None)
    if ready is not None and ready.ok is False:
        warnings.warn(f"vistest: {key.as_str()}: {ready.text()}. It was accepted as "
                      "the baseline anyway, and a picture of a page that had not "
                      "finished is what every later check will be held to. Wait for "
                      "the page in the test (or raise capture.ready_timeout_ms) and "
                      "accept again.", VisTestWarning, stacklevel=4)


def _refuse_too_large(ctx, png: bytes, key: SnapshotKey) -> None:
    """A baseline the engine could never compare against is not written.

    `--vistest-update` used to accept a 90-megapixel picture, and every later
    check of it failed with `ImageTooLarge`: the limit is said at the accept,
    where the decision is still open, with nothing written.
    """
    from ..core.comparator import ImageTooLarge

    limit = int(getattr(ctx.config.diff, "max_pixels", 0) or 0)
    if limit <= 0:
        return
    width, height = pngio.dimensions(png, source=f"the screenshot of {key.name}")
    if width * height <= limit:
        return
    def amount(pixels: int) -> str:
        return f"{pixels / 1_000_000:.1f} Mpx" if pixels >= 100_000 else f"{pixels} px"

    raise ImageTooLarge(
        f"{key.as_str()}: the picture is {width}×{height} = {amount(width * height)}, "
        f"over the engine's limit of {amount(limit)}: as a baseline it could never "
        "be compared, so it "
        "was not written. Capture a smaller area (an element instead of the whole "
        "page), or raise VISTEST_ENGINE_MAX_PIXELS deliberately.")


def _scale_hint(shot, result) -> str:
    """The one size change that has an explanation we can give.

    A baseline exactly two (or three) times the size of the screenshot on both
    axes, while the screenshot was taken in CSS pixels, is a baseline taken at
    device scale on a HiDPI screen — which is what the library did before
    `scale="css"` became the default. Saying so turns «the picture changed
    size» into something a person can act on.
    """
    if shot.scale_mode != "css" or not result.size_changed:
        return ""
    (ew, eh), (aw, ah) = result.size_expected, result.size_actual
    if not (aw and ah) or ew % aw or eh % ah or ew // aw != eh // ah \
            or ew // aw < 2:
        return ""
    k = ew // aw
    return (f"the baseline is exactly {k}x the size of this screenshot — it was "
            f"probably taken at device scale on a {k}x screen, before "
            "screenshots were taken in CSS pixels by default. Accept it again "
            "with --vistest-update, or pass scale=\"device\" to keep device "
            "pixels")


def _ai_hooks(ctx):
    """Attribution and installed plugins if available, nothing if not.

    Optional parts: a plugin may be missing, broken or switched off. None of
    that is a reason to stop somebody's test run, so it warns once and the
    cascade runs without it — the verdict is then the engine's own, which is
    what it always was. `fail_on` comes from the context, where the pytest flag
    has already been folded over `vistest.yaml`.
    """
    try:
        from ..ai.pipeline import AIPipeline
    except ImportError:
        return None
    try:
        from ..plugins.loader import ensure_loaded

        plugins = ctx.plugins_config
        ensure_loaded(options=dict(plugins.options),
                      disabled=_disabled(plugins))
        return AIPipeline(ctx.config.ai, plugins=plugins)
    except Exception as e:  # pragma: no cover - depends on optional models
        import warnings

        warnings.warn(f"vistest: the AI layer is not available ({e}); "
                      "comparing without it", VisTestWarning, stacklevel=4)
        return None


def _disabled(plugins) -> frozenset:
    if plugins.enabled:
        return frozenset(plugins.disabled)
    from ..plugins.loader import installed_names

    return frozenset(installed_names())


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
                      VisTestWarning, stacklevel=4)
        return None


def _record(ctx, key: SnapshotKey, *, verdict: str, action: str, reason: str,
            images: dict, duration_ms: int, result=None,
            limits: dict | None = None, capture: dict | None = None,
            renderer=None, run_canary: str = "", lines=None) -> None:
    """One row for the report, written as this process's own file.

    `run_canary` is the sha256 of the canary this check drew, kept under
    `.vistest/renderers/` (`fingerprint.keep_run`), or '' when it drew none.
    """
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
    if lines:
        #  The reason the way the message says it: one thing per line, under
        #  its label — `reason` above is the same, joined, for whoever reads
        #  the JSON.
        entry["lines"] = [[label, text] for label, text in lines]
    if capture:
        entry["capture"] = capture
    if renderer is not None:
        entry["renderer"] = {**renderer.as_dict(), "line": renderer.line(),
                             **({"run_sha256": run_canary} if run_canary else {})}
    if result is not None:
        entry["metrics"] = {
            "max_severity": round(float(result.max_severity), 2),
            "changed_area_pct": round(float(result.changed_area_pct), 4),
            "region_area_pct": round(float(result.region_area_pct), 4),
            "unassigned_pixels": int(result.unassigned_pixels),
            "regions_total": len(result.regions),
            "ssim_global": round(float(result.ssim_global), 6),
            "de_mean": round(float(result.de_mean), 4),
        }
        entry["size"] = {"expected": list(result.size_expected),
                         "actual": list(result.size_actual),
                         "changed": bool(result.size_changed)}
        entry["regions"] = [_region_row(r) for r in list(result.regions)[:12]]
        #  Everything set aside, counted in full and listed up to a limit. The
        #  pytest summary adds these counts up across the run: a difference
        #  the engine decided not to count is still reported.
        from ..plugins.runtime import count_suppressed

        suppressed = list(result.suppressed)
        entry["suppressed_count"] = len(suppressed)
        entry["suppressed_by_reason"] = count_suppressed(suppressed)
        entry["suppressed"] = [_region_row(r) for r in suppressed[:MAX_LISTED]]
        #  What a threshold a person set let through (engine v2): listed, with
        #  the sentence the failure message and the API carry.
        if result.threshold is not None:
            entry["threshold"] = dict(result.threshold)
            entry["below_threshold"] = [_region_row(r)
                                        for r in list(result.below_threshold)[:MAX_LISTED]]
            entry["below_line"] = _words.below(result)
    try:
        write_part(ctx.parts_dir, entry)
    except OSError as e:  # pragma: no cover - a report row is not the verdict
        import warnings

        warnings.warn(f"vistest: could not write the report entry for "
                      f"{key.name} ({e})", VisTestWarning, stacklevel=4)


def _record_error(ctx, key: SnapshotKey, exc: BaseException, *, started: float,
                  actual: Path | None, capture: dict | None) -> None:
    """The row of a check that raised before it reached a verdict.

    Written so that the check is counted and its name is seen by the collision
    check; the exception itself goes on to the test unchanged. Nothing here may
    raise in its place: a failure to write this row is a warning, and the
    caller re-raises the original error either way.
    """
    try:
        _record(ctx, key, verdict="error", action="error",
                reason=f"the check did not finish: {type(exc).__name__}: {exc}",
                images={"actual": str(actual)} if actual is not None else {},
                duration_ms=_ms(started), capture=capture)
    except Exception as e:  # pragma: no cover - the original error matters more
        import warnings

        warnings.warn(f"vistest: could not write the report entry for "
                      f"{key.name} ({type(e).__name__}: {e})",
                      VisTestWarning, stacklevel=4)


#  Suppressed regions listed per check in the report. The count is never cut.
MAX_LISTED = 50


def _region_row(r) -> dict:
    return {"kind": getattr(r.kind, "value", str(r.kind)),
            "severity": round(float(r.severity), 2),
            "x": r.x, "y": r.y, "w": r.w, "h": r.h,
            "selector": r.selector or "",
            "score": r.score,
            "annotations": list(r.annotations or []),
            "suppressed_by": r.suppressed_by}


def ctx_nodeid() -> str:
    """The test this check belongs to, when pytest is the one running us."""
    import os

    return os.environ.get("PYTEST_CURRENT_TEST", "").split(" (")[0]


#  pytest: the library's own verdicts and refusals are shown without its
#  frames (library/errors.py, hide_for_verdicts).
__tracebackhide__ = hide_for_verdicts
