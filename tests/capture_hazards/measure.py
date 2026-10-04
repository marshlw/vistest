# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""The capture-hazard stand, measured: our capture against Playwright's.

    python -m tests.capture_hazards.measure                  # both tools, calibration seeds
    python -m tests.capture_hazards.measure --table --no-timing

For every hazard page (stand.py) and every calibration seed:

* **the baseline** — after `window.__ready` and 500 ms more, through the same
  path the checks take (ours: `expect_screenshot` writing the baseline;
  Playwright: `toHaveScreenshot()` under --update-snapshots);
* **20 checks**, each in a fresh browser context, the way a test takes them:
  `goto`, the previous step (hover or focus, scroll) when the page has one,
  and the assertion at once — ours `expect_screenshot` (engine v2, its
  defaults), Playwright's `toHaveScreenshot()` with its defaults;
* **the signal variant** — the same page, its data arriving with a real
  change after the load: every check of it must fail.

A check of the plain page that fails is a false failure; a check of the
signal variant that passes is a miss. The time is the assertion's own
(`expect_screenshot` / `toHaveScreenshot()`), not the page's load; for ours,
"frames" is the part of it the capture spent waiting for two identical frames.

Results go line by line to `<work>/results.<tool>.jsonl`; a run that was cut
off goes on where it stopped. The held-out seeds (10–19) are not run and not
printed unless `--held-out` is given — once, after the numbers of S2 are
chosen (REPORT_S1). Nothing here changes the library: it is only called.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

from . import server, stand

REPO = stand.HERE.parents[1]
DEFAULT_WORK = REPO / "bench_out" / "capture_hazards"
BENCH = REPO / "scripts" / "bench"
TOOLS = ("ours", "ours_plugin", "playwright")


# --------------------------------------------------------------------------- #
#  What is run
# --------------------------------------------------------------------------- #
def jobs(seeds, only: set[str] | None) -> list[tuple[stand.Hazard, int]]:
    return [(h, s) for h in stand.HAZARDS if not only or h.key in only for s in seeds]


def step_of(h: stand.Hazard, seed: int) -> dict:
    if h.step == "hover_focus":
        sel = stand.step_target(seed)
        return {"kind": "focus" if sel == "#search" else "hover", "selector": sel}
    if h.step == "scroll":
        return {"kind": "scroll", "y": stand.scroll_y(seed)}
    if h.step == "click":
        return {"kind": "click", "selector": h.click or stand.CLICK_TARGET}
    return {"kind": ""}


def counts(h: stand.Hazard) -> dict[str, int]:
    return {"normal": stand.SHOTS, "signal": stand.SIGNAL_SHOTS if h.signal else 0}


def _recorded(path: Path) -> dict[tuple, dict]:
    out = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                out[(r["hazard"], r["seed"], r["kind"], r["i"])] = r
    return out


def _complete(h, seed, rec) -> bool:
    return all((h.key, seed, kind, i) in rec for kind, n in counts(h).items()
               for i in range(n))


# --------------------------------------------------------------------------- #
#  Ours: expect_screenshot, as a test calls it
# --------------------------------------------------------------------------- #
def _do_step(page, step: dict) -> None:
    if step["kind"] == "hover":
        page.hover(step["selector"])
    elif step["kind"] == "focus":
        page.click(step["selector"])
    elif step["kind"] == "scroll":
        page.evaluate("y => window.scrollTo(0, y)", step["y"])
    elif step["kind"] == "click":
        page.click(step["selector"])


def _lateness(page, ended_ms: float) -> int | None:
    """When the check returned, minus when the page said it was ready (ms).

    Both on this machine's wall clock: the page's `performance.timeOrigin`
    plus the moment it set `window.__ready`, and Python's `time.time()` when
    the assertion returned. Negative — the picture was taken before the page
    was ready. None — the page never said so.
    """
    try:
        page.wait_for_function("window.__ready === true", timeout=stand.READY_TIMEOUT_MS)
        ready = page.evaluate("performance.timeOrigin + window.__events.ready")
    except Exception:  # noqa: BLE001 - a page that never got ready has no lateness
        return None
    return int(round(ended_ms - float(ready)))


def _newest_part(parts: Path) -> dict:
    newest, best = {}, -1.0
    for p in parts.glob("*.json"):
        try:
            row = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if row.get("written_at", 0) > best:
            newest, best = row, row.get("written_at", 0)
    return newest


#: What the second look said, by the head of its note: the S1 library's words,
#: then S2a's (core/retry.py).
SECOND_LOOK = (("Failed on the first capture and passed on the second", "passed"),
               ("A second capture was taken", "taken"),
               ("Confirmed on a second capture", "confirmed"),
               ("Failed on the first capture and passed on a later one", "later frame passed"),
               ("A second look was taken", "later frame failed"),
               ("The page was not ready", "not ready: none"))


def _second_of(notes) -> str:
    said = ""
    for n in notes or ():
        for head, tag in SECOND_LOOK:
            if n.startswith(head):
                said = tag
        if "kept changing" in n:
            return "live, named"
    return said


def _second(res) -> str:
    return _second_of(list(getattr(res, "notes", None) or ()))


def _tracker(tool: str):
    """For `ours_plugin`: what the pytest plugin does to every new context.

    The plugin of the library hangs a count of requests in flight on each
    browser context when it is created (`vistest.capture.inflight.track`);
    the stand creates its contexts itself, so it calls the same function on
    each. `ours` leaves contexts alone: the library then sees only the page.
    """
    if tool != "ours_plugin":
        return lambda context: None
    from vistest.capture.inflight import track

    return track


def _ours_job(browsers, base: str, h: stand.Hazard, seed: int, work: Path,
              results: Path, tool: str = "ours") -> None:
    from vistest import expect_screenshot
    from vistest.core.engines import region_words
    from vistest.library import context as _context
    from vistest.library.errors import ScreenshotMismatch

    root = work / tool / f"{h.key}-{seed}"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    ctx = _context.LibraryContext(root=root, config_path=str(work / "no-vistest.yaml"))
    _context.install(ctx)
    browser = browsers[h.launch]
    track = _tracker(tool)
    name = f"{h.key}.png"
    mask = list(h.mask) or None
    step = step_of(h, seed)
    examples = work / "examples" / tool
    try:
        c = browser.new_context(viewport=stand.VIEWPORT)
        track(c)
        p = c.new_page()
        p.goto(stand.page_url(base, h, seed))
        if h.step_in_baseline:
            _do_step(p, step)
        p.wait_for_function("window.__ready === true", timeout=stand.READY_TIMEOUT_MS)
        p.wait_for_timeout(stand.AFTER_READY_MS)
        ctx.update = True
        expect_screenshot(p.locator(h.target) if h.target else p, name, mask=mask,
                          **dict(h.call))
        ctx.update = False
        c.close()
        for kind, n in counts(h).items():
            for i in range(n):
                c = browser.new_context(viewport=stand.VIEWPORT)
                track(c)
                p = c.new_page()
                p.goto(stand.page_url(base, h, seed, signal=kind == "signal"))
                _do_step(p, step)
                target = p.locator(h.target) if h.target else p
                started = time.perf_counter()
                failed, error, res = False, "", None
                try:
                    res = expect_screenshot(target, name, mask=mask, **dict(h.call))
                except ScreenshotMismatch as e:
                    failed, res = True, e.result
                except Exception as e:  # noqa: BLE001 - every outcome is a row
                    failed, error = True, f"{type(e).__name__}: {str(e).splitlines()[0]}"
                ended = time.time() * 1000
                ms = int((time.perf_counter() - started) * 1000)
                late = _lateness(p, ended)
                row = _newest_part(ctx.parts_dir)
                cap = row.get("capture") or {}
                rec = {"tool": tool, "hazard": h.key, "seed": seed, "kind": kind, "i": i,
                       "failed": failed, "ms": ms, "late_ms": late,
                       "frames": cap.get("frames"),
                       "stable": cap.get("stable"), "settle_ms": cap.get("elapsed_ms"),
                       "ready": cap.get("ready"),
                       "renderer": (row.get("renderer") or {}).get("status"),
                       "regions": len(res.regions) if res is not None else None,
                       "first": (region_words(res.regions[0])
                                 if res is not None and res.regions else ""),
                       #  The library's second look (core/retry.py), in its
                       #  own words: the notes it left on the result.
                       "second_look": _second(res),
                       "notes": [n[:160] for n in (getattr(res, "notes", None) or [])],
                       "reason": (row.get("reason") or "")[:3000],
                       "error": error}
                with open(results, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                if failed and kind == "normal" and i == 0:
                    examples.mkdir(parents=True, exist_ok=True)
                    for which in ("actual", "diff", "baseline"):
                        src = (row.get("images") or {}).get(which)
                        if src and Path(src).is_file():
                            shutil.copyfile(src, examples / f"{h.key}-{seed}-{which}.png")
                c.close()
    finally:
        _context.uninstall()


def run_ours(todo, work: Path, worker: int, of: int, tool: str = "ours") -> None:
    from playwright.sync_api import sync_playwright

    results = work / f"results.{tool}.{worker}.jsonl"
    rec = _recorded(results)
    mine = [(h, s) for n, (h, s) in enumerate(todo) if n % of == worker]
    os.chdir(work)                       # no vistest.yaml of anybody's
    with server.serve() as base, sync_playwright() as pw:
        browsers = {"default": pw.chromium.launch(),
                    "scrollbars": pw.chromium.launch(ignore_default_args=["--hide-scrollbars"])}
        try:
            for h, seed in mine:
                if _complete(h, seed, rec):
                    continue
                _ours_job(browsers, base, h, seed, work, results, tool)
                print(f"{tool} {h.key} {seed} done", flush=True)
        finally:
            for b in browsers.values():
                b.close()


# --------------------------------------------------------------------------- #
#  Playwright: toHaveScreenshot(), through its own test runner
# --------------------------------------------------------------------------- #
def chromium_executable() -> str:
    """The headless shell the Python side launches, for Node to launch too."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        full = Path(pw.chromium.executable_path)
    rev = next((p.name.split("-")[-1] for p in full.parents if p.name.startswith("chromium-")),
               "")
    root = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or full.parents[2])
    for shell in sorted(root.glob(f"chromium_headless_shell-{rev}/*/headless_shell")):
        return str(shell)
    return str(full)


def run_playwright(todo, work: Path, workers: int) -> int:
    if not (BENCH / "node_modules" / "@playwright" / "test").is_dir():
        print("playwright: scripts/bench/node_modules is missing — npm ci --prefix "
              "scripts/bench", file=sys.stderr)
        return 2
    pw_dir = work / "pw"
    pw_dir.mkdir(parents=True, exist_ok=True)
    for f in ("hazards.spec.mjs", "playwright.config.mjs"):
        shutil.copyfile(stand.HERE / "pw" / f, pw_dir / f)
    link = pw_dir / "node_modules"
    if not link.exists():
        link.symlink_to(BENCH / "node_modules", target_is_directory=True)
    #  A row that only exists for an option of ours is not Playwright's.
    todo = [(h, s) for h, s in todo if not h.call]
    with server.serve() as base:
        listing = [{"key": h.key, "seed": s, "url": stand.page_url(base, h, s),
                    "signal_url": stand.page_url(base, h, s, signal=True),
                    "target": h.target, "step": step_of(h, s), "mask": list(h.mask),
                    "launch": h.launch, "step_in_baseline": h.step_in_baseline,
                    "shots": counts(h)["normal"],
                    "signal_shots": counts(h)["signal"],
                    "after_ready_ms": stand.AFTER_READY_MS,
                    "ready_timeout_ms": stand.READY_TIMEOUT_MS} for h, s in todo]
        (pw_dir / "jobs.json").write_text(json.dumps(listing), encoding="utf-8")
        env = {**os.environ, "NODE_PATH": "", "HAZARD_JOBS": str(pw_dir / "jobs.json"),
               "HAZARD_RESULTS": str(work / "results.playwright.jsonl"),
               "HAZARD_CHROMIUM": chromium_executable(), "HAZARD_WORKERS": str(workers)}
        cli = [shutil.which("node") or "node", "node_modules/@playwright/test/cli.js", "test",
               "-c", "playwright.config.mjs"]
        for grep, update in (("@baseline", "all"), ("@shot", "none")):
            done = subprocess.run([*cli, "--grep", grep, f"--update-snapshots={update}"],
                                  cwd=pw_dir, env=env, check=False)
            if done.returncode not in (0, 1):
                return done.returncode
    return 0


# --------------------------------------------------------------------------- #
#  What the launch does with scroll bars
# --------------------------------------------------------------------------- #
def scrollbar_facts() -> list[str]:
    from playwright.sync_api import sync_playwright

    h = stand.BY_KEY["scrollbar"]
    out = []
    with server.serve() as base, sync_playwright() as pw:
        for label, kw in (("Playwright's headless launch", {}),
                          ("the same without --hide-scrollbars",
                           {"ignore_default_args": ["--hide-scrollbars"]})):
            b = pw.chromium.launch(**kw)
            p = b.new_page(viewport=stand.VIEWPORT)
            p.goto(stand.page_url(base, h, 0))
            p.wait_for_function("window.__ready === true", timeout=stand.READY_TIMEOUT_MS)
            width = p.evaluate("window.innerWidth - document.documentElement.clientWidth")
            version = b.version
            b.close()
            out.append(f"{label}: scroll bar {width} px")
    driver = Path(sys.modules["playwright"].__file__).parent / "driver" / "package" / "lib"
    passes = any("--hide-scrollbars" in p.read_text(encoding="utf-8", errors="ignore")
                 for p in driver.rglob("chromium.js"))
    node = (BENCH / "node_modules" / "playwright-core" / "lib" / "coreBundle.js")
    passes_node = node.is_file() and "--hide-scrollbars" in node.read_text(
        encoding="utf-8", errors="ignore")
    return [f"Chromium {version}: " + "; ".join(out) + ". --hide-scrollbars is added by "
            f"Playwright itself in headless mode (Python driver: {'yes' if passes else 'no'}, "
            f"@playwright/test 1.63: {'yes' if passes_node else 'no'}); a headed browser "
            "(headless=False) draws the bar."]


# --------------------------------------------------------------------------- #
#  The table
# --------------------------------------------------------------------------- #
def _p95(xs):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(0.95 * (len(xs) - 1))))] if xs else 0


def load(work: Path) -> dict[str, dict[tuple, dict]]:
    """Every tool's rows in one work directory, keyed (hazard, seed, kind, i)."""
    out = {}
    for tool in TOOLS:
        rec: dict[tuple, dict] = {}
        for path in sorted(work.glob(f"results.{tool}.*jsonl")):
            rec.update(_recorded(path))
        out[tool] = rec
    return out


#: Column labels: a tool of this work directory, or the library before (--before).
LABELS = {"before": "before", "ours": "ours", "ours_plugin": "ours+plugin",
          "playwright": "Playwright"}


def columns(work: Path, before: list[Path]) -> list[tuple[str, dict]]:
    """(label, rows) for each column of the table, left to right.

    `before` — work directories of a run of the library as it was (its `ours`
    rows, and its Playwright rows for hazards this run has none of): the
    «before» column, and Playwright's, when this run did not repeat them.
    """
    now = load(work)
    cols: list[tuple[str, dict]] = []
    if before:
        old: dict = {}
        old_pw: dict = {}
        for d in before:
            rec = load(d)
            old.update(rec["ours"])
            old_pw.update(rec["playwright"])
        cols.append(("before", old))
        for k, r in old_pw.items():
            now["playwright"].setdefault(k, r)
    for tool in TOOLS:
        if now[tool]:
            cols.append((tool, now[tool]))
    return cols


def _cell(rows, kind):
    rows = [r for r in rows if r["kind"] == kind]
    if not rows:
        return "—"
    bad = sum(r["failed"] for r in rows) if kind == "normal" else \
        sum(not r["failed"] for r in rows)
    return f"{bad}/{len(rows)}"


def _med(xs) -> str:
    xs = [x for x in xs if x is not None]
    return f"{statistics.median(xs):.0f}" if xs else "—"


def table(work: Path, seeds, *, timing: bool, facts: list[str],
          before: list[Path] | None = None) -> str:
    cols = columns(work, before or [])
    seeds = list(seeds)
    L = [f"=== Capture hazards: seeds {seeds[0]}–{seeds[-1]} "
         f"({'held out' if seeds[0] in stand.HELD_OUT else 'calibration' if seeds[0] in stand.CALIBRATION else 'pilot'}) ===",
         f"{len(seeds)} seeds × {stand.SHOTS} checks of the plain page, × "
         f"{stand.SIGNAL_SHOTS} of the signal variant; {stand.VIEWPORT['width']}x"
         f"{stand.VIEWPORT['height']} at 1x; baseline after window.__ready + "
         f"{stand.AFTER_READY_MS} ms",
         "ours: vistest expect_screenshot (engine v2, defaults), without the pytest "
         "plugin; ours+plugin: the same, with the plugin's count of requests in flight; "
         "before: the library before S2; Playwright: @playwright/test 1.63.0 "
         "toHaveScreenshot(), defaults, same Chromium",
         *facts, ""]

    def rows_of(rec, h):
        return [r for k, r in rec.items() if k[0] == h.key and k[1] in seeds
                and k[2] in ("normal", "signal")]

    head = f"{'hazard':18s}" + "".join(
        f" | {LABELS[label] + ' false':>17s} {'misses':>7s}" for label, _ in cols)
    L += ["False failures (plain page) and misses (signal variant)", head, "-" * len(head)]
    for h in stand.HAZARDS:
        line = f"{h.key:18s}"
        for _, rec in cols:
            rows = rows_of(rec, h)
            line += f" | {_cell(rows, 'normal'):>17s} {_cell(rows, 'signal'):>7s}"
        L.append(line)

    if timing:
        head = f"{'hazard':18s}" + "".join(
            f" | {LABELS[label] + ' ms med/p95':>22s} {'late':>6s}" for label, _ in cols)
        L += ["", "Time of the check, ms (median/p95, plain page), and its lateness: the "
              "median of (returned − the page's window.__ready), ms", head, "-" * len(head)]
        for h in stand.HAZARDS:
            line = f"{h.key:18s}"
            for _, rec in cols:
                normal = [r for r in rows_of(rec, h) if r["kind"] == "normal"]
                ms = [r["ms"] for r in normal]
                cell = f"{statistics.median(ms):.0f}/{_p95(ms):.0f}" if ms else "—"
                line += f" | {cell:>22s} {_med(r.get('late_ms') for r in normal):>6s}"
            L.append(line)

    L += ["", "What failed (plain page): ours — the first region in v2's words, most "
          "common; Playwright — its message, most common"]
    for h in stand.HAZARDS:
        for label, rec in cols:
            rows = [r for r in rows_of(rec, h) if r["kind"] == "normal" and r["failed"]]
            if not rows:
                continue
            if label != "playwright":
                said = Counter((r.get("first") or r.get("error") or "?")[:110] for r in rows)
                unstable = sum(r.get("stable") is False for r in rows)
                extra = f"; capture said «did not settle» in {unstable}" if unstable else ""
            else:
                said = Counter(_pw_words(r.get("error", "")) for r in rows)
                extra = ""
            top = "; ".join(f"{w} ×{n}" for w, n in said.most_common(2))
            L.append(f"  {h.key} / {LABELS[label]}: {len(rows)} — {top}{extra}")

    L += ["", "The second look (core/retry.py), by what it said — plain page / signal "
          "variant; a check of the signal variant it turned green is a miss"]
    for h in stand.HAZARDS:
        for label, rec in cols:
            if label == "playwright":
                continue
            said = Counter()
            for r in rows_of(rec, h):
                tag = _second_of(r["notes"]) if "notes" in r else r.get("second_look")
                if tag:
                    said[(tag, r["kind"])] += 1
            if said:
                kinds = sorted({w for w, _ in said})
                text = "; ".join(f"{w} {said[(w, 'normal')]}/{said[(w, 'signal')]}"
                                 for w in kinds)
                L.append(f"  {h.key} / {LABELS[label]}: {text}")

    pw = dict(cols).get("playwright", {})
    which = Counter(k[0] for k in pw if k[2] == "baseline" and k[1] in seeds)
    if which:
        L += ["", "Playwright's baseline was one frame (toHaveScreenshot() found no two equal "
              "ones): " + ", ".join(f"{k} ×{n}" for k, n in sorted(which.items()))]
    return "\n".join(L) + "\n"


def _pw_words(message: str) -> str:
    import re

    m = re.search(r"(\d+) pixels \(ratio ([\d.]+) of all image pixels\) are different", message)
    unstable = "stable screenshot" in message or "Timeout" in message
    if m:
        n = int(m.group(1))
        size = ("under 100 px" if n < 100 else "100–1000 px" if n < 1000
                else "1000–10000 px" if n < 10000 else "10000 px or more")
        return f"pixels differ, {size}" + (" (no stable screenshot)" if unstable else "")
    if "sizes differ" in message or "Expected an image" in message:
        return "the sizes differ"
    if unstable:
        return "no stable screenshot within the timeout"
    return message[:90] or "?"


# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tests.capture_hazards.measure")
    ap.add_argument("--tool", choices=("ours", "ours_plugin", "playwright", "both", "all"),
                    default="both",
                    help="both: ours and Playwright; all: ours, ours_plugin and Playwright")
    ap.add_argument("--before", default="",
                    help="work directories of a run of the library before, comma-separated: "
                         "the «before» column (and Playwright's rows this run lacks)")
    ap.add_argument("--only", help="hazards, comma-separated (stand.py keys)")
    ap.add_argument("--held-out", action="store_true",
                    help="the held-out seeds 10–19 instead of the calibration — once, "
                         "after the numbers of S2 are chosen")
    ap.add_argument("--seeds", default="",
                    help="a pilot on other seeds, e.g. 100-103 — never 0–19, which are the "
                         "calibration and the held-out")
    ap.add_argument("--jobs", type=int, default=2, help="processes / Playwright workers")
    ap.add_argument("--work", default=str(DEFAULT_WORK))
    ap.add_argument("--table", action="store_true", help="only print the table")
    ap.add_argument("--no-timing", action="store_true", help="leave the times out")
    ap.add_argument("--worker", type=int, help=argparse.SUPPRESS)
    ap.add_argument("--as", dest="as_tool", default="ours", help=argparse.SUPPRESS)
    ap.add_argument("--smoke", action="store_true",
                    help="one seed, two checks and one signal check: does it run at all")
    args = ap.parse_args(argv)

    seeds = stand.HELD_OUT if args.held_out else stand.CALIBRATION
    if args.seeds:
        lo, _, hi = args.seeds.partition("-")
        seeds = tuple(range(int(lo), int(hi or lo) + 1))
    if args.smoke:
        seeds = seeds[:1]
        stand.SHOTS, stand.SIGNAL_SHOTS = 2, 1
    work = Path(args.work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    only = set(args.only.split(",")) if args.only else None
    todo = jobs(seeds, only)

    if args.worker is not None:
        run_ours(todo, work, args.worker, args.jobs, args.as_tool)
        return 0
    if not args.table:
        ours = {"ours": ["ours"], "ours_plugin": ["ours_plugin"], "both": ["ours"],
                "all": ["ours", "ours_plugin"]}.get(args.tool, [])
        for tool in ours:
            procs = [subprocess.Popen([sys.executable, "-m", "tests.capture_hazards.measure",
                                       "--worker", str(k), "--as", tool,
                                       "--jobs", str(args.jobs),
                                       "--work", str(work), *(["--held-out"] if args.held_out
                                                              else []),
                                       *(["--only", args.only] if args.only else []),
                                       *(["--seeds", args.seeds] if args.seeds else []),
                                       *(["--smoke"] if args.smoke else [])],
                                      cwd=REPO) for k in range(args.jobs)]
            if any(p.wait() for p in procs):
                return 1
        if args.tool in ("playwright", "both", "all"):
            code = run_playwright(todo, work, args.jobs)
            if code:
                return code
    facts_file = work / "scrollbar_facts.txt"
    if not facts_file.is_file():
        facts_file.write_text("\n".join(scrollbar_facts()) + "\n", encoding="utf-8")
    facts = facts_file.read_text(encoding="utf-8").splitlines()
    before = [Path(d).resolve() for d in args.before.split(",") if d]
    sys.stdout.write(table(work, list(seeds), timing=not args.no_timing, facts=facts,
                           before=before))
    return 0


if __name__ == "__main__":
    sys.exit(main())
