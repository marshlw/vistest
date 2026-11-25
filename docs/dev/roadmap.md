# VisTest for QA teams

A QA lead needs to enforce visual coverage in CI, diagnose a red run, review
intentional changes, and trace a baseline decision after merge. This roadmap
complements the [development journal](journal.md).

| Priority | Work | Acceptance criteria |
| --- | --- | --- |
| P0 · implemented | Guard against empty visual runs | `--vistest-require-checks` fails a passing pytest job with zero recorded visual checks, in serial pytest and with xdist. |
| P0 · implemented | Machine-readable CI results | Versioned JSON includes the run outcome and status by snapshot key and platform. Parallel jobs can upload separate results. Empty and incomplete results are distinguishable. The maintainer verified this workflow in a real project. |
| P0 · implemented | Require specific snapshots and environments | CI flags a missing required key or platform even when other checks pass. Diagnostics show observed platforms and report renderer canary changes when detected. |
| P1 · implemented | Accept individual baselines | `--vistest-update-snapshot` selects one baseline path. The controller checks ownership across local xdist workers before writing the baseline. |
| P1 · verified on guest and authenticated screens | Diagnose unstable snapshots | `vistest diagnose` summarizes mixed verdicts, suppression reasons and observed renderer fingerprints across CI reports, grouped by snapshot, platform and baseline. The pilots below verified controlled changes in the real application. |

Engine quality remains a release criterion. Changes to the comparison cascade
are evaluated on the frozen and held-out corpora and on the real application.
For a team pilot, measure false failures, detection of known changes, and time
from CI failure to a baseline decision.

## Windows verification

The full local run used Python 3.12.0, OpenCV 5.1.0, Playwright 1.58.0 and three
xdist workers.

```powershell
$env:PYTEST_XDIST_AUTO_NUM_WORKERS = "3"
$env:PYTHONIOENCODING = "utf-8"
python -m pytest -n auto -q --tb=short
```

The suite checks that a refused coroutine cannot resume and normalizes
JSON-escaped project paths before comparing reports from fresh directories.
Both checks previously failed on Windows with Python 3.12.0 and passed after
the fixes. The full run recorded 2,142 passed, 207 skipped, and six failed.
Each failure reproduced on the starting commit (`2550e63`). Four failures
were in the browser and live corpus tests and one was in the renderer
fingerprint test. The sixth was the `vistest bench` CLI test, which passes
when `PYTHONUTF8=1` makes the test process decode subprocess output as UTF-8.

## Guest-screen pilot

The pilot ran the real application's login and registration pages in local
headless Chromium through a fresh frontend build. It used Windows, Python
3.12.0, OpenCV 5.1.0 and Playwright 1.58.0 at 800 × 800 and 1× scale. A required
snapshot manifest covered both pages. The baseline files were held fixed
across the six measured runs.

```powershell
pytest -q --vistest-require-checks --vistest-require-snapshots required.json --vistest-json-report run-1.json
vistest diagnose run-1.json run-2.json run-3.json run-4.json run-5.json run-6.json
```

Login and registration passed in all three unchanged runs. Hiding the login
submit button in the browser caused both controlled runs to fail on login.
Registration passed in both. Both snapshots passed after restoring the button.
All six reports satisfied coverage for both snapshots. The diagnosis showed
one group with mixed verdicts, corresponding to login on the same baseline.
Both failing runs reported a renderer fingerprint matching that baseline.
Running the diagnosis on the four unchanged reports yielded zero mixed groups.

## Authenticated pilot

A fresh build of the real application used its login and profile APIs with a
synthetic account in a temporary PostgreSQL database. Chromium ran headless
on Windows with Python 3.12.0, OpenCV 5.1.0, Playwright 1.58.0, a 390 × 844
viewport, and 1× scale. The test verified the redirect to login, submitted
the credentials in the browser, and opened the account's profile. It compared
the closed profile and the edit form with the Save button scrolled into view.
A Playwright trial click checked that the button was unobstructed.

The test used the same flags for each measured run and held the baseline files
fixed. Each run directory kept its own HTML and JSON artifacts.

```powershell
python -m pytest ../test_authenticated_profile.py --rootdir=. -q --tb=short `
  --vistest-baselines=../baselines --vistest-require-checks `
  --vistest-require-snapshots=../required.json --vistest-json-report=ci.json
vistest diagnose clean-1/ci.json clean-2/ci.json clean-3/ci.json changed-1/ci.json changed-2/ci.json restored/ci.json
```

The two snapshots passed in all three unchanged runs and after restoration.
In both controlled runs, hiding Save caused only the edit snapshot to fail.
All six runs compared both required snapshots. Diagnosis isolated one mixed
group on the same baseline and reported matching renderer fingerprints in
both failed runs. Diagnosis of the four unchanged runs reported zero mixed
groups.

The first controlled failure took 23 seconds from local pytest exit to the
decision to reject a baseline update. The reviewer knew the mutation, used
the pytest failure and diff, and confirmed the renderer status in JSON. This
measurement covers one local review with the artifacts already available.

### Edge suppression

A separate run cropped the edit form with a Playwright locator. The fixed
navigation overlaid the Save label, leaving a strip of the button along the
bottom edge. When Save was hidden, VisTest classified the strip as
`scrollbar` and passed the check. Scrolling the control clear of the navigation
and comparing the viewport exposed the missing button.

CI reports now include counts by built-in suppression rule, so a passing run
can be inspected for `scrollbar` suppressions in `vistest diagnose`. A replay of
the cropped form with the same baseline recorded `"suppressed_by_rule":
{"scrollbar": 1}`. Diagnosis printed that rule when given the original and
new reports together.

An OpenCV 5.1.0 probe with `compare(expected, actual, engine="v2")` tried
requiring a visible thumb before suppressing an edge band. It caught the
clipped Save button and still suppressed a synthetic track with a visible
thumb. A synthetic track with a transparent thumb started failing. The
current rule is still in use.

A browser probe repeated the comparison on a fixed-width page with horizontal
overflow. In Chromium 145.0.7632.6, launched without Playwright's
`--hide-scrollbars` flag, the two CSS-styled scrollbars passed with the
current rule and failed with the proposed change. One had a visible thumb
and the other a transparent thumb. The default Chromium scrollbar already
failed with the current rule. This gives the next engine investigation two
browser-rendered noise pairs to preserve alongside the clipped-control
regression.
