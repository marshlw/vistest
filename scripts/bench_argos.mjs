/* VisTest - self-hosted visual regression testing.
 * Copyright (C) 2026 Kirill Kulagin
 * SPDX-License-Identifier: AGPL-3.0-or-later
 *
 * This file is part of VisTest. See LICENSE for the full terms and NOTICE for
 * the trademark and commercial-licensing terms. Removing this header does not
 * remove those obligations.
 */

/**
 * Argos's own comparison over the browser corpus, on a grid of its setting.
 *
 *     npm ci --prefix scripts/bench
 *     node scripts/bench_argos.mjs > docs/benchmark_browser_argos.json
 *     python tests/benchmark.py --corpus browser
 *
 * Argos (https://github.com/argos-ci/argos, MIT) is open source; what it
 * does with two screenshots is `diffImages` in
 * apps/backend/src/screenshot-diff/diff/image/index.ts. `argosDiff` below is
 * that function, line for line, at commit ARGOS_COMMIT, with only its
 * tracing (Sentry spans) and its storage (S3 handles, temporary names) taken
 * out: two calls of odiff — the `odiff-bin` Argos pins, here pinned exactly
 * in scripts/bench — one with a high threshold against anti-aliasing and one
 * with a low threshold against colour changes, each with its own share of
 * pixels it lets through; the pair is a change when either score is left
 * above zero. That is what an Argos build marks as «changed» and asks a
 * person to review.
 *
 * The one setting a user has is the threshold, 0.5 by default; the grid is
 * 0.25, 0.5 and 1. The corpus frames all have one size, so Argos's
 * enlargement to a common size never happens; a pair of two sizes stops
 * the script instead of being guessed at.
 *
 * That the port says what Argos says is checked on Argos's own test table
 * (index.test.ts next to that file, its fixtures and their expected
 * verdicts), with a clone of Argos at hand:
 *
 *     node scripts/bench_argos.mjs --argos-fixtures <argos>/apps/backend/src/screenshot-diff/diff/image/__fixtures__
 *
 * Every case but `different-dimensions` (the enlargement this script does
 * not do) must come out as the test expects, or the script stops.
 *
 * The corpus digest is the formula of scripts/browser_corpus.py::corpus_digest,
 * and tests/benchmark.py refuses a JSON computed on other files. The sealed
 * half (held-out-2) is in the digest but compared only with `--final`, as in
 * scripts/bench_playwright_grid.mjs.
 */

import { createHash } from 'node:crypto';
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ARGOS_COMMIT = '91c0d0b0cb8af157518a2a40570af1b16e1f1fa8';
const ARGOS_FILE = 'apps/backend/src/screenshot-diff/diff/image/index.ts';

const here = dirname(fileURLToPath(import.meta.url));
const repo = resolve(here, '..');
const argv = process.argv.slice(2);
const final = argv.includes('--final');
const positional = argv.filter((a, i) => !a.startsWith('--') && argv[i - 1] !== '--argos-fixtures');
const root = resolve(positional[0] ?? join(repo, 'tests', 'browser_corpus'));
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
const odiffVersion = JSON.parse(readFileSync(benchRequire.resolve('odiff-bin/package.json'), 'utf8')).version;
const { compare } = benchRequire('odiff-bin');

// ---- Argos, apps/backend/src/screenshot-diff/diff/image/index.ts ---------- //
const BASE_THRESHOLD = 0.15;
const BASE_MAX_SCORE = 0.0002;
const MAXIMUM_PIXELS_TO_IGNORE = 20;
const COLOR_SENSIBLE_THRESHOLD = 0.0225;
const COLOR_SENSIBLE_MAX_SCORE = 0.03; // 3% of image is different
const DEFAULT_THRESHOLD = 0.5;

function getConfiguration(threshold) {
  const relativeThreshold = threshold * 2;
  return {
    baseThreshold: BASE_THRESHOLD * relativeThreshold,
    baseMaxScore: BASE_MAX_SCORE * relativeThreshold,
    maximumPixelsToIgnore: MAXIMUM_PIXELS_TO_IGNORE * relativeThreshold,
    colorSensitiveThreshold: COLOR_SENSIBLE_THRESHOLD * relativeThreshold,
    colorSensitiveMaxScore: COLOR_SENSIBLE_MAX_SCORE * relativeThreshold,
  };
}

async function computeDiff({ basePath, headPath, diffPath, threshold }) {
  const result = await compare(basePath, headPath, diffPath, {
    outputDiffMask: true,
    threshold,
    antialiasing: true,
  });
  if (result.match) return 0;
  switch (result.reason) {
    case 'file-not-exists':
      throw new Error('File not exists');
    case 'layout-diff':
      return 1;
    case 'pixel-diff':
      return result.diffPercentage / 100;
    default:
      throw new Error('Unknown reason');
  }
}

async function argosDiff(basePath, headPath, maxDimensions, threshold, scratch) {
  const diffConfig = getConfiguration(threshold);
  const [baseScore, colorSensitiveScore] = await Promise.all([
    computeDiff({ basePath, headPath, diffPath: join(scratch, 'base.png'),
                  threshold: diffConfig.baseThreshold }),
    computeDiff({ basePath, headPath, diffPath: join(scratch, 'color.png'),
                  threshold: diffConfig.colorSensitiveThreshold }),
  ]);
  const maxBaseScore = Math.min(
    diffConfig.baseMaxScore,
    diffConfig.maximumPixelsToIgnore / (maxDimensions.width * maxDimensions.height),
  );
  const adjustedBaseScore = baseScore < maxBaseScore ? 0 : baseScore;
  const adjustedSensitiveScore =
    colorSensitiveScore < diffConfig.colorSensitiveMaxScore ? 0 : colorSensitiveScore;
  let score = 0;
  if (adjustedBaseScore > 0 && adjustedBaseScore >= adjustedSensitiveScore) score = adjustedBaseScore;
  else if (adjustedSensitiveScore > 0 && adjustedSensitiveScore > adjustedBaseScore) score = adjustedSensitiveScore;
  return { score, baseScore, colorSensitiveScore };
}
// --------------------------------------------------------------------------- //

const THRESHOLDS = [0.25, DEFAULT_THRESHOLD, 1];

function pngSizeOf(path) {
  const buf = readFileSync(path);
  return { width: buf.readUInt32BE(16), height: buf.readUInt32BE(20) };
}

// Argos's index.test.ts, as of ARGOS_COMMIT: [fixture, threshold, hasDiff].
const ARGOS_TESTS = [
  ['alphaBackground', 0.5, false], ['alphaSmallGraph', 0.2, true],
  ['big-images', 0.5, true], ['border', 0.5, false], ['boxShadow', 0.5, false],
  ['fontAliasing', 0.5, false], ['imageCompression1', 0.5, false],
  ['imageCompression2', 0.5, false], ['imageCompression3', 0.5, false],
  ['imageCompression4', 0.5, false], ['imageCompression5', 0.5, false],
  ['imageCompression6', 0.5, false], ['imageCompression7', 0.5, false],
  ['simple', 0.5, true], ['tableAlpha', 0.5, true], ['minorChange1', 0.5, true],
  ['minorChange2', 0.5, false], ['minorChange3', 0, true],
];

const fixturesAt = argv.indexOf('--argos-fixtures');
if (fixturesAt >= 0) {
  const dir = argv[fixturesAt + 1];
  if (!dir || !existsSync(dir)) die('--argos-fixtures: give the __fixtures__ directory of an Argos clone');
  const scratch = mkdtempSync(join(tmpdir(), 'vistest-argos-'));
  let bad = 0;
  let checked = 0;
  try {
    for (const [name, threshold, hasDiff] of ARGOS_TESTS) {
      const base = join(dir, name, 'base.png');
      const head = join(dir, name, 'compare.png');
      const a = pngSizeOf(base);
      const b = pngSizeOf(head);
      const size = { width: Math.max(a.width, b.width), height: Math.max(a.height, b.height) };
      if (a.width !== b.width || a.height !== b.height) {
        process.stdout.write(`${name}: two sizes — not ported, skipped\n`);
        continue;
      }
      const r = await argosDiff(base, head, size, threshold, scratch);
      const ok = (r.score > 0) === hasDiff;
      checked += 1;
      if (!ok) bad += 1;
      process.stdout.write(`${ok ? 'ok ' : 'BAD'} ${name}: threshold ${threshold}, ` +
                           `score ${r.score} (expected ${hasDiff ? 'a diff' : 'none'})\n`);
    }
  } finally {
    rmSync(scratch, { recursive: true, force: true });
  }
  if (bad) die(`${bad} case(s) of Argos's own test table come out otherwise`);
  process.stdout.write(`Argos's test table: ${checked} of ${ARGOS_TESTS.length} cases checked, ` +
                       `all as expected; ${ARGOS_TESTS.length - checked} of two sizes skipped ` +
                       `(odiff-bin ${odiffVersion}, Argos ${ARGOS_COMMIT.slice(0, 7)})\n`);
  process.exit(0);
}

function pngSize(buf) {
  // IHDR: width and height, big-endian, at bytes 16 and 20.
  return { width: buf.readUInt32BE(16), height: buf.readUInt32BE(20) };
}

const sha = (b) => createHash('sha256').update(b).digest('hex');
const manifestBytes = readFileSync(manifestPath);
const manifest = JSON.parse(manifestBytes.toString('utf8'));

const lines = [`manifest.json ${sha(manifestBytes)}\n`];
for (const t of Object.values(manifest.templates)) {
  lines.push(`${t.base} ${sha(readFileSync(join(root, t.base)))}\n`);
}
const sealedSplit = manifest.sealed ? manifest.sealed.split : null;
const isSealed = (c) => sealedSplit !== null && manifest.templates[c.template].split === sealedSplit;
const compared = manifest.cases.filter((c) => final || !isSealed(c));
for (const c of manifest.cases) {
  lines.push(`${c.actual} ${sha(readFileSync(join(root, c.actual)))}\n`);
}

const scratch = mkdtempSync(join(tmpdir(), 'vistest-argos-'));
const results = {};
try {
  for (const [i, c] of compared.entries()) {
    const basePath = join(root, c.expected);
    const headPath = join(root, c.actual);
    const a = pngSize(readFileSync(basePath));
    const b = pngSize(readFileSync(headPath));
    if (a.width !== b.width || a.height !== b.height) {
      die(`${c.name}: frames of two sizes; Argos would enlarge them, this script does not`);
    }
    const entry = { score: {}, failed: {} };
    for (const threshold of THRESHOLDS) {
      const r = await argosDiff(basePath, headPath, a, threshold, scratch);
      entry.score[threshold] = { final: r.score, base: r.baseScore, color: r.colorSensitiveScore };
      entry.failed[threshold] = r.score > 0;
    }
    results[c.name] = entry;
    if ((i + 1) % 50 === 0) process.stderr.write(`${i + 1}/${compared.length}\n`);
  }
} finally {
  rmSync(scratch, { recursive: true, force: true });
}

process.stdout.write(JSON.stringify({
  source: 'argos',
  generator: 'scripts/bench_argos.mjs',
  corpus: { sha256: sha(Buffer.from(lines.join(''), 'utf8')), cases: compared.length,
            ...(sealedSplit === null ? {} : {
              sealed: { split: sealedSplit, cases: manifest.cases.filter(isSealed).length,
                        compared: final } }) },
  environment: {
    node: process.version,
    platform: `${process.platform} ${process.arch}`,
    packages: { 'odiff-bin': odiffVersion },
  },
  tool: {
    title: `Argos (odiff-bin ${odiffVersion})`,
    source: `https://github.com/argos-ci/argos, ${ARGOS_FILE} at ${ARGOS_COMMIT} (MIT)`,
    invoked: 'diffImages(base, head, { threshold }): two odiff compares with antialiasing, ' +
             'base threshold 0.15 and colour threshold 0.0225 (×2×threshold), ' +
             'each with its own share of pixels let through; changed when the score is above 0',
    defaults: 'threshold 0.5',
  },
  grid: { threshold: THRESHOLDS, verdict: 'score > 0 — what an Argos build marks as changed' },
  results,
}, null, 1) + '\n');
