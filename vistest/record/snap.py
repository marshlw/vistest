# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""`vistest snap` — baselines from a list of URLs, with no interactivity at all.

When it helps: initial setup of snapshots for a finished site. The list of
pages is already known (from a sitemap, router, analytics) — there is no point
running through it with a mouse.

    vistest snap https://shop.example/checkout --name checkout
    vistest snap https://shop.example --viewport 390x844 --viewport 1440x900
    vistest snap --suite pages.yaml

pages.yaml:
    snapshots:
      - {url: "https://shop/", name: "home.png", viewport: "1440x900"}
      - {url: "https://shop/cart", name: "cart.png", selector: "#cart", wait: 500}
"""

from __future__ import annotations

from ..config import VisTestConfig
from ..integrations.driver import PlaywrightDriver
from ..service import CheckService

C = {"g": "\033[32m", "y": "\033[33m", "r": "\033[31m",
     "b": "\033[34m", "0": "\033[0m"}


def snap_urls(targets: list[dict], *, browser: str = "chromium",
              update: bool = False, variants=None) -> int:
    """Снять эталоны по адресам.

    `variants` — матрица «браузер × размер окна». Не задана — один вариант, тот
    самый `browser`, и поведение ровно как раньше. Задана — каждый адрес
    снимается во всех вариантах, у каждого свой набор эталонов (см.
    `vistest.matrix`), а размер окна варианта перекрывает размер из цели: сама
    матрица и есть заявление о том, в каких размерах смотрим.
    """
    try:
        from playwright.sync_api import sync_playwright

        from ..matrix import launch
    except ImportError:
        print("Playwright is required:  python run.py setup")
        return 1

    from ..matrix import Variant

    cfg = VisTestConfig.load()
    variants = list(variants or [Variant(browser=browser, viewport=None,
                                         scale=cfg.capture.device_scale_factor,
                                         base=True)])

    created = skipped = failed = 0
    roots = []

    with sync_playwright() as p:
        for v in variants:
            service = CheckService(cfg, platform=v.platform, browser=v.browser,
                                   run_dir=cfg.runs_path() / "snap" / v.slug)
            roots.append(str(service.store.root))
            if len(variants) > 1:
                print(f"\n{C['b']}{v.label}{C['0']}  →  {v.platform}")

            br = launch(p, v.browser, headless=True)
            shots = [dict(t, viewport=v.viewport) if v.viewport else t
                     for t in targets]
            state = _maybe_auth(br, cfg, shots)
            try:
                for t in shots:
                    name = t.get("name") or "snapshot.png"
                    if not name.endswith(".png"):
                        name += ".png"

                    if service.store.exists(name) and not update:
                        print(f"{C['y']}skipped{C['0']}  {name} — baseline already "
                              f"exists (use --update to overwrite)")
                        skipped += 1
                        continue

                    try:
                        rgb, dom, unstable = _shoot(
                            br, cfg, _no_relogin(t, cfg, state), state)
                    except Exception as e:
                        print(f"{C['r']}error{C['0']}    {name}: "
                              f"{type(e).__name__}: {e}")
                        failed += 1
                        continue

                    service.check(name, rgb, unstable=unstable, dom=dom,
                                  update_baseline=True, render=False,
                                  meta={"url": t.get("url"),
                                        "selector": t.get("selector"),
                                        "viewport": t.get("viewport"),
                                        "wait": t.get("wait")})
                    h, w = rgb.shape[:2]
                    print(f"{C['g']}created{C['0']}  {name}  {w}×{h}  "
                          f"{t.get('url', '')}")
                    created += 1
            finally:
                br.close()

    print(f"\nCreated: {created}, skipped: {skipped}, failed: {failed}")
    for root in dict.fromkeys(roots):
        print(f"Baselines: {root}")
    return 1 if failed else 0


def _shoot(browser, cfg, target: dict, storage_state=None):
    from .. import scenario
    from ..capture.stabilize import context_options, install

    w, h = _viewport(target.get("viewport") or "1440x900")
    ctx_kwargs = context_options(cfg, w, h)
    if storage_state:
        ctx_kwargs["storage_state"] = str(storage_state)

    context = browser.new_context(**ctx_kwargs)
    install(context, determinism=cfg.capture.determinism, cfg=cfg)
    page = context.new_page()
    page.set_default_timeout(cfg.capture.step_timeout_ms)
    try:
        page.goto(target["url"], wait_until="domcontentloaded",
                  timeout=cfg.capture.step_timeout_ms * 2)

        if target.get("steps"):
            scenario.execute(page, target["steps"], flows=cfg.flows,
                             timeout_ms=cfg.capture.step_timeout_ms,
                             log=lambda t: print(f"    {t}"))
        if target.get("wait"):
            page.wait_for_timeout(int(target["wait"]))

        driver = PlaywrightDriver(page)
        shot = driver.capture(cfg=cfg.capture, clip_selector=target.get("selector"))
        return shot.rgb, shot.dom, shot.unstable
    finally:
        context.close()


def _maybe_auth(browser, cfg, targets):
    """Authenticate once for the whole set if at least one target needs the login flow."""
    from .. import scenario

    flow = cfg.auth.flow
    if not cfg.auth.reuse_state or flow not in (cfg.flows or {}):
        return None
    needs = any(
        any(s.get("action") == "flow" and s.get("name") == flow
            for s in (t.get("steps") or []))
        for t in targets
    )
    if not needs:
        return None
    try:
        return scenario.establish_auth(browser, cfg, flow, log=lambda t: print(f"  {t}"))
    except Exception as e:
        print(f"{C['r']}authentication failed{C['0']}: {e}")
        return None


def _no_relogin(target: dict, cfg, state) -> dict:
    if not state:
        return target
    steps = [s for s in (target.get("steps") or [])
             if not (s.get("action") == "flow" and s.get("name") == cfg.auth.flow)]
    return {**target, "steps": steps}


def _viewport(value) -> tuple[int, int]:
    if isinstance(value, (list, tuple)):
        return int(value[0]), int(value[1])
    try:
        w, h = str(value).lower().split("x")
        return int(w), int(h)
    except Exception:
        return 1440, 900
