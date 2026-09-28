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
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform
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
        "arch": platform.machine(),
        "distro": _distro(),
        "python": platform.python_version(),
        "viewport": dict(VIEWPORT),
        "device_scale_factor": DEVICE_SCALE_FACTOR,
        "container": _container(),
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
    return doc


# --------------------------------------------------------------------------- #
#  Freezing
# --------------------------------------------------------------------------- #
def _split_of(template: str) -> str:
    return next(t.split for t in TEMPLATES if t.key == template)


def _case_why(case: dict) -> str:
    if case["kind"] == "render":
        cfg = next(c for c in NOISE_CONFIGS if c.key == case["magnitude"])
        return f"{WHY_NOISE} ({cfg.what})"
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


def build_manifest(doc: dict, root: Path) -> dict:
    """`capture_all`'s record plus the sha256 of every file under `root`."""
    templates = {}
    for t in TEMPLATES:
        rec = doc["templates"][t.key]
        templates[t.key] = {
            "title": rec["title"], "split": t.split, "base": rec["base"],
            "sha256": sha256((root / rec["base"]).read_bytes()), "fonts": rec["fonts"]}
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
        "noise_configs": configs,
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
    return out


def verify_files(manifest: dict, root: Path = CORPUS_DIR) -> list[str]:
    """What is wrong with the files on disk, against the manifest. Empty: nothing."""
    problems = []
    want = frozen_files(manifest)
    on_disk = {p.relative_to(root).as_posix()
               for p in (root / "frames").rglob("*.png")}
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

    staging = Path(tempfile.mkdtemp(prefix="vistest-browser-corpus-"))
    try:
        doc = capture_all(staging, log=log)
        manifest = build_manifest(doc, staging)
        problems = verify_files(manifest, staging)
        if problems:
            raise CorpusError("the fresh corpus does not check out: " + "; ".join(problems))
        if FRAMES_DIR.exists():
            shutil.rmtree(FRAMES_DIR)
        shutil.copytree(staging / "frames", FRAMES_DIR)
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
    for cfg in NOISE_CONFIGS:
        s = Session(pw, cfg.launch)
        try:
            for key, t in manifest["templates"].items():
                case = by_name.get(case_name(key, "render", cfg.key))
                want = frozen(case["actual"] if case else t["base"])
                n = differing_pixels(want, s.frame(key))
                if n:
                    drifted.append(f"{key}/render/{cfg.key} ({n} px)")
        finally:
            s.close()
    return [], drifted


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
    ap.add_argument("--full", action="store_true",
                    help="with --check: every pair, not a sample")
    ap.add_argument("--out", default="bench_out/browser_corpus",
                    help="without --regenerate or --check: where to capture a "
                         "copy to look at (the frozen corpus is not touched)")
    args = ap.parse_args(argv)

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
        reasons, drifted = drift(manifest, full=args.full)
        if reasons:
            print("Not redrawn — the corpus was drawn elsewhere: " + "; ".join(reasons))
            return 1 if problems else 0
        for d in drifted:
            print(f"drifted: {d}")
        print(f"{'OK' if not (problems or drifted) else 'DIFFERS'}: "
              f"{len(problems)} file problem(s), {len(drifted)} drifted frame(s)")
        return 1 if problems or drifted else 0

    out = Path(args.out)
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
