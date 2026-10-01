# The capture-hazard stand

A check photographs the page the moment the test asks for it. On a real page
that moment is often too early, or in the wrong state: the data is still
under a spinner, the font has not arrived, a banner is about to push
everything down, the previous step left the pointer on a button. This stand
reproduces those hazards on local pages, served with real network delays,
and counts what each tool does with them: our `expect_screenshot` and
Playwright's `toHaveScreenshot()`, both with their defaults, both in the same
Chromium.

Nothing in the library, the capture or the engine is changed for this, and
none of them reads the pages' readiness flag: the stand only calls them.

## Commands

```bash
python -m tests.capture_hazards.measure                 # both tools, seeds 0–9: ~1 h ours + ~35 min Playwright, 2 cores
python -m tests.capture_hazards.measure --table --no-timing   # print the table again, deterministic
pytest -m capture_hazards tests/capture_hazards         # the oracle test (below), about a minute
```

`--tool ours|playwright`, `--only spinner,web_font`, `--jobs N`,
`--work DIR` (default `bench_out/capture_hazards/`, ignored by git) and
`--smoke` (one seed, two checks, one signal check) narrow a run. Results are
written line by line to `<work>/results.<tool>*.jsonl`; a run that was cut
off goes on where it stopped when started again with the same `--work`.

Playwright's side runs `@playwright/test` from `scripts/bench/node_modules`
(`npm ci --prefix scripts/bench`) with the headless shell the Python
Playwright launches, so both photograph with one renderer.

## The pages

Every page is in `pages/`, served by `server.py` (`127.0.0.1`, a free port).
Every delay is the server holding its answer — real network, not a timer in
the page — and comes from the seed (`stand.delay_ms`, 50–1500 ms).

| hazard | what makes a picture of it wrong |
|---|---|
| `spinner` | data by request, under a spinning CSS spinner |
| `skeleton` | data by request, under a shimmering skeleton |
| `lazy_images` | lazy images in the frame and below it, each answered late |
| `web_font` | a web font answered late, `font-display: swap`, the text inserted after `load` |
| `late_banner` | a banner by request, inserted on top, moving the page down |
| `scrollbar` | rows by request make the page scroll; centred layout; Playwright's headless launch |
| `scrollbar_visible` | the same without `--hide-scrollbars`: the scroll bar appears late |
| `hover_focus` | the previous step left the pointer on a control or the focus in a field |
| `scrolled` | an element photographed after the page was scrolled somewhere; sticky header, fixed background |
| `raf_canvas` | a canvas animated by `requestAnimationFrame`, for ever |
| `raf_canvas_masked` | the same, the canvas masked |
| `counter` | a number `setInterval` changes every 200 ms (the control: only a mask helps) |
| `counter_masked` | the same, the number masked |

Each page sets `window.__ready = true` when its hazard is over (the data in
and painted, the font loaded, the banner inserted, …). That flag is the
oracle for the baseline; neither tool reads it during a check. The pages that
never hold still (`raf`, `counter`) set it once loaded: their hazard does not
end, and waiting cannot help there.

`test_oracle.py` checks the oracle without the library: a watcher installed
before the page's scripts tests a condition of its own, from the DOM, on every
animation frame, and the test asserts that the condition holds when `__ready`
turns true, that `__ready` follows it within 150 ms, and — where the server
holds a request — that the condition did not hold sooner than that hold.

## The protocol

For every hazard and every seed:

* **the baseline**, after `window.__ready` and 500 ms more, through the same
  path a check takes (ours: `expect_screenshot` writing it; Playwright:
  `toHaveScreenshot()` under `--update-snapshots=all`);
* **20 checks**, each in a fresh browser context: `goto`, the previous step
  when the page has one, and the assertion at once;
* **5 checks of the signal variant**: the same page whose late data carries
  a real change (a number, a colour, a row, a picture, a line of text).

A failed check of the plain page is a false failure; a passed check of the
signal variant is a miss. The time is the assertion's own, not the page's
load.

**Seeds.** 0–9 are the calibration: everything chosen from these numbers (the
readiness step, S2) is chosen on them. 10–19 are held out: `--held-out` runs
them once, after that choice, and the table prints them only then.

**Playwright's baseline on a page that never holds still.** `toHaveScreenshot()`
writes a baseline only from two equal consecutive screenshots; on `raf_canvas`
and `counter` it finds none. The spec then takes one frame with the same
options and records that in the results (`kind: baseline`); the table says so.

**Scroll bars.** Playwright launches headless Chromium with
`--hide-scrollbars`; the table's header says what the bar measures in both
launches on the machine it ran on. `scrollbar_visible` is the page as a headed
browser, or a launch without that flag, draws it.

## The output

One row per hazard: false failures out of the plain page's checks and misses
out of the signal variant's, for each tool, and — unless `--no-timing` — the
median and 95th percentile of the assertion's time, and for ours the part of
it the capture spent waiting for two identical frames. Below it, what failed in
each tool's own words, and how often our library's second look (a failed
first frame compared again with what moved between two frames masked) turned
a failure into a pass. Without the times the output depends only on the
results files.
