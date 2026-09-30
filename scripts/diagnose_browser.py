# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Where the v1 cascade loses the browser corpus — measured, not argued.

    python scripts/diagnose_browser.py                 # calibration templates
    python scripts/diagnose_browser.py --template table

The engine of v1 filters first (consensus, morphology, a tolerance that grows
with local contrast) and explains afterwards. On the browser corpus the
filters throw a signal away before it has become a region. This script
replays the cascade on the pairs where that happens, with every stage
instrumented, and prints the numbers that say *which* filter took the signal:

  1. text_color / link_color / icon_color at ΔE00 15. The `rerender` rule
     suppresses the region as "the baseline as it is reproduces 100 %".
     In `explain._search` a pixel counts as explained when its difference is
     at most CONTRAST_TOLERANCE × the local contrast. Text is nothing but
     edges with ~200 levels of contrast, so a new ink colour is always
     "within tolerance". The ink colour itself is never checked.
  2. underline / border_removed. The changed pixels reach the change mask
     but end up in no region at all (`unassigned_pixels`). Unassigned pixels
     do not take part in the verdict.
  3. fill at ΔE00 8. Every pixel of the button changed; after consensus not
     one is left: the structure did not change, ΔE00 is below
     `strong_color` (4 × 2.3 = 9.2) and the area is below the 1.25 % of the
     frame `flat_recolour` asks for.
  4. render:hinting_none and render:full_chromium (NOISE). Global alignment
     "compensates" a shift of about −1.6 px that does not exist, and what is
     left is dozens of regions of severity 94. A re-rasterised glyph changes
     shape; it does not move.
  5. render:shift_0.25px and render:shift_0.5px (NOISE). phaseCorrelate on
     the whole page answers a mix of the box edges (moved by the fraction)
     and the text (snapped to whole pixels) — dx +0.02…+0.35, dy +0.00…+0.05
     on the calibration pages moved by a quarter pixel both ways, dx
     +0.53…+0.97, dy +0.91…+1.00 on those moved by a half — and nothing
     checks it. What turns such a pair green is the re-drawing of the
     baseline region by region.

Read-only and deterministic: nothing is written, nothing is tuned. The
held-out templates are left out unless named with --template — they are for
measuring, and a diagnosis is where thresholds come from.
"""

from __future__ import annotations

import argparse
import contextlib
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import browser_corpus as bc  # noqa: E402

from vistest.config import VisTestConfig  # noqa: E402
from vistest.core import align as _align  # noqa: E402
from vistest.core import color as _color  # noqa: E402
from vistest.core import explain as _explain  # noqa: E402
from vistest.core import pngio  # noqa: E402
from vistest.core import recolour as _recolour  # noqa: E402
from vistest.core import segment as _seg  # noqa: E402
from vistest.core import structure as _struct  # noqa: E402
from vistest.core.comparator import compare  # noqa: E402

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None


# --------------------------------------------------------------------------- #
#  Tracing the v1 cascade
# --------------------------------------------------------------------------- #
@dataclass
class Trace:
    """What the stages of one v1 comparison saw."""

    alignment: _align.Alignment | None = None
    smap: np.ndarray | None = None
    flat_recolour: int = 0
    pre_clean: np.ndarray | None = None     # the change mask handed to segmentation
    cleaned: np.ndarray | None = None       # after close → open
    before_explain: list = field(default_factory=list)


@contextlib.contextmanager
def traced():
    """Wrap the module functions the comparator calls and record their answers.

    The comparator reaches every stage through a module attribute
    (`_align.align_images(...)`), so replacing the attribute for the duration
    of one call sees exactly what the engine sees — no copy of the cascade
    that could drift from it.
    """
    t = Trace()
    saved = (_align.align_images, _struct.ssim_map, _recolour.large_flat_recolour,
             _seg.clean_mask, _explain.explain_regions)

    def align_images(*a, **k):
        out, al = saved[0](*a, **k)
        t.alignment = al
        return out, al

    def ssim_map(*a, **k):
        smap, g = saved[1](*a, **k)
        t.smap = smap
        return smap, g

    def large_flat_recolour(*a, **k):
        mask, n = saved[2](*a, **k)
        t.flat_recolour = n
        return mask, n

    def clean_mask(mask, **k):
        t.pre_clean = mask.copy()
        out = saved[3](mask, **k)
        t.cleaned = out
        return out

    def explain_regions(regions, **k):
        t.before_explain = [(r.x, r.y, r.w, r.h, r.kind.value, round(r.severity, 1))
                            for r in regions]
        return saved[4](regions, **k)

    _align.align_images = align_images
    _struct.ssim_map = ssim_map
    _recolour.large_flat_recolour = large_flat_recolour
    _seg.clean_mask = clean_mask
    _explain.explain_regions = explain_regions
    try:
        yield t
    finally:
        (_align.align_images, _struct.ssim_map, _recolour.large_flat_recolour,
         _seg.clean_mask, _explain.explain_regions) = saved


def run_v1(exp: np.ndarray, act: np.ndarray, name: str):
    cfg = VisTestConfig.preset_of("balanced").diff
    with traced() as t:
        res = compare(exp, act, cfg=cfg, name=name)
    return res, t, cfg


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #
def _chmax(x: np.ndarray) -> np.ndarray:
    return np.maximum(np.maximum(x[..., 0], x[..., 1]), x[..., 2])


def _in_box(mask: np.ndarray, box) -> np.ndarray:
    x, y, w, h = box
    out = np.zeros_like(mask, dtype=bool)
    out[y:y + h, x:x + w] = mask[y:y + h, x:x + w]
    return out


def _grow(box, pad: int, shape) -> tuple[int, int, int, int]:
    x, y, w, h = box
    H, W = shape
    x0, y0 = max(0, x - pad), max(0, y - pad)
    return x0, y0, min(W, x + w + pad) - x0, min(H, y + h + pad) - y0


def ink_core_delta(exp: np.ndarray, act: np.ndarray, box) -> tuple[float, str, str]:
    """ΔE00 between the ink of both frames inside `box`.

    The paper is a 15×15 median of the baseline (text is sparse, so the
    median is the background). Among the pixels of the box that differ
    between the frames, the quarter furthest from the paper in the baseline
    is the core of the strokes; the median colour at those positions in each
    frame is its ink.
    """
    x, y, w, h = box
    e = exp[y:y + h, x:x + w]
    a = act[y:y + h, x:x + w]
    paper = cv2.medianBlur(np.ascontiguousarray(e), 15)
    far = _chmax(np.abs(e.astype(np.int16) - paper.astype(np.int16)))
    differs = np.any(e != a, axis=2)
    if not differs.any():
        return 0.0, "-", "-"
    cut = np.percentile(far[differs], 75)
    core = differs & (far >= cut)
    ca = np.median(e[core].astype(np.float64), axis=0)
    cb = np.median(a[core].astype(np.float64), axis=0)
    a8, b8 = (np.clip(np.rint(c), 0, 255).astype(np.uint8) for c in (ca, cb))
    la = _color.srgb_to_lab(a8[None, None, :])
    lb = _color.srgb_to_lab(b8[None, None, :])
    de = float(_color.delta_e_ciede2000(la, lb)[0, 0])
    return de, _hex(a8), _hex(b8)


def _hex(rgb) -> str:
    return "#" + "".join(f"{int(v):02x}" for v in rgb)


def contrast_budget(exp: np.ndarray, act: np.ndarray, evidence: np.ndarray):
    """The `rerender` tolerance at the changed pixels, as explain._search computes it."""
    e = exp.astype(np.float32)
    a = act.astype(np.float32)
    k = np.ones((3, 3), np.uint8)
    contrast = _chmax(cv2.dilate(e, k) - cv2.erode(e, k))
    diff = _chmax(np.abs(e - a))
    tol = np.where(contrast >= _explain.MIN_EDGE_CONTRAST,
                   _explain.CONTRAST_TOLERANCE * contrast, -1.0)
    ev = evidence
    within = (diff <= tol) & ev
    return {
        "pixels": int(ev.sum()),
        "on_edge": int((ev & (contrast >= _explain.MIN_EDGE_CONTRAST)).sum()),
        "median_contrast": float(np.median(contrast[ev])) if ev.any() else 0.0,
        "median_diff": float(np.median(diff[ev])) if ev.any() else 0.0,
        "within": int(within.sum()),
    }


def load(manifest: dict):
    root = bc.CORPUS_DIR
    bases = {k: pngio.read(root / t["base"]) for k, t in manifest["templates"].items()}
    return root, bases


# --------------------------------------------------------------------------- #
#  The four findings
# --------------------------------------------------------------------------- #
def finding_ink(cases, bases, root, out):
    out("1. Colour of ink at ΔE00 15 — the `rerender` rule calls it noise")
    out("   rule: a pixel is explained when |diff| <= "
        f"{_explain.CONTRAST_TOLERANCE:g} x local contrast "
        f"(3x3, largest channel, contrast >= {_explain.MIN_EDGE_CONTRAST:g})")
    for c in cases:
        if c["family"] not in ("text_color", "link_color", "icon_color") \
                or c["magnitude"] != "de15":
            continue
        exp, act = bases[c["template"]], pngio.read(root / c["actual"])
        res, t, _ = run_v1(exp, act, c["name"])
        rr = [r for r in res.suppressed if (r.suppressed_by or "").startswith("rerender")]
        as_is = [r for r in rr if "as it is" in (r.suppressed_by or "")]
        box = tuple(c["changed"]["box"])
        ev = _in_box(t.pre_clean, _grow(box, 2, exp.shape[:2]))
        b = contrast_budget(exp, act, ev)
        de, h0, h1 = ink_core_delta(exp, act, box)
        out(f"   {c['name']:26s} verdict {res.verdict.value:4s} live {len(res.regions):2d}  "
            f"rerender-suppressed {len(rr):2d} (as it is: {len(as_is):2d})  "
            f"changed px {b['pixels']:5d}, on an edge {b['on_edge']:5d}, "
            f"within tolerance {b['within']:5d}; median contrast "
            f"{b['median_contrast']:5.1f}, median |diff| {b['median_diff']:5.1f}; "
            f"ink {h0} -> {h1} ΔE00 {de:4.1f}")
        d = c.get("detail") or {}
        out(f"   {'':26s} css {d.get('before')} -> {d.get('after')} "
            f"(ΔE00 {d.get('delta_e00')}); "
            + (f"e.g. «{rr[0].suppressed_by}»" if rr else "nothing suppressed"))
    out("")


def finding_unassigned(cases, bases, root, out):
    out("2. underline / border_removed — pixels in the mask, in no region")
    for c in cases:
        if c["family"] not in ("underline", "border_removed"):
            continue
        exp, act = bases[c["template"]], pngio.read(root / c["actual"])
        res, t, cfg = run_v1(exp, act, c["name"])
        box = tuple(c["changed"]["box"])
        grown = _grow(box, 2, exp.shape[:2])
        in_mask = int(_in_box(t.pre_clean, grown).sum())
        after_clean = int(_in_box(t.cleaned, grown).sum())
        out(f"   {c['name']:30s} verdict {res.verdict.value:4s} regions {len(res.regions)} "
            f"suppressed {len(res.suppressed)}; changed_pixels {res.changed_pixels:4d}, "
            f"unassigned {res.unassigned_pixels:4d}; in the mask at the change "
            f"{in_mask:4d} px, after close {cfg.morph_close_px} → open {cfg.morph_open_px} "
            f"{after_clean:4d} px")
    out("   unassigned pixels are counted, reported, and take no part in the verdict")
    out("")


def finding_consensus(cases, bases, root, out):
    out("3. fill at ΔE00 8 — consensus leaves nothing")
    for c in cases:
        if c["family"] != "fill" or c["magnitude"] != "de8":
            continue
        exp, act = bases[c["template"]], pngio.read(root / c["actual"])
        res, t, cfg = run_v1(exp, act, c["name"])
        de = res.maps["de_map"]
        color_hit = de > cfg.delta_e_threshold
        struct_hit = t.smap < cfg.ssim_threshold
        strong = de > cfg.delta_e_threshold * 4.0
        n, _, stats, _ = cv2.connectedComponentsWithStats(color_hit.astype(np.uint8), 8)
        largest = int(stats[1:, cv2.CC_STAT_AREA].max()) if n > 1 else 0
        frame = de.size
        rgb = int(np.any(exp != act, axis=2).sum())
        out(f"   {c['name']:22s} verdict {res.verdict.value:4s}  RGB differs: {rgb:5d} px;  "
            f"ΔE00 > "
            f"{cfg.delta_e_threshold:g}: {int(color_hit.sum()):5d} px;  & SSIM < "
            f"{cfg.ssim_threshold:g}: {int((color_hit & struct_hit).sum()):4d};  ΔE00 > "
            f"{4 * cfg.delta_e_threshold:.1f} (strong): {int(strong.sum()):4d};  largest "
            f"component {largest} px = {100.0 * largest / frame:.2f}% of the frame "
            f"(flat_recolour needs {cfg.flat_recolour_min_area_pct:g}%: "
            f"{t.flat_recolour} taken)"
            f";  after consensus and AA {res.changed_pixels} px")
    out("")


def finding_alignment(cases, bases, root, out):
    out("4. render:hinting_none / render:full_chromium (NOISE) — "
        "an alignment that is not there")
    for c in cases:
        if c["family"] not in ("render:hinting_none", "render:full_chromium"):
            continue
        exp, act = bases[c["template"]], pngio.read(root / c["actual"])
        res, t, cfg = run_v1(exp, act, c["name"])
        al = t.alignment
        raw = _color.delta_e_ciede2000(_color.srgb_to_lab(exp), _color.srgb_to_lab(act))
        raw_hit = int((raw > cfg.delta_e_threshold).sum())
        al_hit = int((res.maps["de_map"] > cfg.delta_e_threshold).sum())
        sev = sorted((r.severity for r in res.regions), reverse=True)
        top = sev[0] if sev else 0.0
        hi = sum(1 for s in sev if s >= 90.0)
        med = float(np.median(sev)) if sev else 0.0
        out(f"   {c['name']:34s} verdict {res.verdict.value:4s}  align "
            f"dx {al.dx:+.2f} dy {al.dy:+.2f} (response {al.confidence:.3f}, "
            f"applied {al.applied});  ΔE00 > {cfg.delta_e_threshold:g}: "
            f"{raw_hit:6d} px as drawn, {al_hit:6d} px after alignment;  live regions "
            f"{len(res.regions):3d}, severity median {med:4.1f}, >= 90: {hi:2d}, "
            f"max {top:5.1f}")
    out("   a re-rasterised glyph changes shape, not position: the \"shift\" moves "
        "every edge of the page and multiplies the difference it was meant to remove")
    out("")


def finding_page_shift(cases, bases, root, out):
    """What v1 does with a page drawn a fraction of a pixel over, next to the
    shift v2 fits on the page's box edges (core/v2/pageshift.py)."""
    from collections import Counter

    from vistest.core.v2 import V2Config
    from vistest.core.v2 import base as _v2base
    from vistest.core.v2 import pageshift as _pageshift

    v2 = V2Config()
    out("5. render:shift_0.25px / render:shift_0.5px (NOISE) — the page moved by a "
        "fraction; v1 estimates the shift on the whole page and never checks it")
    for c in cases:
        if not c["family"].startswith("render:shift_"):
            continue
        exp, act = bases[c["template"]], pngio.read(root / c["actual"])
        res, t, _ = run_v1(exp, act, c["name"])
        al = t.alignment
        la, lb = _color.srgb_to_lab(exp), _color.srgb_to_lab(act)
        _, cand = _v2base.candidates(la, lb, _v2base.differs(exp, act), v2.jnd_delta_e)
        fit = _pageshift.fit(exp, la, lb, cand, v2.jnd_delta_e, v2.move_tolerance)
        by = Counter((r.suppressed_by or "").split(":")[0] for r in res.suppressed)
        why = ", ".join(f"{k} {n}" for k, n in sorted(by.items())) or "none"
        out(f"   {c['name']:30s} v1 {res.verdict.value:4s}  phaseCorrelate dx {al.dx:+.2f} "
            f"dy {al.dy:+.2f} (applied {al.applied});  v2 fits ({fit.dx:+g}, {fit.dy:+g}): "
            f"{fit.moved_share:.0%} of the box edges moved;  v1 live regions "
            f"{len(res.regions)}, suppressed by: {why}")
    out("   the estimate mixes the box edges, moved by the fraction, with the text, "
        "snapped to whole pixels; what passes is passed region by region by re-drawing "
        "the baseline (antialias, rerender) — the same tolerance that lets a new ink "
        "colour through (1)")
    out("")


# --------------------------------------------------------------------------- #
#  v2: what the re-rasterisation rule did, region by region
# --------------------------------------------------------------------------- #
def _family_key(c: dict) -> str:
    if c["kind"] in ("render", "os") or c["label"] == bc.SIGNAL:
        return c["family"]
    return f"{c['family']} {c['magnitude']}"


def _q(values, p) -> float:
    return float(np.percentile(values, p)) if len(values) else float("nan")


#: `--as-if-changed`: two canaries one pixel apart, so the rule runs on every
#: pair whatever its frames' renderers — to see what each property would do.
_AS_IF_CHANGED = (np.zeros((1, 1, 3), np.uint8), np.full((1, 1, 3), 255, np.uint8))


def v2_rerender_table(cases, bases, root, out, manifest, *, as_if_changed=False) -> None:
    """Per family: regions of the v2 base, how many the rule explained, and how
    many failed each deciding property (a region is counted under every
    property it failed); the drift each glyph needed, the moves (e) found,
    and (d) — printed, not deciding."""
    from collections import Counter

    sys.path.insert(0, str(bc.REPO / "tests"))
    from benchmark_browser import fingerprints, renderer_pair

    from vistest.core.v2 import V2Config
    from vistest.core.v2.rerender import _moved

    v2 = V2Config()
    canaries = fingerprints(manifest, root)
    out("=== v2: text re-rasterisation, region by region ===")
    out("the rule runs only where the renderer is proven changed (canary of each "
        "frame, from the manifest)" if not as_if_changed else
        "AS IF the renderer had changed on every pair (--as-if-changed): what each "
        "property would do; not what the engine does")
    out(f"deciding: (a) ink ΔE00 < {v2.ink_delta_e:g}; (b) each glyph within 1 px after "
        f"a shift along the line of up to {v2.glyph_drift_px} px; (c) paper ΔE00 < "
        f"{v2.jnd_delta_e:g} and nothing changed away from the ink; (e) not a pure "
        f"shift: the closest whole-pixel move leaves > {v2.shift_residual:.0%} of the "
        "changed px. Printed only: (d) the page's share of changed ink clusters")
    out("renderer: pairs whose canaries are the same / differ / unknown; regions: "
        "live after the base; «b needs»: the drift the glyphs of the regions "
        "looked at needed, median and largest (∞: one fits at no shift up to 8 px); "
        "moves: the most common move among regions failing (e)")
    fams: dict[tuple[str, str], dict] = {}
    for c in cases:
        exp, act = bases[c["template"]], pngio.read(root / c["actual"])
        rend = _AS_IF_CHANGED if as_if_changed else renderer_pair(c, canaries)
        res = compare(exp, act, cfg=VisTestConfig.preset_of("balanced").diff,
                      name=c["name"], engine="v2", renderer=rend)
        d = fams.setdefault((_family_key(c), c["label"]), {
            "pairs": 0, "green": 0, "rend": Counter(), "regions": 0, "looked": 0,
            "explained": 0, "a": 0, "b": 0, "c": 0, "e": 0, "only b": 0,
            "need": [], "moves": Counter(), "share": [],
            "ps_holds": 0, "ps_moved": [], "ps_cover": [], "ps_shifts": Counter(),
            "ps_explained": 0, "ps_moments": 0, "ps_refused": 0})
        d["pairs"] += 1
        ps = res.maps.get("v2_page_shift") or {}
        if ps:
            d["ps_moved"].append(ps["moved"])
            d["ps_cover"].append(ps["cover"])
            if ps["holds"]:
                d["ps_holds"] += 1
                d["ps_shifts"][(ps["dx"], ps["dy"])] += 1
                d["ps_explained"] += sum(1 for x in ps["regions"] if x["explained"])
                d["ps_moments"] += sum(1 for x in ps["regions"] if x.get("how") == "moments")
                d["ps_refused"] += sum(1 for x in ps["regions"] if not x["explained"])
        d["green"] += res.verdict.value == "pass"
        d["rend"][res.maps["renderer"].status] += 1
        d["regions"] += sum(1 for r in res.regions + res.suppressed
                            if not (r.suppressed_by or "").startswith("min-size"))
        recs = res.maps.get("v2_rerender") or []
        if recs:
            d["share"].append(recs[0]["text_share"])
        for r in recs:
            d["looked"] += 1
            failed = [k for k in ("a", "b", "c", "e") if not r["holds"][k]]
            d["explained"] += not failed
            for k in failed:
                d[k] += 1
            d["only b"] += failed == ["b"]
            need = r["glyph_need_px"]
            d["need"].append(float("inf") if need is None else need)
            if not r["holds"]["e"]:
                d["moves"][tuple(r["shift"])] += 1
    head = (f"{'family':28s} {'label':8s} {'pass':>7s} {'renderer s/d/u':>14s} "
            f"{'regions':>7s} {'looked':>6s} {'expl.':>6s} {'fail a':>6s} {'fail b':>6s} "
            f"{'fail c':>6s} {'fail e':>6s} {'only b':>6s} {'b needs':>8s} "
            f"{'(d) share':>10s}  moves")
    out(head)
    out("-" * len(head))
    for (key, label), d in fams.items():
        share = (f"{min(d['share']):.2f}–{max(d['share']):.2f}" if d["share"] else "—")
        need = d["need"]
        needs = "—"
        if need:
            med, top = np.median(need), max(need)
            needs = "/".join("∞" if v == float("inf") else f"{v:g}" for v in (med, top))
        rend = f"{d['rend']['same']}/{d['rend']['changed']}/{d['rend']['unknown']}"
        mv = d["moves"].most_common(1)
        moves = (f"{_moved(*mv[0][0])[len('the block moved by '):]} ×{mv[0][1]}"
                 if mv else "")
        out(f"{key:28s} {label:8s} {d['green']:>3d}/{d['pairs']:<3d} {rend:>14s} "
            f"{d['regions']:7d} {d['looked']:6d} {d['explained']:6d} {d['a']:6d} "
            f"{d['b']:6d} {d['c']:6d} {d['e']:6d} {d['only b']:6d} {needs:>8s} "
            f"{share:>10s}  {moves}")
    out("")
    v2_page_shift_table(fams, v2, out)


def v2_page_shift_table(fams: dict, v2, out) -> None:
    """Per family: the page's move (core/v2/pageshift.py) — how many pairs it
    was proven on, the shifts fitted there, the share of box edges moved and
    of changed pixels reproduced (smallest–largest over the pairs where a
    shift was fitted), and the regions it explained or refused."""
    out("=== v2: the page moved by a fraction of a pixel ===")
    out(f"proven when at least {v2.page_shift_moved:.0%} of the page's box edges moved "
        f"by the fraction and by no whole pixel, and the move reproduces at least "
        f"{v2.page_shift_cover:.0%} of the changed pixels (ΔE00 ≤ {v2.move_tolerance:g}); "
        f"then a region is explained when the move misses at most "
        f"{v2.shift_region_miss:.0%} of it, or it is the drawing moved (coverage M0 within "
        f"{v2.shift_mass_change:.1%}, its centre within {v2.shift_centroid_px:g} px of the "
        f"move; «moments»), or text redrawn at the new position")
    head = (f"{'family':28s} {'label':8s} {'proven':>7s} {'edges moved':>12s} "
            f"{'reproduced':>11s} {'explained':>9s} {'moments':>7s} {'refused':>7s}  shifts")
    out(head)
    out("-" * len(head))

    def span(v):
        return f"{min(v):.3f}–{max(v):.3f}" if v else "—"
    for (key, label), d in fams.items():
        shifts = ", ".join(f"({dx:+g}, {dy:+g}) ×{n}"
                           for (dx, dy), n in sorted(d["ps_shifts"].items()))
        out(f"{key:28s} {label:8s} {d['ps_holds']:>3d}/{d['pairs']:<3d} "
            f"{span(d['ps_moved']):>12s} {span(d['ps_cover']):>11s} "
            f"{d['ps_explained']:9d} {d['ps_moments']:7d} {d['ps_refused']:7d}  {shifts}")
    out("")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--template", action="append",
                    help="limit to these templates (repeatable); default: the "
                         "calibration half")
    ap.add_argument("--v2", action="store_true",
                    help="instead of the v1 findings: what v2's re-rasterisation "
                         "rule did, by family (regions explained, failed per "
                         "property, the drift glyphs needed, the moves found), "
                         "and where the page was proven to have moved")
    ap.add_argument("--as-if-changed", action="store_true",
                    help="with --v2: run the rule on every pair as if the "
                         "renderer had changed — what each property would do")
    args = ap.parse_args(argv)
    if cv2 is None:
        print("OpenCV is required", file=sys.stderr)
        return 2
    manifest = bc.load_manifest()
    root, bases = load(manifest)
    templates = args.template or list(manifest["split"][bc.CALIBRATION])
    cases = [c for c in manifest["cases"] if c["template"] in templates]
    out = print
    if args.v2:
        out(f"OpenCV {cv2.__version__}, numpy {np.__version__}; templates: "
            + ", ".join(templates))
        v2_rerender_table(cases, bases, root, out, manifest,
                          as_if_changed=args.as_if_changed)
        return 0
    out("=== v1 on the browser corpus: where the signal is lost ===")
    out(f"OpenCV {cv2.__version__}, numpy {np.__version__}; preset balanced; templates: "
        + ", ".join(templates))
    out("")
    finding_ink(cases, bases, root, out)
    finding_unassigned(cases, bases, root, out)
    finding_consensus(cases, bases, root, out)
    finding_alignment(cases, bases, root, out)
    finding_page_shift(cases, bases, root, out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
