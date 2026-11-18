# VisTest - visual regression testing for Python and Playwright.
# Copyright 2026 Kirill Kulagin
# SPDX-License-Identifier: Apache-2.0
#
# This file is part of VisTest, licensed under the Apache License, Version 2.0.
# You may not use it except in compliance with the License. See LICENSE for
# the terms and NOTICE for attribution and the trademark.

"""Required snapshot comparisons for a CI job."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path, PureWindowsPath

from .library import Parts


def load(path: Path) -> list[tuple[str, str]]:
    rows = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(rows, list) or not rows:
        raise ValueError("expected a non-empty array of snapshot requirements")
    required = set()
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict) or row.keys() != {"name", "platform"}:
            raise ValueError(f"entry {index} must contain name and platform")
        name, platform = row["name"], row["platform"]
        if (not isinstance(name, str) or not name.strip()
                or not name.lower().endswith(".png") or "\\" in name
                or PureWindowsPath(name).drive
                or any(part in ("", ".", "..") for part in name.split("/"))):
            raise ValueError(f"entry {index} needs a relative name such as 'shop/checkout.png'")
        if platform is not None and (
            not isinstance(platform, str) or not platform.strip()
            or "/" in platform or "\\" in platform
        ):
            raise ValueError(f"entry {index} needs a platform identifier or null")
        pair = name, platform or ""
        if pair in required:
            raise ValueError(f"entry {index} duplicates {name!r} on {platform!r}")
        required.add(pair)
    return sorted(required)


def check(required: list[tuple[str, str]], parts: Parts) -> dict:
    observed = defaultdict(dict)
    for entry in parts.entries:
        name, platform = entry.get("name"), entry.get("platform")
        if isinstance(name, str) and isinstance(platform, str):
            observed[name][platform] = entry

    missing, unverified = [], []
    for name, platform in required:
        platforms = observed.get(name, {})
        entry = platforms.get(platform)
        key = entry.get("key") if entry is not None else None
        if entry is None:
            missing.append({
                "name": name, "platform": platform or None,
                "observed_platforms": [value or None for value in sorted(platforms)],
            })
        elif (not isinstance(key, str) or not key or key in parts.collisions
              or entry.get("action") != "compared"
              or entry.get("verdict") not in ("pass", "fail")):
            unverified.append({"name": name, "platform": platform or None})
    return {
        "status": "failed" if missing or unverified else "passed",
        "required": len(required),
        "missing": missing,
        "unverified": unverified,
    }
