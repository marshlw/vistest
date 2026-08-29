# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Подключение к существующим тестам без переписывания их структуры.

Все три формы (функция, миксин, контекстный менеджер) ходят в один и тот же
`CheckService`, поэтому пороги, маски, артефакты и метрики одинаковы с
pytest-фикстурой и с HTTP-эндпоинтом.
"""

from __future__ import annotations

import atexit
import json
from contextlib import contextmanager
from pathlib import Path

from ..config import VisTestConfig, platform_key
from ..models import CompareResult, Verdict
from ..runner import VisualMismatch, _new_run_id, git_info
from ..service import CheckService
from .driver import wrap_driver


class VisualSession:
    """Накапливает проверки одного прогона и в конце пишет run.json."""

    def __init__(
        self,
        config: VisTestConfig | None = None,
        *,
        run_id: str | None = None,
        browser: str | None = None,
    ):
        self.cfg = config or VisTestConfig.load()
        self.run_id = run_id or _new_run_id()
        self.browser = browser or "chromium"
        self.platform = platform_key(self.browser, self.cfg.capture.device_scale_factor)
        self.run_dir = self.cfg.runs_path() / self.run_id
        self.results: list[CompareResult] = []
        self._service: CheckService | None = None

    # ------------------------------------------------------------------ #
    def service_for(self, browser: str) -> CheckService:
        if self._service is None or browser != self.browser:
            self.browser = browser
            self.platform = platform_key(browser, self.cfg.capture.device_scale_factor)
            self._service = CheckService(
                self.cfg, platform=self.platform, browser=browser, run_dir=self.run_dir
            )
        return self._service

    def check(
        self,
        driver,
        name: str,
        *,
        clip_selector: str | None = None,
        full_page: bool | None = None,
        mask_selectors: list[str] | None = None,
        soft: bool = False,
        **overrides,
    ) -> CompareResult:
        from dataclasses import replace

        d = wrap_driver(driver)
        svc = self.service_for(d.browser_name)

        cap = self.cfg.capture
        if mask_selectors:
            cap = replace(cap, mask_selectors=tuple(cap.mask_selectors) + tuple(mask_selectors))
        if full_page is not None:
            cap = replace(cap, full_page=full_page)

        shot = d.capture(cfg=cap, clip_selector=clip_selector)
        res = svc.check(
            name, shot.rgb,
            unstable=shot.unstable, dom=shot.dom, notes=shot.notes,
            diff_overrides=overrides or None,
        )
        self.results.append(res)
        if res.failed and not soft:
            raise VisualMismatch(res)
        return res

    def check_image(self, name: str, image, *, soft: bool = False, **overrides):
        """Проверить готовый PNG/массив — если снимок сделан чужим кодом."""
        import numpy as np

        if isinstance(image, (str, Path)):
            from ..capture.playwright_capture import read_png

            rgb = read_png(image)
        elif isinstance(image, bytes):
            import io

            from PIL import Image

            rgb = np.array(Image.open(io.BytesIO(image)).convert("RGB"))
        else:
            rgb = image

        res = self.service_for(self.browser).check(
            name, rgb, diff_overrides=overrides or None
        )
        self.results.append(res)
        if res.failed and not soft:
            raise VisualMismatch(res)
        return res

    # ------------------------------------------------------------------ #
    def flush(self) -> Path | None:
        if not self.results:
            return None
        self.run_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "run_id": self.run_id,
            "platform": self.platform,
            "browser": self.browser,
            "git": git_info(),
            "totals": {
                "total": len(self.results),
                "passed": sum(r.verdict is Verdict.PASS for r in self.results),
                "failed": sum(r.verdict is Verdict.FAIL for r in self.results),
                "new": sum(r.verdict is Verdict.NEW_BASELINE for r in self.results),
            },
            "comparisons": [r.to_dict() for r in self.results],
        }
        path = self.run_dir / "run.json"
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

        if self.cfg.service.api_url:
            try:
                from ..client import ApiClient

                ApiClient(self.cfg.service).push_run(payload, self.run_dir)
            except Exception:
                pass
        return path


# --------------------------------------------------------------------------- #
#  Глобальная сессия для формы «одна функция»
# --------------------------------------------------------------------------- #
_GLOBAL: VisualSession | None = None


def _global_session() -> VisualSession:
    global _GLOBAL
    if _GLOBAL is None:
        _GLOBAL = VisualSession()
        atexit.register(_GLOBAL.flush)
    return _GLOBAL


def visual_check(driver, name: str, **kw) -> CompareResult:
    """Самый быстрый способ добавить визуальную проверку в существующий тест.

        from vistest.integrations import visual_check

        def test_checkout(browser):          # ваш существующий тест
            browser.get("https://shop/checkout")
            visual_check(browser, "checkout.png")

    Результаты всех вызовов за процесс собираются в один прогон, run.json
    пишется автоматически при выходе.
    """
    return _global_session().check(driver, name, **kw)


@contextmanager
def visual_session(**kw):
    """Явная сессия, если нужен свой run_id или отдельная сводка."""
    s = VisualSession(**kw)
    try:
        yield s
    finally:
        s.flush()


# --------------------------------------------------------------------------- #
#  unittest
# --------------------------------------------------------------------------- #
class VisualTestCase:
    """Миксин для unittest. Ожидает `self.driver` или `self.page`.

        class CheckoutTest(VisualTestCase, unittest.TestCase):
            def setUp(self):
                self.driver = webdriver.Chrome()

            def test_page(self):
                self.driver.get(URL)
                self.assert_screenshot("checkout.png")
    """

    vistest_config: VisTestConfig | None = None

    @property
    def visual(self) -> VisualSession:
        s = getattr(self, "_visual_session", None)
        if s is None:
            s = VisualSession(self.vistest_config)
            self._visual_session = s
        return s

    def _visual_driver(self):
        for attr in ("driver", "page", "browser", "wd"):
            d = getattr(self, attr, None)
            if d is not None:
                return d
        raise RuntimeError(
            "VisualTestCase found no driver: set self.driver / self.page"
        )

    def assert_screenshot(self, name: str, **kw) -> CompareResult:
        return self.visual.check(self._visual_driver(), name, **kw)

    def doCleanups(self):  # noqa: N802  (имя из unittest)
        s = getattr(self, "_visual_session", None)
        if s is not None:
            s.flush()
        parent = getattr(super(), "doCleanups", None)
        return parent() if parent else True
