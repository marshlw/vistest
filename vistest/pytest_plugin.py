# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""The pytest plugin.

    from vistest import expect_screenshot

    def test_home(page):
        page.goto("https://example.com")
        expect_screenshot(page, "home.png")

Registered through the `pytest11` entry point, which means this module is
imported in **every** pytest run of every project that has vistest installed —
including the ones that never take a screenshot. That is why nothing heavy is
imported at the top of this file. It used to import the config loader and the
runner here, and the runner pulls the capture layer, the artifact renderer and
the service client; a suite that only wanted its own tests to run paid for all
of it. Everything below imports what it needs inside the function that needs
it.

**Under `pytest -n` (xdist)** the work is split across processes and nothing is
shared between them. Each worker writes its own artifacts and its own one-file
report rows; the report itself is assembled at the end, in the controller,
from whatever is on disk. `pytest_sessionfinish` runs in the workers too, so
every hook here that must happen once checks for `workerinput` first — without
that the report is built once per worker, each time from a partial run.
"""

from __future__ import annotations

import shutil
from contextlib import contextmanager
from pathlib import Path

import pytest

#  CLI flag -> ini key. Both exist because they answer different questions:
#  where the baselines live is a property of the project and belongs in its
#  `pyproject.toml`; whether this particular run updates them is a property of
#  the run and belongs on the command line.
_INI = {
    "vistest_baselines": "Directory holding the committed baselines "
                         "(default: tests/__vistest__)",
    "vistest_platform": "Platform directory for baselines: browser and window "
                        "size, e.g. chromium-1440x900. Empty — taken from the "
                        "page under test.",
    "vistest_report": "Where to write the HTML report "
                      "(default: .vistest/report/index.html)",
}


def pytest_addoption(parser):
    group = parser.getgroup("vistest", "Visual testing")
    group.addoption("--vistest-update", action="store_true",
                    help="Accept the current screenshots as the baselines")
    group.addoption("--vistest-baselines", default=None, metavar="PATH",
                    help="Directory with the committed baselines "
                         "(default: tests/__vistest__)")
    #  `--vistest-platform`, and no `--vistest-profile` alias. A profile in
    #  this package is the rules for reading somebody else's snapshot file
    #  names (`NamingProfile`, `SuiteProfile`); a third meaning for the word,
    #  in the public flag of a library, would be paid for by everyone who reads
    #  the two together. The name is changed now because after publication it
    #  cannot be.
    group.addoption("--vistest-platform", default=None,
                    dest="vistest_platform", metavar="NAME",
                    help="Platform directory for baselines, e.g. "
                         "chromium-1440x900. Default: taken from the page.")
    group.addoption("--vistest-report", default=None, metavar="PATH",
                    help="Where to write the HTML report "
                         "(default: .vistest/report/index.html)")
    group.addoption("--vistest-preset", default=None,
                    choices=["strict", "balanced", "loose"],
                    help="Preset of comparison thresholds")
    group.addoption("--vistest-config", default=None,
                    help="Path to vistest.yaml")
    group.addoption("--vistest-api", default=None,
                    help="URL of the VisTest service")
    group.addoption("--vistest-perceptual", action="store_true",
                    help="Enable the perceptual ONNX model")

    for name, help_text in _INI.items():
        parser.addini(name, help_text, default="")


def _setting(config, option: str, ini: str) -> str:
    """Command line first, then the project's ini. Precedence, once."""
    value = config.getoption(option, None)
    if value:
        return str(value)
    return str(config.getini(ini) or "").strip()


def _is_worker(config) -> bool:
    """True in an xdist worker process, false in the controller and without xdist."""
    return hasattr(config, "workerinput")


# --------------------------------------------------------------------------- #
#  Advisory work
#
#  Every hook this module registers runs inside somebody else's test run, and
#  most of what they do is decoration: clearing yesterday's rows, assembling a
#  report, printing a summary. An exception out of any of them is not a failed
#  test — pytest turns it into INTERNALERROR and the session dies where it
#  stands, with the remaining tests unreported and no report written. That is
#  the shape of the outage this wrapper exists to prevent: eleven tests, one
#  screenshot legitimately different, and the run ends at the fifth.
#
#  So the rule the package already follows for optional parts applies to its
#  own hooks first: an optional piece failing is a warning and the run carries
#  on. The one deliberate exception is a broken configuration, which is raised
#  from `pytest_configure` on purpose — a suite running under thresholds
#  nobody chose is worse than a suite that refuses to start, and it fails
#  before any test has run rather than in the middle.
# --------------------------------------------------------------------------- #
@contextmanager
def _advisory(what: str):
    """Do this, or warn about it — but never take the session down."""
    try:
        yield
    except Exception as exc:          # noqa: BLE001 - deliberately everything
        import warnings

        from .library.errors import VisTestWarning

        warnings.warn(f"vistest: {what} ({exc!r}). The tests themselves are "
                      "unaffected.", VisTestWarning, stacklevel=3)


# --------------------------------------------------------------------------- #
#  Wiring
# --------------------------------------------------------------------------- #
def pytest_configure(config):
    config.addinivalue_line("markers", "visual: test contains visual checks")

    from .library import context as _context

    root = Path(config.rootpath)
    baselines = _setting(config, "vistest_baselines", "vistest_baselines")
    report = _setting(config, "vistest_report", "vistest_report")

    ctx = _context.LibraryContext(
        root=root,
        baselines=(root / baselines) if baselines else None,
        report=(root / report) if report else None,
        platform_override=_setting(config, "vistest_platform",
                                   "vistest_platform"),
        update=bool(config.getoption("--vistest-update")),
        config_path=config.getoption("--vistest-config"),
        preset=config.getoption("--vistest-preset"),
    )
    _context.install(ctx)
    config._vistest_context = ctx

    if not _is_worker(config):
        #  Yesterday's rows are not this run's report. Cleared here rather than
        #  at session start because under xdist the controller configures
        #  before any worker exists — clearing later would race the workers
        #  that have already begun writing.
        #
        #  Advisory: a directory that will not delete (a file open in a viewer,
        #  a permission) costs a stale row in the report, not the run.
        with _advisory(f"could not clear {ctx.parts_dir}"):
            shutil.rmtree(ctx.parts_dir, ignore_errors=True)


def pytest_unconfigure(config):
    with _advisory("could not release the library context"):
        from .library import context as _context

        _context.uninstall()


def pytest_sessionfinish(session, exitstatus):
    """Assemble the report. Once, in the process that owns the run.

    The whole body is advisory. It runs after the last test, so an exception
    here loses nothing that was measured — but pytest still turns it into
    INTERNALERROR, and a run that passed would be reported as broken.
    """
    with _advisory("the report could not be assembled"):
        _finish(session, exitstatus)


def _finish(session, exitstatus) -> None:
    config = session.config
    ctx = getattr(config, "_vistest_context", None)
    if ctx is None or _is_worker(config):
        return
    if not any(ctx.parts_dir.glob("*.json")):
        #  No visual checks ran. An empty report file would be worse than none:
        #  next week somebody opens it and concludes everything passed.
        return

    from .report.library import build

    parts = build(ctx.parts_dir, ctx.report,
                  title=f"VisTest — {ctx.root.name}")
    config._vistest_parts = parts

    if parts.collisions and exitstatus == 0:
        #  Two tests comparing against one baseline is not a reporting problem
        #  to mention in passing. Under `--vistest-update` they overwrite each
        #  other's file in the user's repository and whichever finished last
        #  decides what the baseline is — which under `-n` is not even the same
        #  test twice in a row. A green run that did that is worse than a red
        #  one, so the run goes red.
        session.exitstatus = 1


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Print where things went. Advisory to the last line.

    This is the final hook of the run and it prints; there is nothing here
    worth a session for. It has also been handed `parts` assembled from files
    written by other processes, which is exactly the kind of input that is
    occasionally not what it claims.
    """
    with _advisory("the summary could not be printed"):
        _summary(terminalreporter, exitstatus, config)


def _summary(terminalreporter, exitstatus, config) -> None:
    parts = getattr(config, "_vistest_parts", None)
    ctx = getattr(config, "_vistest_context", None)
    runs_root = Path(config.rootpath) / ".vistest" / "runs"
    runs = sorted(runs_root.glob("*/run.json"), key=lambda p: p.stat().st_mtime) \
        if runs_root.exists() else []

    if not (parts or runs):
        return

    terminalreporter.write_sep("-", "vistest")

    if parts is not None:
        for key, tests in sorted(parts.collisions.items()):
            terminalreporter.write_line(
                f"name collision: {key} — written by {', '.join(tests)}",
                red=True, bold=True)
            terminalreporter.write_line(
                "  these tests compare against one baseline; only the last to "
                "finish is in the report"
                + (", and with --vistest-update they overwrite each other's "
                   "baseline file" if ctx is not None and ctx.update else "")
                + ". Give each check its own name.")

        if parts.unreadable:
            terminalreporter.write_line(
                f"{parts.unreadable} check"
                f"{'s' if parts.unreadable != 1 else ''} could not be read "
                "into this report")

        if parts.report is not None:
            terminalreporter.write_line(f"Report: {parts.report}")
        if ctx is not None and ctx.update:
            terminalreporter.write_line(
                f"Baselines written to {ctx.baselines} — commit them, or CI "
                "has nothing to compare against.")

    if runs:
        terminalreporter.write_line(f"Run artifacts: {runs[-1].parent}")
    api = config.getoption("--vistest-api")
    if api:
        terminalreporter.write_line(f"Review UI: {api.rstrip('/')}/")


# --------------------------------------------------------------------------- #
#  Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session")
def vistest_config(request):
    """The loaded `VisTestConfig` for this run."""
    return request.config._vistest_context.config


@pytest.fixture
def vistest(request):
    """`expect_screenshot`, for suites that would rather ask for a fixture.

    Identical to importing the function. It exists because a fixture is
    discoverable — `pytest --fixtures` lists it with this docstring — and
    because a project that wants to wrap every check in its own defaults has
    one place to override.
    """
    from . import expect_screenshot

    request.node.add_marker(pytest.mark.visual)
    return expect_screenshot


@pytest.fixture(scope="session")
def vistest_run_id():
    from .runner import _new_run_id

    return _new_run_id()


@pytest.fixture(scope="session")
def _vistest_session(vistest_config, vistest_run_id):
    """Session-wide result collector for the service mode: summary and flush."""
    from .runner import VisualTester

    tester = VisualTester(page=None, config=vistest_config, run_id=vistest_run_id)
    yield tester
    tester.flush()


@pytest.fixture
def visual(request, page, vistest_config, _vistest_session):
    """The service-mode fixture. Needs Playwright's `page`."""
    from .runner import VisualTester

    tester = VisualTester(
        page=page,
        config=vistest_config,
        run_id=_vistest_session.run_id,
        browser=_browser_name(page),
    )
    tester.results = _vistest_session.results   # one list for the session
    request.node.add_marker(pytest.mark.visual)
    yield tester


@pytest.fixture
def visual_soft(visual):
    """Soft mode: collect every mismatch, fail once at the end.

    Useful on large pages — otherwise the first diff hides the rest.
    """
    from .models import Verdict

    collected: list = []
    original = visual.assert_screenshot

    def wrapper(name, **kw):
        kw["soft"] = True
        res = original(name, **kw)
        if res.verdict is Verdict.FAIL:
            collected.append(res)
        return res

    visual.assert_screenshot = wrapper
    yield visual
    if collected:
        raise AssertionError(
            f"Visual mismatches: {len(collected)}\n\n"
            + "\n\n".join(r.summary() for r in collected)
        )


def _browser_name(page) -> str:
    try:
        return page.context.browser.browser_type.name
    except Exception:
        return "chromium"


# --------------------------------------------------------------------------- #
#  Reporting into pytest itself
# --------------------------------------------------------------------------- #
@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Attach the visual diff to the report — and never do worse than that.

    This is a hookwrapper, so an exception raised here does not fail one test:
    pytest turns it into INTERNALERROR and the whole session dies, mid-run,
    with every remaining test unreported. Decorating a report is the least
    important thing this package does, and it is not allowed to cost a run.
    Everything below is therefore advisory: it either decorates, or warns.
    """
    outcome = yield
    try:
        report = outcome.get_result()
    except BaseException:
        #  Somebody else's hook failed, and pytest is already carrying that
        #  exception. Reading it here must not turn it into ours, and there is
        #  no report to decorate anyway.
        return
    with _advisory("the visual diff could not be attached to the report"):
        _describe_visual_failure(report, call)


def _describe_visual_failure(report, call) -> None:
    if call.when != "call" or call.excinfo is None:
        return

    error = call.excinfo.value
    from .library.errors import VisualCheckError

    if isinstance(error, VisualCheckError):
        report.sections.append(("Visual diff", str(error)))
        _attach_allure_files(error.artifacts)
        if error.result is not None:
            _attach_allure_result(error.result)
        return

    from .runner import VisualMismatch

    if isinstance(error, VisualMismatch):
        result = error.result
        _attach_allure(result)
        report.sections.append(("Visual diff", result.summary()))


def _allure():
    try:
        import allure
    except ImportError:
        return None
    return allure


def _attach_allure_files(artifacts: dict) -> None:
    allure = _allure()
    if allure is None:
        return
    titles = {"baseline": "Baseline", "actual": "Actual screenshot",
              "diff": "What changed"}
    for key, path in artifacts.items():
        if not path or key == "report" or not Path(path).exists():
            continue
        allure.attach.file(path, name=titles.get(key, key),
                           attachment_type=allure.attachment_type.PNG)


def _attach_allure_result(result) -> None:
    allure = _allure()
    if allure is None:
        return
    allure.attach(result.to_json(), name="result.json",
                  attachment_type=allure.attachment_type.JSON)


def _attach_allure(res) -> None:
    allure = _allure()
    if allure is None:
        return

    order = ["side_by_side", "boxes", "heatmap", "onion", "blink"]
    titles = {
        "side_by_side": "before | after | markup",
        "boxes": "Detected changes",
        "heatmap": "ΔE00 heatmap",
        "onion": "Onion skin (red=before, cyan=after)",
        "blink": "Blink animation",
    }
    for key in order:
        p = res.artifacts.get(key)
        if not p or not Path(p).exists():
            continue
        atype = (allure.attachment_type.GIF if p.endswith(".gif")
                 else allure.attachment_type.PNG)
        allure.attach.file(p, name=titles.get(key, key), attachment_type=atype)

    for key, p in res.artifacts.items():
        if key.startswith("region_") and Path(p).exists():
            allure.attach.file(p, name=f"Close-up: {Path(p).stem}",
                               attachment_type=allure.attachment_type.PNG)

    allure.attach(res.to_json(), name="result.json",
                  attachment_type=allure.attachment_type.JSON)
