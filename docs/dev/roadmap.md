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
| P1 · implemented, pilot pending | Diagnose unstable snapshots | `vistest diagnose` summarizes mixed verdicts, suppression reasons and observed renderer fingerprints across CI reports, grouped by snapshot, platform and baseline. Verify usefulness on unchanged runs and controlled changes to the real application. |

Engine quality remains a release criterion. Changes to the comparison cascade
are evaluated on the frozen and held-out corpora and on the real application.
For a team pilot, measure false failures, detection of known changes, and time
from CI failure to a baseline decision.

Before the pilot, reproduce the full suite on a Windows runner with a bounded
worker count. The local baseline run reproduced failures in terminal output,
snapshot target handling, report comparison, and diff legends. Track those
separately from changes in the visual verdict.
