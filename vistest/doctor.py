# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""`vistest doctor` — measuring the project's own noise.

The first command after installation. The point is for the user to learn the
truth about their project BEFORE they trust the tool.

How it works: the page is loaded N times in a row. Nothing changed between
loads — which means **any** discrepancy here is false by definition. Then
exactly what the person cares about is computed:

    how many of the N−1 pairs would have failed if this were an ordinary run

Without suppression and with VisTest suppression. The difference between these
two numbers is the engine contribution, measured on the user data rather than
on our synthetic samples.

Two kinds of noise are handled separately, because they are treated differently:

* **within a load** — animation, spinner, timer, canvas. Caught by a series of
  consecutive frames (`capture.stability_shots`), masked automatically.
* **between loads** — fonts, lazy-load, random content, dates, the order of
  network responses. An in-frame mask cannot see it at all: each individual
  snapshot is perfectly stable, yet two snapshots from different loads are not.
  This noise is exactly what topples runs in CI, and exactly what nobody measures.

Analysis (`analyze`) deliberately does not depend on Playwright: it takes ready
frames as input. This makes it possible to test the logic on synthetic data
without a browser.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from .ai import attribution as _attr
from .config import VisTestConfig
from .core import noise as _noise
from .core.comparator import compare
from .models import ChangeKind, DiffRegion

C = {"g": "\033[32m", "y": "\033[33m", "r": "\033[31m", "b": "\033[34m",
     "d": "\033[90m", "0": "\033[0m"}


class Frame(Protocol):
    """The minimum the analysis needs from a single page load."""

    rgb: np.ndarray
    unstable: np.ndarray
    dom: dict
    notes: list[str]


@dataclass
class SimpleFrame:
    """A Frame implementation for tests and for calls from external code."""

    rgb: np.ndarray
    unstable: np.ndarray | None = None
    dom: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.unstable is None:
            self.unstable = np.zeros(self.rgb.shape[:2], dtype=bool)


# --------------------------------------------------------------------------- #
#  Report model
# --------------------------------------------------------------------------- #
@dataclass
class NoiseSource:
    """A single spot on the page that produces noise."""

    selector: str | None = None
    text: str | None = None
    kind: str = "content"
    bbox: tuple[int, int, int, int] = (0, 0, 0, 0)
    pairs_seen: int = 0            # in how many pairs it showed up
    max_severity: float = 0.0
    intra_load: bool = False       # also produces noise within a single load
    suppressed: bool = True        # whether VisTest suppresses it

    @property
    def where(self) -> str:
        if self.selector:
            return self.selector
        x, y, w, h = self.bbox
        return f"({x},{y}) {w}×{h}"

    def hint(self) -> str:
        """Why it produces noise and what to do about it."""
        if _attr.looks_like_datetime(_as_region(self)):
            return ("date or time — enable capture.determinism "
                    "or mask the block")
        if self.intra_load:
            return ("lives inside the page (animation, timer, spinner) — "
                    "caught by the auto-mask")
        if self.kind in ("moved", "resized"):
            return ("layout does not settle identically — usually a font loads "
                    "after the first render; check font-display and "
                    "capture.wait_fonts")
        return ("diverges only between loads — lazy-load, cache or "
                "random content; mask it or pin the data")


@dataclass
class DoctorReport:
    target: str = ""
    runs: int = 0
    width: int = 0
    height: int = 0

    pairs: int = 0
    raw_fails: int = 0            # would have failed without suppression
    suppressed_fails: int = 0     # would have failed with VisTest suppression

    raw_max_severity: float = 0.0
    suppressed_max_severity: float = 0.0

    size_flap: bool = False
    heights: list[int] = field(default_factory=list)
    intra_unstable_ratio: float = 0.0   # share of pixels flickering within a load
    inter_unstable_ratio: float = 0.0   # share diverging between loads

    sources: list[NoiseSource] = field(default_factory=list)
    capture_notes: list[str] = field(default_factory=list)
    artifacts: dict[str, str] = field(default_factory=dict)

    # Map of «where exactly it diverged between loads». Not serialized.
    noise_mask: np.ndarray | None = field(default=None, repr=False)

    # ---------- derived ----------
    @property
    def raw_rate(self) -> float:
        return self.raw_fails / max(self.pairs, 1)

    @property
    def suppressed_rate(self) -> float:
        return self.suppressed_fails / max(self.pairs, 1)

    @property
    def healthy(self) -> bool:
        return self.suppressed_fails == 0 and not self.size_flap

    def verdict(self) -> str:
        if self.healthy:
            return ("The project is suitable for visual testing as is: "
                    "on an unchanged page VisTest never fails.")
        if self.size_flap:
            return ("Page height is not stable between loads — this breaks any "
                    "comparison before thresholds even apply. Sort this out first.")
        return (f"On an unchanged page VisTest would have failed in "
                f"{self.suppressed_fails} of {self.pairs} runs. "
                "Until this is fixed, no failure can be trusted.")

    def recommendations(self) -> list[str]:
        out: list[str] = []
        if self.size_flap:
            hs = ", ".join(str(h) for h in sorted(set(self.heights)))
            out.append(
                f"Page height drifts between loads: {hs}px. The cause is almost "
                "always the same — lazy-load pulls in a different number of blocks. "
                "Capture the component via clip_selector or let the page "
                "finish loading: capture.scroll_through_page + settle_timeout_ms."
            )

        dated = [s for s in self.sources if _attr.looks_like_datetime(_as_region(s))]
        if dated:
            out.append(
                "Blocks with a date or time were found "
                f"({', '.join(filter(None, (s.where for s in dated[:3])))}). "
                "Enable capture.determinism — the baseline and the run will see "
                "one and the same date."
            )

        leftovers = [s for s in self.sources if not s.suppressed]
        if leftovers:
            sels = [s.selector for s in leftovers if s.selector][:6]
            out.append(
                f"{len(leftovers)} noise source(s) are not suppressed automatically. "
                + (f"Add to capture.mask_selectors: {', '.join(sels)}"
                   if sels else
                   "Mask them with an ignore zone in the UI — the selector "
                   "could not be determined.")
            )

        if self.intra_unstable_ratio > 0.25:
            out.append(
                f"{self.intra_unstable_ratio * 100:.1f}% of pixels flicker within "
                "a single load. The page is too busy: comparison will be "
                "of little use, capture individual components instead."
            )

        if not out and not self.healthy:
            out.append(
                "There is noise, but the sources were not localized. Look at "
                f"{self.artifacts.get('noise map', 'the noise map')} — "
                "everything that diverged between loads is marked in white."
            )
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "runs": self.runs,
            "size": {"width": self.width, "height": self.height,
                     "heights": self.heights, "flapping": self.size_flap},
            "false_fail": {
                "pairs": self.pairs,
                "without_suppression": self.raw_fails,
                "with_suppression": self.suppressed_fails,
                "rate_without_suppression": round(self.raw_rate, 4),
                "rate_with_suppression": round(self.suppressed_rate, 4),
            },
            "severity": {
                "without_suppression": round(self.raw_max_severity, 2),
                "with_suppression": round(self.suppressed_max_severity, 2),
            },
            "noise_ratio": {
                "intra_load": round(self.intra_unstable_ratio, 6),
                "inter_load": round(self.inter_unstable_ratio, 6),
            },
            "sources": [
                {"where": s.where, "selector": s.selector, "text": s.text,
                 "kind": s.kind, "bbox": list(s.bbox), "pairs_seen": s.pairs_seen,
                 "max_severity": round(s.max_severity, 2),
                 "intra_load": s.intra_load, "suppressed": s.suppressed,
                 "hint": s.hint()}
                for s in self.sources
            ],
            "verdict": self.verdict(),
            "recommendations": self.recommendations(),
            "capture_notes": self.capture_notes,
            "artifacts": self.artifacts,
        }


# --------------------------------------------------------------------------- #
#  Analysis (without a browser)
# --------------------------------------------------------------------------- #
def analyze(frames: list[Frame], *, cfg: VisTestConfig | None = None,
            target: str = "") -> DoctorReport:
    """Compute the report over a series of independent loads of one page.

    frames[0] plays the role of the baseline, the rest are runs. The page did
    not change between them, so every failure is false by construction.
    """
    if len(frames) < 2:
        raise ValueError("At least 2 loads are required: nothing to compare against")

    cfg = cfg or VisTestConfig.load()
    base = frames[0]
    rep = DoctorReport(
        target=target,
        runs=len(frames),
        width=int(base.rgb.shape[1]),
        height=int(base.rgb.shape[0]),
        heights=[int(f.rgb.shape[0]) for f in frames],
        pairs=len(frames) - 1,
    )
    rep.size_flap = len(set(rep.heights)) > 1
    for f in frames:
        rep.capture_notes.extend(n for n in f.notes if n not in rep.capture_notes)

    shape = base.rgb.shape[:2]
    base_mask = _fit(_mask_of(base), shape)

    # For the report — the union across all loads: how much flickers overall.
    intra = base_mask.copy()
    for f in frames[1:]:
        intra |= _fit(_mask_of(f), shape)
    rep.intra_unstable_ratio = _noise.instability_score(intra)

    inter = np.zeros(shape, dtype=bool)
    buckets: dict[tuple, NoiseSource] = {}

    for other in frames[1:]:
        # But for suppression — only the baseline mask and the current run mask.
        # A union across all loads would suppress more than is actually
        # available on the first run, and the number would come out in our favor.
        ignore = base_mask | _fit(_mask_of(other), shape)

        raw = compare(base.rgb, other.rgb, cfg=cfg.diff, name="doctor")
        sup = compare(base.rgb, other.rgb, cfg=cfg.diff, name="doctor",
                      ignore_mask=ignore)

        rep.raw_fails += int(raw.failed)
        rep.suppressed_fails += int(sup.failed)
        rep.raw_max_severity = max(rep.raw_max_severity, raw.max_severity)
        rep.suppressed_max_severity = max(rep.suppressed_max_severity,
                                          sup.max_severity)

        m = raw.maps.get("mask")
        if isinstance(m, np.ndarray):
            inter |= _fit(m.astype(bool), shape)

        # The raw-comparison regions are the full list of what produces noise.
        # An intersection with the sup regions means suppression did not work.
        regions = _attr.attribute(list(raw.regions), base.dom, other.dom)
        survived = _attr.attribute(list(sup.regions), base.dom, other.dom)
        survived_keys = {_key(r) for r in survived}

        for r in regions:
            key = _key(r)
            src = buckets.get(key)
            if src is None:
                src = NoiseSource(
                    selector=r.selector, text=r.element_text,
                    kind=r.kind.value, bbox=r.bbox,
                    intra_load=_overlaps(intra, r),
                )
                buckets[key] = src
            src.pairs_seen += 1
            src.max_severity = max(src.max_severity, r.severity)
            if key in survived_keys:
                src.suppressed = False

    rep.inter_unstable_ratio = _noise.instability_score(inter)
    rep.sources = sorted(buckets.values(),
                         key=lambda s: (-s.pairs_seen, -s.max_severity))
    rep.noise_mask = inter
    return rep


def _mask_of(frame: Frame) -> np.ndarray:
    m = getattr(frame, "unstable", None)
    if m is None:
        return np.zeros(frame.rgb.shape[:2], dtype=bool)
    return np.asarray(m).astype(bool)


def _fit(mask: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    """Fit the mask to the common size: pages of different heights are normal."""
    if mask.shape == shape:
        return mask.copy()
    out = np.zeros(shape, dtype=bool)
    h = min(shape[0], mask.shape[0])
    w = min(shape[1], mask.shape[1])
    out[:h, :w] = mask[:h, :w]
    return out


def _key(r: DiffRegion) -> tuple:
    """Grouping key for a region across pairs.

    A selector is more reliable than coordinates: the same block easily shifts
    by a dozen pixels between loads. Without DOM we fall back to a 32px grid —
    coarse, but enough not to split one source into three.
    """
    if r.selector:
        return ("sel", r.selector)
    return ("box", r.x // 32, r.y // 32, r.w // 32, r.h // 32)


def _overlaps(mask: np.ndarray, r: DiffRegion, min_ratio: float = 0.15) -> bool:
    h, w = mask.shape
    y0, y1 = max(0, r.y), min(h, r.y + r.h)
    x0, x1 = max(0, r.x), min(w, r.x + r.w)
    if y1 <= y0 or x1 <= x0:
        return False
    patch = mask[y0:y1, x0:x1]
    return float(patch.sum()) / max(patch.size, 1) >= min_ratio


def _as_region(s: NoiseSource) -> DiffRegion:
    """NoiseSource → DiffRegion, to reuse the attribution heuristics."""
    return DiffRegion(x=s.bbox[0], y=s.bbox[1], w=s.bbox[2], h=s.bbox[3],
                      kind=ChangeKind(s.kind) if s.kind in
                      {k.value for k in ChangeKind} else ChangeKind.CONTENT,
                      selector=s.selector, element_text=s.text)


# --------------------------------------------------------------------------- #
#  Capture (requires a browser)
# --------------------------------------------------------------------------- #
def collect(url: str, *, runs: int = 3, browser: str = "chromium",
            viewport: str = "1440x900", selector: str | None = None,
            steps: list | None = None, cfg: VisTestConfig | None = None,
            log=print) -> list[SimpleFrame]:
    """N independent loads of the page.

    Each one in its own BrowserContext. This is essential: a reload in the same
    context reuses the font and image cache and hides exactly the noise this
    command is run for. In CI the context is always cold.
    """
    from playwright.sync_api import sync_playwright

    from .capture.stabilize import context_options, install
    from .integrations.driver import PlaywrightDriver
    from .matrix import launch

    cfg = cfg or VisTestConfig.load()
    w, h = _viewport(viewport)
    frames: list[SimpleFrame] = []

    with sync_playwright() as p:
        br = launch(p, browser, headless=True)
        try:
            for i in range(runs):
                log(f"  load {i + 1} of {runs}…")
                ctx = br.new_context(**context_options(cfg, w, h))
                install(ctx, determinism=cfg.capture.determinism, cfg=cfg)
                page = ctx.new_page()
                page.set_default_timeout(cfg.capture.step_timeout_ms)
                try:
                    page.goto(url, wait_until="domcontentloaded",
                              timeout=cfg.capture.step_timeout_ms * 2)
                    if steps:
                        from . import scenario

                        scenario.execute(page, steps, flows=cfg.flows,
                                         timeout_ms=cfg.capture.step_timeout_ms)
                    shot = PlaywrightDriver(page).capture(
                        cfg=cfg.capture, clip_selector=selector)
                    frames.append(SimpleFrame(rgb=shot.rgb, unstable=shot.unstable,
                                              dom=shot.dom, notes=list(shot.notes)))
                finally:
                    ctx.close()
        finally:
            br.close()
    return frames


def _viewport(value: str) -> tuple[int, int]:
    try:
        a, b = str(value).lower().split("x")
        return int(a), int(b)
    except Exception:
        return 1440, 900


# --------------------------------------------------------------------------- #
#  Output
# --------------------------------------------------------------------------- #
def render_text(rep: DoctorReport, color: bool = True) -> str:
    c = C if color else dict.fromkeys(C, "")
    L: list[str] = []

    L.append(f"{c['b']}=== Project noise: {rep.target} ==={c['0']}")
    L.append(f"  loads: {rep.runs} (each in a clean context), "
             f"size {rep.width}×{rep.height}")
    if rep.size_flap:
        L.append(f"  {c['r']}height is unstable: "
                 f"{', '.join(str(x) for x in rep.heights)}px{c['0']}")
    L.append("")

    L.append(f"{c['b']}False failures on an unchanged page{c['0']}")
    L.append(f"  without suppression       {_bar(rep.raw_rate, c)} "
             f"{rep.raw_fails} of {rep.pairs}   ({rep.raw_rate * 100:.0f}%)")
    good = "g" if rep.suppressed_fails == 0 else ("y" if rep.suppressed_rate < 0.5 else "r")
    L.append(f"  with VisTest suppression  {_bar(rep.suppressed_rate, c)} "
             f"{c[good]}{rep.suppressed_fails} of {rep.pairs}   "
             f"({rep.suppressed_rate * 100:.0f}%){c['0']}")
    L.append(f"  {c['d']}maximum severity: {rep.raw_max_severity:.1f} → "
             f"{rep.suppressed_max_severity:.1f}{c['0']}")
    L.append("")

    L.append(f"{c['b']}Unstable pixels{c['0']}")
    L.append(f"  within a single load  {rep.intra_unstable_ratio * 100:6.2f}%  "
             f"{c['d']}animations, timers, canvas — masked automatically{c['0']}")
    L.append(f"  between loads         {rep.inter_unstable_ratio * 100:6.2f}%  "
             f"{c['d']}fonts, lazy-load, random content — this is what topples CI{c['0']}")
    L.append("")

    if rep.sources:
        L.append(f"{c['b']}What exactly produces noise{c['0']}")
        for i, s in enumerate(rep.sources[:12], 1):
            flag = f"{c['r']}not suppressed{c['0']}" if not s.suppressed else \
                   f"{c['g']}suppressed{c['0']}"
            L.append(f"  {i:2d}. {s.pairs_seen}/{rep.pairs} runs  "
                     f"[{s.kind:8s}] sev={s.max_severity:5.1f}  {flag}")
            L.append(f"      {s.where}" + (f'  "{s.text}"' if s.text else ""))
            L.append(f"      {c['d']}{s.hint()}{c['0']}")
        if len(rep.sources) > 12:
            L.append(f"  {c['d']}… and {len(rep.sources) - 12} more{c['0']}")
        L.append("")

    if rep.capture_notes:
        L.append(f"{c['b']}Capture notes{c['0']}")
        for n in rep.capture_notes[:8]:
            L.append(f"  • {n}")
        L.append("")

    mark = f"{c['g']}✓{c['0']}" if rep.healthy else f"{c['r']}✗{c['0']}"
    L.append(f"{mark} {rep.verdict()}")

    recs = rep.recommendations()
    if recs:
        L.append("")
        L.append(f"{c['b']}What to do{c['0']}")
        for r in recs:
            L.append(f"  → {r}")

    if rep.artifacts:
        L.append("")
        for k, v in rep.artifacts.items():
            L.append(f"  {c['d']}{k}: {v}{c['0']}")
    return "\n".join(L)


def _bar(rate: float, c: dict, width: int = 12) -> str:
    filled = int(round(rate * width))
    color = "g" if rate == 0 else ("y" if rate < 0.5 else "r")
    return f"{c[color]}{'█' * filled}{c['d']}{'·' * (width - filled)}{c['0']}"


# --------------------------------------------------------------------------- #
#  CLI entry point
# --------------------------------------------------------------------------- #
def run_doctor(args) -> int:
    cfg = VisTestConfig.load()

    target, selector, steps, viewport = _resolve_target(args, cfg)
    if not target:
        print("Specify a URL: vistest doctor https://my-app.local\n"
              "or the name of an existing baseline: vistest doctor --name checkout.png")
        return 2

    print(f"Capturing {target} {args.runs} time(s)…")
    try:
        frames = collect(target, runs=args.runs, browser=args.browser,
                         viewport=viewport, selector=selector, steps=steps,
                         cfg=cfg)
    except ImportError:
        print("playwright is required:  python run.py setup")
        return 1
    except Exception as e:
        print(f"Could not capture the page: {type(e).__name__}: {e}")
        return 1

    rep = analyze(frames, cfg=cfg, target=target)

    out = Path(args.out) if args.out else cfg.root_path / "doctor"
    out.mkdir(parents=True, exist_ok=True)
    if rep.noise_mask is not None:
        _noise.save_mask(out / "noise.png", rep.noise_mask)
        rep.artifacts["noise map"] = str((out / "noise.png").resolve())
    report_path = out / "report.json"
    rep.artifacts["report"] = str(report_path.resolve())
    report_path.write_text(
        json.dumps(rep.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")

    if args.json:
        print(json.dumps(rep.to_dict(), indent=2, ensure_ascii=False))
    else:
        print()
        print(render_text(rep))

    # Return code: 0 — the project is suitable, 1 — fix the noise first.
    return 0 if rep.healthy else 1


def _resolve_target(args, cfg) -> tuple[str, str | None, list | None, str]:
    """URL from the argument or from the passport of an existing baseline."""
    viewport = args.viewport or "1440x900"
    if args.url:
        return args.url, args.selector, None, viewport

    if not args.name:
        return "", None, None, viewport

    from .config import platform_key
    from .storage import FileBaselineStore

    platform = args.platform or platform_key(
        args.browser, cfg.capture.device_scale_factor)
    store = FileBaselineStore(cfg.baselines_path(platform))

    # Read the passport directly: no need to load the PNG itself for one field.
    meta_path = store.dir_for(args.name) / "meta.json"
    meta: dict = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text("utf-8"))
        except Exception:
            meta = {}
    if not meta.get("url"):
        print(f"The baseline {args.name} has no URL recorded — set url in the UI "
              "or pass it as an argument")
        return "", None, None, viewport
    return (meta["url"], meta.get("selector") or args.selector,
            meta.get("steps"), meta.get("viewport") or viewport)
