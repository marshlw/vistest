# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Стабилизация страницы перед снимком.

Порядок операций важен. В исходной версии стили вставлялись ПОСЛЕ навигации,
когда анимации уже отыграли часть кадров и лейаут успел «доехать».
Здесь заморозка ставится через `add_init_script`, то есть до первого рендера
любой страницы в контексте.
"""

from __future__ import annotations

FREEZE_CSS = """
*, *::before, *::after {
  transition: none !important;
  transition-duration: 0s !important;
  animation: none !important;
  animation-duration: 0s !important;
  animation-delay: 0s !important;
  animation-iteration-count: 1 !important;
  animation-play-state: paused !important;
  caret-color: transparent !important;
  scroll-behavior: auto !important;
  will-change: auto !important;
}
html { scroll-behavior: auto !important; }
::-webkit-scrollbar { width: 0 !important; height: 0 !important; display: none !important; }
* { scrollbar-width: none !important; }
input, textarea, [contenteditable] { caret-color: transparent !important; }
video, [data-vistest="freeze"] { visibility: visible !important; }
"""

# Детерминизм источников случайности и времени.
#
# Без этого «случайный» баннер или сортировка выдачи дают ложный дифф в каждом
# втором прогоне, а любая дата на странице — каждый день.
#
# Важно, ЧТО именно мы делаем со временем. Наивная «заморозка» — вернуть из
# Date.now() одну и ту же константу — ломает живые приложения: анимации на
# requestAnimationFrame считают дельту и получают ноль, дебаунсы никогда не
# срабатывают, некоторые библиотеки уходят в бесконечный цикл ожидания.
#
# Поэтому часы не стоят, а ИДУТ — просто от фиксированной точки отсчёта.
# Отображаемая дата остаётся одной и той же (значит, скриншоты стабильны),
# но время внутри страницы течёт нормально.
#
# `performance.now` не трогаем вообще: он монотонный и к отображаемой дате
# отношения не имеет, а его подмена гарантированно ломает анимации.
_DETERMINISM_TEMPLATE = """
(() => {{
  if (window.__vistestDeterminism) return;
  window.__vistestDeterminism = true;

  // --- воспроизводимый Math.random (mulberry32) ---
  let s = 0x9e3779b9;
  Math.random = function () {{
    s |= 0; s = (s + 0x6D2B79F5) | 0;
    let t = Math.imul(s ^ (s >>> 15), 1 | s);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  }};

  // --- часы от фиксированной точки ---
  const ORIGIN = {origin};
  const TICKING = {ticking};
  const _Date = Date;
  const realNow = _Date.now.bind(_Date);
  const startedAt = realNow();
  const now = () => TICKING ? ORIGIN + (realNow() - startedAt) : ORIGIN;

  const FakeDate = function (...args) {{
    if (!new.target) return new _Date(now()).toString();
    return args.length === 0 ? new _Date(now()) : new _Date(...args);
  }};
  FakeDate.prototype = _Date.prototype;
  FakeDate.now = now;
  FakeDate.parse = _Date.parse;
  FakeDate.UTC = _Date.UTC;
  Object.defineProperty(FakeDate, 'name', {{ value: 'Date' }});
  window.Date = FakeDate;
}})();
"""

DEFAULT_FROZEN_TIME = "2024-01-01T12:00:00.000Z"


def determinism_js(frozen_time: str = DEFAULT_FROZEN_TIME,
                   ticking: bool = True) -> str:
    """Скрипт детерминизма под конкретную точку отсчёта."""
    from datetime import datetime, timezone

    text = (frozen_time or DEFAULT_FROZEN_TIME).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as e:
        raise ValueError(
            f"capture.frozen_time={frozen_time!r} is not valid ISO-8601 "
            "(example: 2024-01-01T12:00:00Z)"
        ) from e
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return _DETERMINISM_TEMPLATE.format(
        origin=int(dt.timestamp() * 1000),
        ticking="true" if ticking else "false",
    )


# Совместимость: константа со значениями по умолчанию.
DETERMINISM_JS = determinism_js()

# Селекторы, которые почти всегда содержат живой контент.
DEFAULT_MEDIA_SELECTORS = (
    "video", "canvas", "iframe", "embed", "object",
    "[data-vistest='ignore']", "[data-testid*='timestamp']",
    ".vistest-ignore",
)

FIND_ANIMATED_JS = """
() => {
  const out = [];
  for (const el of document.querySelectorAll('*')) {
    const cs = getComputedStyle(el);
    const animated =
      (cs.animationName && cs.animationName !== 'none') ||
      (cs.transitionDuration && parseFloat(cs.transitionDuration) > 0);
    if (!animated) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) continue;
    out.push({
      x: r.x + window.scrollX, y: r.y + window.scrollY,
      w: r.width, h: r.height,
    });
  }
  return out;
}
"""

# Все bbox'ы считаем в JS и сразу в координатах ДОКУМЕНТА (+scrollX/Y).
# `locator.bounding_box()` отдаёт координаты относительно вьюпорта — при
# full_page-скриншоте это даёт смещённые маски, если страница проскроллена.
FIND_BOXES_JS = """
(selectors) => {
  const out = [];
  for (const sel of selectors) {
    let nodes;
    try { nodes = document.querySelectorAll(sel); } catch (e) { continue; }
    let i = 0;
    for (const el of nodes) {
      if (++i > 200) break;
      const r = el.getBoundingClientRect();
      if (r.width < 1 || r.height < 1) continue;
      const cs = getComputedStyle(el);
      if (cs.visibility === 'hidden' || cs.display === 'none') continue;
      out.push({
        x: r.x + window.scrollX, y: r.y + window.scrollY,
        w: r.width, h: r.height, source: sel,
      });
    }
  }
  return out;
}
"""

WAIT_MEDIA_JS = """
async () => {
  const imgs = Array.from(document.images);
  await Promise.all(imgs.map(img => img.complete
    ? Promise.resolve()
    : new Promise(res => { img.onload = img.onerror = res; })));
  if (document.fonts && document.fonts.ready) { await document.fonts.ready; }
  // два кадра: первый применяет лейаут, второй гарантирует, что он устоялся
  await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
  return true;
}
"""

SCROLL_THROUGH_JS = """
async (stepRatio) => {
  const H = Math.max(
    document.body.scrollHeight, document.documentElement.scrollHeight,
    document.body.offsetHeight, document.documentElement.offsetHeight);
  const step = Math.max(200, window.innerHeight * stepRatio);
  for (let y = 0; y < H; y += step) {
    window.scrollTo(0, y);
    await new Promise(r => setTimeout(r, 60));
  }
  window.scrollTo(0, 0);
  await new Promise(r => requestAnimationFrame(r));
  return H;
}
"""


def context_options(cfg, width: int = 1440, height: int = 900,
                    **extra) -> dict:
    """Параметры BrowserContext, влияющие на рендер.

    Таймзона и локаль фиксируются намеренно: без них одна и та же страница
    покажет разную дату и разный формат чисел на машине разработчика и в CI —
    и эталон окажется непереносимым ещё до всякого сравнения.
    """
    opts: dict = {
        "viewport": {"width": int(width), "height": int(height)},
        "device_scale_factor": cfg.capture.device_scale_factor,
    }
    if getattr(cfg.capture, "timezone", None):
        opts["timezone_id"] = cfg.capture.timezone
    if getattr(cfg.capture, "locale", None):
        opts["locale"] = cfg.capture.locale
    if getattr(cfg.capture, "color_scheme", None):
        opts["color_scheme"] = cfg.capture.color_scheme
    opts.update(extra)
    return opts


def install(context, *, determinism: bool = True, cfg=None) -> None:
    """Повесить заморозку на BrowserContext до открытия страниц.

    context: playwright BrowserContext
    """
    import json

    if determinism:
        cap = getattr(cfg, "capture", None)
        context.add_init_script(determinism_js(
            getattr(cap, "frozen_time", DEFAULT_FROZEN_TIME),
            getattr(cap, "clock_ticks", True),
        ))
    context.add_init_script(
        "(() => { const apply = () => {"
        "  const s = document.createElement('style');"
        f"  s.textContent = {json.dumps(FREEZE_CSS)};"
        "  (document.head || document.documentElement).appendChild(s);"
        "};"
        "if (document.readyState === 'loading')"
        "  document.addEventListener('DOMContentLoaded', apply);"
        "else apply();"
        "})();"
    )


def settle(page, cfg) -> list[str]:
    """Довести страницу до устойчивого состояния. Возвращает список заметок."""
    notes: list[str] = []

    page.wait_for_load_state("domcontentloaded")
    try:
        page.wait_for_load_state("networkidle", timeout=cfg.network_idle_timeout_ms)
    except Exception:
        notes.append("networkidle not reached — the page keeps loading resources")

    if cfg.freeze_css:
        page.add_style_tag(content=FREEZE_CSS)

    if cfg.scroll_through_page and cfg.full_page:
        try:
            page.evaluate(SCROLL_THROUGH_JS, cfg.scroll_step_ratio)
        except Exception as e:
            notes.append(f"lazy-load warm-up failed: {e}")

    if cfg.wait_images or cfg.wait_fonts:
        try:
            page.evaluate(WAIT_MEDIA_JS)
        except Exception as e:
            notes.append(f"waiting for fonts/images failed: {e}")

    page.wait_for_timeout(min(cfg.settle_timeout_ms, 400))
    return notes


def collect_auto_mask_boxes(page, cfg) -> list[dict]:
    """bbox'ы, которые надо замаскировать автоматически (в CSS-пикселях)."""
    boxes: list[dict] = []
    selectors = list(cfg.mask_selectors)
    if cfg.auto_mask_media:
        selectors += list(DEFAULT_MEDIA_SELECTORS)

    if selectors:
        try:
            boxes.extend(page.evaluate(FIND_BOXES_JS, selectors))
        except Exception:
            pass

    if cfg.auto_mask_animated:
        try:
            for b in page.evaluate(FIND_ANIMATED_JS):
                b["source"] = "animated"
                boxes.append(b)
        except Exception:
            pass
    return boxes
