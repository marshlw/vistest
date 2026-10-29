# VisTest - visual regression testing for Python and Playwright.
# Copyright 2026 Kirill Kulagin
# SPDX-License-Identifier: Apache-2.0
#
# This file is part of VisTest, licensed under the Apache License, Version 2.0.
# You may not use it except in compliance with the License. See LICENSE for
# the terms and NOTICE for attribution and the trademark.

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

#  From the engine's settings rather than from the loader: the library
#  mode draws diff pictures without ever reading a config file, and
#  `vistest.config` is the module that searches for one.
from ..core.settings import RenderConfig
from ..models import ChangeKind, CompareResult
from .palette import category, region_color, severity_band, severity_color

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

    exp = res.maps.get("expected")
    act = res.maps.get("aligned_actual")
    de_map = res.maps.get("de_map")
    mask = res.maps.get("mask")
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

    boxes_img = draw_boxes(act, res.regions, label=cfg.label_regions,
                           set_aside=_set_aside(res))
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


def _set_aside(res) -> list:
    """What the engine did not count: suppressed, then below the threshold."""
    return [*(getattr(res, "suppressed", None) or ()),
            *(getattr(res, "below_threshold", None) or ())]


# --------------------------------------------------------------------------- #
#  Individual views
# --------------------------------------------------------------------------- #
def draw_boxes(base_rgb, regions, *, label: bool = True,
               set_aside=()) -> np.ndarray:
    """Box + semi-transparent fill + label with class and severity.

    The background is dimmed to 55% brightness so the boxes are readable on any
    page — on a light theme a thin colored box would otherwise get lost.

    `set_aside` — the differences the engine did not count: `suppressed`
    (each with its `suppressed_by`) and `below_threshold`. They are drawn
    muted and first: a 1 px dashed neutral grey frame, no fill, no caption,
    the dimmed background left dimmed. The real regions are drawn over them,
    so where the two overlap the real one reads whole. One legend says what
    the grey frames are (`set_aside_legend`), whatever `label` is: it is the
    key to the picture, not a caption. It is not laid over the screenshot —
    it is a dark grey strip added under it (`legend_strip`), so the picture
    is taller than the screenshot by the strip, and above the strip it is,
    pixel for pixel, what it would be without the legend. Without
    `set_aside` the picture is what it always was: the screenshot's size,
    byte for byte.
    """
    img = base_rgb.copy()
    if cv2 is None:
        return img

    dim = (img.astype(np.float32) * 0.55 + 255 * 0.45 * 0.25).astype(np.uint8)
    out = dim
    overlay = out.copy()

    for r in regions:
        c = region_color(r)
        cv2.rectangle(overlay, (r.x, r.y), (r.x + r.w, r.y + r.h), c, -1)
    cv2.addWeighted(overlay, 0.18, out, 0.82, 0, out)

    legend = _draw_set_aside(out, list(set_aside)) if set_aside else ""

    for r in regions:
        c = region_color(r)
        sc = severity_color(r.severity)
        thickness = 2 if r.severity < 50 else 3
        cv2.rectangle(out, (r.x, r.y), (r.x + r.w, r.y + r.h), c, thickness)
        # Restore the original brightness inside the region: we need to see
        # WHAT exactly changed, not only where.
        out[r.y:r.y + r.h, r.x:r.x + r.w] = base_rgb[r.y:r.y + r.h, r.x:r.x + r.w]
        cv2.rectangle(out, (r.x, r.y), (r.x + r.w, r.y + r.h), c, thickness)

        if not label:
            continue
        said = category(r)
        text = f"{(said or r.kind.value).upper()} {r.severity:.0f}"
        if not said and r.kind is ChangeKind.MOVED:
            text += f" ({r.moved_dx:+d},{r.moved_dy:+d})"
        _label(out, text, r.x, r.y, sc)

        if r.selector:
            _label(out, r.selector[:52], r.x, r.y + r.h + 20, (60, 60, 60), scale=0.4)

    if legend:
        #  Under the screenshot, never over it: a corner of a small element
        #  is half of it, and the legend there covered what was to be seen.
        out = np.vstack([out, legend_strip(out.shape[1], legend)])
    return out


#: At most this many set-aside differences are drawn, the largest first; the
#: legend says how many of how many.
SET_ASIDE_DRAWN = 40
#: Neutral grey (R = G = B), lighter than anything the dimmed background can be
#: (at most 255 * 0.55 + 28 ≈ 168), so it shows on a light page and a dark one,
#: and never reads as one of the kind colors of a real change.
SET_ASIDE_GREY = (225, 225, 225)
#: The legend strip under the picture: dark grey, not a color.
_LEGEND_BACK = (40, 40, 40)
#: At most this many reasons are named in the legend; the rest are "+N other".
LEGEND_GROUPS = 3
_DASH, _GAP = 4, 3

#: The short word for a reason on the picture, by the name the engine files it
#: under. The one place these words live; a reason not here is shown as it is.
SET_ASIDE_WORD = {
    "page-shift": "subpixel shift",
    "min-size": "too small",
    "rerender": "re-render noise",
    "rerender-text": "re-render noise",
    "antialias": "antialiasing",
    "below threshold": "below threshold",
}


def set_aside_reason(region) -> str:
    """'antialias', 'page-shift', 'min-size'… — or 'below threshold'.

    The part of `suppressed_by` before its colon: the reason without the
    details, which are in the message and the report. A region with no
    `suppressed_by` is one a threshold a person set let through.
    """
    said = getattr(region, "suppressed_by", None) or ""
    return said.split(":", 1)[0].strip() or "below threshold"


def set_aside_word(region) -> str:
    """The reason in the legend's words: 'subpixel shift', 'too small'…"""
    reason = set_aside_reason(region)
    return SET_ASIDE_WORD.get(reason, reason)


def set_aside_legend(set_aside, drawn: int | None = None) -> str:
    """'set aside: 35 subpixel shift; 2 too small' — the key to the grey frames.

    One group per reason, the most frequent first, at most `LEGEND_GROUPS`,
    the rest '+N other'. When fewer frames were drawn than were set aside,
    '(40 of 45 drawn)' at the end. `drawn` defaults to what `draw_boxes`
    draws: at most `SET_ASIDE_DRAWN`.
    """
    regions = list(set_aside)
    if not regions:
        return ""
    counts: dict[str, int] = {}
    for r in regions:
        word = set_aside_word(r)
        counts[word] = counts.get(word, 0) + 1
    #  Most frequent first; on a tie, the one met first (dicts keep order).
    order = sorted(counts.items(), key=lambda kv: -kv[1])
    parts = [f"{n} {word}" for word, n in order[:LEGEND_GROUPS]]
    other = sum(n for _, n in order[LEGEND_GROUPS:])
    if other:
        parts.append(f"+{other} other")
    text = "set aside: " + "; ".join(parts)
    total = len(regions)
    shown = min(total, SET_ASIDE_DRAWN) if drawn is None else drawn
    if shown < total:
        text += f" ({shown} of {total} drawn)"
    return text


def _draw_set_aside(out, regions) -> str:
    """The muted frames of what the engine set aside, in place; returns their
    legend, which `draw_boxes` puts under the picture. See `draw_boxes`. No
    caption at a frame: one legend says them all."""
    h, w = out.shape[:2]
    regions = sorted(regions, key=lambda r: -int(getattr(r, "pixel_count", 0) or 0))
    drawn = regions[:SET_ASIDE_DRAWN]
    for r in drawn:
        x0, y0 = max(0, int(r.x)), max(0, int(r.y))
        x1, y1 = min(w - 1, int(r.x + r.w)), min(h - 1, int(r.y + r.h))
        if x1 < x0 or y1 < y0:
            continue
        _dashed_rect(out, x0, y0, x1, y1, SET_ASIDE_GREY)
    return set_aside_legend(regions, len(drawn))


_LEGEND_SCALE = 0.4
_SAMPLE_W = 18          # the dashed sample before the text
_PAD = 5                # above the first line, under the last, around the sample
_MARGIN = 6             # from the picture's left and right edges
_LINE_GAP = 4           # between the lines of a legend that wrapped
#: As tall as a line of the legend can be: a capital, a descender, a bracket.
_LINE_PROBE = "Ag(|)"


def _text_x() -> int:
    return _MARGIN + _SAMPLE_W + _PAD


def legend_lines(text: str, width: int) -> list[str]:
    """The legend broken into the lines of a strip `width` px wide.

    It breaks after each group ('35 subpixel shift;') and before
    '(40 of 45 drawn)', never inside a group while a group fits; a group
    wider than the line alone is broken between its words. The font is the
    legend's own, never smaller. The lines joined with one space are the
    text.
    """
    if not text:
        return []
    room = max(1, width - _text_x() - _MARGIN)

    def wide(s: str) -> int:
        return cv2.getTextSize(s, _FONT, _LEGEND_SCALE, 1)[0][0]

    head, sep, tail = text.rpartition(" (")
    if not (sep and tail.endswith(" drawn)")):
        head, sep, tail = text, "", ""
    groups = head.split("; ")
    pieces = [g + ";" for g in groups[:-1]] + [groups[-1]]
    if sep:
        pieces.append("(" + tail)
    words: list[str] = []
    for piece in pieces:
        words.extend([piece] if wide(piece) <= room else piece.split(" "))

    lines: list[str] = []
    for word in words:
        if lines and wide(lines[-1] + " " + word) <= room:
            lines[-1] += " " + word
        else:
            lines.append(word)
    return lines


def legend_strip(width: int, text: str) -> np.ndarray:
    """The legend as a strip `width` px wide to go under the picture: dark
    grey, the dashed sample on the left, the text after it, in as many lines
    as it takes (`legend_lines`). Only neutral pixels: R = G = B."""
    lines = legend_lines(text, width)
    (_, th), base = cv2.getTextSize(_LINE_PROBE, _FONT, _LEGEND_SCALE, 1)
    lh = th + base
    height = 2 * _PAD + len(lines) * lh + (len(lines) - 1) * _LINE_GAP
    strip = np.empty((height, width, 3), np.uint8)
    strip[:] = _LEGEND_BACK
    #  The sample stands by the first line, as it stood by the one line.
    mid = _PAD + lh // 2
    sx1 = min(width - 1, _MARGIN + _SAMPLE_W - 1)
    if sx1 > _MARGIN:
        _dashed_rect(strip, _MARGIN, mid - 4, sx1, mid + 4, SET_ASIDE_GREY)
    for i, line in enumerate(lines):
        ty = _PAD + th + i * (lh + _LINE_GAP)
        cv2.putText(strip, line, (_text_x(), ty), _FONT, _LEGEND_SCALE,
                    SET_ASIDE_GREY, 1, cv2.LINE_AA)
    return strip


def _dashed_rect(img, x0, y0, x1, y1, color) -> None:
    """A 1 px dashed rectangle with corners at (x0, y0) and (x1, y1), inclusive."""
    period = _DASH + _GAP

    def on(n: int) -> np.ndarray:
        return (np.arange(n) % period) < _DASH

    xs = np.arange(x0, x1 + 1)[on(x1 - x0 + 1)]
    ys = np.arange(y0, y1 + 1)[on(y1 - y0 + 1)]
    img[y0, xs] = color
    img[y1, xs] = color
    img[ys, x0] = color
    img[ys, x1] = color


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
