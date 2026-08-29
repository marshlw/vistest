# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Instability profile of a snapshot.

The core idea of the whole framework: **noise must be measured, not guessed**.

Instead of picking thresholds «by eye» and manually writing out
mask_selectors, we capture the page N times in a row. Pixels that changed
between snapshots of one and the same page cannot be a regression by
definition — they are a clock, a spinner, a cursor, a canvas, a random avatar, a
random ad block. Those are what we mask.

The mask accumulates between runs (sticky): instability can be rare,
and jitter seen once is remembered forever.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None


def stability_mask(
    shots: list[np.ndarray],
    *,
    threshold: int = 6,
    dilate_px: int = 3,
) -> np.ndarray:
    """bool mask of unstable pixels across a series of snapshots of one page.

    threshold is in brightness units: below it is sensor/codec noise, we ignore.
    dilate_px expands the mask: an animation edge jitters wider than its core.
    """
    if len(shots) < 2:
        return np.zeros(shots[0].shape[:2] if shots else (1, 1), dtype=bool)

    base = shots[0]
    h, w = base.shape[:2]
    unstable = np.zeros((h, w), dtype=bool)

    for other in shots[1:]:
        if other.shape[:2] != (h, w):
            # A different height between snapshots is itself a sign of a live page.
            hh, ww = min(h, other.shape[0]), min(w, other.shape[1])
            diff = np.abs(
                base[:hh, :ww].astype(np.int16) - other[:hh, :ww].astype(np.int16)
            ).max(axis=2)
            unstable[:hh, :ww] |= diff > threshold
            unstable[hh:, :] = True
            unstable[:, ww:] = True
        else:
            diff = np.abs(base.astype(np.int16) - other.astype(np.int16)).max(axis=2)
            unstable |= diff > threshold

    if dilate_px > 0 and cv2 is not None:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilate_px * 2 + 1,) * 2)
        unstable = cv2.dilate(unstable.astype(np.uint8), k).astype(bool)
    return unstable


def merge_masks(old: np.ndarray | None, new: np.ndarray, sticky: bool = True) -> np.ndarray:
    """Accumulation of the profile between runs."""
    if old is None:
        return new
    if old.shape != new.shape:
        h = min(old.shape[0], new.shape[0])
        w = min(old.shape[1], new.shape[1])
        out = np.zeros(new.shape, dtype=bool)
        out[:h, :w] = old[:h, :w]
        old = out
    return (old | new) if sticky else new


def instability_score(mask: np.ndarray) -> float:
    """Share of unstable pixels — a snapshot health metric."""
    return float(mask.sum()) / float(max(mask.size, 1))


def save_mask(path: str | Path, mask: np.ndarray) -> None:
    """PNG 1 bit per pixel (after compression — a few KB even for full-page)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if cv2 is not None:
        cv2.imwrite(str(path), (mask.astype(np.uint8) * 255))
    else:  # pragma: no cover
        from PIL import Image
        Image.fromarray(mask.astype(np.uint8) * 255).save(path)


def load_mask(path: str | Path) -> np.ndarray | None:
    path = Path(path)
    if not path.exists():
        return None
    if cv2 is not None:
        img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    else:  # pragma: no cover
        from PIL import Image
        img = np.array(Image.open(path).convert("L"))
    return None if img is None else img > 127


def mask_from_boxes(shape: tuple[int, int], boxes) -> np.ndarray:
    """Rectangular ignore regions (from the UI «ignore this area») into a mask."""
    m = np.zeros(shape, dtype=bool)
    h, w = shape
    for b in boxes:
        x, y, bw, bh = (int(b["x"]), int(b["y"]), int(b["w"]), int(b["h"])) \
            if isinstance(b, dict) else (int(b[0]), int(b[1]), int(b[2]), int(b[3]))
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(w, x + bw), min(h, y + bh)
        if x1 > x0 and y1 > y0:
            m[y0:y1, x0:x1] = True
    return m
