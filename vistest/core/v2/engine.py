# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""The v2 comparison: base, then explanations, then the verdict.

Reached through `vistest.core.compare(..., engine="v2")`. The result has the
shape of a v1 result — the same `CompareResult`, the same `DiffRegion`s —
so every consumer of the engine reads it without knowing which path made it.
What differs is what the fields promise:

* `changed_pixels` is the candidate mask (ΔE00 above `jnd_delta_e`), and
  `region_pixels + suppressed_pixels` plus the pixels below a threshold a
  person set make `changed_pixels`: nothing unassigned;
* a region in `regions` is one nothing explained, and any such region fails
  the comparison — severity orders them, it does not decide, unless a person
  set a threshold: then one below it is in `below_threshold`, said and not
  failed on (`_apply_threshold`);
* a region in `suppressed` names in `suppressed_by` the rule that took it out
  and the numbers that rule measured.
"""

from __future__ import annotations

import time

import numpy as np

from ...models import ChangeKind, CompareResult, DiffRegion, Verdict
from .. import align as _align
from .. import classify as _cls
from .. import color as _color
from .. import engines as _engines
from .. import explain as _explain
from .. import renderer as _renderer
from .. import structure as _struct
from .. import warp as _warp
from ..settings import DiffConfig
from . import base as _base
from . import describe as _describe
from . import pageshift as _pageshift
from . import rerender as _rerender
from .settings import V2Config

ENGINE = "v2"

#: What the notes say about the renderer, one sentence per answer.
RENDERER_NOTES = {
    _renderer.SAME: "renderer: same as the baseline's",
    _renderer.UNKNOWN: "renderer: unknown — text re-rasterisation is not explained",
    _renderer.CHANGED: "renderer differs from the baseline's (canary: {pixels} px): "
                       "typography changes within a pixel cannot be verified here "
                       "— make baselines on this renderer to check them",
}


def compare(
    expected_rgb: np.ndarray,
    actual_rgb: np.ndarray,
    *,
    cfg: DiffConfig | None = None,
    v2: V2Config | None = None,
    name: str = "snapshot",
    ignore_mask: np.ndarray | None = None,
    ai_hooks=None,
    renderer=None,
) -> CompareResult:
    """Compare two RGB images (uint8, H×W×3) the v2 way. See the module docstring.

    `renderer` is `None` or the pair of canaries (`core/renderer.py`); text
    re-rasterisation is explained only when they prove the renderer changed.
    """
    from ..comparator import _check_size, _fit_mask, _identical, _same_pixels

    t0 = time.perf_counter()
    cfg = cfg or DiffConfig()
    v2 = v2 or V2Config()
    _check_size(expected_rgb, "the baseline", cfg.max_pixels)
    _check_size(actual_rgb, "the screenshot", cfg.max_pixels)

    res = CompareResult(name=name, verdict=Verdict.PASS)
    res.size_expected = (int(expected_rgb.shape[1]), int(expected_rgb.shape[0]))
    res.size_actual = (int(actual_rgb.shape[1]), int(actual_rgb.shape[0]))
    if _same_pixels(expected_rgb, actual_rgb):
        return _identical(res, expected_rgb, actual_rgb, t0)

    exp, act, padded = _align.reconcile_sizes(expected_rgb, actual_rgb)
    if padded:
        res.size_changed = True
        res.notes.append(
            f"Size changed: {res.size_expected} → {res.size_actual}. "
            "The images were padded to a common size (not cropped), "
            "the difference goes into the diff.")
    if ignore_mask is not None and ignore_mask.shape != exp.shape[:2]:
        ignore_mask = _fit_mask(ignore_mask, exp.shape[:2])
    h, w = exp.shape[:2]
    res.total_pixels = h * w

    # ---------- 1. Base: every pixel above the threshold of discernibility ----
    lab_exp = _color.srgb_to_lab(exp)
    lab_act = _color.srgb_to_lab(act)
    de, cand = _base.candidates(lab_exp, lab_act, _base.differs(exp, act),
                                v2.jnd_delta_e, ignore_mask)
    labels, groups = _base.group(cand, v2.group_px)

    gray_exp = _warp.to_uint8(lab_exp[:, :, 0] * 2.55)
    gray_act = _warp.to_uint8(lab_act[:, :, 0] * 2.55)
    _, res.ssim_global = _struct.ssim_map(gray_exp, gray_act)

    changed = de[cand]
    res.changed_pixels = int(cand.sum())
    res.de_mean = float(changed.mean()) if changed.size else 0.0
    res.de_p95 = float(np.percentile(changed, 95)) if changed.size else 0.0
    res.changed_area_pct = 100.0 * res.changed_pixels / max(res.total_pixels, 1)

    regions = [_region(g, gray_exp, gray_act, de, cand, res.total_pixels, cfg)
               for g in groups]
    group_of = {id(r): g for r, g in zip(regions, groups, strict=True)}
    for r, g in zip(regions, groups, strict=True):
        if g.pixels < v2.min_region_px:
            r.suppressed_by = (f"min-size: {g.pixels} px < {v2.min_region_px} px "
                               f"(v2.min_region_px) (was {r.kind.value})")

    # ---------- 2. Explanations: named rules, all-or-nothing per region ----
    #  The environment, not the page: a scroll bar that appeared, a frame
    #  that went through a JPEG encoder — v1's rules, each decided once for
    #  the frame and then region by region.
    environment = _explain_environment(regions, groups, labels, exp, act, lab_act, cand,
                                       v2, res)
    #  The page moved by a fraction of a pixel: proven on its box edges
    #  first, whatever the renderer, then region by region. Coverage moments
    #  (a drawing the rasteriser drew again at the fraction) only where the
    #  canaries prove it is the same rasteriser: another one draws another
    #  picture, and its coverage is not the baseline's to keep.
    rend = _renderer.check(renderer)
    shifted = _explain_page_shift(regions, groups, labels, exp, act, lab_exp, lab_act,
                                  cand, v2, res, moments=rend.same)
    #  Text re-rasterisation is the renderer's doing, so it is looked for only
    #  where the renderer is proven to have changed. The same renderer, or
    #  one nobody measured, leaves every region for what it is.
    res.notes.append(RENDERER_NOTES[rend.status].format(pixels=rend.pixels))
    assessed = (_explain_rerender(regions, groups, labels, exp, act, lab_exp, lab_act,
                                  cand, v2, res) if rend.changed else [])

    # ---------- 3. AI layer (optional), on what nothing explained ----------
    #  Whatever the layer does, every region stays accounted for: one it does
    #  not hand back is suppressed under the layer's name, never dropped.
    live = [r for r in regions if not r.suppressed_by]
    if ai_hooks is not None and live:
        try:
            explained = [r for r in regions if r.suppressed_by]
            back = ai_hooks.refine(live, exp, act, res)
            kept = {id(r) for r in back}
            layer = _layer_name(ai_hooks)
            for r in live:
                if id(r) not in kept:
                    r.suppressed_by = (f"ai-layer: {layer} did not return this region "
                                       f"(was {r.kind.value})")
                    explained.append(r)
            regions = list(back) + explained
        except Exception as e:  # AI should never fail the test
            res.notes.append(f"AI layer skipped: {type(e).__name__}: {e}")

    # ---------- 4. Separation and verdict ----------
    ignore_kinds = {ChangeKind(k) for k in cfg.ignore_kinds}
    for r in regions:
        if r.kind in ignore_kinds and not r.suppressed_by:
            r.suppressed_by = f"ignored-kind: {r.kind.value} (diff.ignore_kinds)"
        (res.suppressed if r.suppressed_by else res.regions).append(r)
    res.regions.sort(key=lambda r: (-r.severity, r.y, r.x))
    _describe_regions(res.regions, group_of, labels, exp, act, lab_exp, lab_act, v2)
    _apply_threshold(res, cfg)
    res.max_severity = max((r.severity for r in (*res.regions, *res.below_threshold)),
                           default=0.0)
    _account(res)
    res.notes.insert(0, f"Engine {ENGINE}: ΔE00 > {v2.jnd_delta_e:g} per pixel, grouped "
                        f"within {v2.group_px} px; {len(groups)} region(s), "
                        f"{len(res.suppressed)} explained.")
    res.verdict = _verdict(res, cfg)
    res.duration_ms = int((time.perf_counter() - t0) * 1000)

    res.maps["v2_environment"] = environment
    res.maps["v2_rerender"] = assessed
    res.maps["v2_page_shift"] = shifted
    res.maps["renderer"] = rend
    res.maps["de_map"] = de
    res.maps["mask"] = cand
    res.maps["labels"] = labels
    res.maps["aligned_actual"] = act
    res.maps["expected"] = exp
    return res


def _layer_name(hooks) -> str:
    return str(getattr(hooks, "name", None) or type(hooks).__name__)


def _describe_regions(live, group_of, labels, exp, act, lab_exp, lab_act,
                      v2: V2Config) -> None:
    """Every region that counts gets one sentence of what was measured on it
    (core/v2/describe.py), as the first of its annotations. A region the AI
    layer made up has no group behind it, and gets none."""
    for r in live:
        g = group_of.get(id(r))
        if g is None:
            continue
        ys, xs = np.nonzero(labels[g.y:g.y + g.h, g.x:g.x + g.w] == g.label)
        where = (ys + g.y, xs + g.x)
        c = _rerender.crop(exp, act, labels, g.label, (g.x, g.y, g.w, g.h), v2.group_px)
        forward = _rerender.block_shift(exp, act, lab_exp, lab_act, where,
                                        jnd=v2.jnd_delta_e, limit=v2.shift_residual,
                                        pure_only=True)
        backward = forward if forward.holds() else _rerender.block_shift(
            act, exp, lab_act, lab_exp, where, jnd=v2.jnd_delta_e,
            limit=v2.shift_residual, pure_only=True)
        d = _describe.describe(c, forward, backward, jnd=v2.jnd_delta_e,
                               ink_limit=v2.ink_delta_e)
        r.annotations.insert(0, d.annotation(f"engine {ENGINE}"))


def _explain_environment(regions, groups, labels, exp, act, lab_act, cand,
                         v2: V2Config, res: CompareResult) -> dict:
    """v1's scroll-bar and JPEG rules (core/explain.py), on the regions still live.

    **Scroll bar** — v1's rule as it is: a band 4–20 px wide at the right or
    bottom edge of the frame, featureless along its length in both frames,
    changed along at least 80 % of it, the page beside it untouched
    (`explain.scrollbar_bands`); a region inside the band is the scroll bar
    (`explain.in_band`).

    **JPEG** — v1's detection as it is (`explain.detect_jpeg`): re-encoding
    the baseline at one of the qualities v1 tries leaves at most half of the
    error the baseline leaves in the changed areas. Region by region, not
    v1's test, which would break two things v2 promises:

    * v1 calls a pixel reproduced when it is within 30 % of the local
      contrast, and on text that lets a new ink colour through
      (`scripts/diagnose_browser.py`, finding 1). Here a changed pixel is
      reproduced when the re-encoded baseline is not discernibly different
      from it — ΔE00 at most `jnd_delta_e`, the test that made it a
      candidate.
    * v1 lets 5 % of a region stay unexplained. v2 groups without an
      opening, and on a JPEG frame the ringing joins a whole page into one
      region: 5 % of it is a paragraph. Here what the re-encoding leaves is
      grouped like the base groups (`group_px`), and a region is explained
      only when no group of `min_region_px` or more is left — the base's
      own «this is a change».

    Neither runs on a frame whose size changed, as in v1. Returns what was
    found, for the benchmark and the diagnosis (kept in `res.maps`).
    """
    record: dict = {"scrollbar": [], "jpeg": None, "explained": 0}
    live = [(r, g) for r, g in zip(regions, groups, strict=True) if not r.suppressed_by]
    if not live or res.size_changed:
        return record
    counts: dict[str, int] = {}

    def suppress(r, rule: str, detail: str) -> None:
        r.suppressed_by = f"{rule}: {detail} (was {r.kind.value})"
        r.kind = ChangeKind.NOISE
        r.severity = 0.0
        counts[rule] = counts.get(rule, 0) + 1

    bands = _explain.scrollbar_bands(exp, act)
    record["scrollbar"] = [{"edge": b.edge, "width": b.width} for b in bands]
    for r, _g in live:
        why = _explain.in_band(r, bands, exp.shape[:2]) if bands else None
        if why is not None:
            suppress(r, why.rule, why.detail)
    live = [(r, g) for r, g in live if not r.suppressed_by]

    codec = _explain.detect_jpeg(exp, act, [r for r, _ in live], cand) if live else None
    if codec is not None:
        #  The whole frame, once the quality is known: v1 re-encodes the
        #  changed boxes on the 16-px grid, and a decoder that upsamples
        #  chroma reads the neighbouring blocks, so a box differs from the
        #  frame along its border — invisible under v1's tolerance, not
        #  under ΔE00 1.
        quality = codec[0]
        reencoded = _explain.reencode(exp, quality)
        record["jpeg"] = quality
        ys, xs = np.nonzero(cand)
        lab_re = _color.srgb_to_lab(np.ascontiguousarray(reencoded[ys, xs])[None])
        left = np.zeros(cand.shape, bool)
        left[ys, xs] = _color.delta_e_ciede2000(lab_re, lab_act[ys, xs][None])[0] \
            > v2.jnd_delta_e
        for r, g in live:
            box = (slice(g.y, g.y + g.h), slice(g.x, g.x + g.w))
            mine = left[box] & (labels[box] == g.label)
            kept = int(mine.sum())
            _, pieces = _base.group(mine, v2.group_px)
            largest = max((p.pixels for p in pieces), default=0)
            done = f"the baseline re-encoded at JPEG quality {quality} reproduces " \
                   f"{100.0 * (g.pixels - kept) / g.pixels:.0f}% of the changed pixels " \
                   f"within ΔE00 {v2.jnd_delta_e:g}"
            if largest < v2.min_region_px:
                suppress(r, "jpeg", f"{done}; what is left is in groups under "
                                    f"{v2.min_region_px} px ({kept} of {g.pixels} px)")
            else:
                r.annotations.append({
                    "text": f"not JPEG re-encoding — {done}, and leaves {kept} px, the "
                            f"largest group {largest} px",
                    "kind": "jpeg", "value": largest, "source": f"engine {ENGINE}"})
    record["explained"] = sum(counts.values())
    if counts:
        names = {"scrollbar": "a scroll bar band", "jpeg": "JPEG re-encoding"}
        res.notes.append("Noise explained and suppressed: " + ", ".join(
            f"{n} region(s) by {names[k]}" for k, n in sorted(counts.items())) + ".")
    return record


def _explain_page_shift(regions, groups, labels, exp, act, lab_exp, lab_act, cand,
                        v2: V2Config, res: CompareResult, *, moments: bool) -> dict:
    """The page moved by a fraction of a pixel (core/v2/pageshift.py).

    The move is fitted on the page's box edges and must prove itself there
    (`page_shift_moved`) and on the page's changed pixels
    (`page_shift_cover`) before any region is looked at. Returns what was
    measured, for the benchmark and the diagnosis (kept in `res.maps`).
    """
    live = [(r, g) for r, g in zip(regions, groups, strict=True) if not r.suppressed_by]
    if not live:
        return {}
    ps = _pageshift.fit(exp, lab_exp, lab_act, cand, v2.jnd_delta_e, v2.move_tolerance)
    if ps is None:
        return {}
    record = {"dx": ps.dx, "dy": ps.dy, "moved": ps.moved_share, "cover": ps.cover,
              "holds": ps.holds(v2.page_shift_moved, v2.page_shift_cover), "regions": []}
    if not record["holds"]:
        return record
    explained = 0
    for r, g in live:
        box = (g.x, g.y, g.w, g.h)
        where = np.zeros(cand.shape, bool)
        where[g.y:g.y + g.h, g.x:g.x + g.w] = labels[g.y:g.y + g.h, g.x:g.x + g.w] == g.label
        crop = _rerender.crop(exp, act, labels, g.label, box, v2.group_px)
        rs = _pageshift.region(ps, where & cand, box, crop, exp, act, lab_exp, lab_act,
                               limit=v2.shift_region_miss, ink_limit=v2.ink_delta_e,
                               jnd=v2.jnd_delta_e, mass_limit=v2.shift_mass_change,
                               centroid_limit_px=v2.shift_centroid_px, moments=moments)
        mo = rs.moments
        record["regions"].append({
            "box": box, "pixels": g.pixels, "missed": rs.missed_share,
            "explained": rs.explained, "how": rs.how,
            "mass_change": None if mo is None else mo.mass_change,
            "centroid_miss": None if mo is None else mo.centroid_miss})
        if rs.explained:
            r.suppressed_by = (f"{_pageshift.RULE}: {ps.text()}; this region: {rs.text()} "
                               f"(was {r.kind.value})")
            r.kind = ChangeKind.NOISE
            r.severity = 0.0
            explained += 1
        else:
            r.annotations.append({
                "text": f"not the page's move ({ps.dx:+g}, {ps.dy:+g}) px — {rs.text()}",
                "kind": _pageshift.RULE, "value": round(rs.missed_share, 3),
                "source": f"engine {ENGINE}"})
    res.notes.append(f"Noise explained and suppressed: {explained} region(s) by the page's "
                     f"move — {ps.text()}.")
    return record


def _explain_rerender(regions, groups, labels, exp, act, lab_exp, lab_act, cand,
                      v2: V2Config, res: CompareResult) -> list[dict]:
    """Text re-rasterisation (core/v2/rerender.py), on every region still live.

    Called only when the renderer is proven to have changed. A region is
    taken out only when (a), (b), (c) and (e) all hold; (d) is measured and
    printed with them. The sentence in `suppressed_by` carries the numbers
    of each. A region the rule looked at and refused keeps an annotation
    saying which properties failed and by how much — a block that moved
    first, in words. Returns one record per region looked at, for the
    benchmark and the diagnosis script (kept in `res.maps`, not serialised).
    """
    live = [(r, g) for r, g in zip(regions, groups, strict=True) if not r.suppressed_by]
    if not live:
        return []
    text = _rerender.page_text_change(exp, act, cand)
    out = []
    counts = {"explained": 0}
    for r, g in live:
        box = (g.x, g.y, g.w, g.h)
        c = _rerender.crop(exp, act, labels, g.label, box, v2.group_px)
        where = np.zeros(cand.shape, bool)
        where[g.y:g.y + g.h, g.x:g.x + g.w] = labels[g.y:g.y + g.h, g.x:g.x + g.w] == g.label
        block = _rerender.block_shift(exp, act, lab_exp, lab_act, where & cand,
                                      jnd=v2.jnd_delta_e, limit=v2.shift_residual)
        a = _rerender.assess(c, text, block, ink_limit=v2.ink_delta_e,
                             share_limit=v2.min_text_share, jnd=v2.jnd_delta_e,
                             drift_px=v2.glyph_drift_px)
        out.append({"box": box, "pixels": g.pixels,
                    "holds": a.holds, "failed": a.failed,
                    "ink_delta_e": a.ink.delta_e,
                    "glyph_need_px": a.shape.need_px, "outside": a.shape.outside,
                    "paper_delta_e": a.background.delta_e,
                    "changed_away": a.background.changed_away,
                    "shift": (block.dx, block.dy), "shift_residual": block.residual,
                    "text_share": a.text.share, "mass_a": a.mass_a, "mass_b": a.mass_b})
        if a.explained:
            r.suppressed_by = f"{_rerender.RULE}: {a.sentence()} (was {r.kind.value})"
            r.kind = ChangeKind.NOISE
            r.severity = 0.0
            counts["explained"] += 1
        else:
            r.annotations.append({
                "text": f"not re-rasterised text — fails {a.failed}: {a.why_not()}",
                "kind": _rerender.RULE, "value": a.failed, "source": f"engine {ENGINE}"})
    if counts["explained"]:
        res.notes.append(
            f"Noise explained and suppressed: {counts['explained']} region(s) by text "
            f"re-rasterisation ({text.text()[4:]}). Each names the properties it "
            "passed and their numbers.")
    return out


def _region(g: _base.Group, gray_exp, gray_act, de, cand, total, cfg) -> DiffRegion:
    """A group as a `DiffRegion`: kind and severity from the v1 classifier.

    MOVED detection is off here: a move is an explanation of a region, and
    in v2 explanations are separate rules that say what they measured, not a
    side effect of classification.
    """
    return _cls.classify_region(
        (g.x, g.y, g.w, g.h), g.pixels, g.fill,
        gray_exp=gray_exp, gray_act=gray_act, de_map=de, mask=cand,
        total_pixels=total, detect_moved=False,
        above_fold_px=cfg.above_fold_px, above_fold_weight=cfg.above_fold_weight)


def _apply_threshold(res: CompareResult, cfg: DiffConfig) -> None:
    """A threshold a person set: what no rule explained, below it, is listed apart.

    Without one (`DiffConfig.v2_threshold` is 0 — a preset's `fail_severity`
    is v1's) nothing happens here: every region no rule explained fails. With
    one, a region whose severity is below it moves to `below_threshold`,
    keeps its description, and does not fail the check on its own — the
    share of the frame they cover together still can (`_verdict`).
    """
    threshold = cfg.v2_threshold
    if threshold <= 0:
        return
    below = [r for r in res.regions if r.severity < threshold]
    res.regions = [r for r in res.regions if r.severity >= threshold]
    res.below_threshold = below
    res.threshold = {"value": threshold, "source": cfg.threshold_source,
                     "regions": len(below),
                     "pixels": int(sum(r.pixel_count for r in below)),
                     "area_limit": cfg.v2_area_limit,
                     "area_source": cfg.v2_area_source}


def _account(res: CompareResult) -> None:
    """Membership by group, not by box: every candidate pixel is in one region
    — one that counts, one explained, or one below a threshold a person set."""
    below = int(sum(r.pixel_count for r in res.below_threshold))
    res.region_pixels = int(sum(r.pixel_count for r in res.regions))
    res.suppressed_pixels = int(sum(r.pixel_count for r in res.suppressed))
    res.unassigned_pixels = int(res.changed_pixels - res.region_pixels
                                - res.suppressed_pixels - below)
    res.region_area_pct = 100.0 * res.region_pixels / max(res.total_pixels, 1)


def _verdict(res: CompareResult, cfg: DiffConfig) -> Verdict:
    reasons = []
    if cfg.fail_on_size_change and res.size_changed:
        dw = abs(res.size_actual[0] - res.size_expected[0])
        dh = abs(res.size_actual[1] - res.size_expected[1])
        if max(dw, dh) > cfg.size_tolerance_px:
            reasons.append(f"size changed by {dw}×{dh}px")
    threshold = res.threshold
    if res.regions:
        reasons.append(f"{len(res.regions)} region(s) no rule explained "
                       f"({res.region_pixels} px)"
                       + (f", at or above the threshold {threshold['value']:g} "
                          f"({threshold['source']})" if threshold else ""))
    elif res.below_threshold:
        #  As in v1: a share of the frame that is enough on its own, whatever
        #  the severity. Only what no rule explained counts; under the default
        #  threshold of 0 every such region fails anyway, so this adds nothing.
        #  The limit is the one a person set, or the default whatever the
        #  preset (`DiffConfig.v2_area_limit`), named with where it came from.
        area = 100.0 * threshold["pixels"] / max(res.total_pixels, 1)
        if area >= threshold["area_limit"]:
            reasons.append(
                f"{len(res.below_threshold)} region(s) below the threshold "
                f"{threshold['value']:g} ({threshold['source']}) cover {area:.2f}% "
                f"of the frame together, at least the area limit "
                f"{threshold['area_limit']:.2f}% ({threshold['area_source']}) — "
                f"{_engines.AREA_HINT}")
            res.regions, res.below_threshold = res.below_threshold, []
            threshold.update(regions=0, pixels=0, area_pct=round(area, 4))
            _account(res)
    line = _engines.below_line(res)
    if line:
        res.notes.append(line)
    if reasons:
        res.notes.append("Reason for the failure: " + "; ".join(reasons))
        return Verdict.FAIL
    return Verdict.PASS
