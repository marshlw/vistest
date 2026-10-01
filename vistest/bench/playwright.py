# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Playwright's own comparator on the pairs, through Node.

The same function `toHaveScreenshot()` calls and scripts/bench_playwright_grid.mjs
measures the corpus with — `getComparator('image/png')` from playwright-core —
at threshold 0.2 (Playwright's default) and 0.05, maxDiffPixels 0. It is
Playwright's code, not a port: so it needs Node and an installed Playwright.
Where to find one, in this order: `--playwright <folder>`; the input folder
and the folders above it (test-results sit inside the project that made
them); the current folder and above; the `scripts/bench` of a VisTest
checkout — each in its own node_modules — and only then one Node finds
through NODE_PATH or a global install. The output names the one used.
Without Node or Playwright the Playwright columns stay empty and
`Unavailable.how` says how to fill them.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["SETTINGS", "Playwright", "Unavailable", "run"]

HELPER = Path(__file__).with_name("playwright_compare.mjs")
#: (label, the comparator's threshold) — maxDiffPixels is 0 for both.
SETTINGS = (("0.05/0", "0.05"), ("0.2/0", "0.2"))
REPO_BENCH = Path(__file__).resolve().parents[2] / "scripts" / "bench"
TIMEOUT_S = 3600


@dataclass
class Unavailable:
    """Why the Playwright columns are empty, and the one line that fills them."""

    why: str
    how: str


@dataclass
class Playwright:
    title: str
    versions: dict
    source: str
    #: pair id -> {"0.05/0": {"failed", "pixels", "message"?}, "0.2/0": {...}}
    results: dict = field(default_factory=dict)


def _candidates(start: list[Path], explicit: Path | None) -> list[Path]:
    out: list[Path] = []
    if explicit is not None:
        return [explicit.resolve()]
    for base in start:
        base = base.resolve()
        for folder in (base, *base.parents):
            if folder not in out:
                out.append(folder)
    if REPO_BENCH.is_dir() and REPO_BENCH not in out:
        out.append(REPO_BENCH)
    return out


HOW = ("pass --playwright <a folder of a project with @playwright/test "
       "installed>, or in a VisTest checkout run: npm ci --prefix scripts/bench")


def run(pairs, *, look_from: list[Path], explicit: Path | None = None
        ) -> Playwright | Unavailable:
    """Every pair through Playwright's comparator, or why it could not be."""
    node = shutil.which("node")
    if node is None:
        return Unavailable("no node on PATH",
                           "install Node.js 18 or newer, then " + HOW)
    if explicit is not None and not explicit.is_dir():
        return Unavailable(f"--playwright {explicit}: not a folder", HOW)
    folders = _candidates(look_from, explicit)
    with tempfile.TemporaryDirectory(prefix="vistest-bench-") as tmp:
        listing = Path(tmp) / "pairs.json"
        listing.write_text(json.dumps([{"id": p.id, "expected": str(p.expected.resolve()),
                                        "actual": str(p.actual.resolve())} for p in pairs]),
                           encoding="utf-8")
        try:
            done = subprocess.run([node, str(HELPER), str(listing), *map(str, folders)],
                                  capture_output=True, text=True, timeout=TIMEOUT_S,
                                  check=False)
        except (OSError, subprocess.SubprocessError) as e:
            return Unavailable(f"node did not run ({type(e).__name__}: {e})", HOW)
    try:
        doc = json.loads(done.stdout.strip().splitlines()[-1]) if done.stdout.strip() else {}
    except ValueError:
        doc = {}
    if done.returncode != 0 or "results" not in doc:
        why = doc.get("error") or (done.stderr.strip().splitlines() or ["no answer"])[-1]
        return Unavailable(f"Playwright's comparator did not run: {why}", HOW)
    results = {pid: {label: entry[threshold] for label, threshold in SETTINGS}
               for pid, entry in doc["results"].items()}
    tool = doc.get("tool") or {}
    return Playwright(title=str(tool.get("title") or "Playwright"),
                      versions=dict(tool.get("versions") or {}),
                      source=str(tool.get("from") or ""), results=results)
