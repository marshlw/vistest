# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Intercepting the comparison inside someone else's tests.

The plugin attaches to their pytest from the outside:

    pytest -p vistest.adapter ...

and replaces exactly one method — the one the project uses to compare a
screenshot against a baseline. Everything else keeps running on their code:
conftest, fixtures, login, waits, markers, parametrization. We do not and
should not know what happens in there.

Why replacement rather than parsing the code. The steps of someone else's
test are arbitrary Python with inheritance, fixtures and conditionals; you
cannot understand them statically. But every visual test has one shared
point: the moment the picture is compared against a baseline. That is what
gets intercepted.

What happens inside the replaced method:

1. the page is captured by their own driver — through `capture`, that is,
   with our stabilization, a series of frames and a DOM snapshot;
2. the comparison is done by the VisTest engine, the baseline is read from
   their project folder;
3. the result goes into the run: severity, regions, classes, artifacts;
4. on a mismatch an `AssertionError` is raised — their test goes red exactly
   as it would from their own check.

Configured through environment variables, because that is the only channel
that survives launching someone else's pytest in a separate process:

    VISTEST_ADAPTER_TARGET     UiTests.pages.base_page.BasePage.assert_screenshot
    VISTEST_ADAPTER_BASELINES  the project baseline directory
    VISTEST_ADAPTER_SIDECAR    the VisTest service data directory
    VISTEST_ADAPTER_RUN        the run directory (artifacts and result.json)
    VISTEST_ADAPTER_NAME_ARG   position of the argument holding the snapshot name (default 0)
    VISTEST_ADAPTER_PAGE_ATTR  the object attribute where the page lives
    VISTEST_ADAPTER_UPDATE     1 — accept the current state as the baseline
"""

from __future__ import annotations

import functools
import json
import os
import re
import time
from pathlib import Path

_STATE: dict = {
    "results": [],
    "patched": "",
    "patched_classes": [],
    "errors": [],
    # Why the run is accounted for test by test as well as snapshot by
    # snapshot. A snapshot lands in the report only if the comparison was
    # actually reached. A test that dies earlier — a timeout on a click, a
    # broken fixture, a login that did not go through — leaves no snapshot, and
    # the report then shows «2 checked» for eleven tests that ran with no hint
    # of where the other nine went. That gap is the difference between «the
    # tests did not start» and «the tests did not get as far as the screenshot»,
    # and only the second one is true.
    "collected": 0,
    "tests": [],
}


class MissingBaseline(RuntimeError):
    """There is no baseline to compare against, and creating one is not allowed."""


def _reset_state() -> None:
    _STATE.update({"results": [], "patched": "", "patched_classes": [],
                   "errors": [], "collected": 0, "tests": []})


# --------------------------------------------------------------------------- #
#  Attaching to pytest
# --------------------------------------------------------------------------- #
def pytest_configure(config) -> None:
    _reset_state()
    target = os.getenv("VISTEST_ADAPTER_TARGET", "").strip()
    if not target:
        return
    try:
        owners = _patch(target)
        _STATE["patched"] = target
        _STATE["patched_classes"] = owners
        if len(owners) > 1:
            print(f"[vistest] interception set on {len(owners)} classes: "
                  + ", ".join(owners))
    except Exception as e:
        # Interception failed — that is bad, but we must not crash someone
        # else's run: the person was running tests, not our adapter. Say it
        # loudly and step aside.
        _STATE["errors"].append(f"interception of {target} failed: {type(e).__name__}: {e}")
        print(f"[vistest] WARNING: {_STATE['errors'][-1]}")
        print("[vistest] Tests will use their own comparison, no VisTest analysis will run.")


def pytest_collection_modifyitems(session, config, items) -> None:
    """How many of their tests actually go into the run.

    Taken from pytest itself rather than parsed out of the output: markers,
    deselection and `-k` have already been applied here, so this is the real
    number to compare the snapshot count against.
    """
    _STATE["collected"] = len(items)


def pytest_runtest_logreport(report) -> None:
    """Outcome of every test — so the report can explain a missing snapshot.

    Only the phase that decided the outcome is recorded: a failed setup means
    the test never ran, a failed call means it ran and broke somewhere. Passing
    tests are kept too — without them «11 ran, 2 checked» is impossible to
    interpret.
    """
    if report.when == "call" or (report.when == "setup" and report.outcome != "passed"):
        entry = {"id": report.nodeid, "phase": report.when,
                 "outcome": report.outcome}
        if report.outcome == "failed":
            text = str(getattr(report, "longrepr", ""))
            # One line of cause, not the whole traceback: the traceback is
            # already in the run output as-is, and adapter.json must not become
            # a second copy of it.
            entry["error"] = _exception_line(text)
            # Arguments that arrived empty. Nine identical setup failures with
            # `user_pass = None` in the header are not nine broken tests, they
            # are one variable that did not reach the run — and that is worth
            # saying in those words instead of leaving it in the traceback.
            empty = _empty_arguments(text)
            if empty:
                entry["empty_args"] = empty
        _STATE["tests"].append(entry)


def _exception_line(longrepr: str) -> str:
    """The line pytest itself marked as the cause (`E   ...`)."""
    lines = [ln for ln in longrepr.splitlines() if ln.strip()]
    marked = [ln for ln in lines if ln.startswith("E ")]
    if marked:
        return marked[-1][1:].strip()
    return lines[-1].strip()[:300] if lines else ""


_ARG_RE = re.compile(r"\b([A-Za-z_]\w*) = None\b")


def _empty_arguments(longrepr: str) -> list[str]:
    """Fixtures of the failing test that arrived as `None`.

    Only the argument header of the outermost frame is read — the one pytest
    prints before the test/fixture source. Deeper frames belong to library code
    (`value = None, timeout = None` inside Playwright) and say nothing about the
    project.
    """
    head: list[str] = []
    for line in longrepr.splitlines():
        if line.startswith("    ") and line.lstrip().startswith(("@", "def ", "async def ")):
            break
        head.append(line)
    seen: dict[str, None] = {}
    for name in _ARG_RE.findall("\n".join(head)):
        seen.setdefault(name, None)
    return list(seen)


def pytest_sessionfinish(session, exitstatus) -> None:
    """Write the run summary next to the artifacts — the service reads it."""
    out = os.getenv("VISTEST_ADAPTER_RUN")
    if not out:
        return
    path = Path(out)
    path.mkdir(parents=True, exist_ok=True)
    payload = {
        "patched": _STATE["patched"],
        "patched_classes": _STATE["patched_classes"],
        "errors": _STATE["errors"],
        "exitstatus": int(exitstatus),
        "collected": _STATE["collected"],
        "tests": _STATE["tests"],
        "results": _STATE["results"],
    }
    try:
        text = json.dumps(payload, indent=2, ensure_ascii=False, default=_jsonable)
    except Exception as e:      # pragma: no cover — a last line of defence
        # Losing the whole report because one number did not serialize is the
        # worst possible outcome: the service would report «the adapter left no
        # report» and the person would go looking for a launch problem that does
        # not exist. Better a report without results than no report at all.
        text = json.dumps({
            "patched": _STATE["patched"],
            "errors": _STATE["errors"] + [f"report not serialized: {e}"],
            "exitstatus": int(exitstatus),
            "collected": _STATE["collected"],
            "results": [],
        }, indent=2, ensure_ascii=False)
    (path / "adapter.json").write_text(text, encoding="utf-8")


def _jsonable(value):
    """numpy scalars and anything else exotic → a plain number or a string."""
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return item()
        except Exception:
            pass
    return str(value)


# --------------------------------------------------------------------------- #
#  Replacement
# --------------------------------------------------------------------------- #
_PATCHED = "__vistest_patched__"


def _patch(target: str) -> list[str]:
    """`pkg.mod.Class.method` → a wrapper around the method.

    Subclasses that override the method are patched too. Otherwise a page object
    with its own `assert_screenshot` slips past the interception without a
    single error: its tests run their own comparison, are timed by their own
    clock and simply never appear in the VisTest report — which from the outside
    looks exactly like «the tests did not start».
    """
    import importlib

    parts = target.split(".")
    method_name = parts[-1]
    for split in range(len(parts) - 1, 0, -1):
        module_name = ".".join(parts[:split])
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        obj = module
        for attr in parts[split:-1]:
            obj = getattr(obj, attr)

        patched = [_patch_one(obj, method_name)]
        if isinstance(obj, type):
            patched += _patch_overrides(obj, method_name)
        return [name for name in patched if name]

    raise ImportError(
        f"could not import {target}. Check that the path is given from the "
        "project root and that the root is on sys.path (usually the pytest "
        "rootdir).")


def _patch_one(owner, method_name: str) -> str:
    original = getattr(owner, method_name)
    if getattr(original, _PATCHED, False):
        return ""                       # already ours — do not wrap twice
    setattr(owner, method_name, _wrap(original, method_name))
    return getattr(owner, "__name__", str(owner))


def _patch_overrides(base: type, method_name: str) -> list[str]:
    """Every subclass that declares the method itself, recursively."""
    out: list[str] = []
    for sub in base.__subclasses__():
        if method_name in vars(sub):
            name = _patch_one(sub, method_name)
            if name:
                out.append(name)
        out += _patch_overrides(sub, method_name)
    return out


def _wrap(original, method_name: str):
    @functools.wraps(original)
    def wrapper(self, *args, **kwargs):
        name = _snapshot_name(args, kwargs)
        if not name:
            _STATE["errors"].append(
                f"{method_name}: could not work out the snapshot name from the "
                f"call arguments (name_arg="
                f"{os.getenv('VISTEST_ADAPTER_NAME_ARG', '0')}). "
                "The project comparison ran, VisTest saw nothing.")
            return original(self, *args, **kwargs)
        try:
            return _check(self, name, kwargs)
        except AssertionError:
            raise                       # a regression is their failure, let it pass
        except Exception as e:
            note = f"{name}: interception did not work ({type(e).__name__}: {e})"
            _STATE["errors"].append(note)
            print(f"[vistest] {note}")
            # The snapshot goes into the report even when we failed on it. A
            # silently dropped snapshot is the worst kind of result: the run
            # looks clean, only shorter, and nothing points at the cause.
            _record_error(name, note)
            if os.getenv("VISTEST_ADAPTER_PASSTHROUGH", "1") == "1":
                print("[vistest] calling the project original check")
                return original(self, *args, **kwargs)
            raise

    setattr(wrapper, _PATCHED, True)
    return wrapper


def _record_error(name: str, message: str) -> None:
    from .models import CompareResult, Verdict

    res = CompareResult(name=name, verdict=Verdict.ERROR)
    res.notes = [message]
    entry = res.to_dict()
    entry.update({"error": message, "actual": "", "max_severity": 0.0,
                  "changed_area_pct": 0.0, "ssim": 1.0,
                  "region_count": 0, "suppressed_count": 0})
    _STATE["results"].append(entry)


def _snapshot_name(args, kwargs) -> str:
    try:
        idx = int(os.getenv("VISTEST_ADAPTER_NAME_ARG", "0"))
    except ValueError:
        idx = 0
    for key in ("name", "snapshot", "filename", "screenshot_name"):
        if key in kwargs:
            return str(kwargs[key])
    if 0 <= idx < len(args) and isinstance(args[idx], str):
        return args[idx]
    # A wrong position in the settings should not cost the whole run: if there
    # is exactly one string among the arguments, it is the name.
    strings = [a for a in args if isinstance(a, str)]
    return strings[0] if len(strings) == 1 else ""


# --------------------------------------------------------------------------- #
#  Check by the VisTest engine
# --------------------------------------------------------------------------- #
def _check(owner, name: str, kwargs: dict):
    from . import source as _source
    from .config import VisTestConfig
    from .integrations.driver import wrap_driver
    from .service import CheckService

    page = _find_page(owner)
    if page is None:
        raise RuntimeError(
            f"could not find the page on {type(owner).__name__}. Set the "
            "attribute in the project settings (page_attr).")

    cfg = VisTestConfig.load()
    # We respect the masks from their call: the team did not write them out of boredom.
    extra_masks = tuple(kwargs.get("mask_selectors") or ())
    if extra_masks:
        from dataclasses import replace

        cfg.capture = replace(
            cfg.capture,
            mask_selectors=tuple(cfg.capture.mask_selectors) + extra_masks)

    store = _make_store()
    run_dir = Path(os.getenv("VISTEST_ADAPTER_RUN", ".vistest/runs/external"))
    service = CheckService(cfg, run_dir=run_dir, store=store)

    update = os.getenv("VISTEST_ADAPTER_UPDATE") == "1"
    # A run against the project baselines must not write into the project
    # baselines. `CheckService` creates a missing baseline on the spot — right
    # for our own set, unacceptable for theirs: an unnoticed PNG would appear in
    # someone else's repository, in someone else's git diff, and the snapshot
    # would be «green» purely because we had just written it ourselves.
    if not update and _store_kind() == "project" and not store.exists(name):
        raise MissingBaseline(
            f"no baseline {name} in {os.getenv('VISTEST_ADAPTER_BASELINES', '')}. "
            "VisTest does not create baselines in the project repository: either "
            "add the file to the project, or run against the VisTest baselines.")

    started = time.perf_counter()
    driver = wrap_driver(page)
    shot = driver.capture(cfg=cfg.capture)
    res = service.check(
        name, shot.rgb,
        unstable=shot.unstable, dom=shot.dom, notes=list(shot.notes),
        update_baseline=update or None,
        # Чужой тест остановился ровно на этой странице и ждёт нашего ответа —
        # второй кадр снимается тем же их драйвером и им ничего не стоит.
        recapture=lambda: driver.capture(cfg=cfg.capture).rgb,
        # `source` — тот же паспорт, что пишет наш раннер. Здесь он особенно
        # важен: чужой тест умеет логиниться, готовить данные и ходить по
        # страницам, а мы про это не знаем ничего и знать не должны. Запомнить,
        # КАКОЙ тест снял снимок, — единственный способ потом его перепроверить,
        # не пытаясь повторить логин своими силами.
        meta={"external": True,
              "source_method": os.getenv("VISTEST_ADAPTER_TARGET", ""),
              "source": _source.detect()},
    )

    # We always save the captured frame, not only on failure. A snapshot that
    # passed is a result too: it shows exactly what was accepted as the norm,
    # and without it the history keeps a «pass» line without a single picture.
    try:
        from .capture.playwright_capture import _write_png
        from .service import slug

        out = run_dir / slug(name)
        out.mkdir(parents=True, exist_ok=True)
        _write_png(out / "actual.png", shot.rgb)
        res.artifacts["actual"] = str(out / "actual.png")
    except Exception as e:      # an artifact is not more important than the run
        _STATE["errors"].append(f"{name}: did not save actual.png ({e})")

    _STATE["results"].append(_entry(res, started))

    if res.failed:
        raise AssertionError(res.summary())
    return res


def _entry(res, started: float) -> dict:
    """The comparison result for the report.

    We store the full `to_dict()` — with regions, metrics and artifacts. It is
    needed so that the external project run lands in the shared history and
    opens in the same viewer as our own: with the curtain, onion, blink and
    the region table. The flat fields alongside are there so the table on the
    «Projects» tab reads without unpacking the nesting.
    """
    entry = res.to_dict()
    metrics = entry.get("metrics") or {}
    entry.update({
        "actual": res.artifacts.get("actual", ""),
        "max_severity": metrics.get("max_severity", 0.0),
        "changed_area_pct": metrics.get("changed_area_pct", 0.0),
        "ssim": metrics.get("ssim_global", 1.0),
        "region_count": len(entry.get("regions") or []),
        "suppressed_count": len(entry.get("suppressed") or []),
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
    })
    return entry


def _make_store():
    """The baseline store for this run.

    `project` — the project folder: the baseline is read and overwritten in
    place, the approval lands in their git. Suitable when the snapshots were
    taken in a compatible way.

    `vistest` — our own set, taken by our capture. It is needed because their
    baselines were taken by their pipeline: their waits, their screenshot
    parameters, their full-page assembly. Our capture freezes animations until
    the first render, substitutes the sources of time and randomness and takes
    a series of frames. Comparing a snapshot from one pipeline against a
    baseline from another is pointless — the fonts, shadows and the moment of
    capture will diverge, and the engine will honestly show mismatches that do
    not exist in the application.
    """
    baselines = os.environ["VISTEST_ADAPTER_BASELINES"]
    if _store_kind() == "vistest":
        from .storage import FileBaselineStore

        return FileBaselineStore(baselines)

    from .storage import ExternalBaselineStore

    return ExternalBaselineStore(baselines,
                                 os.environ["VISTEST_ADAPTER_SIDECAR"])


def _store_kind() -> str:
    return "vistest" if os.getenv("VISTEST_ADAPTER_STORE") == "vistest" else "project"


def _find_page(owner):
    """Fetch the page driver from the object the comparison was called on.

    In a page object it is almost always `self.page`, but `self.driver`,
    `self._page` and nesting through a base page also occur. We try the known
    variants before giving up.
    """
    preferred = os.getenv("VISTEST_ADAPTER_PAGE_ATTR", "page")
    names = [preferred, "page", "_page", "driver", "browser", "wd"]

    for attr in names:
        obj = getattr(owner, attr, None)
        if _is_driver(obj):
            return obj
    # One level deeper: self.base.page, self.page_object.page…
    for attr in ("base", "base_page", "page_object", "ctx", "context"):
        holder = getattr(owner, attr, None)
        if holder is None:
            continue
        for inner in names:
            obj = getattr(holder, inner, None)
            if _is_driver(obj):
                return obj
    return owner if _is_driver(owner) else None


def _is_driver(obj) -> bool:
    if obj is None:
        return False
    return (hasattr(obj, "screenshot") and hasattr(obj, "evaluate")) or (
        hasattr(obj, "get_screenshot_as_png") and hasattr(obj, "execute_script"))
