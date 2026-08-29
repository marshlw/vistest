# VisTest

**Self-hosted visual regression testing.** Runs entirely inside your own
network, records baselines without writing code, and explains *why* a check
failed instead of printing a percentage.

*Читать по-русски: [README.ru.md](README.ru.md)*

---

## What it does

| | |
|---|---|
| **Stays inside your network** | No cloud, no telemetry, no required outbound traffic. Installs on a machine without internet access. |
| **No code required to start** | Mouse recorder, `--suite pages.yaml`, a pytest fixture, or `POST /api/check` from any stack — four entry points. |
| **Explains failures** | Not "3.2% different", but `button[data-testid=submit] moved 14px down, severity 82, threshold 50`. The engine is open and inspectable. |

The comparison engine combines **ΔE00** (perceptual colour distance), **SSIM**
(structural similarity), sub-pixel **alignment**, and automatic noise
suppression. A pixel counts as changed only when both colour *and* structure
agree that it changed.

---

## Requirements

- Python 3.10+
- For browser capture: Playwright 1.47 and its Chromium build (installed by `setup`)
- Optional: Docker, for reproducible cross-machine baselines

The comparison core itself needs only `numpy`, `opencv-python-headless` and
`pillow`. Everything else is an optional extra.

---

## Quick start

```bash
git clone <repo-url> && cd visual-testing

# Windows
.\run.ps1 setup          # venv + dependencies + browsers
.\run.ps1 ui             # review UI at http://127.0.0.1:8420/ui/

# Linux / macOS
./run.sh setup && ./run.sh ui
```

`run.py` detects the OS, creates the virtualenv in the right place
(`Scripts` vs `bin`), installs the Playwright browsers and opens the UI.
Run `python run.py doctor` to see what is missing.

### Check your project first

```bash
python run.py doctor https://your-app.example.com
```

The page is loaded several times in a row with nothing changing in between, so
any difference found here is false by construction. The report shows how many
false failures you would get **without** suppression and **with** VisTest's
suppression, and what exactly is noisy — clocks, animations, lazy-loaded
content, fonts.

Run this before trusting the tool. If the project itself is noisy, it is better
to find out in the first ten minutes.

---

## Recording baselines without writing code

```bash
python run.py record https://your-app.example.com
```

A browser opens with a control panel. Click **Capture page** and you get a
baseline **and** a ready pytest + Playwright test file you can review and edit
by hand — a normal test, not a recording in a private format.

The same thing from the UI: **Baselines → ● Record with the mouse**.

Generate page objects instead of inline selectors:

```bash
python run.py record https://your-app.example.com --pom
```

Rebuild tests from baseline passports (manual edits are preserved):

```bash
python run.py codegen --pom
```

Capture baselines by URL without any browser interaction:

```bash
python run.py snap https://your-app.example.com/checkout
```

---

## Use in tests

```python
def test_checkout(page, visual):
    page.goto("https://shop.example/checkout")
    visual.assert_screenshot("checkout.png")
```

The first run creates the baseline, later runs compare against it. You do not
need to write `mask_selectors` — dynamic regions are detected automatically.

Fine-tuning, when you need it:

```python
visual.assert_screenshot(
    "checkout.png",
    clip_selector="#order-summary",    # one component: faster and more stable
    fail_severity=40,                  # a softer threshold for this page
    mask_selectors=["#promo-banner"],  # a manual mask, if auto-detection is not enough
)
```

Compare two PNGs with no browser involved:

```bash
vistest compare expected.png actual.png -o diff/
```

---

## Connecting an existing test suite

VisTest can drive a test suite that already exists, without changing a line in
it. Register the suite once:

```bash
python run.py project add /path/to/your-tests
python run.py project list
python run.py project run your-tests
```

The connected suite runs in **VisTest's environment** — pytest, Playwright and
the browsers are already there — while your code is imported as-is, with the
repository root added to `PYTHONPATH`.

Secrets are never stored in the project description. Values are referenced by
name (`${YOUR_APP_PASSWORD}`) and resolved from the environment or from
`.vistest/secrets.env`; see `.vistest/secrets.env.example`. If a variable is
missing, the run fails with a clear message instead of submitting an empty
password.

Other stacks can post a ready screenshot over HTTP:

```bash
curl -F image=@shot.png -F name=checkout.png \
     http://127.0.0.1:8420/api/check
```

A Node client and the API contract for other languages live in `clients/`.

---

## The service

```bash
python run.py ui                    # local
python -m vistest.cli serve         # headless
```

- **Runs / Compare** — review diffs, accept or reject baselines.
- **Baselines** — versioned, with history and rollback.
- **Tests** — open, edit, save and run recorded tests from the browser.
- **Metrics** — false-fail rate, time to review, accumulated statistics.
- **Diagnostics** — the `doctor` report and an environment check.
- Command palette: `⌘K` / `Ctrl+K`.

### Roles and access

Three roles: `viewer` (read), `reviewer` (accept/reject, start runs), `admin`
(users, secrets, connecting projects, mouse recording).

While no administrator exists the service is open and behaves like a
single-user install. As soon as the first admin is created, authentication
turns itself on:

```bash
python -m vistest.cli user add anna --role admin
```

Invite the rest of the team from **Settings → Team** with a one-time link — the
person sets their own login and password, so no password travels through chat.
CI run submission stays open: the runner has no session.

### For a team, on a VM

The default `docker/docker-compose.yml` is a light image with no browser, and
recording, projects and secrets answer on localhost only. For a shared install
there is a profile with a browser and a noVNC recording window:

```bash
docker compose -f docker-compose.team.yml up -d --build
docker compose -f docker-compose.team.yml exec vistest \
  python -m vistest.cli user add anna --role admin
```

Ports `8420` (UI) and `6080` (recording window) must be reachable by the team.
**Create the administrator immediately** — until then the unlocked operations
are open to anyone who can reach the port.

If the base image cannot be pulled, point the build at an internal mirror
without editing any file:

```bash
VISTEST_BASE_IMAGE=<mirror>/playwright/python:v1.47.0-jammy \
  docker compose -f docker-compose.team.yml build
```

---

## Configuration

Everything lives in `vistest.yaml`. Three presets:

| Preset | ΔE00 | fail_severity | Use for |
|---|---|---|---|
| `strict` | 1.2 | 10 | design systems, components in isolation |
| `balanced` | 2.3 | 25 | the default |
| `loose` | 4.0 | 45 | pages with live CMS content |

Local overrides go in `vistest.local.yaml` (git-ignored). Per-project and
global thresholds can also be set through the API and are stored in the
database, where they take precedence over the file.

### Baselines across machines

Text rendering differs physically between Windows, macOS and Linux, so
baselines are stored per platform (`baselines/win-chromium-1x/`,
`baselines/docker-chromium-1x/`). You compare against your own set locally and
CI compares against the container set. A single shared baseline is only
possible from a container with a pinned browser version and font set:

```bash
python run.py docker         # start the service
python run.py docker test    # run the tests inside the container
```

---

## Verifying the engine

```bash
pytest tests/                           # invariants: noise must not fail, regressions must be caught
pytest tests/api                        # API tests: auth, roles, invites, team
python tests/benchmark.py --artifacts   # noise / signal table plus images
python tests/benchmark.py --compare     # VisTest vs absdiff, pixelmatch, Playwright
```

The synthetic benchmark is the main tool for tuning thresholds: after any
configuration change you immediately see whether the engine started catching
noise. Real screenshots are useless for this — in a real screenshot you do not
know the ground truth.

The primary metric is **false-fail rate**: the share of pages that did not
meaningfully change but produced a red test.

Statistics from real runs accumulate on their own:

```bash
vistest evidence                        # the number over the last 90 days
vistest evidence --format md -o EVIDENCE.md
```

A failure counts as false when a human looked at it and accepted it as the new
baseline. The report always includes the confidence interval and the share of
unreviewed failures — without those, the number would be misleading.

ΔE00 is separately verified against the reference pairs from the CIE Technical
Report (Sharma et al., 2005).

---

## Command reference

```
python run.py setup                 install dependencies and browsers
python run.py ui                    start the service and open the UI
python run.py doctor [URL]          check the environment, or a project's noise
python run.py record URL            record baselines with the mouse
python run.py snap URL              capture baselines by URL
python run.py check IMAGE           check a ready PNG against the baseline
python run.py compare A.png B.png   compare two PNGs, no browser
python run.py test [pytest args]    run the tests
python run.py update                overwrite the baselines
python run.py list | rm | prune     manage baselines and old runs
python run.py codegen [--pom]       rebuild tests from baseline passports
python run.py project add|list|run|rm    connected test suites
python run.py report RUN            offline HTML report
python run.py evidence              accumulated quality statistics
python run.py push                  upload runs made offline
python run.py docker [up|test|down] run in a container
```

`python -m vistest.cli` exposes the same commands plus `serve` and `user`.

---

## Project layout

```
vistest/core/          comparison engine (numpy/cv2, no browser, no network)
vistest/service.py     single check entry point for pytest / HTTP / CLI / recording
vistest/capture/       stabilisation, N-shot capture, DOM snapshot
vistest/integrations/  universal driver (Playwright/Selenium) and suite adapters
vistest/record/        mouse recording, snap by URL, code generation
vistest/ai/            attribution / perceptual / captioner
vistest/render/        difference visualisation
vistest/api/           FastAPI + SQLite + metrics + /api/check + users and team
vistest/storage/       versioned baselines
frontend/              review UI, no build step
control/               operator panel for per-team instances
scripts/               provisioning and benchmark helpers
deploy/                compose templates and a Caddyfile
clients/               Node client and the API contract for other languages
docker/                images: Dockerfile.api, .full, .runner
tests/                 synthetic engine benchmark and API tests
examples/              demo page and example tests
```

Every entry point — the pytest fixture, the HTTP endpoint, the CLI and the
recorder — goes through one `CheckService`. Thresholds, masks, artifacts and
metrics are identical no matter where a screenshot came from.

---

## Security notes

- Secrets are read from the environment or `.vistest/secrets.env`, which is
  git-ignored. Never commit real credentials; `.vistest/secrets.env.example`
  shows the expected names.
- Saved browser sessions (`.vistest/state/`) are live access to your
  environment. They are git-ignored — keep it that way.
- **Baselines and DOM snapshots contain the content of the pages you capture.**
  If you test an internal system, treat `.vistest/baselines/` as data of the
  same sensitivity as that system, and do not push it to a public repository.
- The variable editor in the UI is limited to localhost by default
  (`VISTEST_SECRETS_UI=local`); `off` disables it, `all` opens it deliberately.

---

## License

AGPL-3.0-or-later. See [LICENSE](LICENSE) and [NOTICE](NOTICE).

If you run a modified version of VisTest as a network service, the AGPL
requires you to offer the complete corresponding source of your modified
version to its users. A commercial license without that obligation is
available — see [NOTICE](NOTICE) for contact details.

"VisTest" and the VisTest logo are trademarks of the project author and are not
covered by the AGPL grant. Forks must be renamed and rebranded.
