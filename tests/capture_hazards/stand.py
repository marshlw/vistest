# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""The capture-hazard stand: the pages, their hazards, and every number in it.

Each page has one way of being photographed too early or in the wrong state —
data still on its way under a spinner, a font that arrives late, a banner that
pushes the page down, a hover left over from the previous step — and knows
when that way is over: it sets `window.__ready = true` then (and
`window.__events.done`/`.ready`, for the oracle test). The baseline is taken
after that, the checks are taken the way a test takes them, without it.

Everything that varies comes from the seed: how long the server holds each
answer (real network — `server.py` sleeps before it answers), which element
the previous step hovered or focused, where the page was scrolled. Seeds 0–9
are the calibration, 10–19 are held out: the numbers of the readiness step
(S2) are chosen on the first and looked at once on the second (REPORT_S1).
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
PAGES = HERE / "pages"
FONT = HERE.parents[1] / "vistest" / "library" / "fonts" / "canary-sans.ttf"

CALIBRATION = tuple(range(0, 10))
HELD_OUT = tuple(range(10, 20))
VIEWPORT = {"width": 800, "height": 600}
#: Checks per page and seed, each in a fresh browser context.
SHOTS = 20
#: Checks of the signal variant per page and seed.
SIGNAL_SHOTS = 5
#: The baseline is taken this long after `window.__ready`.
AFTER_READY_MS = 500
#: How long a page may take to say it is ready, at most.
READY_TIMEOUT_MS = 15000
#: The range of every network delay, milliseconds.
DELAY_MS = (50, 1500)
#: silent_fetch's range: data with no sign that it is coming (S2 pre-registration).
SILENT_MS = (300, 1500)
#: modal_scroll's two blocks: the answers come within a few tens of ms of the test's click.
MODAL_MS = (30, 130)
#: pulse: the period of the decorative animation, milliseconds.
PULSE_PERIOD_MS = (900, 2400)
#: spinning_logo: the period of one turn, milliseconds.
LOGO_PERIOD_MS = (2000, 6000)


@dataclass(frozen=True)
class Hazard:
    key: str
    page: str
    #: One line: what makes a picture of it wrong.
    what: str
    #: "" — the page; a CSS selector — that element (a Locator).
    target: str = ""
    #: The previous step of the test: "" | "hover_focus" | "scroll" | "click".
    step: str = ""
    #: Is the step part of the state being photographed — taken before the
    #: baseline too (a click that asks for data), or only left over by the
    #: test's previous step (a hover, a scroll)?
    step_in_baseline: bool = False
    #: Selectors masked in every picture of it (ours and Playwright's).
    mask: tuple[str, ...] = ()
    #: "default" — Playwright's headless launch, which passes --hide-scrollbars;
    #: "scrollbars" — the same without that flag, as a headed browser draws.
    launch: str = "default"
    #: Is there a signal variant (a real change after the page loaded)?
    signal: bool = True
    #: Does the hazard end? A counter or an endless animation never does:
    #: waiting cannot help there, only a mask.
    ends: bool = True
    #: Options of our `expect_screenshot` for this row (Playwright has none of
    #: them: the row is not run under it).
    call: tuple[tuple[str, object], ...] = ()
    #: The element the step "click" presses ("" — CLICK_TARGET).
    click: str = ""


HAZARDS: tuple[Hazard, ...] = (
    Hazard("spinner", "spinner",
           "data by request after 50–1500 ms, under a spinning CSS spinner"),
    Hazard("skeleton", "skeleton",
           "data by request after 50–1500 ms, under a shimmering skeleton"),
    Hazard("lazy_images", "lazyimg",
           "lazy images in the frame and below it, each answered after 50–1500 ms"),
    Hazard("web_font", "font",
           "a web font answered after 50–1500 ms, font-display: swap"),
    Hazard("late_banner", "banner",
           "a banner by request after 50–1500 ms, inserted on top, moving the page down"),
    Hazard("scrollbar", "scrollbar",
           "rows by request after 50–1500 ms make the page scroll; centred layout "
           "(headless default: --hide-scrollbars)"),
    Hazard("scrollbar_visible", "scrollbar",
           "the same, launched without --hide-scrollbars: the scroll bar appears late",
           launch="scrollbars"),
    Hazard("hover_focus", "hover",
           "the previous step left the pointer on a control or the focus in a field",
           step="hover_focus"),
    Hazard("scrolled", "scrolled",
           "an element photographed after the page was scrolled somewhere; a sticky "
           "header and a fixed background", target="#card", step="scroll"),
    Hazard("raf_canvas", "raf",
           "a canvas animated by requestAnimationFrame, for ever", signal=False, ends=False),
    Hazard("raf_canvas_masked", "raf", "the same, the canvas masked",
           mask=("#anim",), ends=False),
    Hazard("counter", "counter",
           "a number that setInterval changes every 200 ms", signal=False, ends=False),
    Hazard("counter_masked", "counter", "the same, the number masked",
           mask=("#counter",), ends=False),
    #  Added in S2 (pre-registration, S2 addendum A).
    Hazard("pulse", "pulse",
           "a static page with a decorative dot that pulses for ever; nothing to wait for"),
    Hazard("silent_fetch", "silent",
           "data by request after 300–1500 ms, with no sign that it is coming"),
    Hazard("after_click", "click",
           "the test clicks a button; the click asks for data, answered after 50–1500 ms",
           step="click", step_in_baseline=True),
    #  Added in S2b (the second addendum to the pre-registration).
    Hazard("spinning_logo", "logo",
           "a static page with a decorative mark that turns for ever; no request"),
    Hazard("hover_focus_reset", "hover",
           "hover_focus, photographed with the options that move the pointer away and "
           "take the focus off (reset_hover_focus=True until 0.2.0.dev3)",
           step="hover_focus",
           call=(("keep_pointer", False), ("blur_focus", True))),
    #  Added in dev2 (the addendum to the pre-registration, DEV2_PREREG.md).
    Hazard("chained", "chained",
           "request A, at its answer the same spinner and request B, at its answer a "
           "picture C answered late; each 50–1500 ms"),
    Hazard("modal_scroll", "modal",
           "a long page whose blocks arrive 30–130 ms after load; the test's click on the "
           "button at the bottom opens a modal; how far the page was scrolled depends on "
           "which answer came first", step="click", step_in_baseline=True, click="#open"),
)

BY_KEY = {h.key: h for h in HAZARDS}


def _rng(*parts) -> random.Random:
    #  A string seed is hashed with SHA-512: the same numbers on every Python.
    return random.Random(":".join(str(p) for p in parts))


def delay_ms(what: str, seed: int) -> int:
    """How long the server holds the answer `what` for this seed."""
    lo, hi = (SILENT_MS if what == "data:silent_fetch"
              else MODAL_MS if what in ("data:ms_a", "data:ms_b") else DELAY_MS)
    return int(_rng("delay", what, seed).uniform(lo, hi))


def logo_ms(seed: int) -> tuple[int, int]:
    """spinning_logo: the period of one turn and how far into it the page starts."""
    rng = _rng("logo", seed)
    period = int(rng.uniform(*LOGO_PERIOD_MS))
    return period, int(rng.uniform(0, period))


def pulse_ms(seed: int) -> tuple[int, int]:
    """pulse: the period of the dot's animation and how far into it the page starts."""
    rng = _rng("pulse", seed)
    period = int(rng.uniform(*PULSE_PERIOD_MS))
    return period, int(rng.uniform(0, period))


HOVER_TARGETS = ("#save", "#export", "#docs", "#search", "#tip")


def step_target(seed: int) -> str:
    """What the previous step pointed at: hovered, or — `#search` — clicked into."""
    return _rng("hover", seed).choice(HOVER_TARGETS)


def scroll_y(seed: int) -> int:
    """Where the previous step left the page scrolled, CSS pixels."""
    return int(_rng("scroll", seed).uniform(0, 1100))


#: What the test clicks on the after_click page.
CLICK_TARGET = "#show"


def page_url(base: str, hazard: Hazard, seed: int, signal: bool = False) -> str:
    url = f"{base}/pages/{hazard.page}.html?seed={seed}&signal={int(signal)}"
    if hazard.page == "pulse":
        period, phase = pulse_ms(seed)
        url += f"&period={period}&phase={phase}"
    if hazard.page == "logo":
        period, phase = logo_ms(seed)
        url += f"&period={period}&phase={phase}"
    return url
