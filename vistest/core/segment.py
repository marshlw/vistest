# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Segmentation of the change mask into regions.

`findContours` from the original version was replaced with `connectedComponentsWithStats`:
it immediately returns the area of the **mask** (not bbox), centroid, and label of
each pixel. This is critical: a filter "bbox area < 1200" simultaneously passes
a long thin noise strip (huge bbox, few pixels) and discards a changed 24×24 icon.
"""

from __future__ import annotations

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None


def clean_mask(
    mask: np.ndarray,
    *,
    open_px: int = 2,
    close_px: int = 6,
) -> np.ndarray:
    """open (remove salt) → close (merge letters into word/block)."""
    if cv2 is None:
        return mask
    m = (mask.astype(np.uint8)) * 255
    if open_px > 0:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (open_px * 2 + 1,) * 2)
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, k)
    if close_px > 0:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (close_px * 2 + 1,) * 2)
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, k)
    return m > 0


def components(
    mask: np.ndarray,
    *,
    min_pixels: int = 24,
    min_fill: float = 0.06,
    max_regions: int = 200,
):
    """-> (labels, [(x, y, w, h, pixel_count, fill_ratio, label_id), ...])

    Sorted by mask pixel count, descending.
    """
    if cv2 is None:
        return np.zeros(mask.shape, np.int32), []

    m = mask.astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)

    out = []
    for i in range(1, count):
        x, y, w, h, px = (
            int(stats[i, cv2.CC_STAT_LEFT]),
            int(stats[i, cv2.CC_STAT_TOP]),
            int(stats[i, cv2.CC_STAT_WIDTH]),
            int(stats[i, cv2.CC_STAT_HEIGHT]),
            int(stats[i, cv2.CC_STAT_AREA]),
        )
        if px < min_pixels:
            continue
        fill = px / float(max(w * h, 1))
        if fill < min_fill:
            continue
        out.append((x, y, w, h, px, fill, i))

    out.sort(key=lambda r: -r[4])
    return labels, out[:max_regions]


def merge_close_boxes(boxes, gap: int = 12):
    """Collapses nearby boxes: 20 separate letters → one line.

    Without this, the report becomes a cloud of hundreds of boxes,
    incomprehensible.
    """
    if not boxes:
        return []

    items = [list(b) for b in boxes]
    changed = True
    while changed:
        changed = False
        merged: list[list] = []
        used = [False] * len(items)
        for i, a in enumerate(items):
            if used[i]:
                continue
            ax, ay, aw, ah = a[0], a[1], a[2], a[3]
            for j in range(i + 1, len(items)):
                if used[j]:
                    continue
                b = items[j]
                bx, by, bw, bh = b[0], b[1], b[2], b[3]
                if (ax - gap < bx + bw and bx - gap < ax + aw
                        and ay - gap < by + bh and by - gap < ay + ah):
                    nx, ny = min(ax, bx), min(ay, by)
                    nx2, ny2 = max(ax + aw, bx + bw), max(ay + ah, by + bh)
                    ax, ay, aw, ah = nx, ny, nx2 - nx, ny2 - ny
                    a[4] += b[4]
                    used[j] = True
                    changed = True
            a[0], a[1], a[2], a[3] = ax, ay, aw, ah
            a[5] = a[4] / float(max(aw * ah, 1))
            used[i] = True
            merged.append(a)
        items = merged
    return [tuple(x) for x in items]
