# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Self-contained HTML report of a run.

Why a separate report if there's already a UI. Because investigating a
regression doesn't end in the UI: a ticket gets filed, and something has to be
attached to it. A link to `localhost:8420` in a ticket is useless — it won't
open for a developer on another machine, nor for a tester half a year later
when the run has already been cleaned up by retention.

That's why the report is **a single file**. Images inside as data-URIs, styles
inside, script inside. Not a single external request: the file opens with a
double click on a machine with no network, gets forwarded in a messenger and
lives in the ticket for as long as the ticket lives.

This is also requirement §1.1: an installation in a closed environment does not
reach outside for anything, including fonts and chart libraries.

What it's built from: the run directory, where each snapshot has a subdirectory
with `result.json` and artifacts. This layout is produced by `CheckService`, so
any run works — your own, from the recorder, or from an externally connected
project.
"""

from __future__ import annotations

import base64
import html
import json
import mimetypes
from datetime import datetime
from pathlib import Path
from typing import Any

from vistest import provenance as _prov

# Images of large canvases weigh megabytes, but the report has to open and be
# forwarded. An upper bound per file and on the whole report: better a report
# without some illustrations than a 300-megabyte file that nobody will open.
MAX_IMAGE_BYTES = 6 * 1024 * 1024
MAX_TOTAL_BYTES = 60 * 1024 * 1024

# Display order: first what answers the question "what broke", then what
# answers "how much".
ARTIFACT_ORDER = ["boxes", "side_by_side", "onion", "heatmap", "actual", "blink"]
ARTIFACT_TITLES = {
    "boxes": "What changed",
    "side_by_side": "Before / after / boxes",
    "onion": "Overlay: red — before, cyan — after",
    "heatmap": "How strong (ΔE00)",
    "actual": "Full snapshot",
    "blink": "Blink before/after",
}

KIND_LABELS = {
    "text": "text", "moved": "shift", "color": "color", "added": "appeared",
    "removed": "disappeared", "content": "content", "resized": "size",
    "noise": "noise", "antialias": "antialiasing",
}


# --------------------------------------------------------------------------- #
#  Data collection
# --------------------------------------------------------------------------- #
def collect_run(run_dir: str | Path) -> list[dict]:
    """Run directory → list of comparisons.

    We read each snapshot's `result.json`. If there's an `external.json` or
    `adapter.json` from a connected project alongside it — we use those: they
    have the same dicts, but already assembled in the right order.
    """
    run_dir = Path(run_dir)

    for name in ("external.json", "run.json"):
        path = run_dir / name
        if path.exists():
            try:
                data = json.loads(path.read_text("utf-8"))
            except Exception:
                continue
            items = data.get("results") or data.get("comparisons") or []
            if items:
                return [dict(i) for i in items]

    out: list[dict] = []
    for result in sorted(run_dir.glob("*/result.json")):
        try:
            out.append(json.loads(result.read_text("utf-8")))
        except Exception:
            continue
    return out


def _embed(path: str | Path, budget: dict) -> str:
    """File → data-URI. Empty string if it doesn't fit or is unavailable."""
    try:
        p = Path(path)
        if not p.is_file():
            return ""
        size = p.stat().st_size
        if size > MAX_IMAGE_BYTES or budget["used"] + size > MAX_TOTAL_BYTES:
            budget["skipped"] += 1
            return ""
        data = p.read_bytes()
    except OSError:
        return ""

    budget["used"] += len(data)
    mime = mimetypes.guess_type(p.name)[0] or "image/png"
    return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")


# --------------------------------------------------------------------------- #
#  Rendering
# --------------------------------------------------------------------------- #
def render_report(comparisons: list[dict], *, title: str = "Visual run",
                  meta: dict | None = None) -> str:
    budget = {"used": 0, "skipped": 0}
    meta = meta or {}

    failed = [c for c in comparisons if c.get("verdict") == "fail"]
    new = [c for c in comparisons if c.get("verdict") == "new_baseline"]
    passed = [c for c in comparisons
              if c.get("verdict") not in ("fail", "new_baseline")]

    # Failures first and by descending severity: the report is read from the
    # top and usually only the first screen.
    ordered = (sorted(failed, key=_severity, reverse=True)
               + sorted(new, key=lambda c: c.get("name", ""))
               + sorted(passed, key=lambda c: c.get("name", "")))

    cards = "\n".join(_card(c, i, budget) for i, c in enumerate(ordered))

    summary = (
        f'<div class="sum">'
        f'<span class="pill fail">{len(failed)} failed</span>'
        f'<span class="pill new">{len(new)} new baselines</span>'
        f'<span class="pill pass">{len(passed)} passed</span>'
        f'</div>'
    )

    lines = [f"<b>{html.escape(str(k))}:</b> {html.escape(str(v))}"
             for k, v in meta.items() if v]
    note = ""
    if budget["skipped"]:
        note = (f'<p class="warn">Images not embedded: {budget["skipped"]} — '
                "they did not fit within the report size limit. The originals "
                "remain in the run directory.</p>")

    return _PAGE.format(
        title=html.escape(title),
        generated=datetime.now().strftime("%Y-%m-%d %H:%M"),
        meta="<br>".join(lines),
        summary=summary,
        note=note,
        cards=cards or '<p class="muted">No comparisons in the run.</p>',
        size_mb=budget["used"] / 1048576,
        provenance=_prov.html_footer(),
    )


def _severity(c: dict) -> float:
    return float((c.get("metrics") or {}).get("max_severity")
                 or c.get("max_severity") or 0)


def _card(c: dict, idx: int, budget: dict) -> str:
    name = html.escape(str(c.get("name", "snapshot")))
    verdict = c.get("verdict", "pass")
    cls = {"fail": "fail", "new_baseline": "new"}.get(verdict, "pass")
    label = {"fail": "failed", "new_baseline": "new baseline"}.get(verdict, "passed")

    m = c.get("metrics") or {}
    stats = [
        ("severity", _fmt(m.get("max_severity", c.get("max_severity")), 1)),
        ("changed", _fmt(m.get("changed_area_pct", c.get("changed_area_pct")), 3) + "%"),
        ("SSIM", _fmt(m.get("ssim_global", c.get("ssim")), 5)),
        ("ΔE00 avg.", _fmt(m.get("de_mean"), 2)),
    ]
    stat_html = "".join(
        f'<div><span class="k">{k}</span><span class="v">{v}</span></div>'
        for k, v in stats)

    images = []
    artifacts = c.get("artifacts") or {}
    for kind in ARTIFACT_ORDER:
        src = artifacts.get(kind)
        if not isinstance(src, str):
            continue
        uri = _embed(src, budget)
        if not uri:
            continue
        images.append(
            f'<figure><figcaption>{ARTIFACT_TITLES.get(kind, kind)}</figcaption>'
            f'<img loading="lazy" src="{uri}" alt="{html.escape(kind)}"></figure>')

    regions = c.get("regions") or []
    rows = ""
    for r in sorted(regions, key=lambda x: -(x.get("severity") or 0))[:20]:
        kind = KIND_LABELS.get(r.get("kind", ""), r.get("kind", ""))
        where = r.get("selector") or ""
        text = r.get("element_text") or ""
        shift = ""
        if r.get("kind") == "moved":
            shift = f' shift ({r.get("moved_dx", 0):+d}, {r.get("moved_dy", 0):+d})'
        rows += (
            "<tr>"
            f'<td><span class="kind">{html.escape(kind)}</span></td>'
            f'<td class="num">{_fmt(r.get("severity"), 1)}</td>'
            f'<td class="num">{r.get("w", 0)}×{r.get("h", 0)}</td>'
            f'<td class="num">{r.get("x", 0)}, {r.get("y", 0)}</td>'
            f'<td class="sel">{html.escape(str(where))}'
            + (f' <i>"{html.escape(str(text))}"</i>' if text else "")
            + html.escape(shift) + "</td></tr>")

    table = ""
    if rows:
        table = (
            '<table><thead><tr><th>Class</th><th>Severity</th><th>Size</th>'
            "<th>Position</th><th>Element</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>")
        if len(regions) > 20:
            table += f'<p class="muted">Showing 20 of {len(regions)} regions.</p>'

    notes = "".join(f"<li>{html.escape(str(n))}</li>" for n in (c.get("notes") or []))
    notes_html = f'<ul class="notes">{notes}</ul>' if notes else ""

    open_attr = " open" if verdict == "fail" and idx < 5 else ""
    return (
        f'<details class="card {cls}"{open_attr}>'
        f'<summary><span class="badge {cls}">{label}</span>'
        f'<span class="name">{name}</span>'
        f'<span class="sev">severity {_fmt(m.get("max_severity", c.get("max_severity")), 1)}'
        f'</span></summary>'
        f'<div class="stats">{stat_html}</div>'
        f"{notes_html}{table}"
        f'<div class="shots">{"".join(images)}</div>'
        "</details>")


def _fmt(value: Any, digits: int = 2) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "—"


# --------------------------------------------------------------------------- #
def build_report(run_dir: str | Path, out: str | Path | None = None, *,
                 title: str = "", meta: dict | None = None) -> Path:
    """Build the report from a run directory. Returns the path to the file."""
    run_dir = Path(run_dir)
    comparisons = collect_run(run_dir)

    out = Path(out) if out else run_dir / "report.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        render_report(comparisons,
                      title=title or f"Visual run: {run_dir.name}",
                      meta={"Run directory": str(run_dir.resolve()),
                            **(meta or {})}),
        encoding="utf-8")
    return out


# --------------------------------------------------------------------------- #
#  Template. Everything inside: not a single external request.
# --------------------------------------------------------------------------- #
_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
  :root {{
    --bg:#12141a; --panel:#171a21; --panel2:#1e222b; --line:#272c37;
    --fg:#e7ebf3; --muted:#98a0b0; --pass:#2ecc71; --fail:#e74c3c;
    --new:#9b59b6; --accent:#4c8dff;
  }}
  @media print {{
    :root {{ --bg:#fff; --panel:#fff; --panel2:#f5f6f8; --line:#d7dae1;
             --fg:#1a1d24; --muted:#5b6270; }}
    details {{ page-break-inside: avoid; }}
    summary {{ list-style: none; }}
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--fg);
         font:14px/1.6 -apple-system,Segoe UI,Roboto,sans-serif; }}
  .wrap {{ max-width:1180px; margin:0 auto; padding:28px 20px 60px; }}
  h1 {{ font-size:22px; margin:0 0 6px; }}
  .head-meta {{ color:var(--muted); font-size:12.5px; line-height:1.7; }}
  .sum {{ display:flex; gap:8px; flex-wrap:wrap; margin:16px 0 6px; }}
  .pill {{ padding:4px 12px; border-radius:20px; font-size:12px; font-weight:700;
          border:1px solid var(--line); }}
  .pill.fail {{ color:var(--fail); border-color:var(--fail); }}
  .pill.new  {{ color:var(--new);  border-color:var(--new); }}
  .pill.pass {{ color:var(--pass); border-color:var(--pass); }}
  .warn {{ color:#f1c40f; font-size:12.5px; }}
  .muted {{ color:var(--muted); font-size:12.5px; }}
  .card {{ background:var(--panel); border:1px solid var(--line);
          border-radius:10px; margin:12px 0; overflow:hidden; }}
  .card.fail {{ border-left:3px solid var(--fail); }}
  .card.new  {{ border-left:3px solid var(--new); }}
  .card.pass {{ border-left:3px solid var(--pass); }}
  summary {{ cursor:pointer; padding:12px 14px; display:flex; gap:12px;
            align-items:center; user-select:none; }}
  summary::-webkit-details-marker {{ display:none; }}
  .badge {{ padding:2px 9px; border-radius:20px; font-size:11px; font-weight:700;
           text-transform:uppercase; letter-spacing:.4px; }}
  .badge.fail {{ background:rgba(231,76,60,.16); color:var(--fail); }}
  .badge.new  {{ background:rgba(155,89,182,.16); color:var(--new); }}
  .badge.pass {{ background:rgba(46,204,113,.16); color:var(--pass); }}
  .name {{ font-weight:600; flex:1; word-break:break-all; }}
  .sev {{ color:var(--muted); font-size:12px; white-space:nowrap; }}
  .stats {{ display:flex; gap:22px; flex-wrap:wrap; padding:0 14px 10px; }}
  .stats .k {{ color:var(--muted); font-size:11px; display:block; }}
  .stats .v {{ font-size:16px; font-weight:700; }}
  .notes {{ margin:0 14px 12px; padding-left:18px; color:var(--muted);
           font-size:12.5px; }}
  table {{ width:calc(100% - 28px); margin:0 14px 14px; border-collapse:collapse; }}
  th,td {{ text-align:left; padding:6px 8px; border-bottom:1px solid var(--line);
          font-size:12.5px; vertical-align:top; }}
  th {{ color:var(--muted); font-size:11px; font-weight:600; }}
  td.num {{ white-space:nowrap; }}
  td.sel {{ font-family:ui-monospace,Consolas,monospace; font-size:11px;
           word-break:break-all; color:var(--muted); }}
  .kind {{ background:var(--panel2); padding:1px 7px; border-radius:4px;
          font-size:11px; }}
  .shots {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(320px,1fr));
           gap:14px; padding:0 14px 16px; }}
  figure {{ margin:0; background:var(--panel2); border:1px solid var(--line);
           border-radius:8px; overflow:hidden; }}
  figcaption {{ padding:7px 10px; font-size:11.5px; color:var(--muted);
               border-bottom:1px solid var(--line); }}
  img {{ display:block; width:100%; height:auto; cursor:zoom-in; }}
  #zoom {{ position:fixed; inset:0; background:rgba(0,0,0,.92); display:none;
          overflow:auto; z-index:50; padding:20px; }}
  #zoom img {{ max-width:none; width:auto; margin:0 auto; cursor:zoom-out; }}
  footer {{ color:var(--muted); font-size:12px; margin-top:30px;
           border-top:1px solid var(--line); padding-top:14px; }}
</style></head>
<body><div class="wrap">
  <h1>{title}</h1>
  <div class="head-meta">Generated {generated}<br>{meta}</div>
  {summary}
  {note}
  {cards}
  <footer>
    The report is self-contained: images and styles are embedded, there are no
    external requests — it can be attached to a ticket and opened on a machine
    with no network. Attachment size: {size_mb:.1f} MB.<br>
    {provenance}
  </footer>
</div>
<div id="zoom"><img alt=""></div>
<script>
  // Click on an image — view at full size. On full-page canvases an image
  // fitted to the width is unreadable, yet that's exactly the one where you
  // need to make out what shifted.
  var z = document.getElementById('zoom'), zi = z.querySelector('img');
  document.addEventListener('click', function (e) {{
    if (e.target.tagName === 'IMG' && e.target !== zi) {{
      zi.src = e.target.src; z.style.display = 'block';
    }} else if (z.style.display === 'block') {{
      z.style.display = 'none'; zi.removeAttribute('src');
    }}
  }});
  document.addEventListener('keydown', function (e) {{
    if (e.key === 'Escape') {{ z.style.display = 'none'; }}
  }});
</script>
</body></html>
"""
