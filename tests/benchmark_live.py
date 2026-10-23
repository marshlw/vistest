# VisTest - visual regression testing for Python and Playwright.
# Copyright 2026 Kirill Kulagin
# SPDX-License-Identifier: Apache-2.0
#
# This file is part of VisTest, licensed under the Apache License, Version 2.0.
# You may not use it except in compliance with the License. See LICENSE for
# the terms and NOTICE for attribution and the trademark.

"""The engines on the live corpus (`scripts/live_corpus.py`, plan v13).

    python tests/benchmark.py --corpus live [--engine v1|v2|both] [--no-ai]

Every pair, every engine's verdict, and per half: false failures on NOISE,
misses on SIGNAL; DISPUTED printed, never counted. The held-out half
(`gallery`) is printed like the other: it is for measuring a choice made on
`history`, not for making one.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import live_corpus as lc  # noqa: E402

ENGINES = {"v1": ("v1",), "v2": ("v2",), "both": ("v1", "v2")}


@dataclass
class Tool:
    key: str
    title: str
    failed: dict[str, bool] = field(default_factory=dict)
    reason: dict[str, str] = field(default_factory=dict)


def run(manifest: dict, engine: str, *, ai: bool = True,
        root: Path = lc.LIVE_DIR) -> Tool:
    from vistest.ai import AIPipeline
    from vistest.config import VisTestConfig
    from vistest.core.comparator import compare
    from vistest.models import Verdict

    cfg = VisTestConfig.preset_of("balanced")
    hooks = AIPipeline(cfg.ai) if ai else None
    bases = {k: lc.read(root / t["base"]) for k, t in manifest["templates"].items()}
    canary = (root / manifest["renderer"]["file"]).read_bytes()
    canaries = {"base": canary}
    tool = Tool(key=engine, title=f"VisTest {engine}" + ("" if ai else " (no AI)"))
    for c in manifest["cases"]:
        r = compare(bases[c["template"]], lc.read(root / c["actual"]), cfg=cfg.diff,
                    name=c["name"], ai_hooks=hooks, engine=engine,
                    renderer=(canaries[c["renderer"]["expected"]],
                              canaries[c["renderer"]["actual"]]))
        tool.failed[c["name"]] = r.verdict is Verdict.FAIL
        tool.reason[c["name"]] = _reason(r)
    return tool


def _reason(r) -> str:
    """The first line a person reads, cut short: what the verdict rests on."""
    for attr in ("summary", "message", "explanation"):
        text = getattr(r, attr, None)
        if isinstance(text, str) and text.strip():
            return " ".join(text.split())[:90]
    return ""


def score(tool: Tool, cases: list[dict]) -> tuple[int, int, int, int]:
    """(false failures, NOISE pairs, misses, SIGNAL pairs)."""
    noise = [c for c in cases if c["label"] == "NOISE"]
    signal = [c for c in cases if c["label"] == "SIGNAL"]
    return (sum(tool.failed[c["name"]] for c in noise), len(noise),
            sum(not tool.failed[c["name"]] for c in signal), len(signal))


def report(manifest: dict, tools: list[Tool]) -> str:
    cases = manifest["cases"]
    spent = manifest["split"].get("spent") or {}
    L = ["Live corpus: tests/live_corpus — part of a page a fraction of a pixel "
         "off, and real changes of the same photos",
         f"Split: {', '.join(manifest['split'][lc.CALIBRATION])} = calibration; "
         f"{', '.join(manifest['split'][lc.HELD_OUT])} = held out"
         + (f"; spent: {', '.join(spent)}" if spent else ""), ""]
    for key, t in manifest["templates"].items():
        part = [c for c in cases if c["template"] == key]
        title = {lc.CALIBRATION: "calibration", lc.HELD_OUT: "held out"}[t["split"]]
        if key in spent:
            title += ", spent"
        L.append(f"{key} — {title} ({len(part)} pairs)")
        for tool in tools:
            ff, n, miss, s = score(tool, part)
            L.append(f"  {tool.title:22s} false failures {ff}/{n}   misses {miss}/{s}")
        L.append("")
    L.append("Every pair (F = fails, . = passes):")
    width = max(len(c["name"]) for c in cases)
    L.append(f"  {'pair':{width}s}  {'label':8s} " + " ".join(f"{t.key:>3s}" for t in tools))
    for c in cases:
        marks = " ".join(f"{'F' if t.failed[c['name']] else '.':>3s}" for t in tools)
        L.append(f"  {c['name']:{width}s}  {c['label']:8s} {marks}")
    return "\n".join(L) + "\n"


def main(args) -> int:
    manifest = lc.load_manifest()
    problems = lc.verify_files(manifest)
    if problems:
        print("the live corpus on disk does not match its manifest: "
              + "; ".join(problems[:5]), file=sys.stderr)
        return 2
    engines = ENGINES[getattr(args, "engine", None) or "both"]
    tools = [run(manifest, e, ai=not args.no_ai) for e in engines]
    sys.stdout.write(report(manifest, tools))
    return 0
