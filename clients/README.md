# vistest-client

Send screenshots from a JavaScript test suite to a self-hosted
[VisTest](https://github.com/marshlw/visual-testing) service and get back a
verdict from its engine — regions, change classes, severity, artifacts,
history, review with approval.

No dependencies. Node 18+ (built-in `fetch`).

```bash
npm i -D vistest-client
```

## Two ways to use it

### 1. One line in the test — every snapshot, passed ones included

```js
import { VisTest } from 'vistest-client';

const vt = new VisTest({
  apiUrl: process.env.VISTEST_API_URL,
  project: 'web-e2e',
  token: process.env.VISTEST_TOKEN,
  runKey: process.env.CI_JOB_ID,     // this is what names the run
});

await vt.checkPage(page, 'checkout.png');
...
await vt.finish();                    // one run in the history
```

`checkPage` does what the Python runner does: it pulls `/api/stabilize.js`,
freezes animations, substitutes `Math.random` and `Date`, takes a few frames in
a row and captures a DOM snapshot. That is what makes snapshots from a JS suite
comparable with snapshots taken by VisTest itself.

`finish()` is not decoration. Without it every check lives on its own: a verdict
comes back, pictures land on disk — and nothing appears in the run list. No
history for the snapshot, no review queue, no answer to «this build broke three
screens». A run is a separate thing, and only the suite knows when the checks
are over.

Works with Playwright, Puppeteer, WebdriverIO and TestCafe pages; for Cypress,
call it from a `task` — the browser there cannot reach the service directly.

### 2. Zero lines in the test — the Playwright reporter

```ts
// playwright.config.ts
export default defineConfig({
  reporter: [
    ['list'],
    ['vistest-client/playwright-reporter', {
      apiUrl: process.env.VISTEST_API_URL,
      project: 'web-e2e',
    }],
  ],
});
```

When `toHaveScreenshot()` fails, Playwright attaches `<name>-expected.png`,
`<name>-actual.png` and `<name>-diff.png` to the test result. The reporter picks
them up, seeds the baseline from *their* expected picture the first time, and
sends the actual one for judging.

**The limit, stated plainly:** those attachments appear **only for a failed
comparison**. A snapshot that passed leaves nothing behind, and nothing here can
recover it. If you want every snapshot, use the first way. The reporter exists
for the case where nobody is going to touch the tests.

Options: `apiUrl`, `project`, `token`, `runKey`, `prefixWithSpec` (put the spec
file in the snapshot name — for suites where two tests each hold a `login.png`),
`seedBaselines` (default `true`).

### 3. Zero lines in the test — the Cypress plugin

```js
// cypress.config.js
import { defineConfig } from 'cypress';
import vistest from 'vistest-client/cypress';

export default defineConfig({
  e2e: {
    setupNodeEvents(on, config) {
      vistest(on, config, { project: 'web-e2e' });
      return config;
    },
  },
});
```

`after:screenshot` is a point Cypress provides itself, and it fires for **every**
snapshot — the ones that passed included. That is the whole reason this is a
separate plugin rather than a copy of the Playwright reporter, and it is the
closest thing outside Python to what the pytest adapter does.

The screenshot Cypress takes of a failure is skipped on purpose: it is not a
comparison, and sending it as one would compare an arbitrary frame against a
baseline and report a regression out of nowhere.

Options: `apiUrl`, `project`, `token`, `runKey`, `ignore` (a `RegExp` of names to
skip), `name` (a function `details => 'checkout/login.png'` when the default
name is not what you want).

### 4. The JVM — one file, no dependency

`java/VisTest.java` is a single class on `java.net.http`, Java 11+. Copy it into
`src/test/java` and call it:

```java
VisTest vt = new VisTest(System.getenv("VISTEST_API_URL"))
        .project("web-e2e")
        .token(System.getenv("VISTEST_TOKEN"))
        .runKey(System.getenv("CI_JOB_ID"));

VisTest.Result r = vt.check("checkout.png", screenshotBytes);
assertTrue(r.message(), r.passed);
...
vt.finish();
```

It also runs straight from a shell without being compiled, which makes it usable
from a pipeline step in any JVM project:

```bash
java VisTest.java http://vistest:8420 web-e2e checkout.png target/shot.png
```

There is deliberately no Java agent. An agent that replaces the comparison
inside somebody else's suite is the most expensive thing on the roadmap and the
least verifiable — every suite hooks its screenshots differently, and one that
guesses wrong fails silently. For a JVM suite the first choice is not this file
either: it is letting the suite run where it already runs and handing VisTest
the folder afterwards (see below).

## Access

A single-user installation needs nothing. A shared one needs a token — a
per-project one from «CI tokens», or `VISTEST_INGEST_TOKEN` — passed as `token`
or read from `VISTEST_TOKEN`. «Not configured» must not mean «open».

## Without any of this

If your suite is not in JavaScript at all — or you would rather not add a
dependency — let your CI run the tests and hand VisTest the folder afterwards:

```bash
vistest project ingest web-e2e --dir test-results --browser chromium
```

Nothing is started; the pictures your run left behind are judged as they are.

## License

AGPL-3.0-or-later. See `LICENSE` and `NOTICE` in the repository root.
