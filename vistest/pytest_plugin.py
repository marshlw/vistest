# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""pytest-плагин.

    def test_home(page, visual):
        page.goto("https://example.com")
        visual.assert_screenshot("home.png")

Регистрируется через entry point `pytest11` (см. pyproject.toml).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from .config import VisTestConfig
from .models import Verdict
from .runner import VisualMismatch, VisualTester


def pytest_addoption(parser):
    g = parser.getgroup("vistest", "Visual testing")
    g.addoption("--vistest-update", action="store_true",
                help="Overwrite the baselines with the current screenshots")
    g.addoption("--vistest-preset", default=None, choices=["strict", "balanced", "loose"],
                help="Preset of comparison thresholds")
    g.addoption("--vistest-config", default=None, help="Path to vistest.yaml")
    g.addoption("--vistest-api", default=None, help="URL of the VisTest service")
    g.addoption("--vistest-perceptual", action="store_true",
                help="Enable the perceptual ONNX model")


def pytest_configure(config):
    config.addinivalue_line("markers", "visual: test contains visual checks")


@pytest.fixture(scope="session")
def vistest_config(request) -> VisTestConfig:
    opt = request.config.getoption
    cfg = (VisTestConfig.preset_of(opt("--vistest-preset"))
           if opt("--vistest-preset") else VisTestConfig.load(opt("--vistest-config")))
    if opt("--vistest-update"):
        cfg.update_baselines = True
    if opt("--vistest-api"):
        from dataclasses import replace

        cfg.service = replace(cfg.service, api_url=opt("--vistest-api"))
    if opt("--vistest-perceptual"):
        from dataclasses import replace

        cfg.ai = replace(cfg.ai, perceptual_enabled=True)
    return cfg


@pytest.fixture(scope="session")
def vistest_run_id() -> str:
    from .runner import _new_run_id

    return _new_run_id()


@pytest.fixture(scope="session")
def _vistest_session(vistest_config, vistest_run_id):
    """Общий на сессию сборщик результатов — для итоговой сводки и flush."""
    tester = VisualTester(page=None, config=vistest_config, run_id=vistest_run_id)
    yield tester
    tester.flush()


@pytest.fixture
def visual(request, page, vistest_config, _vistest_session):
    """Основная фикстура. Требует playwright-фикстуру `page`."""
    tester = VisualTester(
        page=page,
        config=vistest_config,
        run_id=_vistest_session.run_id,
        browser=_browser_name(page),
    )
    tester.results = _vistest_session.results  # общий список на сессию
    request.node.add_marker(pytest.mark.visual)
    yield tester


@pytest.fixture
def visual_soft(visual):
    """Мягкий режим: собрать ВСЕ расхождения, упасть один раз в конце.

    Полезно на больших страницах — иначе первый же дифф прячет остальные.
    """
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
#  Отчётность
# --------------------------------------------------------------------------- #
@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if call.when != "call" or call.excinfo is None:
        return
    if not isinstance(call.excinfo.value, VisualMismatch):
        return

    res = call.excinfo.value.result
    _attach_allure(res)
    report.sections.append(("Visual diff", res.summary()))


def _attach_allure(res) -> None:
    try:
        import allure
    except ImportError:
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


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    root = Path(config.rootpath) / ".vistest" / "runs"
    if not root.exists():
        return
    runs = sorted(root.glob("*/run.json"), key=lambda p: p.stat().st_mtime)
    if not runs:
        return
    terminalreporter.write_sep("-", "vistest")
    terminalreporter.write_line(f"Run artifacts: {runs[-1].parent}")
    api = config.getoption("--vistest-api")
    if api:
        terminalreporter.write_line(f"Review UI: {api.rstrip('/')}/")
