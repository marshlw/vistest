# VisTest

**Visual regression testing for pytest and Playwright that says why a check
failed.** One function, `expect_screenshot`, with the baselines in your
repository and nothing running anywhere else. A check that fails names what
changed — «recolored: #1f2937 → #4d5666», «block moved by +1 px along y» —
instead of printing a percentage. There is also a server for teams — a
commercial product.

*Читать по-русски: [README.ru.md](https://github.com/marshlw/vistest/blob/main/README.ru.md)*

---

## Install

```bash
pip install "vistest[browser]"
playwright install chromium
```

`[browser]` brings Playwright and `pytest-playwright`, which gives your tests
the `page` fixture; the second line downloads the browser. Python 3.11 or newer.
Without `[browser]` the package compares pictures you hand it and opens no
browser.

## A first test

```python
from pathlib import Path

import pytest

from vistest import expect_screenshot

DEMO = (Path(__file__).parent / "demo_page.html").resolve().as_uri()

#  The line with a counter that ticks every 200 ms and a spinner that never
#  stops: masked, because a moving number is not a regression. A selector is
#  painted over in the picture, the way Playwright's own `mask=` does it.
LIVE = ["p.lead"]


@pytest.fixture
def demo(page):
    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(DEMO)
    return page


def test_landing_page(demo):
    """The whole window."""
    expect_screenshot(demo, "landing.png", mask=LIVE)


def test_pricing_cards(demo):
    """One element: a Locator is photographed alone, smaller and steadier."""
    expect_screenshot(demo.locator("#pricing"), "pricing.png")
```

This is [`examples/test_library.py`](https://github.com/marshlw/vistest/blob/main/examples/test_library.py)
of this repository, without its third test and the line that skips it when
Playwright is not installed; the page it opens, `demo_page.html`, sits next to
it. To try it as is:

```bash
mkdir -p tests
curl -fsSo tests/test_library.py https://raw.githubusercontent.com/marshlw/vistest/main/examples/test_library.py
curl -fsSo tests/demo_page.html https://raw.githubusercontent.com/marshlw/vistest/main/examples/demo_page.html
```

In your own project, `page.goto` your own address. `expect_screenshot` takes a
Playwright `Page` or `Locator`, PNG bytes, a `PIL.Image`, a numpy array or a
path to a PNG.

## The first run, and the baselines

```bash
pytest tests/test_library.py
```

The first run is red, because there is no baseline yet and a check that passes
before anyone has looked at the picture is not a check:

```
vistest: no baseline for 'landing.png' (linux-chromium-1x-1280x800)
  expected:    tests/__vistest__/linux-chromium-1x-1280x800/landing.png
  captured:    .vistest/actual/linux-chromium-1x-1280x800/landing.png
  create it:   pytest --vistest-update
               then commit the file — in CI the baseline has to come from the repository, not from the run
```

(Paths are shortened to the project's root; the message prints them in full.)
Accept the pictures, look at them, commit them:

```bash
pytest tests/test_library.py --vistest-update
git add tests/__vistest__
git commit -m "visual baselines"
pytest tests/test_library.py                     # green
```

`.vistest/` holds the pictures of the last run and the report; it does not
belong in git:

```gitignore
# VisTest artifacts. Baselines live in tests/__vistest__/ and MUST be committed.
.vistest/
```

PNGs grow a repository quickly, so baselines are usually better off in LFS:
`git lfs track "tests/__vistest__/**/*.png"`.

## When a check fails

Change the page — here the price's font cut from 34 to 30 px — and the check
says what changed and where everything is:

```
vistest: 'landing.png' differs from the baseline (linux-chromium-1x-1280x800)
  32 changes not explained as rendering noise · worst 93/100 · no threshold set, so any change fails
    · 2.53% of the frame
  reason:      shape in 30, line in 2; worst 178x27 at (823, 308)
  second look: the second picture is the same as the first, so the change is real, not motion
  renderer:    same as the baseline's
  baseline:    tests/__vistest__/linux-chromium-1x-1280x800/landing.png
  actual:      .vistest/actual/linux-chromium-1x-1280x800/landing.png
  changed:     178x27 at (823, 308): shape changed: 48% of the old strokes are not where the new ones are
               110x43 at (589, 358): shape changed: 70% of the new strokes are not where the old ones were
               65x33 at (523, 305): shape changed: 42% of the old strokes are not where the new ones are
               ... and 29 more in the report
  diff:        .vistest/diff/linux-chromium-1x-1280x800/landing.png
  report:      .vistest/report/index.html
  accept it:   pytest --vistest-update
```

One thing per line. `reason:` sums the changes up; `second look:` says whether
more frames showed the change holding still; `renderer:` says whether this
browser draws text the way the one that took the baseline did. Each change
that counts is said as the engine measured it — three of them, the rest
counted in the report:

```
  changed:     153x14 at (202, 242): recolored: #1f2937 → #4d5666, color difference 14.8
               ...
               (color difference is ΔE00 (CIEDE2000): 1 ≈ barely visible, 10 and more is plainly another color)
```

The other words: «fill: #2563eb → #4d77ff», «line added: 41×1 px», «block moved
by +1 px along y», «shape changed», and for a corner rounded 8 → 10 px «edges
redrawn within 1 px, same color (#c7ced8): the outline changed slightly». The
report's `kind` column uses the same words: moved, line, fill, color, redrawn,
shape.

The report, `.vistest/report/index.html`, is one self-contained file —
pictures inlined, nothing fetched — so it opens on a machine with no network
and can be attached to a ticket. If the change is what you wanted, accept it
with `pytest --vistest-update` and commit the new baseline.

## In CI

CI compares against the baselines in the repository and never writes them.
Text is drawn differently on Windows, macOS and Linux, so the baselines live in
one directory per platform (below), and CI needs the set taken where it runs.
Pin both halves of that — the runner's system and Playwright:

```yaml
# .github/workflows/visual.yml
name: visual
on: [push, pull_request]
jobs:
  visual:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v5
        with: { lfs: true }
      - uses: actions/setup-python@v6
        with: { python-version: "3.12" }
      - run: pip install "vistest[browser]" "playwright==1.56.0"
      - run: playwright install --with-deps chromium
      - run: pytest
      - uses: actions/upload-artifact@v4
        if: failure()
        with: { name: vistest, path: .vistest/ }
```

When a check fails in CI, the uploaded `.vistest/` has the report and the
pictures it took. That is also how the CI's own set is taken the first time:
the run fails with «no baseline», you download `.vistest/actual/`, look at
the pictures, put them under `tests/__vistest__/` and commit them. A new
Playwright brings a new Chromium, which draws some pixels differently, so
change the pin on purpose, and take the baselines again when you do. For
GitLab, [`.gitlab-ci.example.yml`](https://github.com/marshlw/vistest/blob/main/.gitlab-ci.example.yml)
runs in the Playwright image of the pinned version, with a manual job that
takes new baselines for a person to look at.

`pytest -n auto` works: every write goes through a temporary file and an
atomic rename, and the report is assembled once at the end.

## Settings

Five pytest flags:

| Flag | `pyproject.toml` | What it does |
| --- | --- | --- |
| `--vistest-update[=MODE]` | — | accept screenshots as baselines: `missing` — only absent ones; `changed` (default, also the bare flag) — absent and failed; `all` — everything that differs by a byte |
| `--vistest-baselines=PATH` | `vistest_baselines` | where the baselines live (default `tests/__vistest__`) |
| `--vistest-platform=NAME` | `vistest_platform` | the platform directory, e.g. `linux-chromium-1x-1440x900`; taken from the page when not set |
| `--vistest-report=PATH` | `vistest_report` | where the HTML report goes |
| `--vistest-config=PATH` | — | the vistest.yaml to read, when it is not the one found from the working directory up |

A bare `--vistest-update` takes the next argument as its mode, so write paths
first — `pytest tests/ --vistest-update` — or spell the mode out:
`pytest --vistest-update=changed tests/`.

One order for every setting: **the call or the command line, then the
environment, then the file — `vistest.yaml`, or `pyproject.toml` for the
flags —, then the default.** A threshold has one more layer, between the call
and the environment: the snapshot's own passport (the `.json` next to its
baseline).

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
must not accept a run's pictures.

The call itself:

```python
expect_screenshot(
    page, "checkout.png",
    fail_severity=40,                  # 0–100; and max_changed_area_pct=, in %
    mask=["#promo", (0, 0, 320, 64)],  # selectors are painted, boxes are ignored
    full_page=False,
    scale="css",                       # or "device"; the platform key says which
    timeout_ms=10000,                  # the whole check; past it, CaptureError
                                       # (a hung page: up to 1.5 s more, to say why)
)
```

A `vistest.yaml` is optional; [`examples/vistest.yaml`](https://github.com/marshlw/vistest/blob/main/examples/vistest.yaml)
lists every key the library reads, with what it does. A key the library does
not read is named in one warning per run instead of changing nothing in
silence. Every parameter, key and step is in the
[guide](https://github.com/marshlw/vistest/blob/main/docs/GUIDE.md).

## Where the files go

```
tests/__vistest__/                 <- committed, reviewed in pull requests
  linux-chromium-1x-1440x900/      one directory per platform
    home.png                       the baseline
    home.json                      its passport: size, thresholds, how it was taken
    shop/checkout.png
  .renderers/<sha256>.png          the renderer canaries the baselines were taken with

.vistest/                          <- ignored
  actual/…  diff/…  report/index.html
```

When the project has no `tests/` folder, the baselines go to `__vistest__/`
at its root.

**The platform** is the directory name: the operating system, the browser,
the picture's scale and the window size — `linux-chromium-1x-1440x900`. A
baseline taken on Windows is never compared with one taken on Linux, because
fonts are drawn differently there. `platform=` and `--vistest-platform` name
it yourself; keep the system in the name (`linux-chromium-1x-1440x900`), or
know that without it (`chromium-1440x900`) every machine shares one set, and a
developer's macOS and a Linux CI compare against the same file. A target that
is not a live page — bytes, a file, an array — carries no browser and no
window size: its baselines go to the root of the baseline directory, and a
warning says so once per run.

## Not Python: `vistest check`

The same check for a picture taken by anything else — a Node test, a JVM
suite, a shell script:

```bash
vistest check checkout.png shots/checkout.png --platform linux-chromium-1x-1440x900
echo $?          # 0 matches, 1 differs, 2 no baseline yet (--update writes it)
vistest check checkout.png shots/checkout.png --json
```

`--json` prints one object: `verdict` (`pass`, `fail`, `missing` or
`new_baseline`), `message`, and the paths of `baseline`, `actual` and `diff`.
The layout and the messages are the library's. Also in the library:

- `vistest compare expected.png actual.png -o diff/` — two PNGs, no baselines;
- `vistest doctor https://your-app.example` — loads an unchanged page several
  times and counts how often it would fail: the noise of your stand before
  you trust the tool;
- `vistest bench test-results` — the engine on your own failures, against
  Playwright's own comparator, the disagreements collected for a person.

## More

- [docs/GUIDE.md](https://github.com/marshlw/vistest/blob/main/docs/GUIDE.md) — the library in full: every parameter, the readiness wait
  and the second look, masks, the engine and what it does not tell apart.
- [CHANGELOG.md](https://github.com/marshlw/vistest/blob/main/CHANGELOG.md) — what changed, and what to change in your tests.

---

## Verifying the engine

```bash
pytest tests/                           # invariants: noise must not fail, regressions must be caught
python tests/benchmark.py --artifacts   # noise / signal table plus images
python tests/benchmark.py --regenerate  # redraw the frozen corpus (deliberately)

# VisTest vs absdiff and the real pixelmatch and Playwright (Node.js >= 18)
npm ci --prefix scripts/bench
node scripts/bench_pixelmatch.mjs > docs/benchmark_native.json
python tests/benchmark.py --compare --native docs/benchmark_native.json \
    --no-timing --markdown docs/benchmark.md
```

Without `--native`, `--compare` scores pixelmatch and Playwright with a numpy
port of their core (`tests/baselines.py`). That is a quick check without Node,
marked as a port, and never the published figure.

The synthetic benchmark is the main tool for tuning thresholds: after any
configuration change you immediately see whether the engine started catching
noise. Unlabelled real screenshots are useless for this — in a screenshot
nobody has labelled you do not know the ground truth. Real screenshots with
mutations are not: take a real render, apply a change whose label is known in
advance (a recolored button, a shifted block, a re-encoded frame), and the
ground truth is known by construction.

The primary metric is **false-fail rate**: the share of pages that did not
meaningfully change but produced a red test.

### Figures

The comparison with other tools on real browser renders is published with the
first release on PyPI, with the conditions of the run written down before it.
Until then this page carries no figures.

ΔE00 is separately verified against the reference pairs from the CIE Technical
Report (Sharma et al., 2005).

---

# License

AGPL-3.0-or-later. See [LICENSE](https://github.com/marshlw/vistest/blob/main/LICENSE) and [NOTICE](https://github.com/marshlw/vistest/blob/main/NOTICE).

If you run a modified version of VisTest as a network service, the AGPL
requires you to offer the complete corresponding source of your modified
version to its users. A commercial license without that obligation is
available — see [NOTICE](https://github.com/marshlw/vistest/blob/main/NOTICE) for contact details.

"VisTest" and the VisTest logo are trademarks of the project author and are not
covered by the AGPL grant. Forks must be renamed and rebranded.
