# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""The library mode's report: one HTML file, assembled after the run.

Separate from `report.html`, which builds from a run directory produced by
`CheckService` — a layout the library mode does not create and does not want
to. What the two share is the principle, not the code: no CDN, no web fonts,
no external request of any kind, every picture inlined as a data URI. The file
opens by double-clicking it on a machine with no network, survives being
forwarded in a messenger, and outlives the run directory it came from.

**Why it is assembled from parts.** Under `pytest -n` the checks run in several
processes at once. Each writes its own small JSON file under a name nobody else
uses, and nothing is shared while the run is going: no index, no lock, no
append to a common file. When the run ends, the controller reads whatever is
there and renders it. A worker that died mid-test costs exactly its own row.

The ordering is by platform and name rather than by time, deliberately: the
same suite must produce the same report whichever worker happened to finish
first, or the file is useless for comparing two runs.
"""

from __future__ import annotations

import base64
import html
import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from ..storage import atomic

__all__ = ["Parts", "build", "describe", "read_parts", "render", "write_part"]

#  A full-page screenshot is megabytes. The report has to open and be
#  forwarded, so there is a ceiling per picture and one on the whole file:
#  better a report missing some illustrations than a 300 MB file nobody opens.
MAX_IMAGE_BYTES = 6 * 1024 * 1024
MAX_TOTAL_BYTES = 60 * 1024 * 1024

KIND_WORDS = {
    "text": "text", "moved": "a shift", "color": "colour",
    "added": "something appeared", "removed": "something disappeared",
    "content": "content", "resized": "a size change",
    "noise": "noise", "antialias": "antialiasing",
}


# --------------------------------------------------------------------------- #
#  Saying what happened, in words
# --------------------------------------------------------------------------- #
def describe(result) -> str:
    """One line naming what changed. The same text the exception carries.

    It is written once and used twice on purpose. The line in the CI log and
    the line in the report have to agree — when they do not, the first thing a
    person does is stop trusting both.
    """
    if getattr(result, "size_changed", False):
        expected, actual = result.size_expected, result.size_actual
        return (f"the picture changed size, {expected[0]}x{expected[1]} "
                f"to {actual[0]}x{actual[1]}")

    regions = list(getattr(result, "regions", ()) or ())
    if not regions:
        return (f"no single region stands out; "
                f"{result.changed_area_pct:.2f}% of the frame differs")

    counts: dict[str, int] = {}
    for region in regions:
        kind = getattr(region.kind, "value", str(region.kind))
        counts[kind] = counts.get(kind, 0) + 1
    parts = [f"{KIND_WORDS.get(kind, kind)} in {n} region{'s' if n > 1 else ''}"
             for kind, n in sorted(counts.items(), key=lambda kv: -kv[1])]

    biggest = max(regions, key=lambda r: getattr(r, "severity", 0.0))
    where = (f"largest {biggest.w}x{biggest.h} at ({biggest.x}, {biggest.y})"
             + (f", {biggest.selector}" if getattr(biggest, "selector", None)
                else ""))
    return ", ".join(parts[:3]) + f"; {where}"


# --------------------------------------------------------------------------- #
#  Parts
# --------------------------------------------------------------------------- #
def write_part(parts_dir: str | Path, entry: dict) -> Path:
    """One check's row, written under a name no other process will pick."""
    entry = {**entry, "written_at": time.time()}
    path = Path(parts_dir) / f"{uuid4().hex}.json"
    return atomic.write_json(path, entry)


@dataclass
class Parts:
    """What the run left behind, and what is wrong with it.

    Three things rather than a list of rows, because two of them are findings
    in their own right and both used to be invisible.
    """

    entries: list[dict] = field(default_factory=list)
    #  key -> the distinct tests that wrote it. Present only when there is more
    #  than one, which is the definition of the problem.
    collisions: dict[str, list[str]] = field(default_factory=dict)
    #  Parts that could not be read back. Tolerated, counted, and reported.
    unreadable: int = 0
    #  Where the report was written, filled in by `build`.
    report: Path | None = None


def read_parts(parts_dir: str | Path) -> Parts:
    """Every part, newest wins per key, ordered for a human — plus what is wrong.

    **Collisions.** Two tests can be written to use the same snapshot name, and
    until now that produced one row and no signal at all: the newest part won
    and the other test vanished from the report. Under `--vistest-update` it is
    worse than a missing row — the two tests overwrite each other's baseline in
    somebody's repository, and whichever ran last decides what the picture is.
    So the parts are grouped by key and the distinct `nodeid`s counted. One
    nodeid writing a key twice is a retry and stays silent; two nodeids are a
    collision and are reported by name.

    **Unreadable parts.** Skipping one is right — the run already reported that
    check, and a report that fails to build helps nobody. Skipping it in
    silence is not: a report with things missing and no mention of it is worse
    than a report that did not build, because it will be read as complete.
    """
    parts_dir = Path(parts_dir)
    if not parts_dir.exists():
        return Parts()

    latest: dict[str, dict] = {}
    writers: dict[str, set[str]] = {}
    unreadable = 0

    for path in sorted(parts_dir.glob("*.json")):
        if path.name.startswith("."):
            continue
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            unreadable += 1
            continue
        if not isinstance(entry, dict):
            unreadable += 1
            continue

        key = str(entry.get("key") or entry.get("name") or path.stem)
        nodeid = str(entry.get("nodeid") or "").strip()
        if nodeid:
            writers.setdefault(key, set()).add(nodeid)

        previous = latest.get(key)
        if previous is None or entry.get("written_at", 0) >= \
                previous.get("written_at", 0):
            latest[key] = entry

    collisions = {key: sorted(tests) for key, tests in writers.items()
                  if len(tests) > 1}
    return Parts(
        entries=sorted(latest.values(),
                       key=lambda e: (str(e.get("platform", "")),
                                      str(e.get("name", "")))),
        collisions=collisions,
        unreadable=unreadable,
    )


# --------------------------------------------------------------------------- #
#  Rendering
# --------------------------------------------------------------------------- #
def _data_uri(path: str | Path | None, budget: list[int]) -> str:
    if not path:
        return ""
    path = Path(path)
    try:
        size = path.stat().st_size
    except OSError:
        return ""
    if size > MAX_IMAGE_BYTES or size > budget[0]:
        return ""
    try:
        raw = path.read_bytes()
    except OSError:
        return ""
    budget[0] -= len(raw)
    return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")


def _e(text) -> str:
    return html.escape(str(text), quote=True)


def _viewer(entry: dict, images: dict[str, str]) -> str:
    baseline, actual, diff = (images.get("baseline", ""),
                              images.get("actual", ""),
                              images.get("diff", ""))
    if not (baseline or actual):
        missing = entry.get("images", {}) or {}
        return ('<p class="none">The pictures are not in this report. '
                'They are on disk at:</p><ul class="paths">'
                + "".join(f"<li>{_e(v)}</li>" for v in missing.values() if v)
                + "</ul>")

    tabs, panes = [], []
    if baseline and actual:
        tabs.append('<button class="tab on" data-pane="slider">Slider</button>')
        panes.append(
            '<div class="pane on" data-pane="slider">'
            f'<div class="cmp" style="--p:50%"><img alt="baseline" src="{baseline}">'
            f'<img class="over" alt="actual" src="{actual}">'
            '<span class="handle"></span></div>'
            '<input class="range" type="range" min="0" max="100" value="50" '
            'aria-label="Reveal the new screenshot">'
            '<div class="legend"><span>baseline</span><span>actual</span></div>'
            '</div>')
        tabs.append('<button class="tab" data-pane="side">Side by side</button>')
        panes.append(
            '<div class="pane" data-pane="side"><div class="side">'
            f'<figure><img alt="baseline" src="{baseline}">'
            '<figcaption>baseline</figcaption></figure>'
            f'<figure><img alt="actual" src="{actual}">'
            '<figcaption>actual</figcaption></figure>'
            '</div></div>')
    if diff:
        tabs.append('<button class="tab" data-pane="diff">Diff</button>')
        panes.append(f'<div class="pane" data-pane="diff">'
                     f'<img alt="diff" src="{diff}"></div>')
    if actual and not baseline:
        tabs.append('<button class="tab on" data-pane="only">Screenshot</button>')
        panes.append('<div class="pane on" data-pane="only">'
                     f'<img alt="screenshot" src="{actual}"></div>')

    return f'<div class="tabs">{"".join(tabs)}</div>{"".join(panes)}'


def _row(entry: dict, budget: list[int]) -> str:
    verdict = str(entry.get("verdict", "error"))
    images = {kind: _data_uri(path, budget)
              for kind, path in (entry.get("images") or {}).items()}
    metrics = entry.get("metrics") or {}
    limits = entry.get("limits") or {}

    facts = []
    if "max_severity" in metrics:
        facts.append(f"severity {metrics['max_severity']:.1f}"
                     + (f" / {limits['fail_severity']:.1f}"
                        if "fail_severity" in limits else ""))
    if "changed_area_pct" in metrics:
        facts.append(f"area {metrics['changed_area_pct']:.2f}%"
                     + (f" / {limits['max_changed_area_pct']:.2f}%"
                        if "max_changed_area_pct" in limits else ""))
    if "ssim_global" in metrics:
        facts.append(f"SSIM {metrics['ssim_global']:.4f}")
    if entry.get("duration_ms"):
        facts.append(f"{int(entry['duration_ms'])} ms")

    open_attr = " open" if verdict in ("fail", "error") else ""
    body = _viewer(entry, images) if verdict != "pass" else ""

    return (
        f'<details class="row {_e(verdict)}"{open_attr} data-verdict="{_e(verdict)}">'
        f'<summary><span class="badge">{_e(verdict.replace("_", " "))}</span>'
        f'<span class="name">{_e(entry.get("name", "?"))}</span>'
        f'<span class="platform">{_e(entry.get("platform") or "no platform")}</span>'
        f'<span class="facts">{_e(" · ".join(facts))}</span></summary>'
        f'<div class="detail">'
        f'<p class="reason">{_e(entry.get("reason", ""))}</p>'
        + (f'<p class="nodeid">{_e(entry["nodeid"])}</p>'
           if entry.get("nodeid") else "")
        + body + '</div></details>')


_CSS = """
:root{
  --bg:#f6f7f9; --fg:#12161c; --muted:#5b6672; --card:#fff; --line:#dfe3e8;
  --fail:#c0392b; --pass:#2e7d4d; --new:#8a6d1f; --accent:#2f6fd0;
}
@media (prefers-color-scheme: dark){
  :root{
    --bg:#11151a; --fg:#e6eaef; --muted:#93a0ad; --card:#171d24; --line:#2a333d;
    --fail:#ff7b6b; --pass:#6fd08c; --new:#e8c65c; --accent:#79aaff;
  }
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
  font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Ubuntu,
  "Helvetica Neue",Arial,sans-serif;}
.wrap{max-width:1080px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:20px;margin:0 0 4px}
.sub{color:var(--muted);margin:0 0 20px}
.counts{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 18px}
.count{background:var(--card);border:1px solid var(--line);border-radius:8px;
  padding:8px 12px;cursor:pointer;color:inherit;font:inherit}
.count.on{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent) inset}
.count b{font-size:18px;display:block}
.row{background:var(--card);border:1px solid var(--line);border-radius:10px;
  margin:0 0 10px;overflow:hidden}
.row summary{display:flex;gap:10px;align-items:center;flex-wrap:wrap;
  padding:10px 14px;cursor:pointer;list-style:none}
.row summary::-webkit-details-marker{display:none}
.badge{font-size:11px;text-transform:uppercase;letter-spacing:.04em;
  border-radius:999px;padding:2px 9px;border:1px solid currentColor}
.fail .badge{color:var(--fail)} .pass .badge{color:var(--pass)}
.new_baseline .badge{color:var(--new)} .error .badge{color:var(--fail)}
.name{font-weight:600;word-break:break-all}
.platform{color:var(--muted);font-size:12px}
.facts{margin-left:auto;color:var(--muted);font-size:12px;
  font-variant-numeric:tabular-nums}
.detail{padding:0 14px 16px;border-top:1px solid var(--line)}
.reason{margin:12px 0 4px}
.nodeid{margin:0 0 12px;color:var(--muted);font-size:12px;
  font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.none{color:var(--muted)} .paths{color:var(--muted);font-size:12px;
  font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.tabs{display:flex;gap:6px;margin:6px 0 10px;flex-wrap:wrap}
.tab{background:transparent;border:1px solid var(--line);border-radius:7px;
  padding:5px 11px;cursor:pointer;color:var(--muted);font:inherit;font-size:12px}
.tab.on{color:var(--fg);border-color:var(--accent)}
.pane{display:none} .pane.on{display:block}
.pane img{max-width:100%;display:block;border:1px solid var(--line);
  border-radius:6px;background:
  repeating-conic-gradient(#8884 0% 25%,transparent 0% 50%) 50%/16px 16px}
.cmp{position:relative;display:inline-block;max-width:100%;line-height:0}
.cmp img{max-width:100%}
.cmp .over{position:absolute;inset:0;width:100%;height:100%;
  clip-path:inset(0 0 0 var(--p));border-radius:6px}
.cmp .handle{position:absolute;top:0;bottom:0;left:var(--p);width:2px;
  background:var(--accent);pointer-events:none}
.range{width:100%;margin:10px 0 2px;accent-color:var(--accent)}
.legend{display:flex;justify-content:space-between;color:var(--muted);
  font-size:12px}
.side{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.side figure{margin:0} .side figcaption{color:var(--muted);font-size:12px;
  padding-top:4px}
@media (max-width:720px){.side{grid-template-columns:1fr}
  .facts{margin-left:0;width:100%}}
.empty{background:var(--card);border:1px solid var(--line);border-radius:10px;
  padding:28px;text-align:center;color:var(--muted)}
.banner{border:1px solid var(--line);border-left-width:4px;border-radius:8px;
  background:var(--card);padding:12px 14px;margin:0 0 12px}
.banner.bad{border-left-color:var(--fail)}
.banner.warn{border-left-color:var(--new)}
.banner b{display:block;margin-bottom:6px;color:var(--fail)}
.banner.warn b{color:var(--new)}
.banner ul{margin:0 0 8px;padding-left:20px}
.banner p{margin:0;color:var(--muted)}
.banner code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  font-size:12px}
footer{color:var(--muted);font-size:12px;margin-top:28px}
"""

_JS = """
document.addEventListener('input', function (e) {
  if (!e.target.classList.contains('range')) return;
  var pane = e.target.closest('.pane');
  var cmp = pane && pane.querySelector('.cmp');
  if (cmp) cmp.style.setProperty('--p', e.target.value + '%');
});
document.addEventListener('click', function (e) {
  var tab = e.target.closest('.tab');
  if (tab) {
    var box = tab.closest('.detail');
    box.querySelectorAll('.tab').forEach(function (t) { t.classList.remove('on'); });
    box.querySelectorAll('.pane').forEach(function (p) { p.classList.remove('on'); });
    tab.classList.add('on');
    var pane = box.querySelector('.pane[data-pane="' + tab.dataset.pane + '"]');
    if (pane) pane.classList.add('on');
    return;
  }
  var count = e.target.closest('.count');
  if (!count) return;
  var want = count.dataset.verdict;
  document.querySelectorAll('.count').forEach(function (c) {
    c.classList.toggle('on', c === count);
  });
  document.querySelectorAll('.row').forEach(function (row) {
    row.hidden = !(want === 'all' || row.dataset.verdict === want);
  });
});
"""


def _banners(parts: Parts) -> str:
    """What the reader has to know before believing the rows below."""
    out = []
    if parts.collisions:
        rows = "".join(
            f"<li><code>{_e(key)}</code> — written by "
            + ", ".join(f"<code>{_e(test)}</code>" for test in tests)
            + "</li>"
            for key, tests in sorted(parts.collisions.items()))
        out.append(
            '<div class="banner bad"><b>Name collision</b>'
            f"<ul>{rows}</ul>"
            "<p>These tests compare against the same baseline. Only the one "
            "that finished last is shown here, and under "
            "<code>--vistest-update</code> they overwrite each other's "
            "baseline file. Give each check its own name.</p></div>")
    if parts.unreadable:
        out.append(
            '<div class="banner warn">'
            f"{parts.unreadable} check"
            f"{'s' if parts.unreadable != 1 else ''} could not be read into "
            "this report. The run itself reported them; what is missing is "
            "their row here.</div>")
    return "".join(out)


def render(parts: Parts | list[dict], *, title: str = "VisTest") -> str:
    """The whole report as one string. No network, no fonts, no libraries."""
    if not isinstance(parts, Parts):
        parts = Parts(entries=list(parts))
    entries = parts.entries
    budget = [MAX_TOTAL_BYTES]
    counts: dict[str, int] = {}
    for entry in entries:
        verdict = str(entry.get("verdict", "error"))
        counts[verdict] = counts.get(verdict, 0) + 1

    order = ("fail", "error", "new_baseline", "pass")
    chips = ['<button class="count on" data-verdict="all">'
             f'<b>{len(entries)}</b>all</button>']
    for verdict in order:
        if counts.get(verdict):
            chips.append(f'<button class="count" data-verdict="{verdict}">'
                         f'<b>{counts[verdict]}</b>{verdict.replace("_", " ")}'
                         '</button>')

    rows = "".join(_row(entry, budget) for entry in entries) or \
        '<div class="empty">This run made no visual checks.</div>'

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>{_e(title)}</title><style>{_CSS}</style></head><body>"
        f'<div class="wrap"><h1>{_e(title)}</h1>'
        f'<p class="sub">{len(entries)} visual '
        f'check{"s" if len(entries) != 1 else ""} · {stamp}</p>'
        f'{_banners(parts)}'
        f'<div class="counts">{"".join(chips)}</div>{rows}'
        "<footer>Generated by VisTest. Everything in this file is inside it — "
        "no network access is needed to read it.</footer></div>"
        f"<script>{_JS}</script></body></html>")


def build(parts_dir: str | Path, out: str | Path, *,
          title: str = "VisTest") -> Parts:
    """Read the parts, render, write the file.

    Returns what went in — including the collisions and the count of parts that
    could not be read, because the caller has to say those out loud too. The
    report is one place a person may look; the terminal is the one they cannot
    avoid.
    """
    parts = read_parts(parts_dir)
    parts.report = atomic.write_text(Path(out), render(parts, title=title))
    return parts
