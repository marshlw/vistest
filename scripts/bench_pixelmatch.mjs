/* VisTest - self-hosted visual regression testing.
 * Copyright (C) 2026 Kirill Kulagin
 * SPDX-License-Identifier: AGPL-3.0-or-later
 *
 * This file is part of VisTest. See LICENSE for the full terms and NOTICE for
 * the trademark and commercial-licensing terms. Removing this header does not
 * remove those obligations.
 */

/**
 * Нативный прогон конкурентов по замороженному корпусу VisTest.
 *
 * Цифры pixelmatch и Playwright в опубликованной таблице получаются здесь,
 * подлинным кодом этих инструментов, а не нашим портом из tests/baselines.py.
 *
 *     npm ci --prefix scripts/bench
 *     node scripts/bench_pixelmatch.mjs > docs/benchmark_native.json
 *     python tests/benchmark.py --compare --native docs/benchmark_native.json
 *
 * Корпус по умолчанию — tests/benchmark_corpus, те же PNG, что читает
 * benchmark.py. Другой каталог можно передать первым аргументом. В JSON
 * пишется отпечаток корпуса (sha256), и benchmark.py отказывается смешивать
 * результаты, посчитанные на других файлах.
 *
 * Настройки — только по умолчанию, ни одна опция не передаётся:
 *
 *   pixelmatch   Запускается его собственный CLI (`bin/pixelmatch`) ровно
 *                так, как его запускает человек: `pixelmatch expected.png
 *                actual.png`, без порога и без includeAA. Значит, threshold
 *                0.1 и includeAA false (детектор анти-алиасинга включён) —
 *                значения самого пакета. Вердикт — код выхода CLI:
 *                0 — совпало, 66 — есть отличающиеся пиксели,
 *                65 — разный размер. Это политика самого инструмента, а не
 *                наша: CLI падает на первом же отличающемся пикселе.
 *
 *   playwright   `getComparator('image/png')` из playwright-core — та самая
 *                функция, которую вызывает `expect(page).toHaveScreenshot()`
 *                (Page.expectScreenshot в playwright-core). Модуль берётся из
 *                того же места, откуда его берёт установленный @playwright/test.
 *                Опции — ровно тот объект, который собирает toHaveScreenshot,
 *                когда пользователь ничего не задал: comparator, threshold,
 *                maxDiffPixels, maxDiffPixelRatio равны undefined. Внутри это
 *                значит: comparator "pixelmatch" (своя копия pixelmatch внутри
 *                playwright-core), threshold 0.2, includeAA false,
 *                maxDiffPixels 0 → один непрощённый пиксель валит тест;
 *                разный размер — тоже падение.
 *
 * Что здесь НЕ воспроизводится у Playwright: съёмка. toHaveScreenshot сам
 * снимает страницу (animations: "disabled", caret: "hide", повторные кадры до
 * стабильности), а в корпусе снимки уже готовы. Сравниваются только готовые
 * пары — ровно та часть, которая решает, красный тест или зелёный.
 */

import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const repo = resolve(here, '..');
const benchPkg = join(here, 'bench', 'package.json');
const root = resolve(process.argv[2] ?? join(repo, 'tests', 'benchmark_corpus'));
const manifestPath = join(root, 'manifest.json');

function die(msg) {
  process.stderr.write(msg + '\n');
  process.exit(2);
}

if (!existsSync(manifestPath)) die(`Не найден ${manifestPath}.`);
if (!existsSync(join(here, 'bench', 'node_modules'))) {
  die('Зависимости не установлены. Сначала: npm ci --prefix scripts/bench');
}

// --------------------------------------------------------------------------- //
//  Где лежат инструменты — строго там, откуда их берёт пользователь
// --------------------------------------------------------------------------- //
const benchRequire = createRequire(benchPkg);

function pkgInfo(req, name) {
  const path = req.resolve(`${name}/package.json`);
  return { dir: dirname(path), json: JSON.parse(readFileSync(path, 'utf8')) };
}

const pixelmatchPkg = pkgInfo(benchRequire, 'pixelmatch');
const pixelmatchBin = join(pixelmatchPkg.dir,
  typeof pixelmatchPkg.json.bin === 'string'
    ? pixelmatchPkg.json.bin : pixelmatchPkg.json.bin.pixelmatch);
const pngjsPkg = pkgInfo(createRequire(join(pixelmatchPkg.dir, 'package.json')), 'pngjs');

const ptestPkg = pkgInfo(benchRequire, '@playwright/test');
// @playwright/test → playwright → playwright-core: разрешаем так же, как это
// делает сам matcher (require из каталога пакета playwright).
const ptestRequire = createRequire(join(ptestPkg.dir, 'package.json'));
const playwrightPkg = pkgInfo(ptestRequire, 'playwright');
const pwRequire = createRequire(join(playwrightPkg.dir, 'package.json'));
const corePkg = pkgInfo(pwRequire, 'playwright-core');

function loadGetComparator() {
  // 1.5x+ собирает утилиты в coreBundle; до этого они лежали в lib/utils.
  for (const mod of ['playwright-core/lib/coreBundle', 'playwright-core/lib/utils']) {
    try {
      const m = pwRequire(mod);
      const fn = m.utils?.getComparator ?? m.getComparator;
      if (typeof fn === 'function') return { fn, from: mod };
    } catch { /* пробуем следующий */ }
  }
  die(`В playwright-core ${corePkg.json.version} не найден getComparator. ` +
      'Формат пакета поменялся: найдите, чем сравнивает Page.expectScreenshot, ' +
      'и поправьте этот скрипт. Подставлять наш порт вместо него нельзя.');
}
const { fn: getComparator, from: comparatorModule } = loadGetComparator();
const pwCompare = getComparator('image/png');

// То, что собирает toHaveScreenshot(), когда ни в тесте, ни в конфиге ничего
// не задано (playwright/lib/matchers: expectScreenshotOptions).
const PLAYWRIGHT_DEFAULT_OPTIONS = {
  comparator: undefined,
  maxDiffPixels: undefined,
  maxDiffPixelRatio: undefined,
  threshold: undefined,
};

// --------------------------------------------------------------------------- //
//  Корпус и его отпечаток
// --------------------------------------------------------------------------- //
const manifestBytes = readFileSync(manifestPath);
const manifest = JSON.parse(manifestBytes.toString('utf8'));
const sha = (b) => createHash('sha256').update(b).digest('hex');

// Отпечаток: sha256 от строк "<путь> <sha256 файла>\n" — манифест и все PNG в
// порядке манифеста. Ту же формулу считает tests/benchmark.py.
const digestLines = [`manifest.json ${sha(manifestBytes)}\n`];

function pngSize(buf) {
  // IHDR: ширина и высота — big-endian uint32 по смещениям 16 и 20.
  return { width: buf.readUInt32BE(16), height: buf.readUInt32BE(20) };
}

function runPixelmatchCli(expectedPath, actualPath) {
  const r = spawnSync(process.execPath, [pixelmatchBin, expectedPath, actualPath],
    { encoding: 'utf8' });
  if (r.error) throw r.error;
  const out = (r.stdout ?? '') + (r.stderr ?? '');
  const m = /different pixels: (\d+)/.exec(out);
  if (r.status === 0 || r.status === 66) {
    if (!m) throw new Error(`pixelmatch: неожиданный вывод: ${out}`);
    return { failed: r.status === 66, exit_code: r.status, diff_pixels: Number(m[1]),
             size_changed: false };
  }
  if (r.status === 65) {
    return { failed: true, exit_code: 65, diff_pixels: null, size_changed: true };
  }
  throw new Error(`pixelmatch: код выхода ${r.status}: ${out}`);
}

function runPlaywright(expectedBuf, actualBuf) {
  // Порядок аргументов — как в Page.expectScreenshot: (actual, expected, options).
  const res = pwCompare(actualBuf, expectedBuf, { ...PLAYWRIGHT_DEFAULT_OPTIONS });
  if (!res) return { failed: false, diff_pixels: 0, size_changed: false };
  const msg = res.errorMessage ?? '';
  const m = /(\d+) pixels \(ratio/.exec(msg);
  return {
    failed: true,
    diff_pixels: m ? Number(m[1]) : 0,
    size_changed: msg.includes('Expected an image'),
    message: msg.trim(),
  };
}

const results = { pixelmatch: {}, playwright: {} };

for (const c of manifest.cases) {
  const expectedPath = join(root, c.expected);
  const actualPath = join(root, c.actual);
  const expectedBuf = readFileSync(expectedPath);
  const actualBuf = readFileSync(actualPath);
  digestLines.push(`${c.expected} ${sha(expectedBuf)}\n`, `${c.actual} ${sha(actualBuf)}\n`);
  const { width, height } = pngSize(expectedBuf);
  const total = width * height;

  results.pixelmatch[c.name] = { ...runPixelmatchCli(expectedPath, actualPath),
                                 total_pixels: total };
  results.playwright[c.name] = { ...runPlaywright(expectedBuf, actualBuf),
                                 total_pixels: total };
}

const corpusSha = sha(Buffer.from(digestLines.join(''), 'utf8'));

process.stdout.write(JSON.stringify({
  source: 'native',
  generator: 'scripts/bench_pixelmatch.mjs',
  corpus: { sha256: corpusSha, cases: manifest.cases.length },
  environment: {
    node: process.version,
    platform: `${process.platform} ${process.arch}`,
    packages: {
      'pixelmatch': pixelmatchPkg.json.version,
      'pngjs (used by pixelmatch)': pngjsPkg.json.version,
      '@playwright/test': ptestPkg.json.version,
      'playwright': playwrightPkg.json.version,
      'playwright-core': corePkg.json.version,
    },
  },
  tools: {
    pixelmatch: {
      title: `pixelmatch ${pixelmatchPkg.json.version}`,
      native: true,
      invoked: 'npm package CLI: pixelmatch <expected.png> <actual.png>',
      settings: {
        threshold: '0.1 (package default, not passed)',
        includeAA: 'false (package default: anti-aliasing detector on)',
        'fail when': 'CLI exit code != 0: any differing pixel (66) or size mismatch (65)',
      },
    },
    playwright: {
      title: `Playwright ${ptestPkg.json.version} toHaveScreenshot()`,
      native: true,
      invoked: `playwright-core ${corePkg.json.version} ` +
               `${comparatorModule}: getComparator('image/png')(actual, expected, options) — ` +
               'the call Page.expectScreenshot makes for toHaveScreenshot()',
      settings: {
        comparator: '"pixelmatch" (default; option not set)',
        threshold: '0.2 (default; option not set)',
        includeAA: 'false (built into Playwright\'s pixelmatch copy)',
        maxDiffPixels: '0 (default; option not set)',
        maxDiffPixelRatio: 'not set',
        'fail when': 'more than 0 differing pixels, or size mismatch',
        'not reproduced': 'page capture (animations/caret/stability retries): the corpus is already captured',
      },
    },
  },
  results,
}, null, 2) + '\n');
