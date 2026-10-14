# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""The photos of the live corpus: drawn here, from a seed, nobody's pictures.

Smooth landscapes — a sky, hills, a sun, a little low-frequency grain — so
that a thumbnail resampled half a pixel off differs the way a real photo
does: a little everywhere, most at the edges. Stored as lossless WebP, so a
tinted copy differs from its original by the tint and nothing else.

    python scripts/live_photos.py      # writes tests/live_corpus/templates/photos/

The files are committed; this script is how they were made, and
`tests/test_live_corpus.py` checks that it still makes them byte for byte
(on the OpenCV that made them; elsewhere the check is skipped with the
version named).
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

PHOTO_DIR = REPO / "tests" / "live_corpus" / "templates" / "photos"

#: name → (width, height, seed). `h*` are the thumbnails of the history list
#: (16:9, so that `object-fit: cover` in a square crops them and
#: `object-position` has room to move); `g*` the photos of the gallery (2:1).
PHOTOS: dict[str, tuple[int, int, int]] = {
    **{f"h{i}": (256, 144, 100 + i) for i in range(1, 10)},
    **{f"g{i}": (560, 280, 200 + i) for i in range(1, 10)},
}

#: The tinted copies: (photo, median ΔE00 of the tint over the photo).
TINTS: tuple[tuple[str, int], ...] = tuple(
    (p, de) for p in ("h3", "g2") for de in (2, 4, 8))

WEBP_LOSSLESS = [cv2.IMWRITE_WEBP_QUALITY, 101]

#: The OpenCV the committed photos were made with: another one may round a
#: pixel or encode a byte otherwise, and the check is skipped there.
MADE_WITH_OPENCV = "5.0.0"


def _smooth(rng, h: int, w: int, cell: int) -> np.ndarray:
    """Low-frequency noise in [-1, 1]: a coarse grid, upscaled bicubically."""
    gh, gw = max(2, h // cell + 2), max(2, w // cell + 2)
    g = rng.uniform(-1, 1, (gh, gw)).astype(np.float32)
    return cv2.resize(g, (w, h), interpolation=cv2.INTER_CUBIC)


def _colour(rng, lo: int, hi: int) -> np.ndarray:
    return rng.integers(lo, hi, 3).astype(np.float32)


def draw(width: int, height: int, seed: int) -> np.ndarray:
    """One landscape, RGB uint8, drawn at twice the size and reduced."""
    rng = np.random.default_rng(seed)
    W, H = width * 2, height * 2
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    top, bottom = _colour(rng, 40, 200), _colour(rng, 150, 250)
    t = (yy / H)[..., None]
    img = top * (1 - t) + bottom * t

    # a sun: a soft disc
    cx, cy = rng.uniform(0.15, 0.85) * W, rng.uniform(0.1, 0.45) * H
    r = rng.uniform(0.06, 0.14) * H * 2
    d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    sun = np.clip((r - d) / (0.25 * r), 0, 1)[..., None]
    img = img * (1 - sun) + _colour(rng, 200, 256) * sun

    # hills, back to front, each a little darker
    for k in range(3):
        base = H * (0.45 + 0.15 * k + rng.uniform(-0.05, 0.05))
        f1, f2 = rng.uniform(1, 3) * np.pi / W, rng.uniform(3, 7) * np.pi / W
        p1, p2 = rng.uniform(0, 2 * np.pi, 2)
        ridge = base + H * (0.08 * np.sin(f1 * xx + p1) + 0.03 * np.sin(f2 * xx + p2))
        edge = np.clip((yy - ridge) / 3.0 + 0.5, 0, 1)[..., None]
        c_top, c_bot = _colour(rng, 20, 160), _colour(rng, 10, 120)
        s = np.clip((yy - ridge) / (H * 0.4), 0, 1)[..., None]
        hill = c_top * (1 - s) + c_bot * s
        img = img * (1 - edge) + hill * edge

    grain = _smooth(rng, H, W, 24)[..., None] * 7 + _smooth(rng, H, W, 6)[..., None] * 3
    img = np.clip(img + grain, 0, 255)
    small = cv2.resize(img, (width, height), interpolation=cv2.INTER_AREA)
    return np.clip(np.rint(small), 0, 255).astype(np.uint8)


def _lab(rgb: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(rgb.astype(np.float32) / 255, cv2.COLOR_RGB2Lab)


def _rgb(lab: np.ndarray) -> np.ndarray:
    rgb = cv2.cvtColor(lab.astype(np.float32), cv2.COLOR_Lab2RGB)
    return np.clip(np.rint(rgb * 255), 0, 255).astype(np.uint8)


def median_de(a: np.ndarray, b: np.ndarray) -> float:
    from vistest.core import color

    return float(np.median(color.delta_e_ciede2000(color.srgb_to_lab(a),
                                                   color.srgb_to_lab(b))))


def tint(rgb: np.ndarray, target: float) -> tuple[np.ndarray, float]:
    """A warm tint (+a*, +b*) whose median ΔE00 over the photo is `target`."""
    lab = _lab(rgb)
    direction = np.array([0, 0.6, 1.0], np.float32)
    lo, hi = 0.0, 40.0
    for _ in range(40):
        mid = (lo + hi) / 2
        if median_de(rgb, _rgb(lab + direction * mid)) < target:
            lo = mid
        else:
            hi = mid
    out = _rgb(lab + direction * hi)
    return out, median_de(rgb, out)


def encode(rgb: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".webp", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), WEBP_LOSSLESS)
    if not ok:
        raise RuntimeError("WebP encoding failed")
    return buf.tobytes()


def build() -> dict[str, bytes]:
    """file name → bytes, for every photo and tinted copy."""
    out: dict[str, bytes] = {}
    drawn = {k: draw(*v) for k, v in PHOTOS.items()}
    for k, rgb in drawn.items():
        out[f"{k}.webp"] = encode(rgb)
    for k, de in TINTS:
        tinted, _ = tint(drawn[k], de)
        out[f"{k}.tint{de}.webp"] = encode(tinted)
    return out


def main() -> int:
    PHOTO_DIR.mkdir(parents=True, exist_ok=True)
    files = build()
    for name, data in files.items():
        (PHOTO_DIR / name).write_bytes(data)
    total = sum(len(d) for d in files.values())
    print(f"{len(files)} photos, {total} bytes, OpenCV {cv2.__version__}: {PHOTO_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
