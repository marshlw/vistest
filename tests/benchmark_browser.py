# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""The benchmark on the browser corpus: `python tests/benchmark.py --corpus browser`.

    npm ci --prefix scripts/bench
    node scripts/bench_playwright_grid.mjs > docs/benchmark_browser_native.json
    python tests/benchmark.py --corpus browser --no-timing
    python tests/benchmark.py --corpus browser --no-timing --markdown docs/benchmark_browser.md

Who is measured: VisTest under its three presets, strict, balanced and
loose, with the AI layer as `CheckService` builds it (`--no-ai`: without);
and the native Playwright comparator — the one `toHaveScreenshot()` calls —
on a grid, threshold {0.2, 0.1, 0.05} × maxDiffPixels {0, 25, 100, 500},
from the JSON `scripts/bench_playwright_grid.mjs` writes. That JSON carries
the corpus digest and is refused when it was computed on other files. It is
committed, so the table needs no Node to be read again; without it the
Playwright rows are left out and the run says so.

What is counted. A NOISE pair a tool fails on is a false failure; a SIGNAL
pair it passes is a miss. DISPUTED pairs (ΔE00 ≈ 2) are printed — how often
each tool failed on them — and counted as neither. Everything is split into
the calibration half and the held-out half, by template (see
`scripts/browser_corpus.py`): **the held-out columns are there to be read,
never to choose a threshold by.** A number tuned on them stops meaning what
this table says it means.

`--no-timing` leaves out the only thing that changes from run to run; the
rest of the output is the same byte for byte on the same engine and corpus.
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import browser_corpus as bc  # noqa: E402

NATIVE_DEFAULT = ROOT / "docs" / "benchmark_browser_native.json"
PRESETS = ("strict", "balanced", "loose")
#: `--engine`: which VisTest paths get rows. On this corpus both by default —
#: the table exists to put v1, v2 and Playwright side by side.
ENGINES = {"v1": ("v1",), "v2": ("v2",), "both": ("v1", "v2")}
HALVES = (bc.CALIBRATION, bc.HELD_OUT)
HALF_TITLE = {bc.CALIBRATION: "calibration", bc.HELD_OUT: "held out"}


class NativeError(RuntimeError):
    """The native JSON cannot be used for this corpus."""


@dataclass
class Tool:
    key: str                      # short column name
    title: str
    group: str                    # "VisTest" | "Playwright"
    failed: dict[str, bool] = field(default_factory=dict)
    ms: float = 0.0
    default: bool = False         # the setting a user gets without touching it


@dataclass(frozen=True)
class Row:
    """One line of the per-family table: pairs of one family and one label."""

    key: str
    label: str
    names: tuple[str, ...]


# --------------------------------------------------------------------------- #
#  Running
# --------------------------------------------------------------------------- #
def load(manifest_path: Path = bc.MANIFEST) -> tuple[dict, list[dict]]:
    manifest = bc.load_manifest(manifest_path)
    return manifest, manifest["cases"]


def run_vistest(manifest: dict, preset: str, *, ai: bool = True,
                root: Path = bc.CORPUS_DIR, engine: str = "v1") -> Tool:
    from vistest.ai import AIPipeline
    from vistest.config import VisTestConfig
    from vistest.core import pngio
    from vistest.core.comparator import compare
    from vistest.models import Verdict

    cfg = VisTestConfig.preset_of(preset)
    hooks = AIPipeline(cfg.ai) if ai else None
    bases = {k: pngio.read(root / t["base"]) for k, t in manifest["templates"].items()}
    canaries = fingerprints(manifest, root)
    if engine == "v1":
        tool = Tool(key=preset, title=f"VisTest {preset}" + ("" if ai else " (no AI)"),
                    group="VisTest", default=preset == "balanced")
    else:
        #  No preset in the title: the presets do not act on v2 (its numbers
        #  are in core/v2/settings.py; of DiffConfig it reads only what
        #  DIFFCONFIG_READ lists). What strict/balanced/loose will mean for
        #  v2 is to be decided before it becomes the default.
        tool = Tool(key=engine, title=f"VisTest {engine}" + ("" if ai else " (no AI)"),
                    group="VisTest")
    for c in manifest["cases"]:
        actual = pngio.read(root / c["actual"])
        t0 = time.perf_counter()
        r = compare(bases[c["template"]], actual, cfg=cfg.diff, name=c["name"],
                    ai_hooks=hooks, engine=engine, renderer=renderer_pair(c, canaries))
        tool.ms += (time.perf_counter() - t0) * 1000
        tool.failed[c["name"]] = r.verdict is Verdict.FAIL
    return tool


def fingerprints(manifest: dict, root: Path = bc.CORPUS_DIR) -> dict[str, bytes]:
    """Every canary of the corpus, by the key frames name it with."""
    recs = (manifest.get("renderers") or {}).get("fingerprints") or {}
    return {k: (root / r["file"]).read_bytes() for k, r in recs.items()}


def renderer_pair(case: dict, canaries: dict[str, bytes]):
    """`compare(renderer=...)` for a pair: the canaries its two frames name.

    Taken from the frames' own record in the manifest — never from the
    family's name or the label. A frame that names no canary gives `None`
    on its side, and the engine hears «unknown».
    """
    ref = case.get("renderer") or {}
    return (canaries.get(ref.get("expected")), canaries.get(ref.get("actual")))


#: The two groups the pairs are read in, by how the browser that drew the
#: second frame was started and where: the same way as the baseline's
#: (page CSS aside), or not.
SAME_RENDERER, OTHER_RENDERER = "same renderer", "other renderer"
GROUP_RULE = ("a pair is in «other renderer» when the browser that drew its "
              "second frame was started otherwise than the baseline's — other "
              "flags, another build, another machine — and in «same renderer» "
              "when only the page differs (a mutation, or a stylesheet: "
              "text-rendering, a fractional transform)")


def renderer_group(case: dict, manifest: dict) -> str:
    kind = case["kind"]
    if kind == "mutation":
        return SAME_RENDERER
    if kind in ("os", "cross_render"):
        return OTHER_RENDERER
    cfg = manifest["noise_configs"][case["magnitude"]]
    return OTHER_RENDERER if cfg.get("args") or cfg.get("channel") else SAME_RENDERER


def load_native(path: Path, manifest_path: Path = bc.MANIFEST) -> dict:
    doc = json.loads(Path(path).read_text("utf-8"))
    if doc.get("source") != "native":
        raise NativeError(f"{path}: not a native run (source={doc.get('source')!r})")
    want = bc.corpus_digest(manifest_path)
    got = (doc.get("corpus") or {}).get("sha256")
    if got != want:
        raise NativeError(
            f"{path} was computed on a different browser corpus (sha256 "
            f"{got or 'missing'}, the corpus on disk is {want}). Re-run: node "
            f"scripts/bench_playwright_grid.mjs > {path}")
    return doc


def playwright_tools(native: dict, cases: list[dict]) -> list[Tool]:
    grid = native["grid"]
    version = native["environment"]["packages"]["@playwright/test"]
    tools = []
    for thr in grid["threshold"]:
        for mdp in grid["maxDiffPixels"]:
            key = f"{thr}/{mdp}"
            default = thr == 0.2 and mdp == 0
            t = Tool(key=key, group="Playwright", default=default,
                     title=f"Playwright {version} threshold {thr}, "
                           f"maxDiffPixels {mdp}" + (" (default)" if default else ""))
            for c in cases:
                entry = native["results"].get(c["name"])
                if entry is None:
                    raise NativeError(f"no native result for {c['name']}")
                t.failed[c["name"]] = bool(entry["failed"][key])
            tools.append(t)
    return tools


# --------------------------------------------------------------------------- #
#  Counting
# --------------------------------------------------------------------------- #
def rows(manifest: dict) -> list[Row]:
    """SIGNAL families, then NOISE (rendering, then invisible mutations), then DISPUTED."""
    by: dict[tuple[str, str], list[str]] = {}
    order: list[tuple[str, str]] = []
    for c in manifest["cases"]:
        if c["kind"] in ("render", "os") or c["label"] == bc.SIGNAL:
            key = c["family"]
        else:
            #  One step of a family that is not SIGNAL: opacity 0.98, fill de2.
            key = f"{c['family']} {c['magnitude']}"
        k = (key, c["label"])
        if k not in by:
            by[k] = []
            order.append(k)
        by[k].append(c["name"])
    rank = {bc.SIGNAL: 0, bc.NOISE: 1, bc.DISPUTED: 2}
    first = {k: i for i, k in enumerate(order)}
    order.sort(key=lambda k: (rank[k[1]], first[k]))
    return [Row(key=k[0], label=k[1], names=tuple(by[k])) for k in order]


def errors(tool: Tool, names, label: str) -> int:
    """False failures on NOISE, misses on SIGNAL, failures on DISPUTED."""
    if label == bc.SIGNAL:
        return sum(1 for n in names if not tool.failed[n])
    return sum(1 for n in names if tool.failed[n])


def split_names(cases: list[dict], half: str | None, label: str) -> list[str]:
    return [c["name"] for c in cases
            if c["label"] == label and (half is None or c["split"] == half)]


@dataclass(frozen=True)
class Score:
    false: int
    noise: int
    misses: int
    signal: int
    disputed_failed: int
    disputed: int

    @property
    def false_rate(self) -> float:
        return self.false / max(self.noise, 1)

    @property
    def miss_rate(self) -> float:
        return self.misses / max(self.signal, 1)


def score(tool: Tool, cases: list[dict], half: str | None = None) -> Score:
    n = split_names(cases, half, bc.NOISE)
    s = split_names(cases, half, bc.SIGNAL)
    d = split_names(cases, half, bc.DISPUTED)
    return Score(errors(tool, n, bc.NOISE), len(n), errors(tool, s, bc.SIGNAL), len(s),
                 errors(tool, d, bc.DISPUTED), len(d))


def worst(tool: Tool, table: list[Row], cases: list[dict],
          k: int = 3) -> list[tuple[Row, int, int]]:
    """The k rows (SIGNAL and NOISE) with the highest error rate, ties by count."""
    out = []
    for i, r in enumerate(table):
        if r.label == bc.DISPUTED:
            continue
        e = errors(tool, r.names, r.label)
        if e:
            out.append((e / len(r.names), e, -i, r))
    out.sort(reverse=True, key=lambda x: (x[0], x[1], x[2]))
    return [(r, e, len(r.names)) for _, e, _, r in out[:k]]


def frontier(tools: list[Tool], cases: list[dict], half: str | None = None) -> list[Tool]:
    """The tools no other tool of the same group beats on both axes."""
    pts = {t.key: score(t, cases, half) for t in tools}
    out = []
    for t in tools:
        a = pts[t.key]
        dominated = any(
            b.false <= a.false and b.misses <= a.misses
            and (b.false < a.false or b.misses < a.misses)
            for k, b in pts.items() if k != t.key)
        if not dominated:
            out.append(t)
    return out


# --------------------------------------------------------------------------- #
#  Printing
# --------------------------------------------------------------------------- #
def _frac(a: int, b: int) -> str:
    return f"{a}/{b}"


def _pct(a: int, b: int) -> str:
    return f"{100 * a / b:3.0f}%" if b else "  —"


def environment_line(env: dict) -> str:
    return (f"Playwright {env['playwright']} (Python), Chromium {env['chromium']} "
            f"{env['browser_build']}, {env['distro']} {env['arch']}, "
            f"{env['viewport']['width']}x{env['viewport']['height']} at "
            f"{env['device_scale_factor']}x, "
            + (f"image {env['container']['image']}"
               if (env.get("container") or {}).get("image") else "no container image"))


def other_machines(manifest: dict) -> list[str]:
    """One line per machine whose frames are in the corpus (`--import-noise`)."""
    out = []
    for family, sec in (manifest.get("os_noise") or {}).items():
        env = sec["environment"]
        px = ", ".join(f"{k} {n}" for k, n in sec["pixels_vs_baseline"].items())
        pairs = sum(1 for n in sec["pixels_vs_baseline"].values() if n)
        out.append(f"{family}: the baselines drawn on {env['os']} {env.get('os_version', '')} "
                   f"{env['arch']} (tag {sec['tag']}), Playwright {env['playwright']}, "
                   f"Chromium {env['chromium']} {env['browser_build']}; {pairs} NOISE "
                   f"pairs, pixels apart from the corpus baseline: {px}")
    return out


def engine_environment() -> str:
    """What the VisTest rows were computed with: OpenCV first, it is the one
    dependency the engine's figures have ever moved with."""
    import platform

    import cv2
    import numpy

    return (f"OpenCV {cv2.__version__}, numpy {numpy.__version__}, "
            f"Python {platform.python_version()}, {platform.system()} {platform.machine()}")


def report(manifest: dict, tools: list[Tool], *, timing: bool, notes: list[str],
           engine_env: str | None = None) -> str:
    cases = manifest["cases"]
    table = rows(manifest)
    L: list[str] = []
    count = {lab: sum(1 for c in cases if c["label"] == lab)
             for lab in (bc.SIGNAL, bc.NOISE, bc.DISPUTED)}
    L.append("=== Browser corpus benchmark ===")
    #  In the output, not on stderr as for the synthetic corpus: these figures
    #  are read next to each other across runs, and a "before" and an "after"
    #  from two different OpenCV builds are not a comparison.
    L.append(f"VisTest rows computed with: {engine_env or engine_environment()}")
    L.append(f"Corpus: tests/browser_corpus, {len(cases)} pairs over "
             f"{len(manifest['templates'])} templates: {count[bc.SIGNAL]} SIGNAL, "
             f"{count[bc.NOISE]} NOISE, {count[bc.DISPUTED]} DISPUTED (printed, not counted)")
    L.append(f"Drawn with: {environment_line(manifest['environment'])}")
    for line in other_machines(manifest):
        L.append(f"Also: {line}")
    split = manifest["split"]
    L.append(f"Split by template: calibration {', '.join(split[bc.CALIBRATION])}; "
             f"held out {', '.join(split[bc.HELD_OUT])} — never used to choose a threshold")
    for n in notes:
        L.append(n)

    # ---- summary --------------------------------------------------------- #
    L.append("")
    L.append("Summary: false failures on NOISE, misses on SIGNAL, failures on DISPUTED")
    w = max(len(t.title) for t in tools) + 2
    head = (f"{'tool':{w}s} | {'calibration':^19s} | {'held out':^19s} | "
            f"{'all':^27s} | {'disputed':^8s} | {'ms':>7s}")
    L.append(head)
    L.append(f"{'':{w}s} | {'false':>8s} {'misses':>10s} | {'false':>8s} {'misses':>10s} | "
             f"{'false':>12s} {'misses':>14s} | {'failed':>8s} |")
    L.append("-" * len(head))
    for t in tools:
        c, h, a = (score(t, cases, x) for x in (*HALVES, None))
        ms = f"{t.ms:7.0f}" if timing and t.ms else f"{'—':>7s}"
        L.append(f"{t.title:{w}s} | "
                 f"{_frac(c.false, c.noise):>8s} {_frac(c.misses, c.signal):>10s} | "
                 f"{_frac(h.false, h.noise):>8s} {_frac(h.misses, h.signal):>10s} | "
                 f"{_frac(a.false, a.noise):>6s} {_pct(a.false, a.noise)} "
                 f"{_frac(a.misses, a.signal):>8s} {_pct(a.misses, a.signal)} | "
                 f"{_frac(a.disputed_failed, a.disputed):>8s} | {ms}")

    # ---- by renderer group ----------------------------------------------- #
    groups = {g: [c for c in cases if renderer_group(c, manifest) == g]
              for g in (SAME_RENDERER, OTHER_RENDERER)}
    L.append("")
    L.append(f"By renderer: {GROUP_RULE}")
    L.append(head)
    L.append("-" * len(head))
    for g, members in groups.items():
        count_g = {lab: sum(1 for c in members if c["label"] == lab)
                   for lab in (bc.SIGNAL, bc.NOISE, bc.DISPUTED)}
        L.append(f"{g}: {count_g[bc.SIGNAL]} SIGNAL, {count_g[bc.NOISE]} NOISE, "
                 f"{count_g[bc.DISPUTED]} DISPUTED")
        for t in tools:
            c, h, a = (score(t, members, x) for x in (*HALVES, None))
            L.append(f"  {t.title:{w - 2}s} | "
                     f"{_frac(c.false, c.noise):>8s} {_frac(c.misses, c.signal):>10s} | "
                     f"{_frac(h.false, h.noise):>8s} {_frac(h.misses, h.signal):>10s} | "
                     f"{_frac(a.false, a.noise):>6s} {_pct(a.false, a.noise)} "
                     f"{_frac(a.misses, a.signal):>8s} {_pct(a.misses, a.signal)} | "
                     f"{_frac(a.disputed_failed, a.disputed):>8s} |")

    # ---- per family ------------------------------------------------------ #
    for group in ("VisTest", "Playwright"):
        members = [t for t in tools if t.group == group]
        if not members:
            continue
        L.append("")
        L.append(f"By family — {group}: errors calibration/held-out "
                 "(SIGNAL: misses, NOISE: false failures, DISPUTED: failures)")
        cw = max(7, *(len(t.key) for t in members)) + 1
        fw = max(len(r.key) for r in table) + 2
        head = (f"{'family':{fw}s} {'label':8s} {'pairs':>7s} "
                + " ".join(f"{t.key:>{cw}s}" for t in members))
        L.append(head)
        L.append("-" * len(head))
        for r in table:
            cal = [n for n in r.names if n.split("/")[0] in split[bc.CALIBRATION]]
            held = [n for n in r.names if n not in cal]
            cells = [f"{errors(t, cal, r.label)}/{errors(t, held, r.label)}" for t in members]
            L.append(f"{r.key:{fw}s} {r.label:8s} {len(cal):>3d}/{len(held):<3d} "
                     + " ".join(f"{x:>{cw}s}" for x in cells))

    # ---- worst ----------------------------------------------------------- #
    L.append("")
    L.append("Worst three families per tool (error rate over both halves)")
    for t in tools:
        wst = worst(t, table, cases)
        text = "; ".join(f"{r.key} {r.label.lower()} {e}/{n}" for r, e, n in wst) or "none"
        L.append(f"  {t.title}: {text}")

    # ---- frontier -------------------------------------------------------- #
    L.append("")
    L.append("Frontier: settings no other setting of the same tool beats on both "
             "false failures and misses")
    for group in ("VisTest", "Playwright"):
        members = [t for t in tools if t.group == group]
        if not members:
            continue
        for half in (bc.CALIBRATION, bc.HELD_OUT, None):
            front = frontier(members, cases, half)
            pts = sorted(((score(t, cases, half), t) for t in front),
                         key=lambda x: (x[0].false, x[0].misses))
            title = HALF_TITLE.get(half, "all") if half else "all"
            L.append(f"  {group}, {title}: " + "; ".join(
                f"{t.key} ({s.false}/{s.noise} false, {s.misses}/{s.signal} misses)"
                for s, t in pts))
    return "\n".join(L) + "\n"


def markdown(manifest: dict, tools: list[Tool], native: dict | None,
             *, ai: bool) -> str:
    """The same tables for docs/benchmark_browser.md."""
    from datetime import date

    cases = manifest["cases"]
    table = rows(manifest)
    split = manifest["split"]
    L = ["# Benchmark on the browser corpus", ""]
    L.append(f"Generated by `python tests/benchmark.py --corpus browser --no-timing "
             f"--markdown docs/benchmark_browser.md`{'' if ai else ' --no-ai'} on "
             f"{date.today().isoformat()}. VisTest rows computed with "
             f"{engine_environment()}. Corpus drawn with: "
             f"{environment_line(manifest['environment'])}.")
    for line in other_machines(manifest):
        L.append("")
        L.append(f"Also, NOISE from another machine — {line}.")
    L.append("")
    L.append("Frames rendered by Chromium from the templates in "
             "`tests/browser_corpus/templates/`, captured through "
             "`vistest.library.targets.capture`; labels come from the change that "
             "made the frame (`scripts/browser_corpus.py`). DISPUTED pairs "
             "(ΔE00 ≈ 2) are shown and never counted.")
    L.append("")
    L.append(f"**Split by template.** Calibration: {', '.join(split[bc.CALIBRATION])}. "
             f"Held out: {', '.join(split[bc.HELD_OUT])}. {split['rule'][0].upper()}"
             f"{split['rule'][1:]}.")
    L.append("")
    L.append("## Summary")
    L.append("")
    L.append("| Tool | Calibration: false | Calibration: misses | Held out: false | "
             "Held out: misses | All: false | All: misses | DISPUTED failed |")
    L.append("|---|---|---|---|---|---|---|---|")
    for t in tools:
        c, h, a = (score(t, cases, x) for x in (*HALVES, None))
        L.append(f"| {t.title} | {c.false}/{c.noise} | {c.misses}/{c.signal} | "
                 f"{h.false}/{h.noise} | {h.misses}/{h.signal} | "
                 f"{a.false}/{a.noise} ({_pct(a.false, a.noise).strip()}) | "
                 f"{a.misses}/{a.signal} ({_pct(a.misses, a.signal).strip()}) | "
                 f"{a.disputed_failed}/{a.disputed} |")
    L.append("")
    L.append("## By renderer")
    L.append("")
    L.append(f"{GROUP_RULE[0].upper()}{GROUP_RULE[1:]}.")
    for g in (SAME_RENDERER, OTHER_RENDERER):
        members = [c for c in cases if renderer_group(c, manifest) == g]
        L.append("")
        L.append(f"**{g}**: {sum(1 for c in members if c['label'] == bc.SIGNAL)} SIGNAL, "
                 f"{sum(1 for c in members if c['label'] == bc.NOISE)} NOISE, "
                 f"{sum(1 for c in members if c['label'] == bc.DISPUTED)} DISPUTED.")
        L.append("")
        L.append("| Tool | Calibration: false | Calibration: misses | Held out: false | "
                 "Held out: misses | All: false | All: misses |")
        L.append("|---|---|---|---|---|---|---|")
        for t in tools:
            c, h, a = (score(t, members, x) for x in (*HALVES, None))
            L.append(f"| {t.title} | {c.false}/{c.noise} | {c.misses}/{c.signal} | "
                     f"{h.false}/{h.noise} | {h.misses}/{h.signal} | "
                     f"{a.false}/{a.noise} | {a.misses}/{a.signal} |")
    for group in ("VisTest", "Playwright"):
        members = [t for t in tools if t.group == group]
        if not members:
            continue
        L.append("")
        L.append(f"## By family — {group}")
        L.append("")
        L.append("Errors on the calibration / held-out half. SIGNAL: misses; NOISE: "
                 "false failures; DISPUTED: failures (not errors).")
        L.append("")
        L.append("| Family | Label | Pairs | "
                 + " | ".join(f"`{t.key}`" for t in members) + " |")
        L.append("|---|---|---|" + "---|" * len(members))
        for r in table:
            cal = [n for n in r.names if n.split("/")[0] in split[bc.CALIBRATION]]
            held = [n for n in r.names if n not in cal]
            cells = (f"{errors(t, cal, r.label)}/{errors(t, held, r.label)}"
                     for t in members)
            L.append(f"| {r.key} | {r.label} | {len(cal)}/{len(held)} | "
                     + " | ".join(cells) + " |")
    L.append("")
    L.append("## Worst three families per tool")
    L.append("")
    for t in tools:
        wst = worst(t, table, cases)
        L.append(f"- **{t.title}**: " + ("; ".join(
            f"{r.key} ({r.label}) {e}/{n}" for r, e, n in wst) or "none"))
    L.append("")
    L.append("## Frontier")
    L.append("")
    L.append("Settings of one tool that no other setting of the same tool beats on "
             "both axes at once.")
    L.append("")
    for group in ("VisTest", "Playwright"):
        members = [t for t in tools if t.group == group]
        for half in (bc.CALIBRATION, bc.HELD_OUT, None):
            if not members:
                continue
            front = frontier(members, cases, half)
            pts = sorted(((score(t, cases, half), t) for t in front),
                         key=lambda x: (x[0].false, x[0].misses))
            L.append(f"- {group}, {HALF_TITLE.get(half, 'all') if half else 'all'}: "
                     + "; ".join(f"`{t.key}` {s.false}/{s.noise} false, "
                                 f"{s.misses}/{s.signal} misses" for s, t in pts))
    if native:
        L.append("")
        L.append(f"Playwright rows: {native['tool']['invoked']} "
                 f"(`scripts/bench_playwright_grid.mjs`, "
                 f"`docs/benchmark_browser_native.json`, corpus digest "
                 f"`{native['corpus']['sha256'][:16]}…`). The comparator is "
                 f"@playwright/test {native['environment']['packages']['@playwright/test']}; "
                 "the frames were captured with the Python Playwright named above.")
    L.append("")
    return "\n".join(L)


# --------------------------------------------------------------------------- #
def main(args, *, python_env: str = "") -> int:
    """Called by tests/benchmark.py when `--corpus browser` is given."""
    manifest, cases = load()
    problems = bc.verify_files(manifest)
    if problems:
        print("the browser corpus on disk does not match its manifest: "
              + "; ".join(problems[:5]), file=sys.stderr)
        return 2
    notes: list[str] = []
    native: dict | None = None
    native_path = Path(args.native) if args.native else NATIVE_DEFAULT
    if native_path.is_file():
        try:
            native = load_native(native_path)
        except NativeError as e:
            print(f"--native: {e}", file=sys.stderr)
            return 2
    elif args.native:
        print(f"--native: {native_path} does not exist", file=sys.stderr)
        return 2
    else:
        notes.append(f"No {native_path.relative_to(ROOT)}: the Playwright rows are "
                     "left out (node scripts/bench_playwright_grid.mjs writes it).")

    if python_env:
        #  stderr, as for the synthetic corpus: stdout stays comparable.
        print(f"Run on: {python_env}", file=sys.stderr)
    engines = ENGINES[getattr(args, "engine", None) or "both"]
    tools = []
    if "v1" in engines:
        tools += [run_vistest(manifest, p, ai=not args.no_ai) for p in PRESETS]
    if "v2" in engines:
        tools.append(run_vistest(manifest, "balanced", ai=not args.no_ai, engine="v2"))
    if native is not None:
        tools += playwright_tools(native, cases)
    sys.stdout.write(report(manifest, tools, timing=not args.no_timing, notes=notes))
    if args.markdown:
        path = Path(args.markdown)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown(manifest, tools, native, ai=not args.no_ai),
                        encoding="utf-8", newline="\n")
        print(f"\nMarkdown: {path.resolve()}")
    return 0
