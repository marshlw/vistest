# Changelog

What changed for someone who uses VisTest, release by release, and what to
change in your tests. Every decision, measurement and test behind it is in the
development journal, [docs/dev/journal.md](https://github.com/marshlw/vistest/blob/main/docs/dev/journal.md).

## Unreleased

### Security

- **Pillow 13 is allowed** (`pillow>=10.0,<14`, was `<13`): Pillow 13.0.0
  fixes the CVEs reported against 12.3.0. Pillow 13 needs Python 3.11 or
  newer, and so does VisTest now (see Breaking).

### Breaking

- **Python 3.11 or newer is required**, for `vistest` and `vistest-server`
  alike (`requires-python = ">=3.11"`; 3.10 is no longer supported). The
  reason is Pillow 13 and its security fixes: Pillow 13 does not install on
  3.10, and there pip would have stayed on Pillow 12.x with the CVEs above.
  On 3.10 pip now refuses the package with a message about `requires-python`
  instead of installing it.

- **The images with a browser are on Playwright 1.56.0 and Ubuntu 24.04**
  (`docker/Dockerfile.full`, `docker/Dockerfile.runner`, the team compose
  file: `mcr.microsoft.com/playwright/python:v1.56.0-noble`, was
  `v1.47.0-jammy`, whose Python is too old for VisTest now). A newer
  Playwright brings a newer Chromium, which draws some pixels differently:
  baselines taken in the old image (the `docker-chromium-…` platform key) are
  to be taken again in the new one and looked at before they are accepted.

- **The server is a package of its own, `vistest-server`.** `vistest` is the
  library: the engine, `expect_screenshot`, the pytest plugin and the
  library's commands. Its extras are `browser`, `report` and `dev`; `server`,
  `full`, `ai` and `ldap` are gone from it and are `vistest-server`'s now.
  - the library, as before: `pip install "vistest[browser]"`;
  - the server, the review interface and its commands (`vistest serve`,
    `gate`, `comment`, `snap`, `matrix`, …): `pip install vistest-server`,
    with `vistest-server[ai]`, `[ldap]` or `[full]` for what those extras
    brought; it installs the matching `vistest` itself;
  - from a checkout: `pip install -e ".[browser,report]" -e "./server[full]"`.

  Without the server, `vistest --help` says so in one line, and a server
  command exits with code 2 and the same line.

## 0.2.0.dev3

The library is ready to be shown to someone who has never seen it: its
public surface, its messages and its documentation were reviewed before the
first release on PyPI.

### New

- **The README starts with the library**: install in two lines, one test, the
  first run, CI, the settings, the layout, `vistest check`. The server is
  below, under its own heading. A new English guide,
  [docs/GUIDE.md](https://github.com/marshlw/vistest/blob/main/docs/GUIDE.md),
  has every parameter, flag, key and command; `examples/vistest.yaml` lists
  every key the library reads; `.gitlab-ci.example.yml` is for a project on
  the library.
- **A hung page fails the check in seconds** with `CaptureError` and the
  reason, instead of hanging the run. `timeout_ms=` in the call is the time the
  whole check may spend on the page; a page that stopped answering takes up to
  1.5 s more, to say why.
- **`vistest check NAME ACTUAL` works without a server**: `expect_screenshot`
  on a file, from any language. Exit 0 matches, 1 differs, 2 no baseline yet;
  `--update` accepts; `--json` prints one object (`verdict`, `message`,
  `baseline`, `actual`, `diff`).
- **A failure message you can read**: one thing per line (`reason:`,
  `capture:`, `second look:`, `hint:`), no line longer than 120 characters but
  a path; each change in the same words as the report and `vistest bench` —
  «recolored: #1f2937 → #4d5666, color difference 14.8», «block moved by +1 px
  along y», «edges redrawn within 1 px, same color: the outline changed
  slightly»; the color difference explained once, with its scale; how to
  accept said for where the check runs (pytest, `vistest check`, a script).
- **One order for every setting**: the call or the command line, then the
  environment, then vistest.yaml (`pyproject.toml` for the flags), then the
  default. A key of vistest.yaml the library does not read is named in one
  warning per run.
- **With `vistest[browser]` alone** pytest shows two fixtures and five flags;
  the server's appear with `vistest[server]`. `vistest --help` lists the
  library's commands apart from the server's, and a server command without
  the server says what to install.
- **A console that cannot print `→` and `ΔE`** (a Windows runner's pipe) gets
  `->` and `dE` instead of a crash or `?`.

### Breaking

Before → after. Nothing keeps the old spelling working with a warning: there
was no public release that promised it.

- `from vistest import VisTestConfig` → `from vistest.config import VisTestConfig`;
  likewise `VisualTester`, `VisualMismatch` → `vistest.runner`; `CheckService` →
  `vistest.service`; `visual_check`, `visual_session`, `VisualSession`,
  `VisualTestCase` → `vistest.integrations`; `FileStore`, `SnapshotKey`,
  `SnapshotMeta`, `SnapshotStore` → `vistest.storage`; `ChangeKind`,
  `DiffRegion` → `vistest.models`. The top of the package exports the library
  only: `expect_screenshot`, `BaselineMissing`, `ScreenshotMismatch`,
  `VisualCheckError`, `CaptureError`, `VisTestWarning`, `CompareResult`,
  `Verdict`, `compare`.
- `expect_screenshot(..., threshold=40)` → `fail_severity=40`;
  `threshold={"fail_severity": 40, "max_changed_area_pct": 0.5}` →
  `fail_severity=40, max_changed_area_pct=0.5`.
- `stable_timeout_ms=`, `ready_timeout_ms=` in the call → the same keys under
  `capture:` in vistest.yaml, or `timeout_ms=` for one check.
- `reset_hover_focus=True` → `keep_pointer=False, blur_focus=True`;
  `reset_hover_focus=False` → `keep_pointer=True`.
- `restore_scroll=False` → `capture.match_baseline_scroll: false`;
  `capture.restore_scroll` in vistest.yaml is refused with its new name.
- Engine v1 and the presets are gone from the library: `engine: v1`,
  `preset: strict` (also under `diff:`) and `VISTEST_ENGINE` are refused in one
  line — remove them; choosing an engine in the call, `--vistest-preset`,
  `vistest compare --engine/--preset` and `vistest check --preset` no longer
  exist. There is one engine, v2.
- `visual`, `visual_soft`, `vistest_run_id`, `--vistest-api`,
  `--vistest-perceptual`, `--vistest-fail-on` → `pip install "vistest[server]"`;
  the server's examples moved to `docs/server/examples/`.
- `vistest check NAME IMAGE` against the server's layout → `--api URL`, or a
  server configured with `VISTEST_API_URL` / `service.api_url`. Without one it
  writes into `tests/__vistest__/`, and the first run exits 2.
- `VISTEST_BASELINES`, `VISTEST_PLATFORM`, `VISTEST_REPORT` are read under the
  pytest plugin too, between the flag and `pyproject.toml`.
  `VISTEST_UPDATE_BASELINES` still writes baselines only without the plugin.
- The words of a change in the report and in `POST /api/check`'s regions: the
  kinds `block`, `ink colour`, `ink shape` → `moved`, `color`, `shape`; the text
  «ink colour: A → B, ΔE00 N» → «recolored: A → B, color difference N»; the
  exact counts behind a sentence are in a new `detail`. `vistest compare`
  prints the failure message's words; its `--json` is unchanged.
- The Russian guide is the server's: `docs/GUIDE.md` → `docs/server/GUIDE.ru.md`;
  `docs/GUIDE.md` is the library's English guide.

## 0.2.0.dev2

Capture, after the first trial on a real application.

- The readiness wait holds until nothing of the page has been in flight for the
  whole quiet window, counted from the end of its last request.
- **The pointer is moved off the page before the picture by default**; the
  focus is left alone. This changes the pixels of baselines taken with the
  pointer over something: the capture's version is 3, and a check against an
  older baseline says «the baseline was taken the old way» when it fails —
  look, and accept it again.
- A picture of the window is taken at the scroll its baseline had, and the
  window is put back after.
- The device scale factor is kept in the passport; a check at another one
  says so.
- Baselines live in `tests/__vistest__/`, or `__vistest__/` when the project
  has no `tests/` folder.

## 0.2.0.dev1

- **Engine v2 is the engine**: whatever no rule explains fails, and each change
  is said in words.
- `vistest[browser]` brings `pytest-playwright`, which gives tests the `page`
  fixture.
- The first package on TestPyPI, checked by installing it on Linux, Windows
  and macOS.
