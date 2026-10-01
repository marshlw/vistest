/* VisTest - self-hosted visual regression testing.
 * Copyright (C) 2026 Kirill Kulagin
 * SPDX-License-Identifier: AGPL-3.0-or-later
 *
 * This file is part of VisTest. See LICENSE for the full terms and NOTICE for
 * the trademark and commercial-licensing terms. Removing this header does not
 * remove those obligations.
 */

/**
 * Playwright's own comparator on a list of pairs, for `vistest bench`.
 *
 *     node playwright_compare.mjs <pairs.json> <folder>...
 *
 * `pairs.json` is `[{"id", "expected", "actual"}, ...]` (absolute paths).
 * Each <folder> is a place to look for Playwright: a project with
 * @playwright/test, playwright or playwright-core in its own node_modules;
 * the first one that has it is used, and only when none has, one Node finds
 * through NODE_PATH or a global install. The comparator is the one
 * scripts/bench_playwright_grid.mjs calls — `getComparator('image/png')`
 * from playwright-core, the function `expect(page).toHaveScreenshot()` calls
 * — with threshold 0.2 (Playwright's default) and 0.05, maxDiffPixels 0,
 * argument order as in Page.expectScreenshot: (actual, expected, options).
 *
 * Prints one JSON object: `{"tool": {...}, "results": {id: {"0.2": {...},
 * "0.05": {...}}}}`, each with `failed`, `pixels` (null when the comparator
 * said something else, e.g. the sizes differ) and `message` then. Exit 3,
 * with `{"error": ...}`, when no Playwright was found. Reads the pictures,
 * writes nothing, opens no network.
 */

import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join, sep } from 'node:path';

const [pairsPath, ...folders] = process.argv.slice(2);
const THRESHOLDS = ['0.2', '0.05'];

function say(obj, code = 0) {
  process.stdout.write(JSON.stringify(obj) + '\n');
  process.exit(code);
}

function pkg(req, name) {
  const path = req.resolve(`${name}/package.json`);
  return { dir: dirname(path), version: JSON.parse(readFileSync(path, 'utf8')).version };
}

// From a folder to playwright-core, the way @playwright/test reaches it.
// `local`: only a Playwright installed in that folder's own node_modules
// counts — Node would otherwise answer from NODE_PATH or a global install
// for every folder, before the project's own was even looked at.
function findCore(folder, local) {
  const req = createRequire(join(folder, 'noop.js'));
  const own = join(folder, 'node_modules') + sep;
  for (const chain of [['@playwright/test', 'playwright', 'playwright-core'],
                       ['playwright', 'playwright-core'],
                       ['playwright-core']]) {
    try {
      let r = req;
      let info = null;
      const versions = {};
      for (const [i, name] of chain.entries()) {
        info = pkg(r, name);
        if (i === 0 && local && !info.dir.startsWith(own)) throw new Error('not local');
        versions[name] = info.version;
        r = createRequire(join(info.dir, 'package.json'));
      }
      const from = local ? folder : 'NODE_PATH or a global install';
      return { req: r, versions, from, top: chain[0] };
    } catch { /* the next chain */ }
  }
  return null;
}

let found = null;
for (const folder of folders) {
  found = findCore(folder, true);
  if (found) break;
}
if (!found && folders.length) found = findCore(folders[0], false);
if (!found) say({ error: 'no @playwright/test, playwright or playwright-core in: ' + folders.join(', ') }, 3);

let getComparator = null;
let comparatorModule = '';
for (const mod of ['playwright-core/lib/coreBundle', 'playwright-core/lib/utils']) {
  try {
    const m = found.req(mod);
    const fn = m.utils?.getComparator ?? m.getComparator;
    if (typeof fn === 'function') { getComparator = fn; comparatorModule = mod; break; }
  } catch { /* next */ }
}
if (!getComparator) {
  say({ error: `getComparator not found in playwright-core ${found.versions['playwright-core']}` }, 3);
}
const compare = getComparator('image/png');

const pairs = JSON.parse(readFileSync(pairsPath, 'utf8'));
const results = {};
for (const [i, p] of pairs.entries()) {
  // As in the grid: a failing call keeps a whole-frame PNG until the event
  // loop runs once, so it yields every ten pairs.
  if (i % 10 === 0) await new Promise((done) => setImmediate(done));
  const actual = readFileSync(p.actual);
  const expected = readFileSync(p.expected);
  const entry = {};
  for (const threshold of THRESHOLDS) {
    const r = compare(actual, expected, { threshold: Number(threshold), maxDiffPixels: 0 });
    if (!r) {
      entry[threshold] = { failed: false, pixels: 0 };
      continue;
    }
    const message = String(r.errorMessage ?? '');
    const m = /(\d+) pixels \(ratio/.exec(message);
    entry[threshold] = m ? { failed: true, pixels: Number(m[1]) }
      : { failed: true, pixels: null, message: message.split('\n')[0] };
  }
  results[p.id] = entry;
}

say({
  tool: {
    title: `Playwright ${found.versions['playwright-core']} toHaveScreenshot()`,
    versions: found.versions,
    from: found.from,
    invoked: `${comparatorModule}: getComparator('image/png')(actual, expected, { threshold, maxDiffPixels: 0 })`,
  },
  results,
});
