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
| P1 · verified on guest screens | Diagnose unstable snapshots | `vistest diagnose` summarizes mixed verdicts, suppression reasons and observed renderer fingerprints across CI reports, grouped by snapshot, platform and baseline. The pilot below verified the mixed verdict on a controlled change to the real application. |

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

The next pilot should cover an authenticated workflow and measure the time
from a failed CI job to a baseline decision during review.
