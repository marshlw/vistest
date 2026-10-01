# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""`vistest bench <folder>` — the engine on your own failures.

Our corpus was written by us: simple templates, one browser. This is the
engine on somebody else's pairs. They carry no labels, so nothing here is a
score: every pair goes through engine v2 and through Playwright's own
comparator (threshold 0.2 and 0.05, maxDiffPixels 0), and the pairs on which
they disagree are collected for a person to look at — in the terminal, in an
HTML report on the library report's code, and in `labels.csv`, one row per
disagreement with an empty `label`. Filled in (SIGNAL, NOISE or unsure) and
handed back with `--labels`, it gives each tool's false failures and misses
on the labelled pairs, counted the way the benchmark counts them.

v2 runs as the benchmark runs it: its defaults (no threshold, no preset), the
AI layer as `AIPipeline(AIConfig())` unless `--no-ai`, and the canaries of
the two renderers when the pair has them (bench/layouts.py). A pair it fails
on which the page's move by a fraction of a pixel is proven is flagged: it is
where the known limit of f5 shows (docs/GUIDE.md, «What engine v2 does not
tell apart»).

**Nothing leaves the machine.** The pictures are read where they are; nothing
is copied into a repository; everything written goes to `--out`
(`./vistest-bench-out/` by default). The output without time in it is the
same for the same input, to the byte.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from pathlib import Path

from . import layouts
from . import playwright as _pw

__all__ = ["PairResult", "count_labels", "main", "measure", "read_labels"]

DEFAULT_OUT = Path("vistest-bench-out")
REGIONS_SAID = 3
LABELS = ("SIGNAL", "NOISE", "unsure")
LABEL_FIELDS = ("pair", "v2", "playwright 0.05/0", "playwright 0.2/0", "v2 says", "label")
TOOLS = ("v2", "Playwright 0.05/0", "Playwright 0.2/0")


@dataclass
class PairResult:
    pair: layouts.Pair
    v2_failed: bool
    regions: int
    #: Up to three regions, in the engine's words, «WxH at (x, y): words».
    said: list[str]
    renderer: str
    #: The page's move by a fraction of a pixel, when the rule proved it: (dx, dy).
    page_shift: tuple[float, float] | None
    size_note: str = ""
    #: label ("0.05/0", "0.2/0") -> {"failed", "pixels", "message"?}; empty
    #: when Playwright did not run.
    playwright: dict = field(default_factory=dict)
    entry: dict = field(default_factory=dict)

    @property
    def id(self) -> str:
        return self.pair.id

    @property
    def shift_flag(self) -> bool:
        return self.v2_failed and self.page_shift is not None

    def failed(self, tool: str) -> bool | None:
        if tool == "v2":
            return self.v2_failed
        got = self.playwright.get(tool.split()[-1])
        return None if got is None else bool(got["failed"])

    def disagrees(self) -> bool:
        return any(self.failed(t) is not None and self.failed(t) != self.v2_failed
                   for t in TOOLS[1:])


# --------------------------------------------------------------------------- #
#  v2, pair by pair
# --------------------------------------------------------------------------- #
def _words(region) -> str:
    from ..core.engines import region_words

    where = f"{region.w}x{region.h} at ({region.x}, {region.y})"
    words = region_words(region)
    return where if words == where else f"{where}: {words}"


def measure(found: layouts.Found, out: Path, *, ai: bool = True,
            unreadable: list[str] | None = None) -> list[PairResult]:
    """Every pair through v2, as the benchmark runs it. Diff pictures go to `out/diff/`."""
    from ..ai import AIPipeline
    from ..config import VisTestConfig
    from ..core import pngio
    from ..core.comparator import compare, strip_internal
    from ..library import _region_row
    from ..render.artifacts import draw_boxes

    cfg = VisTestConfig.preset_of("balanced", engine="v2")
    hooks = AIPipeline(cfg.ai) if ai else None
    results: list[PairResult] = []
    for pair in found.pairs:
        try:
            exp = pngio.read(pair.expected)
            act = pngio.read(pair.actual)
        except Exception as e:  # a file that is not a PNG this can read
            if unreadable is not None:
                unreadable.append(f"{pair.id}: not a picture this can read "
                                  f"({type(e).__name__}: {str(e).splitlines()[0]})")
            continue
        canaries = (pair.canary_expected.read_bytes() if pair.canary_expected else None,
                    pair.canary_actual.read_bytes() if pair.canary_actual else None)
        renderer = canaries if any(c is not None for c in canaries) else None
        r = compare(exp, act, cfg=cfg.diff, name=pair.id, ai_hooks=hooks, renderer=renderer)
        rend = r.maps.get("renderer")
        line = rend.line() if rend is not None else "renderer: unknown"
        if rend is not None and rend.status == "unknown" and pair.canary_note:
            line = f"renderer: unknown — {pair.canary_note}"
        shifted = r.maps.get("v2_page_shift") or {}
        shift = ((float(shifted["dx"]), float(shifted["dy"]))
                 if shifted.get("holds") else None)
        size_note = ""
        if exp.shape[:2] != act.shape[:2]:
            size_note = (f"{pair.id}: the baseline is {exp.shape[1]}x{exp.shape[0]}, the "
                         f"screenshot {act.shape[1]}x{act.shape[0]} — compared as they are")
        failed = r.failed
        images = {"baseline": str(pair.expected), "actual": str(pair.actual)}
        if failed:
            diff = out / "diff" / f"{pair.id}.png"
            diff.parent.mkdir(parents=True, exist_ok=True)
            diff.write_bytes(pngio.encode(draw_boxes(act, r.regions)))
            images["diff"] = str(diff)
        entry = {
            "key": pair.id, "name": pair.id, "verdict": "fail" if failed else "pass",
            "images": images, "limits": {"engine": "v2"},
            "renderer": {"line": line},
            "metrics": {"max_severity": round(float(r.max_severity), 2),
                        "changed_area_pct": round(float(r.changed_area_pct), 4),
                        "region_area_pct": round(float(r.region_area_pct), 4),
                        "ssim_global": round(float(r.ssim_global), 6)},
            "regions": [_region_row(g) for g in list(r.regions)[:12]],
            "suppressed_count": len(r.suppressed),
            "suppressed": [_region_row(g) for g in list(r.suppressed)[:12]],
        }
        results.append(PairResult(pair, failed, len(r.regions),
                                  [_words(g) for g in r.regions[:REGIONS_SAID]],
                                  line, shift, size_note, entry=entry))
        strip_internal(r)
    return results


# --------------------------------------------------------------------------- #
#  Words
# --------------------------------------------------------------------------- #
def _verdict(failed: bool | None) -> str:
    return "—" if failed is None else ("fail" if failed else "pass")


def v2_says(res: PairResult) -> str:
    if not res.v2_failed:
        return "pass"
    n = res.regions
    head = f"fail — {n} region{'s' if n != 1 else ''}"
    more = f"; and {n - len(res.said)} more" if n > len(res.said) else ""
    return f"{head}: " + "; ".join(res.said) + more if res.said else head


def playwright_says(res: PairResult) -> str:
    if not res.playwright:
        return "Playwright —"
    parts = []
    for label, _ in _pw.SETTINGS:
        got = res.playwright[label]
        px = (f"{got['pixels']} px" if got.get("pixels") is not None
              else got.get("message", "no count"))
        parts.append(f"{label} {_verdict(got['failed'])} ({px})")
    return "Playwright " + ", ".join(parts)


def _shift_words(res: PairResult) -> str:
    dx, dy = res.page_shift
    return f"the page moved by ({dx:+g}, {dy:+g}) px, proven"


def disagreement_line(res: PairResult) -> str:
    line = f"{res.id} · v2 {v2_says(res)} · {res.renderer} · {playwright_says(res)}"
    if res.shift_flag:
        line += f" · [{_shift_words(res)}]"
    return line


def summary(found: layouts.Found, results: list[PairResult], pw, out: Path,
            *, input_said: str, unreadable: list[str]) -> list[str]:
    L = [f"vistest bench — {input_said}",
         f"Nothing was sent anywhere: the pictures were read where they are, and "
         f"what this wrote is in {out.as_posix()}/.",
         f"layout: {found.layout}; {len(results)} pair{'s' if len(results) != 1 else ''}",
         "v2: its defaults (no threshold, no preset), as the benchmark runs it"]
    if isinstance(pw, _pw.Playwright):
        L.append(f"Playwright: {pw.title} (" + ", ".join(
            f"{k} {v}" for k, v in sorted(pw.versions.items())) + f"; from {pw.source}), "
            "threshold 0.05 and 0.2, maxDiffPixels 0 — getComparator('image/png'), "
            "as toHaveScreenshot()")
    else:
        L.append(f"Playwright: the columns are empty — {pw.why}. To fill them: {pw.how}.")
    for line in found.unrecognised + unreadable:
        L.append(f"not recognised: {line}")
    for line in found.skipped:
        L.append(f"skipped: {line}")
    for res in results:
        if res.size_note:
            L.append(f"sizes differ: {res.size_note}")
    L.append("")
    v2_failed = sum(r.v2_failed for r in results)
    L.append(f"pairs: {len(results)} — v2 failed {v2_failed}, "
             f"passed {len(results) - v2_failed}")
    if isinstance(pw, _pw.Playwright):
        agree = sum(not r.disagrees() for r in results)
        L.append(f"all three agree (v2, Playwright 0.05/0, Playwright 0.2/0): {agree}")
        L.append(f"disagreements: {len(results) - agree} pair"
                 f"{'s' if len(results) - agree != 1 else ''}")
        for tool in TOOLS[1:]:
            a = sum(r.v2_failed and r.failed(tool) is False for r in results)
            b = sum(not r.v2_failed and r.failed(tool) is True for r in results)
            L.append(f"  v2 failed, {tool} passed: {a}")
            L.append(f"  v2 passed, {tool} failed: {b}")
    flagged = [r for r in results if r.shift_flag]
    L.append(f"v2 failed where the page's move by a fraction of a pixel is proven: "
             f"{len(flagged)} pair{'s' if len(flagged) != 1 else ''}")
    L += [f"  {r.id} · {_shift_words(r)} · v2 {v2_says(r)}" for r in flagged]
    shown = [r for r in results if r.disagrees()] if isinstance(pw, _pw.Playwright) \
        else [r for r in results if r.v2_failed]
    if shown:
        L.append("")
        L.append("Disagreements:" if isinstance(pw, _pw.Playwright)
                 else "v2 failed (no Playwright to disagree with):")
        L += [f"  {disagreement_line(r)}" for r in shown]
    return L


# --------------------------------------------------------------------------- #
#  labels.csv
# --------------------------------------------------------------------------- #
def labels_csv(rows: list[PairResult], known: dict[str, str] | None = None) -> str:
    """One row per pair to label; a label already given (`known`) is kept."""
    known = known or {}
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(LABEL_FIELDS)
    for r in rows:
        w.writerow([r.id, _verdict(r.v2_failed), _verdict(r.failed(TOOLS[1])),
                    _verdict(r.failed(TOOLS[2])), v2_says(r), known.get(r.id, "")])
    return buf.getvalue()


def read_labels(path: Path) -> tuple[dict[str, str], list[str]]:
    """pair -> SIGNAL / NOISE / unsure, and a line for each row that is not one."""
    out, said = {}, []
    with open(path, encoding="utf-8", newline="") as f:
        for n, row in enumerate(csv.DictReader(f), start=2):
            pid = (row.get("pair") or "").strip()
            raw = (row.get("label") or "").strip()
            if not pid or not raw:
                continue
            label = {"signal": "SIGNAL", "noise": "NOISE", "unsure": "unsure",
                     "disputed": "unsure"}.get(raw.lower())
            if label is None:
                said.append(f"{path.name} line {n}: label {raw!r} is not SIGNAL, NOISE "
                            "or unsure — not counted")
                continue
            out[pid] = label
    return out, said


def count_labels(results: list[PairResult], labels: dict[str, str], *,
                 playwright: bool) -> list[str]:
    """False failures (NOISE failed) and misses (SIGNAL passed) per tool, on the
    labelled pairs — as the benchmark counts; unsure is printed, not counted."""
    by_id = {r.id: r for r in results}
    missing = sorted(pid for pid in labels if pid not in by_id)
    labelled = [(by_id[pid], lab) for pid, lab in sorted(labels.items()) if pid in by_id]
    n = {lab: sum(1 for _, x in labelled if x == lab) for lab in LABELS}
    L = [f"labelled pairs: {len(labelled)} — SIGNAL {n['SIGNAL']}, NOISE {n['NOISE']}, "
         f"unsure {n['unsure']} (printed, not counted)"]
    if missing:
        shown = ", ".join(missing[:5]) + (f" and {len(missing) - 5} more" if len(missing) > 5
                                          else "")
        L.append(f"labelled but not in this input: {len(missing)} — {shown}")
    w = max(len(t) for t in TOOLS) + 2
    L.append(f"{'tool':{w}s} {'false (NOISE failed)':>22s} {'misses (SIGNAL passed)':>24s} "
             f"{'unsure failed':>14s}")
    for tool in TOOLS if playwright else TOOLS[:1]:
        false = sum(1 for r, lab in labelled if lab == "NOISE" and r.failed(tool))
        miss = sum(1 for r, lab in labelled if lab == "SIGNAL" and r.failed(tool) is False)
        unsure = sum(1 for r, lab in labelled if lab == "unsure" and r.failed(tool))
        cells = (f"{false}/{n['NOISE']}", f"{miss}/{n['SIGNAL']}", f"{unsure}/{n['unsure']}")
        L.append(f"{tool:{w}s} {cells[0]:>22s} {cells[1]:>24s} {cells[2]:>14s}")
    return L


# --------------------------------------------------------------------------- #
#  The report
# --------------------------------------------------------------------------- #
def report_html(results: list[PairResult], lines: list[str], *, playwright: bool) -> str:
    from ..report.library import render

    first = [r for r in results if (r.disagrees() if playwright else r.v2_failed)]
    opened = {r.id for r in first}
    rest = [r for r in results if r.id not in opened]
    entries = []
    for r in first + rest:
        e = dict(r.entry)
        tag = f"v2 {_verdict(r.v2_failed)}" + (
            "".join(f" · Playwright {label} {_verdict(r.failed('Playwright ' + label))}"
                    for label, _ in _pw.SETTINGS) if playwright else "")
        e["platform"] = tag
        e["open"] = r.id in opened
        #  Where the tools agree the row stays shut and the pictures stay on
        #  disk: the page is for the disagreements.
        e["collapsed"] = r.id not in opened
        e["reason"] = (("disagreement — " if r.id in opened and playwright else "")
                       + f"v2 {v2_says(r)}; {playwright_says(r)}"
                       + (f"; {_shift_words(r)}" if r.shift_flag else "")
                       + ("" if r.id in opened else
                          f"; pictures on disk: {r.pair.expected} · {r.pair.actual}"))
        entries.append(e)
    #  Under the title: what was read and how, the counts, and the labelled
    #  count when there is one — not the list of disagreements, which is
    #  the rows themselves.
    intro = [line for line in lines if line and not line.startswith("  ")
             and line not in ("Disagreements:", "v2 failed (no Playwright to disagree with):")]
    return render(entries, title="VisTest bench", intro=intro, stamp="")


# --------------------------------------------------------------------------- #
def main(args) -> int:
    """`vistest bench` — called by vistest/cli.py."""
    import sys

    try:
        found = layouts.discover(args.folder, expected=args.expected, actual=args.actual,
                                 baselines=args.baselines)
    except layouts.LayoutError as e:
        print(f"vistest bench: {e}", file=sys.stderr)
        return 2
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    unreadable: list[str] = []
    results = measure(found, out, ai=not args.no_ai, unreadable=unreadable)
    if args.no_playwright:
        pw = _pw.Unavailable("--no-playwright", "leave out --no-playwright")
    else:
        start = [Path(p) for p in (args.folder, args.expected, args.actual) if p]
        pw = _pw.run([r.pair for r in results], look_from=[*start, Path.cwd()],
                     explicit=Path(args.playwright) if args.playwright else None)
    if isinstance(pw, _pw.Playwright):
        for r in results:
            r.playwright = pw.results.get(r.id, {})
    input_said = (f"--expected {args.expected} --actual {args.actual}"
                  if args.expected else str(args.folder))
    lines = summary(found, results, pw, out, input_said=input_said, unreadable=unreadable)
    have_pw = isinstance(pw, _pw.Playwright)
    to_label = [r for r in results if (r.disagrees() if have_pw else r.v2_failed)]
    #  A label a person gave is never lost: the labels.csv already in --out
    #  and the --labels file are read first and carried into the new one.
    known: dict[str, str] = {}
    if (out / "labels.csv").is_file():
        known.update(read_labels(out / "labels.csv")[0])
    if args.labels:
        labels, said = read_labels(Path(args.labels))
        known.update(labels)
        lines += ["", f"Labels from {args.labels}:", *said,
                  *count_labels(results, labels, playwright=have_pw)]
    (out / "labels.csv").write_text(labels_csv(to_label, known), encoding="utf-8")
    (out / "report.html").write_text(report_html(results, lines, playwright=have_pw),
                                     encoding="utf-8")
    (out / "pairs.json").write_text(json.dumps([{
        "pair": r.id, "expected": str(r.pair.expected), "actual": str(r.pair.actual),
        "v2": _verdict(r.v2_failed), "v2_says": v2_says(r), "renderer": r.renderer,
        "page_shift": list(r.page_shift) if r.page_shift else None,
        "playwright": r.playwright} for r in results], indent=1, ensure_ascii=False),
        encoding="utf-8")
    lines += ["", f"written: {(out / 'report.html').as_posix()}, "
                  f"{(out / 'labels.csv').as_posix()} ({len(to_label)} row"
                  f"{'s' if len(to_label) != 1 else ''} to label), "
                  f"{(out / 'pairs.json').as_posix()}, {(out / 'diff').as_posix()}/"]
    text = "\n".join(lines) + "\n"
    (out / "summary.txt").write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    return 0
