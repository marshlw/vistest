# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Snapshot name -> path on disk. One implementation, used by both layouts.

These rules used to live inside `vistest.storage.fs`, next to the store that
needed them. They moved out when a second layout appeared: the library mode
keeps baselines as plain files in the user's repository, the service keeps a
directory per snapshot, and the two have to agree on the mapping exactly —
that agreement is what makes `vistest baselines export` a migration path from
one to the other rather than a re-import by hand.

Nothing here touches the filesystem. `fs` re-exports the same names, so the
historical `from .fs import _safe` keeps working.
"""

from __future__ import annotations

import hashlib

__all__ = ["MAX_DEPTH", "safe_name", "safe_segment", "split_project"]

_KEEP = "-_.() "

#  How many path segments of a name survive. A name is written by a human or
#  produced by somebody else's test file, and neither is a promise of a bounded
#  depth.
MAX_DEPTH = 4


def safe_segment(part: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in _KEEP else "_" for c in part).strip()
    cleaned = cleaned.strip(".")          # `..` and hidden directories are out
    return cleaned or "snapshot"


def safe_name(name: str) -> str:
    """Snapshot name -> a relative path, without the `.png`.

    A slash in the name means a project: `shop.example/login.png` becomes
    `shop.example/login`. Every segment is sanitised on its own and `..` is
    cut out — names arrive from outside (from the interface, from somebody
    else's tests over HTTP), and none of them may lead out of the baseline
    directory.

    **About the depth.** This used to be `"/".join(parts[-4:])`: the beginning
    of a deep name was dropped in silence. Two different snapshots sharing
    their last four segments —

        billing/eu/checkout/payment/form.png
        billing/us/checkout/payment/form.png

    — landed in ONE directory and overwrote each other's baseline. A silent
    error, and the worst kind a store can have: a person sees a diff between
    the European and the American form and has no idea where it came from.

    The depth limit stayed (a path must not grow without bound), but the
    dropped beginning no longer disappears without trace: a short hash of it is
    appended to the first surviving segment. Names stay readable, and different
    names stay different.
    """
    name = str(name).replace("\\", "/").removesuffix(".png")
    parts = [safe_segment(p) for p in name.split("/") if p.strip(" .")]
    if not parts:
        return "snapshot"
    if len(parts) <= MAX_DEPTH:
        return "/".join(parts)

    dropped = "/".join(parts[:-MAX_DEPTH])
    digest = hashlib.sha1(dropped.encode("utf-8")).hexdigest()[:8]
    kept = parts[-MAX_DEPTH:]
    return "/".join([f"{kept[0]}-{digest}", *kept[1:]])


def split_project(name: str) -> tuple[str, str]:
    """`shop.example/login.png` -> ("shop.example", "login.png")."""
    text = str(name).replace("\\", "/")
    if "/" not in text:
        return "", text
    head, _, tail = text.rpartition("/")
    return head, tail
