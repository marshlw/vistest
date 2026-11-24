# VisTest - visual regression testing for Python and Playwright.
# Copyright 2026 Kirill Kulagin
# SPDX-License-Identifier: Apache-2.0
#
# This file is part of VisTest, licensed under the Apache License, Version 2.0.
# You may not use it except in compliance with the License. See LICENSE for
# the terms and NOTICE for attribution and the trademark.

"""Versioned pytest results for CI consumers."""

from __future__ import annotations

from pathlib import Path

from ..storage.atomic import write_json
from .library import Parts

_STATUSES = {
    ("pass", "compared"): "passed",
    ("fail", "compared"): "failed",
    ("error", "error"): "error",
    ("new_baseline", "missing"): "missing_baseline",
    ("new_baseline", "created"): "baseline_created",
    ("new_baseline", "updated"): "baseline_updated",
    ("new_baseline", "unchanged"): "baseline_unchanged",
}


def write(out: Path, parts: Parts, *, exit_code: int | None = None,
          issues: dict[str, int] | None = None, coverage: dict | None = None) -> None:
    """Write one atomic artifact, including when no snapshot rows survived."""
    counts = dict(issues or {})
    if exit_code is None:
        counts["run_not_finished"] = 1
    if parts.unreadable:
        counts["unreadable_parts"] = parts.unreadable
    if parts.collisions:
        counts["snapshot_collisions"] = len(parts.collisions)

    snapshots = []
    failed = False
    for entry in parts.entries:
        key = entry.get("key")
        name = entry.get("name")
        platform = entry.get("platform")
        verdict, action = entry.get("verdict"), entry.get("action")
        if (not all(isinstance(value, str) for value in (key, name, platform, verdict, action))
                or not key or not name or (verdict, action) not in _STATUSES):
            counts["invalid_parts"] = counts.get("invalid_parts", 0) + 1
            continue
        status = _STATUSES[verdict, action]
        if key in parts.collisions:
            status = "collision"
        if status == "error":
            counts["check_errors"] = counts.get("check_errors", 0) + 1
        failed |= status in ("failed", "missing_baseline")
        nodeid = entry.get("nodeid")
        row = {
            "key": key,
            "name": name,
            "platform": platform or None,
            "status": status,
            "tests": parts.collisions.get(key) or ([nodeid] if nodeid else []),
        }
        if entry.get("baseline_sha256"):
            row["baseline_sha256"] = entry["baseline_sha256"]
        if entry.get("suppressed_by_reason"):
            row["suppressed_by_reason"] = entry["suppressed_by_reason"]
        renderer = entry.get("renderer")
        if isinstance(renderer, dict):
            if renderer.get("status") == "changed":
                row["renderer"] = {"status": "changed", "pixels": renderer.get("pixels")}
            if renderer.get("run_sha256"):
                row.setdefault("renderer", {"status": renderer["status"]})
                row["renderer"]["run_sha256"] = renderer["run_sha256"]
        snapshots.append(row)

    if counts:
        status = "incomplete"
    elif not snapshots:
        status = "empty"
    elif failed or exit_code != 0:
        status = "failed"
    else:
        status = "passed"
    write_json(out, {
        "schema_version": 1,
        "status": status,
        "pytest_exit_code": exit_code,
        "issues": counts,
        "snapshots": sorted(snapshots, key=lambda row: row["key"]),
        **({"coverage": coverage} if coverage is not None else {}),
    })
