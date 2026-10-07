# Changelog

Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/),
версии — [семантические](https://semver.org/lang/ru/).

## [Unreleased]

### Breaking in 0.2.0.dev3

The public surface of the library, before its first release on PyPI (review
v1, step B). Nothing here keeps the old spelling working with a warning: there
was no public release that promised it. Each line is what to write instead.

- **`vistest` exports the library only:** `expect_screenshot`,
  `BaselineMissing`, `ScreenshotMismatch`, `VisualCheckError`, `CaptureError`,
  `VisTestWarning`, `CompareResult`, `Verdict`, `compare`. Everything else by
  its full path; `vistest.CheckService` is an AttributeError that names it,
  `from vistest import CheckService` Python's ImportError.
  - `from vistest import VisTestConfig` → `from vistest.config import VisTestConfig`
  - `from vistest import VisualTester, VisualMismatch` → `from vistest.runner import …`
  - `from vistest import CheckService` → `from vistest.service import CheckService`
  - `from vistest import visual_check, visual_session, VisualSession, VisualTestCase`
    → `from vistest.integrations import …`
  - `from vistest import FileStore, SnapshotKey, SnapshotMeta, SnapshotStore`
    → `from vistest.storage import …`
  - `from vistest import ChangeKind, DiffRegion` → `from vistest.models import …`
- **`threshold=` is two numbers with their own names**, the names vistest.yaml
  (`diff:`), the passport and the interface already use:
  - `expect_screenshot(page, "a.png", threshold=40)` →
    `expect_screenshot(page, "a.png", fail_severity=40)`
  - `threshold={"fail_severity": 40, "max_changed_area_pct": 0.5}` →
    `fail_severity=40, max_changed_area_pct=0.5`
- **The capture knobs of the call are two switches and one time limit**
  (review v1, R8 and 1.1): `keep_pointer`, `blur_focus` and `timeout_ms` — the
  time the whole check may spend on the page; past it, `CaptureError` saying
  what it was waiting for.
  - `stable_timeout_ms=N`, `ready_timeout_ms=N` in the call →
    `capture.stable_timeout_ms`, `capture.ready_timeout_ms` in vistest.yaml;
    for one check, `timeout_ms=N` (both limits are cut to it)
  - `reset_hover_focus=True` → `keep_pointer=False, blur_focus=True`;
    `reset_hover_focus=False` → `keep_pointer=True`. `capture.reset_hover_focus`
    in vistest.yaml is the service's; the library does not read it
  - `restore_scroll=False` → `capture.match_baseline_scroll: false`; the key
    `capture.restore_scroll` is refused with its new name
- **Engine v1 and the presets are off the public surface** (review v1, R5 and
  8.1). There is one engine and nothing to choose; what chose v1 or a preset is
  refused in one line — `v1 and presets were removed before the first
  release; v2 is the only engine` — or is gone:
  - `expect_screenshot(..., engine="v1")` → nothing (`engine=` is a TypeError)
  - `engine: v1`, `preset: strict` in vistest.yaml, `diff.engine`, `diff.preset`
    → remove the key (refused)
  - `VISTEST_ENGINE=v1` → unset it (refused)
  - `pytest --vistest-preset strict` → nothing (an unknown option);
    `vistest compare --engine v1 / --preset strict`, `vistest check --preset`
    → nothing; `vistest check --set engine=v1` → refused
  - `vistest compare` reads vistest.yaml and the environment like every other
    command: the preset it built used to replace them
  - the server keeps `{"engine": "v1"}` and `preset=` in `POST /api/check`
    until phase 6; the benchmark and the bench corpus choose v1 in code
- **One order for every setting:** the call or the command line, then the
  environment, then vistest.yaml (or `pyproject.toml` for the flags), then the
  default — one table in the README and in docs/GUIDE.md (review v1, 2.1, 2.5).
  - `VISTEST_BASELINES`, `VISTEST_PLATFORM`, `VISTEST_REPORT` were read only
    without the pytest plugin → read under it too, between the flag and
    `pyproject.toml`. `VISTEST_UPDATE_BASELINES` still writes baselines only
    without the plugin: under pytest only the command line does
  - a key of vistest.yaml the library does not read was ignored in silence →
    one warning per run names them all (review v1, 2.3)
  - `vistest.local.yaml`, which the README promised, was never read → the
    promise is gone
- **The server's fixtures and flags come with the server** (review v1, R2, R6
  and 2.7). With `vistest` or `vistest[browser]` alone, `pytest --fixtures`
  shows `vistest` and `vistest_config`, and `pytest --help` five flags:
  `--vistest-update`, `--vistest-baselines`, `--vistest-platform`,
  `--vistest-report`, `--vistest-config`.
  - `visual`, `visual_soft`, `_vistest_session`, `vistest_run_id`,
    `--vistest-api`, `--vistest-perceptual`, `--vistest-fail-on` →
    `pip install "vistest[server]"` (they are registered only then)
  - `--vistest-api` only printed the address and `--vistest-perceptual` did
    nothing → they set the service's address and switch the model on
  - `examples/test_demo_visual.py`, `examples/test_existing_suite.py` →
    `docs/server/examples/`; `examples/` is the library's
- **`vistest --help` in two groups** (review v1, R6, 3.1, 3.9): the library's
  commands — `compare`, `check`, `bench`, `doctor`, `baselines export|import` —
  then the server's, headed with what they need.
  - a server command without `vistest[server]` died on `ModuleNotFoundError:
    No module named 'fastapi'` (or ran on the library's layout and found
    nothing, like `vistest list`) → one line, `pip install "vistest[server]"`,
    exit 2; `vistest check --api` too
- **`vistest check NAME ACTUAL` without a server is `expect_screenshot` on a
  file** (review v1, R4, 3.2) — the library's layout and rules, the contract the
  JS wrapper will stand on: exit 0 matches, 1 differs, 2 no baseline yet;
  `--update` accepts; `--json` prints one object (`verdict`, `message`,
  `baseline`, `actual`, `diff`).
  - it wrote into the server's `.vistest/baselines/<os>-chromium-1x/` and
    created a baseline on the first run with exit 0 → `tests/__vistest__/`
    (`--baselines DIR`, `--platform P`), and the first run is exit 2
  - `vistest check NAME IMAGE` in the server's layout → `--api URL`, or a
    server configured with `VISTEST_API_URL` / `service.api_url`; `--frames`,
    `--dom`, `--browser`, `--run-key` and `--set` without one are refused
- **The CLI and the matrix speak English** (review v1, 3.5): the help of
  `snap --matrix/--browsers/--viewports` and of `matrix`, the output of
  `vistest matrix` and the errors of `matrix:` in vistest.yaml were Russian →
  English; for one, `window size '1440*900' is not WIDTHxHEIGHT, for example
  1440x900`

### Review A: reliability and edge cases (after 0.2.0.dev2)

From the review of the library before it is shown to anyone (review v1); the
public API, the words of the messages and the documentation are the next
steps. The engine, the corpora and `metrics.json` are not touched.

- **A page that stops answering fails the check in seconds.** Every question
  `expect_screenshot` asks a live page has a deadline (`library/js.py`): at
  most `max(5 s, ready_timeout_ms)` each, never past
  `2 × ready + 3 × stable + 15 s` for the check. A page whose main thread is
  stuck, a crashed tab or a closed page raises `CaptureError` with the reason;
  it used to hang the run.
- **`examples/test_library.py`**, the library's own example, and the install
  check runs it: red without baselines, `--vistest-update` writes them to
  `tests/__vistest__/`, green, red after the page changes. The install check
  used to run the service fixture's example.
- **A failure is the test's line and the message, printed once** — the
  library's frames are hidden for its own verdicts and refusals
  (`__tracebackhide__`), and the "Visual diff" section that repeated the
  message is gone.
- **Refused by name, with what to do:** Playwright's async API; float,
  out-of-range, RGBA and grey arrays (a float 0..1 array used to become a black
  baseline); an address instead of a page; a Selenium element or driver; a
  snapshot name that is empty, absolute or leads out with `..`. The message
  heads with the name the file really has.
- **An accept warns** for a page a readiness step gave up on, and **refuses**
  a picture over `VISTEST_ENGINE_MAX_PIXELS`, writing nothing.
- **vistest.yaml:** a misspelt section or key is one line — the file, the key,
  «did you mean» — and pytest stops before the first test (it used to be
  ignored, or thirty lines of INTERNALERROR). The library no longer reads
  `service.project` or `VISTEST_PROJECT`.
- **CLI:** `vistest --version`; `snap` without `--viewport` takes one picture
  at 1440x900 (it took one per letter of the default); an error in the input is
  one line and exit 2; a stdout that cannot hold «Δ» no longer crashes
  `compare`; colour only on a terminal, and `NO_COLOR` is honoured.
- The notification and retention clocks start again after a stop: the ticker
  tests failed whenever an e2e test had shut its in-process server down
  earlier in the same worker.
- Four functions nobody called are gone; the dev2 branches only the hazard
  stand reached have unit tests.

### Review A2: what CI found on the review A pull request

- **Plugin migrations work on Python 3.10.** `set_authorizer(None)` takes the
  authorizer off only from 3.11; on 3.10 the None became the callback, and the
  version row, the `COMMIT` and the `ROLLBACK` after a plugin's steps were all
  "not authorized" — every plugin migration failed. One helper lifts it, with
  an authorizer that allows everything before 3.11.
- **The install check reads the example's outcomes from a JUnit report**, one
  per test, not from pytest's text: how many times a message appeared there
  depended on the terminal's width, and all fifteen install jobs went red.
- **Windows:** the review A tests compare paths whatever the separator, and
  read their child processes as UTF-8 whatever the code page.
- **Ten e2e tests are `xfail`** (`strict=False`, "server tails, phase 6 — see
  the plan"). They fail on main as well; the interface is not changed here.
  When phase 6 makes one pass, its marker comes off:
  - `tests/e2e/test_ui_auth.py::test_every_screen_survives_the_access_gate[/baselines]`
  - `tests/e2e/test_ui_auth.py::test_the_sidebar_badge_counts_unreviewed_failures`
  - `tests/e2e/test_ui_auth.py::test_a_snapshot_can_be_reached_from_the_baselines_tab`
  - `tests/e2e/test_ui_auth.py::test_a_snapshot_says_what_captured_it`
  - `tests/e2e/test_ui_auth.py::test_a_snapshot_without_a_url_is_still_runnable_through_its_test`
  - `tests/e2e/test_ui_auth.py::test_the_button_says_which_way_it_will_go`
  - `tests/e2e/test_ui_smoke.py::test_every_run_has_a_delete_button`
  - `tests/e2e/test_ui_smoke.py::test_baselines_screen_has_a_store_picker`
  - `tests/e2e/test_ui_smoke.py::test_project_snapshots_are_visible_and_runnable`
  - `tests/e2e/test_ui_smoke.py::test_projects_screen_shows_the_vistest_set`

### 0.2.0.dev2: capture and messages, after the first trial on a real application

The engine, engine v1 and `metrics.json` are not touched. Stand numbers and
what the trial found: `tests/capture_hazards/PREREG_DEV2.md` and the stand's
README.

- **Readiness: the quiet window runs from the end of the last request.** With
  the requests of the context counted (the pytest plugin), "network" holds
  only once the whole quiet window has passed since the last request of the
  page ended, and the quiet window is counted from that same moment; the rule
  that a spinner-like animation does not hold the picture once nothing is in
  flight applies under the same condition. Before, the window ran while a
  request was out, so the instant its answer came the page counted as quiet and
  the picture was taken before the app re-rendered and sent the next request.
  The second look has the same rule: later frames count as settled only if
  nothing of the page was in flight, or ended, all that time.
- **The pointer is moved off the page by default; the focus is not touched.**
  `keep_pointer=True` (`capture.keep_pointer`) keeps the pointer for a hover
  captured on purpose; `blur_focus=True` (`capture.blur_focus`) takes the
  focus off; `reset_hover_focus=True` is both, as before. **This changes the
  pixels of baselines already accepted** wherever the previous step left the
  pointer over something: capture version 3 (the passport's `version`; the
  service's `capture_version` stays 2), and a check against an older baseline
  says "the baseline was taken the old way" when it fails.
- **The window's scroll, for a picture of the window.** The passport keeps
  `window_scroll`; once the page is ready (not before: a page still arriving is
  too short, and growing content moves the scroll again) the window is set to
  it instantly, the readiness is looked at once more, and it is put back after, one line in the message says where it was and where it was
  set (`capture.restore_scroll: false` / `restore_scroll=False` turns it off).
  A page too short to scroll that far keeps the difference, and the line says
  so.
- **The device scale factor** is in the passport (`device_scale_factor`); a
  check at a different one says so in one line. The platform name is unchanged.
- **Baseline folder:** `<rootdir>/tests/__vistest__`, or `<rootdir>/__vistest__`
  when the project has no `tests/` folder. GUIDE says so.
- **Messages.** The pointer/focus hint only when a concrete element (not
  `html`, `body`, `#root` and its like, not a window-sized container) is
  where the failing region is; "ΔE00 0.00 (below 2)" is said as letters moved
  by less than a pixel, same colour; "Confirmed on a second capture" says the
  frames of this run are identical, so the difference from the baseline is
  real, not motion; "luck, not a pass" says the page was still changing, and a
  failure on a page that changed after the picture names the request or the
  picture still on its way instead of suggesting a mask; "no rule explained" and
  "severity" are put in words with the scale. A before → after table is in the
  dev2 report.
- Tests: the assertions on the old sentences, and two on the old pointer
  default, were changed with the messages and the default.
- The stand got two hazards, `chained` and `modal_scroll`, pre-registered in
  `tests/capture_hazards/PREREG_DEV2.md` before any code changed.

### Phase 2: the engine on pairs from elsewhere

- **Install: `vistest[browser]` brings `pytest-playwright`; smooth scrolling.**
  The README and `examples/` take `page` from pytest-playwright's fixture, so
  the extra that the README tells to install now includes it (and `full`
  does too); `scripts/check_install.py` no longer adds it by itself. On a page
  with `scroll-behavior: smooth` the step that puts an element back at its
  baseline's place scrolled smoothly, and read where the element stood
  halfway: it scrolls instantly now, as the scrolling back already did. Left
  as it is on purpose until phase 3: `HOMEPAGE` in `provenance.py` still names
  the old repository, because it is part of the hash that signs reports;
  GitHub redirects the old name, and it changes together with the signature
  format when the repositories are split.

- **Install check on three systems, through TestPyPI.** Version `0.2.0.dev1`
  (v2 by default is a visible change), written once in `vistest/__init__.py`
  and read from there by `pyproject.toml` (`dynamic = ["version"]`) and by the
  server's API description. Repository links in `pyproject.toml` and
  `clients/` point to github.com/marshlw/vistest; the README's own links are
  absolute, so that they work on the package index's page.
  `scripts/audit_dist.py` lists the built sdist and wheel, checks that the
  canary font, `bench/*.mjs`, `ai/*.json`, the review UI and the two entry
  points are in the wheel, and searches both archives for private keys,
  tokens, `.env` and key files, developer paths, pictures and the terms it is
  given (`--forbid`, `--forbid-file`: kept out of the repository on purpose).
  `scripts/check_install.py` does what a user does, in a clean virtual
  environment: install from a wheel or an index, `playwright install
  chromium`, `vistest --help`, `vistest bench --help`, the version, the pytest
  plugin, and `examples/test_demo_visual.py` twice (write, then compare).

- **A picture of an element no longer leaves the page scrolled elsewhere.**
  The window and every scrollable box around a Locator's element are
  scrolled back to where the test left them — after the first frames, after
  the second look's further frames, and after the naming of what moves. The
  placing at the baseline's place and Playwright's own scrolling into view
  are unchanged; only what they leave behind is. The pointer and the focus
  under `reset_hover_focus` stay the one remaining side effect (GUIDE).

- **S2b: what a failure names; the place, the launch and the way a baseline
  was taken.** A later frame of the second look turns a failure into a pass
  only when the later frames held still and nothing in them kept changing;
  otherwise it is luck and the check fails, naming what changes (frames A, B,
  A, B with A matching the baseline fail). With the requests counted, the
  exact signal beats the loader guess: nothing in flight and a quiet area —
  a rotating logo no longer holds the picture (`aria-busy` and
  `role=progressbar` still always do). A network step at its limit names the
  request by method and path, never its query; `capture.ignore_requests`
  (glob patterns) leaves long polls and beacons out of the wait. Images in
  the area are repainted once after they decode, which removed the
  load-to-load difference in their anti-aliased rounded corners
  (lazy_images). For a Locator the passport keeps where its element was in
  the window, and the page is scrolled back there, in whole pixels, before
  the picture. `reset_hover_focus` (in the call or `capture.reset_hover_focus`,
  off by default) moves the pointer away and takes the focus off before the
  picture; without it a failure at the element under the pointer or in focus
  names it and the option. An element that changes by itself is named by a
  selector with `mask=["…"]` or `data-vistest="ignore"` (now painted out by
  the library too); for a canvas the message says `animations="disabled"`
  does not stop it. The passport records the launch (headless or with a
  window, scroll bar width) — a different one is said in one line — and the
  capture version (2); a failing check against an older baseline says it was
  taken the old way and how to accept it again. Without the pytest plugin
  (scripts, `unittest`, the async API) late data with no loading sign can
  still give a false failure; misses stay at zero on both paths. The stand
  gained spinning_logo and hover_focus_reset. The engine is untouched.

- **S2a: readiness before the frames; the second look hides nothing.** The
  capture-hazard stand (`tests/capture_hazards/`, S1) showed two things about
  the capture of a page whose data comes late. Frames «held still» at once,
  because a spinner frozen for the screenshot is two identical frames: the
  loading state was photographed. And the second look after a failure masked
  whatever differed between two frames — the data that had arrived between
  them — so the check was green, with a real change in that data as green as
  the plain page (misses 50/50 on four pages). Now, before its frames, every
  capture — `expect_screenshot` and the service, one module
  (`vistest/capture/ready.py`) — waits until, at the same moment, the page is
  loaded, its fonts are in, none of its requests is in flight (counted from
  the creation of the context: the pytest plugin does it for every context
  Playwright makes, `capture.track_requests`; the service for the contexts it
  opens; `capture/inflight.py`), nothing in the area says it is loading
  (`aria-busy`, `role=progressbar`, an endless animation that is a loader —
  the rule and where it errs are in the module), the images in the area are
  loaded and decoded, and nothing in the area changed for `capture.quiet_ms`
  (40 ms, chosen on the stand's calibration seeds). Each step waits at most
  `capture.ready_timeout_ms` (5 s; `expect_screenshot(ready_timeout_ms=)`), and
  one that gives up is named in the reason. The second look
  (`core/retry.py`) takes frames until two in a row are identical and judges
  the **last** one; it masks nothing — a one-off change is compared as the
  page became, what keeps changing is named and fails until it is masked on
  purpose — and on a page that was not ready it does not look at all.
  `actual.png` is the frame the verdict is from. In the service the frames
  of a capture are taken after the wait, the last is compared, and only what
  changed in two intervals or more becomes the instability mask; `networkidle`
  and the fixed pause are gone. `/api/stabilize.js` gets `ready()` with the
  same probe. Three hazards were added to the stand first (pulse,
  silent_fetch, after_click). The engine is untouched: both benchmarks are
  byte for byte what they were.

- **Step 0b: the canary does not depend on the window.** The canary is
  360×156 px, and in a tab smaller than that in either direction one of its
  lines came out otherwise (765 px of it: a 320 px wide window, or a 1280 px
  wide one 120 px high). It is kept per browser and device scale factor, not
  per window, so in one browser a check at 320 px and one at 1280 px got the
  canary of whichever came first, and one of them could say «the renderer
  changed» under the same renderer. Fixed by the page, not by the cache key:
  the canary's tab is never smaller than 400×400 px (`canary.MIN_TAB`) —
  raised by `canary.draw`, so in both paths (a tab of the context, and the
  new context of a page from `browser.new_page()`); a larger viewport is
  kept, and the user's page keeps its own size. A test in Chromium: windows
  of 320×120, 1280×720, 1280×120 and 320×900 in one browser draw one
  canary. The corpus's canaries drawn again with it — every configuration
  this machine can start, nine of ten (os_windows is another machine's) —
  come out as the files the manifest names, sha256 for sha256; the manifest
  and the page of the canary (`CANARY_VERSION` 1) are unchanged. A baseline
  accepted before this in a window smaller than 360×156 px carries the
  other canary and now reads as another renderer: accept it again.
- **v6: `vistest bench <folder>` — the engine on your own failures.** Our
  corpus was written by us; this is the engine on somebody else's pairs.
  They carry no labels, so the command keeps no score: it collects where the
  tools disagree, for a person. **Input**, told apart by what is on disk
  (`vistest/bench/layouts.py`): Playwright's test-results
  (`<name>-expected.png` / `<name>-actual.png`), a VisTest run
  (`.vistest/actual/` against the baselines, the report rows read for where
  each baseline is and what the renderer was), or two folders with the same
  file names (`--expected` / `--actual`); whatever a layout does not account
  for is one line, and two sizes are compared as they are and said. **Per
  pair:** v2 with its defaults and the AI layer, as the benchmark runs it —
  verdict, the first three regions in words, the renderer line with both
  canaries when the pair has them (the passport's and the run's; in two
  folders `<name>.canary.png`), «unknown» otherwise; Playwright's own
  comparator (`getComparator('image/png')`, threshold 0.05 and 0.2,
  maxDiffPixels 0) through Node and an installed Playwright
  (`vistest/bench/playwright_compare.mjs`, shipped with the package: found
  through `--playwright`, the input folder and above, the current folder and
  above, `scripts/bench`, then a global install — empty columns and one line
  on how to fill them without); a flag where v2 failed although the page's
  fractional move is proven. **Output:** a summary (pairs, all three
  agreeing, disagreements by kind), one line per disagreement, the flagged
  pairs; `report.html` on the library report's code (the disagreements
  first and open, baseline / actual / diff side by side); `labels.csv` with
  the disagreements and an empty `label`; with `--labels` each tool's false
  failures and misses on the labelled pairs, as the benchmark counts
  (unsure printed, not counted), and a label once given is kept on the next
  run. Nothing goes to the network or into a repository: everything is
  written to `--out` (`./vistest-bench-out/`, now in `.gitignore`), and the
  first lines say so; the same input gives the same text and the same
  report. **Checked on the browser corpus:** the 308 calibration pairs as
  two folders with their canaries, and the manifest's labels as
  `--labels`, give the benchmark's calibration figures exactly — v2 17/28
  false, 0/264 misses; Playwright 0.05/0 24/28, 15/264; 0.2/0 24/28, 70/264
  (OpenCV 4.13.0, @playwright/test 1.63.0); a test does the same pair by
  pair on a third of the table template. held-out-2 is not read. **Around
  it:** the library keeps the canary a check drew under
  `.vistest/renderers/<sha>.png` and names it in the report row
  (`renderer.run_sha256`), so a run's pair can be looked at again with both
  canaries; the library report embeds pictures only for the rows that show
  them and each picture once (the side-by-side pane copies them in when it
  is opened — half the size for large frames), shows the diff next to
  baseline and actual, can open a row whatever its verdict or keep one shut
  (in `vistest bench`, the pairs all tools agree on: shut, pictures left on
  disk), and takes lines under the title and a fixed stamp. The engine does
  not change: both benchmarks print what they printed.

### f5: engine v2 by default

#### Moving to engine v2

Engine v2 now runs wherever a comparison runs — the library's
`expect_screenshot`, the pytest fixtures, the service and `POST /api/check`,
`vistest check`, `vistest compare`, `vistest doctor`. What no rule explains
fails; each region that counts is said in words.

**What turns red after the update**, and why v1 let it through:

- colour differences between ΔE00 1 and 2.3, and recolours SSIM does not
  see: v1 needed both colour and structure to change (consensus); v2 takes
  every pixel above ΔE00 1;
- a new ink colour on text: v1 let it through within 30 % of the local
  contrast;
- thin lines and small marks — an underline, a 1–2 px border, a focus ring,
  a caret, anything under 24 px: v1's morphological opening and its minimum
  region removed them; v2 keeps regions from 4 px;
- a whole page moved by whole pixels: v1 aligned the frame (up to 8 px); v2
  explains a move by a fraction of a pixel only, and a block moved by whole
  pixels is a layout change;
- a baseline taken by another renderer (another machine, OS, Chromium build
  or font hinting): v2 explains re-rasterised text only when the canaries
  prove the renderer changed, and nothing else it drew otherwise; a baseline
  accepted before the canary existed names none, and then nothing about the
  renderer is assumed;
- JPEG at a quality v1's detection does not try, sensor or video noise, a
  gradient re-dithered — none of it comes from a browser screenshot in the
  library's path, and v2 fails it (`tests/benchmark_corpus/v2_ratchet.json`
  lists what it fails on the synthetic corpus, and why);
- a preset's `fail_severity` no longer lets anything through: under v2 the
  threshold is 0 unless a person set one; nor does loose's 0.8 % area
  limit — under v2 it is 0.15 % unless a person set one.

**What to do:**

- accept the baselines again on the renderer the checks run on
  (`pytest --vistest-update=changed` in the container or on the CI machine):
  a baseline accepted now keeps the renderer's canary, and v2 can then tell
  re-rasterised text from a change;
- set a threshold where a check has to tolerate small differences —
  `threshold=` in the call, the snapshot's passport, `diff.fail_severity` in
  vistest.yaml, an override in the interface. A region no rule explained
  below it does not fail the check and is listed apart with where the
  threshold came from: «below the threshold 25 (project override): 2 regions
  — ink colour: #333333 → #3a3a3a, ΔE00 2.2 ×2». Overrides saved before the
  update are applied and named that way. `max_changed_area_pct` still fails
  on its own — counted over what no rule explained; a preset does not set
  it for v2 either: the number a person set, or the default 0.15 %, named
  next to it («area limit 0.15% (default)»), and a check failed on it says
  to raise `max_changed_area_pct` or mask the area;
- mask or ignore what moves: `mask=` (selectors and boxes), ignore zones,
  `data-vistest="ignore"`, `diff.ignore_kinds`;
- for one release, keep v1: `engine: v1` in vistest.yaml,
  `VISTEST_ENGINE=v1`, or `engine="v1"` in the call (`--engine v1` for
  `vistest compare`, `--set engine=v1` for `vistest check`,
  `{"engine": "v1"}` in the API's `options`). A v1 result says in its notes
  that v1 goes in the next release.

- **Step A: coverage moments in page-shift** (`pageshift.coverage_moments`,
  `pageshift.region`). A vector path is drawn by coverage and blended with
  the paper linearly in the frame's own 8-bit sRGB values, so a drawing the
  rasteriser draws again a fraction of a pixel over keeps, in a box that
  holds it (the region's box + 3 px), the sum of (pixel − paper) per channel
  (M0), and the centre of that sum (M1) moves by exactly the move. A region
  the page's move does not reproduce is now also explained as «the drawing
  moved with the page» when (b) its shape agrees to a pixel, (c) the paper
  is the same, M0 changes by at most 0.7 % of the strongest channel's M0
  (`SHIFT_MASS_CHANGE`) and M1 lands within 0.2 px of the page's move
  (`SHIFT_CENTROID_PX`); `suppressed_by` gives the ratios and the centre's
  move. Both are `Gap`s from the calibration half. Noise: the shift-pair
  regions not reproduced by the move whose (b) and (c) hold — icons and
  glyph groups at the fraction; M0 up to 0.0060 kept (article, an icon),
  the centre up to 0.141 px off (cards, where the fit lands 0.125 px from
  the page's move). Signal: every region of every calibration SIGNAL pair,
  as if the page had moved by each of the four moves fitted on the shift
  pairs (the actual frame moved by it with `warp.shift`); on the other
  renderer, the regions that touch the mutation. None passes. What sets the
  numbers: the column header of table/word_swap@full_chromium/one — the
  swapped word re-laid the table and moved letters of the header by up to a
  pixel, the other renderer drew them otherwise too — one 63 px region that
  keeps M0 to 0.0081 with its centre 0.116 px off; the corner of form/border_radius/plus2px keeps M0 to 0.0059
  with its centre 0.302 px off. ×1.16 on M0 — the thinnest gap of v2, and
  the other renderer sets it: on the same renderer alone the nearest signal
  is at 0.0207, and a ΔE00 4 colour changes M0 by 0.054. **The reviewer saw
  these measures on the held-out half before the thresholds were fixed; the
  thresholds were chosen on the calibration half only.** The 10 % that a
  region may leave unreproduced (`SHIFT_REGION_MISS`) stays: of the 71
  calibration regions that pass through it the moments explain 4 — 39 fail
  M1 alone (snapped text and box edges at the fraction in one region, whose
  centre moves by neither), 8 fail (c), 20 more than one property. The
  moments come after the 10 %, before the test for text redrawn at the new
  position, which stays too: without it six of the eight calibration shift
  pairs turn red. Both still take changes a pixel close to the old ink: a
  ring drawn half a pixel larger, or at another fraction, passes the text
  test (`test_the_icon_at_another_fraction_is_not_the_drawing_moved` shows
  the moments refusing it). The native checkbox of form/render/shift_0.5px
  loses a fifth of its coverage (M0 ratio 0.80) — it sticks to the pixel
  grid — and stays red. Browser corpus (OpenCV 4.13.0): same renderer,
  calibration 2/12 → 1/12 false (article/render/shift_0.25px turns green:
  its bookmark icon, missed by 39 %, keeps M0 to 0.26 % and its centre
  moves 0.024 px from the page's move), held out 3/6 → 3/6; misses 0/184
  and 0/92 as before; other renderer unchanged; no other verdict moved.
  Held out, the icon of dark/render/shift_* keeps M0 to 0.83–0.88 % — over
  0.7 % — and stays red. Synthetic corpus: v1 byte for byte as before, v2
  unchanged (12/16, 0/11).
- **Step A, the owner's decision: the moments only on the same renderer,
  ε0 = 0.015.** The premise of the moments is the same rasteriser drawing
  the same path at the fraction; another renderer draws another picture,
  and the rule has nothing to say there (at 0.007 the other renderer as if
  moved still passed 115 records, starting with one renderer-noise region
  of cards in every family). So the moments are asked only when the
  canaries prove the renderer is the same; with «changed» and «unknown» a
  region goes the way it went before step A. The signal side of
  `SHIFT_MASS_CHANGE` is the same renderer only: noise 0.0107 (an icon of
  form/render/shift_0.25px), signal 0.0207 (letters of a column header of
  table/word_swap/one, which the swap re-laid) — value 0.015; the signal
  side of `SHIFT_CENTROID_PX` moves to 0.301 (a corner of
  article/border_radius/plus2px), the value stays 0.2. Both were measured
  on the calibration half before the held-out run. **The held-out half is
  spent for the page-shift rule: the reviewer and the author have both seen
  it, and dark/render/shift_* after this change is not evidence for
  acceptance.** The library's lazy canary follows: a check that failed
  without the canary is compared again with it whenever both canaries are
  there — the same renderer now lets a rule in too
  (`fingerprint.compare_lazily`); the lazy-is-eager test takes
  article/render/shift_0.25px, which passes only on the second comparison.
  Browser corpus (OpenCV 4.13.0): same renderer, calibration 1/12 as after
  step A (form/render/shift_0.5px, the checkbox, M0 ratio 0.80); held out
  3/6 → 1/6 — dark/render/shift_0.25px and shift_0.5px turn green, their
  icon keeping M0 to 0.83–0.88 %, now under 1.5 %: **not evidence**, the
  half is spent; misses 0/184 and 0/92 as before; other renderer and every
  other verdict unchanged. Synthetic corpus: v1 byte for byte as before, v2
  unchanged (12/16, 0/11).
- **A new held-out half, sealed: held-out-2.** The old one is spent for the
  page-shift rule, so the final acceptance of f5 and the figures of the
  README in phase 3 are measured on two new templates, made where the
  engine is weakest: `dashboard` (a light sales dashboard — a sidebar of
  icons, number cards with sparklines, badges) and `settings` (a
  preferences page — switches, checkboxes, radio buttons, selects, many
  icons). Their pairs are the «same renderer» group: all 22 families at
  every magnitude (opacity 0.98 NOISE, ΔE00 2 DISPUTED, as elsewhere) and
  the stylesheet configurations — `shift_0.25px`, `shift_0.5px` (NOISE)
  and `geometric_precision` (DISPUTED); no other renderer, no other
  machine. 106 pairs, 10.2 MB, drawn once by `python
  scripts/browser_corpus.py --capture-sealed` in the environment of the
  corpus after the six baselines and the baseline's canary were redrawn to
  their frozen pixels; every mutation changed pixels. Frozen like the rest:
  sha256 of every frame in the manifest (`split: "held-out-2"`, a `sealed`
  section with the rule), checked by `verify_files` and the drift test.
  **Sealed:** `load_manifest()` leaves it out — what the benchmark, the
  diagnostics and the engine tests read is the manifest as it was before,
  to the byte — and `load_manifest(sealed=True)` is the whole file, for
  what checks, redraws or rewrites the corpus (`write_manifest` refuses a
  manifest read without it). `tests/benchmark.py --corpus browser` runs
  and prints it only with `--final`, after the tables, which stay what they
  are; `scripts/bench_playwright_grid.mjs` puts it in the digest and
  compares it only with `--final`. Neither engine nor Playwright has been
  run on it, and no table of it has been read. The corpus budget goes from
  40 to 50 MiB (51.3 MB used).
- **Step C: v2 is the default engine; v1 stays for one release.** The
  engine is one field, `DiffConfig.engine` (default `core/engines.DEFAULT`,
  "v2"), and `compare()` reads it; no caller in the package passes it on its
  own except training (`ai/train.py`, v1 by name: the gate learns from v1's
  regions). Chosen, in increasing strength, by vistest.yaml (`engine:`, or
  `diff.engine` — the two may not disagree), `VISTEST_ENGINE`, and the call
  (`compare(engine=)`, `expect_screenshot(engine=)`, an `engine` override of
  a check — `vistest check --set engine=v1`, `options` of `POST /api/check`;
  `vistest compare --engine`). A preset never chooses it: the library's
  `--vistest-preset`, `vistest check --preset` and the API's `preset=` keep
  the engine chosen elsewhere (`VisTestConfig.preset_of(..., engine=)`). A v1
  result says in its notes, the library's report and its failure message
  that v1 goes in the next release. **The threshold under v2:** 0 in every
  preset — a preset's `fail_severity` is v1's — unless a person set one:
  `threshold=`, the passport, `diff.fail_severity` in vistest.yaml,
  `VISTEST_FAIL_SEVERITY`, a global or project override in the interface
  (applied by the service, and handed to a project's run with
  `VISTEST_THRESHOLD_SOURCE` so it is named there too). Where it came from
  is `DiffConfig.threshold_source`. A region nothing explained below it
  goes to `CompareResult.below_threshold`, keeps its description, does not
  fail the check, and is said — one line, «below the threshold 25 (project
  override): 14 regions — ink colour: #333333 → #3a3a3a, ΔE00 2.2 ×14» — in
  the notes, the report (a list of its own, open), `ScreenshotMismatch` and
  the API answer (`threshold`, `below_threshold`, only when a threshold was
  set). `max_changed_area_pct` fails on its own as in v1, over what no rule
  explained; the message says so when it does. A preset other than balanced
  under v2 adds one line: it acts on v1 only. The engine does not change:
  the v2 rows of both benchmarks are byte for byte what they were
  (OpenCV 4.13.0, `--no-timing`: the synthetic table with `--compare
  --engine both`, and the browser corpus). **The synthetic ratchet**
  (`tests/benchmark_corpus/v2_ratchet.json`, the maintainer's decision): v2
  misses nothing (0/11 per raster) and fails exactly the 24 listed NOISE
  pairs, each with its reason from step B; a new red pair is red, a listed
  pair that turned green is red until it is taken off — the list only
  shrinks. v1 stays held by metrics.json, which does not change. Every test
  that holds v1 — the cascade's, the ratchet, metrics.json — calls
  `engine="v1"` by name.
  **Measured, no rule added.** Stability on live pages — examples/ (the
  demo page through the pytest fixture and the three integration forms) and
  the library's own live pages (the demo page, the animated page with a
  spinner, a pulse and a focused field, the renderer test's page), 19
  checks: a baseline, then five repeats with nothing changed, each engine on
  its own copy of the baselines: v1 5 of 95 red, v2 5 of 95 — the same
  check both times, `existing-from-bytes.png`, a frame taken by the
  project's own `page.screenshot` with no stabilisation, and in it the live
  counter (v2: 21x13 at (305, 229), «ink shape changed»). Speed per pair on
  the unsealed browser corpus, 1280×800, one Xeon core at 2.1 GHz: median
  v1 169 ms, v2 198 ms, Playwright's comparator 107 ms (which decodes the
  two PNGs itself, 12 ms here); identical frames 0.9 / 0.9 / 38 ms. v2 is
  slower than twice v1 at the p95 (4.8 s against 1.4 s) and on the pairs
  another renderer drew (median 1.96 s against 0.63 s): 65 % of v2's time
  is the text re-rasterisation rule, 13 % the page-shift rule, 8 % the
  descriptions. The canary costs 243 ms once per browser; a failed check is
  compared once more with it (median 159 ms).
- **Step D: the final run on held-out-2, once** (2026-10-01, commit
  `45f057e`, OpenCV 4.13.0, `python tests/benchmark.py --corpus browser
  --final --no-timing --engine both --native bench_out/native_final.json`
  after `node scripts/bench_playwright_grid.mjs --final`; two runs, the
  same to the byte; the tables before the sealed half the same to the byte
  as without `--final`). The target was written down before the run: the
  one of E1 for the «same renderer» group — v2's false failures at most a
  third of Playwright 0.05/0's, its misses at most Playwright's; DISPUTED
  printed, not counted. 106 pairs (dashboard, settings), all drawn by the
  baseline's renderer: 92 SIGNAL, 6 NOISE, 8 DISPUTED.

  | tool | false | misses | DISPUTED failed |
  |---|---|---|---|
  | VisTest v1 strict | 4/6 | 36/92 | 2/8 |
  | VisTest v1 balanced | 3/6 | 38/92 | 2/8 |
  | VisTest v1 loose | 1/6 | 53/92 | 2/8 |
  | VisTest v2 | 2/6 | 0/92 | 8/8 |
  | Playwright 0.2/0 (default) | 4/6 | 36/92 | 2/8 |
  | Playwright 0.05/0 | 4/6 | 5/92 | 2/8 |

  **The target is not met:** no misses (0/92 against 5/92), but 2/6 false
  failures where a third of Playwright's 4/6 allows 1. Both are on
  dashboard, `render:shift_0.25px` and `render:shift_0.5px`, two regions
  each, in the engine's words: 576x122 at (263, 301) — «ink shape changed:
  0 of 2402 (2422) px of the new ink and 1 of 2417 of the old lie farther
  than 1 px from the other»; 33x32 (33x33) at (1180, 14) — «strokes redrawn
  within 1 px; ink #4b4444 → #4b4444, ΔE00 0.00 (below 2)». The figures
  are recorded as they came. No engine change in f5 follows from
  them; a later change made with these pairs in view spends held-out-2 for
  that rule, and says so where it is made. Whether f5 goes into main with
  the target not met is the maintainer's decision. The grid's JSON with
  `--final` is not committed: `docs/benchmark_browser_native.json` stays
  the run without the flag.
- **Step C2: the area limit under v2 is set like the threshold; the canary
  of `browser.new_page()`.** A preset does not set `max_changed_area_pct`
  for v2 either: the number a person set — the call, the passport,
  `diff.max_changed_area_pct` in vistest.yaml, `VISTEST_MAX_CHANGED_AREA_PCT`,
  a global or project override in the interface (handed to a project's run
  with `VISTEST_AREA_SOURCE`) — or else the default of `DiffConfig`, 0.15 %,
  whatever the preset; strict's 0.02 % and loose's 0.8 % are v1's. Where it
  came from is `DiffConfig.area_source` (`v2_area_limit`, `v2_area_source`)
  and it is named next to the number — «threshold 25 (call), area limit
  0.15% (default)» — in the failure message, the report and the API answer
  (`threshold.area_limit`, `threshold.area_source`). A check failed on the
  area says what to do: raise `max_changed_area_pct` or mask the area (the
  owner's decision: the limit stays — a faint change over a paragraph is
  more often real; for something small on an element's snapshot a mask is
  the tool, not a threshold). **The canary:** a page from
  `browser.new_page()` has a context of its own that opens no other tab;
  its canary is now drawn in a new context of the same browser at the same
  device scale factor and viewport, closed after it, and comes out pixel for
  pixel as the one a context the user opened draws (a test in Chromium, at
  scale 1 and 2 and at a 320 px viewport). The viewport is taken too
  because the canary is 360 px wide and comes out otherwise in a narrower
  one — that is how it was drawn before C2 as well, in the user's context;
  the canary itself is not changed. A canary that could not be drawn is no
  longer kept for the browser: the next check tries again. **examples/**: `existing-from-bytes.png` masks the
  live counter in the project's own `page.screenshot` — bytes taken
  elsewhere are not stabilised. **tests/test_licensing.py** takes today's
  date in UTC, as `License.days_left` does; it failed for some hours of
  every day away from UTC. The engine does not change: the output of both
  benchmarks without `--final` is byte for byte what it was.
- **Merged into main by the maintainer's decision** (`152f6b5`) with the
  target on held-out-2 not met for false failures: v2 2/6 where it allowed
  at most 1; misses 0/92 against Playwright 0.05/0's 5/92.

### E1: a second engine path that catches first and explains after (`engine="v2"`)

- **Where v1 loses the browser corpus, as a script.**
  `scripts/diagnose_browser.py` replays the v1 cascade (preset balanced) on
  the calibration templates with every stage instrumented and prints the
  numbers that say which filter took the signal: `rerender` calls a ΔE00 15
  ink change noise — every changed pixel of the text sits on an edge with
  ~200 levels of contrast, and |diff| ≤ 0.3 × contrast lets it through
  (table/text_color: 3980 of 3981 changed pixels "within tolerance", ink
  #343649 → #586074, ΔE00 14.2), and the ink colour is never asked about;
  the underline and the removed border reach the change mask and then no
  region (table: 57 and 168 unassigned pixels, 0 left after close → open),
  and unassigned pixels do not vote; a ΔE00 8 fill is 3165 changed pixels
  and 0 after consensus (no structure change, below `strong_color` 9.2,
  0.30 % of the frame against the 1.25 % `flat_recolour` asks for); on
  hinting_none and full_chromium the global alignment compensates a shift of
  −1.6 px that is not there, raising the ΔE00 > 2.3 count from 54 354 to
  60 022 and leaving 79 live regions (max severity 94.5). Read-only,
  deterministic, calibration half by default.
- **Step 1: the sensitive base of v2.** `compare(..., engine="v2")` goes to
  `vistest/core/v2/`; v1 stays the default and is not touched (synthetic
  benchmark output and `metrics.json` byte for byte as before). A candidate is
  a pixel with ΔE00 above 1.0 — every pixel, no consensus, no anti-aliasing
  veto, no contrast tolerance. Candidates are grouped by proximity (dilated by
  2 px, 8-connected components), with no opening; every candidate pixel is in
  exactly one region (`unassigned_pixels` is 0 by construction), and the
  smallest region is 4 px — a number of pixels, not a share of the frame
  (`V2Config.min_region_px`, a `Gap` measured on the calibration half:
  opacity 0.98 leaves at most 1 px, the smallest signal 13 px). A smaller group
  is a suppressed region named `min-size`, not a dropped one. No explanation of
  noise yet: any region left fails. On the browser corpus (OpenCV 4.13.0):
  calibration 28/32 false, 0/184 misses; held out 15/16 false, 0/92 misses —
  the base catches every signal and almost every noise pair, as it should
  before anything is explained. `tests/benchmark.py --engine v1|v2|both`
  chooses the rows: both by default on the browser corpus, v1 by default on
  the synthetic one. New: `tests/test_engine_v2.py`.
- **Step 2: the explanation «text re-rasterisation»** (`vistest/core/v2/rerender.py`,
  rule `rerender-text`). A region is noise only when four properties hold,
  each a function with its own tests and its numbers in `suppressed_by`:
  (a) the ink did not change colour — each colour channel read at its extreme
  away from the paper, in each frame on its own (LCD anti-aliasing covers the
  channels one at a time), ΔE00 < 2; (b) the ink shapes, binarised at half
  their own contrast, agree to one pixel both ways, A ⊆ dilate(B, 1) and
  B ⊆ dilate(A, 1), not one pixel outside; (c) the paper did not change
  (ΔE00 < 1) and no changed pixel lies more than 2 px from the ink; (d) the
  change reached the page: at least 40 % of the page's ink clusters hold a
  changed pixel (`TEXT_SHARE`, a `Gap` from the calibration half: 0.549 on
  the noisiest form, 0.291 on article/offset/plus1px). A region the rule
  refused keeps an annotation naming the properties that failed. The ink mass
  B/A of every region is measured and reported, not used. On the calibration
  half the rule explains 768 of 1794 regions of the five re-rasterisation
  families; 884 fail on (b) alone — words whose glyphs drift past a pixel;
  no pair turns green yet, and no signal pair is lost (0/184, 0/92). The
  families that must stay red have no region explained on either half.
  `scripts/diagnose_browser.py --v2` prints it by family.
- **The AI layer cannot make a region disappear in v2.** A region `refine`
  does not hand back is suppressed as `ai-layer: <name> did not return this
  region`; `unassigned_pixels` stays 0.
- **The v2 row is «VisTest v2».** The presets do not act on it; of `DiffConfig`
  it reads only `DIFFCONFIG_READ` (size policy, memory limit, ignored kinds,
  and the two severity weights, which order and do not decide), and a test
  changes every other field at once to show it.
- **The OpenCV version is in the browser table.** `--corpus browser` prints
  `VisTest rows computed with: OpenCV …, numpy …, Python …` under its title
  (and in the markdown), in the output rather than on stderr: a before and an
  after from two OpenCV builds are not a comparison.
- **Step 2b, the corpus: every frame names the renderer that drew it.**
  `vistest/library/canary.py` is a fixed page — a subset of DejaVu Sans
  shipped in the package (`vistest/library/fonts/`) at 13, 21 bold and 15
  italic px, one line light on dark, and the machine's serif, sans-serif and
  monospace — drawn in a tab of its own through the library's capture. The
  corpus keeps one per rendering configuration in
  `tests/browser_corpus/renderers/` (sha256 in the manifest, `renderers`),
  and every frame names its own: `templates.<t>.renderer`,
  `cases[].renderer.expected/actual`. Measured: hinting_none 6495 px from the
  baseline's, no_lcd_no_subpixel 10 661, full_chromium 6173; hinting_full and
  gpu_raster — the control — 0; geometric_precision and the two page shifts
  are the page's stylesheet, which the canary tab never sees: 0.
  `--capture-noise-only` draws the canary too, and `--import-noise` of the
  same pixels again takes it without `--replace`; until then os_windows is
  «unknown». New family **«real changes drawn by another renderer»**
  (`<mutation>@hinting_none`, `@full_chromium`, all six templates, the same
  split): 120 SIGNAL pairs. The whole family as asked — 16 SIGNAL and 9
  DISPUTED magnitudes — is 33 027 156 bytes of PNG against 14 741 739 bytes
  left in the 40 MiB budget; left out, and named in the manifest
  (`cross_render.left_out`): the DISPUTED geometry, the ΔE00 15 steps, the
  strong border, the link colour. Existing frames and labels are byte for
  byte as they were (`--regenerate` redrew all 342 to the same bytes);
  `docs/benchmark_browser_native.json` re-run on 462 pairs, its 342 old
  results unchanged. `scripts/bench_playwright_grid.mjs` now yields to the
  event loop every ten pairs: pngjs keeps each diff frame until it does, and
  462 pairs no longer fitted in 8 GB.
- **Step 2b, the engine: text re-rasterisation only where the renderer is
  proven to have changed.** `compare(..., renderer=(baseline canary, run
  canary))` (`vistest/core/renderer.py`): the same pixels — same; other
  pixels — changed, with their count; a missing canary — unknown. v1 ignores
  it. v2 runs `rerender-text` only on «changed», and says which in a note:
  «renderer: same as the baseline's», «renderer: unknown — text
  re-rasterisation is not explained», or «renderer differs from the
  baseline's (canary: N px): typography changes within a pixel cannot be
  verified here — make baselines on this renderer to check them». The rule:
  (a), (c) as they were; **(e) not a pure shift** — the closest whole-pixel
  move (dx, dy) ≠ (0, 0) within 4 px may leave at most 25 % of the region's
  changed pixels changed, or the region is a block that moved, said in
  words («the block moved by +1 px along y») — `SHIFT_RESIDUAL`, a `Gap`
  from the calibration half: the 290 regions step 2 took out in font_size,
  padding, line_height and element_removed are exact moves (0.000), and so
  are 78 regions of renderer noise (0.000–0.143: boxes and icons the
  narrower text moved), against 0.442 for the first re-rasterised text;
  **(b) with a drift per glyph** — each ink component may shift along the
  line by up to K px before its shape is compared — K is a `Gap` too
  (`GLYPH_DRIFT_PX`) and came out 0: a «9» that became an «8» at 9–12 px
  needs one pixel, the renderer's noise needs up to 8 and more; (d) is
  measured and printed and decides nothing. The benchmark takes every
  frame's canary from the manifest (`renderer_pair`), never from a family
  name. On the browser corpus (OpenCV 4.13.0) the verdicts per pair are
  those of step 2, byte for byte; by group, held out, against Playwright
  0.05/0: same renderer — v2 7/8 false (Playwright 6/8), 0/92 misses (7/92);
  other renderer — v2 8/8 false (8/8), 0/40 misses (0/40). Neither group
  meets «false ≤ a third of Playwright's». geometric_precision is a
  stylesheet of the page, its canary is the baseline's, and its six pairs
  are no longer looked at by the rule. `scripts/diagnose_browser.py --v2`
  prints, per family, the pairs by renderer answer and the properties the
  regions failed; `--as-if-changed` runs the rule on every pair.
- **Step 2b: os_windows has its canary.** Drawn by the maintainer on the
  machine the os_windows frames came from, with the Playwright they were
  drawn with (1.56.0, chromium_headless_shell-1194, in a venv of its own:
  the machine had moved on to 1.58.0 / 1208, which draws form, landing and
  dark otherwise). The six frames came out as the ones in the corpus, byte
  for byte as delivered, so `--import-noise` took the canary without
  `--replace`: 10 426 px from the baseline's. The os_windows pairs are no
  longer «unknown» but «changed», and the rule runs on them.
- **Step 2b, the library: every check knows whether the renderer changed.**
  `expect_screenshot` draws the canary once per browser context — in a tab
  of its own in the same context, through the same capture — and keeps it
  for the life of the context (`vistest/library/fingerprint.py`). An
  accepted baseline's passport names it (`renderer`: sha256 and canary
  version; written only when set, so older passports read as before), and
  the PNG is stored once per sha in `<baseline root>/.renderers/`, to be
  committed with the baselines; `FileStore.list` skips directories that
  start with a dot. On a comparison both canaries go to
  `compare(renderer=...)`; the report row and `ScreenshotMismatch` carry one
  line — «renderer: same as the baseline's», «renderer: different from the
  baseline's (canary: N px)», «renderer: unknown — <why>» (a picture handed
  in as bytes, a baseline accepted before the canary, a canary file nobody
  committed). An accept that changes nothing still touches nothing. Cost,
  measured on the table template at 1280×800, nine fresh contexts: the first
  check in a context 149–163 ms before, 410–414 ms after (median); every
  later check unchanged (131–134 ms). The report row says `canary drawn in
  N ms` on the check that paid for it. New: `tests/test_library_renderer.py`.
- **After step 2b: geometric_precision is DISPUTED — relabelled, not a change of
  the engine.** The maintainer's decision: `text-rendering: geometricPrecision`
  is the page's stylesheet (the canary proved it: drawn without the page's CSS
  it is the baseline's to the pixel), and in real life it changes only when
  somebody edits the styles — a change of the page's typography within a
  pixel, printed and not counted, like ΔE00 ≈ 2. The reason sits next to the
  configuration (`WHY_GEOMETRIC`, `noise_configs.geometric_precision.why`).
  **The figures of every tool moved with this label, not with any engine**:
  NOISE 48 → 42 and DISPUTED 18 → 24; v1 balanced 23/32 → 19/28 false on
  calibration, 11/16 → 9/14 held out; v2 28/32 → 24/28 and 15/16 → 13/14;
  Playwright 0.05/0 28/32 → 24/28 and 14/16 → 12/14. In the «same renderer»
  group, held out: v2 7/8 → 5/6 false, Playwright 0.05/0 6/8 → 4/6. No other
  label changed; `--regenerate` redrew every frame to the same bytes.
- **The library draws the canary only where it decides something.** Drawn
  for every context, it cost the first check of each about a quarter of a
  second, and pytest-playwright opens a context per test. Now it is drawn
  (1) when a baseline is written — created or updated — to keep its sha, and
  (2) when a check failed without it against a baseline whose passport names
  a canary; then the two are compared, and only if they differ is the pair
  compared again with `renderer=`. The renderer only lets a rule take regions
  out, so the lazy path gives the verdicts of the eager one — a test runs both
  on pairs of the browser corpus with engine v2 (and every one of the 462
  pairs was run once for the E1 report). A canary is kept per browser and
  device scale factor, not per context. A passing check says «renderer: not
  checked (the check passed)». Measured like before (table template,
  1280×800, nine fresh contexts, two runs): the first passing check in a
  context 382–404 ms → 145–160 ms (149–163 ms before the canary existed); a
  failing check, first in a fresh browser 616 → 635 ms, then in another
  context of that browser 579 → 355 ms.
- **Step 3: the page moved by a fraction of a pixel** (`vistest/core/v2/pageshift.py`,
  rule `page-shift`). Chromium draws a page moved by a `transform` of a
  quarter or half pixel with its box edges anti-aliased at the fraction and
  its text snapped to the grid — where it was, or a whole pixel over in the
  direction of the move. So the move is proven on the page before any region
  is looked at, whatever the renderer: (1) the shift (dx, dy), both under a
  pixel and not both whole, is fitted on the page's *structure edges* —
  straight L* edges at least 40 px long, box borders and rules, not text —
  and must move at least 7 % of them by the fraction and by no whole pixel
  (`PAGE_SHIFT_MOVED`); (2) the fraction and the whole pixels next to it in
  its direction must reproduce at least half of the page's changed pixels
  within ΔE00 2 (`PAGE_SHIFT_COVER`, `MOVE_TOLERANCE`). Then a region is
  explained when the move misses at most 10 % of its changed pixels
  (`SHIFT_REGION_MISS`), or when it is text redrawn at the new position —
  (a) the same ink, (b) the shape within a pixel, (c) the same paper, and a
  whole-pixel move only in the page's direction (e'). A region the move does
  not explain says by how much it missed and which property failed. The four
  numbers are `Gap`s from the calibration half, replayed in
  `tests/test_engine_v2_pageshift.py`: the moved share is 0.174–0.329 on the
  eight shift pairs and at most 0.027 on every other one
  (table/render/no_lcd_no_subpixel; a block moved by whole pixels moves no
  edge by a fraction, a renderer that draws text otherwise moves no box
  edge); the cover 0.792–1.000 (table/offset/plus1px reaches 1.000 on its
  own, which is why the edges are asked first); a moved pixel misses by at
  most 0.86 at the 95th percentile; the regions that are not redrawn text
  miss at most 0.082, except two that miss 0.321 and 0.387 and stay red.
  The moving goes through `core/warp.py`: `warp.sample` is `warp.shift` at
  the pixels asked for, the same arithmetic, held equal by a test. What is
  honest in v1 (preset balanced; `scripts/diagnose_browser.py`, finding 5):
  the idea — estimate the page's move and compensate it with the one warp.
  What is not: phaseCorrelate on the whole page answers a mix of box edges
  and snapped text (dx +0.02…+0.35, dy +0.00…+0.05 on the quarter-pixel
  pages; +0.53…+0.97, +0.91…+1.00 on the half-pixel ones), nothing checks
  it, and what turns a pair green is re-drawing the baseline region by
  region with the contrast tolerance that also lets a new ink colour
  through; its 3/8 false on these families are on the calibration half
  (held out 1/4). On the browser corpus (OpenCV 4.13.0) the page is proven
  on the twelve shift pairs and on none of the other 450 (largest share
  held out: 0.014, dark/padding/plus1px). Same renderer, v2: calibration
  8/12 → 2/12 false, held out 5/6 → 3/6 (Playwright 0.05/0: 4/6), misses
  0/184 and 0/92 as before; other renderer unchanged. Eight pairs turned
  green, all render:shift_*; no other verdict moved. Four shift pairs stay
  red on one region each, the same thing every time — a thin-stroked icon or
  a checkbox drawn at the fraction by the rasteriser, not moved: its strokes
  come out lighter (ink ΔE00 3.2–12.7) and the bilinear move misses 32–86 %
  of it (calibration: article 10×13 at a quarter, form 17×17 at a half; held
  out: dark 12×10 at both). A lighter icon is also what a ΔE00 4 icon
  colour change looks like, so nothing at the region level tells the two
  apart, and the rule does not try. Synthetic corpus, v2: 15/16 → 14/16
  false (antialias 0.4px is a whole-frame sub-pixel warp and passes),
  0/11 misses; v1 byte for byte as before.
- **Step 4: v1's scroll-bar and JPEG rules in v2** (`_explain_environment` in
  `vistest/core/v2/engine.py`, before the page's move). The scroll bar is
  v1's rule as it is: a band 4–20 px wide at the right or bottom edge,
  featureless along its length in both frames, changed along at least 80 %
  of it, the page beside it untouched (`explain.scrollbar_bands`, and
  `explain.in_band`, renamed from `_in_band` so that v2 can ask, like
  `explain.reencode`; v1 unchanged). JPEG detection is v1's as it is
  (`explain.detect_jpeg`). The
  test per region is not, because v1's would break two things v2 promises:
  (1) v1 counts a pixel reproduced when it is within 30 % of the local
  contrast, and on text that is how a new ink colour passes (finding 1 of
  `scripts/diagnose_browser.py`) — on a page of thin text through JPEG
  q=75 with half a line in a new ink (ΔE00 16.6), v1's test leaves 0 of the
  region's 31 300 px; (2) v1 lets 5 % of a region stay unexplained, and v2
  groups without an opening — on a JPEG frame the ringing joins the page
  into one region, 5 % of which is more than the half line that changed.
  In v2 a changed pixel is reproduced when the baseline re-encoded at the
  quality found is within ΔE00 1 of it (the base's own test), what is left
  is grouped as the base groups, and a region is explained only when no
  group of 4 px or more is left (`MIN_REGION_PX`, the base's «a change»).
  The baseline is re-encoded whole once the quality is known: v1 re-encodes
  the changed boxes on the 16-px grid, and the decoder's chroma upsampling
  makes a box differ from the frame on its outermost pixels — within v1's
  tolerance, not within ΔE00 1 (jpeg q=75: 4–119 px of a region). No new
  number. **Caret: there is no rule, in v1 or in v2.** In v1 the synthetic
  caret (a 2 px line, 27 px tall) passes because its changed pixels end up
  in no region — the loss that also drops an underline (finding 2); v2 keeps
  it red, 3×29 px: pixel for pixel it is a line added. The library hides the
  caret when it captures (`caret="hide"`). Results: on the browser corpus
  neither detection fires on any of the 462 pairs, and every verdict is
  that of step 3; synthetic, v2: 14/16 → 12/16 false per raster (scrollbar,
  jpeg q=75), 0/11 misses; jpeg q=88 stays red — v1 tries 50, 60, 70, 75,
  80, 85, 90, 95, and at 90 the re-encoded baseline still leaves 0.54–0.58
  of the error, more than the half detection asks (v1 passes that pair
  through its anti-aliasing filter, not its jpeg rule). Cost: detection
  re-encodes the changed area at eight qualities on every comparison with a
  live region — 6–209 ms on the browser pairs measured.
- **Step 5: a region in words** (`vistest/core/v2/describe.py`). Every
  region that counts in v2 gets one sentence of what was measured on it, as
  the first of its annotations (kind `description`), and nothing else: «block
  moved by +1 px along y» — B is A moved by whole pixels and A is B moved
  back (`rerender.block_shift` both ways; asked one way only, a line taken
  off a plain paper is «moved» too); «line added: 57×1 px, #2563eb» — the
  changed pixels fill a box at most 2 px thick and 8 px long, and the other
  frame shows its paper there; «fill: #2563eb → #4d77ff, ΔE00 8.0» — the
  most frequent colour of the region's neighbourhood changed; «ink colour:
  #1f2937 → #4d5666, ΔE00 14.8» — the same shapes to a pixel in a new ink;
  «strokes redrawn within 1 px; ink #1f2937 → #252f3e, ΔE00 1.97 (below 2)»;
  «ink shape changed: 17 of 607 px of the new ink and 34 of 629 of the old
  lie farther than 1 px from the other». «Ink», not «text»: nothing measured
  knows whether the strokes are letters. It decides no verdict. The failure
  message says it under `changed:` for three regions and counts the rest,
  before `also:`; the report has a «what changed» column (the notes column
  no longer repeats it). v1 writes no sentence, and its message is what it
  was. The library still compares with v1, so the sentence reaches a
  library check once the library runs v2 — tested on messages built from v2
  results. On the calibration half the sentences say the mutations as they
  were made: every one of the 393 regions of text_color (ΔE00 4, 8, 15),
  the 22 of link_color and the 12 of icon_color is «ink colour» (the first
  region of each ΔE00 15 pair: 14.8–17.5); the 12 of fill are «fill» (ΔE00
  8 pairs: 7.9–8.2); the 6 of underline «line added» (the first regions
  41×1 to 123×1 px); the first of table/offset/plus1px «block moved by +1
  px along y». To keep it cheap, `rerender.block_shift(pure_only=True)`
  drops a move as soon as the pixels it surely leaves pass the limit,
  proving them apart on L* alone (ΔE00 ≥ |ΔL*| / S_L, since |R_T| ≤ 2)
  before computing ΔE00; the default search is unchanged. Cost: 160–730 ms
  on the noisiest browser pairs (86–193 regions).

### Windows: a refused replace is retried, and a check that raises is still reported

Found on the Windows job (3.13), where
`test_library_mode.py::test_two_tests_writing_one_name_are_reported_as_a_collision`
went red under `-n 2`: `test_alpha` died with WinError 5 in `atomic.py` while
the other worker wrote the same file, left no report row, and the controller
saw one writer. Two defects of the product, not of the test.

- **`storage.atomic.write_bytes` retries a refused replace on Windows.** There
  `os.replace` fails with `PermissionError` (WinError 5) while another process
  holds the target or is replacing it at the same moment — two tests with one
  snapshot name writing `.vistest/actual/<name>.png` from two workers, or an
  antivirus or the indexer opening a PNG that has just appeared. On Windows
  only, and only for `PermissionError`, the replace is now retried with a
  pause that starts at 5 ms and doubles up to 200 ms, for at most
  `REPLACE_RETRY_SECONDS` (2 s); after that the error is raised as it came. On
  POSIX nothing changes: a `PermissionError` there is about permissions and is
  raised on the first attempt. The temporary file is removed whatever the
  outcome, as before. The platform switch is a module variable,
  `atomic.RETRY_REPLACE`, so the Windows behaviour is tested on every
  platform. The module docstring no longer claims that the replace always
  succeeds on Windows without a lock.
- **A check that raises before its verdict still leaves a report row.**
  `expect_screenshot` wrote its row only at the end, so an exception between
  naming the check and deciding it — writing the actual picture, reading the
  baseline, comparing — left nothing: the check was missing from the report,
  and the collision check saw one writer where there were two. That is how
  the Windows run above failed without saying «name collision». Such a check
  now writes a row with `verdict="error"`, its key, its test's nodeid and the
  exception's text, and the exception reaches the test unchanged.
  `BaselineMissing` and `ScreenshotMismatch` write their own row as before —
  one check, one row. In the report an error row is counted under its own chip
  and open by default, and a row with no pictures at all no longer shows an
  empty «the pictures are on disk at:» list.
- New: `tests/test_library_atomic.py` (refused twice then written; refused
  for good: the original error at the limit, no temporary left; off Windows
  the first refusal is raised at once; only a refusal is retried), in
  `tests/test_library_api.py` the error row (key, nodeid, the exception
  untouched; one row for `BaselineMissing` and `ScreenshotMismatch`; counted
  and open in the report), and in `tests/test_library_mode.py` two tests with
  one name under `-n 2` where one writer fails: the run still prints «name
  collision» with both nodeids.

### R1: a corpus drawn by the browser, with labels known in advance

- **Templates and capture.** Six local pages in
  `tests/browser_corpus/templates/` — an orders table, a settings form,
  product cards, a landing page, a long serif article and a dark-theme
  dashboard — and `scripts/browser_corpus.py`, which renders them in Chromium
  through `vistest.library.targets.capture`, the path `expect_screenshot(page)`
  takes since R3: animations disabled, caret hidden, `scale="css"`,
  `document.fonts.ready`, frames until two in a row are identical. The
  templates load nothing from the network. Their fonts are subsets of DejaVu
  Sans, Sans Bold and Serif (Latin-1 plus a few marks, hinting kept, ~130 KB
  together) shipped next to them with the Bitstream Vera licence, under
  family names of our own, so a font installed on the machine is never picked
  instead. Every element the corpus will change carries a `data-m` token; a
  template without one of the 22 tokens fails a test instead of silently
  losing a family. The environment is written down, not assumed: Playwright,
  the Chromium version, the build that drew the frame (the headless shell and
  the full Chromium of one release report the same `browser.version`; the
  product string over CDP tells them apart), OS, architecture, distribution,
  window, device scale factor, and the fonts Chromium actually drew each
  template with, read back over CDP (`CSS.getPlatformFontsForNode`) — which
  is how the first draft was caught drawing a `›` in Liberation Sans. Two
  fresh browsers in the same configuration must draw the same pixels, or the
  capture stops. New: `tests/test_browser_corpus.py` (templates are local,
  carry every target, ship their fonts; drawn only with those fonts; the
  control; the environment record). The Chromium checks are skipped, not red,
  where Chromium cannot start. `*.ttf` is marked binary in `.gitattributes`.
- **Mutations and noise.** `scripts/browser_corpus.py` now makes the pairs.
  SIGNAL: 22 families of one-element changes (`FAMILIES`) — fill, text,
  link and icon colour at ΔE00 2/4/8/15 (icons 4/8/15), a border appearing or
  disappearing, underline, radius +2/+4/+8px, padding −2…+2px, content moved
  down 1/2/4px, font size ±1px, bold, letter spacing +0.2/+0.5/+1px, line
  height +1/+2/+4px, an icon swapped, one character, one word, an element
  removed, two neighbours swapped, opacity 0.98/0.9/0.8/0.6, a shadow removed
  or made heavier, a focus ring — 50 steps per template, each labelled by the
  step itself. The colour steps are computed by a CIEDE2000 written out in
  the script (checked against Sharma's published pairs), not by the engine's
  own ΔE: the ruler must not be the thing it measures; the manifest gets the
  ΔE00 each step actually has after rounding to 8 bits. Four judgement calls
  were put to the maintainer and answered before anything was frozen: ΔE00 ≈
  2 is DISPUTED (kept, printed, never counted), a 1px shift is SIGNAL,
  opacity 0.98 is NOISE, letter spacing +0.2px is SIGNAL; the answer and its
  reason sit next to the magnitude. NOISE: the same DOM under eight rendering
  configurations (`NOISE_CONFIGS`: hinting none/full, no LCD text with no
  subpixel positioning, `text-rendering: geometricPrecision`, the page moved
  by 0.25 and 0.5px, the full Chromium instead of the headless shell, GPU
  rasterisation). A configuration that draws the baseline's pixels gets no
  pair and is recorded as such. Every frame is drawn twice, in two browsers
  started the same way, and must agree to the pixel, or the capture stops.
  Getting there took three fixes, each found by that control: a mutation is
  applied at DOMContentLoaded, before the first paint (the page checks
  `performance` for a paint entry) — applied after `load`, a change landed
  before or after the first raster depending on timing, and the same fill came
  out 7 pixels different in a second browser; a rendering stylesheet is
  present from the first paint for the same reason (a 0.25px shift of the
  dark template differed by 23 pixels about one run in six); and the focus
  ring is drawn on buttons, not on text inputs — an outline on an `<input>`
  moved one corner pixel in about one frame in eight, a Chromium behaviour
  worth knowing, not a mutation worth measuring. A stress run drew every
  frame four times across two browsers: no frame came out two ways. `offset`
  grows `padding-top`, not `margin-top`, which collapsed with the margin
  above it and moved nothing on the article.
- **Frozen, like the synthetic corpus.** `tests/browser_corpus/` now holds
  the corpus itself: `frames/<template>/base.png` once per template (every
  pair points at it rather than copying it), 336 changed frames, and
  `manifest.json` with, per pair, the template, family, magnitude, label,
  reason, split, the changed pixels and their box, the ΔE00 of a colour step,
  and the sha256 of every PNG; per template, its baseline's sha256 and the
  fonts it was drawn with; per rendering configuration, how many pixels it
  moved on each template and whether it made the corpus at all. 25.6 MB of
  PNG against a 40 MB budget, re-encoded losslessly at the highest
  compression. Of the eight configurations six are noise (hinting none, no
  LCD text with no subpixel positioning, `geometricPrecision`, the 0.25 and
  0.5px shifts, and the full Chromium, which draws text differently from
  the headless shell here — 26 to 117 thousand pixels per template, where
  `browser_probe/`, with the system's DejaVu and no web font, saw none) and two draw the baseline's pixels
  exactly and have no pair: `--font-render-hinting=full` and GPU
  rasterisation (there is no GPU here; the flags change nothing). The pairs: 276 SIGNAL, 42 NOISE (36 rendering + opacity 0.98 on
  six templates), 18 DISPUTED. `python scripts/browser_corpus.py
  --regenerate` is the only thing that writes there; `--check [--full]`
  redraws and compares without writing. **Calibration and held-out halves,
  by template:** table, form, cards and article calibrate; landing and dark
  are held out and never take part in choosing a threshold, a preset or any
  other constant of the engine — in the script's docstring and in the
  manifest (`split`, per template and per pair). New tests
  in `tests/test_browser_corpus.py`: files against the manifest, the budget,
  one baseline per template, labels on disk equal to the code's (relabelling
  without regenerating is red), the split, configurations without pairs, the
  environment record, the digest; and the drift check, which redraws every
  baseline, every configuration and one magnitude of every family on every
  template (`VISTEST_BROWSER_CORPUS_FULL=1`: all 336) — only in the
  environment the manifest records; anywhere else it is skipped with the
  difference named, not red. Drawn here: Playwright 1.56.0, Chromium
  141.0.7390.37 headless shell (build 1194), Ubuntu 24.04 x86-64, 1280×800 at
  1x, no container — Docker was available but `mcr.microsoft.com` is closed
  to this environment (403), so the `mcr.microsoft.com/playwright/python`
  image could not be pulled and no image tag is recorded. A third,
  independent full redraw (`--check --full`) matched all 342 frames.
- **`tests/benchmark.py --corpus browser`.** Measures the browser corpus
  (`tests/benchmark_browser.py`): VisTest strict, balanced and loose, with the
  AI layer as `CheckService` builds it (`--no-ai` without), and the native
  Playwright comparator — `getComparator('image/png')`, what
  `toHaveScreenshot()` calls — on a grid, threshold {0.2, 0.1, 0.05} ×
  maxDiffPixels {0, 25, 100, 500}, from `scripts/bench_playwright_grid.mjs`
  (@playwright/test 1.63.0 pinned in `scripts/bench`). The grid calls the
  comparator once per pair and threshold and applies its own rule,
  `count > maxDiffPixels` (quoted from playwright-core), to the count it
  reports; twelve pairs spread over the corpus are also compared with every
  setting for real and must agree. Its JSON, `docs/benchmark_browser_native.json`,
  carries the corpus digest and is refused on other files; it is committed,
  so the table needs no Node to be read again. Output: a summary per tool
  (false failures and misses on the calibration half, on the held-out half
  and on both; DISPUTED failures printed, never counted), a table by family
  for each tool, the three worst families of every tool, and the frontier
  «false failures against misses» of each tool on each half. `--no-timing`
  output is byte-identical between runs and between OpenCV 4.13 and 4.14;
  `--markdown` writes `docs/benchmark_browser.md`. Without `--corpus` nothing
  changed: `--no-timing` and `--compare --no-timing` print the same bytes as
  before (checked against 235d284). `docs/benchmark.md` gains a «Browser
  corpus» section — the split, stated from `scripts/browser_corpus.py`'s own
  constants — which the generator now writes too, so a regenerated file keeps
  it. **What it says, on this corpus:** VisTest balanced fails 28 of 42 NOISE
  pairs and misses 132 of 276 SIGNAL; Playwright's defaults, 36 and 103. No
  setting of either tool gets below 26 false failures: the text-rendering
  configurations (hinting none, no LCD text with no subpixel positioning,
  `geometricPrecision`, the full Chromium) fail on every template for every
  tool and setting. VisTest misses every change of text, link and icon
  colour — ΔE00 15 included — and every underline, focus ring and removed
  border. On the misses looked at (text, link and icon colour at ΔE00 15, a
  focus ring, an underline) the result has zero regions: the change mask is
  there — 0.39% of the table frame for the text colour — and nothing of it
  survives to a region. The engine is
  not touched in this stage; those are R2's inputs. Tests:
  `tests/test_benchmark_browser.py` (every pair on exactly one row, errors on
  the right side, halves adding up to the whole, the frontier, the worst
  rows, a report that is the same twice, the grid's digest and rule,
  VisTest on one pair of every kind). `browser_probe/README.md` now says the
  probe is superseded, what grew out of it and what was deliberately not
  carried over.
- **Noise from another machine: `--capture-noise-only` and
  `--import-noise`.** The eight rendering configurations are flags of one
  Chromium on one Linux; users break on another machine — a developer on
  Windows, CI on Linux. `python scripts/browser_corpus.py
  --capture-noise-only --tag <name>` runs on that machine and draws only the
  six baselines — no mutations — through the same `Session.frame` and
  `vistest.library.targets.capture` as the corpus (`scale="css"`, 1280×800 at
  1x, `document.fonts.ready`, the stability loop), each in two fresh browsers
  that must agree to the pixel. It writes into `bench_out/os_noise/<tag>/`,
  never into the corpus (a path inside it is refused, so is a directory that
  already holds a capture), and next to the PNGs `environment.json`: OS and
  its version, architecture, Playwright, the Chromium version, build and
  product string, the fonts drawn with over CDP (a font of the machine stops
  the capture: such a frame measures fonts, not the rasteriser), the screen's
  DPI and, on Windows, font smoothing (ClearType, contrast, orientation — Skia
  asks the OS for them), `devicePixelRatio` as the page reports it, the
  sha256 of every PNG, how many pixels each baseline differs from the
  corpus's, and every recorded fact in which that environment differs from
  the corpus's — a different Chromium build is written there, not hidden.
  `--import-noise <dir>` brings it into the corpus as the NOISE family
  `os_<os>` (`os_windows`): one pair per template against the corpus
  baseline, `frames/<template>/os--<os>.png`, split by template like every
  other pair, and an `os_noise` section in the manifest (the environment,
  what differs, the fonts, pixels per template). It refuses a capture that
  was not taken the corpus's way (viewport, scale, capture settings, device
  pixel ratio), a sha256 that does not match, a missing template or a
  system font, and leaves the corpus untouched when it does; a second import
  of the same OS needs `--replace`. The drift test for those frames redraws
  them only on a machine with the same Playwright, Chromium build, OS and
  its version, architecture and font smoothing, and is skipped with the
  difference named everywhere else; the DPI is recorded but not compared,
  because the context draws at scale 1 whatever the screen is.
  `--regenerate` keeps imported frames only while the baselines they pair
  with come out byte for byte the same, otherwise drops them and says so.
  `tests/benchmark.py --corpus browser` puts `os_<os>` on a NOISE row of its
  own and names the machine under the corpus line. The environment record
  gains `os_version` (the distribution on Linux, not the kernel) and `host`
  (DPI, font smoothing). New tests in `tests/test_browser_corpus.py` and
  `tests/test_benchmark_browser.py`; a capture on the corpus's own machine
  draws all six baselines with 0 pixels apart from the frozen ones.
- **The first other machine: Windows (`os_windows`).** Six baselines drawn
  on Windows 11 25H2 (build 26200, AMD64, 96 DPI, ClearType on, contrast
  1200, RGB) with Python 3.13.7, Playwright 1.56.0 and the same Chromium
  build as the corpus, 141.0.7390.37 headless shell 1194 — the difference is
  the OS, not the browser; every fact that differs is in the manifest's
  `os_noise.os_windows.differs_from_corpus`. Every frame was drawn twice
  there, 0 px apart, with the shipped fonts only and the same glyph counts as
  on Linux. Pixels apart from the Linux baseline: table 51 678, form 28 210,
  cards 26 345, landing 46 348, article 113 190, dark 30 473 (2.6–11% of the
  frame; mean channel difference 86–102 where they differ — whole glyphs,
  not a shade). None of the Linux rendering configurations reproduces it: the
  nearest (full Chromium, or no LCD text with no subpixel positioning) is
  still 78–92% of those pixels away. Six NOISE pairs — four calibration,
  two held out — 0.39 MB, the corpus is 25.9 MB of 40. **What it changes:**
  every tool at every setting fails all six. NOISE goes from 42 to 48 pairs,
  SIGNAL, DISPUTED and every old verdict stay as they were (the native grid
  was recomputed: 336 old results byte for byte the same); VisTest balanced
  28/42 → 34/48 false failures, strict 32/42 → 38/48, loose 26/42 → 32/48;
  Playwright's default 36/42 → 42/48, and its best setting on false
  failures, 0.05/500, 34/42 → 40/48 — the smallest Playwright count on a
  Windows pair is 7 838 pixels at threshold 0.2, far past maxDiffPixels 500.
  `docs/benchmark_browser.md` and `docs/benchmark_browser_native.json`
  regenerated (OpenCV 4.13.0). The engine is not touched.

### R3: capture parity with `toHaveScreenshot`, and speed

- **Two loose ends of the public repository.** `SECURITY.md` asked readers to
  write to «<security contact — fill this in>»; it now sends them to a private
  advisory, https://github.com/marshlw/vistest/security/advisories/new, and the
  Russian editing note above it is gone (one line less in the i18n debt). The
  README's *Current figures* now say under the table what the table is
  measured on: a synthetic corpus drawn by OpenCV, where a first probe on real
  Chromium renders did not confirm the advantage in either direction, and a
  corpus captured from a browser is in progress. And the sentence that called
  real screenshots useless for tuning is narrowed to what is true: useless
  without labels, not with mutations whose label is known in advance. Same
  change in `README.ru.md`.
- **The library photographs a live page the way `toHaveScreenshot()` does.**
  `library/targets.capture` used to take one bare `screenshot(type="png")` of
  a `Page` or `Locator`: no stopping animations, no hiding the caret, no
  waiting for fonts, no second look. Now it passes
  `animations="disabled"`, `caret="hide"` and `scale="css"`, waits for
  `document.fonts.ready` first (the script is `WAIT_FONTS_JS` in
  `capture/stabilize.py`, next to the server's `WAIT_MEDIA_JS`; images are not
  waited for, because a lazy image outside the viewport never completes), and
  takes frames until two in a row are identical byte for byte — Playwright's
  schedule of pauses, at least two frames, for at most
  `capture.stable_timeout_ms` (new key, default 5000; 0 takes one frame and
  claims nothing), overridden per check by
  `expect_screenshot(..., stable_timeout_ms=...)`. A page that does not settle
  is compared on its **last** frame, and says so: in the reason (so in the
  exception and the CI log), in the result's notes, and in the report row's
  summary line; accepting such a frame under `--vistest-update` warns. Every
  report row of a live page carries `capture: {frames, stable, elapsed_ms,
  timeout_ms, scale, pixel_ratio}`. A bad `stable_timeout_ms` is a
  `ConfigError` naming the file; a bad argument is refused at the call.
  **Behaviour change — baselines of moving pages.** A baseline captured
  mid-animation, or with the caret visible, will not match a frame of the
  stopped page: accept it again, once.
  **Behaviour change — HiDPI.** `scale="css"` makes the picture one pixel per
  CSS pixel on every screen. On a 2x screen that halves the picture. The key
  follows the picture, not the config: its `Nx` is now the scale of the
  picture — 1 under `scale="css"`, the page's devicePixelRatio under
  `expect_screenshot(..., scale="device")`, and `capture.device_scale_factor`
  only when the ratio cannot be read. So a baseline taken before at device
  scale on a 2x screen is still found under its `1x` directory and fails as
  a size change — and that one case is not left as «the picture changed
  size»: when the baseline is exactly k times the screenshot on both axes, the
  reason says it was probably taken at device scale and names both ways out
  (`--vistest-update`, or `scale="device"`, which files 2x pictures under a
  `2x` key of their own). On 1x screens — every default CI runner — nothing
  moves. A library user who set `capture.device_scale_factor: 2` in
  `vistest.yaml` finds the key back at `1x` under the default scale;
  `BaselineMissing` names the `2x` directory where the old baseline is.
  New tests: `tests/test_library_capture.py` — the options sent, the font wait
  and its failure, the loop on a fake clock (schedule, last frame, a slow first
  frame, zero), the report row, both config errors, scale and key, the HiDPI
  hint; and against a real Chromium, skipped when there is none: a control
  proving the page moves under a bare `screenshot()`, a CSS animation plus a
  focused input giving a stable frame and a pass three times in a row at
  different moments of the cycle, a page updating its text every 16 ms judged
  on its last frame with the line about it, and a `Locator`.
- **A second frame on failure — in the library too, through the server's
  function.** The logic of `CheckService._retry` moved to
  `vistest/core/retry.py` as `second_look(first, frame, recapture,
  recompare)`; the service's `_retry` is now six lines that say how *it*
  compares (stored baseline, its masks, the snapshot's thresholds) and call
  it, and `expect_screenshot` calls the same function. In the library a
  failed check of a `Page` or `Locator` takes exactly one more frame (one
  `screenshot()`, no stability loop), masks what moved between the two, and
  compares the **first** frame again; pictures handed in as bytes, arrays or
  files are never retaken, and `capture.retry_on_fail: false` turns it off as
  it does for the server. One change in behaviour for both: a region of the
  first comparison that is absent from the second — its pixels were among
  those that moved — used to vanish from the result; it is now kept in
  `suppressed` with `suppressed_by="unstable: did not reproduce on a second
  capture (N% of the page moved between the two frames)"`, counted by the
  report and the pytest summary under the new phrase «suppressed: did not
  reproduce on a second capture» (`PREFIX_UNSTABLE` in `plugins/runtime.py`).
  Regions that were already suppressed keep their own reason. Their pixels
  are not added to `suppressed_pixels` — the second comparison ignored them —
  so the three-way split of `changed_pixels` still adds up, and a test says
  so. The notes are unchanged, word for word. New tests in
  `tests/test_retry_on_fail.py` (the unstable region, the header that stays
  red beside it, the count, the accounting, the server going through the
  shared function) and `tests/test_library_capture.py` (the same behaviour
  from `expect_screenshot`, the first frame as the artifact, no extra frame
  on a pass, the switch, no retake of a picture handed in).
- **`--vistest-update=missing|changed|all`; the default is `changed`.**
  **Behaviour change.** The flag used to rewrite every baseline whose bytes
  differed from the new picture — including checks that *passed* on a
  re-encoded or subpixel-shifted frame — so accepting two real changes gave a
  pull request touching every PNG in the project. Now: `missing` writes only
  baselines that do not exist; `changed` writes those and the ones whose
  check failed (after the second look, so a failure that did not reproduce is
  not accepted); `all` is the old behaviour. A bare `--vistest-update` means
  `changed`, as does `VISTEST_UPDATE_BASELINES=1` and `LibraryContext(update=
  True)`. One more consequence of the flag taking a value: a bare flag
  followed by a path — `pytest --vistest-update tests/` — now reads the path
  as the mode and stops with a message that says so and gives the two
  spellings that work (`pytest tests/ --vistest-update`,
  `--vistest-update=changed`); guessing would decide what is written into the
  repository. The report row of a new baseline and the run summary name the
  mode. `LibraryContext.update_mode` validates whatever `update` holds; the
  plugin keeps its own copy of the mode list so that it still imports nothing
  heavy, and a test holds the two lists together. New tests:
  `tests/test_library_update.py` — the three modes on four snapshots (new,
  byte-identical, byte-different but passing, failing) with a control that the
  passing and failing pairs really are, `True` and the environment variable,
  and every command-line spelling in a subprocess project, including the path
  after a bare flag.
- **Re-capture from the interface asks for `--vistest-update=all`.** The
  Re-capture button on a snapshot captured by one of our own tests runs that
  test with the update flag. It passed a bare `--vistest-update`, which now
  means `changed`: a snapshot whose check passed kept its old baseline, and
  the job reported success for a re-capture that wrote nothing. The button
  asks for this picture to become the baseline, so it passes `all`. Projects
  connected from outside are not affected — they are updated through the
  adapter (`VISTEST_ADAPTER_UPDATE`), not through this flag. New test:
  `tests/api/test_recapture_update_mode.py`.
- **`compare` returns at once on identical pixels, and computes ΔE00 only
  where the RGB differs.** A green run is mostly pixel-identical pairs, and
  the cascade paid full price for them: alignment, two Lab conversions,
  CIEDE2000 and SSIM over the whole frame, about a quarter of a second for a
  900×1200 page. Identical arrays now return a PASS with no regions and the
  metrics of a perfect match (SSIM 1, ΔE 0, no changed pixels, no shift),
  a zero ΔE map and an empty mask for any renderer, and one note, «Identical
  to the baseline, pixel for pixel.» — the only field that differs from what
  the cascade used to say about such a pair (it said «Alignment: no shift»,
  or on a flat frame an alignment of low confidence). For every other pair,
  `color.delta_e_ciede2000_where` computes CIEDE2000 on the pixels whose RGB
  differs and leaves 0 elsewhere — which is what the formula gives a colour
  against itself, so the map is the same float for float (tested bit for
  bit, on the corpus and on random frames). Past 80% of pixels selected
  (`DENSE_SHARE`; sensor noise and JPEG touch nearly all of them) picking
  pixels out costs more than it saves, so the full map is computed and the
  rest zeroed. The «RGB differs» map is three channel comparisons OR-ed
  (2 ms) rather than `np.any(..., axis=2)` (15 ms).
  Nothing the engine reports moved: `tests/benchmark.py --no-timing`, with
  and without `--compare` and `--no-ai`, prints byte-identical output before
  and after, under OpenCV 5.0 and 4.14 alike, and `--record-metrics`
  rewrites `metrics.json` to the same bytes. Timing, `compare()` alone after a
  warm-up, median of three, preset balanced, same machine: an identical
  900×1200 pair **277 ms → 0.7 ms**; the 54 corpus pairs, mean **289 → 171
  ms**, median 276 → 154; pairs where most pixels differ (sensor noise,
  JPEG, dither) unchanged within measurement noise. A green library run of
  100 snapshots, sum of `compare()`: **26.96 s → 0.08 s** when the pictures
  are pixel-identical (wall time of the 100 checks 28.8 s → 1.5 s), 30.4 s →
  19.1 s when they are the corpus's passing-but-noisy pairs.
  New tests: `tests/test_compare_fast.py` — the identical result field by
  field and against the full cascade's metrics, its maps, one changed pixel
  and a size change taking the long way, a 900×1200 identical pair under a
  generous 50 ms (best of five), the selective map bit for bit on random
  frames, empty, chunked and dense selections, and the whole result on every
  third corpus pair against the engine with both shortcuts patched out.
  `tests/test_margins.py` caught its ΔE map by spying on
  `color.delta_e_ciede2000`, which the comparator no longer calls for every
  pair; it now reads the map the comparator returns in `maps["de_map"]` —
  the same array the consensus read — and its gaps are unchanged.

### Stage R0 tails

- **A directory role follows the groups down as well as up.** `_try_external`
  only ever raised a role (`_RANK[role] > _RANK[current]`), so somebody taken
  out of the admins group in the directory stayed an administrator here for
  good — while the comment above that code and the README said the role was
  recalculated at every sign-in. Now an existing directory account gets the
  directory's role at each sign-in, in both directions, and every change is
  audited as `user.role` with the provider as the author and `from`/`to` in
  the details. Two exceptions. A role set by hand (`PATCH /api/users/{login}`)
  is pinned in the new column `user.role_manual` and no sign-in moves it, up
  or down; `{"role": "directory"}` to the same endpoint hands it back and the
  next sign-in recalculates it (refused for a local account, and for your own
  account, like removing your own administrator role). And the last active
  administrator is never demoted: the role is kept and `auth.role_kept` says
  why, so a mistake in the role map cannot leave the installation without an
  administrator. The role is read from the `user` row on every request, so
  the demotion reaches open sessions — at the person's next sign-in, because
  that is the only time the directory is asked.
  `test_the_role_follows_group_membership_on_every_sign_in` stopped after
  Boris's sign-in without a single assert (and raised a row that did not exist
  yet); it now checks that a role raised by hand survives the sign-in. New
  tests in `tests/api/test_directory.py`; before the change,
  `test_leaving_the_admins_group_takes_the_role_at_the_next_sign_in` failed
  with Anna still an admin.
  **Upgrade note — check the roles of directory users.** The migration pins
  (`role_manual=1`) every directory account that has a `user.role` entry in the
  audit, that is, every role an administrator set by hand. The audit keeps a
  year by default (`retention_audit_days`), so a role set by hand before that
  is not found: at the next sign-in such a person gets the role their groups
  give, which may be lower. Look through **Settings → Team** after upgrading,
  and set by hand again any role that should not follow the directory.
- **A mistyped local password is one audit row, not two.** With a provider
  installed, every failed sign-in to a local account wrote
  `auth.external_refused` next to `login.failed`, so an administrator's typo
  read as an attack on a directory that was never asked. Now the sign-in
  handler does not reach the provider for a local row at all, and the one
  `login.failed` row carries `reason: local_account, directory: not_asked` in
  its details. The throttle counts `login.failed` by `who`/`target` only, and a
  test shows the pair limit trips at the same attempt either way.
  `auth.external_refused` stays for the rare, useful case: the directory said
  yes to an account disabled here. Before the change
  `test_a_mistyped_local_password_is_one_audit_row` and the reworked
  `test_a_directory_admin_does_not_sign_in_as_the_local_admin` failed on the
  extra row. `SECURITY.md` now says what this costs in response time (a local
  login answers without the directory, so timing shows which logins are
  local), what bounds the guessing, the directory role rules, and that a
  directory disable ends new sign-ins but not sessions already open — disable
  the account here too to cut them; a test holds that, and another holds the
  numbers `SECURITY.md` quotes to the constants in `auth.py`.
- **`/api/baselines/export` and `/api/baselines/import` are core routes.**
  `main` mounted `vistest/api/sync.py` inside `_wire_plugins`, and only when a
  `BaselineSyncBackend` was registered — and the one that was, the core's own
  `ArchiveSyncBackend`, came through the bundled extensions. So the command
  line moved archives with plugins off and the server did not know the paths.
  Now the router is mounted unconditionally, next to the baselines router and
  outside the plugin wiring, so a broken plugin cannot take it away; it uses
  a plugin's `BaselineSyncBackend` when one is registered and
  `transfer.ArchiveSyncBackend` otherwise, and never answers 404 for want of
  a backend. `_extensions` no longer registers the archive. Rights (reviewer,
  per project), `MAX_IMPORT_BYTES` and the `baselines.imported` audit row are
  unchanged; so are the `BaselineSyncBackend` slot and `API_VERSION`.
  `capabilities()["baseline_sync"]` keeps meaning «a third-party backend is
  installed» — the interface does not read it. Orchestration between
  installations (schedules, promotion, conflict policies) will come as a
  plugin's own routes.
- **`/api/health` lists every route again.** FastAPI 0.141 keeps an included
  router as one `_IncludedRouter` entry without a `path`, so the list held
  only the routes declared on `app` itself: nothing from the baselines,
  check, auth or plugin routers. `test_degradation.py` asserted that
  `/api/ldap` and `/api/baselines/export` were *not* in it — true, and
  meaningless. The list now walks included routers.
  `test_server_mode_with_plugins_disabled_is_complete_and_deterministic` and
  `test_server_mode_with_broken_plugins_behaves_as_without_them` now check,
  with no plugins and with broken ones, that the ldap routes are absent and
  that a reviewer exports an archive, a dry-run import answers with a plan, a
  viewer is refused, and both paths are in `/api/health`; both failed before
  the change.

### Pre-publication hygiene (review of 25.09)

What the plan counted as done and the code did not, and what stood between
the repository and publication outside the engine. The engine's answers do
not move: `benchmark.py --no-timing` is byte-identical before and after, and
`metrics.json` re-records with no diff.

- **The `rerender` rule draws its shifts through `core/warp.py`.**
  `explain._search` was the last direct caller of `cv2.warpAffine`, past the
  door `align` and `refit` go through. Its quarter-pixel steps lie on OpenCV
  4.x's 1/32 px grid, so nothing it printed was wrong — by luck.
  `test_warp.py::test_both_paths_go_through_the_same_door` now spies on all
  three stages.
- **`vistest baselines export|import` is core.** The command called the
  archive through the plugin registry and answered «not available in this
  installation» with plugins off. It now calls `vistest.transfer` directly and
  works with `VISTEST_DISABLE_PLUGINS=1` in all three modes; `vistest.transfer`
  left the extension list in `tests/test_plugin_boundary.py`. What stays
  behind `BaselineSyncBackend` is orchestration between running
  installations — for now the server's `/api/baselines/export|import` routes.
  `test_degradation.py::test_baselines_travel_with_every_plugin_switched_off`
  drives export → dry run → import through the command line for `new`,
  `update` and `replace`.
- **A directory sign-in no longer takes over a local account.** `_try_external`
  found the row by login and wrote `source`, `active=1` and `status='active'`
  into it: a directory entry called `admin` became the local administrator,
  and an account switched off here came back on at the next directory sign-in.
  Now a local row under that login is a refusal, audited as
  `auth.external_refused`, with no `UPDATE` — and the provider is not asked,
  so a password typed for a local account never reaches the directory; an
  external row switched off here
  is refused the same way; and no sign-in writes `source`, `active` or
  `status`. The invariant is in `SECURITY.md`; four tests in
  `tests/api/test_directory.py` hold it.
- **The wheel carries the review UI.** `frontend/` lives outside the package,
  so a wheel had none and `pip install "vistest[server]" && vistest serve`
  answered 404 on `/ui/`. A build step in the new `setup.py` (the rest of the
  metadata stays in `pyproject.toml`) copies it into the wheel as
  `vistest/frontend`, where `_find_frontend` looks first; editable installs are
  left alone. `docker/Dockerfile.api`, the non-editable image, now copies
  `setup.py` too. `tests/test_wheel_ui.py` builds the wheel from a copy of the
  sources, installs it with `[server]` into a fresh venv, starts `vistest
  serve` from an empty directory and asks for `/ui/`; marked `packaging`.
- **`CaptureConfig.timezone` and `.locale` default to `None`**: the browser
  keeps its own. `Europe/Moscow` and `ru-RU` were this repository's choice
  shipped as everyone's default; they stay only in this repository's
  `vistest.yaml`. Pin them in your project's `vistest.yaml` for baselines
  that travel between machines.
- **The `browser` and `full` extras ask for `playwright>=1.47,<2`**, not
  `==1.47.0`: an exact pin refused every Playwright but ours in somebody
  else's environment. The pin reproducible pictures need lives with the image:
  `docker/Dockerfile.full` and `docker/Dockerfile.runner` take
  `PLAYWRIGHT_VERSION` once and use it for both the base image tag and
  `pip install "playwright==…"`.
- **Dead code out of the core**: `color.delta_e_76`, `antialias.text_shift_mask`,
  `structure.gradient_similarity` (and `gradient_magnitude`, used only by it),
  `align.find_local_shift`, `classify.recompute_severity`. Nothing called
  them. `delta_e_76` also carried a false docstring: ΔE76 is not an upper bound
  of ΔE00 — on close pairs ΔE00 comes out larger in a fraction of a percent to
  about 1.4 % of cases depending on the sample, by up to ~1.45×, so a
  pre-filter built on it would have dropped real differences.

### A flat recolour is found: the last miss of the frozen corpus

- **`header color` was missed on both rasters**, and not by segmentation.
  The header is repainted, ΔE00 6.7 on every one of its 57 600 pixels — above
  the 2.3 of a visible difference, below the 9.2 of `strong_color`. Consensus
  wants structure to agree, and SSIM is blind to a uniform shift of colour by
  construction: it removes the mean before it compares. Only the bottom edge
  of the header changed structure, a 900×2 line, and the opening removed it.
- **New third term of consensus, next to `strong_color`: large flat recolour**
  (`core/recolour.py`). A connected area of ΔE00 > `delta_e_threshold` is taken
  past consensus when it covers **≥ 1.25 % of the frame** and ΔE is uniform
  across it, **σ/μ ≤ 0.09**. Both numbers sit in the geometric middle of a
  measured gap, recorded beside them in `core/settings.py`
  (`FLAT_RECOLOUR_AREA`, `FLAT_RECOLOUR_CV`) — frozen corpus and
  `generate(6)`, 178 pairs, every component: uniform noise is at most 0.309 %
  of the frame (a scroll bar), a header at least 5.217 % (16.9×); a header is
  at most 0.049 uneven, large noise at least 0.178 (a re-dithered gradient,
  3.6×). Area is in percent, not pixels, so it means the same on any screenshot
  size.
- **Rejected on the way, with the measurement**: a test of direction (all
  pixels moved along one Lab vector) separates nothing the two conditions do
  not; a threshold on ΔE itself has no room (header μ 3.86 vs gradient μ 3.09).
- **What it does not find**: a small uniform recolour — a 40×20 badge — sits
  where the scroll bar sits. A known limit, named in the module.
- **Frozen corpus: 54/54, 0/32 false failures, 0/22 misses** (was 52/54,
  0/32, 2/22). `metrics.json` moves on two pairs only, the two `header color`
  rows. `generate(6)`: misses 6/54 → 0/54, false failures unchanged at 7/70
  — the same seven pairs as before (`font weight` ×6, a known limit;
  `layout1/shadow radius`, an open defect).
- **`tests/test_margins.py`** pins the room between noise and content for this
  rule and for `explain.AA_KEEP_ABOVE` (now recorded as `AA_KEEP_ABOVE_GAP`):
  it fails, naming the gap and the pair, when either side crosses the
  threshold or moves past the worst case its record was placed against. It
  also lowers the area threshold under a scroll bar on purpose and checks that
  `explain.py` still names the bar and the verdict stays pass.

### metrics.json compares with a measured tolerance, the table prints what reproduces

- **The frozen metrics went red on the first run on another machine**, on the
  noisiest pair of the corpus and in the last digit: `sensor noise σ=3.0`,
  `changed_area_pct` 0.689 against 0.6891, `ssim` 0.94783 against 0.94782,
  verdicts identical. The file recorded four decimals of a percentage and five
  of SSIM because that is what the table printed — a precision those numbers do
  not have. Same defect as the shift the engine used to print in
  `suppressed_by`: claiming precision that is not there.
- **What was measured** (25 environments, 54 pairs): the **version axis does
  not move these numbers at all** — numpy 2.3.5 / 2.4.6 / 2.5.3, Python
  3.11 / 3.13 and opencv-python-headless 4.14 / 5.0, ten combinations, agree to
  every digit a float has; so do numpy's own SIMD dispatch and OpenCV's IPP
  path. **One thing moves them: which SIMD kernels OpenCV dispatches to.**
  Without AVX2 the SSIM map differs, the change mask gains or loses a pixel at
  the threshold, and area, ΔE and severity follow. `core/structure.py: _blur`
  (`cv2.GaussianBlur`) is the only stage involved — every other stage of the
  cascade is bit-identical across the split.
- **Rounding harder was not the answer, and the measurement said so.**
  Rounding is a tolerance with cliffs: at two decimals of a percentage
  `sensor noise σ=3.0, thin glyphs` sits 3.7e-04 from a rounding boundary
  while that number moves by 3.8e-03 between machines — green by luck. So the
  file stores six decimals and compares them against a **flat tolerance**
  (`corpus.METRICS_TOLERANCE`): severity 1e-04, changed area 5e-03, ΔE00 2e-02,
  SSIM 1e-04, each above the widest spread measured (headroom x1.3 to x2.2).
  Not a weaker check — a check shaped like the thing it checks; above the
  floor nothing is forgiven.
- **Each tolerance carries the measurement it came from** — `spread`,
  `environments`, `measured` — in `corpus.METRICS_TOLERANCE` and in the file
  next to the number, and a failure prints all of it:
  `(Δ 7.0e-03, tolerance 5.0e-03, widest spread measured 3.9e-03 over 25
  environments on 2026-09-18)`. A Δ a little over the tolerance and of the
  same order as the spread is a machine outside the sample; a Δ orders above
  it is the engine. Without the provenance, in six months those two look
  alike.
- **The sample is all x86-64** (`corpus.METRICS_SAMPLE`), which the file says
  about itself. It covers OpenCV's SIMD dispatcher inside one instruction
  family; Apple Silicon and Graviton are another. Red from ARM on
  `changed_area_pct` alone, verdict and sentences intact, is most likely the
  new platform — and the way to widen the floor for it is to measure there and
  rewrite the provenance with the tolerance, not to raise the number until it
  passes.
- **Three things keep zero tolerance on every machine**: the verdict, the
  region count, and the sentence the engine writes into `suppressed_by`, with
  its digits masked (`antialias: the baseline moved +#,-# px reproduces #% …`).
  The counts inside a sentence follow the change mask, which the tolerances
  already cover; which rule fired and what it claims may not move quietly.
- **More sensitive than the rounding it replaced**, measured on the previous
  entry's own defect: `core/align.py` showed on six rows of the detailed table;
  rounded, five survived; with the tolerance and the strict sentence, all six
  do — `antialias 0.4px` moves no metric past its tolerance and is caught only
  by its explanation changing. A failure line now reads
  `de_mean 15.202669 -> 15.239262 (Δ 3.7e-02, tolerance 2.0e-02)`, so a hair
  and a mile do not look alike.
- The **detailed table prints the reproducible precision** — changed area 2
  decimals, ΔE 1, SSIM 3, severity 1. That is about honest publication, and it
  is a separate question from how sensitively the file detects a change.
- Not done, deliberately: the blur could be written out in numpy the way
  `core/warp.py` was, which would remove the machine dependence entirely and
  let both the precision and the tolerance go away. **Measured at about +28% of
  a comparison** (`structure.ssim_map` 18 ms -> 99 ms per page, x5.5) against a
  budget of 10% for this line of work. Whoever revisits it need not measure
  again.
- **`docs/benchmark.md` now states what the file actually promises, and the
  paragraph is generated** from `corpus.METRICS_TOLERANCE` rather than typed,
  so it cannot outlive the numbers it describes. Two claims were cut down to
  what is measured: "zero tolerance" became strict on verdicts, region counts
  and the rule named in a suppression — the sentence without its digits, which
  is the part that reproduces — and the published tolerances are now said to
  be measured on x86-64 and nowhere else, with the same point repeated in the
  caveats section, where a sceptical reader looks first.

### The engine is deterministic across OpenCV majors, and a test says so

- **`core/align.py: apply_shift` was the last stage that still moved a picture
  with `cv2.warpAffine`**, and therefore the last reason the same two PNGs came
  out with different numbers on OpenCV 4.14 and 5.0: six rows of the detailed
  benchmark table differed in region metrics — verdicts alike — because that
  one call rounds the offset to a 1/32 px grid on 4.x. It now goes through
  `core/warp.py` like everything else. **The detailed table is identical on
  4.14.0.94 and 5.0.0.93: all 54 pairs, every metric, not the verdicts alone.**
  Verdicts are unchanged (52/54, 0/32 false failures, 2/22 misses).
- **The second lie of the same kind, in the same file**: `Alignment.reason`
  printed `compensated shift dx=+0.25 dy=+0.19` — the compensation asked for,
  while 4.x applied 8/32 and 6/32. The estimate now goes through
  `warp.applied_shift` before it is used, so the note names what was drawn.
- **`core/warp.py` is the one door**: `shift`, `to_uint8`, `shift_grid`,
  `applied_shift`, shared by `align`, `refit` and `explain`. Its rounding rule
  is written down and is now the cascade's only one — **round half to even**.
  `align.apply_shift`, `color.luminance`, the two copies of it in
  `comparator.compare` and `pngio.encode` used `astype(np.uint8)`, which
  truncates: not a rounding mode but a systematic bias of half a level
  downwards, in the grey channel every mask and SSIM is computed on.
  `tests/test_warp.py` stands a rounding backend in for the real one and checks
  that *both* stages report a grid point — a safety net over one path and not
  the other is how this defect survived.
- **`tests/benchmark_corpus/metrics.json`**: the engine's answer to the frozen
  corpus, frozen too — verdict, severity, changed area, ΔE00, SSIM, region
  count and the suppression sentences, for each of the 54 pairs.
  `tests/test_corpus_frozen.py` checks it on every run and names what moved and
  by how much. Three OpenCV differences were found by hand, months late, by
  diffing two printed tables; this is that diff, run on every merge. Rewriting
  it is deliberate: `python tests/benchmark.py --record-metrics`, and the diff
  of the file is the review. (How it compares was settled a day later — see
  the entry above.)
- `docs/benchmark.md` no longer carries the caveat that the input is frozen and
  the engine is not; it states the determinism and names the test that holds
  it. The environment line stays, for repeating a run and for the milliseconds.

### The engine names the shift it drew

- **`refit._shift` reported the shift it was asked for, not the one it
  applied.** `cv2.warpAffine` with `INTER_LINEAR` interpolates in fixed point
  on OpenCV 4.x — `INTER_BITS` is 5 — so it rounds the translation to a 1/32 px
  grid before a pixel is touched: a request for +0.30 moved the picture by
  +0.3125. `suppressed_by`, and from it the HTML report, the
  `ScreenshotMismatch` message and the pytest summary, printed +0.30 to two
  decimals — a precision that version does not have. OpenCV 5 applies the
  offset as given, so the same pair was explained with different numbers on the
  two majors. Explainability is the whole argument for calling something noise;
  an explanation that is wrong in the last digit it prints is worse than none.
- **`refit._shift` is now bilinear arithmetic on numpy arrays**: the same
  bilinear interpolation and the same `BORDER_REPLICATE`, written out, so the
  offset is applied exactly on every version of every library. Verdicts on the
  frozen corpus are unchanged — 52/54, 0/32 false failures, 2/22 misses, on
  both 4.14.0.94 and 5.0.0.93 — and no row of the detailed table moved on
  either version. It costs +1.5% (4.14) and +2.2% (5.0) of a comparison,
  measured over the whole corpus in one process with the two backends
  alternating.
- **Every shift is put on the grid the backend can draw, before it is drawn**
  (`refit.applied_shift`, used by `refit.fit` and `explain._grid`), so a `Fit`
  describes the picture that was drawn whatever the backend would have rounded
  to. `refit.shift_grid()` measures that grid rather than assuming it: with the
  shift above it is 0, and the guarantee survives somebody putting
  `cv2.warpAffine` back.
- `core/align.py: apply_shift` was left for its own change, the one above:
  it compensated the global shift with `cv2.warpAffine` and its `reason` string
  named the requested compensation. `core/explain.py` was measured and was
  never a source — it only ever warps by multiples of 0.25 px, which fixed
  point represents exactly.

### Competitors in the benchmark are measured with their own code

- **The published pixelmatch and Playwright rows are now native.**
  `scripts/bench_pixelmatch.mjs` runs the pixelmatch CLI from npm and
  Playwright's own `getComparator('image/png')` — the function
  `toHaveScreenshot()` calls — on `tests/benchmark_corpus/`, every tool with
  its defaults. Before, both rows came from our numpy port and the Playwright
  "native" row was pixelmatch with `threshold: 0.2`, i.e. our retelling.
- The figures did not change: on all 54 pairs the native verdicts equal the
  port's (pixel counts differ, the port has no anti-aliasing detector).
  README now names the tool versions instead of "numpy port".
- `scripts/bench/package.json` + `package-lock.json` pin pixelmatch 7.2.0 and
  @playwright/test 1.63.0 (`npm ci --prefix scripts/bench`). The result is
  kept in `docs/benchmark_native.json`, the generated table in
  `docs/benchmark.md` with every setting of every tool and all versions.
- `--native` checks the corpus fingerprint (sha256 of the manifest and every
  PNG) and refuses a JSON computed on other files. A tool missing from the JSON
  stays a port, marked, with a warning. `--with-ports` prints the port next to
  the original in the console for comparison; it never reaches the markdown.
- Wording fix: README and the generated table said the benchmark runs with the
  trained gate "on". It runs with `AIConfig()` as shipped, where
  `gate_enabled` is `False`.

### The benchmark corpus is frozen on disk

- **Benchmark figures published before this change were tied to the installed
  OpenCV version and are not comparable with each other or with the figures
  below.** The curated corpus used to be drawn on every run by
  `tests/synthetic.py` (`cv2.putText`), and OpenCV 5 rasterises text
  differently from 4.x: all 54 images of the corpus differed between the two.
- **`tests/benchmark_corpus/`** now holds the curated corpus as PNG pairs plus
  `manifest.json` (the `export_corpus` format, with `family` and `render`
  added). `corpus.build()` reads it (`corpus.load()`) and draws nothing.
  `tests/test_benchmark.py` and `tests/test_gate.py` run on it;
  `corpus.generate()` for training still draws on the fly, on purpose (its
  docstring says why).
- **Both rasters are frozen, as separate cases**: 27 cases drawn by
  `opencv-python-headless==4.14.0.94` and the same 27 drawn by `==5.0.0.93`,
  named with the suffix `, thin glyphs` (OpenCV 5 draws thinner, lighter
  strokes). 54 pairs; about 16 MB on disk, 12.7 MB of distinct files.
- **Redrawing is deliberate**: `python tests/benchmark.py --regenerate` redraws
  the raster of the installed OpenCV and leaves the other one alone.
  `tests/test_corpus_frozen.py` fails when the files and the generator
  disagree and names the drifted pairs; it also fails under an OpenCV version
  that has no raster in `corpus.RENDERS`.
- `python tests/benchmark.py --no-timing` leaves the timing column blank, so
  the output can be diffed byte for byte; the environment line goes to stderr.
  The output is split by raster.
- Figures on the frozen corpus, preset `balanced`, AI layer on, OpenCV
  4.14.0.94 and 5.0.0.93, numpy 2.5.3, Python 3.13, Linux x86_64: VisTest
  52/54 (26/27 on each raster), 0/32 false failures, 2/22 misses
  (`header color` on both rasters). The comparison table is byte-identical
  under both versions. The detailed table is not: on the same files, six rows
  differ in region metrics (not verdicts), because `cv2.warpAffine` with
  `INTER_LINEAR` rounds differently in 4.14 and 5.0. Closed since: the table is
  identical under both — see "The engine is deterministic across OpenCV majors"
  above.

### The anti-aliasing filter answers for itself

- **The per-pixel anti-aliasing veto no longer erases a region on its own.**
  Most pixels of a glyph that became another glyph pass the per-pixel test,
  and on the benchmark generator that is how four `price changed` regressions
  vanished before segmentation. The mask may still thin a group of changed
  pixels; a group it covers by half or more is taken out only if the
  baseline, re-drawn by what a rasteriser is allowed to do — sub-pixel
  position, fractional stroke weight, softness — reproduces at least 70% of
  it. Otherwise the mask is withdrawn from the whole group
  (`core/explain.py: explain_antialias`, `core/refit.py`).
- **Every erased group is a suppressed region** of kind `antialias`, with
  `suppressed_by` naming the re-drawing and what it left over:
  `antialias: the baseline moved +0.18,-0.07 px reproduces 100% of the
  changed pixels (0 of 412 left)`. `rerender` and `jpeg` now say it the same
  way. `ScreenshotMismatch` spells out up to three of them under `also:`, and
  the pytest summary lists five (all with `-v`).
- **Stroke weight is a continuous search** (±1 px per side, to 1/16 px), not
  one 3×3 morphology step, and neither weight nor softness may change the
  colour of a stroke: on one-pixel text "lighter" and "thinner" look the
  same, and the engine sides with calling it a change.
- **The shift is estimated, not searched** (Lucas–Kanade on the group's
  window). The prototype tried 980 warps per region and cost +30% per
  comparison; this costs ~6 ms per pair, within the noise of the total.
- Measured on `corpus.generate(6)`, 124 pairs, OpenCV 5.0: false failures
  7/70 → 7/70, misses 10/54 → 6/54, 107 → 111 correct, 303–316 → 298–320
  ms/pair. OpenCV 4.14: 7/70 → 7/70, 7/54 → 6/54. Showcase corpus (27): 25 →
  26 on 5.0, 26 → 26 on 4.14, 0/16 false on both.
- `diff.explain_noise: false` restores the unconditional veto.

### Extension points (plugin API v1)

Preparation for the open core. Nothing moves out of the repository yet; the
modules that will are now reached only through a plugin registry, and
everything works without them.

- **`vistest.plugins`.** The public contract is `vistest/plugins/api.py`,
  `API_VERSION = 1`, with four `@runtime_checkable` protocols:
  `RegionAnnotator.annotate(region, ctx)`, `RegionScorer.score(regions, ctx)`,
  `AuthProvider.authenticate(login, secret)` and `BaselineSyncBackend`.
  Plugins are found through the `vistest.plugins` entry-point group and call
  `register(registry)`. One active implementation per role (highest
  `priority`, then first registered; the loser is named in a warning);
  annotators run as a chain.
- **Nothing a plugin does can take a run down.** A plugin that fails to
  import, exits, has no `register`, raises inside it (its registrations are
  rolled back) or registers something that is not an implementation is a
  warning. A different `API_VERSION` is refused before `register` runs. At
  runtime, a scorer or annotator that raises, or answers nonsense, is a
  warning and the deterministic path. `VISTEST_DISABLE_PLUGINS=1` switches
  loading off entirely — nothing from the group is imported.
- **`fail_on: any | likely-real | confirmed`** (`plugins.fail_on`,
  `VISTEST_FAIL_ON`, `--vistest-fail-on`; default `likely-real`) decides what
  a scorer's estimate does to a check. Without a scorer it changes nothing.
  Two safety rules outrank any scorer, as they did the gate: a region over 20%
  of the page and a page-size change are never suppressed by a score.
- **No difference is swallowed quietly.** Every suppressed region carries
  `suppressed_by` (`noise: …`, `below-fail-on: …`, `ignored-kind: …` — the
  class set aside by `diff.ignore_kinds` now says so too). The library report
  lists them in every row, passing ones included, and pytest prints the total:
  `1 difference suppressed as rendering noise`. `ScreenshotMismatch` adds an
  `also:` line. When every region was suppressed, the area note says so
  instead of "not a single region passed filtering".
- **Region fields `score`, `annotations`, `suppressed_by`** are always
  present — `null`, `[]`, `null` without extensions — in the JSON, in the
  database (three new `region` columns; suppressed regions are now stored as
  well, marked, and excluded from every query that lists or counts regions)
  and in the interface (a score and remarks are shown when present; the
  comparison screen lists what was not counted). `gate_probability` and
  `perceptual_distance` are gone from `DiffRegion`: they were one extension's
  fields in the core's model; the score and annotations replace them.
- **Plugin tables.** `registry.add_migrations([...])`, applied once per step,
  versioned in the new `plugin_schema_version` table — separate from
  `PRAGMA user_version`. Tables must be named `ext_<plugin>_*`; an SQLite
  authorizer refuses anything else, core tables included, before it runs.
- **`plugins:` in `vistest.yaml`.** `enabled`, `fail_on`, `noise_below`,
  `confirmed_at`, `disabled`; any other key is kept, handed to plugins as
  their section, and — unless an installed plugin has that name — logged once
  as a warning. It is not refused.
- **`GET /api/capabilities`** — `region_scores`, `region_annotations`,
  `external_sign_in`, `baseline_sync`. The interface hides what is false: no
  directory card, no score column. No messages about it.
- **Moved behind the protocols, still in the repository:** the region gate
  and its feature extractor and the perceptual filter (scorer + annotator),
  LDAP/AD sign-in (`AuthProvider`; `/api/ldap` is now served by it) and
  baseline transfer between installations (`BaselineSyncBackend`;
  `/api/baselines/export|import` and `vistest baselines export|import` exist
  only when it is active). They are registered by the temporary
  `vistest._extensions` package through the entry point in `pyproject.toml`
  — **reinstall (`pip install -e .`) to register it**. Attribution stays in
  the core. `tests/test_plugin_boundary.py` fails if a core module imports any
  of them by name. `set_annotator` keeps its old contract.
- Benchmark unchanged: 24/27, 0/16 false failures with the extensions; 23/27
  with `VISTEST_DISABLE_PLUGINS=1`, the same as `--no-ai`.
- New tests: `test_plugins.py`, `test_degradation.py` (library and server
  scenarios with plugins disabled and with a set of broken plugins installed),
  `test_plugin_boundary.py`.

### Segmentation keeps text; noise is explained, not erased

- **Close before open.** `segment.clean_mask` now closes the change mask
  before opening it. The opening (5×5 ellipse) used to run first and erased
  every stroke thinner than five pixels — text: 13 262 changed pixels of a
  text edit became 374. The benchmark corpus goes from 3 misses to 1
  (`price changed` and `promo text` are found; `header color` is a separate
  cause). False failures stay at 0/16 — with and without the AI layer.
- **Deterministic noise explanations** (`core/explain.py`, open engine, no
  model). The new order also keeps what the old one erased by accident; each
  such region is now suppressed by a named test and says so in
  `suppressed_by` (`scrollbar: …`, `jpeg: …`, `rerender: …`, with the kind it
  had): a scroll-bar band at the right/bottom edge; a frame the baseline
  re-encoded as JPEG reproduces; pixels a ≤1 px quarter-step shift of the
  baseline reproduces (zero-mean noise off the edges); and a box with no
  changed pixel inside (`morphology: …`), which closing can leave behind. The
  AI layer only sees
  regions this stage left. `diff.explain_noise: false` turns it off for
  diagnostics. Without the AI layer the corpus goes from 1/16 false failures
  (the scroll bar, previously hidden by the learned gate) to 0/16.
- **A move is scored by how far it went.** `moved` regions weigh from
  `moved_severity_scale` (0.35) up to 1.0 with the shift measured in the
  element's own size. Before, a 12 px checkbox moved by 16 px scored 17 and
  passed while the same checkbox moved by 32 px scored 35 and failed.
- Tests whose premise was the old order were updated, each with the reason in
  place: the thin-text accounting test now asserts the text is inside a region;
  the per-snapshot-threshold tests tolerate a 3 px block move by severity as
  well as area; the stability-mask clock test changes every digit between
  frames; the gate tests measure the gate with `explain_noise=False`.

### The learned gate is off by default

- **`ai.gate_enabled` now defaults to `false`.** The gate was trained against
  the cascade as it was before `core/explain.py` — wide aperture, opening
  first, no deterministic noise explanations. Behind the cascade it now sits
  in, it subtracts: on the corpus, 0 of 55 regressions over noise are missed
  without it and 5 of 55 are missed with it. A layer that only takes away must
  not be on by default. Nothing is removed — `ai.gate_enabled: true` brings it
  back, and every safety rule around it is unchanged.
- The deterministic explanations do the work the gate was added for, and name
  the reason in `suppressed_by` instead of a probability. Whether a gate
  re-trained against the current cascade beats them is an open question, and
  the honest answer to "it does not" is to drop the layer, not to keep
  shipping it switched off.

### Engine metrics you can reconcile

Found on a live run: `severity 100.0, changed area 1.44%` next to three
regions totalling ~400 px, and two neighbouring radio buttons "moved" by +48
and -58 at once.

- **Where the changed pixels went.** `changed_area_pct` is still measured on
  the change mask before segmentation — that has not changed. New metrics
  split it: `region_pixels`, `suppressed_pixels`, `unassigned_pixels` (they sum
  to `changed_pixels`) and `region_area_pct`. When more than 10% of the change
  is in no region, the one-line reason says so with numbers:
  `these regions hold 0.04% of the 1.44% changed, 1.40% is in no region`.
  On the live pair 97% of the mask was 1–2 px text strokes removed by the
  morphological opening before segmentation.
- The reason line starts with the total region count, never drops kinds
  silently (`other changes in N`), and names the region it points at
  `most severe` — it was chosen by severity and used to be called `largest`.
  The failure headline adds the region count.
- **Severity is a scale again.** Size now multiplies colour/structure
  intensity instead of being added to it, and the result saturates softly
  (`100·(1 − e^(−raw/0.6))`) instead of being clipped. A 27 px speck that
  disappeared scores ~31 (was 100), a moved 360×120 block ~70, a removed
  button ~98. **Stored severities from earlier versions are on the old scale.**
  Verdicts on the benchmark corpus are unchanged (24/27, 0/16 false fails).
- **MOVED no longer invents vectors.** A region whose content fits several
  places equally well (NCC within 0.03) is not given a shift unless the rest
  of the page agrees on one of those places; otherwise it is classified as
  appeared / disappeared / content, and a note says why.
  `DiffRegion.move_alternatives` records the count. Regions too small to
  search (side < 6 px) get the same check against the page's shift.

### Режим библиотеки

VisTest теперь подключается к чужому проекту обычной библиотекой — без сервера,
без интерфейса и без базы. Эталоны лежат файлами в репозитории пользователя и
ревьюятся в пул-реквестах, артефакты и отчёт — в `.vistest/`.

- `vistest.expect_screenshot(target, name, ...)` — публичный API. Принимает
  `Page` и `Locator` из Playwright, байты PNG, `PIL.Image`, массив numpy и путь
  к файлу; Playwright при этом не импортируется, страница распознаётся по
  наличию `screenshot()`. Возвращает `CompareResult`, бросает
  `BaselineMissing` и `ScreenshotMismatch` — оба наследники `AssertionError`,
  оба называют каждый файл путём и команду, которой это чинится.
- Хранилище эталонов файлами: `vistest.storage.file.FileStore`, раскладка
  `tests/__vistest__/<платформа>/<имя>.png` плюс необязательный паспорт
  `<имя>.json`. Протокол — `SnapshotStore` в `vistest.storage.base`, ключ —
  `SnapshotKey`. Повторный `--vistest-update` не трогает совпадающие файлы:
  принять два снимка не должно означать пул-реквест на триста изменённых.
- Флаги pytest: `--vistest-update`, `--vistest-baselines`, `--vistest-platform`,
  `--vistest-report`; у трёх последних есть ini-эквиваленты в `pyproject.toml`.
  Плагин больше не тянет загрузчик конфигурации и раннер на импорте — он
  грузится в каждом прогоне любого проекта, где установлен пакет.
- Отчёт — один самодостаточный HTML-файл: картинки в base64, ни CDN, ни
  шрифтов, ни одного запроса наружу; слайдер «эталон / снимок», причина
  падения словами, светлая и тёмная тема.
- `pytest -n` (xdist): любая запись идёт через временный файл и атомарное
  переименование, общего индекса и блокировок нет, отчёт собирается один раз в
  контроллере из per-test файлов.
- `vistest baselines export|import` понимают обе раскладки и переносят набор
  между ними одним и тем же архивом (`--baselines PATH`, `--layout flat`).
  Это путь миграции: проект, переросший библиотеку, переезжает в инсталляцию
  без переутверждения эталонов.

### Прогон не умирает из-за отличающегося скриншота

Найдено на живой обкатке на чужом проекте: пятый тест из одиннадцати нашёл
настоящее расхождение, хук отчёта упал с `TypeError: Object of type ndarray is
not JSON serializable`, и pytest превратил это в INTERNALERROR — сессия
умерла, оставшиеся шесть тестов не выполнились, отчёта нет.

- **Корень.** Метрики были ни при чём. `compare()` отдаёт рендереру четыре
  полнокадровых массива, и клал он их в `CompareResult.artifacts` — поле,
  объявленное `dict[str, str]` и сериализуемое в каждый отчёт, с
  `# type: ignore[assignment]` на каждой записи. Все прежние вызывающие звали
  `strip_internal` перед сериализацией; добавленный позже library-режим не
  звал, потому что в типе про это ничего не сказано. Массивы переехали в
  отдельное поле `CompareResult.maps`, которое не сериализуется вовсе; туда же
  ушёл `_dom_changes` из AI-слоя. `artifacts` снова значит то, что написано.
- `strip_internal` перестал быть условием сериализуемости и стал тем, чем и
  должен быть: освобождением памяти. Четыре массива размером со скриншот,
  удерживаемые столько, сколько живёт результат, — на наборе из двухсот
  снимков это разница между прогоном и OOM. Library-режим теперь их отпускает.
- **Ни один хук плагина больше не может уронить сессию.** `pytest_configure`,
  `pytest_unconfigure`, `pytest_sessionfinish`, `pytest_terminal_summary` и
  `pytest_runtest_makereport` обёрнуты: отказ необязательной части —
  предупреждение и работа дальше. Единственное намеренное исключение —
  сломанная конфигурация, она по-прежнему падает на загрузке, до первого теста.
- `models._json_default` (сериализатор, кормящий отчёт, не имеет права падать)
  дополнен: большой массив описывается, а не разворачивается в JSON. Иначе
  падение заменялось бы отчётом на сотни мегабайт, который никто не откроет.
- Потолки по мажорной версии в базовых зависимостях: `numpy<3`,
  `opencv-python-headless<6`, `pillow<13`, `pyyaml<7`. Плюс работа `latest-deps`
  в CI, которая ставит самые свежие версии в обход этих потолков и гоняет по
  ним движок и library-режим: следующий мажорный релиз должен ломать наш CI, а
  не чужой прогон. OpenCV 5.0 и numpy 2.x уже приехали именно так.

### Хвосты этапов 1–2

- **Ломающее для флагов:** `--vistest-profile` убран, остался
  `--vistest-platform` (ini `vistest_platform`, переменная `VISTEST_PLATFORM`,
  аргумент `expect_screenshot(platform=...)`). Слово «профиль» в пакете уже
  значит правила имён снимков (`NamingProfile`, `SuiteProfile`); третьего
  значения ему не нужно, а имя в публичной библиотеке меняется только до
  публикации.
- **Коллизия имён.** Два теста, пишущих один эталон, давали одну строку в
  отчёте и зелёный прогон — а под `--vistest-update` перезаписывали эталон
  друг друга в чужом репозитории. Теперь части отчёта группируются по ключу и
  различным `nodeid`: повтор одного теста — ретрай и молчание, два теста —
  коллизия в итоге прогона с ключом и обоими именами и ненулевой код возврата.
  Проверяется под `pytest -n 2`, где тесты живут в разных процессах.
- Нечитаемая часть отчёта по-прежнему пропускается, но теперь считается: «N
  checks could not be read into this report» и в отчёте, и в итоге прогона.
- Цель без живой страницы (байты, файл, массив) кладёт эталоны в корень — это
  не изменилось, но теперь об этом один раз за прогон говорит предупреждение:
  иначе macOS разработчика и linux-CI сравниваются с одним файлом.
- «Эталона нет» теперь называет платформу, на которой он есть. Самый частый и
  самый непонятный отказ таких инструментов.
- `PUT /api/settings/thresholds` писал значение как `repr(number)` —
  питоновское представление числа в колонке БД. Теперь число биндится
  параметром; миграции не нужно, старые строковые значения читаются как
  читались, и это закреплено тестом.
- Обход дерева при определении набора обрывается на 20000 записях, как и
  раньше, но пишет об этом в лог с числом и корнем: «оборван» и «ничего не
  нашлось» перестали выглядеть одинаково.
- `pip install vistest` тянет ещё и PyYAML. «Поставил пакет, написал
  vistest.yaml, получил требование доустановить парсер» — плохие первые пять
  минут ради 700 килобайт; чистая база защищает от веб-сервера, а не от
  разбора конфига. Extra `yaml` убран.
- `PUT /api/settings/thresholds` приводит значение к `float` явно на месте
  записи, а `validate()` закреплён тестом на тип возврата. К схеме `setting`
  дописано, почему колонка остаётся `TEXT`: значения читаются по имени и в SQL
  не сортируются и не сравниваются, поэтому миграция в `REAL` не окупается.
- `tests/test_ui_boot_smoke.py` — восемь одинаковых тел свёрнуты в один хелпер,
  таймаут поднят до 600 с, все проверки помечены одной группой
  (`xdist_group("jsdom")`): под `pytest -n --dist loadgroup` они идут на одном
  воркере и не конкурируют за диск. Импорт jsdom — это ввод-вывод на сотни
  файлов, а не работа процессора, и на сетевом диске он один занимает минуту;
  сообщение при таймауте теперь называет эту причину, а не показывает голый
  `TimeoutExpired`.
- `scripts/check_i18n.py` — храповик по русскому тексту: ищет кириллицу во всех
  файлах репозитория (кроме `*.ru.md` — это переводы, а не долг) и падает, если
  файл вырос сверх записанного в `scripts/i18n_debt.txt` или если кириллица
  появилась в файле, которого в списке нет. Заведён текущим состоянием: 12702
  строки в 220 файлах. Отдельная работа в CI.

### Ошибки конфигурации стали громкими

Правило: **ошибка конфигурации — падение, отказ необязательной части —
предупреждение и работа дальше.** Раньше в нескольких местах было наоборот, и
в чужом CI это выглядело как «настройка не работает», без единой строки в логе.

- Переменные окружения читаются через `env_int` / `env_float` / `env_flag` /
  `env_text` и падают с текстом, называющим переменную и значение.
  `VISTEST_ENGINE_MAX_PIXELS`, `VISTEST_FAIL_SEVERITY`,
  `VISTEST_MAX_CHANGED_AREA_PCT`, `VISTEST_UPDATE_BASELINES`,
  `VISTEST_PERCEPTUAL`, `VISTEST_IN_DOCKER`, `VISTEST_MAX_*` в `/api/check`.
  Пороги дополнительно проверяются по диапазону.
- Невалидное регулярное выражение в профиле имён падает при сборке профиля
  (`NamingError`), а не доживает до `classify` голым `re.error` посреди обхода
  каталога. Неизвестный ключ в `NamingProfile.with_overrides` и в описании
  проекта тоже отвергается по имени — раньше опечатка молча теряла правило.
  Пустое значение по-прежнему означает «не задано»: форма подключения шлёт
  пустую строку для каждого незаполненного поля.
- `vistest.yaml` при отсутствии PyYAML — падение с именем файла, а не тихий
  откат к умолчаниям.
- Сузились три «глотателя исключений»: `api/thresholds._rows` терпит только
  отсутствующую таблицу, `api/baselines._cfg_with_thresholds` — только
  отсутствие сервиса, `external._threshold_env` — только `ImportError`.
  «Переопределений нет» и «таблица сломана» перестали быть одним ответом.

### Упаковка

- **Ломающее:** extra `service` переименован в `server`.
  `pip install "vistest[service]"` больше не разрешается — используйте
  `vistest[server]`. Обновлены CI, `docker/Dockerfile.api` и документация.
- Базовая установка — `numpy`, `opencv-python-headless`, `pillow`, `pyyaml` и
  ничего больше; `tests/test_library_install.py` падает и при появлении лишней
  зависимости в списке, и при появлении лишнего импорта на пути библиотеки.
  PyYAML в базе намеренно: «поставил пакет, написал vistest.yaml, получил
  требование доустановить парсер» — плохие первые пять минут ради 700 килобайт.
  Чистая база защищает от веб-сервера, а не от разбора конфига.

---

Подготовка к продуктивной эксплуатации: аудит кода и закрытие найденного.
Раздел «Безопасность» стоит первым не для порядка — там есть то, что меняет
поведение существующих установок, и прочитать это нужно до обновления.

### Безопасность

- **Заголовки прокси больше не принимаются от кого попало.** Сервис запускался
  с `--proxy-headers --forwarded-allow-ips *`; в этом режиме uvicorn берёт
  адрес клиента из первого элемента `X-Forwarded-For`, то есть из значения,
  которое пишет сам вызывающий (прокси свой адрес дописывает в конец). На этом
  адресе держалась граница «только с машины сервиса»: подключение проекта
  (запуск произвольного процесса), правка и запуск тестов, редактор
  `secrets.env`, запись с мышью — и счётчик неудачных входов. Теперь адрес
  соединения и адрес клиента — разные вещи (`vistest/api/net.py`), а доверие к
  заголовкам включается переменной `VISTEST_TRUSTED_PROXIES`.
  **Требует действия при обновлении:** если у вас перед сервисом стоит nginx,
  Caddy или Traefik — укажите его адрес в этой переменной, иначе кука сессии
  перестанет получать флаг `Secure` (или задайте `VISTEST_COOKIE_SECURE=1`).
- Схема API (`/docs`, `/redoc`, `/openapi.json`) закрыта. Открывается на время
  разбора переменной `VISTEST_DOCS=on`.
- `POST /api/check` получил пределы: размер снимка, число дополнительных
  кадров, число пикселей после декодирования. До этого файл читался в память
  целиком без всякого потолка.
- Загрузка архива проекта читается кусками во временный файл (раньше проверка
  размера стояла ПОСЛЕ чтения в память) и отказывает по распакованному объёму
  и числу записей.
- Путь артефакта больше не выпускает за каталог данных: `_resolve` принимал
  любой абсолютный путь и `../`, а результат становился эталоном.
- Интерфейс отдаётся с `Content-Security-Policy`, `X-Frame-Options: DENY`,
  `Referrer-Policy` и `Permissions-Policy`.
- Изменяющий запрос со страницы чужого сайта отклоняется по `Origin`.
  Клиентов вне браузера это не касается: они `Origin` не присылают.
- Блокировка входа перестала быть оружием против владельца учётной записи:
  жёсткий порог теперь на пару «логин + адрес», а по одному логину со всех
  адресов — высокий.
- Заявки на доступ ограничены по адресу, и у очереди появился потолок. Каждая
  заявка стоит PBKDF2 в 480 000 раундов, то есть открытая регистрация была
  усилителем нагрузки.
- Добавлен `SECURITY.md` с моделью угроз. Главное в нём: **роль `admin`
  равносильна доступу к shell на машине сервиса** — это свойство продукта, а
  не дефект.

### Исправлено

- Артефакты прогона по матрице перетирали друг друга: платформа не участвовала
  ни в пути (`artifacts/<run>/<snapshot>/`), ни в поиске сравнения, которому
  принадлежит ссылка. Шесть вариантов одного снимка складывались в один
  каталог, и в разборе падения показывалась картинка чужого браузера.
- `add_ignore_box` писал паспорт эталона без замка и без атомарной записи.
  Параллельные «Ignore area» теряли зоны, а обрыв записи оставлял битый
  `meta.json`, после чего защита от конкурентного утверждения молча
  выключалась, а нумерация версий начиналась заново.
- Уборка истории и очередь фоновых задач не знали о существовании соседних
  процессов: при нескольких воркерах или репликах на общем томе два прогона
  одного проекта шли параллельно в одни эталоны, а уборка удаляла данные из
  каждого процесса независимо.
- Пороги в `options` проверялись только по имени: строка вместо числа роняла
  сравнение пятисоткой, отрицательная севериность тихо красила всё в красный.

### Добавлено

- **Вход через корпоративный каталог (LDAP / Active Directory).** Настройка и
  проверка соединения в интерфейсе, соответствие групп ролям, появление
  человека при первом входе, пересчёт роли на каждом входе. Локальные учётные
  записи продолжают работать всегда: каталог спрашивается вторым, чтобы
  недоступный сервер не запирал администратора из его же инсталляции. Учётная
  запись из каталога не открывается локальным паролем. Пароль сервисного
  аккаунта живёт в окружении (`VISTEST_LDAP_BIND_PASSWORD`), а не в базе,
  которая уезжает в бэкапы. Ставится как `pip install "vistest[ldap]"`.

- **Перенос эталонов между инсталляциями** — `vistest baselines export|import`
  и те же действия по API. Выборка по проекту, платформе и маске имён; три
  режима слияния (`new`, `update`, `replace`) и `--dry-run`, отвечающий «что
  будет» до того, как это станет необратимым. В режиме `update` присланная
  картинка кладётся новой версией, поэтому импорт можно откатить, а маски
  игнорирования принимающей стороны переживают его.

- **Лицензионный слой.** Ключ с подписью RSA, проверяемый полностью оффлайн
  (`vistest/licensing.py`), экран **Settings → Licence**, `GET/POST
  /api/license`, выпуск ключей — `scripts/issue_license.py`. Бесплатный режим
  без ключа: 2 проекта и 5 активных пользователей, всё остальное без
  ограничений.

  По истечении срока у заказчика ничего не забирается: эталоны, история и
  разбор остаются доступны всегда. Тридцать дней льготного срока с
  предупреждением на каждом экране, после — отказ только на приёме НОВЫХ
  прогонов (402).

- `vistest backup` и `vistest restore` — копия эталонов, базы и списка
  подключённых проектов. База снимается через `VACUUM INTO`, то есть копию
  можно делать на работающем сервисе. `secrets.env` не входит без
  `--with-secrets`.
- Версия схемы базы (`PRAGMA user_version`). Старый код на базе, поработавшей
  под новой версией, теперь отказывается стартовать с внятным текстом, а не
  пишет в чужую схему.
- Плановая уборка подметает и служебные таблицы: журнал (по умолчанию год),
  протухшие сессии, заявки на разбор, завершённые задачи.
- `/metrics` отдаёт метрики самого сервиса: запросы и время по методам и
  классам кода, размер очереди фоновых задач.
- `constraints-service.txt` — зафиксированные версии для образа сервиса,
  включая транзитивные.
- В CI добавлены работы: `pip-audit`, применение миграций к базе прошлой
  версии, e2e-набор в Chromium, сборка и запуск docker-образа.

### Производительность и пределы

- У движка появился потолок по пикселям (`VISTEST_ENGINE_MAX_PIXELS`, 80 Мпикс
  по умолчанию). Сравнение разворачивает кадр в несколько массивов `float32`;
  снимок на 200 Мпикс раньше означал своп или убитый по памяти процесс — то
  есть прогон, исчезнувший без единой строки в логе.
- У тела запроса появился общий потолок (`VISTEST_MAX_BODY_MB`, 64 МБ).
  Загрузок он не касается: у них свой, более строгий.
- Размер каталога артефактов кешируется на несколько секунд: он считался
  полным обходом дерева на каждое открытие экрана настроек и на каждый тик
  уборки.

### Изменено

- **Ядро сравнения вынесено в `vistest/core/` и больше ничего за собой не
  тянет.** Готовим library-режим: пакет, который подключают к чужому проекту,
  как встроенный скриншот-тест Playwright. Поведение сравнения не изменилось
  ни в одной точке — переехали файлы и границы, а не логика.

  Что теперь в ядре: компаратор и каскад (как и раньше), зоны игнорирования
  (`core/regions.py`, бывший `vistest/zones.py`), правила именования снимков
  чужих наборов (`core/naming.py`, класс `NamingProfile`), разрешение порогов
  вердикта (`core/thresholds.py`) и голые дата-классы настроек
  (`core/settings.py`).

  Что осталось снаружи и почему: `vistest/config.py` — это загрузчик, он ищет
  `vistest.yaml` по рабочему каталогу и читает переменные окружения; чтение
  порогов из базы — реализация протокола `ThresholdStore` в
  `vistest/api/thresholds.py`; запуск чужого раннера (`command`, `install`,
  `browser_arg`, `only_arg`, определение набора по маркерам репозитория) —
  `vistest/suites/`, где `SuiteProfile` теперь наследует `NamingProfile`.

  Зависимости на базу и глобальный конфиг внутри ядра заменены явными
  параметрами: `CheckService` больше не собирает слои порогов сам, а вызывает
  `core.thresholds.patch_for`; экран настроек, паспорт снимка и переменные
  окружения для чужого pytest считают эффективное значение одной и той же
  функцией `core.thresholds.layer` — раньше эта арифметика была написана в
  четырёх местах и могла разойтись.

  Старые пути импорта продолжают работать: `vistest.zones`,
  `vistest.config.DiffConfig`, `vistest.service.SNAPSHOT_THRESHOLDS`,
  `vistest.api.thresholds.*` — всё на месте.

- **`import vistest.core` перестал поднимать сервисный слой.** `vistest/__init__.py`
  импортировал `runner` и `service` жадно, поэтому обращение к ядру тянуло за
  собой оркестратор, хранилище эталонов и модуль съёмки Playwright — 25
  модулей вместо 11. Теперь они доступны через `__getattr__` и грузятся при
  первом обращении; `from vistest import CheckService` работает как работал.
- Добавлен `tests/test_core_standalone.py`: в отдельном процессе импортирует
  `vistest.core` **и** `vistest` и падает, если загрузилось что-то за
  пределами явного allowlist'а (`vistest`, `vistest.models`, `vistest.core.*`)
  или один из шести запрещённых пакетов — `fastapi`, `uvicorn`, `starlette`,
  `sqlite3`, `ldap3`, `onnxruntime`. Allowlist, а не список запретов: перечень
  запрещённого молча одобряет всё, о чём не подумали, а тест существует именно
  ради того, о чём не подумали. Расширение ядра теперь видно одной строкой в
  диффе.
- **`DiffConfig.max_pixels`.** Предел движка по памяти был модульной
  константой `comparator.MAX_PIXELS`, читавшейся из `VISTEST_ENGINE_MAX_PIXELS`
  в момент импорта, — единственное чтение глобального состояния, остававшееся
  в ядре, и единственный параметр движка, который тест мог поменять только
  патчем модуля. Стал полем `DiffConfig` со статическим дефолтом; переменную
  окружения читает загрузчик `vistest.config`, как и все остальные. `0`
  выключает проверку. Поведение не изменилось; переменная окружения работает
  как работала.
- **Ленивый корень пакета виден mypy и автодополнению.** У `__getattr__` нет
  имён в исходнике, поэтому отложенные `CheckService`, `VisTestConfig`,
  `VisualTester` и остальные проверялись как `Any`. Добавлен блок
  `if TYPE_CHECKING:` с настоящими импортами (никогда не выполняется) и
  `__dir__`, который перечисляет отложенные имена, ничего не импортируя.
  `tests/test_lazy_exports.py` держит согласованными три списка одних и тех же
  имён: `_LAZY`, блок `TYPE_CHECKING` и `__all__` — разъехаться незаметно они
  больше не могут.
- **Тесты порядка перестали быть удачей.** Двадцать три файла делают
  `importlib.reload(vistest.api.main)`, чтобы собрать сервис на временной базе.
  `monkeypatch` возвращает `VISTEST_ROOT`, а модуль не возвращал никто: reload
  перестраивает модуль на месте, и `vistest.api.main.db` до конца сессии
  смотрел на временную базу теста, где уже заведён администратор. Дальше
  `settings._guard` делал ленивый `from .main import db`, видел
  `any_users(db) == True` и отвечал «Sign in required» — в файле про IP-адреса,
  который аутентификации не касается. В штатном порядке файлов это не всплывало
  и ждало первого `-n`, `--reverse` или запуска одного файла. Восстановление
  теперь одно, в новом `tests/conftest.py`, вместо двадцати фикстур. Набор
  проверен в обратном порядке и на случайных перестановках.
- **`tests/test_naming.py`** — 57 целевых тестов на `NamingProfile`:
  `classify`, `walk`, `roots` и особенно `with_overrides`, у которого все
  правила молчаливые (неизвестный ключ ничего не делает, пустое значение не
  стирает дефолт, голая строка становится кортежем из одного элемента,
  `search_dirs` добавляются, а не заменяются). На этом классе стоит подключение
  наборов на чужих языках, и до сих пор он был покрыт только косвенно.

- Появился `/api/v1/...` — тот же набор роутов по стабильному адресу. Префикс
  снимается до маршрутизации, то есть это не копия роутов и разойтись двум
  адресам нечем. Пути без версии остаются рабочими.
- Удаление сравнения и очистка истории снимка спрашивают право ДО проверки
  существования — как это уже делало удаление прогона. Обратный порядок
  отвечал на «а есть ли у вас объект номер 42» тому, кто не имеет права
  спрашивать.

- Образ сервиса ставит пакет обычной установкой, а не `pip install -e`, и с
  ограничением версий.
- Соединения с базой закрываются при остановке сервиса; выставлены
  `busy_timeout` и `synchronous=NORMAL`.
- Часы уборки и уведомлений останавливаются по событию, а не досыпают свой
  интервал после остановки сервиса.
