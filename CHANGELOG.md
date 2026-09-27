# Changelog

Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/),
версии — [семантические](https://semver.org/lang/ru/).

## [Unreleased]

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
