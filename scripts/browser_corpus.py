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


TEMPLATES: tuple[Template, ...] = (
    Template("table", "orders table in an admin panel"),
    Template("form", "account settings form"),
    Template("cards", "product cards in a catalogue grid"),
    Template("landing", "marketing landing page with a hero and a chart"),
    Template("article", "long serif text with a sidebar"),
    Template("dark", "dark-theme monitoring dashboard"),
)

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
    css: str = ""                       # injected after load, before capture


BASELINE = Launch()


def template_url(key: str) -> str:
    return (TEMPLATE_DIR / f"{key}.html").as_uri()


class Session:
    """One Chromium process, one context, one page — reused between frames."""

    def __init__(self, pw, launch: Launch = BASELINE):
        kw = {"args": list(launch.args)}
        if launch.channel:
            kw["channel"] = launch.channel
        self.launch = launch
        self.browser = pw.chromium.launch(**kw)
        self.context = self.browser.new_context(
            viewport=dict(VIEWPORT), device_scale_factor=DEVICE_SCALE_FACTOR,
            locale="en-US", color_scheme="light")
        self.page = self.context.new_page()

    def open(self, template: str) -> None:
        self.page.goto(template_url(template), wait_until="load")
        if self.launch.css:
            self.page.add_style_tag(content=self.launch.css)

    def shoot(self) -> bytes:
        """The frame, through the library's capture — or an error."""
        from vistest.library.targets import capture

        cap = capture(self.page, stable_timeout_ms=STABLE_TIMEOUT_MS)
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
#  Capture
# --------------------------------------------------------------------------- #
def capture_bases(out: Path) -> dict:
    """One frame of every template, the environment, the fonts. Checked twice."""
    from playwright.sync_api import sync_playwright

    out.mkdir(parents=True, exist_ok=True)
    doc: dict = {"templates": {}}
    with sync_playwright() as pw:
        doc["environment"] = environment(pw)
        first = Session(pw)
        second = Session(pw)
        try:
            for t in TEMPLATES:
                first.open(t.key)
                png = first.shoot()
                fonts = fonts_used(first.page)
                second.open(t.key)
                again = second.shoot()
                n = differing_pixels(png, again)
                if n:
                    raise CorpusError(
                        f"{t.key}: two fresh browsers in the same configuration "
                        f"drew {n} different pixels. The environment is not "
                        "deterministic; nothing measured here would mean anything.")
                (out / f"{t.key}.png").write_bytes(pack(png))
                doc["templates"][t.key] = {"title": t.title, "fonts": fonts}
        finally:
            first.close()
            second.close()
    (out / "capture.json").write_text(json.dumps(doc, indent=1, ensure_ascii=False)
                                      + "\n", encoding="utf-8")
    return doc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default="bench_out/browser_corpus",
                    help="where to write the frames (not the frozen corpus)")
    args = ap.parse_args(argv)
    doc = capture_bases(Path(args.out))
    env = doc["environment"]
    print(f"Captured {len(doc['templates'])} templates into {args.out}: "
          f"Playwright {env['playwright']}, Chromium {env['chromium']} "
          f"({env['browser_build']}), {env['os']} {env['arch']}")
    for key, t in doc["templates"].items():
        fonts = ", ".join(f"{f['family']}{'' if f['custom'] else ' (system)'}"
                          for f in t["fonts"])
        print(f"  {key:8s} {fonts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
