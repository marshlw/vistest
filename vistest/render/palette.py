# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""The colors of the kinds of change, RGB."""

from __future__ import annotations

from ..models import ChangeKind

KIND_COLOR: dict[ChangeKind, tuple[int, int, int]] = {
    ChangeKind.ADDED:     (46, 204, 113),   # зелёный  — появилось
    ChangeKind.REMOVED:   (231, 76, 60),    # красный  — исчезло
    ChangeKind.MOVED:     (52, 152, 219),   # синий    — переехало
    ChangeKind.RESIZED:   (155, 89, 182),   # фиолет   — изменился размер
    ChangeKind.COLOR:     (241, 196, 15),   # жёлтый   — только цвет
    ChangeKind.TEXT:      (230, 126, 34),   # оранж    — текст
    ChangeKind.CONTENT:   (233, 30, 99),    # малиновый— содержимое
    ChangeKind.NOISE:     (149, 165, 166),  # серый    — отфильтровано
    ChangeKind.ANTIALIAS: (149, 165, 166),
}

SEVERITY_BANDS = [
    (75.0, (192, 28, 28), "critical"),
    (50.0, (222, 100, 20), "major"),
    (25.0, (222, 178, 20), "minor"),
    (0.0,  (120, 144, 156), "trivial"),
]


def severity_color(sev: float) -> tuple[int, int, int]:
    for threshold, color, _ in SEVERITY_BANDS:
        if sev >= threshold:
            return color
    return SEVERITY_BANDS[-1][1]


def severity_band(sev: float) -> str:
    for threshold, _, label in SEVERITY_BANDS:
        if sev >= threshold:
            return label
    return "trivial"


def kind_color(kind: ChangeKind) -> tuple[int, int, int]:
    return KIND_COLOR.get(kind, (233, 30, 99))


#: The colors of what the engine said a change is (core/v2/describe.py), in
#: the palette above: a box is colored and labelled by the word its row in
#: the report and its line in the message use (review v1, 4.5).
CATEGORY_COLOR: dict[str, tuple[int, int, int]] = {
    "moved": KIND_COLOR[ChangeKind.MOVED],
    "line": KIND_COLOR[ChangeKind.ADDED],
    "fill": KIND_COLOR[ChangeKind.COLOR],
    "color": KIND_COLOR[ChangeKind.COLOR],
    "redrawn": KIND_COLOR[ChangeKind.TEXT],
    "shape": KIND_COLOR[ChangeKind.CONTENT],
}


def category(region) -> str:
    """The engine's word for the change, or '' when it said none (v1)."""
    for a in getattr(region, "annotations", None) or ():
        if isinstance(a, dict) and a.get("kind") == "description" and a.get("value"):
            return str(a["value"])
    return ""


def region_color(region) -> tuple[int, int, int]:
    said = category(region)
    return CATEGORY_COLOR.get(said) or kind_color(region.kind)
