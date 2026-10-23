# Security

VisTest is a library: it runs inside your test process, on your machine or CI
runner, with the rights that process has. This file says what it touches and
what it leaves to you.

## Reporting a vulnerability

Open a private security advisory at
**https://github.com/marshlw/vistest/security/advisories/new** with a
description and, if you have one, a way to reproduce it. Please do not open a
public issue for something exploitable.

You will get a first answer within 5 working days. If the report is confirmed,
we will agree a disclosure date with you — normally after a fix is released. We
do not run a bug bounty, and we will credit you in the release notes unless you
would rather we did not.

## Supported versions

Only the latest release gets fixes. VisTest is a young product; backporting to
older versions would mean promising something we cannot keep.

## What the library does

- **No network of its own.** VisTest makes no requests and sends nothing
  anywhere: no telemetry, no update checks. The only traffic is the page your
  test opens in Playwright, which is your test's traffic.
- **Files only where you point it.** It reads and writes baselines under
  `tests/__vistest__/` (or the path you give `--vistest-baselines`), and its
  snapshots, diffs and report under `.vistest/` in the project root.
- **The report is one static HTML file** with the pictures inside it. It loads
  nothing from the network when opened.
- **Configuration is read with `yaml.safe_load`**: a `vistest.yaml` cannot run
  code.
- **Scenario steps with a secret** (a selector or value that looks like a
  password, token or code) are masked in the step descriptions VisTest prints.
  Values come from the environment or from `.vistest/secrets.env`.

## What is yours to do

- **Keep `.vistest/secrets.env` out of version control.** VisTest reads it; it
  does not protect it.
- **Treat baselines and reports as pictures of your application.** If your
  pages show personal or confidential data, so do the screenshots: decide where
  they are stored and who can open them, the same as for any test artefact.
- **Pin your dependencies in CI** and keep them updated; `docs/dev/dependencies.md`
  lists what VisTest pulls in.
