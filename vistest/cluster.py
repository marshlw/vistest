# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Grouping of identical differences within a run.

The problem this was written for. Someone changed the line spacing in the
header, and twenty snapshots turned red. There is one cause, but a person
reviews twenty diffs in a row, each time figuring out anew that it is the same
thing. By the fifth one they stop looking and start approving; by the tenth
they approve without looking. That is exactly how visual testing stops working:
not from a bad engine, but from the volume of monotonous work.

The solution: before showing anything to a person, collapse regions that are
similar to each other into one group. Twenty snapshots turn into a single
question, «the header shifted by 1px, is that expected?», and a single answer
for all of them.

What counts as «the same thing». The features are chosen so that a match on
them means a shared cause rather than accidental similarity:

* **change class** — a shift and a disappearance cannot be one cause;
* **shift vector** for shifts — blocks that moved the same distance in the
  same direction almost certainly moved because of the same thing;
* **selector without indices** — `li:nth-of-type(3)` and `li:nth-of-type(7)`
  are the same list, not two different places;
* **region size** with rounding — identical elements produce identical bboxes.

Coordinates are deliberately excluded from the features: one cause shows up in
different places on the page, and grouping by coordinates is exactly what will
not catch it.

The module does not decide anything for the person and does not suppress
anything. It only rearranges the work into an order in which it can actually be
done.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Indices in a CSS path are what distinguish neighboring elements of one list.
# For finding a shared cause they are noise: `li:nth-of-type(3)` and
# `li:nth-of-type(7)` live in the same container and break together.
_INDEX_RE = re.compile(r":nth-(?:of-type|child)\(\d+\)")
_TAIL_RE = re.compile(r"\[\d+\]")

# Size rounding: elements of one kind rarely match by bbox down to the pixel,
# but they fall into the same bucket.
SIZE_BUCKET = 16
# Shifts differ by a pixel because of subpixel render rounding.
SHIFT_TOLERANCE = 2


@dataclass
class Cluster:
    """One cause that showed up in several places."""

    kind: str
    key: tuple
    regions: list[dict] = field(default_factory=list)
    snapshots: list[str] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.regions)

    @property
    def max_severity(self) -> float:
        return max((r.get("severity") or 0 for r in self.regions), default=0.0)

    @property
    def shift(self) -> tuple[int, int]:
        for r in self.regions:
            dx, dy = r.get("moved_dx") or 0, r.get("moved_dy") or 0
            if dx or dy:
                return int(dx), int(dy)
        return 0, 0

    def selectors(self, limit: int = 5) -> list[str]:
        seen: list[str] = []
        for r in self.regions:
            sel = r.get("selector")
            if sel and sel not in seen:
                seen.append(sel)
            if len(seen) >= limit:
                break
        return seen

    def common_ancestor(self) -> str:
        """The deepest common ancestor of the affected elements.

        It is the answer to the question «where to fix»: if twenty regions
        live under one `header`, the fix goes into `header`, not into twenty
        elements.
        """
        paths = [str(r.get("selector") or "").split(" > ")
                 for r in self.regions if r.get("selector")]
        if not paths:
            return ""
        common: list[str] = []
        for parts in zip(*paths, strict=False):
            if len(set(parts)) == 1:
                common.append(parts[0])
            else:
                break
        return " > ".join(common)

    def describe(self) -> str:
        """A single phrase explaining the group to a person."""
        where = self.common_ancestor() or (self.selectors(1) or [""])[0]
        where_txt = f" inside {where}" if where else ""
        n = self.size
        places = "place" if n == 1 else "places"

        if self.kind == "moved":
            dx, dy = self.shift
            direction = _direction(dx, dy)
            return (f"{n} {places} shifted by {abs(dx) + abs(dy)}px "
                    f"{direction}{where_txt}")
        if self.kind == "color":
            return f"{n} {places} changed color{where_txt}"
        if self.kind == "text":
            return f"{n} {places} with changed text{where_txt}"
        if self.kind == "removed":
            return f"{n} {places} disappeared{where_txt}"
        if self.kind == "added":
            return f"{n} {places} appeared{where_txt}"
        return f"{n} {places} changed{where_txt}"

    def advice(self) -> str:
        """What to do about it. Kept apart from the description: different questions."""
        if self.size == 1:
            return "A single difference. Review it as usual."
        if self.kind == "moved":
            dx, dy = self.shift
            if abs(dx) + abs(dy) <= 2:
                return ("The same 1-2px shift in many places is almost always "
                        "one cause higher up the page: a font loaded, line "
                        "spacing changed, or a container margin changed. Look "
                        "for that, not for twenty separate breakages.")
            return ("The same shift in different places means a shared "
                    "container changed size or margin. Check the common "
                    "ancestor.")
        if self.kind == "text":
            return ("A lot of changed text usually means live data in the "
                    "snapshot: dates, counters, names. They should be masked, "
                    "not approved every run.")
        if self.kind == "color":
            return ("The same color change in many places is an edit to a "
                    "theme variable. One answer for the whole group.")
        if self.kind == "removed":
            return ("Disappearances are the most expensive class. Make sure it "
                    "is intentional before accepting.")
        return "Check the common ancestor: looks like a single cause."


# --------------------------------------------------------------------------- #
def _direction(dx: int, dy: int) -> str:
    parts = []
    if dy:
        parts.append("down" if dy > 0 else "up")
    if dx:
        parts.append("right" if dx > 0 else "left")
    return " and ".join(parts) or "in place"


def normalize_selector(selector: str | None) -> str:
    if not selector:
        return ""
    cleaned = _INDEX_RE.sub("", str(selector))
    return _TAIL_RE.sub("", cleaned).strip()


def _key(region: dict) -> tuple:
    kind = str(region.get("kind") or "content")
    sel = normalize_selector(region.get("selector"))

    if kind in ("moved", "resized"):
        dx = round((region.get("moved_dx") or 0) / SHIFT_TOLERANCE)
        dy = round((region.get("moved_dy") or 0) / SHIFT_TOLERANCE)
        return (kind, sel, dx, dy)

    w = (region.get("w") or 0) // SIZE_BUCKET
    h = (region.get("h") or 0) // SIZE_BUCKET
    return (kind, sel, w, h)


def cluster_regions(comparisons: list[dict], *,
                    min_size: int = 2) -> list[Cluster]:
    """Run comparisons to groups of differences with a shared cause.

    `min_size` is the number of matches at which a group counts as a group. One
    would mean «show everything as is», which the ordinary table already does.
    """
    buckets: dict[tuple, Cluster] = {}

    for comp in comparisons:
        name = str(comp.get("name") or comp.get("snapshot_name") or "")
        for region in comp.get("regions") or []:
            key = _key(region)
            cluster = buckets.get(key)
            if cluster is None:
                cluster = Cluster(kind=str(region.get("kind") or "content"),
                                  key=key)
                buckets[key] = cluster
            cluster.regions.append(region)
            if name and name not in cluster.snapshots:
                cluster.snapshots.append(name)

    clusters = [c for c in buckets.values() if c.size >= min_size]
    # The largest first: they give the biggest reduction in work. At equal
    # size, the most severe first.
    clusters.sort(key=lambda c: (-len(c.snapshots), -c.size, -c.max_severity))
    return clusters


def summarize(comparisons: list[dict], *, min_size: int = 2) -> dict:
    """A ready-made summary for the interface and the report."""
    clusters = cluster_regions(comparisons, min_size=min_size)
    total_regions = sum(len(c.get("regions") or []) for c in comparisons)
    grouped = sum(c.size for c in clusters)

    return {
        "clusters": [
            {
                "kind": c.kind,
                "regions": c.size,
                "snapshots": c.snapshots,
                "snapshot_count": len(c.snapshots),
                "max_severity": round(c.max_severity, 1),
                "shift": list(c.shift),
                "selectors": c.selectors(),
                "common": c.common_ancestor(),
                "description": c.describe(),
                "advice": c.advice(),
            }
            for c in clusters
        ],
        "total_regions": total_regions,
        "grouped_regions": grouped,
        "questions": len(clusters) + (total_regions - grouped),
        "saved": max(0, grouped - len(clusters)),
    }
