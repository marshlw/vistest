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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--template", action="append",
                    help="limit to these templates (repeatable); default: the "
                         "calibration half")
    args = ap.parse_args(argv)
    if cv2 is None:
        print("OpenCV is required", file=sys.stderr)
        return 2
    manifest = bc.load_manifest()
    root, bases = load(manifest)
    templates = args.template or list(manifest["split"][bc.CALIBRATION])
    cases = [c for c in manifest["cases"] if c["template"] in templates]
    out = print
    out("=== v1 on the browser corpus: where the signal is lost ===")
    out(f"OpenCV {cv2.__version__}, numpy {np.__version__}; preset balanced; templates: "
        + ", ".join(templates))
    out("")
    finding_ink(cases, bases, root, out)
    finding_unassigned(cases, bases, root, out)
    finding_consensus(cases, bases, root, out)
    finding_alignment(cases, bases, root, out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
