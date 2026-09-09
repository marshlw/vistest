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
| **Ignore zones bind to elements** | A zone follows the element it was drawn on instead of staying at its coordinates — so it survives the redesign that moved the block. When the element disappears the zone falls back to coordinates and *says so*, because a mask that quietly stopped working looks exactly like one that works. |
| **CI tokens per project** | A token belongs to a project, not to the installation: it cannot write another project's runs, it signs the audit log with its own name, and it carries a last-used stamp — the one thing that answers «is it safe to revoke the old one yet». |
| **Three shapes of a run** | Run as the tests decide, in one named browser, one run per browser (in parallel — different browsers keep different baseline sets), or all browsers in a single run with a single verdict. For your own tests and for connected suites alike. |
| **One snapshot, many variants** | A matrix of browsers × window sizes runs in a single run and asks a single question. Each variant keeps its own baseline — a firefox frame at 390 has nothing to compare against in a chromium set at 1440. |

The comparison engine combines **ΔE00** (perceptual colour distance), **SSIM**
(structural similarity), sub-pixel **alignment**, and automatic noise
suppression. A pixel counts as changed only when both colour *and* structure
agree that it changed.

### Browsers and window sizes

```yaml
# vistest.yaml
matrix:
  browsers: [chromium, firefox]
  viewports: ["1440x900", "768x1024", "390x844"]
  base_viewport: "1440x900"   # its baselines stay exactly where they are
```

```bash
python run.py matrix        # what this expands to, before you run it
```

Six variants, one run, one verdict. Each variant stores its baselines under its
own key (`linux-chromium-1x-390x844`); the **base** size keeps the plain key it
always had, so enabling the matrix does not invalidate a single baseline you
have already captured. That is the whole reason `base_viewport` exists — set it
explicitly, because otherwise reordering the list moves baselines on disk while
looking like a formatting change.

A variant's size overrides the size pinned in a snapshot's own passport: the
matrix is a statement about the whole set. Snapshots whose pinned size differs
from the base one are named out loud in the run log rather than silently
re-captured.

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

## Library mode: no server, no interface

VisTest can be used the way Playwright's own screenshot assertion is used — as
a library inside your test run, with the baselines living in your repository
and nothing else running anywhere.

```bash
pip install vistest            # numpy, opencv, pillow, pyyaml. No web server.
```

```python
from vistest import expect_screenshot


def test_home(page):
    page.goto("https://example.com")
    expect_screenshot(page, "home.png")
```

The first run fails, because there is no baseline yet and a check that passes
before anyone has looked at the picture is not a check:

```
vistest: no baseline for 'home.png' (linux-chromium-1x-1440x900)
  expected: tests/__vistest__/linux-chromium-1x-1440x900/home.png
  captured: .vistest/actual/linux-chromium-1x-1440x900/home.png
  create it with: pytest --vistest-update
```

Run `pytest --vistest-update`, look at the PNG, commit it. From then on the
check compares against it, and when it goes red it says where everything is:

```
vistest: 'home.png' differs from the baseline (linux-chromium-1x-1440x900)
  severity 61.2 (limit 25.0), changed area 3.40% (limit 0.15%)
  reason: text in 2 regions; largest 96x24 at (320, 180), .header .price
  baseline: tests/__vistest__/linux-chromium-1x-1440x900/home.png
  actual:   .vistest/actual/linux-chromium-1x-1440x900/home.png
  diff:     .vistest/diff/linux-chromium-1x-1440x900/home.png
  report:   .vistest/report/index.html
  accept it with: pytest --vistest-update
```

`expect_screenshot` takes a Playwright `Page` or `Locator`, PNG bytes, a
`PIL.Image`, a numpy array, or a path to a PNG — so it works with a project's
own page wrapper, and works with no browser at all. Playwright is never
imported to find out which.

```python
expect_screenshot(
    page, "checkout.png",
    platform="chromium-1440x900",      # which directory the baseline is in
    threshold=40,                      # or {"fail_severity": 40, ...}
    mask=["#promo", (0, 0, 320, 64)],  # selectors are painted, boxes are ignored
    full_page=False,
)
```

### Where the files go

```
tests/__vistest__/                 <- committed, reviewed in pull requests
  linux-chromium-1x-1440x900/
    home.png                       the baseline
    home.json                      its passport: version, size, thresholds (optional)
    shop/checkout.png

.vistest/                          <- ignored
  actual/…  diff/…  report/index.html
```

Add one line to your `.gitignore`:

```gitignore
# VisTest artifacts. Baselines live in tests/__vistest__/ and MUST be committed.
.vistest/
```

PNGs grow a repository quickly, so baselines are usually better off in LFS:

```bash
git lfs install
git lfs track "tests/__vistest__/**/*.png"
```

### Flags

| Flag | `pyproject.toml` | What it does |
| --- | --- | --- |
| `--vistest-update` | — | accept the current screenshots as the baselines |
| `--vistest-baselines=PATH` | `vistest_baselines` | where the baselines live (default `tests/__vistest__`) |
| `--vistest-platform=NAME` | `vistest_platform` | the platform directory; taken from the page when not set |
| `--vistest-report=PATH` | `vistest_report` | where the HTML report goes |

Command line beats `pyproject.toml`, which beats `vistest.yaml`, which beats
the defaults. A target that is not a live page — `bytes`, a file, an array —
carries no browser and no window size, so those baselines go straight to the
root of the baseline directory and a warning says so once per run: set
`vistest_platform` yourself if the pictures depend on the machine that took
them, or a developer's macOS and a Linux CI will compare against one file.

The report is one self-contained HTML file — pictures inlined, no
CDN, no fonts, nothing fetched — so it opens on a machine with no network and
can be attached to a ticket.

`pytest -n auto` is supported: every write goes through a temporary file and an
atomic rename, there is no shared index and no lock, and the report is
assembled once at the end from the rows each worker left behind.

### Growing out of it

The layout the library writes and the archive `vistest baselines export` makes
are the same format, so a set can be moved into a full installation later
without re-approving anything:

```bash
vistest baselines export set.tar.gz --baselines tests/__vistest__
vistest baselines import set.tar.gz --mode new          # on the server
```

It works in the other direction too — `--layout flat` writes an installation's
baselines back out as plain files in a repository.

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

### Suites that are not in Python

A suite started by its own command is connected the same way; set `runner` and
the command, and VisTest judges the run by the pictures it leaves behind:

```yaml
projects:
  web-e2e:
    runner: command
    command: [npx, playwright, test]
    root: /srv/web-e2e
    suite: auto            # or playwright | cypress | jest-image-snapshot | …
```

`suite` is the **profile** — the one place that knows what a tool does on disk.
It answers three questions and nothing else: how the suite is started, which
folders it writes pictures to, and how it names them. Playwright leaves
`login-actual.png`, `login-expected.png` and `login-diff.png` in
`test-results`; Cypress writes `login.actual.png`; jest-image-snapshot writes
`login-received.png` beside `login-snap.png`; BackstopJS keeps two parallel
folders. All of that is one declarative profile each.

Recognised out of the box: **Playwright**, **Cypress**, **jest-image-snapshot**,
**BackstopJS**, **WebdriverIO**, **pytest**, and command shapes for **Maven**,
**Gradle** and **dotnet test**. `auto` picks one by the markers in your
repository — `playwright.config.ts`, `cypress.config.js`, `backstop.json`,
`pom.xml`, `conftest.py`. Nothing recognised means the widest set of rules, not
a guess.

When a suite's layout matches no profile — and on the JVM that is the normal
case, since there is no dominant convention — describe it instead of forking
anything. One regular expression per kind, the snapshot name in a `name` group:

```yaml
    search_dirs: [target/screenshots]
    naming:
      actual:   'snap_(?P<name>.+)_new\.png'
      expected: 'snap_(?P<name>.+)_base\.png'
      diff:     'snap_(?P<name>.+)_diff\.png'
```

Two limits worth knowing before you connect, because neither is fixable from
our side. Interception — the comparison replaced inside your test, with DOM
attribution — is a pytest plugin and cannot exist in a Node or JVM process; a
foreign suite is always judged by its finished pictures. And Playwright writes
those pictures **only for a failed comparison**, so a green Playwright run
leaves nothing to review.

Adding the next tool is a `SuiteProfile` in `vistest/suites/builtin.py` and a
line in `PROFILES`. Nothing in the runner or the collector needs to know it
exists.

Secrets are never stored in the project description. Values are referenced by
name (`${YOUR_APP_PASSWORD}`) and resolved from the environment or from
`.vistest/secrets.env`; see `.vistest/secrets.env.example`. If a variable is
missing, the run fails with a clear message instead of submitting an empty
password.

### Three ways in, and how to pick one

|  | What it costs you | What you get |
|---|---|---|
| **We run your suite** — `runner: command` | Your tool must exist in the VisTest image: for Node that means `docker/Dockerfile.node` and the project mounted with its `node_modules` | One button, the full verdict, nothing added to your CI |
| **You run it, we read the result** — `vistest project ingest` | Nothing. No runtime of yours in our image at all | The same verdict, and the only sane option for a JVM or .NET suite |
| **Your test calls us** — `POST /api/check` | One line in the test | The verdict inside the test, and a snapshot that passed is a result too |

The second one is the one to reach for first when the suite is not in Python:

```bash
# their CI, right after the suite has run
vistest project ingest web-e2e --dir test-results --browser chromium
```

Nothing is started; VisTest reads the pictures the run left behind, judges them
with its engine and records a run in the shared history. The same action lives
in the interface under «⋯ → Read ready artifacts».

The third one is a single call from any language:

```bash
curl -H "X-VisTest-Token: $VISTEST_TOKEN" \
     -F image=@shot.png -F name=checkout.png -F project=web-e2e \
     http://127.0.0.1:8420/api/check
```

The Node client (`clients/`, published as `vistest-client`) takes the snapshot
with your own driver, stabilizes the page with the same script the Python runner
uses, and closes the run with one call:

```js
const vt = new VisTest({ apiUrl, project: 'web-e2e', runKey: process.env.CI_JOB_ID });
await vt.checkPage(page, 'checkout.png');
await vt.finish();          // one run in the history, not a scatter of checks
```

For a Playwright suite nobody is going to touch, there is a reporter — zero
lines in the tests:

```ts
reporter: [['list'], ['vistest-client/playwright-reporter',
                      { apiUrl, project: 'web-e2e' }]]
```

It picks the `expected / actual / diff` triple out of Playwright's own report,
seeds the baseline from *their* expected picture the first time, and sends the
actual one for judging. The limit is worth knowing before you wire it up:
Playwright attaches those pictures **only for a failed comparison**, so a green
run leaves nothing to review.

Cypress has no such limit — `after:screenshot` is a point it provides itself and
it fires for every snapshot, passed ones included:

```js
setupNodeEvents(on, config) { vistest(on, config, { project: 'web-e2e' }); }
```

For the JVM there is `clients/java/VisTest.java` — one class on
`java.net.http`, Java 11+, no dependency to add to anyone's `pom.xml`. It runs
straight from a shell too: `java VisTest.java <url> <project> <name> <file.png>`.
There is deliberately no Java agent: one that replaces the comparison inside
somebody else's suite is the least verifiable thing on the roadmap, and for a
JVM suite the first choice is `vistest project ingest` anyway.

**Access.** On a single-user installation everything above just works. A shared
one needs a token — a per-project one from «CI tokens», or `VISTEST_INGEST_TOKEN`
— in `X-VisTest-Token`; the clients read it from `VISTEST_TOKEN`. «Not
configured» must not mean «open».

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
python run.py matrix                what the browser × size matrix expands to
python run.py project add|list|run|rm    connected test suites
python run.py report RUN            offline HTML report
python run.py evidence              accumulated quality statistics
python run.py push                  upload runs made offline
python run.py docker [up|test|down] run in a container
```

`python -m vistest.cli` exposes the same commands plus `serve` and `user`.

### Signing in through a corporate directory

LDAP and Active Directory, configured in **Settings → Corporate directory**.

```bash
pip install "vistest[ldap]"          # ldap3, pure Python, installs offline
VISTEST_LDAP_BIND_PASSWORD=…         # the service account password
```

Then, in the interface: the server (`ldaps://dc.company.local`), the base DN,
the service account DN, and a mapping from groups to roles, one per line:

```
CN=QA Leads,OU=Groups,DC=company,DC=local=admin
CN=QA,OU=Groups,DC=company,DC=local=reviewer
```

**Test the connection** answers step by step — connected, service account
signed in, person found, role that person would get — because «it does not
work» is not something anyone can act on.

Three things worth knowing before you turn it on:

- **Local accounts keep working.** The directory is asked second. If it is
  unreachable, an administrator with a local password still gets in and can
  turn the integration off.
- **People appear on first sign-in.** No import, no sync job. The role is
  recalculated from group membership at every sign-in, so leaving a group takes
  effect immediately — except a role you raised by hand in VisTest, which is
  kept.
- **A directory account cannot be opened with a local password**, so disabling
  someone in the directory really does mean they cannot sign in.

### Moving baselines between installations

Different from a backup: a subset, merged into an installation that already has
its own baselines. For dev → staging, for a contractor handing work over, for a
team that splits in two.

```bash
# take one project's set
python -m vistest.cli baselines export shop.tar.gz --project shop

# see what an import would do, and change nothing
python -m vistest.cli baselines import shop.tar.gz --mode update --dry-run

# merge it in
python -m vistest.cli baselines import shop.tar.gz --mode update
```

Three modes, because «this snapshot already exists here» has three sensible
answers: `new` keeps what is here and takes only what is missing (the default);
`update` brings the incoming picture in as a **new version**, so the previous
one stays in the history and can be rolled back to from the interface;
`replace` lets the incoming set win, history included.

The same thing is available over the API (`GET /api/baselines/export`,
`POST /api/baselines/import`) for a reviewer.

Approval history does not travel. A signature under «I looked at this and it is
correct» belongs to the person who gave it, in the installation where they gave
it.

### Backups

```bash
# Baselines, the database and the list of connected projects, in one archive.
python -m vistest.cli backup vistest-2026-09-05.tar.gz

# What is inside, without unpacking it
python -m vistest.cli restore vistest-2026-09-05.tar.gz --show

# Into an empty data volume; --force to restore over a live installation
python -m vistest.cli restore vistest-2026-09-05.tar.gz
```

The database is snapshotted with `VACUUM INTO`, so the backup can be taken
while the service is running — copying `vistest.db` by hand under WAL gives a
file that opens, sometimes.

`secrets.env` is **not** included unless you pass `--with-secrets`: a backup
travels to file shares and tickets, and stand credentials should not travel
with it silently. Run artifacts are left out too — they are recreated by the
next run, and the point of the backup is the baselines, which are not
recreated by anything: they are accumulated human decisions.

---

## Project layout

```
vistest/core/          comparison engine (numpy/cv2, no browser, no network)
vistest/service.py     single check entry point for pytest / HTTP / CLI / recording
vistest/capture/       stabilisation, N-shot capture, DOM snapshot
vistest/integrations/  universal driver (Playwright/Selenium) and suite adapters
vistest/record/        mouse recording, snap by URL, code generation
vistest/ai/            attribution / learned gate / perceptual filter
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

### Behind a reverse proxy

VisTest does **not** trust `X-Forwarded-*` from anyone by default, and this is
not paranoia — it is the difference between a boundary and a decoration.

Several actions are restricted to «from the service machine only»: connecting a
project (which starts an arbitrary process), editing and running tests, the
variable editor, mouse recording. That check reads the address of the
connection. If the process is started with uvicorn's `--forwarded-allow-ips *`,
that address is taken from the **first** element of `X-Forwarded-For` — a value
the caller writes themselves, because a proxy *appends* its own address rather
than replacing the header. One request header then opens every one of those
actions.

So: run without `--proxy-headers` (the shipped images already do), and name
your proxy explicitly.

```bash
# The address or subnet of your nginx/Caddy/Traefik. A name works too — in
# docker-compose it is usually the service name.
VISTEST_TRUSTED_PROXIES=10.0.0.5,172.18.0.0/16

# TLS is terminated in front, and you would rather not depend on headers at all:
VISTEST_COOKIE_SECURE=1
```

With the variable unset nothing is trusted: the connection address is used as
is, and the session cookie gets `Secure` only over real HTTPS or when
`VISTEST_COOKIE_SECURE=1` says so. With it set, `X-Forwarded-Proto` from that
proxy decides the cookie flag, and the client address in the audit log and in
the sign-in throttling is taken from the forwarded chain — but «local» still
means the connection itself, never the header.

### The API schema

`/docs`, `/redoc` and `/openapi.json` are closed. They list every route, every
request body and every field name, which is a study aid for whoever is looking
at the service from outside. Turn them on while you need them:

```bash
VISTEST_DOCS=on
```

### Intake limits

The entry point for foreign test suites (`POST /api/check`) accepts a
screenshot from any CI, so it has ceilings — all overridable:

| Variable | Default | What it limits |
|---|---|---|
| `VISTEST_MAX_ARTIFACT_MB` | 40 | one uploaded image or artifact |
| `VISTEST_MAX_FRAMES` | 8 | extra frames per check |
| `VISTEST_MAX_PIXELS` | 60000000 | pixels after decoding — a small PNG can unpack into gigabytes |
| `VISTEST_MAX_BODY_MB` | 64 | any other request body (uploads have their own, stricter limits) |
| `VISTEST_ENGINE_MAX_PIXELS` | 80000000 | what the comparison engine agrees to hold in memory |

### The API path

Routes are reachable both as `/api/...` and as `/api/v1/...`. Clients in other
languages should use the versioned form: when the contract changes one day,
`/api/v2` will be a separate thing and `/api/v1` will keep working.

A project archive (`POST /api/projects/upload`) is capped at 200 MB, 2 GB
unpacked and 50 000 entries.

---

## Licensing

Two things share the word «licence» here, and mixing them up causes trouble, so
they are separated on purpose:

- **The source licence is AGPL-3.0-or-later.** That is what governs the code:
  read it, modify it, run it, and if you run a modified version as a network
  service, publish your changes. Nothing below takes that away.
- **A commercial licence key** is what a paying customer receives. It states
  who bought it, until when, and how many projects and users it covers.

### Editions

| | Free tier | With a key |
|---|---|---|
| Engine, UI, CI integration, clients | everything | everything |
| Connected projects | 2 | as bought |
| Active users | 5 | as bought |
| Support and updates | community | as agreed |

The free tier is not a crippled demo: the engine, the review flow and every
integration are the same. The limits are the whole difference.

### Installing a key

Three places, checked in this order — the environment wins so that a container
is configured the way containers are configured:

```bash
VISTEST_LICENSE="eyJlZGl0aW9uIjoicHJvIiw…"   # environment
.vistest/license.key                          # the data volume
```

…or paste it into **Settings → Licence** in the interface. It is verified on
the spot and saved to the data volume.

Verification is entirely offline: the key carries an RSA signature, the public
half is compiled into VisTest, and checking it needs no network and no extra
package. An installation inside a closed perimeter never has to reach anything.

### When a term ends

Nothing is taken away from the customer. Baselines stay readable, history stays
readable, review keeps working — those are your data and your decisions.

For thirty days after the end date the installation works normally and says so
on every screen. After that, and only then, **new runs are refused** with a 402
and an explanation. Installing a current key restores everything immediately.

### Issuing keys (for the vendor)

```bash
python scripts/issue_license.py --new-key ~/.vistest-signing/private.pem
python scripts/issue_license.py --key ~/.vistest-signing/private.pem \
    --customer "ACME GmbH" --expires 2027-09-01 --projects 10 --users 25
```

The private half never goes into the repository, a backup that leaves your
machine, or a support ticket.

### An honest note about enforcement

The source is open, so anyone can delete the check and rebuild. That is a
property of the AGPL, not a hole to be plugged, and no amount of obfuscation
would change it. What the key does is make the terms explicit and checkable —
so that an honest customer knows what they bought, and a renewal is a
conversation rather than an audit.

---

## License

AGPL-3.0-or-later. See [LICENSE](LICENSE) and [NOTICE](NOTICE).

If you run a modified version of VisTest as a network service, the AGPL
requires you to offer the complete corresponding source of your modified
version to its users. A commercial license without that obligation is
available — see [NOTICE](NOTICE) for contact details.

"VisTest" and the VisTest logo are trademarks of the project author and are not
covered by the AGPL grant. Forks must be renamed and rebranded.
