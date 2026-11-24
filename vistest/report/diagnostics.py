# VisTest - visual regression testing for Python and Playwright.
# Copyright 2026 Kirill Kulagin
# SPDX-License-Identifier: Apache-2.0
#
# This file is part of VisTest, licensed under the Apache License, Version 2.0.
# You may not use it except in compliance with the License. See LICENSE for
# the terms and NOTICE for attribution and the trademark.

"""Summarize comparable observations from a series of CI reports."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

_COMPARED = {"passed", "failed"}
_RUN_STATUSES = _COMPARED | {"incomplete", "empty"}


def _read(path: Path) -> dict:
    try:
        report = json.loads(path.read_text(encoding="utf-8-sig"))
    except ValueError as exc:
        raise ValueError(f"{path}: invalid report JSON ({exc})") from exc
    if (not isinstance(report, dict) or type(report.get("schema_version")) is not int
            or report["schema_version"] != 1):
        raise ValueError(f"{path}: expected a VisTest CI report with schema_version 1")
    status = report.get("status")
    if (not isinstance(status, str) or status not in _RUN_STATUSES
            or not isinstance(report.get("snapshots"), list)):
        raise ValueError(f"{path}: invalid run status or snapshots")
    return report


def summarize(paths: list[Path]) -> dict:
    if len(paths) < 2:
        raise ValueError("provide at least two CI reports")
    if len({path.resolve() for path in paths}) != len(paths):
        raise ValueError("provide a different file for each CI run")

    reports = []
    snapshots = {}
    for number, path in enumerate(paths, 1):
        report = _read(path)
        summary = {"path": str(path), "status": report["status"], "comparisons": 0}
        reports.append(summary)
        if report["status"] not in _COMPARED:
            continue

        seen = set()
        for row in report["snapshots"]:
            if not isinstance(row, dict) or not isinstance(row.get("status"), str):
                raise ValueError(f"{path}: invalid snapshot row")
            if row["status"] not in _COMPARED:
                continue
            key, platform, baseline = (row.get("key"), row.get("platform"),
                                       row.get("baseline_sha256"))
            if (not isinstance(key, str) or not key
                    or (platform is not None and not isinstance(platform, str))
                    or (baseline is not None and not isinstance(baseline, str))):
                raise ValueError(f"{path}: invalid snapshot identity")
            if (key, platform) in seen:
                raise ValueError(f"{path}: duplicate snapshot {key!r} on {platform!r}")
            seen.add((key, platform))

            reasons = row.get("suppressed_by_reason", {})
            renderer = row.get("renderer", {})
            if (not isinstance(reasons, dict)
                    or any(type(count) is not int or count < 0 for count in reasons.values())
                    or not isinstance(renderer, dict)):
                raise ValueError(f"{path}: invalid diagnostic fields for {key!r}")
            profile = renderer.get("run_sha256")
            if profile is not None and not isinstance(profile, str):
                raise ValueError(f"{path}: invalid renderer fingerprint for {key!r}")

            group = snapshots.setdefault((key, platform, baseline), {
                "key": key, "platform": platform, "baseline_sha256": baseline,
                "passed_in": [], "failed_in": [], "suppressed_by_reason": Counter(),
                "renderer_profiles": Counter(), "renderer_changed_in": [],
            })
            group[f'{row["status"]}_in'].append(number)
            group["suppressed_by_reason"].update(reasons)
            if profile:
                group["renderer_profiles"][profile] += 1
            if renderer.get("status") == "changed":
                group["renderer_changed_in"].append(number)
            summary["comparisons"] += 1

    rows = [snapshots[key] for key in sorted(
        snapshots, key=lambda key: (key[0], key[1] or "", key[2] or "")
    )]
    for row in rows:
        row["mixed_verdicts"] = bool(row["passed_in"] and row["failed_in"])
    return {"schema_version": 1, "reports": reports, "snapshots": rows}


def format_summary(data: dict) -> str:
    reports = data["reports"]
    rows = data["snapshots"]
    lines = [
        f'{i}. {run["path"]} ({run["status"]}, {run["comparisons"]} comparisons)'
        for i, run in enumerate(reports, 1)
    ]
    mixed = sum(row["mixed_verdicts"] for row in rows)
    lines.append(f"{mixed} snapshot/baseline groups with mixed verdicts out of {len(rows)}")
    for row in rows:
        reasons = row["suppressed_by_reason"]
        profiles = row["renderer_profiles"]
        changed = row["renderer_changed_in"]
        if not (row["mixed_verdicts"] or reasons or len(profiles) > 1 or changed):
            continue
        baseline = row["baseline_sha256"]
        head = f'{row["key"]} [{row["platform"] or "flat"}, baseline '
        lines.append(head + (baseline[:12] if baseline else "unrecorded") + "]")
        for field, label in (("passed_in", "passed"), ("failed_in", "failed"),
                             ("renderer_changed_in", "renderer differs from baseline")):
            if row[field]:
                lines.append(f"  {label} in reports " + ", ".join(map(str, row[field])))
        if reasons:
            lines.append("  suppressed regions: " + ", ".join(
                f"{reason} ({count})" for reason, count in sorted(reasons.items())
            ))
        if profiles:
            lines.append("  observed renderer fingerprints: " + ", ".join(
                f"{sha[:12]} ({count})" for sha, count in sorted(profiles.items())
            ))
    return "\n".join(lines)
