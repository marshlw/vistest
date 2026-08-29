# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Публичный API: `VisualTester.assert_screenshot(...)`.

Тонкий слой: захват страницы (через универсальный драйвер) → `CheckService`
→ сборка прогона и отправка в сервис. Вся логика сравнения живёт в
`vistest.service`, чтобы pytest, HTTP-API и CLI вели себя одинаково.
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from .capture.playwright_capture import read_png
from .config import VisTestConfig, platform_key
from .core.comparator import compare, strip_internal
from .models import CompareResult, Verdict
from .render.artifacts import render_all
from .service import CheckService
from .service import slug as _slug


class VisualMismatch(AssertionError):
    """Отдельный тип, чтобы CI мог отличить визуальный регресс от прочих падений."""

    def __init__(self, result: CompareResult):
        self.result = result
        super().__init__(result.summary())


class VisualTester:
    def __init__(
        self,
        driver=None,
        *,
        page=None,
        config: VisTestConfig | None = None,
        run_id: str | None = None,
        browser: str | None = None,
    ):
        """driver — Playwright Page, Selenium WebDriver или уже готовый Driver.

        Аргумент `page` оставлен для совместимости и делает то же самое.
        """
        from .integrations.driver import wrap_driver

        self.cfg = config or VisTestConfig.load()
        raw = driver if driver is not None else page
        self.driver = wrap_driver(raw) if raw is not None else None
        self.page = raw
        self.browser = browser or (self.driver.browser_name if self.driver else "chromium")
        self.platform = platform_key(self.browser, self.cfg.capture.device_scale_factor)
        self.run_id = run_id or _new_run_id()
        self.run_dir = self.cfg.runs_path() / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.results: list[CompareResult] = []
        self._client = None
        # Проект — префикс имени снимка. Задаётся один раз на файл тестов,
        # чтобы `login.png` двух разных приложений не схлопнулись в один эталон.
        self.project: str = ""

        self.service = CheckService(
            self.cfg, platform=self.platform, browser=self.browser, run_dir=self.run_dir
        )
        self.store = self.service.store

    # ------------------------------------------------------------------ #
    def assert_screenshot(
        self,
        name: str,
        *,
        mask_selectors: list[str] | None = None,
        clip_selector: str | None = None,
        full_page: bool | None = None,
        # точечные переопределения порогов
        delta_e_threshold: float | None = None,
        fail_severity: float | None = None,
        max_changed_area_pct: float | None = None,
        ignore_kinds: tuple[str, ...] | None = None,
        soft: bool = False,
    ) -> CompareResult:
        """Снять страницу и сравнить с эталоном.

        soft=True — не бросать исключение, только вернуть результат
        (для «собрать все расхождения за прогон и упасть в конце»).
        """
        if self.driver is None:
            raise RuntimeError("VisualTester was created without a driver/page")

        if self.project and "/" not in name:
            name = f"{self.project.strip('/')}/{name}"

        cap_cfg = self.cfg.capture
        if mask_selectors:
            cap_cfg = replace(
                cap_cfg,
                mask_selectors=tuple(cap_cfg.mask_selectors) + tuple(mask_selectors),
            )
        if full_page is not None:
            cap_cfg = replace(cap_cfg, full_page=full_page)

        shot = self.driver.capture(cfg=cap_cfg, clip_selector=clip_selector)

        res = self.service.check(
            name, shot.rgb,
            unstable=shot.unstable,
            dom=shot.dom,
            notes=shot.notes,
            diff_overrides={
                "delta_e_threshold": delta_e_threshold,
                "fail_severity": fail_severity,
                "max_changed_area_pct": max_changed_area_pct,
                "ignore_kinds": ignore_kinds,
            },
            meta=self._baseline_meta(),
            # Снять ещё кадр той же страницы — если понадобится. Понадобится
            # только при падении, и страница к этому моменту всё ещё открыта:
            # дешевле этого второго кадра нет ничего.
            recapture=lambda: self.driver.capture(
                cfg=cap_cfg, clip_selector=clip_selector).rgb,
        )
        self.results.append(res)
        if res.failed and not soft:
            raise VisualMismatch(res)
        return res

    # ------------------------------------------------------------------ #
    def compare_files(
        self, expected_path: str, actual_path: str, *, name: str | None = None
    ) -> CompareResult:
        """Сравнить два PNG без браузера — для отладки движка и CLI."""
        res = compare(
            read_png(expected_path), read_png(actual_path),
            cfg=self.cfg.diff, name=name or Path(actual_path).stem,
            ai_hooks=self.service._make_ai(None, None),
        )
        out = self.run_dir / _slug(res.name)
        out.mkdir(parents=True, exist_ok=True)
        res.artifacts.update(render_all(res, out, cfg=self.cfg.render))
        strip_internal(res)
        (out / "result.json").write_text(res.to_json(), encoding="utf-8")
        self.results.append(res)

        if res.failed:
            raise VisualMismatch(res)
        return res

    def flush(self) -> Path:
        """Записать сводку прогона. Возвращает путь к run.json."""
        payload = self.payload()
        path = self.run_dir / "run.json"
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                        encoding="utf-8")

        # JUnit рядом с run.json. Раннер работает офлайн, и сервис для него
        # необязателен — значит и артефакт для CI обязан появляться без
        # сервиса, просто как файл в каталоге прогона. Иначе выгрузка есть
        # только там, где поднят сервис, то есть ровно не в CI.
        try:
            from .report.junit import render_junit

            git = payload.get("git") or {}
            (self.run_dir / "junit.xml").write_text(
                render_junit(
                    payload.get("comparisons") or [],
                    suite=self.project or self.cfg.service.project,
                    run_key=self.run_id, platform=self.platform,
                    branch=git.get("branch") or "", git_sha=git.get("sha") or "",
                    # В момент прогона разбирать ещё нечего: любое падение —
                    # падение.
                    honor_review=False),
                encoding="utf-8")
        except Exception:
            # Отчёт для CI не может стоить прогона: run.json уже записан.
            pass

        if self._api_client() is not None:
            try:
                self._client.push_run(self.payload(), self.run_dir)
            except Exception:
                pass  # fail_open: сервис не обязан быть доступен
        return path

    def payload(self) -> dict:
        return {
            "run_id": self.run_id,
            "platform": self.platform,
            "browser": self.browser,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "git": git_info(),
            "totals": {
                "total": len(self.results),
                "passed": sum(r.verdict is Verdict.PASS for r in self.results),
                "failed": sum(r.verdict is Verdict.FAIL for r in self.results),
                "new": sum(r.verdict is Verdict.NEW_BASELINE for r in self.results),
            },
            "comparisons": [r.to_dict() for r in self.results],
        }

    # ------------------------------------------------------------------ #
    def _baseline_meta(self) -> dict:
        from .source import detect

        g = git_info()
        return {
            "platform": self.platform,
            "browser": self.browser,
            "git_sha": g.get("sha"),
            "branch": g.get("branch"),
            "approved_by": os.getenv("USER") or os.getenv("USERNAME") or "auto",
            # Чем снят снимок. Без этого он не помнит, откуда взялся, и
            # «перепроверить» для страницы за логином означало снять форму
            # входа: адреса недостаточно, а тест знает и про логин, и про
            # переходы. Известен тест ровно здесь и теряется сразу после.
            "source": detect(),
        }

    def _api_client(self):
        if self._client is not None:
            return self._client
        if not self.cfg.service.api_url:
            return None
        from .client import ApiClient

        self._client = ApiClient(self.cfg.service)
        return self._client


# --------------------------------------------------------------------------- #
def _new_run_id() -> str:
    return (os.getenv("VISTEST_RUN_ID")
            or datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6])


def git_info(root: str | Path | None = None) -> dict:
    """Ветка и коммит прогона.

    `root` — репозиторий, о котором спрашиваем. Он появился ради прогонов
    подключённых проектов: их код лежит в ЧУЖОМ репозитории, а переменные
    `GITHUB_SHA` / `CI_COMMIT_SHA` описывают ту сборку, внутри которой крутится
    сервис, — то есть совсем другой коммит. Поэтому при явном `root` окружение
    не спрашивается вовсе: лучше пустое поле, чем уверенно неверное.
    """
    def run(*args) -> str | None:
        try:
            return subprocess.run(
                args, capture_output=True, text=True, timeout=3, check=True,
                cwd=str(root) if root else None,
            ).stdout.strip()
        except Exception:
            return None

    if root is not None:
        return {"sha": run("git", "rev-parse", "HEAD"),
                "branch": run("git", "rev-parse", "--abbrev-ref", "HEAD")}

    return {
        "sha": os.getenv("GITHUB_SHA") or os.getenv("CI_COMMIT_SHA")
        or run("git", "rev-parse", "HEAD"),
        "branch": os.getenv("GITHUB_REF_NAME") or os.getenv("CI_COMMIT_REF_NAME")
        or run("git", "rev-parse", "--abbrev-ref", "HEAD"),
    }


# Совместимость со старым внутренним именем
_git_info = git_info
