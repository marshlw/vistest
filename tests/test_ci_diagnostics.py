# VisTest - visual regression testing for Python and Playwright.
# Copyright 2026 Kirill Kulagin
# SPDX-License-Identifier: Apache-2.0
#
# This file is part of VisTest, licensed under the Apache License, Version 2.0.
# You may not use it except in compliance with the License. See LICENSE for
# the terms and NOTICE for attribution and the trademark.

from __future__ import annotations

import json

from vistest import cli
from vistest.report import ci
from vistest.report.library import Parts


def test_diagnose_reports_mixed_verdicts_per_platform_and_baseline(tmp_path, capsys):
    checkout = {
        "key": "desktop/shop/checkout", "name": "shop/checkout.png", "platform": "desktop",
        "action": "compared", "baseline_sha256": "a" * 64,
    }
    first, second, third, partial = [tmp_path / f"run-{i}.json" for i in range(1, 5)]
    ci.write(first, Parts(entries=[
        {**checkout, "verdict": "pass", "suppressed_by_reason": {"subpixel shift": 2},
         "renderer": {"status": "same", "run_sha256": "e" * 64}},
        {**checkout, "key": "mobile/shop/checkout", "platform": "mobile", "verdict": "pass"},
    ]), exit_code=0)
    ci.write(second, Parts(entries=[
        {**checkout, "verdict": "fail", "suppressed_by_reason": {"subpixel shift": 1},
         "renderer": {"status": "changed", "pixels": 12, "run_sha256": "f" * 64}},
    ]), exit_code=1)
    ci.write(third, Parts(entries=[
        {**checkout, "verdict": "pass", "baseline_sha256": "b" * 64},
    ]), exit_code=0)
    ci.write(partial, Parts(entries=[{**checkout, "verdict": "fail"}]),
             exit_code=1, issues={"worker_errors": 1})

    command = ["diagnose", *map(str, (first, second, third, partial))]
    assert cli.main([*command, "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert [r["comparisons"] for r in data["reports"]] == [2, 1, 1, 0]
    mixed = [r for r in data["snapshots"] if r["mixed_verdicts"]]
    assert len(mixed) == 1
    assert mixed[0] == {
        "key": "desktop/shop/checkout", "platform": "desktop", "baseline_sha256": "a" * 64,
        "passed_in": [1], "failed_in": [2], "mixed_verdicts": True,
        "suppressed_by_reason": {"subpixel shift": 3},
        "renderer_profiles": {"e" * 64: 1, "f" * 64: 1},
        "renderer_changed_in": [2],
    }
    assert {r["baseline_sha256"] for r in data["snapshots"]} == {"a" * 64, "b" * 64}
    assert cli.main(command) == 0
    output = capsys.readouterr().out
    assert "1 snapshot/baseline groups with mixed verdicts out of 3" in output
    assert "renderer differs from baseline in reports 2" in output
    assert "subpixel shift (3)" in output


def test_diagnose_reads_v1_reports_without_optional_identity_fields(tmp_path, capsys):
    paths = [tmp_path / f"legacy-{status}.json" for status in ("passed", "failed")]
    for path, status in zip(paths, ("passed", "failed"), strict=True):
        path.write_text(json.dumps({
            "schema_version": 1, "status": status,
            "snapshots": [{"key": "checkout", "status": status, "platform": None}],
        }), encoding="utf-8")
    assert cli.main(["diagnose", *map(str, paths)]) == 0
    assert "checkout [flat, baseline unrecorded]" in capsys.readouterr().out


def test_diagnose_rejects_an_unknown_schema(tmp_path, capsys):
    bad = tmp_path / "unsupported.json"
    bad.write_text('{"schema_version": 2}', encoding="utf-8")
    assert cli.main(["diagnose", str(bad), str(tmp_path / "unread.json")]) == 2
    assert "unsupported.json: expected a VisTest CI report" in capsys.readouterr().err
