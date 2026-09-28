/* VisTest - self-hosted visual regression testing.
 * Copyright (C) 2026 Kirill Kulagin
 * SPDX-License-Identifier: AGPL-3.0-or-later
 *
 * This file is part of VisTest. See LICENSE for the full terms and NOTICE for
 * the trademark and commercial-licensing terms. Removing this header does not
 * remove those obligations.
 */

/**
 * Native Playwright comparator over the browser corpus, on a grid of settings.
 *
 *     npm ci --prefix scripts/bench
 *     node scripts/bench_playwright_grid.mjs > docs/benchmark_browser_native.json
 *     python tests/benchmark.py --corpus browser
 *
 * The comparator is `getComparator('image/png')` from playwright-core — the
 * function `expect(page).toHaveScreenshot()` calls — resolved from the
 * @playwright/test pinned in scripts/bench, exactly as bench_pixelmatch.mjs
 * resolves it. Every pair is compared once per setting, with the options
 * toHaveScreenshot passes when the user sets `threshold` and
 * `maxDiffPixels`: threshold ∈ {0.2, 0.1, 0.05} × maxDiffPixels ∈
 * {0, 25, 100, 500}. 0.2 and 0 are Playwright's defaults.
 *
 * The comparator is called once per pair and threshold, with maxDiffPixels 0,
 * and the pixel count comes from its own error message. The verdict for the
 * other maxDiffPixels is the comparator's rule applied to that count —
 * `count > maxDiffPixels` (playwright-core, compareImages: `const
 * pixelsMismatchError = count > maxDiffPixels ? … : ""`). Calling it twelve
 * times per pair would give the same answer at four times the cost: a failing
 * call encodes a diff PNG of the whole frame. That the rule is applied the
 * way the comparator applies it is not taken on trust: twelve pairs spread
 * over the corpus are also compared with every maxDiffPixels for real, and a
 * disagreement stops the script.
 *
 * The corpus digest is the formula of scripts/browser_corpus.py::corpus_digest,
 * and tests/benchmark.py refuses a JSON computed on other files.
 */

import { createHash } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const repo = resolve(here, '..');
const root = resolve(process.argv[2] ?? join(repo, 'tests', 'browser_corpus'));
const manifestPath = join(root, 'manifest.json');

function die(msg) {
  process.stderr.write(msg + '\n');
  process.exit(2);
}

if (!existsSync(manifestPath)) die(`${manifestPath} does not exist.`);
if (!existsSync(join(here, 'bench', 'node_modules'))) {
  die('Dependencies are not installed. First: npm ci --prefix scripts/bench');
}

const benchRequire = createRequire(join(here, 'bench', 'package.json'));
function pkgInfo(req, name) {
  const path = req.resolve(`${name}/package.json`);
  return { dir: dirname(path), json: JSON.parse(readFileSync(path, 'utf8')) };
}
const ptestPkg = pkgInfo(benchRequire, '@playwright/test');
const playwrightPkg = pkgInfo(createRequire(join(ptestPkg.dir, 'package.json')), 'playwright');
const pwRequire = createRequire(join(playwrightPkg.dir, 'package.json'));
const corePkg = pkgInfo(pwRequire, 'playwright-core');

let getComparator, comparatorModule;
for (const mod of ['playwright-core/lib/coreBundle', 'playwright-core/lib/utils']) {
  try {
    const m = pwRequire(mod);
    const fn = m.utils?.getComparator ?? m.getComparator;
    if (typeof fn === 'function') { getComparator = fn; comparatorModule = mod; break; }
  } catch { /* next */ }
}
if (!getComparator) die(`getComparator not found in playwright-core ${corePkg.json.version}`);
const compare = getComparator('image/png');

const THRESHOLDS = [0.2, 0.1, 0.05];
const MAX_DIFF_PIXELS = [0, 25, 100, 500];

const sha = (b) => createHash('sha256').update(b).digest('hex');
const manifestBytes = readFileSync(manifestPath);
const manifest = JSON.parse(manifestBytes.toString('utf8'));

// Digest: "<path> <sha256>\n" for the manifest, every baseline, every pair.
const lines = [`manifest.json ${sha(manifestBytes)}\n`];
const bases = {};
for (const [key, t] of Object.entries(manifest.templates)) {
  bases[key] = readFileSync(join(root, t.base));
  lines.push(`${t.base} ${sha(bases[key])}\n`);
}

const SELF_CHECK_PAIRS = 12;
const selfCheckEvery = Math.ceil(manifest.cases.length / SELF_CHECK_PAIRS);
let selfChecked = 0;
const results = {};
manifest.cases.forEach((c, i) => {
  const actual = readFileSync(join(root, c.actual));
  lines.push(`${c.actual} ${sha(actual)}\n`);
  const expected = bases[c.template];
  const entry = { diff_pixels: {}, failed: {} };
  for (const threshold of THRESHOLDS) {
    // Argument order as in Page.expectScreenshot: (actual, expected, options).
    const r = compare(actual, expected, { threshold, maxDiffPixels: 0 });
    let count = 0;
    if (r) {
      const m = /(\d+) pixels \(ratio/.exec(r.errorMessage ?? '');
      if (!m) die(`${c.name}: unexpected comparator message: ${r.errorMessage}`);
      count = Number(m[1]);
    }
    entry.diff_pixels[threshold] = count;
    for (const maxDiffPixels of MAX_DIFF_PIXELS) {
      const failed = count > maxDiffPixels;
      if (i % selfCheckEvery === 0) {
        selfChecked += 1;
        const real = compare(actual, expected, { threshold, maxDiffPixels });
        if (Boolean(real) !== failed) {
          die(`${c.name}: threshold ${threshold}, maxDiffPixels ${maxDiffPixels}: ` +
              `the comparator says ${Boolean(real)}, the rule says ${failed}`);
        }
      }
      entry.failed[`${threshold}/${maxDiffPixels}`] = failed;
    }
  }
  results[c.name] = entry;
  if ((i + 1) % 50 === 0) process.stderr.write(`${i + 1}/${manifest.cases.length}\n`);
});

process.stdout.write(JSON.stringify({
  source: 'native',
  generator: 'scripts/bench_playwright_grid.mjs',
  corpus: { sha256: sha(Buffer.from(lines.join(''), 'utf8')), cases: manifest.cases.length },
  environment: {
    node: process.version,
    platform: `${process.platform} ${process.arch}`,
    packages: {
      '@playwright/test': ptestPkg.json.version,
      'playwright': playwrightPkg.json.version,
      'playwright-core': corePkg.json.version,
    },
  },
  tool: {
    title: `Playwright ${ptestPkg.json.version} toHaveScreenshot()`,
    invoked: `playwright-core ${corePkg.json.version} ${comparatorModule}: ` +
             "getComparator('image/png')(actual, expected, { threshold, maxDiffPixels })",
    defaults: 'threshold 0.2, maxDiffPixels 0',
  },
  grid: { threshold: THRESHOLDS, maxDiffPixels: MAX_DIFF_PIXELS,
          verdict: 'count > maxDiffPixels, count from the comparator at maxDiffPixels 0; ' +
                   `checked against real calls on ${selfChecked / (THRESHOLDS.length * MAX_DIFF_PIXELS.length)} ` +
                   'pairs spread over the corpus' },
  results,
}, null, 1) + '\n');
