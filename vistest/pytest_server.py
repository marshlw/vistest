# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""The server's half of the pytest plugin: its fixtures and its flags.

    def test_checkout(page, visual):
        page.goto("https://shop.example/checkout")
        visual.assert_screenshot("checkout.png")

`visual`, `visual_soft` and the session collector behind them write a run
into `.vistest/runs/` for the review interface, and `--vistest-api`,
`--vistest-perceptual` and `--vistest-fail-on` tune that mode. None of it is
the library's: a project that installed `vistest` or `vistest[browser]` and
calls `expect_screenshot` used to find these in `pytest --fixtures` and
`pytest --help` next to its own, doing nothing for it (review v1, R2 and R6).

So this module is registered by the library's plugin (`pytest_plugin.py`,
`pytest_addoption`) only when the `server` extra is installed
(`vistest._extras`), and is never imported otherwise.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from .pytest_plugin import _advisory, _allure


def pytest_addoption(parser):
    group = parser.getgroup("vistest", "Visual testing")
    group.addoption("--vistest-api", default=None, metavar="URL",
                    help="Server mode (vistest[server]): URL of the VisTest service "
                         "the `visual` fixture reports its run to")
    group.addoption("--vistest-perceptual", action="store_true",
                    help="Server mode (vistest[server]): enable the perceptual ONNX "
                         "model, if a plugin providing it is installed")
    group.addoption("--vistest-fail-on", default=None,
                    choices=["any", "likely-real", "confirmed"],
                    help="Server mode (vistest[server]): what a region scorer's "
                         "estimate does to a check — fail on any difference, on "
                         "likely-real ones (default) or only on confirmed ones. Has "
                         "no effect without a scorer installed.")


@pytest.hookimpl(trylast=True)
def pytest_configure(config):
    """The flags onto the run's settings, once the library's plugin made them.

    `--vistest-api` and `--vistest-perceptual` used to be registered and read
    by nothing: the address only reached the summary line, the model was never
    switched on. They set what their help says now.
    """
    ctx = getattr(config, "_vistest_context", None)
    if ctx is None:
        return
    fail_on = config.getoption("--vistest-fail-on")
    if fail_on:
        ctx.fail_on = fail_on
    api = config.getoption("--vistest-api")
    if api:
        ctx.config.service = replace(ctx.config.service, api_url=api)
    if config.getoption("--vistest-perceptual"):
        ctx.config.ai = replace(ctx.config.ai, perceptual_enabled=True)


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    with _advisory("the server's summary could not be printed"):
        runs_root = Path(config.rootpath) / ".vistest" / "runs"
        runs = sorted(runs_root.glob("*/run.json"), key=lambda p: p.stat().st_mtime) \
            if runs_root.exists() else []
        api = config.getoption("--vistest-api")
        if not (runs or api):
            return
        terminalreporter.write_sep("-", "vistest server")
        if runs:
            terminalreporter.write_line(f"Run artifacts: {runs[-1].parent}")
        if api:
            terminalreporter.write_line(f"Review UI: {api.rstrip('/')}/")


# --------------------------------------------------------------------------- #
#  Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session")
def vistest_run_id():
    from .runner import _new_run_id

    return _new_run_id()


@pytest.fixture(scope="session")
def _vistest_session(vistest_config, vistest_run_id):
    """Session-wide result collector for the server mode: summary and flush."""
    from .runner import VisualTester

    tester = VisualTester(page=None, config=vistest_config, run_id=vistest_run_id)
    yield tester
    tester.flush()


@pytest.fixture
def visual(request, page, vistest_config, _vistest_session):
    """The server-mode fixture. Needs Playwright's `page`."""
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
    """The server mode's diff on the report — advisory, like the library's."""
    outcome = yield
    try:
        report = outcome.get_result()
    except BaseException:
        return
    with _advisory("the visual diff could not be attached to the report"):
        _describe_mismatch(report, call)


def _describe_mismatch(report, call) -> None:
    if call.when != "call" or call.excinfo is None:
        return
    from .runner import VisualMismatch

    if isinstance(call.excinfo.value, VisualMismatch):
        result = call.excinfo.value.result
        _attach_allure(result)
        report.sections.append(("Visual diff", result.summary()))


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
