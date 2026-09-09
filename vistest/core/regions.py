# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Ignore zones: «do not compare THIS block», not «this rectangle».

An ignore zone used to be four numbers: `{x: 340, y: 120, w: 200, h: 80}`. That
works right up to the first time the block moves — and it moves during a
redesign, which is exactly when there are most masks and being wrong is most
expensive.

It breaks twice over, and both times silently. The rectangle stays where it
was: there is a different element there now, and its changes stop being checked
— a mask placed over an avatar ends up muting a price. And the block the mask
was made for slides out from under it and starts painting the run red every
day. A person reads both of those as «VisTest is lying».

Here a zone is bound to an ELEMENT. The coordinates do not go anywhere — they
stay as the fallback, and that is the whole point of the construction:

* there is a DOM and the element was found — the mask goes where the element is
  now;
* there is no DOM (an old comparison, somebody else's set of PNGs, a call
  without a snapshot) — the coordinates work, exactly as before;
* the element is gone — the coordinates work, and this is **said out loud**,
  because a mask that quietly stopped working is indistinguishable from one
  that works.

Backwards compatibility here is not «we try not to break it» but a property of
the construction: a zone in the old format is a zone without a selector, and
the path for it is the same one it always had.

--------------------------------------------------------------------------
How an element is identified

We have no CSS engine: what we hold is the JSON snapshot from `capture/dom.py`,
where every visible node carries geometry, `selector`, `id`, `testid`, the tag
and the classes. So identification is a ladder of priorities, not «execute the
selector»:

  1. `data-testid` — the most durable thing in any markup: it is put there by
     hand and for exactly the purpose of holding on to an element;
  2. `id` — almost as durable, but more often generated;
  3. the exact `selector` — the whole path, broken by any rebuild of the tree;
  4. tag and classes — the last line, and it fires on every similar node at
     once.

The first level that gives at least one match wins: mixing `testid` with a
match by classes would mean adding accidental hits to an exact one.

There may be several matches, and that is not an error. A carousel of five
slides, a list of avatars, an ad row inside every card — all of them get
masked, and that is exactly what «do not compare blocks like this» is asked to
mean.

--------------------------------------------------------------------------
Why BOTH positions are masked

The mask is applied to the diff, and the diff lives in the coordinates of the
current frame, which is where the baseline is brought by alignment. If the
element moved, differences appear in two places: where it was on the baseline,
and where it is now. Masking only the new position leaves the trace of the old
one — a red rectangle over empty space that nothing can explain.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# An element's box is flush with its edges, and anti-aliasing on the border
# gives a difference a pixel or two outwards. A couple of pixels of slack cost
# less than a recurring «the mask is in place and the thin strip along the edge
# is still red».
PAD_PX = 2

# The identification keys, in decreasing order of durability. The order here
# IS the ladder of priorities from the file header.
IDENTITY = ("testid", "id", "selector", "cls")


@dataclass
class Resolved:
    """What a list of zones came out as."""

    boxes: list[dict] = field(default_factory=list)
    # Zones whose selector stopped finding anything. They still work by
    # coordinates, but a person has to be told: a mask that quietly stopped
    # catching its target is indistinguishable from one that works.
    lost: list[dict] = field(default_factory=list)
    # How many zones landed by element rather than by coordinates.
    by_selector: int = 0

    @property
    def notes(self) -> list[str]:
        out = []
        for z in self.lost:
            out.append(
                f"the ignore zone «{z.get('selector') or z.get('reason') or 'unnamed'}»"
                " no longer finds its element — it is holding by coordinates,"
                " which stop meaning the same thing as soon as the layout moves")
        return out


# --------------------------------------------------------------------------- #
#  Format
# --------------------------------------------------------------------------- #
def normalize(raw) -> dict:
    """One zone in canonical form. Coordinates are always required.

    Even for a zone with a selector: the element can disappear, and then the
    only thing left is the rectangle where it used to be. A zone without
    fallback coordinates would turn into nothing at that moment, and the mask
    would vanish silently.
    """
    if not isinstance(raw, dict):
        raise ValueError("a zone must be an object")
    try:
        zone = {k: int(raw[k]) for k in ("x", "y", "w", "h")}
    except (KeyError, TypeError, ValueError):
        raise ValueError("x, y, w and h are required, as integers") from None
    if zone["w"] <= 0 or zone["h"] <= 0:
        raise ValueError("a zone must have a non-zero size")
    if zone["x"] < 0 or zone["y"] < 0:
        raise ValueError("a zone starts inside the frame")

    selector = str(raw.get("selector") or "").strip()
    if selector:
        zone["selector"] = selector[:400]
    match = raw.get("match")
    if isinstance(match, dict):
        clean = {k: (str(v)[:200] if v not in (None, "") else None)
                 for k, v in match.items() if k in IDENTITY}
        clean = {k: v for k, v in clean.items() if v}
        if clean:
            zone["match"] = clean
    if raw.get("reason"):
        zone["reason"] = str(raw["reason"])[:200]
    return zone


def normalize_all(raw) -> list[dict]:
    if not isinstance(raw, list):
        raise ValueError("boxes must be a list")
    out = []
    for i, item in enumerate(raw):
        # The wording here is what a person reads in a toast, and it should
        # not be changed without a reason: it is already covered by tests and
        # has already been seen by somebody.
        if not isinstance(item, dict):
            raise ValueError(f"boxes[{i}] is not an object")
        try:
            out.append(normalize(item))
        except ValueError as e:
            raise ValueError(f"boxes[{i}]: {e}") from None
    return out


def held_by(zone: dict) -> str:
    """What holds the zone, in one word, for the interface and the reports."""
    return "element" if (zone.get("selector") or zone.get("match")) else "coordinates"


# --------------------------------------------------------------------------- #
#  Identification
# --------------------------------------------------------------------------- #
def _classes(value) -> list[str]:
    return [c for c in str(value or "").split() if c]


def _level_of(zone: dict) -> list[tuple[str, str]]:
    """The zone's identifying traits, down the ladder of priorities."""
    match = zone.get("match") or {}
    out = []
    for key in IDENTITY:
        value = match.get(key) or (zone.get("selector") if key == "selector" else None)
        if value:
            out.append((key, value))
    return out


def _node_matches(node: dict, key: str, value: str) -> bool:
    if key == "testid":
        return bool(node.get("testid")) and node["testid"] == value
    if key == "id":
        return bool(node.get("id")) and node["id"] == value
    if key == "selector":
        return node.get("selector") == value
    if key == "cls":
        # ALL of the zone's classes must be present, while extra ones on the
        # node are fine: markup adds modifiers (`is-open`, `theme-dark`), and
        # demanding a byte-for-byte match of the class string would mean losing
        # the target to a theme switch.
        want = set(_classes(value))
        return bool(want) and want <= set(_classes(node.get("cls")))
    return False


def find_nodes(dom, zone: dict) -> list[dict]:
    """The snapshot nodes the zone considers to be itself.

    The first non-empty level of the ladder is returned. Several nodes is
    normal: a carousel, a list of avatars, an ad inside every card.
    """
    nodes = (dom or {}).get("nodes") or []
    if not nodes:
        return []
    for key, value in _level_of(zone):
        hit = [n for n in nodes if _node_matches(n, key, value)]
        if hit:
            return hit
    return []


def _box_of(node: dict) -> dict | None:
    try:
        x, y = int(node["x"]), int(node["y"])
        w, h = int(node["w"]), int(node["h"])
    except (KeyError, TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    return {"x": max(0, x - PAD_PX), "y": max(0, y - PAD_PX),
            "w": w + PAD_PX * 2, "h": h + PAD_PX * 2}


# --------------------------------------------------------------------------- #
#  Resolution
# --------------------------------------------------------------------------- #
def resolve(zones, *, baseline_dom=None, actual_dom=None) -> Resolved:
    """Zones → the rectangles to mute in this comparison.

    `baseline_dom` and `actual_dom` are the snapshots of the baseline and of
    the current frame. Both are optional: without them a zone works by
    coordinates, the way it always did. Both together — because an element that
    moved produces differences in two places at once (see the file header).
    """
    out = Resolved()
    for zone in (zones or []):
        if not isinstance(zone, dict):
            continue
        fallback = {k: int(zone.get(k) or 0) for k in ("x", "y", "w", "h")}
        if held_by(zone) == "coordinates":
            out.boxes.append(fallback)
            continue

        found = []
        for dom in (actual_dom, baseline_dom):
            for node in find_nodes(dom, zone):
                box = _box_of(node)
                if box and box not in found:
                    found.append(box)

        if found:
            out.boxes.extend(found)
            out.by_selector += 1
        else:
            # The target is lost. Coordinates are not «another option» but
            # the last thing left: they describe where the element WAS. The run
            # will not go red because of it, but it has stopped being true
            # either, so the zone goes into `lost` and from there into the
            # comparison notes and onto the snapshot page.
            out.boxes.append(fallback)
            out.lost.append(zone)
    return out


def mask(shape, zones, *, baseline_dom=None, actual_dom=None):
    """A ready bool mask of the frame. `resolve` + `noise.mask_from_boxes`."""
    from . import noise as _noise

    res = resolve(zones, baseline_dom=baseline_dom, actual_dom=actual_dom)
    return _noise.mask_from_boxes(shape, res.boxes), res
