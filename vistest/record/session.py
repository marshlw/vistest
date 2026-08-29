# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""`vistest record` — baselines are created with the mouse, without writing code.

Flow: a browser opens with your test environment, you log in, navigate to where
you need, and press «Capture page». The baseline is saved right away, and at the
end a ready-made test file is generated.

Why this matters: the main barrier of visual testing is not the engine but the
chore of setting up snapshots. Writing thirty `assert_screenshot` calls by hand,
picking selectors and masks, is a day of work. Here it is ten minutes of clicks.

--- About the exchange architecture ---

The `window.__vistestRecord` binding handler does NOT take the snapshot itself:
it only puts a request into the queue and responds immediately. The snapshot is
performed by the main loop.

This is done deliberately. Calling Playwright methods from inside a binding
callback in the synchronous API is a source of hard-to-catch hangs: at that
moment the main thread is itself inside a Playwright call, and any heavy step (a
full-page capture on an infinite feed, waiting for networkidle on a site with
ads) blocks everything, leaving no way to even report an error. The queue removes
this problem entirely.

Two connection modes:
  * your own browser (the default) — Playwright launches a headed window;
  * `--cdp http://localhost:9222` — connecting to an already open Chrome, where
    you are already logged in and have set up the state. For this, Chrome must be
    started with `--remote-debugging-port=9222`.
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import replace
from pathlib import Path

from .. import scenario
from ..config import VisTestConfig, platform_key
from ..integrations.driver import PlaywrightDriver
from ..service import CheckService
from .overlay import OVERLAY_JS

C = {"g": "\033[32m", "y": "\033[33m", "r": "\033[31m", "b": "\033[34m", "0": "\033[0m"}


def say(msg: str, color: str = "0") -> None:
    print(f"{C.get(color, '')}{msg}{C['0']}", flush=True)


def project_from_url(url: str | None) -> str:
    """Project from the address: domain without www and port.

    An explicit `--project` takes priority, but guessing by domain covers the
    common case of «testing two of my apps in one directory» without extra flags.
    """
    if not url:
        return ""
    from urllib.parse import urlparse

    host = (urlparse(url).hostname or "").lower()
    if not host or host in ("localhost", "127.0.0.1"):
        # localhost:3000 and localhost:8080 are almost certainly different
        # projects; they cannot be told apart by host, so we keep the port.
        netloc = (urlparse(url).netloc or "").lower()
        return netloc.replace(":", "-") if netloc else ""
    return host.removeprefix("www.")


class Recorder:
    def __init__(self, cfg: VisTestConfig, browser: str = "chromium",
                 project: str = ""):
        self.cfg = cfg
        self.browser = browser
        self.project = project.strip().strip("/")
        self.platform = platform_key(browser, cfg.capture.device_scale_factor)
        self.service = CheckService(
            cfg, platform=self.platform, browser=browser,
            run_dir=cfg.runs_path() / "record",
        )
        self.steps: list[dict] = []
        self.queue: list[dict] = []
        self.finished = False

    # ------------------------------------------------------------------ #
    #  Binding callback: state only, no Playwright calls
    # ------------------------------------------------------------------ #
    def handle(self, source, payload: dict) -> dict:
        action = payload.get("action")
        if action == "capture":
            self.queue.append(payload)
            return {"ok": True, "queued": True}
        if action == "finish":
            self.finished = True
            return {"ok": True}
        if action == "drop":
            self.steps = [s for s in self.steps if s["name"] != payload.get("name")]
            return {"ok": True}
        return {"ok": False, "error": f"unknown action: {action}"}

    # ------------------------------------------------------------------ #
    #  Work performed by the main loop
    # ------------------------------------------------------------------ #
    def do_capture(self, page, payload: dict) -> dict:
        name = self._unique(self.qualify(payload["name"], payload.get("url")))
        selector = payload.get("selector")
        zones = payload.get("ignore_boxes") or []
        dpr = float(payload.get("dpr") or 1.0)
        started = time.perf_counter()

        def progress(text: str) -> None:
            say(f"    … {text}", "b")
            _eval(page, "(t) => window.__vistestOverlay "
                        "&& window.__vistestOverlay.progress(t)", text)

        # The panel must not end up in the baseline.
        _eval(page, "() => window.__vistestOverlay && window.__vistestOverlay.hide()")
        page.wait_for_timeout(80)
        try:
            driver = PlaywrightDriver(page)
            shot = driver.capture(cfg=self.cfg.capture, clip_selector=selector,
                                  progress=progress)
        finally:
            _eval(page, "() => window.__vistestOverlay "
                        "&& window.__vistestOverlay.show()")

        # Zones were drawn in document CSS pixels — convert to snapshot pixels.
        boxes = [{"x": int(z["x"] * dpr), "y": int(z["y"] * dpr),
                  "w": int(z["w"] * dpr), "h": int(z["h"] * dpr)} for z in zones]

        try:
            actions = scenario.normalize(payload.get("steps") or [])
        except Exception as e:
            say(f"    recorded steps discarded: {e}", "y")
            actions = []

        vp_meta = payload.get("viewport") or {}
        self.service.check(
            name, shot.rgb,
            unstable=shot.unstable, dom=shot.dom,
            update_baseline=True, render=False,
            meta={"recorded": True, "url": payload.get("url"),
                  "selector": selector,
                  # Element passport: from it the page objects generator builds
                  # a locator sturdier than a CSS path. Recordings without it do
                  # not break — the generator silently falls back to the selector.
                  "selector_element": payload.get("selector_element"),
                  "viewport": f"{vp_meta.get('w', 1440)}x{vp_meta.get('h', 900)}",
                  "steps": actions,
                  "project": self.project or project_from_url(payload.get("url")),
                  "ignore_boxes": boxes},
        )
        for b in boxes:
            self.service.store.add_ignore_box(name, b)

        vp = payload.get("viewport") or {}
        self.steps.append({
            "name": name,
            "url": payload.get("url"),
            "selector": selector,
            "selector_element": payload.get("selector_element"),
            "viewport": (int(vp.get("w") or 1440), int(vp.get("h") or 900)),
            "ignore_boxes": boxes,
            "steps": actions,
            "project": self.project or project_from_url(payload.get("url")),
        })

        h, w = shot.rgb.shape[:2]
        elapsed = time.perf_counter() - started
        note = f" [{selector}]" if selector else ""
        zone_note = f", ignore zones: {len(boxes)}" if boxes else ""
        step_note = f", steps: {len(actions)}" if actions else ""
        say(f"  + {name}  {w}×{h}{note}{zone_note}{step_note}  ({elapsed:.1f} s)", "g")
        for a in actions:
            say(f"      · {scenario.describe(a)}", "b")
        for n in shot.notes:
            say(f"      {n}", "y")

        return {"ok": True, "name": name, "width": int(w), "height": int(h),
                "seconds": round(elapsed, 1), "notes": shot.notes}

    def qualify(self, name: str, url: str | None) -> str:
        """Append the project to the name if it is not there yet.

        Without this, two apps captured from one directory would collapse their
        `login.png` into a single baseline — and you would get a diff of «the home
        page of one project against the home page of another».
        """
        if "/" in name:
            return name
        project = self.project or project_from_url(url)
        return f"{project}/{name}" if project else name

    def _unique(self, name: str) -> str:
        existing = {s["name"] for s in self.steps}
        if name not in existing and not self.service.store.exists(name):
            return name
        stem = name[:-4] if name.endswith(".png") else name
        i = 2
        while f"{stem}-{i}.png" in existing or self.service.store.exists(f"{stem}-{i}.png"):
            i += 1
        return f"{stem}-{i}.png"


# --------------------------------------------------------------------------- #
def run_recorder(args) -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        say("Playwright is required:  python run.py setup", "r")
        return 1

    cfg = VisTestConfig.load()
    cfg.capture = _record_capture_config(cfg.capture, args)
    project = getattr(args, "project", "") or ""
    if not project and args.url:
        project = project_from_url(args.url)
    rec = Recorder(cfg, browser=args.browser, project=project)
    width, height = _parse_viewport(args.viewport)

    say("=== VisTest record ===", "b")
    say("The panel will appear in the bottom-right corner of the page.")
    say("  «Capture page»      — baseline of the whole page (Ctrl+Shift+S)")
    say("  «Select element»    — baseline of one component: hover and click")
    say("  «Ignore zone»       — mark an area that should not be compared")
    say("  «Done»              — save and exit")
    if project:
        say(f"\nProject: {project} — baselines go into a separate subdirectory,", "b")
        say("and tests into their own file. Another project will not overwrite them.", "b")
    if cfg.capture.determinism:
        origin = cfg.capture.frozen_time
        say(f"\nThe page will believe that the current time is {origin} "
            f"({cfg.capture.timezone or 'local TZ'}).", "b")
        say("This way the baseline and the run see the same date — otherwise a date", "b")
        say("diff would show up every day. The clock still ticks, the site works.", "b")
        say("If the app complains about the date — run it with --no-freeze-time", "b")
        say("and mask the date block (data-vistest=\"ignore\").", "b")
    else:
        say("\nTime freezing is off: the baseline is captured with the current date", "y")
        say("and will produce a diff tomorrow. Mask the date block with an ignore zone.", "y")
    say("")

    with sync_playwright() as p:
        if args.cdp:
            say(f"Connecting to the open browser: {args.cdp}", "b")
            browser = p.chromium.connect_over_cdp(args.cdp)
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            owns_browser = False
        else:
            from ..capture.stabilize import context_options

            btype = getattr(p, args.browser)
            browser = btype.launch(headless=False, args=["--start-maximized"])
            context = browser.new_context(**context_options(cfg, width, height))
            owns_browser = True

        context.expose_binding("__vistestRecord", rec.handle)
        context.add_init_script(OVERLAY_JS)   # the panel survives navigations

        page = context.pages[0] if context.pages else context.new_page()
        page.set_default_timeout(cfg.capture.step_timeout_ms)

        if args.url:
            try:
                page.goto(args.url, wait_until="domcontentloaded",
                          timeout=cfg.capture.step_timeout_ms * 2)
            except Exception as e:
                say(f"Could not open {args.url}: {e}", "y")
                say("The panel is still available — navigate where you need manually.", "y")
        elif not args.cdp:
            page.goto("about:blank")

        # For an already open page the init script did not run — inject it manually.
        _eval(page, OVERLAY_JS)
        page.on("load", lambda *_: _eval(page, OVERLAY_JS))
        page.on("framenavigated", lambda *_: None)

        say("Recording. Close the window or press «Done» to finish.\n", "y")
        _main_loop(rec, page)

        if owns_browser:
            try:
                browser.close()
            except Exception:
                pass

    return _finish(rec, args)


def _main_loop(rec: Recorder, page) -> None:
    """The only place where heavy operations on the page are performed."""
    while not rec.finished:
        try:
            if page.is_closed():
                break
        except Exception:
            break

        if rec.queue:
            req = rec.queue.pop(0)
            try:
                result = rec.do_capture(page, req)
            except Exception as e:
                say(f"  snapshot error: {type(e).__name__}: {e}", "r")
                result = {"ok": False, "error": f"{type(e).__name__}: {e}"}
                _eval(page, "() => window.__vistestOverlay "
                            "&& window.__vistestOverlay.show()")
            _eval(page, "(r) => window.__vistestOverlay "
                        "&& window.__vistestOverlay.onResult(r)", result)
            continue

        try:
            page.wait_for_timeout(150)
        except KeyboardInterrupt:
            break
        except Exception:
            break


def _finish(rec: Recorder, args) -> int:
    if not rec.steps:
        say("\nNot a single snapshot was taken — nothing was saved.", "y")
        return 0

    say(f"\nBaselines saved: {len(rec.steps)}", "g")
    say(f"  {rec.service.store.root}")

    if getattr(args, "no_code", False):
        return 0

    # We generate NOT from one session, but from the passports of all platform
    # baselines. Otherwise recording a second project would overwrite the tests of
    # the first: the file is the same.
    from .codegen import write_test_files

    out_dir = Path(getattr(args, "out", None) or "tests")
    if out_dir.suffix == ".py":
        out_dir = out_dir.parent

    pom = bool(getattr(args, "pom", False))
    written = write_test_files(rec.service.store, out_dir, platform=rec.platform,
                               pom=pom)
    if not written:
        say("Could not assemble tests: the baselines have no addresses.", "y")
        return 0

    say("\nGenerated tests:", "g")
    for path, count in written:
        say(f"  {path}  ({count} snapshot(s))")
    if pom:
        say(f"\nPage objects: {out_dir / 'pages'}", "g")
        say("  Edit the locators there: the block with markers survives a rebuild,")
        say("  while the test speaks of intent and does not depend on the markup.")
    say(f"\nCheck with a run:  python run.py test {out_dir}", "b")
    return 0


# --------------------------------------------------------------------------- #
def _record_capture_config(cap, args):
    """Capture settings that are sensible specifically for recording.

    The time reference point is THE SAME as in the run — this is fundamental. If
    you record a baseline with the current date and run it tomorrow, a date diff
    is guaranteed, and it will look like a markup breakage. The clock still ticks
    (see `clock_ticks`), so a live site does not break.

    The rest is gentler than in the run: fewer frames and shorter waits — the
    person recording is sitting and waiting, so responsiveness matters more.
    """
    return replace(
        cap,
        determinism=not getattr(args, "no_freeze_time", False),
        stability_shots=int(getattr(args, "shots", 0) or 2),
        network_idle_timeout_ms=min(cap.network_idle_timeout_ms, 2500),
        settle_timeout_ms=min(cap.settle_timeout_ms, 1200),
        step_timeout_ms=min(cap.step_timeout_ms, 10000),
        screenshot_timeout_ms=min(cap.screenshot_timeout_ms, 20000),
        scroll_through_page=not getattr(args, "no_scroll", False),
    )


def _eval(page, js: str, arg=None):
    """A JS call that must never bring down the recording under any circumstances."""
    try:
        return page.evaluate(js, arg) if arg is not None else page.evaluate(js)
    except Exception:
        return None


def _parse_viewport(value: str) -> tuple[int, int]:
    try:
        w, h = value.lower().split("x")
        return int(w), int(h)
    except Exception:
        print(f"Could not parse --viewport {value!r}, using 1440x900", file=sys.stderr)
        return 1440, 900


def dump_steps(rec: Recorder, path: str | Path) -> None:
    """Save the raw steps — useful for debugging the code generator."""
    Path(path).write_text(json.dumps(rec.steps, indent=2, ensure_ascii=False),
                          encoding="utf-8")
