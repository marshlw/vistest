# VisTest - visual regression testing for Python and Playwright.
# Copyright 2026 Kirill Kulagin
# SPDX-License-Identifier: Apache-2.0
#
# This file is part of VisTest, licensed under the Apache License, Version 2.0.
# You may not use it except in compliance with the License. See LICENSE for
# the terms and NOTICE for attribution and the trademark.


"""The integration layer, the library's half: scenario steps and secrets, the
`--set` overrides, the driver adapter, the stable clock and time zone, and the
diagnosis of dates and small shifts (o1c: split from
server/tests/test_integration_layer.py, which keeps the check service, the
recorder's code generation and the server's storage)."""

from __future__ import annotations

import pytest

from vistest.config import VisTestConfig

from . import synthetic as syn


# --------------------------------------------------------------------------- #
#  Scenario steps
# --------------------------------------------------------------------------- #
def test_steps_normalize_string_form():
    from vistest import scenario

    got = scenario.normalize([
        "goto https://app/login",
        "fill #username ${VISTEST_USER}",
        "click button[type=submit]",
        "wait 300",
    ])
    assert got[0] == {"action": "goto", "url": "https://app/login"}
    assert got[1]["selector"] == "#username"
    assert got[3] == {"action": "wait", "ms": 300}


def test_steps_reject_unknown_action():
    from vistest import scenario

    with pytest.raises(ValueError, match="unknown action"):
        scenario.normalize([{"action": "teleport", "selector": "#x"}])


def test_steps_require_selector():
    from vistest import scenario

    with pytest.raises(ValueError, match="selector"):
        scenario.normalize([{"action": "click"}])


def test_secret_placeholder_resolves_from_env(monkeypatch):
    from vistest import scenario

    monkeypatch.setenv("VISTEST_PASSWORD", "hunter2")
    assert scenario.resolve("${VISTEST_PASSWORD}") == "hunter2"
    assert scenario.resolve("{{ VISTEST_PASSWORD }}") == "hunter2"


def test_missing_env_var_fails_loudly(monkeypatch):
    """Молча ввести пустой пароль — худший из возможных исходов."""
    from vistest import scenario

    monkeypatch.delenv("VISTEST_NOPE", raising=False)
    with pytest.raises(RuntimeError, match="VISTEST_NOPE"):
        scenario.resolve("${VISTEST_NOPE}")


def test_secrets_are_masked_in_logs():
    from vistest import scenario

    step = {"action": "fill", "selector": "#password", "value": "hunter2"}
    assert "hunter2" not in scenario.describe(step)
    assert "••" in scenario.describe(step)
    # а несекретное поле маскировать не надо
    plain = {"action": "fill", "selector": "#city", "value": "Москва"}
    assert "Москва" in scenario.describe(plain)


def test_flows_load_from_yaml(tmp_path, monkeypatch):
    (tmp_path / "vistest.yaml").write_text(
        "auth:\n  flow: login\n  reuse_state: true\n"
        "flows:\n"
        "  login:\n"
        "    - {action: goto, url: 'https://app/login'}\n"
        "    - {action: fill, selector: '#u', value: '${VISTEST_USER}'}\n"
        "    - {action: click, selector: 'button'}\n",
        encoding="utf-8")
    cfg = VisTestConfig.load(tmp_path / "vistest.yaml")
    assert list(cfg.flows) == ["login"]
    assert len(cfg.flows["login"]) == 3
    assert cfg.auth.flow == "login"


def test_bad_flow_in_yaml_reports_name(tmp_path):
    (tmp_path / "vistest.yaml").write_text(
        "flows:\n  login:\n    - {action: nope}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="flows.login"):
        VisTestConfig.load(tmp_path / "vistest.yaml")


def test_secrets_env_file_is_loaded(tmp_path, monkeypatch):
    from vistest import scenario

    monkeypatch.delenv("VISTEST_DEMO_TOKEN", raising=False)
    (tmp_path / "secrets.env").write_text(
        "# комментарий\nVISTEST_DEMO_TOKEN='abc123'\n", encoding="utf-8")
    assert scenario.load_secrets(tmp_path) == 1
    assert scenario.resolve("${VISTEST_DEMO_TOKEN}") == "abc123"


# --------------------------------------------------------------------------- #
#  Parsing the CLI's --set
# --------------------------------------------------------------------------- #
def test_parse_set_types():
    from vistest.cli import _parse_set

    got = _parse_set([
        "fail_severity=40", "delta_e_threshold=1.5",
        "detect_moved=false", "ignore_kinds=noise,antialias,moved",
    ])
    assert got == {
        "fail_severity": 40,
        "delta_e_threshold": 1.5,
        "detect_moved": False,
        "ignore_kinds": ("noise", "antialias", "moved"),
    }


def test_parse_set_rejects_garbage():
    from vistest.cli import _parse_set

    with pytest.raises(SystemExit):
        _parse_set(["fail_severity"])


def test_overrides_reach_diff_config():
    from vistest.cli import _parse_set

    cfg = VisTestConfig.preset_of("balanced").diff
    merged = cfg.merged(**_parse_set(["fail_severity=40"]))
    assert merged.fail_severity == 40
    assert merged.delta_e_threshold == cfg.delta_e_threshold


def test_unknown_override_is_rejected():
    cfg = VisTestConfig.preset_of("balanced").diff
    with pytest.raises(TypeError):
        cfg.merged(no_such_option=1)


# --------------------------------------------------------------------------- #
#  The driver adapter
# --------------------------------------------------------------------------- #
def test_wrap_driver_rejects_unknown_object():
    from vistest.integrations.driver import wrap_driver

    with pytest.raises(TypeError):
        wrap_driver(object())


def test_wrap_driver_detects_playwright_like():
    from vistest.integrations.driver import PlaywrightDriver, wrap_driver

    class FakePage:
        def evaluate(self, *a, **k): return None

        def screenshot(self, *a, **k): return b""

    assert isinstance(wrap_driver(FakePage()), PlaywrightDriver)


def test_wrap_driver_detects_selenium_like():
    from vistest.integrations.driver import SeleniumDriver, wrap_driver

    class FakeWD:
        capabilities = {"browserName": "chrome"}
        def execute_script(self, *a, **k): return None
        def get_screenshot_as_png(self): return b""

    d = wrap_driver(FakeWD())
    assert isinstance(d, SeleniumDriver)
    assert d.browser_name == "chrome"


def test_capture_pipeline_on_fake_driver():
    """Драйвер-заглушка: проверяем, что общая логика захвата работает
    без браузера — стабилизация, N кадров, маска нестабильности."""
    from vistest.config import CaptureConfig
    from vistest.integrations.driver import Driver

    base = syn.page()
    tick = iter(["12:00:01", "12:00:02", "12:00:03"])

    class FakeDriver(Driver):
        def evaluate(self, expression, arg=None, *, timeout_ms=None):
            return [] if "selectors" in expression or "querySelectorAll" in expression \
                else None

        def screenshot(self, *, full_page=True, timeout_ms=None):
            import cv2

            frame = syn.add_clock(base, next(tick))
            ok, buf = cv2.imencode(".png", cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))
            return buf.tobytes()

        def sleep_ms(self, ms): pass

    cfg = CaptureConfig(stability_shots=3, scroll_through_page=False,
                        capture_dom=False, settle_timeout_ms=0,
                        max_full_page_px=0)
    steps: list[str] = []
    shot = FakeDriver().capture(cfg=cfg, progress=steps.append)

    assert shot.rgb.shape == base.shape
    assert shot.unstable.any(), "часы должны попасть в маску нестабильности"
    assert not shot.unstable.all(), "маска не должна покрывать всю страницу"
    assert any("1 из 3" in s or "1 of 3" in s for s in steps), steps


def test_capture_reports_progress_and_caps_huge_pages():
    """Полотно выше лимита Chromium не должно вешать захват."""
    from vistest.config import CaptureConfig
    from vistest.integrations.driver import Driver

    base = syn.page()
    calls = {"full_page": []}

    class TallDriver(Driver):
        def evaluate(self, expression, arg=None, *, timeout_ms=None):
            if "scrollHeight" in expression:
                return 90000            # бесконечная лента
            return []

        def screenshot(self, *, full_page=True, timeout_ms=None):
            import cv2

            calls["full_page"].append(full_page)
            ok, buf = cv2.imencode(".png", cv2.cvtColor(base, cv2.COLOR_RGB2BGR))
            return buf.tobytes()

        def sleep_ms(self, ms): pass

    cfg = CaptureConfig(stability_shots=1, scroll_through_page=False,
                        capture_dom=False, settle_timeout_ms=0,
                        max_full_page_px=16000)
    shot = TallDriver().capture(cfg=cfg)

    assert calls["full_page"] == [False], "полотно 90000px нельзя снимать целиком"
    assert any("above the" in n and "limit" in n for n in shot.notes), shot.notes


# --------------------------------------------------------------------------- #
#  The stable clock and time zone
# --------------------------------------------------------------------------- #
def test_clock_ticks_from_fixed_origin():
    """Часы должны идти от фиксированной точки, а не стоять.

    Стоящие часы ломают анимации на requestAnimationFrame и дебаунсы:
    дельта всегда ноль, и приложение зависает в ожидании.
    """
    from vistest.capture import stabilize as stab

    js = stab.determinism_js("2024-01-01T12:00:00Z", ticking=True)
    assert "const ORIGIN = 1704110400000" in js
    assert "TICKING = true" in js
    assert "realNow() - startedAt" in js
    assert "performance.now" not in js, "подмена performance.now ломает анимации"


def test_frozen_time_is_configurable():
    from datetime import UTC, datetime

    from vistest.capture import stabilize as stab

    moment = "2030-06-15T00:00:00Z"
    expected = int(datetime(2030, 6, 15, tzinfo=UTC).timestamp() * 1000)
    assert f"const ORIGIN = {expected}" in stab.determinism_js(moment)

    with pytest.raises(ValueError, match="frozen_time"):
        stab.determinism_js("вчера вечером")


def test_context_options_fix_timezone_and_locale():
    """Без фиксации TZ и локали эталон непереносим между машинами."""
    from vistest.capture.stabilize import context_options

    cfg = VisTestConfig.preset_of("balanced")
    cfg.capture.timezone = "Europe/Berlin"
    cfg.capture.locale = "de-DE"
    opts = context_options(cfg, 390, 844)
    assert opts["viewport"] == {"width": 390, "height": 844}
    assert opts["timezone_id"] == "Europe/Berlin"
    assert opts["locale"] == "de-DE"


def test_timezone_and_locale_are_not_overridden_by_default():
    """Unset, the browser keeps its own: the defaults pin nobody's city."""
    from vistest.capture.stabilize import context_options
    from vistest.core.settings import CaptureConfig

    assert CaptureConfig().timezone is None
    assert CaptureConfig().locale is None
    opts = context_options(VisTestConfig.preset_of("balanced"), 390, 844)
    assert "timezone_id" not in opts and "locale" not in opts


# --------------------------------------------------------------------------- #
#  Dates and small shifts
# --------------------------------------------------------------------------- #
def test_datetime_regions_are_diagnosed():
    """Дифф на дате должен объясняться, а не выглядеть как поломка вёрстки."""
    from vistest.ai.attribution import diagnose, looks_like_datetime
    from vistest.models import ChangeKind, DiffRegion

    date_region = DiffRegion(x=0, y=0, w=200, h=20, kind=ChangeKind.TEXT,
                             element_text="Пятница, 7 августа")
    assert looks_like_datetime(date_region)

    by_selector = DiffRegion(x=0, y=0, w=10, h=10,
                             selector="[data-testid=today-date]")
    assert looks_like_datetime(by_selector)

    plain = DiffRegion(x=0, y=0, w=200, h=20, kind=ChangeKind.TEXT,
                       element_text="Оформить заказ", selector="button.cta")
    assert not looks_like_datetime(plain)

    notes = diagnose([date_region, plain])
    assert notes and "date" in notes[0].lower()
    assert "data-vistest" in notes[0], "подсказка должна быть действием, а не констатацией"


def test_small_shifts_are_diagnosed_together():
    from vistest.ai.attribution import diagnose
    from vistest.models import ChangeKind, DiffRegion

    regions = [DiffRegion(x=0, y=i * 40, w=300, h=20, kind=ChangeKind.MOVED,
                          moved_dx=0, moved_dy=1) for i in range(4)]
    notes = diagnose(regions)
    assert any("1–2 px" in n for n in notes)


def test_stabilize_js_is_single_source_of_truth():
    """Скрипт для чужих клиентов собирается из тех же констант, что и Python-путь."""
    from vistest.capture import dom as dom_mod
    from vistest.capture import stabilize as stab

    for chunk in (stab.DETERMINISM_JS, stab.FIND_BOXES_JS, dom_mod.SNAPSHOT_JS):
        assert chunk.strip(), "константы стабилизации не должны быть пустыми"
    assert "__vistestDeterminism" in stab.DETERMINISM_JS, "нужна защита от двойного применения"
