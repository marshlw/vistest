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

__all__ = ["Parts", "build", "describe", "read_parts", "render", "total_suppressed",
           "write_part"]

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
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])
    parts = [f"{KIND_WORDS.get(kind, kind)} in {n}" for kind, n in ranked[:3]]
    rest = sum(n for _, n in ranked[3:])
    if rest:
        #  Never drop kinds silently: the counts have to add up to the total.
        parts.append(f"other changes in {rest}")
    total = len(regions)
    head = (f"{total} region{'s' if total > 1 else ''}: " + ", ".join(parts))

    #  Picked by severity, and named for it. It used to say "largest", which a
    #  reader checks against the sizes and finds false.
    top = max(regions, key=lambda r: getattr(r, "severity", 0.0))
    where = (f"most severe {top.w}x{top.h} at ({top.x}, {top.y})"
             + (f", {top.selector}" if getattr(top, "selector", None) else ""))
    return f"{head}; {where}" + _coverage(result)


#  Below this share of the changed pixels left outside every region, the
#  headline area and the regions tell the same story and nothing is added.
UNASSIGNED_SHARE_TO_MENTION = 0.10


def _coverage(result) -> str:
    """The bridge between `changed area` and the regions, when they disagree.

    `changed_area_pct` is measured on the change mask before segmentation;
    regions are what survives it. When most of the change is in no region, the
    reader must be told so in the same line — otherwise the area and the list
    cannot be reconciled and neither number is believed.
    """
    changed = int(getattr(result, "changed_pixels", 0) or 0)
    unassigned = int(getattr(result, "unassigned_pixels", 0) or 0)
    if changed <= 0 or unassigned <= UNASSIGNED_SHARE_TO_MENTION * changed:
        return ""
    total = max(int(getattr(result, "total_pixels", 0) or 0), 1)
    in_regions = 100.0 * int(getattr(result, "region_pixels", 0) or 0) / total
    suppressed = int(getattr(result, "suppressed_pixels", 0) or 0)
    outside = 100.0 * unassigned / total
    text = (f"; these regions hold {in_regions:.2f}% of the "
            f"{result.changed_area_pct:.2f}% changed, {outside:.2f}% is in no "
            "region (strokes or specks too thin to form one)")
    if suppressed:
        text += f", {100.0 * suppressed / total:.2f}% was suppressed as noise"
    return text


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
                        if "max_changed_area_pct" in limits else "")
                     + (f" ({metrics['region_area_pct']:.2f}% in regions)"
                        if "region_area_pct" in metrics else ""))
    if "ssim_global" in metrics:
        facts.append(f"SSIM {metrics['ssim_global']:.4f}")
    if entry.get("duration_ms"):
        facts.append(f"{int(entry['duration_ms'])} ms")

    suppressed = int(entry.get("suppressed_count") or 0)
    if suppressed:
        facts.append(f"{suppressed} suppressed")

    open_attr = " open" if verdict in ("fail", "error") else ""
    body = _viewer(entry, images) if verdict != "pass" else ""
    body = _regions_table(entry) + _suppressed_list(entry) + body

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


def _annotations_text(region: dict) -> str:
    return " · ".join(str(a.get("text", "")) for a in region.get("annotations") or []
                      if isinstance(a, dict))


def _regions_table(entry: dict) -> str:
    """The regions that count — shown only when an extension said something.

    Without a scorer or an annotator the reason line already names them, and a
    table of coordinates adds nothing. With one, the score and the remarks are
    the point, and they need a place.
    """
    regions = [r for r in entry.get("regions") or [] if isinstance(r, dict)]
    scored = any(r.get("score") is not None for r in regions)
    noted = any(r.get("annotations") for r in regions)
    if not (scored or noted):
        return ""
    head = "<tr><th>kind</th><th>severity</th><th>where</th>"
    head += "<th>score</th>" if scored else ""
    head += "<th>notes</th>" if noted else ""
    rows = []
    for r in regions:
        cells = (f"<td>{_e(r.get('kind', ''))}</td>"
                 f"<td>{float(r.get('severity') or 0):.1f}</td>"
                 f"<td>{_e(_where(r))}</td>")
        if scored:
            score = r.get("score")
            cells += f"<td>{'' if score is None else f'{float(score):.2f}'}</td>"
        if noted:
            cells += f"<td>{_e(_annotations_text(r))}</td>"
        rows.append(f"<tr>{cells}</tr>")
    return (f'<table class="regions"><thead>{head}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table>')


def _where(region: dict) -> str:
    text = (f"{region.get('w')}x{region.get('h')} at "
            f"({region.get('x')}, {region.get('y')})")
    if region.get("selector"):
        text += f", {region['selector']}"
    return text


def _suppressed_list(entry: dict) -> str:
    """What was set aside and why. Present in passing rows too."""
    count = int(entry.get("suppressed_count") or 0)
    if not count:
        return ""
    from ..plugins.runtime import say_suppressed

    listed = [r for r in entry.get("suppressed") or [] if isinstance(r, dict)]
    items = []
    for r in listed:
        extra = _annotations_text(r)
        items.append(
            f"<li>{_e(r.get('kind', ''))} {_e(_where(r))} — "
            f"{_e(r.get('suppressed_by') or 'suppressed')}"
            + (f" <span class=\"muted\">({_e(extra)})</span>" if extra else "")
            + "</li>")
    more = count - len(listed)
    if more > 0:
        items.append(f"<li>… and {more} more</li>")
    summary = "; ".join(say_suppressed(entry.get("suppressed_by_reason") or
                                       {"suppressed": count}))
    return (f'<details class="suppressed"><summary>{_e(summary)}</summary>'
            f'<ul>{"".join(items)}</ul></details>')


def total_suppressed(entries: list[dict]) -> dict[str, int]:
    """Suppression counts summed over a run, by reason phrase."""
    total: dict[str, int] = {}
    for entry in entries:
        for phrase, n in (entry.get("suppressed_by_reason") or {}).items():
            try:
                total[str(phrase)] = total.get(str(phrase), 0) + int(n)
            except (TypeError, ValueError):
                continue
    return total


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
.regions{border-collapse:collapse;margin:8px 0;font-size:12px;width:100%}
.regions th,.regions td{border-bottom:1px solid var(--line);padding:4px 8px;
  text-align:left;vertical-align:top}
.regions th{color:var(--muted);font-weight:500}
.suppressed{margin:8px 0;color:var(--muted);font-size:12px}
.suppressed summary{cursor:pointer}
.suppressed ul{margin:4px 0 0;padding-left:20px}
.muted{color:var(--muted)}
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


def _suppressed_total(entries: list[dict]) -> str:
    total = total_suppressed(entries)
    if not total:
        return ""
    from ..plugins.runtime import say_suppressed

    return " · " + _e("; ".join(say_suppressed(total)))


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
        f'check{"s" if len(entries) != 1 else ""} · {stamp}'
        f'{_suppressed_total(entries)}</p>'
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
