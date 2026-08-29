# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Универсальный драйвер: один код работает и с Playwright, и с Selenium.

Смысл: подключить VisTest к уже написанным тестам, не переписывая их.
Если проект годами живёт на Selenium — менять его ради визуальных проверок
никто не будет. Поэтому наружу торчит один интерфейс:

    driver = wrap_driver(page_or_webdriver)
    shot = driver.capture(cfg=capture_config)

Различия, которые прячет обёртка:

| Операция          | Playwright              | Selenium                        |
|-------------------|-------------------------|---------------------------------|
| выполнить JS      | page.evaluate(fn, arg)  | driver.execute_script(js, arg)  |
| асинхронный JS    | evaluate ждёт Promise   | execute_async_script + callback |
| скриншот страницы | screenshot(full_page)   | CDP или сшивка скроллом         |
| скриншот элемента | locator.screenshot()    | element.screenshot_as_png       |
| пауза             | wait_for_timeout        | time.sleep                      |

Playwright умеет full-page нативно. Selenium — нет: в Chrome используется
CDP-команда `Page.captureBeyondViewport`, в остальных браузерах страница
сшивается из кадров по мере прокрутки.
"""

from __future__ import annotations

import io
import json
import time
from dataclasses import dataclass, field

import numpy as np

from ..capture import dom as _dom
from ..capture import stabilize as _stab
from ..config import CaptureConfig
from ..core import noise as _noise


@dataclass
class Capture:
    rgb: np.ndarray
    unstable: np.ndarray
    dom: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    device_scale: float = 1.0


def _png_to_rgb(data: bytes) -> np.ndarray:
    from PIL import Image

    return np.array(Image.open(io.BytesIO(data)).convert("RGB"))


def _noop(_text: str) -> None:
    """Заглушка колбэка прогресса."""


# --------------------------------------------------------------------------- #
#  Базовый класс
# --------------------------------------------------------------------------- #
class Driver:
    browser_name = "chromium"

    # --- примитивы, которые реализуют наследники ---
    def evaluate(self, expression: str, arg=None, *, timeout_ms: int | None = None):
        raise NotImplementedError

    def screenshot(self, *, full_page: bool = True,
                   timeout_ms: int | None = None) -> bytes:
        raise NotImplementedError

    def screenshot_element(self, selector: str,
                           *, timeout_ms: int | None = None) -> bytes:
        raise NotImplementedError

    def sleep_ms(self, ms: int) -> None:
        time.sleep(ms / 1000.0)

    def wait_ready(self, timeout_ms: int) -> None:
        pass

    def page_height(self) -> int:
        try:
            return int(self.evaluate(
                "() => Math.max(document.body.scrollHeight,"
                " document.documentElement.scrollHeight)") or 0)
        except Exception:
            return 0

    # --- общая логика поверх примитивов ---
    def device_scale(self) -> float:
        try:
            return float(self.evaluate("() => window.devicePixelRatio") or 1.0)
        except Exception:
            return 1.0

    def install_init_scripts(self, cfg: CaptureConfig) -> None:
        """Подмена Math.random / Date до первого рендера. Наследники переопределяют."""

    def settle(self, cfg: CaptureConfig, progress=_noop) -> list[str]:
        """Довести страницу до устойчивого состояния.

        Каждый шаг ограничен по времени и рапортует о себе. Без этого на любом
        реальном сайте (реклама, вебсокеты, бесконечная лента) захват просто
        зависает без единого сообщения — и понять, где именно, невозможно.
        """
        notes: list[str] = []
        step = cfg.step_timeout_ms

        self.install_init_scripts(cfg)

        progress("waiting for the page to load")
        self.wait_ready(cfg.network_idle_timeout_ms)

        if cfg.determinism:
            # Подстраховка, если init-скрипт не успел встать (страница уже
            # открыта): хуже, чем до первого рендера, но лучше, чем ничего.
            js = _stab.determinism_js(cfg.frozen_time, cfg.clock_ticks)
            self._try(lambda: self.evaluate("() => { " + js + " }", timeout_ms=step),
                      notes, "determinism")

        if cfg.freeze_css:
            progress("freezing animations")
            self._try(lambda: self.evaluate(_INJECT_CSS_JS, _stab.FREEZE_CSS,
                                            timeout_ms=step),
                      notes, "CSS freeze")

        if cfg.scroll_through_page and cfg.full_page:
            progress("warming up lazy-load")
            self._try(lambda: self.evaluate(_SCROLL_SYNC_JS, cfg.scroll_step_ratio,
                                            timeout_ms=step),
                      notes, "lazy-load warm-up")

        if cfg.wait_images or cfg.wait_fonts:
            progress("waiting for fonts and images")
            deadline = time.time() + cfg.settle_timeout_ms / 1000.0
            while time.time() < deadline:
                try:
                    if self.evaluate(_MEDIA_READY_JS, timeout_ms=2000):
                        break
                except Exception:
                    break
                time.sleep(0.05)

        self.sleep_ms(min(cfg.settle_timeout_ms, 300))
        return notes

    def auto_mask_boxes(self, cfg: CaptureConfig) -> list[dict]:
        boxes: list[dict] = []
        step = cfg.step_timeout_ms
        selectors = list(cfg.mask_selectors)
        if cfg.auto_mask_media:
            selectors += list(_stab.DEFAULT_MEDIA_SELECTORS)
        if selectors:
            try:
                boxes.extend(self.evaluate(_stab.FIND_BOXES_JS, selectors,
                                           timeout_ms=step) or [])
            except Exception:
                pass
        if cfg.auto_mask_animated:
            try:
                for b in self.evaluate(_stab.FIND_ANIMATED_JS, timeout_ms=step) or []:
                    b["source"] = "animated"
                    boxes.append(b)
            except Exception:
                pass
        return boxes

    def capture(
        self,
        *,
        cfg: CaptureConfig | None = None,
        clip_selector: str | None = None,
        progress=_noop,
    ) -> Capture:
        """progress(text) — колбэк для отображения этапа снаружи."""
        cfg = cfg or CaptureConfig()
        notes = self.settle(cfg, progress)

        full_page = cfg.full_page
        if full_page and not clip_selector and cfg.max_full_page_px:
            height = self.page_height()
            if height > cfg.max_full_page_px:
                # Chromium физически не снимает такие полотна: запрос либо
                # висит до таймаута, либо отдаёт мусор. Честно снимаем экран.
                full_page = False
                notes.append(
                    f"Page is {height}px, above the {cfg.max_full_page_px}px limit "
                    "(infinite feed?) — only the first screen was captured. "
                    "Capture the component via clip_selector."
                )
                progress(f"page is {height}px — capturing the first screen only")

        frames: list[np.ndarray] = []
        total = max(1, cfg.stability_shots)
        for i in range(total):
            if i:
                self.sleep_ms(cfg.stability_delay_ms)
            progress(f"frame {i + 1} of {total}")
            data = (self.screenshot_element(clip_selector,
                                            timeout_ms=cfg.screenshot_timeout_ms)
                    if clip_selector
                    else self.screenshot(full_page=full_page,
                                         timeout_ms=cfg.screenshot_timeout_ms))
            frames.append(_png_to_rgb(data))

        rgb = frames[0]
        progress("computing the instability mask")
        unstable = (_noise.stability_mask(frames) if len(frames) > 1
                    else np.zeros(rgb.shape[:2], dtype=bool))

        dpr = self.device_scale()
        boxes = self.auto_mask_boxes(cfg)
        if boxes and not clip_selector:
            scaled = [{"x": b["x"] * dpr, "y": b["y"] * dpr,
                       "w": b["w"] * dpr, "h": b["h"] * dpr} for b in boxes]
            unstable |= _noise.mask_from_boxes(rgb.shape[:2], scaled)
            srcs = sorted({b.get("source", "?") for b in boxes})
            notes.append(f"Auto-mask: {len(boxes)} elements ({', '.join(srcs[:6])})")

        ratio = _noise.instability_score(unstable)
        if ratio > cfg.max_unstable_ratio:
            notes.append(
                f"WARNING: {ratio * 100:.1f}% of pixels are unstable — "
                "the page is too dynamic, the comparison says little"
            )

        dom_data = {}
        if cfg.capture_dom and not clip_selector:
            progress("capturing the DOM")
            try:
                dom_data = self.evaluate(_dom.SNAPSHOT_JS, dpr,
                                         timeout_ms=cfg.step_timeout_ms) or {}
            except Exception as e:
                notes.append(f"DOM snapshot not captured: {e}")

        return Capture(rgb=rgb, unstable=unstable, dom=dom_data,
                       notes=notes, device_scale=dpr)

    @staticmethod
    def _try(fn, notes: list[str], what: str) -> None:
        try:
            fn()
        except Exception as e:
            notes.append(f"{what}: failed ({type(e).__name__})")


# --------------------------------------------------------------------------- #
#  Playwright
# --------------------------------------------------------------------------- #
_INSTALLED_CONTEXTS: set[int] = set()


class PlaywrightDriver(Driver):
    def __init__(self, page):
        self.page = page
        try:
            self.browser_name = page.context.browser.browser_type.name
        except Exception:
            self.browser_name = "chromium"

    def install_init_scripts(self, cfg: CaptureConfig | None = None) -> None:
        try:
            ctx = self.page.context
        except Exception:
            return
        key = id(ctx)
        if key in _INSTALLED_CONTEXTS:
            return
        try:
            _stab.install(
                ctx,
                determinism=(cfg is None or cfg.determinism),
                cfg=type("_C", (), {"capture": cfg})() if cfg is not None else None,
            )
            _INSTALLED_CONTEXTS.add(key)
        except Exception:
            pass

    def evaluate(self, expression: str, arg=None, *, timeout_ms: int | None = None):
        # Playwright не даёт таймаут на evaluate: если JS страницы ушёл в
        # бесконечный цикл, ждать можно вечно. Ограничиваем срок жизни всей
        # страницы на время вызова.
        prev = None
        if timeout_ms:
            prev = getattr(self, "_default_timeout", None)
            self.page.set_default_timeout(timeout_ms)
        try:
            return (self.page.evaluate(expression, arg) if arg is not None
                    else self.page.evaluate(expression))
        finally:
            if timeout_ms and prev is not None:
                self.page.set_default_timeout(prev)

    def screenshot(self, *, full_page: bool = True,
                   timeout_ms: int | None = None) -> bytes:
        kwargs = {"full_page": full_page}
        if timeout_ms:
            kwargs["timeout"] = timeout_ms
        try:
            return self.page.screenshot(animations="disabled", caret="hide", **kwargs)
        except TypeError:  # старые версии Playwright не знают animations/caret
            return self.page.screenshot(**kwargs)

    def screenshot_element(self, selector: str,
                           *, timeout_ms: int | None = None) -> bytes:
        kwargs = {"timeout": timeout_ms} if timeout_ms else {}
        return self.page.locator(selector).screenshot(**kwargs)

    def sleep_ms(self, ms: int) -> None:
        self.page.wait_for_timeout(ms)

    def wait_ready(self, timeout_ms: int) -> None:
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
        except Exception:
            pass
        try:
            self.page.wait_for_load_state("networkidle", timeout=timeout_ms)
        except Exception:
            # Реальные сайты почти никогда не доходят до networkidle: аналитика,
            # вебсокеты, реклама. Это ожидаемо, а не ошибка.
            pass

    def goto(self, url: str) -> None:
        self.page.goto(url)


# --------------------------------------------------------------------------- #
#  Selenium
# --------------------------------------------------------------------------- #
class SeleniumDriver(Driver):
    def __init__(self, webdriver):
        self.wd = webdriver
        try:
            self.browser_name = (webdriver.capabilities.get("browserName")
                                 or "chromium").lower()
        except Exception:
            self.browser_name = "chromium"

    def install_init_scripts(self, cfg: CaptureConfig | None = None) -> None:
        # Chrome/Edge умеют ставить скрипт до первого рендера через CDP.
        # Firefox/Safari — нет, там работает только подстраховка из settle().
        source = _INIT_FREEZE_JS
        if cfg is None or cfg.determinism:
            det = _stab.determinism_js(
                getattr(cfg, "frozen_time", _stab.DEFAULT_FROZEN_TIME),
                getattr(cfg, "clock_ticks", True),
            )
            source = det + "\n" + source
        try:
            self.wd.execute_cdp_cmd(
                "Page.addScriptToEvaluateOnNewDocument", {"source": source})
        except Exception:
            pass

    def evaluate(self, expression: str, arg=None, *, timeout_ms: int | None = None):
        """JS в Selenium — это тело функции, а в Playwright — стрелочная функция.

        Приводим второе к первому: оборачиваем выражение и вызываем с аргументом.
        Промисы разворачиваются через execute_async_script.
        """
        expr = expression.strip()
        is_async = expr.startswith("async")
        if is_async:
            script = (
                "var done = arguments[arguments.length - 1];"
                f"var fn = ({expr});"
                "Promise.resolve(fn(arguments[0]))"
                ".then(function(r){ done(r); })"
                ".catch(function(e){ done(null); });"
            )
            return self.wd.execute_async_script(script, arg)
        return self.wd.execute_script(f"return ({expr})(arguments[0]);", arg)

    def screenshot(self, *, full_page: bool = True,
                   timeout_ms: int | None = None) -> bytes:
        if timeout_ms:
            try:
                self.wd.set_page_load_timeout(timeout_ms / 1000.0)
            except Exception:
                pass
        if not full_page:
            return self.wd.get_screenshot_as_png()

        # Chrome/Edge: нативный full-page через CDP — быстро и без швов.
        try:
            metrics = self.wd.execute_cdp_cmd("Page.getLayoutMetrics", {})
            css = metrics.get("cssContentSize") or metrics["contentSize"]
            result = self.wd.execute_cdp_cmd("Page.captureScreenshot", {
                "format": "png",
                "captureBeyondViewport": True,
                "clip": {"x": 0, "y": 0, "width": css["width"],
                         "height": css["height"], "scale": 1},
            })
            import base64

            return base64.b64decode(result["data"])
        except Exception:
            pass

        return self._stitch_full_page()

    def _stitch_full_page(self) -> bytes:
        """Сшивка кадров прокруткой — запасной путь для Firefox/Safari.

        Прокручиваем ровно на высоту вьюпорта и склеиваем; последний кадр
        обрезаем, чтобы не задваивать хвост страницы.
        """
        from PIL import Image

        total_h = int(self.wd.execute_script(
            "return Math.max(document.body.scrollHeight,"
            " document.documentElement.scrollHeight);"))
        view_h = int(self.wd.execute_script("return window.innerHeight;"))
        self.wd.execute_script("window.scrollTo(0, 0);")
        time.sleep(0.1)

        tiles, y = [], 0
        while y < total_h:
            png = self.wd.get_screenshot_as_png()
            tiles.append((y, Image.open(io.BytesIO(png)).convert("RGB")))
            y += view_h
            if y < total_h:
                self.wd.execute_script("window.scrollTo(0, arguments[0]);", y)
                time.sleep(0.12)
        self.wd.execute_script("window.scrollTo(0, 0);")

        if not tiles:
            return self.wd.get_screenshot_as_png()

        scale = tiles[0][1].width / float(
            self.wd.execute_script("return window.innerWidth;") or tiles[0][1].width)
        canvas = Image.new("RGB", (tiles[0][1].width, int(total_h * scale)), "white")
        for y_css, tile in tiles:
            canvas.paste(tile, (0, int(y_css * scale)))

        buf = io.BytesIO()
        canvas.save(buf, format="PNG")
        return buf.getvalue()

    def screenshot_element(self, selector: str,
                           *, timeout_ms: int | None = None) -> bytes:
        from selenium.webdriver.common.by import By

        el = self.wd.find_element(By.CSS_SELECTOR, selector)
        self.wd.execute_script(
            "arguments[0].scrollIntoView({block:'center',behavior:'instant'});", el)
        time.sleep(0.1)
        return el.screenshot_as_png

    def wait_ready(self, timeout_ms: int) -> None:
        deadline = time.time() + timeout_ms / 1000.0
        while time.time() < deadline:
            try:
                if self.wd.execute_script("return document.readyState;") == "complete":
                    return
            except Exception:
                return
            time.sleep(0.05)

    def goto(self, url: str) -> None:
        self.wd.get(url)


# --------------------------------------------------------------------------- #
def wrap_driver(obj) -> Driver:
    """Определить тип объекта и вернуть подходящую обёртку."""
    if isinstance(obj, Driver):
        return obj
    if hasattr(obj, "evaluate") and hasattr(obj, "screenshot"):
        return PlaywrightDriver(obj)          # playwright.sync_api.Page
    if hasattr(obj, "execute_script") and hasattr(obj, "get_screenshot_as_png"):
        return SeleniumDriver(obj)            # selenium WebDriver
    raise TypeError(
        f"Unsupported driver type: {type(obj).__name__}. "
        "Expected a playwright Page, a selenium WebDriver or a vistest Driver."
    )


# --------------------------------------------------------------------------- #
#  JS, общий для обоих драйверов
# --------------------------------------------------------------------------- #
_INJECT_CSS_JS = """
(css) => {
  let s = document.getElementById('__vistest_freeze');
  if (!s) {
    s = document.createElement('style');
    s.id = '__vistest_freeze';
    (document.head || document.documentElement).appendChild(s);
  }
  s.textContent = css;
  return true;
}
"""

# Синхронная версия прогрева: Selenium не умеет ждать промисы обычным скриптом,
# а асинхронный вариант ограничен таймаутом script_timeout.
_SCROLL_SYNC_JS = """
(stepRatio) => {
  const H = Math.max(document.body.scrollHeight,
                     document.documentElement.scrollHeight);
  const step = Math.max(200, window.innerHeight * stepRatio);
  for (let y = 0; y < H; y += step) window.scrollTo(0, y);
  window.scrollTo(0, 0);
  return H;
}
"""

_MEDIA_READY_JS = """
() => {
  const imgs = Array.from(document.images);
  const imagesOk = imgs.every(i => i.complete);
  const fontsOk = !document.fonts || document.fonts.status === 'loaded';
  return imagesOk && fontsOk;
}
"""

# Заморозка, ставящаяся до первого рендера (Selenium через CDP).
_INIT_FREEZE_JS = f"""
(() => {{
  const css = {json.dumps(_stab.FREEZE_CSS)};
  const apply = () => {{
    if (document.getElementById('__vistest_freeze')) return;
    const s = document.createElement('style');
    s.id = '__vistest_freeze';
    s.textContent = css;
    (document.head || document.documentElement).appendChild(s);
  }};
  if (document.readyState === 'loading')
    document.addEventListener('DOMContentLoaded', apply);
  else apply();
}})();
"""
