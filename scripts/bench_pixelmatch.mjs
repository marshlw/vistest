/* VisTest - self-hosted visual regression testing.
 * Copyright (C) 2026 Kirill Kulagin
 * SPDX-License-Identifier: AGPL-3.0-or-later
 *
 * This file is part of VisTest. See LICENSE for the full terms and NOTICE for
 * the trademark and commercial-licensing terms. Removing this header does not
 * remove those obligations.
 */

/**
 * Нативный прогон pixelmatch по экспортированному корпусу VisTest.
 *
 * Нужен ровно для одного: чтобы цифра в README была получена
 * подлинным npm-пакетом, а не нашим портом на numpy. Порт живёт в
 * tests/baselines.py и помечен в таблице как порт — с этим скриптом его
 * можно проверить, а результаты pixelmatch и Playwright опубликовать честно.
 *
 * Подготовка корпуса:
 *     python tests/benchmark.py --export bench_out/corpus
 *
 * Запуск:
 *     npm install pixelmatch pngjs
 *     node scripts/bench_pixelmatch.mjs bench_out/corpus > bench_out/native.json
 *
 * Дальше:
 *     python tests/benchmark.py --compare --native bench_out/native.json
 *
 * Считаются две конфигурации:
 *   pixelmatch   threshold 0.1  — значение по умолчанию самого пакета
 *   playwright   threshold 0.2  — значение по умолчанию toHaveScreenshot()
 *
 * Политика падения в обоих случаях одна: тест красный, если остался хотя бы
 * один непрощённый пиксель. Так ведёт себя Playwright из коробки.
 */

import { readFileSync, existsSync } from 'node:fs';
import { join, resolve } from 'node:path';
import pixelmatch from 'pixelmatch';
import { PNG } from 'pngjs';

const root = resolve(process.argv[2] ?? 'bench_out/corpus');
const manifestPath = join(root, 'manifest.json');

if (!existsSync(manifestPath)) {
  console.error(`Не найден ${manifestPath}.`);
  console.error('Сначала: python tests/benchmark.py --export ' + root);
  process.exit(2);
}

const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'));
const CONFIGS = [
  { key: 'pixelmatch', threshold: 0.1 },
  { key: 'playwright', threshold: 0.2 },
];

const results = {};
for (const cfg of CONFIGS) results[cfg.key] = {};

for (const c of manifest.cases) {
  const a = PNG.sync.read(readFileSync(join(root, c.expected)));
  const b = PNG.sync.read(readFileSync(join(root, c.actual)));

  // Разный размер pixelmatch не умеет и бросает исключение. Для всех
  // инструментов трактуем это одинаково: несовпадение размера = падение.
  const sizeChanged = a.width !== b.width || a.height !== b.height;

  for (const cfg of CONFIGS) {
    let diff = 0;
    if (!sizeChanged) {
      diff = pixelmatch(a.data, b.data, null, a.width, a.height, {
        threshold: cfg.threshold,
      });
    }
    results[cfg.key][c.name] = {
      failed: sizeChanged || diff > 0,
      diff_pixels: diff,
      total_pixels: a.width * a.height,
      size_changed: sizeChanged,
    };
  }
}

process.stdout.write(JSON.stringify({
  source: 'native',
  tool: 'pixelmatch (npm)',
  configs: CONFIGS,
  results,
}, null, 2) + '\n');
