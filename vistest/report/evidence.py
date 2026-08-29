# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Cumulative quality statistics — something you can actually present.

The synthetic benchmark (`tests/benchmark.py`) answers the question "does the
engine tell known kinds of noise apart from known kinds of regression". That is
about the engine.

Here the question and the evidence are different: **how many false failures the
engine produced on a live project over a real period**. It is counted from the
review that a person does anyway in the course of work: pressed "accept as
baseline" — the failure was false, pressed "this is a bug" — it was real.

The value is that the number accumulates on its own. There is no need to run an
experiment, label a corpus, or persuade anyone: it builds up from ordinary
runs, and after a month of work it can already be shown.

Three things without which such a number is a lie, and that is why they are
computed here unconditionally:

1. **Confidence interval.** "0% false failures" over eight comparisons means
   nothing: with such a sample the true value could well be 30%. The Wilson
   interval is used — it is correct on small samples and on proportions near
   the boundaries, unlike the normal approximation.
2. **Share of unreviewed.** Until someone has looked at a failure, it is
   unknown whether it is false or real. If more than half is unreviewed, the
   metric is incomplete, and this must be stated plainly rather than silently
   counted over the remainder.
3. **Measurement conditions.** Thresholds, preset, platform, period. Without
   them the number is not reproducible, which means it is not verifiable.

The document is deliberately called "evidence" with a caveat: this is data
collected by the tool itself on its own installation. It does not replace an
independent check and says so directly.
"""

from __future__ import annotations

import html
import json
import math
from datetime import datetime

Z95 = 1.959963985


# --------------------------------------------------------------------------- #
#  Statistics
# --------------------------------------------------------------------------- #
def wilson(successes: int, total: int, z: float = Z95) -> tuple[float, float]:
    """Wilson confidence interval for a proportion.

    The ordinary "p ± z·√(p(1−p)/n)" on small samples produces bounds outside
    [0,1] and degenerates to zero at p=0 — exactly where the interval is needed
    most. Wilson behaves correctly both at zero and at one.
    """
    if total <= 0:
        return 0.0, 1.0
    p = successes / total
    denom = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denom
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return max(0.0, center - margin), min(1.0, center + margin)


def confidence_note(total: int) -> str:
    """How much a sample of this size can be relied on at all."""
    if total == 0:
        return "No data."
    if total < 30:
        return ("The sample is small — at this volume the interval is wider "
                "than the estimate itself. Too early to show as a result.")
    if total < 100:
        return ("The sample is small: the direction is visible, the exact "
                "value is not. For publication it is worth accumulating at "
                "least a hundred reviewed failures.")
    if total < 500:
        return "The sample is sufficient for internal conclusions."
    return "The sample is sufficient for publication."


# --------------------------------------------------------------------------- #
#  Collection
# --------------------------------------------------------------------------- #
def collect_evidence(db, project: str = "*", days: int = 90) -> dict:
    """Collect quality statistics from run history."""
    from ..api.metrics import _scope
    from ..config import VisTestConfig

    where, args = _scope(project)
    since = f"-{int(days)} days"
    p = args + (since,)

    totals = db.one(
        "SELECT COUNT(*) AS comparisons,"
        "       SUM(c.verdict='pass') AS passed,"
        "       SUM(c.verdict='fail') AS failed,"
        "       SUM(c.verdict='new_baseline') AS new_baselines,"
        "       SUM(c.review='approved') AS approved,"
        "       SUM(c.review='rejected') AS rejected,"
        "       COUNT(DISTINCT c.run_id) AS runs,"
        "       COUNT(DISTINCT c.snapshot_id) AS snapshots,"
        "       MIN(c.created_at) AS first_seen,"
        "       MAX(c.created_at) AS last_seen"
        "  FROM comparison c JOIN run r ON r.id = c.run_id"
        "  JOIN project p ON p.id = r.project_id"
        f" WHERE {where} AND c.created_at >= datetime('now', ?)", p) or {}

    failed = int(totals.get("failed") or 0)
    approved = int(totals.get("approved") or 0)
    rejected = int(totals.get("rejected") or 0)
    reviewed = approved + rejected
    unreviewed = max(0, failed - reviewed)

    low, high = wilson(approved, reviewed)
    rate = (approved / reviewed) if reviewed else 0.0

    by_month = db.query(
        "SELECT strftime('%Y-%m', c.created_at) AS period,"
        "       SUM(c.verdict='fail') AS failed,"
        "       SUM(c.review='approved') AS approved,"
        "       SUM(c.review='rejected') AS rejected,"
        "       COUNT(*) AS comparisons"
        "  FROM comparison c JOIN run r ON r.id = c.run_id"
        "  JOIN project p ON p.id = r.project_id"
        f" WHERE {where} AND c.created_at >= datetime('now', ?)"
        " GROUP BY period ORDER BY period", p)

    for row in by_month:
        rev = (row.get("approved") or 0) + (row.get("rejected") or 0)
        row["reviewed"] = rev
        row["rate"] = round((row.get("approved") or 0) / rev, 4) if rev else None

    by_project = db.query(
        "SELECT p.name AS project, COUNT(*) AS comparisons,"
        "       SUM(c.verdict='fail') AS failed,"
        "       SUM(c.review='approved') AS approved,"
        "       SUM(c.review='rejected') AS rejected"
        "  FROM comparison c JOIN run r ON r.id = c.run_id"
        "  JOIN project p ON p.id = r.project_id"
        f" WHERE {where} AND c.created_at >= datetime('now', ?)"
        " GROUP BY p.id ORDER BY comparisons DESC", p)

    for row in by_project:
        rev = (row.get("approved") or 0) + (row.get("rejected") or 0)
        row["reviewed"] = rev
        row["rate"] = round((row.get("approved") or 0) / rev, 4) if rev else None
        lo, hi = wilson(row.get("approved") or 0, rev)
        row["ci"] = [round(lo, 4), round(hi, 4)]

    platforms = db.query(
        "SELECT r.platform, r.browser, COUNT(*) AS runs"
        "  FROM run r JOIN project p ON p.id = r.project_id"
        f" WHERE {where} AND r.started_at >= datetime('now', ?)"
        " GROUP BY r.platform, r.browser ORDER BY runs DESC", p)

    cfg = VisTestConfig.load()
    diff = cfg.diff

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "project": project,
        "days": days,
        "period": {"from": totals.get("first_seen"),
                   "to": totals.get("last_seen")},
        "volume": {
            "runs": int(totals.get("runs") or 0),
            "comparisons": int(totals.get("comparisons") or 0),
            "snapshots": int(totals.get("snapshots") or 0),
            "passed": int(totals.get("passed") or 0),
            "failed": failed,
            "new_baselines": int(totals.get("new_baselines") or 0),
        },
        "false_fail": {
            "approved": approved,
            "rejected": rejected,
            "reviewed": reviewed,
            "unreviewed": unreviewed,
            "rate": round(rate, 4),
            "ci95": [round(low, 4), round(high, 4)],
            "coverage": round(reviewed / failed, 4) if failed else 1.0,
            "confidence": confidence_note(reviewed),
        },
        "by_month": by_month,
        "by_project": by_project,
        "platforms": platforms,
        "conditions": {
            "preset": cfg.preset,
            "delta_e_threshold": diff.delta_e_threshold,
            "ssim_threshold": diff.ssim_threshold,
            "require_consensus": diff.require_consensus,
            "fail_severity": diff.fail_severity,
            "max_changed_area_pct": diff.max_changed_area_pct,
            "align_enabled": diff.align_enabled,
            "antialias_filter": diff.antialias_filter,
            "stability_shots": cfg.capture.stability_shots,
        },
        "caveats": caveats(failed, reviewed, unreviewed,
                           int(totals.get("comparisons") or 0)),
    }


def caveats(failed: int, reviewed: int, unreviewed: int,
            comparisons: int) -> list[str]:
    """Caveats without which the number must not be published.

    The list is computed, not written by hand: a caveat that was forgotten is
    worse than a missing one — it creates the impression that everything has
    been accounted for.
    """
    out = [
        "This is data from the project's own installation, collected by the "
        "tool itself. It does not replace an independent check — for that "
        "there is a synthetic corpus with an open methodology "
        "(see the benchmark section of README.md).",
        "A failure is considered false when a person accepted it as a "
        "baseline. The review is done by the same person who uses the tool — "
        "this is a subjective assessment, not an objective fact.",
    ]
    if comparisons < 30:
        out.append(
            f"Only {comparisons} comparisons in total — at this volume it is "
            "too early to draw conclusions regardless of the metric value.")
    if failed and unreviewed:
        share = unreviewed / failed
        out.append(
            f"{unreviewed} of {failed} failures are unreviewed "
            f"({share * 100:.0f}%). The metric is computed over the reviewed "
            "ones; if the unreviewed ones are distributed differently, the "
            "value will shift.")
    if failed == 0:
        out.append(
            "There were no failures over the period. This may mean either high "
            "quality, or that the tests check nothing — verify the second "
            "before rejoicing at the first.")
    if reviewed and reviewed < 30:
        out.append(
            "The confidence interval is wider than the estimate itself: there "
            "are too few reviewed failures.")
    return out


# --------------------------------------------------------------------------- #
#  Rendering
# --------------------------------------------------------------------------- #
def to_markdown(d: dict) -> str:
    ff = d["false_fail"]
    v = d["volume"]
    c = d["conditions"]
    lines: list[str] = []

    lines.append("# VisTest quality statistics")
    lines.append("")
    lines.append(f"Generated {d['generated_at']}. "
                 f"Period: {d['period']['from'] or '—'} — "
                 f"{d['period']['to'] or '—'} "
                 f"(last {d['days']} days).")
    lines.append("")

    lines.append("## Headline number")
    lines.append("")
    lines.append(f"**False failures: {ff['rate'] * 100:.1f}%** "
                 f"(95% CI: {ff['ci95'][0] * 100:.1f}–{ff['ci95'][1] * 100:.1f}%)")
    lines.append("")
    lines.append(f"Computed over {ff['reviewed']} reviewed failures out of "
                 f"{v['failed']}. {ff['confidence']}")
    lines.append("")
    lines.append("A failure is considered false when a person looked at it and "
                 "accepted it as a new baseline: the engine turned red where "
                 "the application did not break.")
    lines.append("")

    lines.append("## Volume")
    lines.append("")
    lines.append("| | |")
    lines.append("|---|---|")
    for label, key in (("Runs", "runs"), ("Comparisons", "comparisons"),
                       ("Unique snapshots", "snapshots"),
                       ("Passed", "passed"), ("Failed", "failed"),
                       ("New baselines", "new_baselines")):
        lines.append(f"| {label} | {v[key]} |")
    lines.append(f"| Reviewed failures | {ff['reviewed']} "
                 f"({ff['coverage'] * 100:.0f}% of all) |")
    lines.append("")

    if d["by_month"]:
        lines.append("## Trend")
        lines.append("")
        lines.append("| Month | Comparisons | Failed | Reviewed | False |")
        lines.append("|---|---|---|---|---|")
        for m in d["by_month"]:
            rate = ("—" if m["rate"] is None else f"{m['rate'] * 100:.0f}%")
            lines.append(f"| {m['period']} | {m['comparisons']} | "
                         f"{m['failed'] or 0} | {m['reviewed']} | {rate} |")
        lines.append("")

    if len(d["by_project"]) > 1:
        lines.append("## By project")
        lines.append("")
        lines.append("| Project | Comparisons | Failed | False | 95% CI |")
        lines.append("|---|---|---|---|---|")
        for row in d["by_project"]:
            rate = ("—" if row["rate"] is None else f"{row['rate'] * 100:.0f}%")
            ci = (f"{row['ci'][0] * 100:.0f}–{row['ci'][1] * 100:.0f}%"
                  if row["reviewed"] else "—")
            lines.append(f"| {row['project']} | {row['comparisons']} | "
                         f"{row['failed'] or 0} | {rate} | {ci} |")
        lines.append("")

    lines.append("## Measurement conditions")
    lines.append("")
    lines.append("Without them the number is not reproducible, which means it "
                 "is not verifiable.")
    lines.append("")
    lines.append("| Parameter | Value |")
    lines.append("|---|---|")
    labels = {
        "preset": "Preset", "delta_e_threshold": "ΔE00 threshold",
        "ssim_threshold": "SSIM threshold", "require_consensus": "Color∧structure consensus",
        "fail_severity": "Severity threshold", "max_changed_area_pct": "Area threshold, %",
        "align_enabled": "Alignment", "antialias_filter": "AA filter",
        "stability_shots": "Frames per snapshot",
    }
    for key, label in labels.items():
        lines.append(f"| {label} | `{c[key]}` |")
    lines.append("")

    if d["platforms"]:
        lines.append("| Platform | Browser | Runs |")
        lines.append("|---|---|---|")
        for row in d["platforms"]:
            lines.append(f"| {row['platform'] or '—'} | "
                         f"{row['browser'] or '—'} | {row['runs']} |")
        lines.append("")

    lines.append("## What this number does not prove")
    lines.append("")
    for note in d["caveats"]:
        lines.append(f"- {note}")
    lines.append("")
    lines.append("Comparison against competitors on an open corpus — "
                 "the benchmark section of README.md. There is the methodology and "
                 "reproducibility, here is practice on live data. One without "
                 "the other is incomplete.")
    lines.append("")
    return "\n".join(lines)


def _month_row(m: dict) -> str:
    rate = "—" if m.get("rate") is None else f"{m['rate'] * 100:.0f}%"
    return (f"<tr><td>{html.escape(str(m.get('period') or ''))}</td>"
            f"<td>{m.get('comparisons', 0)}</td>"
            f"<td>{m.get('failed') or 0}</td>"
            f"<td>{m.get('reviewed', 0)}</td>"
            f"<td>{rate}</td></tr>")


def to_html(d: dict) -> str:
    """The same document as a page: one file, not a single external request."""
    ff = d["false_fail"]
    v = d["volume"]

    rate = ff["rate"] * 100
    color = "var(--pass)" if rate < 5 else "var(--warn)" if rate < 20 else "var(--fail)"

    months = "".join(_month_row(m) for m in d["by_month"]) \
        or '<tr><td colspan="5" class="muted">no data</td></tr>'

    conditions = "".join(
        f"<tr><td>{html.escape(str(k))}</td><td><code>{html.escape(str(val))}</code></td></tr>"
        for k, val in d["conditions"].items())

    caveat_items = "".join(f"<li>{html.escape(x)}</li>" for x in d["caveats"])

    return _EVIDENCE_PAGE.format(
        generated=html.escape(d["generated_at"]),
        period_from=html.escape(str(d["period"]["from"] or "—")),
        period_to=html.escape(str(d["period"]["to"] or "—")),
        days=d["days"],
        rate=f"{rate:.1f}",
        color=color,
        ci_low=f"{ff['ci95'][0] * 100:.1f}",
        ci_high=f"{ff['ci95'][1] * 100:.1f}",
        reviewed=ff["reviewed"],
        failed=v["failed"],
        coverage=f"{ff['coverage'] * 100:.0f}",
        confidence=html.escape(ff["confidence"]),
        runs=v["runs"],
        comparisons=v["comparisons"],
        snapshots=v["snapshots"],
        passed=v["passed"],
        new_baselines=v["new_baselines"],
        months=months,
        conditions=conditions,
        caveats=caveat_items,
        raw=html.escape(json.dumps(d, ensure_ascii=False, indent=2)),
    )


_EVIDENCE_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>VisTest quality statistics</title>
<style>
  :root {{ --bg:#12141a; --panel:#171a21; --line:#272c37; --fg:#e7ebf3;
          --muted:#98a0b0; --pass:#2ecc71; --fail:#e74c3c; --warn:#f1c40f; }}
  @media print {{ :root {{ --bg:#fff; --panel:#fff; --fg:#1a1d24;
                          --line:#d7dae1; --muted:#5b6270; }} }}
  body {{ margin:0; background:var(--bg); color:var(--fg);
         font:14px/1.65 -apple-system,Segoe UI,Roboto,sans-serif; }}
  .wrap {{ max-width:900px; margin:0 auto; padding:30px 20px 60px; }}
  h1 {{ font-size:23px; margin:0 0 4px; }}
  h2 {{ font-size:16px; margin:28px 0 8px; }}
  .muted {{ color:var(--muted); font-size:12.5px; }}
  .hero {{ background:var(--panel); border:1px solid var(--line);
          border-radius:12px; padding:22px; margin:20px 0; text-align:center; }}
  .hero .big {{ font-size:52px; font-weight:800; line-height:1; color:{color}; }}
  .hero .label {{ font-size:13px; color:var(--muted); margin-top:6px; }}
  .hero .ci {{ font-size:13px; margin-top:10px; }}
  table {{ width:100%; border-collapse:collapse; margin:8px 0; }}
  th,td {{ text-align:left; padding:6px 9px; border-bottom:1px solid var(--line);
          font-size:13px; }}
  th {{ color:var(--muted); font-size:11.5px; font-weight:600; }}
  code {{ background:rgba(127,127,127,.14); padding:1px 5px; border-radius:4px;
         font-size:12px; }}
  ul {{ padding-left:20px; }} li {{ margin:6px 0; color:var(--muted);
        font-size:13px; }}
  details {{ margin-top:24px; }} summary {{ cursor:pointer; color:var(--muted);
        font-size:12.5px; }}
  pre {{ background:var(--panel); border:1px solid var(--line); border-radius:8px;
        padding:12px; overflow:auto; font-size:11px; max-height:420px; }}
  footer {{ margin-top:34px; border-top:1px solid var(--line); padding-top:14px;
           color:var(--muted); font-size:12px; }}
</style></head>
<body><div class="wrap">
<h1>VisTest quality statistics</h1>
<div class="muted">Generated {generated} · period {period_from} — {period_to}
  (last {days} days)</div>

<div class="hero">
  <div class="big">{rate}%</div>
  <div class="label">false failures</div>
  <div class="ci">95% confidence interval: {ci_low}–{ci_high}%</div>
  <div class="muted" style="margin-top:10px">
    Computed over {reviewed} reviewed failures out of {failed}
    ({coverage}% reviewed).<br>{confidence}
  </div>
</div>

<p>A failure is considered false when a person looked at it and accepted it as a
new baseline: the engine turned red where the application did not break. This is
a direct measure of whether a red test can be trusted.</p>

<h2>Volume</h2>
<table>
  <tr><th>Metric</th><th>Value</th></tr>
  <tr><td>Runs</td><td>{runs}</td></tr>
  <tr><td>Comparisons</td><td>{comparisons}</td></tr>
  <tr><td>Unique snapshots</td><td>{snapshots}</td></tr>
  <tr><td>Passed</td><td>{passed}</td></tr>
  <tr><td>Failed</td><td>{failed}</td></tr>
  <tr><td>New baselines</td><td>{new_baselines}</td></tr>
</table>

<h2>Trend by month</h2>
<table>
  <tr><th>Month</th><th>Comparisons</th><th>Failed</th><th>Reviewed</th>
      <th>False</th></tr>
  {months}
</table>

<h2>Measurement conditions</h2>
<p class="muted">Without them the number is not reproducible, which means it is
not verifiable.</p>
<table>
  <tr><th>Parameter</th><th>Value</th></tr>
  {conditions}
</table>

<h2>What this number does not prove</h2>
<ul>{caveats}</ul>

<details>
  <summary>Raw data (JSON)</summary>
  <pre>{raw}</pre>
</details>

<footer>
  Comparison against competitors on an open synthetic corpus — a separate
  document with methodology and reproducibility. Here is practice on live data.
  One without the other is incomplete.<br>
  The file is self-contained: there are no external requests.
</footer>
</div></body></html>
"""
