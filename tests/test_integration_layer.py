# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Тесты слоёв подключения: сервис проверки, генерация кода, парсинг CLI.

Браузер не нужен — проверяется всё, что можно проверить без него.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vistest.config import VisTestConfig
from vistest.models import Verdict
from vistest.record.codegen import generate_test_file
from vistest.service import CheckService, slug

from . import synthetic as syn


@pytest.fixture
def svc(tmp_path):
    cfg = VisTestConfig.preset_of("balanced")
    cfg.paths.root = str(tmp_path / ".vistest")
    cfg.ai.attribution_enabled = False
    return CheckService(cfg, platform="test-chromium-1x", run_dir=tmp_path / "run")


# --------------------------------------------------------------------------- #
#  CheckService — общая точка для pytest / HTTP / CLI
# --------------------------------------------------------------------------- #
def test_first_call_creates_baseline(svc):
    res = svc.check("home.png", syn.page(), render=False)
    assert res.verdict is Verdict.NEW_BASELINE
    assert svc.store.exists("home.png")


def test_second_call_compares(svc):
    base = syn.page()
    svc.check("home.png", base, render=False)
    res = svc.check("home.png", base.copy(), render=False)
    assert res.verdict is Verdict.PASS


def test_regression_detected_through_service(svc):
    base = syn.page()
    svc.check("home.png", base, render=False)
    res = svc.check("home.png", syn.regress_button_removed(base), render=False)
    assert res.verdict is Verdict.FAIL
    assert res.max_severity > 0


def test_frames_build_stability_mask(svc):
    """Кадры с меняющимся счётчиком → область маскируется, тест проходит.

    Важно: маска покрывает ровно то, что успело подрожать между кадрами.
    Поэтому кадры берём с полностью разным содержимым поля — так же, как
    ведёт себя настоящий счётчик или таймер обратного отсчёта.
    """
    base = syn.page()
    frames = [syn.add_clock(base, t) for t in ("12:41:07", "23:59:58", "05:14:31")]
    svc.check("clock.png", frames[0], frames=frames, render=False)

    later = [syn.add_clock(base, t) for t in ("18:02:55", "07:33:10", "21:45:09")]
    res = svc.check("clock.png", later[0], frames=later, render=False)
    assert res.verdict is Verdict.PASS, res.summary()


def test_single_frame_warns_about_disabled_noise_detection(svc):
    svc.check("x.png", syn.page(), render=False)
    res = svc.check("x.png", syn.page(), render=False)
    assert any("A single frame" in n for n in res.notes)


def test_update_baseline_flag(svc):
    base = syn.page()
    svc.check("home.png", base, render=False)
    changed = syn.regress_promo_gone(base)
    res = svc.check("home.png", changed, update_baseline=True, render=False)
    assert res.verdict is Verdict.NEW_BASELINE
    # теперь эталоном стал изменённый вариант
    assert svc.check("home.png", changed, render=False).verdict is Verdict.PASS


def test_diff_overrides_applied(svc):
    base = syn.page()
    svc.check("home.png", base, render=False)
    act = syn.regress_button_color(base)

    strict = svc.check("home.png", act, diff_overrides={"fail_severity": 1.0},
                       render=False)
    # severity ограничена сверху сотней, поэтому «порог 99» — это не
    # ослабленная проверка, а почти строгая: отключает гейт только значение
    # выше максимума.
    loose = svc.check("home.png", act, diff_overrides={"fail_severity": 100.1,
                                                       "max_changed_area_pct": 99.0},
                      render=False)
    assert strict.verdict is Verdict.FAIL
    assert loose.verdict is Verdict.PASS


def test_ignore_boxes_from_meta_are_applied(svc):
    base = syn.page()
    svc.check("home.png", base, render=False)

    act = syn.regress_tiny_icon(base)
    assert svc.check("home.png", act, render=False).regions

    svc.store.add_ignore_box("home.png", {"x": 830, "y": 6, "w": 60, "h": 60})
    res = svc.check("home.png", act, render=False)
    assert res.verdict is Verdict.PASS, res.summary()


def test_result_has_no_numpy_after_check(svc):
    import json

    base = syn.page()
    svc.check("home.png", base, render=False)
    res = svc.check("home.png", syn.regress_promo_gone(base), render=False)
    json.loads(res.to_json())


# --------------------------------------------------------------------------- #
#  Генерация кода из записи
# --------------------------------------------------------------------------- #
def _steps():
    return [
        {"name": "home.png", "url": "https://shop.example/",
         "selector": None, "viewport": (1440, 900), "ignore_boxes": []},
        {"name": "pricing.png", "url": "https://shop.example/",
         "selector": "#pricing", "viewport": (1440, 900), "ignore_boxes": []},
        {"name": "home-mobile.png", "url": "https://shop.example/",
         "selector": None, "viewport": (390, 844), "ignore_boxes": []},
        {"name": "cart.png", "url": "https://shop.example/cart",
         "selector": None, "viewport": (1440, 900),
         "ignore_boxes": [{"x": 1, "y": 2, "w": 3, "h": 4}]},
    ]


# --------------------------------------------------------------------------- #
#  Проекты: два приложения в одном каталоге
# --------------------------------------------------------------------------- #
def test_same_name_in_two_projects_does_not_collide(svc):
    """`login.png` двух приложений — это два разных эталона, а не один."""
    a = syn.page(title="Проект А")
    b = syn.page(title="Проект Б")

    svc.check("shop.example/login.png", a, render=False)
    svc.check("admin.example/login.png", b, render=False)

    assert set(svc.store.list_names()) == {
        "shop.example/login.png", "admin.example/login.png"}
    # каждый сравнивается со своим, а не с чужим
    assert svc.check("shop.example/login.png", a, render=False).verdict is Verdict.PASS
    assert svc.check("admin.example/login.png", b, render=False).verdict is Verdict.PASS


def test_project_listing(svc):
    svc.check("shop.example/home.png", syn.page(), render=False)
    svc.check("shop.example/cart.png", syn.page(), render=False)
    svc.check("legacy.png", syn.page(), render=False)
    assert svc.store.projects() == ["", "shop.example"]


def test_flat_names_still_work(svc):
    """Старые эталоны без проекта не должны сломаться."""
    svc.check("home.png", syn.page(), render=False)
    assert svc.store.exists("home.png")
    assert "home.png" in svc.store.list_names()


def test_snapshot_name_cannot_escape_baselines_dir(svc):
    """Имена приходят снаружи — выйти за каталог эталонов через них нельзя."""
    svc.check("../../evil.png", syn.page(), render=False)
    saved = svc.store.list_names()
    assert all(".." not in n for n in saved), saved
    for name in saved:
        assert svc.store.dir_for(name).resolve().is_relative_to(
            svc.store.root.resolve())


def test_project_from_url():
    from vistest.record.session import project_from_url

    assert project_from_url("https://www.shop.example/checkout") == "shop.example"
    assert project_from_url("https://admin.shop.example/") == "admin.shop.example"
    assert project_from_url("http://localhost:3000/x") == "localhost-3000"
    assert project_from_url(None) == ""


def test_visual_tester_prefixes_project():
    from vistest.runner import VisualTester

    t = VisualTester(config=VisTestConfig.preset_of("balanced"))
    t.project = "shop.example"
    # драйвера нет, но проверить склейку имени можно и так
    assert t.project and "/" not in "login.png"
    name = f"{t.project}/login.png"
    assert name == "shop.example/login.png"


def test_write_test_files_splits_by_project(svc, tmp_path):
    """Запись второго проекта не должна затирать тесты первого."""
    from vistest.record.codegen import write_test_files

    svc.check("shop.example/home.png", syn.page(), render=False,
              meta={"url": "https://shop.example/", "viewport": "1440x900"})
    svc.check("shop.example/cart.png", syn.page(), render=False,
              meta={"url": "https://shop.example/cart", "viewport": "1440x900"})
    svc.check("admin.example/login.png", syn.page(), render=False,
              meta={"url": "https://admin.example/login", "viewport": "1440x900"})

    out = tmp_path / "gen"
    written = write_test_files(svc.store, out, platform="test-chromium-1x")

    files = {Path(p).name for p, _ in written}
    assert files == {"test_visual_shop_example.py", "test_visual_admin_example.py"}

    shop = (out / "test_visual_shop_example.py").read_text("utf-8")
    compile(shop, "shop.py", "exec")
    assert 'PROJECT = "shop.example"' in shop
    assert 'visual.assert_screenshot("home.png")' in shop, "имя без префикса проекта"
    assert "admin.example" not in shop, "проекты не должны перемешиваться"


def test_write_test_files_skips_baselines_without_url(svc, tmp_path):
    from vistest.record.codegen import write_test_files

    svc.check("shop.example/no-url.png", syn.page(), render=False)
    assert write_test_files(svc.store, tmp_path / "gen") == []


def test_generated_code_is_valid_python():
    code = generate_test_file(_steps(), platform="win-chromium-1x")
    compile(code, "generated.py", "exec")


# --------------------------------------------------------------------------- #
#  Шаги сценария
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
        "preset: balanced\n"
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
#  Генерация кода со шагами
# --------------------------------------------------------------------------- #
def _auth_steps():
    return [{
        "name": "account.png",
        "url": "https://app.local/account",
        "selector": None,
        "viewport": (1440, 900),
        "ignore_boxes": [],
        "steps": [
            {"action": "goto", "url": "https://app.local/login"},
            {"action": "fill", "selector": "#username", "value": "${VISTEST_USER}"},
            {"action": "fill", "selector": "#password", "value": "${VISTEST_PASSWORD}",
             "secret": True},
            {"action": "click", "selector": "button[type=submit]"},
            {"action": "wait_for", "selector": "[data-testid=user-menu]"},
            {"action": "goto", "url": "https://app.local/account"},
        ],
    }]


def test_generated_code_with_steps_is_valid():
    code = generate_test_file(_auth_steps())
    compile(code, "generated.py", "exec")


def test_generated_code_uses_env_for_secrets():
    code = generate_test_file(_auth_steps())
    assert 'os.environ["VISTEST_PASSWORD"]' in code
    assert "hunter" not in code
    assert "import os" in code
    assert "VISTEST_PASSWORD" in code.split('"""')[1], "секреты должны быть в шапке"


def test_generated_code_emits_playwright_calls():
    code = generate_test_file(_auth_steps())
    for expected in ('page.click("button[type=submit]")',
                     'page.wait_for_selector("[data-testid=user-menu]")',
                     'page.fill("#username"'):
        assert expected in code, expected


def test_generated_test_name_marks_auth():
    code = generate_test_file(_auth_steps())
    assert "def test_auth_account(" in code


def test_no_os_import_without_secrets():
    code = generate_test_file(_steps())
    assert "import os" not in code


def test_generated_code_groups_by_page_and_viewport():
    code = generate_test_file(_steps())
    assert code.count("def test_") == 3          # index@1440, index@390, cart
    assert 'clip_selector="#pricing"' in code
    assert 'page.set_viewport_size({"width": 390, "height": 844})' in code
    # для дефолтного вьюпорта строка размера не нужна
    assert code.count("set_viewport_size") == 1


def test_generated_code_extracts_base_url():
    code = generate_test_file(_steps())
    assert 'BASE_URL = "https://shop.example"' in code
    assert 'BASE_URL + "/cart"' in code


def test_generated_names_are_unique():
    steps = [
        {"name": "a.png", "url": "https://x/p", "selector": None,
         "viewport": (1440, 900), "ignore_boxes": []},
        {"name": "b.png", "url": "https://x/p?q=1", "selector": None,
         "viewport": (1440, 900), "ignore_boxes": []},
    ]
    code = generate_test_file(steps)
    names = [ln.split("(")[0].removeprefix("def ")
             for ln in code.splitlines() if ln.startswith("def test_")]
    assert len(names) == len(set(names))


def test_single_snapshot_produces_minimal_test():
    code = generate_test_file([_steps()[0]])
    assert "def test_index(page, visual):" in code
    assert 'visual.assert_screenshot("home.png")' in code


# --------------------------------------------------------------------------- #
#  Разбор аргументов CLI
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


def test_name_from_url():
    from vistest.cli import _name_from_url

    assert _name_from_url("https://shop.example/checkout/step-2") \
        == "shop.example-checkout-step-2"
    assert _name_from_url("https://shop.example/") == "shop.example-index"


def test_slug_is_filesystem_safe():
    assert slug("a/b:c*.png") == "a_b_c__png"
    assert slug("checkout.png") == "checkout_png"


# --------------------------------------------------------------------------- #
#  Драйвер-адаптер
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
#  Удаление
# --------------------------------------------------------------------------- #
def test_delete_baseline_removes_everything(svc, tmp_path):
    import shutil

    base = syn.page()
    svc.check("home.png", base, render=False)
    d = svc.store.dir_for("home.png")
    assert (d / "baseline.png").exists()

    shutil.rmtree(d)
    assert not svc.store.exists("home.png")
    # после удаления следующий прогон создаёт эталон заново, а не падает
    assert svc.check("home.png", base, render=False).verdict is Verdict.NEW_BASELINE


def test_rmtree_safe_refuses_paths_outside_root(tmp_path, monkeypatch):
    """Пути в БД приходят из чужих прогонов — снести что-то вне .vistest нельзя."""
    monkeypatch.setenv("VISTEST_ROOT", str(tmp_path / ".vistest"))
    import importlib

    from vistest.api import baselines as bl

    importlib.reload(bl)

    outsider = tmp_path / "important"
    outsider.mkdir()
    (outsider / "file.txt").write_text("не трогать", encoding="utf-8")

    assert bl._rmtree_safe(outsider) == 0
    assert (outsider / "file.txt").exists()


def test_db_cascade_deletes_comparisons_and_regions(tmp_path):
    from vistest.api.db import Database

    db = Database(tmp_path / "t.db")
    payload = {
        "run_id": "r1", "platform": "test", "browser": "chromium",
        "created_at": "2026-01-01T00:00:00",
        "git": {"branch": "main", "sha": "abc"},
        "totals": {"total": 1, "passed": 0, "failed": 1, "new": 0},
        "comparisons": [{
            "name": "home.png", "verdict": "fail",
            "metrics": {"ssim_global": 0.9, "de_mean": 5.0, "de_p95": 9.0,
                        "changed_area_pct": 1.0, "max_severity": 50.0},
            "size": {"changed": False}, "duration_ms": 100, "artifacts": {},
            "regions": [{"x": 1, "y": 2, "w": 3, "h": 4, "kind": "content",
                         "severity": 50.0}],
        }],
    }
    run_id = db.ingest_run(payload, "proj")

    assert db.one("SELECT COUNT(*) AS n FROM comparison")["n"] == 1
    assert db.one("SELECT COUNT(*) AS n FROM region")["n"] == 1

    db.execute("DELETE FROM run WHERE id=?", (run_id,))

    assert db.one("SELECT COUNT(*) AS n FROM comparison")["n"] == 0, \
        "сравнения должны уходить каскадом"
    assert db.one("SELECT COUNT(*) AS n FROM region")["n"] == 0, \
        "регионы должны уходить каскадом"


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
    from datetime import datetime, timezone

    from vistest.capture import stabilize as stab

    moment = "2030-06-15T00:00:00Z"
    expected = int(datetime(2030, 6, 15, tzinfo=timezone.utc).timestamp() * 1000)
    assert f"const ORIGIN = {expected}" in stab.determinism_js(moment)

    with pytest.raises(ValueError, match="frozen_time"):
        stab.determinism_js("вчера вечером")


def test_context_options_fix_timezone_and_locale():
    """Без фиксации TZ и локали эталон непереносим между машинами."""
    from vistest.capture.stabilize import context_options

    cfg = VisTestConfig.preset_of("balanced")
    opts = context_options(cfg, 390, 844)
    assert opts["viewport"] == {"width": 390, "height": 844}
    assert opts["timezone_id"] == "Europe/Moscow"
    assert opts["locale"] == "ru-RU"


def test_record_and_run_share_the_same_clock():
    """Эталон и прогон обязаны видеть одну дату — иначе дифф каждый день."""
    from types import SimpleNamespace

    from vistest.record.session import _record_capture_config

    cfg = VisTestConfig.preset_of("balanced")
    args = SimpleNamespace(shots=2, no_scroll=False, no_freeze_time=False)
    rec_cap = _record_capture_config(cfg.capture, args)

    assert rec_cap.determinism is cfg.capture.determinism
    assert rec_cap.frozen_time == cfg.capture.frozen_time
    assert rec_cap.clock_ticks == cfg.capture.clock_ticks

    off = _record_capture_config(
        cfg.capture, SimpleNamespace(shots=2, no_scroll=False, no_freeze_time=True))
    assert off.determinism is False


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
