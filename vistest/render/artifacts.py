# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Visualization of differences.

Requirement: differences must be *visible*, not printed as a number in a log.
Therefore a set of complementary views is generated — each one
answers its own question:

  boxes.png        — WHAT changed (boxes + class + severity)
  heatmap.png      — HOW strongly (ΔE00 heatmap)
  side_by_side.png — before / after / boxes in one frame for the report
  onion.png        — precise alignment: red = only in expected,
                     cyan = only in actual (1px shifts become visible)
  blink.gif        — the most sensitive way to spot tiny diffs
  regions/*.png    — close-up of each region: before | after | ΔE
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ..config import RenderConfig
from ..models import ChangeKind, CompareResult
from .palette import kind_color, severity_band, severity_color

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

_FONT = 0  # cv2.FONT_HERSHEY_SIMPLEX


def render_all(
    res: CompareResult,
    out_dir: str | Path,
    *,
    cfg: RenderConfig | None = None,
) -> dict[str, str]:
    """Generate artifacts. Returns {name: path}."""
    cfg = cfg or RenderConfig()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    exp = res.artifacts.get("_expected")
    act = res.artifacts.get("_aligned_actual")
    de_map = res.artifacts.get("_de_map")
    mask = res.artifacts.get("_mask")
    if exp is None or act is None:
        return {}

    paths: dict[str, str] = {}

    # Each artifact is produced IN ISOLATION: if one view (for example a close-up
    # of an exotic region) fails, the rest — the slider, the overlay, the heatmap,
    # «before/after» — are still saved. Previously any failure took down the WHOLE
    # render, and the snapshot was left with no images at all («Artifact unavailable»).
    def _try(key, fn):
        try:
            r = fn()
            if r:
                paths[key] = r
        except Exception:  # noqa: BLE001 — the artifact is decorative, we swallow this deliberately
            pass

    boxes_img = draw_boxes(act, res.regions, label=cfg.label_regions)
    if cfg.boxes:
        _try("boxes", lambda: _save(out / "boxes.png", boxes_img, cfg.max_artifact_width))

    if cfg.heatmap and de_map is not None:
        _try("heatmap", lambda: _save(
            out / "heatmap.png",
            draw_heatmap(act, de_map, max_de=cfg.heatmap_max_de),
            cfg.max_artifact_width))

    if cfg.onion_skin and mask is not None:
        _try("onion", lambda: _save(
            out / "onion.png", draw_onion(exp, act), cfg.max_artifact_width))

    if cfg.side_by_side:
        _try("side_by_side", lambda: _save(
            out / "side_by_side.png",
            draw_triptych(exp, act, boxes_img),
            cfg.max_artifact_width * 2))

    if cfg.blink_gif:
        p = out / "blink.gif"
        _try("blink", lambda: str(p) if write_blink_gif(
            p, exp, act, frame_ms=cfg.blink_frame_ms,
            max_width=min(cfg.max_artifact_width, 1200)) else None)

    if res.regions and de_map is not None:
        rdir = out / "regions"
        rdir.mkdir(exist_ok=True)
        for i, r in enumerate(sorted(res.regions, key=lambda x: -x.severity)[:12]):
            # The number is written onto the region itself, not only into the
            # artifact name. The interface used to pair `region_3.png` with the
            # fourth row of the region table, assuming both were ordered the
            # same way — but the table comes back from SQL `ORDER BY severity
            # DESC`, which does not agree with this sort on ties, and only the
            # first twelve regions get a picture at all. So a close-up could
            # end up captioned with a different element's selector: quiet, and
            # worse than no caption, because a person reads it and believes it.
            r.region_index = i
            p = rdir / f"{i:02d}_{r.kind.value}.png"
            _try(f"region_{i}", lambda p=p, r=r: str(p) if write_region_closeup(
                p, exp, act, de_map, r, max_de=cfg.heatmap_max_de) else None)

    # The slider in the UI shows exactly expected/actual — we save them at the very
    # end and also in isolation, so the basic «before/after» are always present.
    _try("expected", lambda: _save(out / "expected.png", exp, cfg.max_artifact_width))
    _try("actual", lambda: _save(out / "actual.png", act, cfg.max_artifact_width))
    return paths


# --------------------------------------------------------------------------- #
#  Individual views
# --------------------------------------------------------------------------- #
def draw_boxes(base_rgb, regions, *, label: bool = True) -> np.ndarray:
    """Box + semi-transparent fill + label with class and severity.

    The background is dimmed to 55% brightness so the boxes are readable on any
    page — on a light theme a thin colored box would otherwise get lost.
    """
    img = base_rgb.copy()
    if cv2 is None:
        return img

    dim = (img.astype(np.float32) * 0.55 + 255 * 0.45 * 0.25).astype(np.uint8)
    out = dim
    overlay = out.copy()

    for r in regions:
        c = kind_color(r.kind)
        cv2.rectangle(overlay, (r.x, r.y), (r.x + r.w, r.y + r.h), c, -1)
    cv2.addWeighted(overlay, 0.18, out, 0.82, 0, out)

    for r in regions:
        c = kind_color(r.kind)
        sc = severity_color(r.severity)
        thickness = 2 if r.severity < 50 else 3
        cv2.rectangle(out, (r.x, r.y), (r.x + r.w, r.y + r.h), c, thickness)
        # Restore the original brightness inside the region: we need to see
        # WHAT exactly changed, not only where.
        out[r.y:r.y + r.h, r.x:r.x + r.w] = base_rgb[r.y:r.y + r.h, r.x:r.x + r.w]
        cv2.rectangle(out, (r.x, r.y), (r.x + r.w, r.y + r.h), c, thickness)

        if not label:
            continue
        text = f"{r.kind.value.upper()} {r.severity:.0f}"
        if r.kind is ChangeKind.MOVED:
            text += f" ({r.moved_dx:+d},{r.moved_dy:+d})"
        _label(out, text, r.x, r.y, sc)

        if r.selector:
            _label(out, r.selector[:52], r.x, r.y + r.h + 20, (60, 60, 60), scale=0.4)

    return out


def draw_heatmap(base_rgb, de_map, *, max_de: float = 25.0) -> np.ndarray:
    """ΔE00 in color over a desaturated actual."""
    if cv2 is None:
        return base_rgb.copy()

    norm = np.clip(de_map / max(max_de, 1e-6), 0.0, 1.0)
    heat_bgr = cv2.applyColorMap((norm * 255).astype(np.uint8), cv2.COLORMAP_INFERNO)
    heat = cv2.cvtColor(heat_bgr, cv2.COLOR_BGR2RGB)

    gray = cv2.cvtColor(base_rgb, cv2.COLOR_RGB2GRAY)
    backdrop = cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB)
    backdrop = (backdrop.astype(np.float32) * 0.45).astype(np.uint8)

    alpha = norm[..., None].astype(np.float32)
    out = backdrop.astype(np.float32) * (1 - alpha) + heat.astype(np.float32) * alpha
    out = out.astype(np.uint8)
    _legend(out, max_de)
    return out


def draw_onion(exp_rgb, act_rgb) -> np.ndarray:
    """Red = was only in expected, cyan = only in actual.

    The most precise way to see a 1-pixel shift: matching areas
    stay gray, any discrepancy is colored immediately.
    """
    if cv2 is None:
        return act_rgb.copy()
    a = cv2.cvtColor(exp_rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
    b = cv2.cvtColor(act_rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
    out = np.stack([a, b, b], axis=-1)  # R=expected, G=B=actual
    return np.clip(out, 0, 255).astype(np.uint8)


def draw_triptych(exp_rgb, act_rgb, boxes_rgb) -> np.ndarray:
    """expected | actual | boxes, with headers and separators."""
    if cv2 is None:
        return np.hstack([exp_rgb, act_rgb, boxes_rgb])

    header = 34
    gap = 8
    panels = [("EXPECTED", exp_rgb), ("ACTUAL", act_rgb), ("DIFF", boxes_rgb)]
    h = max(p.shape[0] for _, p in panels)
    w = sum(p.shape[1] for _, p in panels) + gap * (len(panels) - 1)

    canvas = np.full((h + header, w, 3), 24, dtype=np.uint8)
    x = 0
    for title, p in panels:
        canvas[header:header + p.shape[0], x:x + p.shape[1]] = p
        cv2.putText(canvas, title, (x + 10, 23), _FONT, 0.6, (240, 240, 240), 1, cv2.LINE_AA)
        x += p.shape[1] + gap
    return canvas


def write_blink_gif(path, exp_rgb, act_rgb, *, frame_ms=600, max_width=1200) -> bool:
    """Blinking expected↔actual animation — the eye catches differences instantly."""
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover
        return False

    frames = []
    for img, tag in ((exp_rgb, "EXPECTED"), (act_rgb, "ACTUAL")):
        small = _downscale(img, max_width)
        if cv2 is not None:
            small = small.copy()
            cv2.rectangle(small, (0, 0), (128, 22), (20, 20, 20), -1)
            cv2.putText(small, tag, (6, 16), _FONT, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
        frames.append(Image.fromarray(small))

    frames[0].save(
        str(path), save_all=True, append_images=frames[1:],
        duration=frame_ms, loop=0, optimize=True,
    )
    return True


def write_region_closeup(path, exp_rgb, act_rgb, de_map, region, *, max_de=25.0) -> bool:
    """Close-up of a single region: before | after | ΔE, with padding for context."""
    if cv2 is None:
        return False

    pad = max(16, min(region.w, region.h) // 2)
    h, w = exp_rgb.shape[:2]
    x0, y0 = max(0, region.x - pad), max(0, region.y - pad)
    x1, y1 = min(w, region.x + region.w + pad), min(h, region.y + region.h + pad)
    if x1 <= x0 or y1 <= y0:
        return False

    ce = exp_rgb[y0:y1, x0:x1]
    ca = act_rgb[y0:y1, x0:x1]
    cd = draw_heatmap(ca, de_map[y0:y1, x0:x1], max_de=max_de)

    scale = max(1, min(4, 320 // max(ce.shape[1], 1)))
    if scale > 1:
        interp = cv2.INTER_NEAREST
        ce = cv2.resize(ce, None, fx=scale, fy=scale, interpolation=interp)
        ca = cv2.resize(ca, None, fx=scale, fy=scale, interpolation=interp)
        cd = cv2.resize(cd, None, fx=scale, fy=scale, interpolation=interp)

    tri = draw_triptych(ce, ca, cd)
    caption = (f"{region.kind.value} sev={region.severity:.0f} "
               f"({severity_band(region.severity)}) dE={region.de_mean:.1f} "
               f"ssim={region.ssim_local:.3f}")
    strip = np.full((26, tri.shape[1], 3), 24, dtype=np.uint8)
    cv2.putText(strip, caption, (8, 18), _FONT, 0.45, (235, 235, 235), 1, cv2.LINE_AA)
    cv2.imwrite(str(path), cv2.cvtColor(np.vstack([tri, strip]), cv2.COLOR_RGB2BGR))
    return True


# --------------------------------------------------------------------------- #
#  helpers
# --------------------------------------------------------------------------- #
def _label(img, text, x, y, color, scale: float = 0.45):
    (tw, th), _ = cv2.getTextSize(text, _FONT, scale, 1)
    ly = y - 6 if y - th - 10 >= 0 else y + th + 12
    lx = min(max(0, x), img.shape[1] - tw - 10)
    cv2.rectangle(img, (lx, ly - th - 6), (lx + tw + 10, ly + 5), color, -1)
    cv2.putText(img, text, (lx + 5, ly), _FONT, scale, (255, 255, 255), 1, cv2.LINE_AA)


def _legend(img, max_de: float):
    w, h = 220, 14
    x, y = img.shape[1] - w - 16, 16
    # A fixed-size legend bar (220×14) is inserted into the top-right corner.
    # On region close-ups the image can be a couple dozen pixels tall — then the
    # slice img[16:30] is cropped to fewer rows, and inserting the bar would crash
    # the render with a broadcast error «(14,220,3) into (9,220,3)». We draw the
    # legend ONLY when it actually fits both in width and in height.
    if cv2 is None or img.shape[1] < 260 or img.shape[0] < y + h + 6:
        return
    ramp = np.linspace(0, 255, w, dtype=np.uint8)[None, :].repeat(h, 0)
    bar_bgr = cv2.applyColorMap(ramp, cv2.COLORMAP_INFERNO)
    img[y:y + h, x:x + w] = cv2.cvtColor(bar_bgr, cv2.COLOR_BGR2RGB)
    cv2.rectangle(img, (x, y), (x + w, y + h), (255, 255, 255), 1)
    cv2.putText(img, "dE00 0", (x - 62, y + 12), _FONT, 0.4, (255, 255, 255), 1, cv2.LINE_AA)
    cv2.putText(img, f"{max_de:.0f}+", (x + w + 6, y + 12), _FONT, 0.4,
                (255, 255, 255), 1, cv2.LINE_AA)


def _downscale(img, max_width: int):
    if cv2 is None or img.shape[1] <= max_width:
        return img
    scale = max_width / img.shape[1]
    return cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)


def _save(path: Path, img_rgb: np.ndarray, max_width: int) -> str:
    img = _downscale(img_rgb, max_width)
    if cv2 is not None:
        cv2.imwrite(str(path), cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
    else:  # pragma: no cover
        from PIL import Image
        Image.fromarray(img).save(path)
    return str(path)
