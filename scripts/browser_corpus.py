# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""The browser corpus: pages drawn by Chromium, labels known in advance.

The synthetic corpus (`tests/synthetic.py`) is drawn with `cv2.putText` — the
same kind of raster the engine's thresholds were tuned on. It stays as a
ratchet («not worse»), but it cannot answer «better»: a picture the thresholds
were fitted to says nothing about a picture they were not. This corpus is the
other half. Every frame is rendered by Chromium from a local HTML template,
and every label comes from the change that produced the frame, not from a
person looking at it afterwards.

    python scripts/browser_corpus.py --out bench_out/browser   # capture, look

Templates live in `tests/browser_corpus/templates/`. They load nothing from
the network: the fonts are subsets of DejaVu shipped next to them, under our
own family names, so a font installed on the machine is never picked instead.

Frames are taken the way the library takes them since R3 —
`vistest.library.targets.capture`: animations disabled, caret hidden,
`scale="css"`, `document.fonts.ready` awaited, frames repeated until two in a
row are identical. So the corpus measures exactly what a user of
`expect_screenshot(page)` gets, and not what a bare `page.screenshot()` would.

The environment is written down, not assumed: the Playwright and Chromium
builds, the OS and architecture, the window, the device scale factor, and the
fonts Chromium actually used for the text of each template (read back over
CDP, `CSS.getPlatformFontsForNode`).

Three kinds of pair
-------------------

* **SIGNAL** — one element of the template changed on purpose (`FAMILIES`:
  22 families, most of them in graded steps — a fill ΔE00 of 2, 4, 8, 15;
  padding −2…+2px; …). The label is the mutation's: it is known before the
  frame exists.
* **NOISE** — the same DOM drawn another way (`NOISE_CONFIGS`: font hinting,
  LCD text and subpixel positioning, `text-rendering`, a page shifted by a
  fraction of a pixel, the full Chromium instead of the headless shell, GPU
  rasterisation), plus the one mutation nobody can see (opacity 0.98). A
  configuration that draws the same pixels as the baseline is not noise and
  gets no pair; it is recorded as such, because that is a result too.
* **DISPUTED** — ΔE00 ≈ 2. In the corpus, printed, never counted.

And a fourth, both at once: **real changes drawn by another renderer**
(`CROSS_SIGNAL`, family `<mutation>@<config>`) — a SIGNAL mutation drawn
with `--font-render-hinting=none` or by the full Chromium, against the
corpus baseline. Geometry within two pixels drawn that way is DISPUTED
(`CROSS_DISPUTED`, reason in the manifest). Part of it is left out for the
budget; `CROSS_LEFT_OUT` says which part and why.

The renderer's fingerprint
--------------------------

Every frame names the renderer that drew it, by its canary
(`vistest.library.canary`): a fixed page of text drawn in a tab of its own,
the way `expect_screenshot` draws it. One per rendering configuration, in
`tests/browser_corpus/renderers/`, with its sha256 in the manifest
(`renderers`); a baseline says `renderer`, a pair says `renderer.expected`
and `renderer.actual`. A configuration that is the page's stylesheet
(`text-rendering`, a fractional `transform`) is drawn by the baseline's
renderer — the canary tab never sees the page's CSS — and `hinting_full`
and `gpu_raster`, which draw the templates to the baseline's pixels, are
the control: their canary must come out as the baseline's.

The four judgement calls (ΔE00 ≈ 2, a 1px shift, opacity 0.98 and letter
spacing +0.2px) were put to the maintainer before the corpus was frozen; the
answer and its reason sit next to the magnitude (`DECIDED`).

Every frame is drawn twice, in two browsers started the same way, and must
come out the same to the pixel — or the capture stops. Mutations are applied
at DOMContentLoaded, before the first paint (the page checks), so a changed
page is drawn the way a fresh load of changed code is.

Calibration and held-out halves — by template, never by pair
------------------------------------------------------------

Four templates are for **calibration**: table, form, cards, article. Two are
**held out**: landing, dark. The split is by template, not by pair, because a
pair shares its baseline, its fonts and its layout with every other pair of
its template — split by pair, the held-out half would be the calibration
half with other magnitudes. **Held-out templates never take part in choosing
a threshold**, a preset or any other constant of the engine: their only use
is to be measured once a choice has been made on the calibration half. The
split is recorded per template and per case in `manifest.json` (`split`)
and in `docs/benchmark.md`. The held-out pair was chosen to be unlike the
rest: a dark theme, and a landing page with large type, a gradient and a
chart — the half where a threshold fitted on dense light UI would fail
first if it is going to.

Frozen, like tests/benchmark_corpus
----------------------------------

    python scripts/browser_corpus.py --regenerate    # on purpose: redraw it all
    python scripts/browser_corpus.py --check         # redraw, compare, change nothing

`tests/browser_corpus/manifest.json` lists every pair (template, family,
magnitude, label, why, split) and the sha256 of every PNG. The baseline of a
template is stored once, `frames/<template>/base.png`, and every pair of
that template points at it. `--regenerate` is the only thing that writes
there. `tests/test_browser_corpus.py` checks the files against the manifest
everywhere, and redraws frames only in the environment the manifest records
— anywhere else it is skipped with the difference named, because a frame
drawn by another Chromium is a different frame, not a regression.

Noise from another machine
--------------------------

The rendering configurations above are flags of one Chromium on one Linux.
What users run into is another machine: a developer on Windows, CI on Linux.
That noise cannot be drawn here, so it is captured there and brought in:

    python scripts/browser_corpus.py --capture-noise-only --tag win11
        # on the other machine: the six baselines and the renderer's canary,
        # no mutations, the same capture path, into bench_out/os_noise/win11/
        # (not the corpus), with environment.json: OS and its version,
        # Playwright, the Chromium build, the fonts over CDP, the screen's DPI
        # and font smoothing, the sha256 of each PNG and how it differs from
        # the corpus environment
    python scripts/browser_corpus.py --import-noise bench_out/os_noise/win11
        # here: the frames become the NOISE family os_<os> (os_windows), one
        # pair per template against the corpus baseline, split by template
        # like everything else; the canary becomes renderers/os_<os>.png.
        # A second capture of the same pixels, to bring a canary the first
        # one did not have, is taken without --replace

An imported frame is redrawn only where its own environment is found again
(same Playwright, Chromium build, OS and its version, architecture and font
smoothing); everywhere else its drift test is skipped with the difference
named. `--regenerate` keeps imported frames only while the baselines they
pair with come out byte for byte the same; otherwise it drops them and says
so — a Windows frame against a baseline from another Chromium is not the
noise that was measured.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

CORPUS_DIR = REPO / "tests" / "browser_corpus"
TEMPLATE_DIR = CORPUS_DIR / "templates"

#: One window for every frame. 1280x800 at 1x: a laptop-sized viewport, and
#: every template is laid out to fit inside it, so the frame is the page.
VIEWPORT = {"width": 1280, "height": 800}
DEVICE_SCALE_FACTOR = 1

#: Every frame goes through the library's own capture. The time limit is the
#: library default.
STABLE_TIMEOUT_MS = 5000


@dataclass(frozen=True)
class Template:
    key: str
    title: str
    split: str


#: The two halves. Held-out templates never take part in choosing a
#: threshold — see the module docstring.
CALIBRATION, HELD_OUT = "calibration", "held_out"

TEMPLATES: tuple[Template, ...] = (
    Template("table", "orders table in an admin panel", CALIBRATION),
    Template("form", "account settings form", CALIBRATION),
    Template("cards", "product cards in a catalogue grid", CALIBRATION),
    Template("landing", "marketing landing page with a hero and a chart", HELD_OUT),
    Template("article", "long serif text with a sidebar", CALIBRATION),
    Template("dark", "dark-theme monitoring dashboard", HELD_OUT),
)

SPLIT_RULE = (
    "by template, never by pair: held-out templates never take part in "
    "choosing a threshold, a preset or any other constant of the engine; they "
    "are only measured once a choice has been made on the calibration half")

#: The frozen corpus.
MANIFEST = CORPUS_DIR / "manifest.json"
FRAMES_DIR = CORPUS_DIR / "frames"

#: What the frozen PNGs may weigh together.
BUDGET_BYTES = 40 * 1024 * 1024

#: The `data-m` tokens every template must carry: one element per kind of
#: change the corpus makes. A template without one of them would silently lose
#: a family, and the per-family table would compare different sets of pages.
REQUIRED_TARGETS = (
    "fill", "text", "link", "iconcolor", "noborder", "border", "underline",
    "radius", "pad", "offset", "fsize", "weight", "spacing", "lh", "icon",
    "char", "word", "remove", "order", "opacity", "shadow", "focus",
)


class CorpusError(RuntimeError):
    """The corpus cannot be captured or trusted here."""


# --------------------------------------------------------------------------- #
#  Browser
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Launch:
    """How Chromium is started for one rendering configuration."""

    args: tuple[str, ...] = ()
    channel: str | None = None          # None: Playwright's headless shell
    css: str = ""                       # present from the first paint on


#: A stylesheet that is part of the page from its first paint. Added after
#: `load` instead, a fractional `transform` on <body> was rasterised one way
#: or another depending on timing — in about one run in six, 23 pixels of the
#: dark template came out different in the same configuration. From the first
#: paint it is the same every time.
_EARLY_CSS = """(css) => {
  const put = () => {
    if (!document.head) return false;
    const s = document.createElement('style');
    s.setAttribute('data-corpus', 'render');
    s.textContent = css;
    document.head.appendChild(s);
    return true;
  };
  if (put()) return;
  new MutationObserver((_, o) => { if (put()) o.disconnect(); })
    .observe(document, {childList: true, subtree: true});
}"""


BASELINE = Launch()


def template_url(key: str) -> str:
    return (TEMPLATE_DIR / f"{key}.html").as_uri()


#: A mutation is applied at DOMContentLoaded, before the first paint, and the
#: page checks that it was: `performance` must hold no paint entry yet. The
#: first version changed the page after `load`, and a change that lands
#: after the first raster is rasterised again only where it was invalidated —
#: whether it landed before or after depended on timing, and the same
#: mutation came out 7 pixels different in a second browser. A changed page
#: drawn from its first paint is also what a visual test compares in real
#: life: a fresh load of the new code, not a live edit of the old one.
_MUTATE = """(([sel, code, v]) => {
  document.addEventListener('DOMContentLoaded', () => {
    const state = window.__corpus = {painted: performance.getEntriesByType('paint').length};
    try {
      const els = Array.from(document.querySelectorAll(sel));
      state.count = els.length;
      (new Function('els', 'v', code))(els, v);
      state.done = true;
    } catch (e) { state.error = String(e); }
  }, {once: true});
})"""


class Session:
    """One Chromium process and context; every frame from a fresh page."""

    def __init__(self, pw, launch: Launch = BASELINE):
        kw = {"args": list(launch.args)}
        if launch.channel:
            kw["channel"] = launch.channel
        self.launch = launch
        self.browser = pw.chromium.launch(**kw)
        self.context = self.browser.new_context(
            viewport=dict(VIEWPORT), device_scale_factor=DEVICE_SCALE_FACTOR,
            locale="en-US", color_scheme="light")
        if launch.css:
            self.context.add_init_script(
                f"({_EARLY_CSS})({json.dumps(launch.css)})")

    def frame(self, template: str, mutation: tuple[str, str, object] | None = None,
              *, fonts: bool = False):
        """The template, optionally changed from its first paint, photographed.

        `mutation` is `(selector, js body over els and v, v)`. Returns the PNG,
        or `(png, fonts)` when `fonts=True`.
        """
        page = self.context.new_page()
        try:
            if mutation is not None:
                page.add_init_script(f"({_MUTATE})({json.dumps(list(mutation))})")
            page.goto(template_url(template), wait_until="load")
            if mutation is not None:
                state = page.evaluate("() => window.__corpus || null")
                if not state or state.get("error") or not state.get("done"):
                    raise CorpusError(f"{template}: the mutation did not run: {state}")
                if state.get("count") != 1:
                    raise CorpusError(f"{template}: {state.get('count')} elements "
                                      f"match {mutation[0]}, expected 1")
                if state.get("painted"):
                    raise CorpusError(f"{template}: the page was painted before "
                                      "the mutation ran")
            png = self._shoot(page)
            if fonts:
                return png, fonts_used(page)
            return png
        finally:
            page.close()

    def read(self, template: str, js: str):
        """Evaluate `js` on the unchanged template."""
        page = self.context.new_page()
        try:
            page.goto(template_url(template), wait_until="load")
            return page.evaluate(js)
        finally:
            page.close()

    def canary(self) -> bytes:
        """The renderer's fingerprint, drawn in a tab of this context.

        Through the library's own `vistest.library.canary.draw`, which is
        what `expect_screenshot` draws it with.
        """
        from vistest.library.canary import CanaryError, draw

        try:
            return draw(self.context, stable_timeout_ms=STABLE_TIMEOUT_MS)
        except CanaryError as e:
            raise CorpusError(f"the canary: {e}") from None

    @staticmethod
    def _shoot(page) -> bytes:
        """The frame, through the library's capture — or an error."""
        from vistest.library.targets import capture

        cap = capture(page, stable_timeout_ms=STABLE_TIMEOUT_MS)
        if cap.stability.stable is not True:
            raise CorpusError(f"the page did not settle: {cap.stability.as_dict()}")
        if cap.notes:
            raise CorpusError("capture notes: " + "; ".join(cap.notes))
        return cap.png

    def close(self) -> None:
        self.context.close()
        self.browser.close()


def fonts_used(page) -> list[dict]:
    """The fonts Chromium actually drew the text of this page with.

    `CSS.getPlatformFontsForNode` for every element that holds text directly,
    and for form controls, whose text lives in a user-agent shadow tree.
    Glyph counts are summed per font. A system font showing up here means a
    glyph fell outside the shipped subset.
    """
    cdp = page.context.new_cdp_session(page)
    try:
        cdp.send("DOM.enable")
        cdp.send("CSS.enable")
        root = cdp.send("DOM.getDocument", {"depth": -1, "pierce": True})["root"]
        ids: list[int] = []

        def walk(node):
            kids = node.get("children") or []
            has_text = any(k.get("nodeType") == 3 and k.get("nodeValue", "").strip()
                           for k in kids)
            if node.get("nodeType") == 1 and (
                    has_text or node.get("nodeName") in ("INPUT", "SELECT", "BUTTON")):
                ids.append(node["nodeId"])
            for k in kids:
                walk(k)
            for k in node.get("shadowRoots") or []:
                walk(k)

        walk(root)
        total: dict[tuple, int] = {}
        for node_id in ids:
            try:
                fonts = cdp.send("CSS.getPlatformFontsForNode",
                                 {"nodeId": node_id})["fonts"]
            except Exception:
                continue
            for f in fonts:
                key = (f["familyName"], f.get("postScriptName", ""),
                       bool(f.get("isCustomFont")))
                total[key] = total.get(key, 0) + int(f.get("glyphCount", 0))
    finally:
        cdp.detach()
    return [{"family": k[0], "postscript": k[1], "custom": k[2], "glyphs": n}
            for k, n in sorted(total.items())]


# --------------------------------------------------------------------------- #
#  Environment
# --------------------------------------------------------------------------- #
def _distro() -> str:
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if line.startswith("PRETTY_NAME="):
                return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return platform.platform()


def _os_version() -> str:
    """`10.0.26100` on Windows, `14.5` on macOS, the distribution on Linux.

    On Linux the kernel is not what draws text, and its build string would
    make every kernel update look like another machine.
    """
    system = platform.system()
    if system == "Windows":
        return platform.version()
    if system == "Darwin":
        return platform.mac_ver()[0] or platform.release()
    return _distro()


def _revision(executable: str) -> str:
    """`1194` out of `.../chromium-1194/chrome-linux/chrome`."""
    for part in Path(executable).parts:
        if part.startswith("chromium") and "-" in part:
            return part.rsplit("-", 1)[1]
    return "?"


def browser_identity(browser, executable: str) -> dict:
    """Which Chromium drew the frame, as the browser itself reports it.

    `browser.version` is the same for the headless shell and for the full
    Chromium of one Playwright release; the product string is not
    (`HeadlessChrome/…` against `Chrome/…`), and neither is the build
    directory Playwright installs them into.
    """
    page = browser.new_page()
    try:
        cdp = page.context.new_cdp_session(page)
        info = cdp.send("Browser.getVersion")
        cdp.detach()
    finally:
        page.close()
    product = info.get("product", "")
    shell = product.startswith("HeadlessChrome")
    return {
        "product": product,
        "build": f"{'chromium_headless_shell' if shell else 'chromium'}-"
                 f"{_revision(executable)}",
    }


def _container() -> dict:
    """Where the frames were drawn: a container image, or not.

    The tag comes from `VISTEST_CORPUS_IMAGE` — whoever starts the container
    knows it; a guess from inside would be worse than nothing.
    """
    image = os.environ.get("VISTEST_CORPUS_IMAGE", "")
    inside = Path("/.dockerenv").exists()
    return {"image": image or None, "in_container": inside}


def _windows_display() -> dict:
    """DPI and font smoothing as Windows reports them to a program asking.

    Headless Chromium draws at the context's device scale factor (1 here),
    not at the screen's, so the DPI is written down rather than expected to
    matter. Font smoothing is another matter: Skia on Windows asks the system
    for ClearType and its contrast, so it is part of the environment.
    """
    import ctypes
    from ctypes import wintypes

    out: dict = {}
    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    try:
        #  The thread, not the process, becomes DPI-aware for one call and is
        #  put back: an unaware thread is told 96 whatever the screen is.
        user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        before = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        try:
            out["dpi"] = int(user32.GetDpiForSystem())
        finally:
            if before:
                user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(before))
        out["dpi_source"] = "GetDpiForSystem, per-monitor aware thread"
        out["scale_percent"] = round(out["dpi"] * 100 / 96)
    except Exception as e:  # pragma: no cover - old Windows
        out["dpi"] = None
        out["dpi_source"] = f"unavailable: {e}"

    def spi(action: int) -> int | None:
        value = wintypes.UINT(0)
        ok = user32.SystemParametersInfoW(action, 0, ctypes.byref(value), 0)
        return int(value.value) if ok else None

    smoothing = {
        "enabled": spi(0x004A),        # SPI_GETFONTSMOOTHING
        "type": spi(0x200A),           # SPI_GETFONTSMOOTHINGTYPE: 1 standard, 2 ClearType
        "contrast": spi(0x200C),       # SPI_GETFONTSMOOTHINGCONTRAST, 1000-2200
        "orientation": spi(0x2012),    # SPI_GETFONTSMOOTHINGORIENTATION: 0 BGR, 1 RGB
    }
    if smoothing["enabled"] is not None:
        smoothing["enabled"] = bool(smoothing["enabled"])
    smoothing["cleartype"] = smoothing["type"] == 2
    out["font_smoothing"] = smoothing
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as k:
            for name in ("ProductName", "DisplayVersion", "CurrentBuild", "UBR"):
                try:
                    out.setdefault("windows", {})[name] = winreg.QueryValueEx(k, name)[0]
                except OSError:
                    pass
    except Exception:  # pragma: no cover - registry closed
        pass
    return out


def host_display() -> dict:
    """The screen and font settings of the machine that drew the frames.

    Recorded, not assumed. On Linux without a display there is none to
    read, and that is what is written.
    """
    system = platform.system()
    if system == "Windows":
        try:
            return _windows_display()
        except Exception as e:  # pragma: no cover - depends on the machine
            return {"dpi": None, "dpi_source": f"unavailable: {e}",
                    "font_smoothing": None}
    display = os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    if not display:
        return {"dpi": None, "dpi_source": "no display: headless",
                "font_smoothing": None}
    dpi = None
    try:
        import subprocess

        res = subprocess.run(["xrdb", "-query"], capture_output=True, text=True,
                             timeout=5)
        for line in res.stdout.splitlines():
            if line.startswith("Xft.dpi:"):
                dpi = int(float(line.split(":", 1)[1]))
    except Exception:
        pass
    return {"dpi": dpi, "dpi_source": f"xrdb Xft.dpi on {display}" if dpi
            else f"display {display}, Xft.dpi not set", "font_smoothing": None}


def environment(pw) -> dict:
    from importlib import metadata

    browser = pw.chromium.launch()
    try:
        version = browser.version
        who = browser_identity(browser, pw.chromium.executable_path)
    finally:
        browser.close()
    return {
        "playwright": metadata.version("playwright"),
        "chromium": version,
        "browser_build": who["build"],
        "browser_product": who["product"],
        "os": platform.system(),
        "os_version": _os_version(),
        "arch": platform.machine(),
        "distro": _distro(),
        "python": platform.python_version(),
        "viewport": dict(VIEWPORT),
        "device_scale_factor": DEVICE_SCALE_FACTOR,
        "container": _container(),
        "host": host_display(),
        "capture": {
            "via": "vistest.library.targets.capture",
            "screenshot": {"type": "png", "animations": "disabled",
                           "caret": "hide", "scale": "css"},
            "before": "document.fonts.ready",
            "stability": f"frames until two in a row are byte-identical, "
                         f"{STABLE_TIMEOUT_MS} ms limit",
        },
    }


#: What has to be equal for a frame to be expected to come out the same.
#: The distro is recorded but not compared: Chromium brings its own FreeType
#: and Skia, and the fonts are ours.
ENV_KEYS = ("playwright", "chromium", "browser_build", "os", "arch")


def env_mismatch(recorded: dict, here: dict) -> list[str]:
    """What differs between the recorded environment and this one."""
    out = [f"{k}: recorded {recorded.get(k)!r}, here {here.get(k)!r}"
           for k in ENV_KEYS if recorded.get(k) != here.get(k)]
    image = (recorded.get("container") or {}).get("image")
    if image and (here.get("container") or {}).get("image") != image:
        out.append(f"container image: recorded {image!r}, here "
                   f"{(here.get('container') or {}).get('image')!r}")
    return out


# --------------------------------------------------------------------------- #
#  PNG
# --------------------------------------------------------------------------- #
def pixels(png: bytes):
    import numpy as np
    from PIL import Image

    return np.asarray(Image.open(io.BytesIO(png)).convert("RGB"))


def differing_pixels(a: bytes, b: bytes) -> int:
    import numpy as np

    pa, pb = pixels(a), pixels(b)
    if pa.shape != pb.shape:
        return max(pa.shape[0] * pa.shape[1], pb.shape[0] * pb.shape[1])
    return int(np.any(pa != pb, axis=2).sum())


def pack(png: bytes) -> bytes:
    """Re-encode losslessly at the highest compression: same pixels, fewer bytes."""
    from PIL import Image

    img = Image.open(io.BytesIO(png)).convert("RGB")
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True, compress_level=9)
    return out.getvalue()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------- #
#  Colour: CIEDE2000, written out here on purpose
# --------------------------------------------------------------------------- #
#  The ruler must not be the thing it measures: the engine has its own ΔE00
#  (vistest/core/color.py, through OpenCV), and the magnitude of a mutation
#  is not computed with it. This one is the textbook formula (Sharma, Wu and
#  Dalal, 2005) on single colours, in plain Python.
def _srgb_to_lab(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    import math

    def lin(c):
        c /= 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(float(c)) for c in rgb)
    x = (0.4124564 * r + 0.3575761 * g + 0.1804375 * b) / 0.95047
    y = (0.2126729 * r + 0.7151522 * g + 0.0721750 * b) / 1.00000
    z = (0.0193339 * r + 0.1191920 * g + 0.9503041 * b) / 1.08883

    def f(t):
        return math.copysign(abs(t) ** (1 / 3), t) if t > 216 / 24389 \
            else (24389 / 27 * t + 16) / 116

    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def _lab_to_srgb(lab: tuple[float, float, float]) -> tuple[int, int, int]:
    L, a, b = lab
    fy = (L + 16) / 116
    fx, fz = fy + a / 500, fy - b / 200

    def finv(t):
        return t ** 3 if t ** 3 > 216 / 24389 else (116 * t - 16) / (24389 / 27)

    x, y, z = finv(fx) * 0.95047, finv(fy), finv(fz) * 1.08883
    r = 3.2404542 * x - 1.5371385 * y - 0.4985314 * z
    g = -0.9692660 * x + 1.8760108 * y + 0.0415560 * z
    bl = 0.0556434 * x - 0.2040259 * y + 1.0572252 * z

    def gam(c):
        c = min(max(c, 0.0), 1.0)
        return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055

    return tuple(int(round(gam(c) * 255)) for c in (r, g, bl))  # type: ignore[return-value]


def delta_e00(c1: tuple[int, int, int], c2: tuple[int, int, int]) -> float:
    """CIEDE2000 between two sRGB colours (D65), kL = kC = kH = 1."""
    return delta_e00_lab(_srgb_to_lab(c1), _srgb_to_lab(c2))


def delta_e00_lab(lab1: tuple[float, float, float],
                  lab2: tuple[float, float, float]) -> float:
    """CIEDE2000 between two CIELAB colours — checked against Sharma's table."""
    import math

    L1, a1, b1 = lab1
    L2, a2, b2 = lab2
    C1, C2 = math.hypot(a1, b1), math.hypot(a2, b2)
    Cm = (C1 + C2) / 2
    G = 0.5 * (1 - math.sqrt(Cm ** 7 / (Cm ** 7 + 25 ** 7)))
    a1p, a2p = (1 + G) * a1, (1 + G) * a2
    C1p, C2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360 if C1p else 0.0
    h2p = math.degrees(math.atan2(b2, a2p)) % 360 if C2p else 0.0
    dLp, dCp = L2 - L1, C2p - C1p
    if C1p * C2p == 0:
        dhp = 0.0
    elif abs(h2p - h1p) <= 180:
        dhp = h2p - h1p
    elif h2p - h1p > 180:
        dhp = h2p - h1p - 360
    else:
        dhp = h2p - h1p + 360
    dHp = 2 * math.sqrt(C1p * C2p) * math.sin(math.radians(dhp / 2))
    Lmp, Cmp = (L1 + L2) / 2, (C1p + C2p) / 2
    if C1p * C2p == 0:
        hmp = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        hmp = (h1p + h2p) / 2
    elif h1p + h2p < 360:
        hmp = (h1p + h2p + 360) / 2
    else:
        hmp = (h1p + h2p - 360) / 2
    T = (1 - 0.17 * math.cos(math.radians(hmp - 30))
         + 0.24 * math.cos(math.radians(2 * hmp))
         + 0.32 * math.cos(math.radians(3 * hmp + 6))
         - 0.20 * math.cos(math.radians(4 * hmp - 63)))
    dtheta = 30 * math.exp(-(((hmp - 275) / 25) ** 2))
    Rc = 2 * math.sqrt(Cmp ** 7 / (Cmp ** 7 + 25 ** 7))
    Sl = 1 + 0.015 * (Lmp - 50) ** 2 / math.sqrt(20 + (Lmp - 50) ** 2)
    Sc = 1 + 0.045 * Cmp
    Sh = 1 + 0.015 * Cmp * T
    Rt = -math.sin(math.radians(2 * dtheta)) * Rc
    return math.sqrt((dLp / Sl) ** 2 + (dCp / Sc) ** 2 + (dHp / Sh) ** 2
                     + Rt * (dCp / Sc) * (dHp / Sh))


RGB = tuple[int, int, int]


def shift_colour(rgb: RGB, target: float) -> tuple[RGB, float]:
    """The sRGB colour closest to ΔE00 = `target` from `rgb`, lightness only.

    Hue and chroma stay; L* moves towards the middle — darker for a light
    colour, lighter for a dark one — or the other way when the first runs out
    of gamut. Returns the colour and the ΔE00 it actually has after rounding
    to 8 bits, which is the number the manifest records.
    """
    L, a, b = _srgb_to_lab(rgb)
    best: tuple[tuple[int, int, int], float] | None = None
    for sign in ((-1, 1) if L > 50 else (1, -1)):
        lo, hi = 0.0, 60.0
        for _ in range(40):
            mid = (lo + hi) / 2
            c = _lab_to_srgb((min(max(L + sign * mid, 0.0), 100.0), a, b))
            if delta_e00(rgb, c) < target:
                lo = mid
            else:
                hi = mid
        for dl in (lo, hi):
            c = _lab_to_srgb((min(max(L + sign * dl, 0.0), 100.0), a, b))
            de = delta_e00(rgb, c)
            if best is None or abs(de - target) < abs(best[1] - target):
                best = (c, de)
        if best is not None and abs(best[1] - target) <= 0.25:
            break
    assert best is not None
    return best


def parse_css_colour(value: str) -> tuple[int, int, int]:
    nums = [float(x) for x in value[value.index("(") + 1:value.index(")")]
            .replace("/", " ").replace(",", " ").split()]
    if len(nums) == 4 and nums[3] < 1:
        raise CorpusError(f"a translucent colour cannot be shifted by ΔE: {value}")
    return int(round(nums[0])), int(round(nums[1])), int(round(nums[2]))


def css_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


# --------------------------------------------------------------------------- #
#  SIGNAL — mutations of one element, the label comes from the mutation
# --------------------------------------------------------------------------- #
SIGNAL, NOISE, DISPUTED = "SIGNAL", "NOISE", "DISPUTED"

#: The answers to the three questions the maintainer was asked before the
#: corpus was frozen, and the reasoning that goes with each. A label that is
#: a judgement call says whose judgement it is.
DECIDED = "decided by the maintainer on 2026-09-28"
WHY_DE2 = (f"{DECIDED}: ΔE00 ≈ 2 sits at the edge of what a person sees side "
           "by side (≈1 is the just-noticeable difference). Kept in the corpus "
           "as DISPUTED: every tool's verdict is printed, none is counted as a "
           "false failure or a miss.")
WHY_1PX = (f"{DECIDED}: a layout that moved by one pixel is a layout that "
           "changed. SIGNAL, like the larger steps.")
WHY_OPACITY_098 = (f"{DECIDED}: opacity 0.98 on these backgrounds stays under "
                   "ΔE00 1 — nobody sees it, so failing on it is a false "
                   "failure. NOISE, although the DOM changed.")
WHY_LS_02 = (f"{DECIDED}: +0.2px per letter is invisible in one glyph and "
             "adds up to several pixels over a line — the line got longer. "
             "SIGNAL.")


@dataclass(frozen=True)
class Magnitude:
    key: str                  # file-name safe
    value: object             # what the mutation is parameterised with
    label: str = SIGNAL
    why: str = ""             # empty: the family's reason applies


@dataclass(frozen=True)
class Family:
    key: str
    target: str               # the data-m token of the element it changes
    what: str
    why: str                  # why SIGNAL, by construction
    magnitudes: tuple[Magnitude, ...]
    unit: str = ""


def _de(*steps: float) -> tuple[Magnitude, ...]:
    return tuple(Magnitude(f"de{int(v)}", v, DISPUTED if v <= 2 else SIGNAL,
                           WHY_DE2 if v <= 2 else "") for v in steps)


_COLOUR_WHY = ("the colour in the stylesheet changed; at ΔE00 ≥ 4 the change "
               "is visible side by side, so a tool that stays green missed it")

FAMILIES: tuple[Family, ...] = (
    Family("fill", "fill", "background colour of a filled button", _COLOUR_WHY,
           _de(2, 4, 8, 15), "ΔE00"),
    Family("text_color", "text", "colour of body text", _COLOUR_WHY,
           _de(2, 4, 8, 15), "ΔE00"),
    Family("link_color", "link", "colour of a link", _COLOUR_WHY,
           _de(2, 4, 8, 15), "ΔE00"),
    Family("icon_color", "iconcolor", "stroke colour of an icon", _COLOUR_WHY,
           _de(4, 8, 15), "ΔE00"),
    Family("border_added", "noborder",
           "a 1px border appears on an element that had none",
           "an outline that was not there is drawn around the element",
           (Magnitude("light", "rgba(127,127,127,.35)"),
            Magnitude("strong", "rgba(127,127,127,.9)"))),
    Family("border_removed", "border", "an element's border becomes transparent",
           "the outline of the element disappears",
           (Magnitude("gone", "transparent"),)),
    Family("underline", "underline", "a link gets underlined",
           "a line appears under the text", (Magnitude("on", "underline"),)),
    Family("border_radius", "radius", "corner radius of a button grows",
           "the corners change shape",
           tuple(Magnitude(f"plus{v}px", v) for v in (2, 4, 8)), "px"),
    Family("padding", "pad", "padding on every side of a button",
           "the button changes size and its text moves",
           (Magnitude("minus2px", -2), Magnitude("minus1px", -1, why=WHY_1PX),
            Magnitude("plus1px", 1, why=WHY_1PX), Magnitude("plus2px", 2)), "px"),
    Family("offset", "offset", "the content of a block moves down (padding-top)",
           "the block and everything under it move",
           (Magnitude("plus1px", 1, why=WHY_1PX), Magnitude("plus2px", 2),
            Magnitude("plus4px", 4)), "px"),
    Family("font_size", "fsize", "font size of a heading",
           "the heading is drawn at another size",
           (Magnitude("minus1px", -1), Magnitude("plus1px", 1)), "px"),
    Family("font_weight", "weight", "a label goes from regular to bold",
           "the glyphs are drawn heavier", (Magnitude("bold", 700),)),
    Family("letter_spacing", "spacing", "letter spacing of a heading or a menu",
           "the text gets wider",
           (Magnitude("plus0.2px", 0.2, why=WHY_LS_02), Magnitude("plus0.5px", 0.5),
            Magnitude("plus1px", 1.0)), "px"),
    Family("line_height", "lh", "line height of a paragraph",
           "every line after the first moves",
           (Magnitude("plus1px", 1, why=WHY_1PX), Magnitude("plus2px", 2),
            Magnitude("plus4px", 4)), "px"),
    Family("icon_swap", "icon", "one icon replaced by another of the same size",
           "a different picture in the same place", (Magnitude("swapped", "data-icon"),)),
    Family("one_char", "char", "one character of a number changes",
           "the number is different", (Magnitude("one", "data-char"),)),
    Family("word_swap", "word", "one word replaced by another",
           "the text is different", (Magnitude("one", "data-word"),)),
    Family("element_removed", "remove", "a small element is removed (display:none)",
           "the element is gone and its neighbours close the gap",
           (Magnitude("gone", "none"),)),
    Family("order_swap", "order", "two neighbours swap places",
           "the two elements are in each other's place",
           (Magnitude("swapped", 1),)),
    Family("opacity", "opacity", "opacity of a button",
           "the button is visibly paler",
           (Magnitude("0.98", 0.98, NOISE, WHY_OPACITY_098), Magnitude("0.9", 0.9),
            Magnitude("0.8", 0.8), Magnitude("0.6", 0.6))),
    Family("shadow", "shadow", "the shadow of a card",
           "the card's edge is drawn differently",
           (Magnitude("removed", "none"),
            Magnitude("heavier", "0 8px 24px rgba(0,0,0,.35)"))),
    Family("focus_ring", "focus", "a 2px focus outline appears on a control",
           "a ring is drawn around the control",
           (Magnitude("2px", "2px solid #3b82f6"),)),
)

#: CSS property per colour family.
_COLOUR_PROP = {"fill": "background-color", "text_color": "color",
                "link_color": "color", "icon_color": "color"}

#: The JavaScript side of every non-colour mutation: `els` are the targets,
#: `v` the magnitude's value. Computed values are read before anything is
#: written, so a step is relative to what the template draws.
_JS = {
    "border_added":
        "els.forEach(e => e.style.setProperty('border', '1px solid ' + v, 'important'))",
    "border_removed": "els.forEach(e => e.style.setProperty('border-color', v, 'important'))",
    "underline": "els.forEach(e => e.style.setProperty('text-decoration', v, 'important'))",
    "border_radius": """els.forEach(e => { const c = getComputedStyle(e);
        for (const k of ['top-left', 'top-right', 'bottom-right', 'bottom-left'])
          e.style.setProperty(`border-${k}-radius`,
            (parseFloat(c.getPropertyValue(`border-${k}-radius`)) + v) + 'px',
            'important'); })""",
    "padding": """els.forEach(e => { const c = getComputedStyle(e);
        for (const k of ['top', 'right', 'bottom', 'left'])
          e.style.setProperty(`padding-${k}`,
            Math.max(0, parseFloat(c.getPropertyValue(`padding-${k}`)) + v) + 'px',
            'important'); })""",
    #  padding-top and not margin-top: a margin collapses with the one above
    #  it, and on the article template +1 and +2px moved nothing at all.
    "offset": """els.forEach(e => e.style.setProperty('padding-top',
        (parseFloat(getComputedStyle(e).paddingTop) + v) + 'px', 'important'))""",
    "font_size": """els.forEach(e => e.style.setProperty('font-size',
        (parseFloat(getComputedStyle(e).fontSize) + v) + 'px', 'important'))""",
    "font_weight":
        "els.forEach(e => e.style.setProperty('font-weight', String(v), 'important'))",
    "letter_spacing": """els.forEach(e => { const ls = getComputedStyle(e).letterSpacing;
        e.style.setProperty('letter-spacing',
          ((ls === 'normal' ? 0 : parseFloat(ls)) + v) + 'px', 'important'); })""",
    "line_height": """els.forEach(e => { const lh = getComputedStyle(e).lineHeight;
        if (lh === 'normal') throw new Error('line-height: normal on a lh target');
        e.style.setProperty('line-height', (parseFloat(lh) + v) + 'px', 'important'); })""",
    "icon_swap": """els.forEach(e => { const d = e.getAttribute('data-icon');
        if (!d) throw new Error('icon target without data-icon');
        e.innerHTML = '<path d="' + d + '"/>'; })""",
    "one_char": """els.forEach(e => { const t = e.getAttribute('data-char');
        if (!t) throw new Error('char target without data-char');
        if (e.tagName === 'INPUT') { e.value = t; e.setAttribute('value', t); }
        else e.textContent = t; })""",
    "word_swap": """els.forEach(e => { const t = e.getAttribute('data-word');
        if (!t) throw new Error('word target without data-word');
        e.textContent = t; })""",
    "element_removed": "els.forEach(e => e.style.setProperty('display', v, 'important'))",
    "order_swap": """els.forEach(e => { if (e.children.length < 2)
          throw new Error('order target with fewer than two children');
        e.insertBefore(e.children[1], e.children[0]); })""",
    "opacity": "els.forEach(e => e.style.setProperty('opacity', String(v), 'important'))",
    "shadow": "els.forEach(e => e.style.setProperty('box-shadow', v, 'important'))",
    "focus_ring": """els.forEach(e => { e.style.setProperty('outline', v, 'important');
        e.style.setProperty('outline-offset', '2px', 'important'); })""",
}


def _targets(family: Family) -> str:
    return f'[data-m~="{family.target}"]'


def plan_mutation(session: Session, template: str, family: Family,
                  mag: Magnitude) -> tuple[tuple[str, str, object], dict]:
    """What to run on the page, and what it changes, in numbers, for the manifest.

    A colour step is computed here, from the colour the template actually
    draws (read off the unchanged page), so the page itself only ever
    receives a fixed value.
    """
    sel = _targets(family)
    if family.key in _COLOUR_PROP:
        prop = _COLOUR_PROP[family.key]
        before = session.read(
            template, f"() => getComputedStyle(document.querySelector('{sel}'))"
                      f".getPropertyValue('{prop}')")
        rgb = parse_css_colour(before)
        new, de = shift_colour(rgb, float(mag.value))  # type: ignore[arg-type]
        code = f"els.forEach(e => e.style.setProperty('{prop}', v, 'important'))"
        return (sel, code, css_hex(new)), {
            "property": prop, "before": css_hex(rgb), "after": css_hex(new),
            "delta_e00": round(de, 2)}
    return (sel, _JS[family.key], mag.value), {}


# --------------------------------------------------------------------------- #
#  NOISE — the same DOM, another rendering configuration
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class NoiseConfig:
    key: str
    what: str
    launch: Launch


NOISE_CONFIGS: tuple[NoiseConfig, ...] = (
    NoiseConfig("hinting_none", "--font-render-hinting=none",
                Launch(args=("--font-render-hinting=none",))),
    NoiseConfig("hinting_full", "--font-render-hinting=full",
                Launch(args=("--font-render-hinting=full",))),
    NoiseConfig("no_lcd_no_subpixel",
                "--disable-lcd-text with --disable-font-subpixel-positioning",
                Launch(args=("--disable-lcd-text",
                             "--disable-font-subpixel-positioning"))),
    NoiseConfig("geometric_precision", "text-rendering: geometricPrecision",
                Launch(css="*, *::before, *::after "
                           "{ text-rendering: geometricPrecision !important; }")),
    NoiseConfig("shift_0.25px", "the whole page moved by transform: translate(0.25px, 0.25px)",
                Launch(css="body { transform: translate(0.25px, 0.25px); }")),
    NoiseConfig("shift_0.5px", "the whole page moved by transform: translate(0.5px, 0.5px)",
                Launch(css="body { transform: translate(0.5px, 0.5px); }")),
    NoiseConfig("full_chromium", "full Chromium (channel=chromium) instead of the "
                "headless shell", Launch(channel="chromium")),
    NoiseConfig("gpu_raster", "--enable-gpu-rasterization --force-gpu-rasterization",
                Launch(args=("--enable-gpu-rasterization",
                             "--force-gpu-rasterization"))),
)

WHY_NOISE = ("the DOM is the template's, untouched; only the way Chromium was "
             "asked to render it differs — nobody changed the page")


# --------------------------------------------------------------------------- #
#  The renderer's fingerprint
# --------------------------------------------------------------------------- #
#: Where the canary of every rendering configuration is kept.
RENDERERS_DIR = CORPUS_DIR / "renderers"

#: The renderer of the corpus baselines and of every mutation pair.
BASE_RENDERER = "base"

#: Configurations whose canary must come out as the baseline's: they draw the
#: templates to the baseline's pixels, so a canary that told them apart
#: would be telling apart what is not different.
RENDERER_CONTROLS = ("hinting_full", "gpu_raster")

WHY_CANARY_CSS = ("the configuration is a stylesheet of the page, not a way of "
                  "starting the browser: the canary tab never sees the page's "
                  "CSS, so it is drawn by the baseline's renderer")


def renderer_file(key: str) -> str:
    return f"renderers/{key}.png"


def renderer_launch(launch: Launch) -> Launch:
    """How the browser was started, without the page's stylesheet."""
    return Launch(args=launch.args, channel=launch.channel)


def canary_record() -> dict:
    """What the fingerprints were drawn from."""
    from vistest.library import canary

    return {"version": canary.CANARY_VERSION, "page_sha256": canary.page_sha256(),
            "page": "vistest/library/canary.py",
            "what": "a fixed page — the shipped font at three sizes, bold, "
                    "italic, light on dark; the machine's serif, sans-serif "
                    "and monospace — drawn in a tab of its own through the "
                    "library's capture"}


# --------------------------------------------------------------------------- #
#  SIGNAL drawn by another renderer
# --------------------------------------------------------------------------- #
#: The rendering configurations real changes are drawn under, against the
#: corpus baseline: a page that changed AND a renderer that changed.
CROSS_CONFIGS = ("hinting_none", "full_chromium")

#: (family, magnitudes) taken from `FAMILIES`, SIGNAL here as they are there:
#: a colour, a picture, a character, a word, an outline, an element — changes
#: that no renderer makes on its own.
CROSS_SIGNAL: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("text_color", ("de8", "de15")), ("link_color", ("de8", "de15")),
    ("icon_color", ("de8", "de15")), ("fill", ("de8", "de15")),
    ("element_removed", ("gone",)), ("icon_swap", ("swapped",)),
    ("one_char", ("one",)), ("word_swap", ("one",)),
    ("border_added", ("light", "strong")), ("border_removed", ("gone",)),
    ("underline", ("on",)),
)

#: Geometry within two pixels, DISPUTED under another renderer.
CROSS_DISPUTED: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("padding", ("minus2px", "minus1px", "plus1px", "plus2px")),
    ("offset", ("plus1px", "plus2px")),
    ("letter_spacing", ("plus1px",)),
    ("font_size", ("minus1px", "plus1px")),
)

#: What of the family is not in the frozen corpus, and why. Measured on
#: 2026-09-29: the whole family is 300 pairs, 33 027 156 bytes of PNG
#: (≈1.26 MiB per magnitude over six templates and two configurations);
#: the corpus held 27 201 301 of its 41 943 040, which leaves room for 11
#: magnitudes and nothing else. Cut, in this order: the DISPUTED geometry
#: (printed, never counted — 9 magnitudes); the second colour step, ΔE00 15,
#: keeping the harder 8 (4); the second border, keeping the lighter (1);
#: the link colour, which is text colour on another element and tests the
#: same property (1). Left in: 10 magnitudes, 120 pairs. To put the rest
#: back: raise BUDGET_BYTES to 64 MiB, empty this set, --regenerate.
CROSS_LEFT_OUT: frozenset[tuple[str, str]] = frozenset(
    [(k, m) for k, mags in CROSS_DISPUTED for m in mags]
    + [(k, "de15") for k in ("text_color", "link_color", "icon_color", "fill")]
    + [("border_added", "strong"), ("link_color", "de8")])
WHY_CROSS_LEFT_OUT = (
    "the corpus budget (40 MiB): the whole family is 33.0 MB of PNG, the room "
    "was 14.7 MB. Left out: the DISPUTED geometry, the ΔE00 15 steps, the "
    "strong border and the link colour; defined here, drawn by --regenerate "
    "once the budget allows")

WHY_CROSS_SIGNAL = ("the page changed and the renderer changed too: {what}, drawn "
                    "with {config}, against the corpus baseline. No renderer "
                    "makes this change on its own; a tool that stays green "
                    "missed it")
WHY_CROSS_DISPUTED = (
    "decided by the maintainer on 2026-09-29: geometry within two "
    "pixels, drawn by another renderer. The renderer on its own moves glyphs "
    "and line boxes by a pixel, so on this pair a layout change of that size "
    "cannot be told from re-rasterisation by the pixels — printed, never "
    "counted, like ΔE00 ≈ 2")


def cross_family(family: str, config: str) -> str:
    return f"{family}@{config}"


def cross_plan(*, everything: bool = False) -> list[tuple[Family, Magnitude, str, str]]:
    """(family, magnitude, config, label) of every pair of the family.

    Without `everything`, what `CROSS_LEFT_OUT` names is left out.
    """
    fams = {f.key: f for f in FAMILIES}
    out = []
    for config in CROSS_CONFIGS:
        for group, label in ((CROSS_SIGNAL, SIGNAL), (CROSS_DISPUTED, DISPUTED)):
            for key, mags in group:
                fam = fams[key]
                by = {m.key: m for m in fam.magnitudes}
                for m in mags:
                    if everything or (key, m) not in CROSS_LEFT_OUT:
                        out.append((fam, by[m], config, label))
    return out


def cross_why(label: str, family: str, config: str) -> str:
    if label == DISPUTED:
        return WHY_CROSS_DISPUTED
    fam = next(f for f in FAMILIES if f.key == family)
    cfg = next(c for c in NOISE_CONFIGS if c.key == config)
    return WHY_CROSS_SIGNAL.format(what=fam.what, config=cfg.what)


def cross_record() -> dict:
    """The definition, as the manifest keeps it next to the frames."""
    return {
        "what": "real changes drawn by another renderer: the mutation of the "
                "family named before '@', drawn in the configuration named "
                "after it, against the corpus baseline",
        "configs": list(CROSS_CONFIGS),
        SIGNAL: {k: list(m) for k, m in CROSS_SIGNAL},
        DISPUTED: {k: list(m) for k, m in CROSS_DISPUTED},
        "why": {SIGNAL: WHY_CROSS_SIGNAL.format(what="<the family's change>",
                                                config="<the configuration>"),
                DISPUTED: WHY_CROSS_DISPUTED},
        "left_out": {"magnitudes": sorted(f"{k}/{m}" for k, m in CROSS_LEFT_OUT),
                     "why": WHY_CROSS_LEFT_OUT},
    }


# --------------------------------------------------------------------------- #
#  Capture
# --------------------------------------------------------------------------- #
def case_name(template: str, family: str, magnitude: str) -> str:
    return f"{template}/{family}/{magnitude}"


def frame_file(template: str, stem: str) -> str:
    return f"frames/{template}/{stem}.png"


def _changed_box(a: bytes, b: bytes) -> dict:
    import numpy as np

    pa, pb = pixels(a), pixels(b)
    diff = np.any(pa != pb, axis=2)
    ys, xs = np.nonzero(diff)
    if not len(xs):
        return {"pixels": 0}
    return {"pixels": int(diff.sum()),
            "box": [int(xs.min()), int(ys.min()),
                    int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)]}


def capture_all(out: Path, *, log=print) -> dict:
    """Every frame of the corpus into `out`, and the record of how it was drawn.

    Every frame is drawn twice, in two browsers started the same way, and the
    two must agree to the pixel — or this raises and nothing is written as a
    result. The same holds for each rendering configuration.
    """
    from playwright.sync_api import sync_playwright

    out.mkdir(parents=True, exist_ok=True)
    doc: dict = {"templates": {}, "cases": [], "no_pixel_change": [],
                 "noise_configs": {}}
    bases: dict[str, bytes] = {}

    def write(rel: str, png: bytes) -> None:
        path = out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(pack(png))

    def twice(a: Session, b: Session, template: str, what: str, mutation=None) -> bytes:
        png = a.frame(template, mutation)
        n = differing_pixels(png, b.frame(template, mutation))
        if n:
            raise CorpusError(f"{what}: a second browser started the same way drew "
                              f"{n} different pixels — stop: nothing measured on "
                              "such frames would mean anything")
        return png

    with sync_playwright() as pw:
        doc["environment"] = environment(pw)

        # ---- bases and every mutation ------------------------------------ #
        first, second = Session(pw), Session(pw)
        try:
            for t in TEMPLATES:
                base, fonts = first.frame(t.key, fonts=True)
                n = differing_pixels(base, second.frame(t.key))
                if n:
                    raise CorpusError(f"{t.key}: a second browser drew {n} different "
                                      "pixels of the unchanged template — stop")
                bases[t.key] = base
                write(frame_file(t.key, "base"), base)
                doc["templates"][t.key] = {"title": t.title, "fonts": fonts,
                                           "base": frame_file(t.key, "base")}
                made = 0
                for fam in FAMILIES:
                    for mag in fam.magnitudes:
                        name = case_name(t.key, fam.key, mag.key)
                        mutation, detail = plan_mutation(first, t.key, fam, mag)
                        png = twice(first, second, t.key, name, mutation)
                        changed = _changed_box(base, png)
                        if not changed["pixels"]:
                            doc["no_pixel_change"].append(name)
                            log(f"  {name}: no pixel changed, left out")
                            continue
                        rel = frame_file(t.key, f"{fam.key}--{mag.key}")
                        write(rel, png)
                        made += 1
                        doc["cases"].append({
                            "name": name, "template": t.key, "family": fam.key,
                            "magnitude": mag.key, "label": mag.label,
                            "kind": "mutation", "actual": rel, "changed": changed,
                            **({"detail": detail} if detail else {}),
                        })
                log(f"{t.key}: base and {made} mutations")
        finally:
            first.close()
            second.close()

        # ---- rendering configurations ------------------------------------ #
        for cfg in NOISE_CONFIGS:
            a, b = Session(pw, cfg.launch), Session(pw, cfg.launch)
            record: dict = {"what": cfg.what, "pixels": {}}
            try:
                if cfg.launch.channel:
                    record["browser"] = browser_identity(
                        a.browser, pw.chromium.executable_path)
                for t in TEMPLATES:
                    png = twice(a, b, t.key, f"noise config {cfg.key}, {t.key}")
                    changed = _changed_box(bases[t.key], png)
                    record["pixels"][t.key] = changed["pixels"]
                    if not changed["pixels"]:
                        continue
                    rel = frame_file(t.key, f"noise--{cfg.key}")
                    write(rel, png)
                    doc["cases"].append({
                        "name": case_name(t.key, "render", cfg.key),
                        "template": t.key, "family": f"render:{cfg.key}",
                        "magnitude": cfg.key, "label": NOISE, "kind": "render",
                        "actual": rel, "changed": changed,
                    })
            finally:
                a.close()
                b.close()
            record["control_pixels"] = 0
            doc["noise_configs"][cfg.key] = record
            zero = [k for k, v in record["pixels"].items() if not v]
            log(f"noise {cfg.key}: {len(TEMPLATES) - len(zero)} templates differ"
                + (f", 0 px on {', '.join(zero)}" if zero else ""))

        # ---- the renderer of every configuration ------------------------- #
        doc["renderers"] = capture_renderers(pw, write, log=log)

        # ---- real changes drawn by another renderer ----------------------- #
        doc["cases"] += capture_cross(pw, bases, write, log=log)
    return doc


def capture_renderers(pw, write, *, log=print) -> dict:
    """The canary of the baseline and of every rendering configuration.

    Drawn the way every frame is: in two browsers started the same way,
    which must agree to the pixel. A configuration that is a stylesheet of
    the page is drawn by the baseline's launch — the canary tab never sees
    the page's CSS. Returns `{key: record}`; the PNGs go through `write`.
    """
    launches = [(BASE_RENDERER, BASELINE, "")] + [
        (c.key, renderer_launch(c.launch), WHY_CANARY_CSS if c.launch.css else "")
        for c in NOISE_CONFIGS]
    drawn: dict[str, bytes] = {}
    out: dict[str, dict] = {}
    for key, launch, note in launches:
        a, b = Session(pw, launch), Session(pw, launch)
        try:
            png = a.canary()
            n = differing_pixels(png, b.canary())
        finally:
            a.close()
            b.close()
        if n:
            raise CorpusError(f"canary {key}: a second browser started the same way "
                              f"drew {n} different pixels — stop")
        drawn[key] = png
        write(renderer_file(key), png)
        out[key] = {"file": renderer_file(key),
                    "drawn_with": {"args": list(launch.args),
                                   **({"channel": launch.channel}
                                      if launch.channel else {})},
                    "pixels_vs_base": differing_pixels(drawn[BASE_RENDERER], png),
                    **({"note": note} if note else {}),
                    **({"control": "must come out as the baseline's"}
                       if key in RENDERER_CONTROLS else {})}
        log(f"canary {key}: {out[key]['pixels_vs_base']} px from the baseline's")
    return out


def capture_cross(pw, bases: dict[str, bytes], write, *, log=print,
                  templates: tuple[Template, ...] = TEMPLATES,
                  everything: bool = False) -> list[dict]:
    """The family 'real changes drawn by another renderer', every template.

    The mutation is drawn in the configuration, twice, in two browsers that
    must agree; the pair is the corpus baseline against it.
    """
    cases: list[dict] = []
    plan = cross_plan(everything=everything)
    for config in CROSS_CONFIGS:
        cfg = next(c for c in NOISE_CONFIGS if c.key == config)
        a, b = Session(pw, cfg.launch), Session(pw, cfg.launch)
        try:
            for t in templates:
                made = 0
                for fam, mag, cfg_key, label in plan:
                    if cfg_key != config:
                        continue
                    family = cross_family(fam.key, config)
                    name = case_name(t.key, family, mag.key)
                    mutation, detail = plan_mutation(a, t.key, fam, mag)
                    png = a.frame(t.key, mutation)
                    n = differing_pixels(png, b.frame(t.key, mutation))
                    if n:
                        raise CorpusError(f"{name}: a second browser started the "
                                          f"same way drew {n} different pixels — stop")
                    changed = _changed_box(bases[t.key], png)
                    rel = frame_file(t.key, f"{fam.key}--{mag.key}--{config}")
                    write(rel, png)
                    made += 1
                    cases.append({
                        "name": name, "template": t.key, "family": family,
                        "magnitude": mag.key, "label": label, "kind": "cross_render",
                        "actual": rel, "changed": changed,
                        "detail": {"mutation": fam.key, "config": config, **detail},
                    })
                log(f"{t.key}: {made} changes drawn with {config}")
        finally:
            a.close()
            b.close()
    return cases


# --------------------------------------------------------------------------- #
#  Freezing
# --------------------------------------------------------------------------- #
def _split_of(template: str) -> str:
    return next(t.split for t in TEMPLATES if t.key == template)


def _case_why(case: dict) -> str:
    if case["kind"] == "render":
        cfg = next(c for c in NOISE_CONFIGS if c.key == case["magnitude"])
        return f"{WHY_NOISE} ({cfg.what})"
    if case["kind"] == "cross_render":
        return cross_why(case["label"], case["detail"]["mutation"],
                         case["detail"]["config"])
    fam = next(f for f in FAMILIES if f.key == case["family"])
    mag = next(m for m in fam.magnitudes if m.key == case["magnitude"])
    return mag.why or fam.why


def families_record() -> dict:
    """The definitions, as the manifest keeps them next to the frames."""
    return {f.key: {
        "target": f.target, "what": f.what, "why": f.why, "unit": f.unit,
        "magnitudes": {m.key: {"value": m.value, "label": m.label,
                               **({"why": m.why} if m.why else {})}
                       for m in f.magnitudes}}
        for f in FAMILIES}


def _renderer_of(case: dict, fingerprints: dict) -> str | None:
    """The key of the canary drawn by the renderer of the pair's second frame."""
    kind = case["kind"]
    if kind == "mutation":
        return BASE_RENDERER
    if kind == "render":
        return case["magnitude"]
    if kind == "cross_render":
        return case["detail"]["config"]
    if kind == "os":
        return case["family"] if case["family"] in fingerprints else None
    raise CorpusError(f"{case['name']}: no renderer for a pair of kind {kind!r}")


def renderers_section(records: dict, root: Path) -> dict:
    """The `renderers` section: what the canary is and every fingerprint."""
    return {
        "canary": canary_record(),
        "rule": "every frame names the canary its renderer drew: "
                "templates.<t>.renderer for a baseline, cases[].renderer."
                "expected and .actual for a pair; null where no canary was "
                "drawn by that renderer",
        "fingerprints": {k: {**r, "sha256": sha256((root / r["file"]).read_bytes())}
                         for k, r in records.items()},
    }


def build_manifest(doc: dict, root: Path) -> dict:
    """`capture_all`'s record plus the sha256 of every file under `root`."""
    renderers = renderers_section(doc["renderers"], root)
    fingerprints = renderers["fingerprints"]
    templates = {}
    for t in TEMPLATES:
        rec = doc["templates"][t.key]
        templates[t.key] = {
            "title": rec["title"], "split": t.split, "base": rec["base"],
            "sha256": sha256((root / rec["base"]).read_bytes()), "fonts": rec["fonts"],
            "renderer": BASE_RENDERER}
    cases = []
    for c in doc["cases"]:
        cases.append({
            "name": c["name"], "template": c["template"],
            "split": _split_of(c["template"]), "family": c["family"],
            "magnitude": c["magnitude"], "label": c["label"], "kind": c["kind"],
            "why": _case_why(c),
            "expected": templates[c["template"]]["base"], "actual": c["actual"],
            "sha256": sha256((root / c["actual"]).read_bytes()),
            "changed": c["changed"], **({"detail": c["detail"]} if "detail" in c else {}),
            "renderer": {"expected": BASE_RENDERER,
                         "actual": _renderer_of(c, fingerprints)},
        })
    configs = {}
    for cfg in NOISE_CONFIGS:
        rec = doc["noise_configs"][cfg.key]
        zero = not any(rec["pixels"].values())
        configs[cfg.key] = {
            "what": cfg.what, "args": list(cfg.launch.args),
            **({"channel": cfg.launch.channel} if cfg.launch.channel else {}),
            **({"css": cfg.launch.css} if cfg.launch.css else {}),
            **({"browser": rec["browser"]} if "browser" in rec else {}),
            "pixels_vs_baseline": rec["pixels"],
            "in_corpus": not zero,
            "control_pixels": rec["control_pixels"],
        }
    return {
        "format": 1,
        "about": "Browser corpus of VisTest: frames rendered by Chromium from "
                 "tests/browser_corpus/templates, labels known before the "
                 "frame exists. Written by `python scripts/browser_corpus.py "
                 "--regenerate`; do not edit by hand.",
        "environment": doc["environment"],
        "control": "every frame drawn in two browsers started the same way; "
                   "0 pixels apart, or the capture stops",
        "split": {
            "rule": SPLIT_RULE,
            CALIBRATION: [t.key for t in TEMPLATES if t.split == CALIBRATION],
            HELD_OUT: [t.key for t in TEMPLATES if t.split == HELD_OUT],
        },
        "labels": {
            SIGNAL: "a change a person sees; a tool that stays green missed it",
            NOISE: "nothing a person sees changed; a tool that fails is wrong",
            DISPUTED: "judgement call left open: printed, never counted",
        },
        "templates": templates,
        "families": families_record(),
        "cross_render": cross_record(),
        "noise_configs": configs,
        "renderers": renderers,
        "no_pixel_change": doc["no_pixel_change"],
        "cases": cases,
    }


def write_manifest(manifest: dict, path: Path = MANIFEST) -> None:
    path.write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n",
                    encoding="utf-8", newline="\n")


def load_manifest(path: Path = MANIFEST) -> dict:
    if not path.is_file():
        raise CorpusError(f"{path} does not exist; the browser corpus is kept in "
                          "the repository. To draw it: "
                          "`python scripts/browser_corpus.py --regenerate`")
    return json.loads(path.read_text("utf-8"))


def frozen_files(manifest: dict) -> dict[str, str]:
    """Every PNG the manifest vouches for: relative path -> sha256."""
    out = {t["base"]: t["sha256"] for t in manifest["templates"].values()}
    out.update({c["actual"]: c["sha256"] for c in manifest["cases"]})
    fingerprints = (manifest.get("renderers") or {}).get("fingerprints") or {}
    out.update({r["file"]: r["sha256"] for r in fingerprints.values()})
    return out


def verify_files(manifest: dict, root: Path = CORPUS_DIR) -> list[str]:
    """What is wrong with the files on disk, against the manifest. Empty: nothing."""
    problems = []
    want = frozen_files(manifest)
    on_disk = {p.relative_to(root).as_posix()
               for d in ("frames", "renderers") for p in (root / d).rglob("*.png")}
    for rel in sorted(set(want) - on_disk):
        problems.append(f"missing: {rel}")
    for rel in sorted(on_disk - set(want)):
        problems.append(f"not in the manifest: {rel}")
    for rel in sorted(set(want) & on_disk):
        if sha256((root / rel).read_bytes()) != want[rel]:
            problems.append(f"sha256 differs: {rel}")
    total = sum((root / rel).stat().st_size for rel in on_disk)
    if total > BUDGET_BYTES:
        problems.append(f"{total} bytes of PNG, over the {BUDGET_BYTES} budget")
    return problems


def corpus_digest(manifest_path: Path = MANIFEST) -> str:
    """sha256 over the manifest and every PNG, in manifest order.

    The same formula as `tests/benchmark.py::corpus_digest` for the synthetic
    corpus: sha256 of the lines `"<path> <sha256 of the file>\n"`.
    """
    root = manifest_path.parent
    raw = manifest_path.read_bytes()
    manifest = json.loads(raw.decode("utf-8"))
    lines = [f"manifest.json {sha256(raw)}\n"]
    for t in manifest["templates"].values():
        lines.append(f"{t['base']} {sha256((root / t['base']).read_bytes())}\n")
    for c in manifest["cases"]:
        lines.append(f"{c['actual']} {sha256((root / c['actual']).read_bytes())}\n")
    return sha256("".join(lines).encode("utf-8"))


def regenerate(log=print) -> dict:
    """Redraw the whole corpus into place. A deliberate act, never a side effect."""
    import shutil
    import tempfile

    old = load_manifest() if MANIFEST.is_file() else None
    staging = Path(tempfile.mkdtemp(prefix="vistest-browser-corpus-"))
    try:
        doc = capture_all(staging, log=log)
        manifest = build_manifest(doc, staging)
        manifest, _ = carry_os_noise(old, CORPUS_DIR, manifest, staging, log=log)
        problems = verify_files(manifest, staging)
        if problems:
            raise CorpusError("the fresh corpus does not check out: " + "; ".join(problems))
        for rel in ("frames", "renderers"):
            if (CORPUS_DIR / rel).exists():
                shutil.rmtree(CORPUS_DIR / rel)
            shutil.copytree(staging / rel, CORPUS_DIR / rel)
        write_manifest(manifest)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return manifest


def sample(manifest: dict, *, full: bool = False) -> list[dict]:
    """The mutation pairs a drift check redraws.

    All of them with `full`. Otherwise one magnitude of every family on every
    template, rotating with the template's position, so that every magnitude
    is redrawn on some template: about a third of the pairs, every family on
    every template, in about a minute.
    """
    cases = [c for c in manifest["cases"] if c["kind"] == "mutation"]
    if full:
        return cases
    order = [t.key for t in TEMPLATES]
    by_family: dict[tuple[str, str], list[dict]] = {}
    for c in cases:
        by_family.setdefault((c["template"], c["family"]), []).append(c)
    out = []
    for (template, _), group in by_family.items():
        out.append(group[order.index(template) % len(group)])
    return out


def drift(manifest: dict, *, full: bool = False, pw=None,
          log=None) -> tuple[list[str], list[str]]:
    """Redraw frames and compare them with the frozen ones, pixel for pixel.

    Returns `(reasons_not_to, drifted)`. When the environment differs from the
    recorded one, nothing is drawn: the reasons say what differs, and a frame
    from another Chromium would be a different frame, not a drift.
    """
    if pw is None:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as own:
            return drift(manifest, full=full, pw=own, log=log)

    root = CORPUS_DIR
    frozen = lambda rel: (root / rel).read_bytes()  # noqa: E731
    drifted: list[str] = []
    reasons = env_mismatch(manifest["environment"], environment(pw))
    if reasons:
        return reasons, []
    s = Session(pw)
    try:
        for key, t in manifest["templates"].items():
            if differing_pixels(frozen(t["base"]), s.frame(key)):
                drifted.append(f"{key}/base")
        fams = {f.key: f for f in FAMILIES}
        for c in sample(manifest, full=full):
            fam = fams[c["family"]]
            mag = next(m for m in fam.magnitudes if m.key == c["magnitude"])
            mutation, _ = plan_mutation(s, c["template"], fam, mag)
            n = differing_pixels(frozen(c["actual"]), s.frame(c["template"], mutation))
            if n:
                drifted.append(f"{c['name']} ({n} px)")
            if log:
                log(c["name"])
    finally:
        s.close()
    by_name = {c["name"]: c for c in manifest["cases"]}
    cross = [c for c in manifest["cases"] if c["kind"] == "cross_render"]
    if not full:
        cross = sample_cross(cross)
    for cfg in NOISE_CONFIGS:
        s = Session(pw, cfg.launch)
        try:
            for key, t in manifest["templates"].items():
                case = by_name.get(case_name(key, "render", cfg.key))
                want = frozen(case["actual"] if case else t["base"])
                n = differing_pixels(want, s.frame(key))
                if n:
                    drifted.append(f"{key}/render/{cfg.key} ({n} px)")
            for c in (c for c in cross if c["detail"]["config"] == cfg.key):
                fam = fams[c["detail"]["mutation"]]
                mag = next(m for m in fam.magnitudes if m.key == c["magnitude"])
                mutation, _ = plan_mutation(s, c["template"], fam, mag)
                n = differing_pixels(frozen(c["actual"]),
                                     s.frame(c["template"], mutation))
                if n:
                    drifted.append(f"{c['name']} ({n} px)")
        finally:
            s.close()
    fingerprints = (manifest.get("renderers") or {}).get("fingerprints") or {}
    launches = {BASE_RENDERER: BASELINE,
                **{c.key: renderer_launch(c.launch) for c in NOISE_CONFIGS}}
    for key, rec in fingerprints.items():
        if key not in launches:
            continue                    # another machine's: drift_os
        s = Session(pw, launches[key])
        try:
            n = differing_pixels(frozen(rec["file"]), s.canary())
        finally:
            s.close()
        if n:
            drifted.append(f"canary {key} ({n} px)")
    return [], drifted


def sample_cross(cases: list[dict]) -> list[dict]:
    """One pair of the cross-renderer family per template and mutation,
    rotating over configurations and magnitudes like `sample`."""
    order = [t.key for t in TEMPLATES]
    groups: dict[tuple[str, str], list[dict]] = {}
    for c in cases:
        groups.setdefault((c["template"], c["detail"]["mutation"]), []).append(c)
    return [g[order.index(t) % len(g)] for (t, _), g in groups.items()]


# --------------------------------------------------------------------------- #
#  Noise from another machine
# --------------------------------------------------------------------------- #
#: What `--capture-noise-only` writes next to the frames.
OS_ENV_FILE = "environment.json"
OS_CANARY_FILE = "canary.png"
OS_NOISE_KIND = "vistest-browser-corpus-os-noise"
OS_NOISE_FORMAT = 1
OS_NOISE_OUT = Path("bench_out") / "os_noise"

_TAG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

#: How the frame was taken. It has to be the corpus's, or the pair would
#: measure a different capture, not a different machine.
CAPTURE_KEYS = ("viewport", "device_scale_factor", "capture")

#: What the record of another machine is compared on, against the corpus,
#: to say how the two environments differ. Written down in full, hidden
#: nowhere: a different Chromium build on the other machine is part of what
#: the pair measures, and the manifest says so.
DIFF_KEYS = ("playwright", "chromium", "browser_build", "browser_product", "os",
             "os_version", "arch", "distro", "python", "host", *CAPTURE_KEYS)

WHY_OS = ("the DOM is the template's, untouched, and so is the capture; only "
          "the machine that drew it differs — {what}. Nobody changed the page")


def os_family(os_name: str) -> str:
    """`os_windows` for Windows: the NOISE family of frames from that OS."""
    slug = re.sub(r"[^a-z0-9]+", "_", os_name.lower()).strip("_")
    if not slug:
        raise CorpusError(f"no OS name to make a family of: {os_name!r}")
    return f"os_{slug}"


def os_frame_file(template: str, family: str) -> str:
    return frame_file(template, f"os--{family[len('os_'):]}")


def env_differences(corpus: dict, here: dict, keys=DIFF_KEYS) -> list[dict]:
    """Every recorded fact that differs between two environments."""
    return [{"key": k, "corpus": corpus.get(k), "here": here.get(k)}
            for k in keys if corpus.get(k) != here.get(k)]


def os_env_mismatch(recorded: dict, here: dict) -> list[str]:
    """What differs between the machine a frame came from and this one.

    The corpus's own keys, and the OS version and font smoothing on top:
    DirectWrite and ClearType settings belong to the OS, not to Chromium.
    The DPI is recorded but not compared — the context draws at a device
    scale factor of 1 whatever the screen is, and the page checks it.
    """
    out = env_mismatch(recorded, here)
    if recorded.get("os_version") != here.get("os_version"):
        out.append(f"os_version: recorded {recorded.get('os_version')!r}, "
                   f"here {here.get('os_version')!r}")
    rs = (recorded.get("host") or {}).get("font_smoothing")
    hs = (here.get("host") or {}).get("font_smoothing")
    if rs != hs:
        out.append(f"font smoothing: recorded {rs!r}, here {hs!r}")
    return out


_PAGE_FACTS = """() => ({device_pixel_ratio: window.devicePixelRatio,
  screen: [screen.width, screen.height], user_agent: navigator.userAgent})"""


def capture_noise_only(out: Path, tag: str, *, log=print, pw=None,
                       manifest_path: Path = MANIFEST) -> dict:
    """The six baselines and the canary, drawn here, for the corpus of another machine.

    No mutations and no rendering configurations: only `base` of every
    template, through the same `Session.frame` and the library's capture as
    the corpus, and the renderer's fingerprint (`Session.canary`), each drawn
    in two fresh browsers that must agree to the pixel. Nothing is written
    until every frame is in hand, and nothing is ever written into the
    corpus: this is the half that runs on the other machine, `--import-noise`
    is the half that runs where the corpus lives.
    """
    if not _TAG.match(tag or ""):
        raise CorpusError(f"--tag {tag!r}: letters, digits, '.', '_' and '-', "
                          "64 at most, e.g. win11")
    out = Path(out).resolve()
    corpus_dir = CORPUS_DIR.resolve()
    if out == corpus_dir or corpus_dir in out.parents:
        raise CorpusError(f"{out} is inside the corpus; frames from another "
                          "machine go elsewhere and come in by --import-noise")
    if out.exists():
        stale = sorted(p.name for p in out.iterdir()
                       if p.suffix == ".png" or p.name == OS_ENV_FILE)
        if stale:
            raise CorpusError(f"{out} already holds {', '.join(stale)}: a capture "
                              "is never mixed with an older one — another --out "
                              "or --tag, or remove it")
    corpus = None
    corpus_raw = b""
    if manifest_path.is_file():
        corpus_raw = manifest_path.read_bytes()
        corpus = json.loads(corpus_raw.decode("utf-8"))

    if pw is None:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as own:
            return capture_noise_only(out, tag, log=log, pw=own,
                                      manifest_path=manifest_path)

    frames: dict[str, bytes] = {}
    templates: dict[str, dict] = {}
    env = environment(pw)
    a, b = Session(pw), Session(pw)
    try:
        page = a.read(TEMPLATES[0].key, _PAGE_FACTS)
        if page.get("device_pixel_ratio") != DEVICE_SCALE_FACTOR:
            raise CorpusError(f"the page reports devicePixelRatio "
                              f"{page.get('device_pixel_ratio')}, the corpus "
                              f"is drawn at {DEVICE_SCALE_FACTOR}")
        for t in TEMPLATES:
            png, fonts = a.frame(t.key, fonts=True)
            n = differing_pixels(png, b.frame(t.key))
            if n:
                raise CorpusError(f"{t.key}: a second browser started the same "
                                  f"way drew {n} different pixels — stop")
            system = [f for f in fonts if not f["custom"]]
            if system:
                raise CorpusError(f"{t.key}: drawn partly with a font of this "
                                  f"machine, {system}: the shipped fonts did "
                                  "not load, and such a frame measures the "
                                  "fonts, not the rasteriser")
            data = pack(png)
            frames[t.key] = data
            entry = {"file": f"{t.key}.png", "sha256": sha256(data),
                     "split": t.split, "fonts": fonts}
            if corpus and t.key in corpus.get("templates", {}):
                base = (manifest_path.parent
                        / corpus["templates"][t.key]["base"]).read_bytes()
                entry["vs_corpus_base"] = _changed_box(base, data)
            templates[t.key] = entry
            vs = entry.get("vs_corpus_base")
            log(f"{t.key}: drawn twice, 0 px apart"
                + (f"; {vs['pixels']} px differ from the corpus baseline"
                   if vs else ""))
        #  The renderer's fingerprint, drawn the same way as in the corpus.
        canary_png = a.canary()
        n = differing_pixels(canary_png, b.canary())
        if n:
            raise CorpusError(f"canary: a second browser started the same way drew "
                              f"{n} different pixels — stop")
        canary_data = pack(canary_png)
        canary_entry = {"file": OS_CANARY_FILE, "sha256": sha256(canary_data),
                        **canary_record()}
        base_fp = ((corpus or {}).get("renderers") or {}).get(
            "fingerprints", {}).get(BASE_RENDERER)
        if base_fp:
            canary_entry["vs_corpus_base"] = differing_pixels(
                (manifest_path.parent / base_fp["file"]).read_bytes(), canary_data)
        log("canary: drawn twice, 0 px apart"
            + (f"; {canary_entry['vs_corpus_base']} px differ from the corpus "
               "baseline's" if base_fp else ""))
    finally:
        a.close()
        b.close()

    record = {
        "format": OS_NOISE_FORMAT,
        "kind": OS_NOISE_KIND,
        "tag": tag,
        "generator": f"python scripts/browser_corpus.py --capture-noise-only --tag {tag}",
        "environment": env,
        "page": page,
        "control": "every frame drawn in two browsers started the same way; "
                   "0 pixels apart, or the capture stops",
        "templates": templates,
        "canary": canary_entry,
        "corpus": None if corpus is None else {
            "manifest_sha256": sha256(corpus_raw),
            "environment": {k: corpus["environment"].get(k) for k in DIFF_KEYS},
            "differences": env_differences(corpus["environment"], env),
        },
    }
    out.mkdir(parents=True, exist_ok=True)
    for key, data in frames.items():
        (out / f"{key}.png").write_bytes(data)
    (out / OS_CANARY_FILE).write_bytes(canary_data)
    (out / OS_ENV_FILE).write_text(json.dumps(record, indent=1, ensure_ascii=False)
                                   + "\n", encoding="utf-8", newline="\n")
    return record


def check_os_capture(record: dict, src: Path, manifest: dict) -> list[str]:
    """Why a capture from another machine cannot go into the corpus. Empty: it can."""
    problems: list[str] = []
    if record.get("kind") != OS_NOISE_KIND or record.get("format") != OS_NOISE_FORMAT:
        return [f"{src / OS_ENV_FILE}: not a --capture-noise-only record "
                f"(kind {record.get('kind')!r}, format {record.get('format')!r})"]
    if not _TAG.match(str(record.get("tag", ""))):
        problems.append(f"tag {record.get('tag')!r} is not a tag")
    env = record.get("environment") or {}
    if not env.get("os"):
        problems.append("the environment names no OS")
    corpus_env = manifest["environment"]
    for k in CAPTURE_KEYS:
        if env.get(k) != corpus_env.get(k):
            problems.append(f"{k}: the corpus has {corpus_env.get(k)!r}, the "
                            f"capture {env.get(k)!r} — not the same way of taking "
                            "the frame")
    dpr = (record.get("page") or {}).get("device_pixel_ratio")
    if dpr != DEVICE_SCALE_FACTOR:
        problems.append(f"devicePixelRatio {dpr!r}, the corpus is drawn at "
                        f"{DEVICE_SCALE_FACTOR}")
    got = set(record.get("templates") or {})
    want = set(manifest["templates"])
    if got != want:
        problems.append(f"templates: the corpus has {sorted(want)}, the capture "
                        f"{sorted(got)}")
    for key in sorted(got & want):
        entry = record["templates"][key]
        path = src / entry.get("file", "")
        if not path.is_file():
            problems.append(f"{key}: {path.name} is missing")
            continue
        data = path.read_bytes()
        if sha256(data) != entry.get("sha256"):
            problems.append(f"{key}: sha256 of {path.name} is not the recorded one")
            continue
        h, w = pixels(data).shape[:2]
        if (w, h) != (VIEWPORT["width"], VIEWPORT["height"]):
            problems.append(f"{key}: {w}x{h}, the corpus is "
                            f"{VIEWPORT['width']}x{VIEWPORT['height']}")
        system = [f for f in entry.get("fonts") or [] if not f.get("custom")]
        if not entry.get("fonts") or system:
            problems.append(f"{key}: not drawn with the shipped fonts only: "
                            f"{entry.get('fonts')}")
        if entry.get("split") != manifest["templates"][key]["split"]:
            problems.append(f"{key}: split {entry.get('split')!r}, the corpus says "
                            f"{manifest['templates'][key]['split']!r}")
    canary = record.get("canary")
    if canary is not None:
        path = src / canary.get("file", "")
        want = canary_record()
        if not path.is_file():
            problems.append(f"canary: {path.name} is missing")
        elif sha256(path.read_bytes()) != canary.get("sha256"):
            problems.append(f"canary: sha256 of {path.name} is not the recorded one")
        if (canary.get("version"), canary.get("page_sha256")) != (
                want["version"], want["page_sha256"]):
            problems.append(f"canary: drawn from another page (version "
                            f"{canary.get('version')!r}, here {want['version']}) "
                            "— capture again with this version of the script")
    return problems


def _with_os_noise(manifest: dict, sections: dict) -> dict:
    """The manifest with its `os_noise` section, placed after `noise_configs`."""
    out: dict = {}
    for k, v in manifest.items():
        if k == "os_noise":
            continue
        out[k] = v
        if k == "noise_configs" and sections:
            out["os_noise"] = sections
    if sections and "os_noise" not in out:
        out["os_noise"] = sections
    return out


def import_os_noise(src: Path, *, replace: bool = False, root: Path = CORPUS_DIR,
                    log=print) -> dict:
    """Frames from `--capture-noise-only` into the corpus, as the NOISE family os_<os>.

    One pair per template: the corpus baseline against the frame the other
    machine drew. A template drawn to the same pixels gets no pair and is
    recorded as such, like a rendering configuration that changed nothing.
    The split is the template's. Returns the new manifest.
    """
    src = Path(src)
    manifest_path = root / "manifest.json"
    manifest = load_manifest(manifest_path)
    env_file = src / OS_ENV_FILE
    if not env_file.is_file():
        raise CorpusError(f"{env_file} does not exist: --import-noise takes the "
                          "directory --capture-noise-only wrote")
    record = json.loads(env_file.read_text("utf-8"))
    problems = check_os_capture(record, src, manifest)
    if problems:
        raise CorpusError("the capture cannot go into the corpus: " + "; ".join(problems))
    env = record["environment"]
    family = os_family(env["os"])
    sections = dict(manifest.get("os_noise") or {})
    if family in sections and not replace:
        #  The same machine drawing the same pixels again — a second capture
        #  to bring its canary — changes no frame and needs no --replace.
        stored = {c["template"]: c["actual"] for c in manifest["cases"]
                  if c["family"] == family}
        redrawn = [t.key for t in TEMPLATES if differing_pixels(
            (root / stored.get(t.key, manifest["templates"][t.key]["base"])).read_bytes(),
            (src / record["templates"][t.key]["file"]).read_bytes())]
        if redrawn:
            raise CorpusError(f"{family} is already in the corpus (tag "
                              f"{sections[family].get('tag')!r}) and this capture "
                              f"draws {', '.join(redrawn)} otherwise; --replace to "
                              "replace it")
        log(f"{family}: the same pixels as the frames in the corpus — only the "
            "record and the canary are taken")
    corpus_env = manifest["environment"]
    what = (f"{env['os']} {env.get('os_version', '')} ({env.get('distro', '')}, "
            f"{env.get('arch', '')}), {env.get('browser_build')}, instead of "
            f"{corpus_env['os']} ({corpus_env.get('distro', '')}, "
            f"{corpus_env.get('arch', '')}), {corpus_env.get('browser_build')}")

    kept = [c for c in manifest["cases"] if c["family"] != family]
    old_files = {c["actual"]: (root / c["actual"]).read_bytes()
                 for c in manifest["cases"] if c["family"] == family}
    pixel_counts: dict[str, int] = {}
    new_cases: list[dict] = []
    writes: dict[str, bytes] = {}
    for t in TEMPLATES:
        tpl = manifest["templates"][t.key]
        stored = pack((src / record["templates"][t.key]["file"]).read_bytes())
        changed = _changed_box((root / tpl["base"]).read_bytes(), stored)
        pixel_counts[t.key] = changed["pixels"]
        if not changed["pixels"]:
            log(f"{t.key}: {env['os']} draws the corpus baseline's pixels — no pair")
            continue
        rel = os_frame_file(t.key, family)
        writes[rel] = stored
        new_cases.append({
            "name": case_name(t.key, "os", family[len("os_"):]),
            "template": t.key, "split": tpl["split"], "family": family,
            "magnitude": family[len("os_"):], "label": NOISE, "kind": "os",
            "why": WHY_OS.format(what=what),
            "expected": tpl["base"], "actual": rel, "sha256": sha256(stored),
            "changed": changed, "detail": {"tag": record["tag"]},
        })
        log(f"{t.key}: {changed['pixels']} px differ from the corpus baseline "
            f"({t.split})")
    #  The machine's canary: its renderer's fingerprint, named by its frames.
    renderers = manifest.get("renderers")
    if renderers is not None:
        fingerprints = {k: v for k, v in renderers["fingerprints"].items()
                        if k != family}
        old_fp = renderers["fingerprints"].get(family)
        if old_fp:
            old_files[old_fp["file"]] = (root / old_fp["file"]).read_bytes()
        canary = record.get("canary")
        if canary:
            data = pack((src / canary["file"]).read_bytes())
            base_fp = fingerprints.get(BASE_RENDERER)
            writes[renderer_file(family)] = data
            fingerprints[family] = {
                "file": renderer_file(family),
                "drawn_with": {"machine": what, "tag": record["tag"]},
                "pixels_vs_base": differing_pixels(
                    (root / base_fp["file"]).read_bytes(), data) if base_fp else None,
                "sha256": sha256(data)}
            log(f"canary: {fingerprints[family]['pixels_vs_base']} px differ from "
                "the corpus baseline's")
        else:
            log("no canary in this capture: the renderer of its frames stays unknown")
        manifest = dict(manifest, renderers=dict(renderers, fingerprints=fingerprints))
        for c in new_cases:
            c["renderer"] = {"expected": BASE_RENDERER,
                             "actual": family if family in fingerprints else None}
    sections[family] = {
        "tag": record["tag"],
        "what": what,
        "environment": env,
        "page": record.get("page"),
        "differs_from_corpus": env_differences(corpus_env, env),
        "fonts": {k: v["fonts"] for k, v in record["templates"].items()},
        "pixels_vs_baseline": pixel_counts,
        "in_corpus": any(pixel_counts.values()),
        "control_pixels": 0,
        "delivered_sha256": {k: v["sha256"] for k, v in record["templates"].items()},
        "redrawn": "only where this environment is found again (same Playwright, "
                   "Chromium build, OS and its version, architecture, font "
                   "smoothing); elsewhere the drift test is skipped with the "
                   "difference named",
    }
    new_manifest = _with_os_noise(dict(manifest, cases=kept + new_cases), sections)

    manifest_raw = manifest_path.read_bytes()
    for rel in old_files:
        (root / rel).unlink()
    for rel, data in writes.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(data)
    write_manifest(new_manifest, manifest_path)
    problems = verify_files(new_manifest, root)
    if problems:
        for rel in writes:
            (root / rel).unlink(missing_ok=True)
        for rel, data in old_files.items():
            (root / rel).write_bytes(data)
        manifest_path.write_bytes(manifest_raw)
        raise CorpusError("the corpus with these frames does not check out, "
                          "nothing changed: " + "; ".join(problems))
    return new_manifest


def carry_os_noise(old: dict | None, old_root: Path, new: dict, new_root: Path,
                   *, log=print) -> tuple[dict, list[str]]:
    """Keep imported frames across `--regenerate` — only while they still pair.

    A frame from another machine is paired with the corpus baseline. If a
    regenerated baseline comes out other pixels, the pair would no longer be
    the noise that was measured; the family is dropped and named. Returns the
    new manifest and the dropped families.
    """
    sections = (old or {}).get("os_noise") or {}
    if not sections:
        return new, []
    same = (set(old["templates"]) == set(new["templates"]) and all(
        old["templates"][k]["sha256"] == new["templates"][k]["sha256"]
        for k in new["templates"]))
    if not same:
        dropped = sorted(sections)
        log(f"the baselines changed: {', '.join(dropped)} dropped — capture them "
            "again on their machine (--capture-noise-only) and --import-noise")
        return new, dropped
    carried = [dict(c) for c in old["cases"] if c["kind"] == "os"]
    for c in carried:
        dst = new_root / c["actual"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(old_root / c["actual"], dst)
    #  A machine's canary goes with its frames — while it is the same canary.
    old_renderers = old.get("renderers") or {}
    renderers = new.get("renderers")
    if renderers is not None:
        fingerprints = dict(renderers["fingerprints"])
        same_page = old_renderers.get("canary") == renderers["canary"]
        for family, rec in (old_renderers.get("fingerprints") or {}).items():
            if family not in sections:
                continue
            if not same_page:
                log(f"{family}: the canary page changed, its fingerprint is dropped "
                    "— capture it again on its machine (--capture-noise-only)")
                continue
            dst = new_root / rec["file"]
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(old_root / rec["file"], dst)
            fingerprints[family] = rec
        renderers = dict(renderers, fingerprints=fingerprints)
        for c in carried:
            c["renderer"] = {"expected": BASE_RENDERER,
                             "actual": c["family"] if c["family"] in fingerprints
                             else None}
        new = dict(new, renderers=renderers)
    return _with_os_noise(dict(new, cases=new["cases"] + carried), sections), []


def drift_os(manifest: dict, *, pw=None, root: Path = CORPUS_DIR
             ) -> tuple[dict[str, list[str]], list[str]]:
    """Redraw the baselines where a machine's frames came from, and compare.

    Returns `(skipped, drifted)`: per family, what differs from here when
    this is not that machine (nothing drawn then), and the frames that no
    longer come out as frozen when it is.
    """
    sections = manifest.get("os_noise") or {}
    if not sections:
        return {}, []
    if pw is None:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as own:
            return drift_os(manifest, pw=own, root=root)
    here = environment(pw)
    by_name = {c["name"]: c for c in manifest["cases"]}
    skipped: dict[str, list[str]] = {}
    drifted: list[str] = []
    for family, sec in sections.items():
        reasons = os_env_mismatch(sec["environment"], here)
        if reasons:
            skipped[family] = reasons
            continue
        s = Session(pw)
        try:
            for key, t in manifest["templates"].items():
                case = by_name.get(case_name(key, "os", family[len("os_"):]))
                want = (root / (case["actual"] if case else t["base"])).read_bytes()
                n = differing_pixels(want, s.frame(key))
                if n:
                    drifted.append(f"{key}/os/{family} ({n} px)")
            fp = ((manifest.get("renderers") or {}).get("fingerprints") or {}).get(family)
            if fp:
                n = differing_pixels((root / fp["file"]).read_bytes(), s.canary())
                if n:
                    drifted.append(f"canary {family} ({n} px)")
        finally:
            s.close()
    return skipped, drifted


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    what = ap.add_mutually_exclusive_group()
    what.add_argument("--regenerate", action="store_true",
                      help="redraw the frozen corpus in tests/browser_corpus. A "
                           "deliberate act: the benchmark figures have to be "
                           "re-run and re-published after it")
    what.add_argument("--check", action="store_true",
                      help="redraw frames and compare them with the frozen ones; "
                           "writes nothing")
    what.add_argument("--capture-noise-only", action="store_true",
                      help="on another machine: draw only the baseline of each "
                           "template and the renderer's canary, no mutations, "
                           "the same capture, into "
                           "--out (default bench_out/os_noise/<tag>) with "
                           "environment.json; the corpus is not touched")
    what.add_argument("--import-noise", metavar="DIR",
                      help="bring what --capture-noise-only wrote into the corpus "
                           "as the NOISE family os_<os>; the benchmark figures "
                           "have to be re-run after it")
    ap.add_argument("--tag", help="with --capture-noise-only: a name for the "
                                  "machine, e.g. win11")
    ap.add_argument("--replace", action="store_true",
                    help="with --import-noise: replace the frames of the same OS "
                         "already in the corpus")
    ap.add_argument("--full", action="store_true",
                    help="with --check: every pair, not a sample")
    ap.add_argument("--out", default=None,
                    help="without --regenerate or --check: where to capture a "
                         "copy to look at (default bench_out/browser_corpus; "
                         "the frozen corpus is not touched)")
    args = ap.parse_args(argv)

    if args.capture_noise_only:
        if not args.tag:
            ap.error("--capture-noise-only needs --tag, e.g. --tag win11")
        out = Path(args.out) if args.out else OS_NOISE_OUT / args.tag
        record = capture_noise_only(out, args.tag)
        env = record["environment"]
        print(f"\n{len(record['templates'])} baselines into {out.resolve()}: "
              f"Playwright {env['playwright']}, Chromium {env['chromium']} "
              f"({env['browser_build']}), {env['os']} {env['os_version']} "
              f"{env['arch']}, DPI {env['host'].get('dpi')}")
        corpus = record["corpus"]
        if corpus:
            print("Differs from the environment the corpus was drawn in:")
            for d in corpus["differences"]:
                print(f"  {d['key']}: corpus {d['corpus']!r}, here {d['here']!r}")
        print("canary (the renderer's fingerprint): "
              + (f"{record['canary']['vs_corpus_base']} px differ from the corpus "
                 "baseline's" if "vs_corpus_base" in record["canary"] else "drawn"))
        print(f"Send the six PNG, {OS_CANARY_FILE} and {OS_ENV_FILE} from "
              f"{out.resolve()}")
        return 0

    if args.import_noise:
        manifest = import_os_noise(Path(args.import_noise), replace=args.replace)
        fams = manifest.get("os_noise") or {}
        for family, sec in fams.items():
            print(f"{family} (tag {sec['tag']}): " + ", ".join(
                f"{k} {n} px" for k, n in sec["pixels_vs_baseline"].items()))
        print("Next: python tests/benchmark.py --corpus browser, and "
              "node scripts/bench_playwright_grid.mjs > "
              "docs/benchmark_browser_native.json")
        return 0

    if args.regenerate:
        manifest = regenerate()
        env = manifest["environment"]
        size = sum(p.stat().st_size for p in FRAMES_DIR.rglob("*.png"))
        print(f"Redrew {len(manifest['cases'])} pairs over "
              f"{len(manifest['templates'])} baselines ({size / 2**20:.1f} MB) with "
              f"Playwright {env['playwright']}, {env['browser_build']}, "
              f"{env['os']} {env['arch']}: {CORPUS_DIR}")
        print("Next: git diff --stat tests/browser_corpus, then "
              "python tests/benchmark.py --corpus browser")
        return 0

    if args.check:
        manifest = load_manifest()
        problems = verify_files(manifest)
        for p in problems:
            print(p)
        skipped, os_drifted = drift_os(manifest)
        for family, why in skipped.items():
            print(f"{family} not redrawn — drawn on another machine: " + "; ".join(why))
        for d in os_drifted:
            print(f"drifted: {d}")
        reasons, drifted = drift(manifest, full=args.full)
        drifted += os_drifted
        if reasons:
            print("Not redrawn — the corpus was drawn elsewhere: " + "; ".join(reasons))
            return 1 if problems or os_drifted else 0
        for d in drifted:
            print(f"drifted: {d}")
        print(f"{'OK' if not (problems or drifted) else 'DIFFERS'}: "
              f"{len(problems)} file problem(s), {len(drifted)} drifted frame(s)")
        return 1 if problems or drifted else 0

    out = Path(args.out or "bench_out/browser_corpus")
    doc = capture_all(out)
    (out / "capture.json").write_text(json.dumps(doc, indent=1, ensure_ascii=False)
                                      + "\n", encoding="utf-8")
    env = doc["environment"]
    print(f"{len(doc['cases'])} frames into {out}: Playwright {env['playwright']}, "
          f"Chromium {env['chromium']} ({env['browser_build']}), "
          f"{env['os']} {env['arch']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
