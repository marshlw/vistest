# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Orchestrator of comparison. All stages of the cascade are assembled here."""

from __future__ import annotations

import time

import numpy as np

from ..models import ChangeKind, CompareResult, DiffRegion, Verdict
from . import align as _align
from . import antialias as _aa
from . import classify as _cls
from . import color as _color
from . import segment as _seg
from . import structure as _struct
from .settings import DiffConfig


class ImageTooLarge(ValueError):
    """A frame larger than we agree to unfold in memory."""


def _check_size(image: np.ndarray, what: str, limit: int) -> None:
    """The memory guard, before the first allocation of the cascade.

    The limit arrives as an argument — `DiffConfig.max_pixels`, like every
    other engine parameter. It used to be a module-level constant read from
    the environment at import time, which made it the one piece of global
    state left in the core and meant a test could only change it by patching
    the module. Reasoning about the number itself lives with the field.
    """
    h, w = image.shape[:2]
    if limit and h * w > limit:
        raise ImageTooLarge(
            f"{what} is {w}×{h} = {h * w // 1_000_000} Mpx, and the engine "
            f"limit is {limit // 1_000_000} Mpx. Comparing it would need "
            "gigabytes of memory. Capture a smaller area, or raise "
            "VISTEST_ENGINE_MAX_PIXELS deliberately.")


def compare(
    expected_rgb: np.ndarray,
    actual_rgb: np.ndarray,
    *,
    cfg: DiffConfig | None = None,
    name: str = "snapshot",
    ignore_mask: np.ndarray | None = None,
    ai_hooks=None,
) -> CompareResult:
    """Compare two RGB images (uint8, H×W×3).

    ignore_mask: bool mask "don't look here" (user masks, stability mask,
                 persistent ignore regions from history).
    ai_hooks:    object with optional methods
                 `refine(regions, exp, act)` and
                 `attribute(regions)`. See vistest.ai.pipeline.AIPipeline.
    """
    t0 = time.perf_counter()
    cfg = cfg or DiffConfig()

    # Before the first allocation: further down the cascade a refusal is late.
    _check_size(expected_rgb, "the baseline", cfg.max_pixels)
    _check_size(actual_rgb, "the screenshot", cfg.max_pixels)

    res = CompareResult(name=name, verdict=Verdict.PASS)
    res.size_expected = (int(expected_rgb.shape[1]), int(expected_rgb.shape[0]))
    res.size_actual = (int(actual_rgb.shape[1]), int(actual_rgb.shape[0]))

    # ---------- 0. Sizes ----------
    exp, act, padded = _align.reconcile_sizes(expected_rgb, actual_rgb)
    if padded:
        res.size_changed = True
        res.notes.append(
            f"Size changed: {res.size_expected} → {res.size_actual}. "
            "The images were padded to a common size (not cropped), "
            "the difference goes into the diff."
        )
    if ignore_mask is not None and ignore_mask.shape != exp.shape[:2]:
        ignore_mask = _fit_mask(ignore_mask, exp.shape[:2])

    h, w = exp.shape[:2]
    res.total_pixels = h * w

    # ---------- 1. Perceptual lightness ----------
    lab_exp = _color.srgb_to_lab(exp)
    gray_exp = np.clip(lab_exp[:, :, 0] * 2.55, 0, 255).astype(np.uint8)
    gray_act_raw = _color.luminance(act)

    # ---------- 2. Global alignment ----------
    act_aligned, al = _align.align_images(
        exp, act, gray_exp, gray_act_raw,
        enabled=cfg.align_enabled,
        max_shift_px=cfg.max_align_shift_px,
    )
    res.align_dx, res.align_dy, res.aligned = al.dx, al.dy, al.applied
    if al.reason:
        res.notes.append(f"Alignment: {al.reason}")

    lab_act = _color.srgb_to_lab(act_aligned)
    gray_act = np.clip(lab_act[:, :, 0] * 2.55, 0, 255).astype(np.uint8)

    # ---------- 3. Perceptual and structural maps ----------
    de_map = _color.delta_e_ciede2000(lab_exp, lab_act)
    smap, ssim_global = _struct.ssim_map(gray_exp, gray_act)
    res.ssim_global = ssim_global

    color_hit = de_map > cfg.delta_e_threshold
    struct_hit = smap < cfg.ssim_threshold

    # ---------- 4. Candidates: CONSENSUS, not OR ----------
    if cfg.require_consensus:
        # Exception: very strong color difference on a flat fill doesn't change
        # structure (SSIM stays high), so we skip it past consensus — otherwise
        # a background color change would go unnoticed.
        strong_color = de_map > (cfg.delta_e_threshold * 4.0)
        candidate = (color_hit & struct_hit) | strong_color
    else:
        candidate = color_hit | struct_hit

    # ---------- 5. Known noise suppression ----------
    suppress = np.zeros((h, w), dtype=bool)
    if cfg.antialias_filter:
        suppress |= _aa.antialias_mask(gray_exp, gray_act, tolerance=cfg.aa_tolerance)
    if ignore_mask is not None:
        suppress |= ignore_mask

    mask = candidate & ~suppress

    de_masked = de_map[mask]
    res.changed_pixels = int(mask.sum())
    res.de_mean = float(de_masked.mean()) if de_masked.size else 0.0
    res.de_p95 = float(np.percentile(de_masked, 95)) if de_masked.size else 0.0
    res.changed_area_pct = 100.0 * res.changed_pixels / max(res.total_pixels, 1)

    # ---------- 6. Segmentation ----------
    cleaned = _seg.clean_mask(mask, open_px=cfg.morph_open_px, close_px=cfg.morph_close_px)
    _, boxes = _seg.components(
        cleaned,
        min_pixels=cfg.min_region_px,
        min_fill=cfg.min_region_fill,
        max_regions=cfg.max_regions,
    )
    boxes = _seg.merge_close_boxes(boxes, gap=max(cfg.morph_close_px * 2, 10))

    # ---------- 7. Classification ----------
    regions: list[DiffRegion] = []
    for (x, y, bw, bh, px, fill, _lbl) in boxes:
        regions.append(
            _cls.classify_region(
                (x, y, bw, bh), px, fill,
                gray_exp=gray_exp, gray_act=gray_act,
                de_map=de_map, mask=cleaned,
                total_pixels=res.total_pixels,
                detect_moved=cfg.detect_moved,
                move_search_px=cfg.move_search_px,
                move_match_threshold=cfg.move_match_threshold,
                above_fold_px=cfg.above_fold_px,
                above_fold_weight=cfg.above_fold_weight,
                moved_scale=cfg.moved_severity_scale,
            )
        )

    # ---------- 8. AI layer (optional) ----------
    if ai_hooks is not None and regions:
        try:
            regions = ai_hooks.refine(regions, exp, act_aligned, res)
        except Exception as e:  # AI should never fail the test
            res.notes.append(f"AI layer skipped: {type(e).__name__}: {e}")

    # ---------- 9. Separation and verdict ----------
    ignore_kinds = {ChangeKind(k) for k in cfg.ignore_kinds}
    for r in regions:
        if r.kind in ignore_kinds or r.suppressed_by:
            res.suppressed.append(r)
        else:
            res.regions.append(r)

    res.max_severity = max((r.severity for r in res.regions), default=0.0)
    res.verdict = _verdict(res, cfg)
    res.duration_ms = int((time.perf_counter() - t0) * 1000)

    # Keep maps for artifact rendering (not serialized to JSON).
    #  Into `maps`, which is typed for arrays, and not into `artifacts`, which
    #  is typed for paths and is serialised into every report.
    res.maps["de_map"] = de_map
    res.maps["mask"] = cleaned
    res.maps["aligned_actual"] = act_aligned
    res.maps["expected"] = exp
    return res


def _verdict(res: CompareResult, cfg: DiffConfig) -> Verdict:
    reasons = []
    if cfg.fail_on_size_change and res.size_changed:
        dw = abs(res.size_actual[0] - res.size_expected[0])
        dh = abs(res.size_actual[1] - res.size_expected[1])
        if max(dw, dh) > cfg.size_tolerance_px:
            reasons.append(f"size changed by {dw}×{dh}px")

    if res.max_severity >= cfg.fail_severity:
        reasons.append(f"severity {res.max_severity:.1f} ≥ {cfg.fail_severity}")

    if res.changed_area_pct >= cfg.max_changed_area_pct:
        # Area is counted before segmentation: it includes pixels that are
        # later discarded as too small. If no region passes filtering —
        # the engine recognized everything as insignificant, and failing by the
        # sum of discarded pixels means disagreeing with its own decision.
        diffuse = not res.regions
        huge = res.changed_area_pct >= cfg.area_hard_fail_pct

        if diffuse and cfg.area_requires_region and not huge:
            res.notes.append(
                f"Changed {res.changed_area_pct:.3f}% ≥ "
                f"{cfg.max_changed_area_pct}%, but not a single region passed "
                "filtering — the divergence is smeared out and insignificant, "
                "no failure. This is what a subpixel difference between "
                "snapshots from different capture pipelines looks like. "
                "Turn off with: area_requires_region=false"
            )
        else:
            reasons.append(
                f"changed {res.changed_area_pct:.3f}% ≥ "
                f"{cfg.max_changed_area_pct}%"
                + ("  (no significant regions, but the area is large)"
                   if diffuse and huge else "")
            )

    if reasons:
        res.notes.append("Reason for the failure: " + "; ".join(reasons))
        return Verdict.FAIL
    return Verdict.PASS


def _fit_mask(mask: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    """Fit the mask to image shape (crop/pad with False)."""
    out = np.zeros(shape, dtype=bool)
    hh = min(shape[0], mask.shape[0])
    ww = min(shape[1], mask.shape[1])
    out[:hh, :ww] = mask[:hh, :ww].astype(bool)
    return out


def strip_internal(res: CompareResult) -> CompareResult:
    """Let go of the full-frame maps once the pictures have been drawn.

    Not a serialisation fix any more — `maps` is not serialised, so forgetting
    this call can no longer produce a broken report. What it still buys is
    memory: four arrays the size of the screenshot, held for as long as anybody
    holds the result. In a suite of two hundred snapshots, with the failures
    kept inside exceptions, that is the difference between a run and an
    out-of-memory kill.

    The underscore-prefixed keys are still swept out of `artifacts` for results
    that came from an older version of the engine — through a pickle, a
    long-lived worker, or a caller that built one by hand.
    """
    res.maps.clear()
    for k in list(res.artifacts):
        if k.startswith("_"):
            res.artifacts.pop(k)
    return res
