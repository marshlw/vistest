# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""The live corpus: part of a page a fraction of a pixel off (plan, v13).

On a live page a block can land on a fractional position — a transform, an
animation stopped half-way, a sticky header. Its text snaps to the pixel
grid; its pictures do not, and are resampled. Nothing changed, yet every
rounded thumbnail differs a little at every edge. The rule of engine v2 for a
whole page shifted by a fraction of a pixel does not cover it: only a part
moved. The browser corpus has no raster pictures to show it, and its
templates cannot change without changing its frozen frames — so this is a
corpus of its own, drawn the same way:

* the same capture (`vistest.library.targets.capture`, through
  `browser_corpus.Session`), window and fonts;
* every frame drawn twice, in two browsers started the same way, and the
  two must agree to the pixel;
* labels known before the frame exists, decided by the maintainer on
  2026-10-14 (`DECIDED_*`);
* split by template: `history` is for calibration, `gallery` is held out —
  it never takes part in choosing a number of the engine. `gallery`,
  `feed` (drawn after attempt 1 of v13) and `inbox` (text only, drawn for
  v13b) are spent (`SPENT`): a rule measured on each failed, and an attempt
  that has seen their figures needs a template drawn after it.

It has its own environment, recorded in its manifest: it was drawn in
another container than the browser corpus (whose baselines come out ~55
thousand pixels different here — other system fonts for the UI of
Chromium's own widgets). Its frames are redrawn only where that
environment is found again.

Frames are stored as lossless WebP (decided by the maintainer on
2026-10-14: the photos make PNG frames ~10 MB): the pixels are those of the
PNG the capture returned, checked on write and on read.

    python scripts/live_photos.py               # the photos, once
    python scripts/live_corpus.py --regenerate  # draw it all
    python scripts/live_corpus.py --check       # redraw, compare, change nothing
    python tests/benchmark.py --corpus live     # the engines on it
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import browser_corpus as bc  # noqa: E402

LIVE_DIR = REPO / "tests" / "live_corpus"
TEMPLATE_DIR = LIVE_DIR / "templates"
MANIFEST = LIVE_DIR / "manifest.json"
FRAMES_DIR = LIVE_DIR / "frames"
#: The renderer's fingerprint (`vistest.library.canary`), drawn once: every
#: frame of this corpus is drawn by the same renderer, so every pair names it
#: on both sides — what `expect_screenshot` hands the engine for a run on the
#: machine its baseline came from.
CANARY = "renderers/base.png"

#: What the frozen frames may weigh together: 10 MiB with the format
#: (decided by the maintainer on 2026-10-14), 12 MiB since the text template
#: of v13b (decided by the maintainer on 2026-10-15).
BUDGET_BYTES = 12 * 1024 * 1024

CALIBRATION, HELD_OUT = bc.CALIBRATION, bc.HELD_OUT


@dataclass(frozen=True)
class Template:
    key: str
    title: str
    split: str
    photo: str = ""     # the picture the image families change
    swap: str = ""      # the picture it is swapped for
    radius: int = 0     # its corner radius, px
    #  A text template (v13b) has no photo: its families change one line.
    kind: str = "photo"
    line_colour: str = ""   # the line's text colour, #rrggbb
    one_char: str = ""      # the line with one character changed
    word_swap: str = ""     # the line with one word replaced


TEMPLATES: tuple[Template, ...] = (
    Template("history", "a list of trips with rounded photo thumbnails", CALIBRATION,
             photo="h3", swap="h9", radius=12),
    Template("gallery", "a grid of large rounded photos, captions, badges, a dark "
             "header", HELD_OUT, photo="g2", swap="g9", radius=14),
    #  Drawn after attempt 1 of v13 spent `gallery` (`SPENT`), before any
    #  change of attempt 2: its held-out template.
    Template("feed", "a social feed: a large rounded photo in a post, round "
             "avatars, a column of thumbnails", HELD_OUT, photo="f1", swap="f9",
             radius=10),
    #  v13b, drawn before any change of it: the held-out template of the text
    #  of a block at a fraction of a pixel. Text only, no photo.
    Template("inbox", "a mail list: senders, subjects, snippets, middle dots, "
             "initials in round avatars — text only", HELD_OUT, kind="text",
             line_colour="#334155", one_char="Invoice 2047 for October is ready",
             word_swap="Invoice 2041 for November is ready"),
)

SPLIT_RULE = (
    "by template: history is for calibration; gallery is held out — it never "
    "takes part in choosing a threshold or any other constant of the engine, "
    "and is measured once a choice has been made on history")

#: The held-out template is spent once a rule chosen on history has been
#: measured on it: a second attempt that has seen its figures needs another.
SPENT = {
    "gallery": (
        "2026-10-14, v13 attempt 1 (the rule «a picture drawn a fraction of a "
        "pixel over»): measured once after the choice on history — 6 of 6 "
        "false failures and a new miss (image_tint/de4); rolled back. A second "
        "attempt is measured on a template drawn after this date"),
    "feed": (
        "2026-10-15, v13 attempt 2 (the rule «a raster picture drawn a fraction "
        "of a pixel over»), the acceptance narrowed to pictures by the "
        "maintainer before it was measured: image_shift 0 of 3 false failures, "
        "but a new miss (image_crop/plus4px — a large smooth photo whose crop "
        "moved by 4 px reads as noise once blurred); rolled back"),
    "inbox": (
        "2026-10-15, v13b (the page's move proven by its text, a dot of text "
        "drawn at the fraction, text snapped across the line): part_shift 0.1 "
        "passed, 0.25 and 0.4 failed — the box edges proved a move of "
        "(0.375, 0.375) while glyph pieces moved by 0.17 along the line, and a "
        "round avatar did not follow; 2 of 3 false failures against at most 1; "
        "rolled back"),
}

DECIDED_NOISE = ("decided by the maintainer on 2026-10-14: a part of the page "
                 "a fraction of a pixel off, under half a pixel, is NOISE — "
                 "as for the whole page shifted by 0.25 px. Nobody changed it")
DECIDED_HALF = ("decided by the maintainer on 2026-10-14: at half a pixel the "
                "text of the block jumps by a whole pixel against the rest of "
                "the page. DISPUTED: printed, never counted")
DECIDED_DE2 = bc.DECIDED + (": ΔE00 ≈ 2 sits at the edge of what a person sees "
                            "side by side. DISPUTED, as in the browser corpus")

PART = '[data-m~="part"]'
PHOTO = '[data-m~="photo"]'
LINE = '[data-m~="line"]'


@dataclass(frozen=True)
class Magnitude:
    key: str
    value: object
    label: str
    why: str = ""


@dataclass(frozen=True)
class Family:
    key: str
    target: str
    js: str
    what: str
    kind: str                     # "noise" or "mutation"
    magnitudes: tuple[Magnitude, ...]


def _px(v: float) -> str:
    return f"{v:g}px".replace(".", "_")


def _part_shift() -> Family:
    return Family("part_shift", PART,
                  "els[0].style.transform = `translate(${v}px, ${v}px)`",
                  "the block of photos and text is moved by a fraction of a pixel "
                  "(transform)", "noise",
                  tuple(Magnitude(_px(v), v, "NOISE", DECIDED_NOISE) for v in (0.1, 0.25, 0.4))
                  + (Magnitude(_px(0.5), 0.5, "DISPUTED", DECIDED_HALF),))


DECIDED_LETTER = (bc.DECIDED + ": +0.2px per letter is invisible in one glyph and "
                  "adds up to several pixels over a line. SIGNAL, as in the browser corpus")
DECIDED_ONE_PX = ("a layout that moved by one pixel is a layout that changed "
                  "(decided by the maintainer on 2026-09-28)")


def _text_families(t: Template) -> tuple[Family, ...]:
    """v13b: one line of a text template, changed the way the browser corpus
    changes text, with the maintainer's labels of 2026-09-28."""
    h = t.line_colour.lstrip("#")
    rgb = (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    tints = []
    for d in (2, 4, 8):
        colour, _ = bc.shift_colour(rgb, d)
        tints.append(Magnitude(f"de{d}", bc.css_hex(colour), "DISPUTED" if d == 2 else "SIGNAL",
                               DECIDED_DE2 if d == 2 else ""))
    return (
        _part_shift(),
        Family("text_tint", LINE, "els[0].style.color = v",
               "the colour of one line of text (ΔE00 from its own)", "mutation",
               tuple(tints)),
        Family("letter_spacing", LINE, "els[0].style.letterSpacing = v + 'px'",
               "the letter spacing of one line", "mutation",
               (Magnitude("plus0_2px", 0.2, "SIGNAL", DECIDED_LETTER),
                Magnitude("plus0_5px", 0.5, "SIGNAL"))),
        Family("one_char", LINE, "els[0].textContent = v",
               "one character of the line changes", "mutation",
               (Magnitude("one", t.one_char, "SIGNAL"),)),
        Family("word_swap", LINE, "els[0].textContent = v",
               "one word of the line is replaced", "mutation",
               (Magnitude("one", t.word_swap, "SIGNAL"),)),
        Family("font_weight", LINE, "els[0].style.fontWeight = v",
               "the line goes bold", "mutation", (Magnitude("bold", "700", "SIGNAL"),)),
        Family("line_offset", LINE, "els[0].style.marginLeft = v + 'px'",
               "the line moves along by whole pixels", "mutation",
               (Magnitude("plus1px", 1, "SIGNAL", DECIDED_ONE_PX),
                Magnitude("plus2px", 2, "SIGNAL"))),
    )


def families(t: Template) -> tuple[Family, ...]:
    """The families of one template: its photo, its radius and its swap
    differ; a text template changes one line instead."""
    if t.kind == "text":
        return _text_families(t)
    return (
        _part_shift(),
        Family("image_shift", PART,
               "els[0].querySelectorAll('img').forEach("
               "i => { i.style.transform = `translateX(${v}px)`; })",
               "only the photos of the block are moved by a fraction of a pixel",
               "noise",
               tuple(Magnitude(_px(v), v, "NOISE", DECIDED_NOISE) for v in (0.1, 0.25, 0.4))),
        Family("image_swap", PHOTO, "els[0].src = v",
               "one photo is replaced by another", "mutation",
               (Magnitude("other", f"photos/{t.swap}.webp", "SIGNAL"),)),
        Family("image_tint", PHOTO, "els[0].src = v",
               "one photo is tinted warm (median ΔE00 over the photo)", "mutation",
               tuple(Magnitude(f"de{d}", f"photos/{t.photo}.tint{d}.webp",
                               "DISPUTED" if d == 2 else "SIGNAL",
                               DECIDED_DE2 if d == 2 else "")
                     for d in (2, 4, 8))),
        Family("image_radius", PHOTO, "els[0].style.borderRadius = v + 'px'",
               "the corner radius of one photo", "mutation",
               (Magnitude("plus2px", t.radius + 2, "SIGNAL"),
                Magnitude("minus2px", t.radius - 2, "SIGNAL"))),
        Family("image_offset", PHOTO, "els[0].style.transform = `translateX(${v}px)`",
               "one photo is moved by whole pixels", "mutation",
               (Magnitude("plus1px", 1, "SIGNAL",
                          "a layout that moved by one pixel is a layout that "
                          "changed (decided by the maintainer on 2026-09-28)"),
                Magnitude("plus2px", 2, "SIGNAL"))),
        Family("image_crop", PHOTO,
               "els[0].style.objectPosition = `calc(50% + ${v}px) 50%`",
               "the crop of one photo moves (object-position)", "mutation",
               (Magnitude("plus4px", 4, "SIGNAL"), Magnitude("plus12px", 12, "SIGNAL"))),
    )


def case_name(template: str, family: str, magnitude: str) -> str:
    return f"{template}/{family}/{magnitude}"


def frame_file(template: str, stem: str) -> str:
    return f"frames/{template}/{stem}.webp"


# --------------------------------------------------------------------------- #
#  Frames: lossless WebP, the PNG's pixels
# --------------------------------------------------------------------------- #
def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def to_webp(png: bytes) -> bytes:
    """The PNG's pixels as lossless WebP — checked to decode to the same."""
    import cv2
    import numpy as np

    rgb = bc.pixels(png)
    ok, buf = cv2.imencode(".webp", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR),
                           [cv2.IMWRITE_WEBP_QUALITY, 101])
    if not ok:
        raise bc.CorpusError("WebP encoding failed")
    data = buf.tobytes()
    if not np.array_equal(read_bytes(data), rgb):
        raise bc.CorpusError("lossless WebP did not give the PNG's pixels back")
    return data


def read_bytes(data: bytes):
    """RGB uint8 of a stored frame."""
    import cv2
    import numpy as np

    bgr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if bgr is None:
        raise bc.CorpusError("a frame does not decode")
    return np.ascontiguousarray(bgr[:, :, ::-1])


def read(path: Path):
    return read_bytes(path.read_bytes())


# --------------------------------------------------------------------------- #
#  Capture
# --------------------------------------------------------------------------- #
def capture(out: Path, *, log=print) -> dict:
    """Every frame into `out/frames`, and the manifest's content."""
    from playwright.sync_api import sync_playwright

    doc: dict = {"templates": {}, "cases": []}

    def write(rel: str, png: bytes) -> str:
        data = to_webp(png)
        path = out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return sha256(data)

    with sync_playwright() as pw:
        doc["environment"] = bc.environment(pw)
        first = bc.Session(pw, template_dir=TEMPLATE_DIR)
        second = bc.Session(pw, template_dir=TEMPLATE_DIR)
        try:
            canary = first.canary()
            n = bc.differing_pixels(canary, second.canary())
            if n:
                raise bc.CorpusError(f"the canary: a second browser drew {n} different pixels")
            packed = bc.pack(canary)
            (out / CANARY).parent.mkdir(parents=True, exist_ok=True)
            (out / CANARY).write_bytes(packed)
            doc["renderer"] = {"file": CANARY, "sha256": sha256(packed),
                               "via": "vistest.library.canary.draw"}
            for t in TEMPLATES:
                base, fonts = first.frame(t.key, fonts=True)
                n = bc.differing_pixels(base, second.frame(t.key))
                if n:
                    raise bc.CorpusError(f"{t.key}: a second browser drew {n} "
                                         "different pixels of the unchanged template")
                foreign = [f for f in fonts if not f["custom"]]
                if foreign:
                    raise bc.CorpusError(f"{t.key}: text drawn with a system font: {foreign}")
                rel = frame_file(t.key, "base")
                doc["templates"][t.key] = {"title": t.title, "split": t.split,
                                           "base": rel, "sha256": write(rel, base),
                                           "fonts": fonts}
                log(f"{t.key}: base")
                for fam in families(t):
                    for m in fam.magnitudes:
                        mutation = (fam.target, fam.js, m.value)
                        png = first.frame(t.key, mutation)
                        n = bc.differing_pixels(png, second.frame(t.key, mutation))
                        if n:
                            raise bc.CorpusError(
                                f"{case_name(t.key, fam.key, m.key)}: a second "
                                f"browser drew {n} different pixels")
                        changed = bc._changed_box(base, png)
                        if not changed["pixels"]:
                            raise bc.CorpusError(
                                f"{case_name(t.key, fam.key, m.key)}: no pixel changed")
                        rel = frame_file(t.key, f"{fam.key}--{m.key}")
                        doc["cases"].append({
                            "name": case_name(t.key, fam.key, m.key),
                            "template": t.key, "split": t.split,
                            "family": fam.key, "magnitude": m.key,
                            "value": m.value, "label": m.label,
                            "kind": fam.kind, "why": m.why or fam.what,
                            "expected": doc["templates"][t.key]["base"],
                            "actual": rel, "sha256": write(rel, png),
                            "changed": changed,
                            "renderer": {"expected": "base", "actual": "base"}})
                        log(f"  {fam.key}/{m.key}: {changed['pixels']} px")
        finally:
            first.close()
            second.close()
    return doc


def families_record() -> dict:
    out: dict = {}
    for t in TEMPLATES:
        for f in families(t):
            rec = out.setdefault(f.key, {"target": f.target, "what": f.what,
                                         "kind": f.kind, "js": f.js, "magnitudes": {}})
            for m in f.magnitudes:
                rec["magnitudes"].setdefault(m.key, {"label": m.label})
                rec["magnitudes"][m.key].setdefault("values", {})[t.key] = m.value
                if m.why:
                    rec["magnitudes"][m.key]["why"] = m.why
    return out


def build_manifest(doc: dict) -> dict:
    return {
        "format": 1,
        "about": ("Live corpus of VisTest: part of a page a fraction of a pixel "
                  "off, and real changes of the same photos. Written by "
                  "`python scripts/live_corpus.py --regenerate`; do not edit."),
        "environment": doc["environment"],
        "control": "every frame drawn in two browsers started the same way; 0 "
                   "pixels apart, or the capture stops",
        "storage": "lossless WebP; the pixels of the PNG the capture returned",
        "split": {"rule": SPLIT_RULE,
                  CALIBRATION: [t.key for t in TEMPLATES if t.split == CALIBRATION],
                  HELD_OUT: [t.key for t in TEMPLATES if t.split == HELD_OUT],
                  "spent": dict(SPENT)},
        "labels": {"SIGNAL": "the page changed; a tool must fail",
                   "NOISE": "nobody changed the page; a tool must pass",
                   "DISPUTED": "printed, never counted"},
        "renderer": doc["renderer"],
        "templates": doc["templates"],
        "families": families_record(),
        "cases": doc["cases"],
    }


def write_manifest(manifest: dict, path: Path = MANIFEST) -> None:
    path.write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n",
                    encoding="utf-8", newline="\n")


def load_manifest(path: Path = MANIFEST) -> dict:
    if not path.is_file():
        raise bc.CorpusError(f"{path} does not exist; to draw it: "
                             "`python scripts/live_corpus.py --regenerate`")
    return json.loads(path.read_text("utf-8"))


def frozen_files(manifest: dict) -> dict[str, str]:
    out = {t["base"]: t["sha256"] for t in manifest["templates"].values()}
    out.update({c["actual"]: c["sha256"] for c in manifest["cases"]})
    out[manifest["renderer"]["file"]] = manifest["renderer"]["sha256"]
    return out


def verify_files(manifest: dict, root: Path = LIVE_DIR) -> list[str]:
    """What is wrong with the frames on disk, against the manifest."""
    problems = []
    want = frozen_files(manifest)
    on_disk = {p.relative_to(root).as_posix() for p in (root / "frames").rglob("*.webp")}
    on_disk |= {p.relative_to(root).as_posix() for p in (root / "renderers").glob("*.png")}
    problems += [f"missing: {r}" for r in sorted(set(want) - on_disk)]
    problems += [f"not in the manifest: {r}" for r in sorted(on_disk - set(want))]
    for rel in sorted(set(want) & on_disk):
        if sha256((root / rel).read_bytes()) != want[rel]:
            problems.append(f"sha256 differs: {rel}")
    total = sum((root / rel).stat().st_size for rel in on_disk)
    if total > BUDGET_BYTES:
        problems.append(f"{total} bytes of frames, over the {BUDGET_BYTES} budget")
    return problems


def regenerate(log=print) -> dict:
    import shutil

    for d in (FRAMES_DIR, LIVE_DIR / "renderers"):
        if d.exists():
            shutil.rmtree(d)
    manifest = build_manifest(capture(LIVE_DIR, log=log))
    write_manifest(manifest)
    problems = verify_files(manifest)
    if problems:
        raise bc.CorpusError("; ".join(problems))
    return manifest


def sample(manifest: dict, *, full: bool = False) -> list[dict]:
    """The pairs a drift check redraws: all with `full`; otherwise one
    magnitude of every family on every template, rotating with the
    template's position, so that every magnitude is redrawn somewhere."""
    if full:
        return list(manifest["cases"])
    order = [t.key for t in TEMPLATES]
    groups: dict[tuple[str, str], list[dict]] = {}
    for c in manifest["cases"]:
        groups.setdefault((c["template"], c["family"]), []).append(c)
    return [g[order.index(t) % len(g)] for (t, _), g in groups.items()]


def drift(manifest: dict, *, full: bool = False, pw=None,
          root: Path = LIVE_DIR) -> tuple[list[str], list[str]]:
    """(reasons, drifted): redraw here and compare pixels with the frozen frames.

    Only in the environment the manifest records: anywhere else nothing is
    drawn, and `reasons` names the difference. Otherwise every baseline, the
    canary and `sample(full=full)` are drawn, and `drifted` lists the frames
    that came out otherwise.
    """
    import numpy as np

    def run(pw) -> tuple[list[str], list[str]]:
        reasons = bc.env_mismatch(manifest["environment"], bc.environment(pw))
        if reasons:
            return reasons, []
        drifted: list[str] = []
        session = bc.Session(pw, template_dir=TEMPLATE_DIR)
        try:
            rel = manifest["renderer"]["file"]
            if bc.differing_pixels((root / rel).read_bytes(), session.canary()):
                drifted.append(rel)
            for key, t in manifest["templates"].items():
                if not np.array_equal(read(root / t["base"]), bc.pixels(session.frame(key))):
                    drifted.append(t["base"])
            by_key = {t.key: t for t in TEMPLATES}
            for c in sample(manifest, full=full):
                f = {x.key: x for x in families(by_key[c["template"]])}[c["family"]]
                png = session.frame(c["template"], (f.target, f.js, c["value"]))
                if not np.array_equal(read(root / c["actual"]), bc.pixels(png)):
                    drifted.append(c["actual"])
        finally:
            session.close()
        return [], drifted

    if pw is not None:
        return run(pw)
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        return run(p)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    what = ap.add_mutually_exclusive_group(required=True)
    what.add_argument("--regenerate", action="store_true",
                      help="redraw the live corpus into tests/live_corpus (on purpose)")
    what.add_argument("--check", action="store_true",
                      help="redraw here and compare with the frozen frames; change nothing")
    args = ap.parse_args(argv)
    try:
        if args.regenerate:
            m = regenerate()
            size = sum((LIVE_DIR / r).stat().st_size for r in frozen_files(m))
            print(f"{len(m['cases'])} pairs, {size} bytes: {MANIFEST}")
            return 0
        problems = verify_files(load_manifest())
        if not problems:
            reasons, problems = drift(load_manifest(), full=True)
            if reasons:
                print("not the environment of the live corpus, nothing redrawn: "
                      + "; ".join(reasons), file=sys.stderr)
                return 2
    except bc.CorpusError as e:
        print(f"live corpus: {e}", file=sys.stderr)
        return 2
    for p in problems:
        print(p)
    print("the same" if not problems else f"{len(problems)} differences")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
