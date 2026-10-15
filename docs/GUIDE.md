# VisTest library guide

The library in full: `expect_screenshot`, its pytest plugin, the
`vistest.yaml` keys it reads, the files it writes, and its commands. The
[README](../README.md) is the short version. The self-hosted server has its own
guide, in Russian: [docs/server/GUIDE.ru.md](server/GUIDE.ru.md).

- [Install](#install)
- [A first check](#a-first-check)
- [`expect_screenshot`](#expect_screenshot)
- [pytest: flags and fixtures](#pytest-flags-and-fixtures)
- [vistest.yaml](#vistestyaml)
- [Baselines: layout, platform, names](#baselines-layout-platform-names)
- [Readiness before the frames, and a second look after a failure](#readiness-before-the-frames-and-a-second-look-after-a-failure)
- [`timeout_ms`, and what a hung page costs](#timeout_ms-and-what-a-hung-page-costs)
- [Masks and `data-vistest="ignore"`](#masks-and-data-vistestignore)
- [The engine](#the-engine)
- [The report](#the-report)
- [Commands](#commands)
- [Windows](#windows)
- [Measuring the capture](#measuring-the-capture)

---

## Install

```bash
pip install "vistest[browser]"
playwright install chromium
```

`[browser]` installs Playwright and `pytest-playwright` (the `page` fixture);
the second line downloads Chromium. Python 3.10–3.14. The package alone —
`pip install vistest` — compares pictures it is handed (PNG bytes, files,
arrays) and needs no browser.

Pin Playwright where the pictures must not move: a new Playwright brings a new
Chromium, and a new Chromium draws some pixels differently. In CI pin it
together with the runner's system — `ubuntu-24.04` with `playwright==1.56.0`,
or a container image of exactly that Playwright
(`mcr.microsoft.com/playwright/python:v1.56.0-noble` with `playwright==1.56.0`).

## A first check

Under pytest, with `pytest-playwright`:

```python
from vistest import expect_screenshot


def test_home(page):
    page.goto("https://your-app.example/")
    expect_screenshot(page, "home.png")
```

The first run fails with `BaselineMissing`; `pytest --vistest-update` writes
the baselines; you look at them and commit `tests/__vistest__/`; the next runs
compare. The README walks through it with
[`examples/test_library.py`](../examples/test_library.py).

**Without pytest** — a script, `unittest`, your own runner — the call is the
same. Nothing installs the plugin, so the settings come from the environment
and `vistest.yaml`, and a missing baseline is written with
`VISTEST_UPDATE_BASELINES=1` in the environment instead of the flag. The
readiness wait does not count the page's requests on this path (see
[Readiness](#readiness-before-the-frames-and-a-second-look-after-a-failure)).

**Selenium or anything else** that gives you a picture: pass the PNG bytes.

```python
expect_screenshot(driver.get_screenshot_as_png(), "home.png",
                  platform="linux-chromium-1x-1440x900")
```

A picture handed in gets none of the waiting and frames a live page gets, and
carries no platform: name one, or its baselines go to the root of the baseline
directory for every machine (see [the platform](#baselines-layout-platform-names)).

**Playwright's async API** is refused by name: `expect_screenshot` drives the
sync API. With an async page, take the picture yourself:
`expect_screenshot(await page.screenshot(), "home.png")`.

## `expect_screenshot`

```python
expect_screenshot(target, name, *, platform=None, fail_severity=None,
                  max_changed_area_pct=None, mask=None, full_page=None,
                  store=None, scale="css", keep_pointer=None, blur_focus=None,
                  timeout_ms=None) -> CompareResult
```

| Parameter | What it is |
| --- | --- |
| `target` | a Playwright `Page` or `Locator` (sync API), PNG bytes, a `PIL.Image`, a numpy array (H×W×3, RGB, uint8) or a path to a PNG. A page is recognised by its `screenshot` method, so a project's own page wrapper works and Playwright is never imported to find out |
| `name` | the snapshot's name, a relative path inside the baseline directory: `home.png`, `shop/checkout.png`. `.png` is added when missing. An empty name, an absolute path and `..` are refused |
| `platform` | the platform directory of the baseline, e.g. `linux-chromium-1x-1440x900`. Left out, it is taken from the page; a target that is not a page has none |
| `fail_severity` | the severity, 0–100, at or above which a change the engine could not explain fails the check. Not set: any such change fails |
| `max_changed_area_pct` | the share of the picture, in %, that fails the check whatever the severity. Default 0.15 |
| `mask` | CSS selectors and Locators, painted over before the picture as Playwright's `mask=` does; boxes `(x, y, w, h)`, left out of the comparison and not painted |
| `full_page` | for a `Page`: the whole page instead of the window. Off by default; meaningless for a Locator |
| `store` | where the baselines are read and written, when it is not the directory the context names — for tests of your own tooling |
| `scale` | `"css"` (default): one picture pixel per CSS pixel on any screen; `"device"`: the screen's own pixels. The platform key carries it (`1x`, `2x`), so the two never share a baseline |
| `keep_pointer` | `True` keeps the pointer where the test left it — for a hover captured on purpose. By default it is moved off the page |
| `blur_focus` | `True` takes the focus off whatever has it. By default the focus is left alone |
| `timeout_ms` | how long the whole check may spend on the page; see [`timeout_ms`](#timeout_ms-and-what-a-hung-page-costs) |

`fail_severity`, `max_changed_area_pct`, `keep_pointer`, `blur_focus` and the
two capture limits have the same names in `vistest.yaml`; the call is the
innermost layer ([Where a setting comes from](#where-a-setting-comes-from)).

**It returns** the `CompareResult` of a check that passed: the verdict, the
regions, the changed area and the timings.

**It raises:**

| Exception | When |
| --- | --- |
| `BaselineMissing` | there is no baseline for this name and platform yet. An `AssertionError` |
| `ScreenshotMismatch` | the picture differs beyond what the engine explains and the thresholds allow. An `AssertionError` |
| `CaptureError` | the page is closed, crashed, or did not answer in time. A `RuntimeError` |
| `VisualCheckError` | the base class of the two `AssertionError`s above |

Both `AssertionError`s name every file involved: the baseline, the picture
taken, the diff, the report. `VisTestWarning` is the category of the library's
warnings — a key it does not read, a target with no platform — so a project
can filter them by it (`filterwarnings = ["ignore::vistest.VisTestWarning"]`
under `[tool.pytest.ini_options]`). Do not turn them into errors: the warning
about unread keys is given while pytest starts, and an error there stops the
run with a traceback instead of a line.

`vistest.compare(expected, actual)` compares two RGB arrays (uint8, H×W×3)
with the same engine and returns a `CompareResult` (`.verdict` is a `Verdict`:
`pass`, `fail`, `new_baseline`, `error`); `ignore_mask=` takes a boolean
array of what not to look at. No baselines, no files, no browser, and no
threshold: every change no rule explains fails. A threshold is
`expect_screenshot`'s (or `vistest check` with vistest.yaml).

## pytest: flags and fixtures

| Flag | `pyproject.toml` | What it does |
| --- | --- | --- |
| `--vistest-update[=MODE]` | — | accept screenshots as baselines: `missing` — only absent ones; `changed` (default, also the bare flag) — absent and failed; `all` — everything that differs by a byte |
| `--vistest-baselines=PATH` | `vistest_baselines` | where the baselines live (default `tests/__vistest__`, or `__vistest__` without a `tests/` folder) |
| `--vistest-platform=NAME` | `vistest_platform` | the platform directory, e.g. `linux-chromium-1x-1440x900`; taken from the page when not set |
| `--vistest-report=PATH` | `vistest_report` | where the HTML report goes (default `.vistest/report/index.html`) |
| `--vistest-config=PATH` | — | the vistest.yaml to read, when it is not the one found from the working directory up to the repository's root |

A bare `--vistest-update` takes the next argument as its mode, so write paths
first — `pytest tests/ --vistest-update` — or spell the mode out. A path in the
mode's place is refused with that advice rather than guessed at. After an
update run one line names the directory the baselines were written to.

The ini keys go into `pyproject.toml`:

```toml
[tool.pytest.ini_options]
vistest_baselines = "tests/__vistest__"
vistest_platform = ""            # empty: taken from the page
```

Two fixtures:

- `vistest` — `expect_screenshot` itself, for a suite that would rather ask
  for a fixture (and override it in one place with its own defaults);
- `vistest_config` — the loaded configuration of this run.

The fixtures `visual`, `visual_soft` and the flags `--vistest-api`,
`--vistest-perceptual`, `--vistest-fail-on` are the server's: they exist only
with `pip install "vistest[server]"`.

`pytest -n auto` is supported: every write goes through a temporary file and
an atomic rename, there is no shared index and no lock, and the report is
assembled once at the end from the rows each worker left. Give two tests that
check one page two names: the same name on the same platform is one baseline
and one set of pictures.

## vistest.yaml

Optional. Looked for in the working directory and up to the repository's root
(the folder with `.git`), or named with `--vistest-config`. A broken file stops
the run before any test, with one line naming the file and the key.
[`examples/vistest.yaml`](../examples/vistest.yaml) is a starting point with
every key below commented.

The keys the library reads:

| Key | Default | What it does |
| --- | --- | --- |
| `diff.fail_severity` | not set | the severity (0–100) at or above which a change the engine could not explain fails. Not set: any such change fails. Below it, a change is listed in the message and the report, with where the threshold came from, and does not fail |
| `diff.max_changed_area_pct` | 0.15 | the share of the picture, in %, that fails the check whatever the severity: a faint change over a whole paragraph is more often real |
| `diff.max_pixels` | 80 000 000 | the largest picture the engine agrees to hold in memory; a larger one fails with that reason |
| `diff.size_tolerance_px` | 0 | how many pixels the picture's size may change by before that alone fails the check |
| `diff.fail_on_size_change` | true | whether a size change beyond the tolerance fails the check at all |
| `diff.ignore_kinds` | `[noise, antialias]` | kinds of change that never fail: they are listed as suppressed, with this key named |
| `diff.above_fold_px` | 900 | where the first screen ends, for the weight below |
| `diff.above_fold_weight` | 1.5 | how much more a change on the first screen weighs in its severity |
| `capture.stable_timeout_ms` | 5000 | how long frames are taken until two in a row are identical; 0 — one frame |
| `capture.ready_timeout_ms` | 5000 | how long each readiness step may wait; 0 — no wait |
| `capture.quiet_ms` | 40 | how long nothing may change in the photographed area before the picture |
| `capture.track_requests` | true | count the page's requests from the moment the browser context is created, so the wait knows what is in flight (the pytest plugin does it) |
| `capture.ignore_requests` | `[]` | requests the wait never waits for: glob patterns against the URL without its query — a long poll, an analytics beacon |
| `capture.retry_on_fail` | true | the second look: more frames for a check that failed |
| `capture.keep_pointer` | false | keep the pointer where the test left it instead of moving it off the page |
| `capture.blur_focus` | false | take the focus off whatever has it before the picture |
| `capture.match_baseline_scroll` | true | for a picture of the window: scroll it where the baseline's window was, and back after |
| `capture.device_scale_factor` | 1.0 | the scale to put into the platform key when the page's own cannot be read |
| `paths.root` | `.vistest` | where the pictures of the run, the diffs and the report go |
| `plugins` | — | what an installed scoring plugin may do: `plugins.fail_on` is `any`, `likely-real` (default) or `confirmed`; `plugins.disabled` lists plugins not to load. Without a plugin it changes nothing |
| `ai` | off | the optional AI layer after the engine (`ai.gate_enabled`, `ai.perceptual_enabled`); both off by default |

Refused, with one line saying why: `engine:` and `preset:` (also under
`diff:`), and `VISTEST_ENGINE` in the environment —

```
vistest.yaml: engine: v1 and presets were removed before the first release; v2 is the only engine
```

— and `capture.restore_scroll`, which is called `capture.match_baseline_scroll`
since 0.2.0.dev3. Any other key the library does not read — `matrix:`,
`service:`, `render:`, the server's `capture.full_page`, the tuning of the old
engine under `diff:` — is named in one warning per run:

```
VisTestWarning: vistest: vistest.yaml: the library does not read service.api_url, matrix.browsers — they change nothing in this run (they are the server's settings)
```

### Where a setting comes from

One order for every setting: **the call or the command line, then the
environment, then the file — vistest.yaml, or `pyproject.toml` for the
flags —, then the default.** A threshold has one more layer, between the call
and the environment: the snapshot's own passport (`<name>.json` next to its
baseline). A key of vistest.yaml the library does not read — `capture.full_page`,
`matrix:`, `render:`, `service:`, the v1 tuning of `diff:` — is named in one
warning per run instead of changing nothing in silence.

| Setting | Call / command line | Environment | vistest.yaml / pyproject.toml | Default |
| --- | --- | --- | --- | --- |
| the severity that fails a check | `fail_severity=` | `VISTEST_FAIL_SEVERITY` | `diff.fail_severity` | none: whatever no rule explains fails |
| the changed area that fails it | `max_changed_area_pct=` | `VISTEST_MAX_CHANGED_AREA_PCT` | `diff.max_changed_area_pct` | 0.15 % |
| the time the check may take | `timeout_ms=` | — | `capture.ready_timeout_ms`, `capture.stable_timeout_ms` | 5 s each; the check 2 × ready + 3 × stable + 15 s |
| the pointer kept where the test left it | `keep_pointer=` | — | `capture.keep_pointer` | moved off the page |
| the focus taken off | `blur_focus=` | — | `capture.blur_focus` | left alone |
| the largest picture | — | `VISTEST_ENGINE_MAX_PIXELS` | `diff.max_pixels` | 80 000 000 pixels |
| artifacts (`actual/`, `diff/`) | — | `VISTEST_ROOT` | `paths.root` | `.vistest` |
| the report | `--vistest-report` | `VISTEST_REPORT` | `vistest_report` | `.vistest/report/index.html` |
| the baseline directory | `--vistest-baselines` | `VISTEST_BASELINES` | `vistest_baselines` | `tests/__vistest__` |
| the platform directory | `platform=`, `--vistest-platform` | `VISTEST_PLATFORM` | `vistest_platform` | taken from the page |
| what a plugin's score does | — | `VISTEST_FAIL_ON` | `plugins.fail_on` | `likely-real` |
| writing baselines | `--vistest-update` | `VISTEST_UPDATE_BASELINES`, without the pytest plugin only | — | off |

`VISTEST_UPDATE_BASELINES` is the one exception, on purpose: under pytest only
the command line writes baselines into the repository — a variable a CI sets
for the server must not accept a library run's pictures.

## Baselines: layout, platform, names

```
tests/__vistest__/                 <- committed, reviewed in pull requests
  linux-chromium-1x-1440x900/      one directory per platform
    home.png                       the baseline
    home.json                      its passport
    shop/checkout.png              a name with a folder in it
  .renderers/<sha256>.png          the renderer canaries, once each

.vistest/                          <- ignored by git
  actual/<platform>/<name>.png     the picture of the last run
  diff/<platform>/<name>.png       what differs, drawn
  report/index.html                the report
  renderers/                       this run's canaries
```

Without a `tests/` folder the baselines go to `__vistest__/` at the project's
root; a folder is not made just to hold them. `--vistest-baselines` or
`vistest_baselines` names another place.

**The platform.** The directory is named by the operating system, the browser,
the picture's scale and the window size: `linux-chromium-1x-1440x900`,
`win-chromium-1x-1280x800`. Text is drawn differently on Linux, Windows and
macOS, and a baseline taken on one is not comparable on another, so each has
its own set. A name you give with `platform=`, `--vistest-platform` or
`VISTEST_PLATFORM` replaces it: keep the system in it, or know that without
it (`chromium-1440x900`) every machine that runs the suite shares one set. A
target that is not a live page (bytes, a file, an array) has no platform: its
baselines sit at the root of the baseline directory, and one warning per run
says so.

**Names.** A name is a path inside the baseline directory. Characters a file
system does not take are replaced in the file's name, and a message that
names the file then gives both. An empty name, an absolute path or `..` is
refused.

**The passport** (`<name>.json`) records how the baseline was taken: its size,
the thresholds set for this snapshot (`fail_severity`,
`max_changed_area_pct` — a layer between the call and the environment), the
capture's version, the device scale factor, whether the browser had a window,
the scroll of the window and the place of a Locator's element, and which
canary the renderer drew. A check against a baseline taken by an older capture
says so when it fails.

**The renderer canary** is a small fixed page of text the library draws in a
tab of its own when it writes a baseline and when a check fails. The failure
says whether this browser draws text as the one that took the baseline:
`renderer: same as the baseline's`, `different from the baseline's (canary: N
px)` or `unknown`, with the reason. A different renderer is a diagnosis, not
a pass: take the baselines again where the checks run.

**Accepting.** `--vistest-update` writes into the baseline directory; it is
always a command a person types. `missing` writes only what is absent,
`changed` (the default) also what failed, `all` everything that differs by a
byte. From a script, `VISTEST_UPDATE_BASELINES=1`; under pytest that variable
is ignored on purpose, so a variable a CI sets cannot accept a run's pictures.
A picture taken from a page that never held still is written with a warning.

**Moving them.** `vistest baselines export set.tar.gz --baselines
tests/__vistest__` packs a set; `vistest baselines import` reads it into a
server installation, and `--layout flat` writes an installation's baselines
back out as files in a repository.

## Readiness before the frames, and a second look after a failure

Visual tests die less of missing integrations than of a red that nobody
believes a month later. Most of that red is born at capture: the picture was
taken before the page was done. Stopping animations makes it worse, not
better — a frozen spinner over data that is still on its way is two identical
frames, and «two identical frames» is what screenshot tools wait for. The
capture-hazard stand (`tests/capture_hazards/`) measured exactly that.

**Readiness.** Before its frames, every capture — `expect_screenshot` and the
service alike, one piece of code (`vistest/capture/ready.py`) — asks the
page, step by step:

| step | holds when |
|---|---|
| load | `document.readyState` is `complete` |
| fonts | `document.fonts.status` is `loaded` |
| network | no request of this page is in flight — only when they are counted, see below |
| loaders | nothing in the photographed area says it is loading: `aria-busy="true"`, `role="progressbar"`, an indeterminate `<progress>`, or an endless animation that is a loader rather than a decoration |
| images | every image in the area has loaded and been decoded (a lazy one outside the viewport is not waited for) |
| quiet | nothing in the area changed for `capture.quiet_ms`: no DOM mutation outside `<head>` and the masks, no layout shift, no finished response |

All of them must hold at the same moment. Each waits at most
`capture.ready_timeout_ms` (5 s, cut to `expect_screenshot(..., timeout_ms=)`
when the call gives one; 0 turns the wait off); a step that gives up is named in the
reason — `the page was not ready within the limits: 1 request (GET /api/poll)
still in flight after 5000 ms (a request the page never waits for, like a long
poll, goes into capture.ignore_requests) — it was photographed as it was` —
and the picture is taken anyway. A request is named by its method and path,
never by its query string: tokens live there.

**Requests in flight.** From inside a check, a request the page started
before the check cannot be seen. So the count has to run from the moment the
browser context is created: the pytest plugin of the library does that for
every context Playwright creates in the test process
(`capture.track_requests: true`, the default), and the service does it for
the contexts it opens. Images, media, fonts and streams are not counted: the
images step and the quiet window cover what of them is in the picture, and a
lazy image below the fold would hold the wait for nothing.

A request the page never waits for — a long poll, an analytics beacon — would
hold every check to its limit. List it in `vistest.yaml`:

```yaml
capture:
  ignore_requests: ["*/api/poll*", "https://stats.example/*"]
```

Glob patterns, matched against the URL without its query string.

**Without the plugin** — `expect_screenshot` called from a plain script or
from `unittest` — the network step is not asked. Misses stay at zero on this path
too: the second look masks nothing, so late data is never hidden. But a page
that loads data with no sign of it (no spinner, no skeleton, no `aria-busy`)
can be photographed early, and that is a **false failure**. Mark such loading
with `aria-busy="true"` and the wait sees it. The stand measures this path
separately and prints its false failures and time; they are not held to the
plugin path's numbers.

**Playwright's async API** is not supported: `expect_screenshot` drives the
sync API, and an async page is refused with a sentence that says so. With an
async page, take the picture yourself and pass the bytes —
`expect_screenshot(await page.screenshot(), "home.png")` — knowing that a
picture handed in gets none of the waiting and frames a page gets.

**A page that stops answering** — a script stuck in a loop, a crashed tab, a
page closed before the check — fails the check with `CaptureError` and the
reason, in seconds: every question to the page has a deadline, at most
`max(5 s, capture.ready_timeout_ms)` each and never past
`2 × ready_timeout_ms + 3 × stable_timeout_ms + 15 s` for the whole check —
or past `timeout_ms` when the call gives one: then the check spends at most
that much on the page and raises `CaptureError` with what it was waiting for.
A page that stopped answering takes up to 1.5 s more than that, to say why —
whether it answers at all and whether its tab crashed is asked after the
deadline: `timeout_ms=2000` on a page stuck in a loop raises in about 3.5 s.

**Loader or decoration.** An endless animation in the area counts as a
loader when its element (or one of two ancestors) is named like one — class,
id, aria-label or the animation's name containing `spin`, `load`,
`skeleton`, `shimmer`, `placeholder`, `progress`, `busy`, `pending` — or when
it turns the element by a quarter turn or more (a spinner) or moves a
background across it (a shimmer). Pulsing, glowing, floating is decoration
and is not waited for. With the requests counted, the exact signal beats this
guess: when nothing is in flight and the area is quiet, an animation that only
looks like a loader — a rotating logo — does not hold the picture.
`aria-busy` and `role="progressbar"`, what the page says itself, always do.
Wrong without the count: a decoration that turns — the wait runs to its limit
and says so; a loader that is neither named nor turning (Tailwind's
`animate-pulse` skeleton) — not seen. Wrong with it: a spinner whose data
comes over a WebSocket or an EventSource, which are not counted — not waited
for. Each is a false failure at worst, never a hidden change.

Images in the area are repainted once after they decode (an invisible
outline, for two animation frames) before the picture: without it Chromium
sometimes photographed the anti-aliased rounded corners of a freshly decoded
image differently from one load to the next.

**The second look.** A check that fails gets more frames, until two in a row
are identical (and at least 300 ms have passed). Then:

- the page changed after it looked ready and held still — the data replaced
  a spinner after all: the **later frame** is compared, and the result says
  the snapshot was taken before the page was done. A later frame can turn a
  failure into a pass only when the later frames held still (two identical in
  a row) and nothing in them kept changing; otherwise a later frame that
  happens to match the baseline is luck, and the check fails, naming what
  changes;
- something keeps changing — a counter, an animation that does not stop: it
  is named, **not masked**, and the check fails until it is masked on purpose
  (`mask=["#counter"]`). Masking it quietly made checks pass by luck;
- nothing changed: `Confirmed on a second capture: the frames of this run are identical, so the difference from the baseline is real, not motion`.

Nothing the second look sees is masked, and nothing is remembered as a mask:
a change in data that arrived after `load` cannot be hidden this way. On a
page that was not ready — a step gave up, or the frames never held still —
there is no second look at all, and the note says why. The picture a person
opens, `actual.png`, is the frame the verdict is from.

The cost is one or a few extra frames, and only for what has already failed.
`capture.retry_on_fail: false` in `vistest.yaml` switches it off.

**What a failure names.** The picture's pixels say what differs; the live
page can say what it is. When a check fails:

- *the element under the pointer or in focus* — only when you asked to keep
  it there. By default the pointer is moved off the page before the picture,
  so that no element is under it and a hover left by the previous step is not
  photographed; the focus is left alone. `keep_pointer=True`
  (`capture.keep_pointer: true`) keeps the pointer where the test put it — for
  a hover captured on purpose —, `blur_focus=True` (`capture.blur_focus:
  true`) takes the focus off whatever has it; `keep_pointer=False,
  blur_focus=True` is both (`capture.reset_hover_focus` is the service's and
  the library does not read it). With the pointer or the focus
  kept, a failure whose region is that element names it — a concrete element
  only: never `html`, `body`, `#root` or a container as large as the window,
  which is under every pointer;
- *an element that changes by itself* — a counter, a clock, a canvas a
  script redraws — is named by a selector of the place that moves (its id,
  its `data-testid`, otherwise a short path) with one action:
  `mask=["#counter"]`, or `data-vistest="ignore"` on the element (the library
  paints it out like a mask, as the service does). When the request or the
  picture that changed it was still on its way after the picture, the message
  names that instead (`GET /api/orders`, `image /img/1.png`) and says to wait
  for it in the test: a mask would hide it. For a `<canvas>` the
  message adds that `animations="disabled"` does not stop it: that freezes CSS
  animations, and a script draws on a canvas;
- *the launch* — the passport keeps whether the baseline was taken headless
  or in a browser with a window, and the width of the scroll bars; when the
  check runs in a different one, the reason says so in one line;
- *the device scale factor* — the passport keeps it too
  (`device_scale_factor`); a check at a different one says so in one line.
  The platform name (`linux-chromium-1x-1440x900`) carries the scale of the
  picture, not the screen's, so it does not show this.

**Where an element was.** For a Locator, the passport keeps where its element
was in the window and how far the page was scrolled. Before the picture the
page is scrolled so that the element is in the same place again, in whole
pixels — a `position: fixed` background or a sticky header behind the element
then looks the same. A baseline without that record is photographed as
before.

**Where the window was.** For a picture of the window (a Page, not
`full_page`) the passport keeps how far the window was scrolled. Before the
picture it is set to that scroll instantly, and after the picture put back
where the test left it; one line in the message says where it was and where it
was set. `capture.match_baseline_scroll: false` (`restore_scroll` until
0.2.0.dev3) switches it off. A page too short to scroll that far stays where it can, the difference
remains, and the line says so.

**What a picture of an element leaves behind.** The scrolling: after the
picture — a failed check's further frames included — the window and every
scrollable box around the element are scrolled where the test left them.
The side effects that remain: the pointer is moved off the page (unless
`keep_pointer` is on), and, with `blur_focus`, the focus
is taken off its element; neither is put back.

**How the baseline was taken.** The passport records the capture's version
(`capture.version`). A change in how pictures are taken that changes pixels
of baselines already accepted comes with a new version, and a check against a
baseline of an older one (or of none: a passport written before the record
existed) says, when it fails: `the baseline was taken the old way (before the
pointer moved off the page before the picture, the window scroll put back, and
the other changes of capture version 3); if that is the
difference, accept it again: pytest --vistest-update=changed`. The service
keeps the same number as `capture_version` in a baseline's meta when its own
capture (the runner, the adapter) took the picture, and says the same on a
failure against an older one; a picture uploaded from elsewhere is not judged
by it.

**Where the baselines go.** `<rootdir>/tests/__vistest__/<platform>/<name>.png`
— or, when the project has no `tests/` folder, `<rootdir>/__vistest__/`: a
folder is not made for it just to hold baselines. `--vistest-baselines` (or
`vistest_baselines` in the pytest ini) says another place, and the line after
an update run, `baselines written to …`, names the one that was used.

## `timeout_ms`, and what a hung page costs

`timeout_ms` in the call is the time the whole check may spend on the page:
the readiness wait, the frames, the second look and the renderer's canary
together. Both capture limits are cut to it. When the page keeps the check
waiting past it, the check raises `CaptureError` and says what it was waiting
for.

Without `timeout_ms` the check still has a deadline, made of the two limits:
`2 × capture.ready_timeout_ms + 3 × capture.stable_timeout_ms + 15 s` — 40 s
with the defaults. Every single question to the page has its own deadline
too, so a page whose main thread is stuck fails the check in seconds instead
of hanging the run.

A page that stopped answering costs up to **1.5 s more** than the deadline:
whether it still answers at all and whether its tab crashed is asked after
it, to say why. `timeout_ms=2000` on a page stuck in a loop raises in about
3.5 s.

## Masks and `data-vistest="ignore"`

Three ways to keep something out of a check:

- **`mask=` with a selector or a Locator** — painted over in the picture
  before it is taken, as Playwright's `mask=` does. The painted box is in the
  baseline too, so both sides agree.
- **`mask=` with a box `(x, y, w, h)`** — not painted: the area is left out of
  the comparison. The baseline keeps the real pixels, so the decision can be
  revisited later.
- **`data-vistest="ignore"` on an element** — the page's own way to say «not
  this»: painted out like a mask selector, in every check of that page.

```python
expect_screenshot(page, "dashboard.png",
                  mask=["#clock", page.get_by_test_id("ad-slot"), (0, 0, 320, 64)])
```

```html
<span data-vistest="ignore">build 8f2a91c</span>
```

A mask is the right tool for something small that changes by itself — a
counter, a clock, an ad. When the second look sees something that keeps
changing, the failure names it with the `mask=` that would hide it; it is never
masked for you. A threshold is the wrong tool for that: it lets every small
change through, not just the one you meant.

## The engine

Every check — the library's `expect_screenshot`, the pytest fixture, the
service and `POST /api/check`, `vistest check`, `vistest compare`,
`vistest doctor` — runs **engine v2**, and there is nothing to choose. v2
catches every difference a person can see (ΔE00 above 1 per pixel, grouped
within 2 px, from 4 px up) and takes out only what a named rule explains: a
scroll bar that appeared, a JPEG re-encode, the page moved by a fraction of a
pixel, text re-rasterised by a renderer the canaries prove changed. **A
region no rule explained fails the check.** Each region that counts is said
in words in the report and the failure message («recolored: #1f2937 →
#4d5666, color difference 14.8»).

Engine v1, the cascade, and the presets that tuned it were removed before
the first release (0.2.0.dev3). `engine:` and `preset:` in vistest.yaml (also
under `diff:`) and `VISTEST_ENGINE` are refused in one line —
`v1 and presets were removed before the first release; v2 is the only engine`
—, and choosing an engine in the call, `--engine` and `--preset` of the CLI
and `--vistest-preset` of pytest are gone. v1 stays inside: the benchmark and the
bench corpus run it in code, and the server keeps `{"engine": "v1"}` in the
`options` of `POST /api/check` and its presets until phase 6. A v1 result
carries one line in its notes saying it is deprecated.

**The threshold.** Not set, it is 0 — whatever no rule explains fails. When
a person sets one — `fail_severity=` in the call, the snapshot's passport,
`diff.fail_severity` in vistest.yaml, `VISTEST_FAIL_SEVERITY`, an override
in the interface — a region no rule explained whose severity is below it
does not fail the check, and it is not dropped either: the result, the
report, the failure message and the API answer list it apart, in the
engine's words and with where the threshold came from:

```
below the threshold 25 (a project override): 2 changes — recolored: #333333 → #3a3a3a, color difference 2.0; …
```

Overrides saved in the interface before the switch are applied by v2 and
named that way, so an old number that now lets something through is seen.
`max_changed_area_pct` is the share of the frame that fails on its own,
whatever the severity — counted over the regions no rule explained, so under
the default threshold of 0 it adds nothing. Like the threshold, it is the
number a person set (the
call, the passport, vistest.yaml, `VISTEST_MAX_CHANGED_AREA_PCT`, an
override in the interface) or else the default, 0.15 %, named next to it —
`(limit 0.15%, the default)`. The threshold is there to let small things
through: a counter, a speck. A faint change over a paragraph is more often
real (the text color of a whole theme changed), so it still fails, and the
message says what to do: raise `max_changed_area_pct`, or mask the area —
for something small on an element's snapshot a mask is the right tool, not
a threshold.

### What engine v2 does not tell apart

What v2 cannot explain fails; these are the known places where that is a
false failure, not a change. None of them has a rule of its own yet: for
each, mask the element, set a threshold for the snapshot, or accept the new
picture after looking at it.

- **A page moved by a fraction of a pixel, on charts and SVG icons.** v2
  explains a fractional move of the page when each region's drawing moved
  with it: text, boxes and plain icons keep their shape and their coverage
  moves by exactly the page's move. A chart (lines, areas, sparklines) or an
  SVG icon can be re-rasterised at the new offset rather than moved — its
  strokes come out a little different — and then the region is said in
  words («shape changed», «edges redrawn within 1 px») and fails.
- **A part of the page a fraction of a pixel off, with pictures in it.** A
  block that lands on a fractional position — a `transform`, an animation
  stopped half-way — keeps its text on the pixel grid, but its raster
  pictures are resampled at the new position: every thumbnail differs a
  little, most at its rounded corners and edges, and v2 says «edges redrawn
  within 1 px, same color» and fails. The page-shift rule does not cover it,
  because only a part of the page moved. The text of such a block is drawn
  at the fraction along the line (it keeps to the pixel grid across it), and
  fails too — every glyph on a fractional grid of its own. Make sure nothing on the page is
  left at a fractional offset when the screenshot is taken (finish or
  disable the animation, round the transform), or mask the pictures.
- **A native checkbox (and other native form controls) on a page moved by a
  fraction of a pixel.** The browser draws them in its own theme, and the
  page-shift rule does not take them as moved with the page: they are left
  as a change.
- **`opacity: 0.98` and changes as faint.** By pixels it is the same as a
  real small edit — a color moved by about ΔE00 1–2. v2 takes every pixel
  above ΔE00 1, so it fails; it is not meant to tell the two apart. If such
  faintness is expected on a snapshot, a threshold for that snapshot is the
  tool.
- **Another renderer.** When the canaries say the renderer changed (another
  machine, OS, Chromium build or font hinting), v2 says so and explains the
  text it re-rasterised — but what else the other renderer drew otherwise
  (antialiasing of shapes, icons, form controls) still fails. The check
  gives a diagnosis, not a pass: accept the baselines again on the renderer
  the checks run on.

## The report

`.vistest/report/index.html`, written at the end of every run that checked
something — or where `--vistest-report` says. One self-contained file:
the pictures are inlined, nothing is fetched, no fonts, no script from a CDN.
It opens on a machine with no network and can be attached to a ticket.

Failures come first and open: baseline, actual and diff side by side, a
slider between them, and the regions in words — the same words as the
message, with the exact counts behind them. Checks that passed are listed
shut. Each row says how the picture was taken: the number of frames and the
time to stability.

### Allure

With `allure-pytest` installed (`pip install allure-pytest`, or the `report`
extra) and `--alluredir` given, a failed check attaches its pictures to the
test in Allure — nothing to configure:

| The check | Attached to the test |
| --- | --- |
| differs from the baseline | `Baseline`, `Actual screenshot`, `What changed` (PNG), and `result.json` with the regions and the numbers |
| has no baseline yet | `Actual screenshot` |
| passes | nothing |

The failure's message is the test's error in Allure, as in pytest. Without
`allure-pytest` nothing is attached and nothing is asked for; an attachment
that cannot be written is a warning, never a failed run.

```bash
pytest --alluredir=allure-results
allure serve allure-results
```

## Commands

The library's commands need no server:

| Command | What it does |
| --- | --- |
| `vistest check NAME ACTUAL` | `expect_screenshot` for a ready PNG, from any language |
| `vistest compare EXPECTED ACTUAL` | two PNGs, no baselines |
| `vistest doctor URL` | the noise of a stand on an unchanged page |
| `vistest bench [FOLDER]` | the engine on your own failures, against Playwright's comparator |
| `vistest baselines export\|import` | move a set of baselines |

`vistest --help` lists them apart from the server's; a server command without
`vistest[server]` says what to install in one line and exits 2.

### `vistest check`

```bash
vistest check checkout.png shots/checkout.png --platform linux-chromium-1x-1440x900
vistest check checkout.png shots/checkout.png --update      # write it when missing or different
vistest check checkout.png shots/checkout.png --json
```

The same layout, names and messages as `expect_screenshot`. Exit codes: **0**
— it matches (or `--update` accepted it); **1** — it differs; **2** — there is
no baseline yet. `--baselines DIR` names the baseline directory; without
`--platform` the picture has none and its baseline sits at the root of it.
`--json` prints one object:

```json
{"verdict": "fail", "message": "vistest: 'checkout.png' differs from the baseline …",
 "baseline": "tests/__vistest__/linux-chromium-1x-1440x900/checkout.png",
 "actual": ".vistest/actual/linux-chromium-1x-1440x900/checkout.png",
 "diff": ".vistest/diff/linux-chromium-1x-1440x900/checkout.png"}
```

`verdict` is `pass`, `fail`, `missing` or `new_baseline`. With `--api`, or a
server configured (`VISTEST_API_URL`, `service.api_url`), the command checks
against the server instead, in its layout.

### `vistest compare`

```bash
vistest compare expected.png actual.png -o diff/
vistest compare expected.png actual.png --json
```

Two files, the engine, the verdict and the regions in words; `-o` writes the
diff pictures and `result.json`.

### `vistest doctor`

```bash
vistest doctor https://your-app.example/ --runs 5
```

Loads the page several times, each in a clean browser context, with nothing
changing in between, so any difference it finds is false by construction. It
prints how many of the pairs would have failed and what exactly is noisy;
exit 0 means none would. `--selector` measures one element, `--json` prints
the report, `--out` names where the noise map goes. Run it before you trust
the tool on a stand.

### The engine on your own failures: `vistest bench`

Our benchmark corpus was written by us — simple templates, one browser. This
command runs the engine on yours: the pairs your own checks failed on, from
whatever made them.

```bash
vistest bench test-results                     # Playwright's test-results
vistest bench .                                # a project with a VisTest run (.vistest/)
vistest bench --expected baselines --actual screenshots   # two folders, same file names
```

**The input** is told apart by what is on disk:

- Playwright's `test-results/`: a failed `toHaveScreenshot` leaves
  `<test>/<name>-expected.png` next to `<name>-actual.png`;
- a VisTest run: `.vistest/actual/<platform>/<name>.png` against
  `tests/__vistest__/<platform>/<name>.png` (`--baselines` for another
  root; the report rows say where each baseline was);
- two folders with the same file names, `--expected` and `--actual`.

Anything the layout does not account for — a picture with no other half, a
file that is not a picture — is one line in the output, never dropped in
silence. Two pictures of different sizes are compared as they are, and said.

**Each pair** goes through:

- **engine v2**, with its defaults (no threshold, no preset) and the AI layer
  as the benchmark runs it: its verdict, the first three regions in words, and
  the renderer — with the canaries of both sides when the pair has them (the
  baseline's passport and the canary the library kept for the run under
  `.vistest/renderers/`; in two folders, `<name>.canary.png` next to a
  picture), «unknown» otherwise;
- **Playwright's own comparator** at threshold 0.05 and 0.2 (its default),
  maxDiffPixels 0 — `getComparator('image/png')`, the function
  `toHaveScreenshot()` calls, not a port. It needs Node and an installed
  Playwright: `--playwright <folder>`, else the input folder and the folders
  above it, the current folder and above, a VisTest checkout's `scripts/bench`,
  and only then a global install. Without them those columns are empty and
  the output says how to fill them.

A pair v2 fails while the page's move by a fraction of a pixel is proven is
flagged — that is where «What engine v2 does not tell apart» shows in your
pictures, and how often.

**There are no labels, so there is no score.** What comes out is where the
tools disagree, for a person to look at:

```
vistest bench — --expected tmp/expected --actual tmp/actual
Nothing was sent anywhere: the pictures were read where they are, and what this wrote is in tmp/out/.
layout: two folders; 77 pairs
v2: its defaults (no threshold, no preset), as the benchmark runs it
Playwright: Playwright 1.63.0 toHaveScreenshot() (@playwright/test 1.63.0, …), threshold 0.05 and 0.2, maxDiffPixels 0 — getComparator('image/png'), as toHaveScreenshot()

pairs: 77 — v2 failed 74, passed 3
all three agree (v2, Playwright 0.05/0, Playwright 0.2/0): 55
disagreements: 22 pairs
  v2 failed, Playwright 0.05/0 passed: 7
  v2 passed, Playwright 0.05/0 failed: 2
  v2 failed, Playwright 0.2/0 passed: 20
  v2 passed, Playwright 0.2/0 failed: 2
v2 failed where the page's move by a fraction of a pixel is proven: 0 pairs

Disagreements:
  table/fill/de8 · v2 fail — 1 change · Playwright 0.05/0 fail (2712 px), 0.2/0 pass (0 px)
    107x30 at (405, 77): fill: #2563eb → #4d77ff, color difference 8.0
    renderer: same as the baseline's
    (color difference is ΔE00 (CIEDE2000): 1 ≈ barely visible, 10 and more is plainly another color)
  table/render/shift_0.25px · v2 pass · Playwright 0.05/0 fail (5785 px), 0.2/0 fail (5051 px)
    renderer: same as the baseline's
  …
```

(The table template of our corpus, as two folders with the canaries next to
the pictures.) In `--out` (`./vistest-bench-out/` by default, ignored by git):

- `report.html` — the library report's page: the disagreements first and
  open, baseline / actual / diff side by side, a slider, the regions in words;
  the pairs all three tools agree on stay shut, their pictures left on disk;
- `labels.csv` — one row per disagreement, with what each tool said and an
  empty `label` column;
- `summary.txt` (the text above), `pairs.json`, `diff/`.

**Labelling.** Fill the `label` column with `SIGNAL` (a real change),
`NOISE` (nothing changed that matters) or `unsure`, and run again with
`--labels labels.csv`: each tool's false failures (NOISE it failed) and
misses (SIGNAL it passed) on the labelled pairs, counted as the benchmark
counts — `unsure` is printed and not counted. A label already given is kept
when the command runs again.

```
labelled pairs: 22 — SIGNAL 17, NOISE 2, unsure 3 (printed, not counted)
tool                  false (NOISE failed)   misses (SIGNAL passed)  unsure failed
v2                                     0/2                     0/17            3/3
Playwright 0.05/0                      2/2                     4/17            0/3
Playwright 0.2/0                       2/2                    17/17            0/3
```

**Nothing leaves the machine.** The command opens no network connection,
copies nothing into a repository, and writes only to `--out`; the first lines
of the output and of the report say so. The output without time in it is
the same for the same input, to the byte.

## Windows

Messages carry `→`, `ΔE00` and `≈`. A console or a pipe in a legacy code page
(cp1252, and the like — a Windows runner's pipe is one) cannot print them; the
library and the commands then write `->` and `dE`, never a crash. To see them
as they are, run with UTF-8:

```powershell
$env:PYTHONUTF8 = "1"
```

Baselines taken on Windows live under `win-…` and are not compared with the
`linux-…` ones of a Linux CI; take each set where it runs.

## Measuring the capture

For people working on VisTest itself, in a clone of the repository.

### Photographed too early: the capture-hazard stand

How often a check fails because it photographed the page too early — data
under a spinner, a late font, a banner that pushes the page down, a hover left
by the previous step — is measured on local pages with real network delays,
our `expect_screenshot` against Playwright's `toHaveScreenshot()`:

```bash
python -m tests.capture_hazards.measure
```

The stand's own test (the pages say when their hazard is over) carries its
own pytest mark and is skipped otherwise: `pytest -m capture_hazards
tests/capture_hazards`. What the pages are and how the table is counted:
`tests/capture_hazards/README.md`.
