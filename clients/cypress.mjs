/* VisTest - self-hosted visual regression testing.
 * Copyright (C) 2026 Kirill Kulagin
 * SPDX-License-Identifier: AGPL-3.0-or-later
 *
 * This file is part of VisTest. See LICENSE for the full terms and NOTICE for
 * the trademark and commercial-licensing terms. Removing this header does not
 * remove those obligations.
 */

/**
 * Плагин Cypress: точка, предусмотренная самим Cypress.
 *
 *   // cypress.config.js
 *   import { defineConfig } from 'cypress';
 *   import vistest from 'vistest-client/cypress';
 *
 *   export default defineConfig({
 *     e2e: {
 *       setupNodeEvents(on, config) {
 *         vistest(on, config, { project: 'web-e2e' });
 *         return config;
 *       },
 *     },
 *   });
 *
 * Чем это лучше репортёра Playwright — и это единственная причина писать
 * отдельный плагин, а не переиспользовать тот. `after:screenshot` срабатывает
 * на КАЖДЫЙ снимок: и на прошедший, и на упавший. У Playwright вложения
 * появляются только при падении, то есть зелёный прогон не оставляет ничего
 * для разбора, и «прошедший снимок — тоже результат» там не выполняется. Здесь
 * выполняется, и это ближе всего к тому, что делает pytest-адаптер.
 *
 * Плагин живёт в НОДОВОЙ части Cypress, а не в браузере. Иначе никак: из
 * браузера сервис за CORS и токеном не достать, а файл снимка ему не виден
 * вовсе.
 *
 * Работает и просто на `cy.screenshot()`, без плагинов сравнения: сравнивать
 * будет VisTest, у него для этого всё и есть.
 */

import { VisTest } from './vistest.mjs';

/**
 * @param {Function} on      `on` из setupNodeEvents
 * @param {object}   config  конфигурация Cypress (из неё берётся браузер)
 * @param {object}   [opts]  apiUrl, project, token, runKey, ignore, name
 * @returns {VisTest} клиент — на случай, если он нужен вызывающему
 */
export default function vistest(on, config = {}, opts = {}) {
  const client = new VisTest({
    apiUrl: opts.apiUrl,
    project: opts.project,
    token: opts.token,
    runKey: opts.runKey || process.env.VISTEST_RUN_ID
      || process.env.CI_JOB_ID || process.env.GITHUB_RUN_ID,
    // Падение теста — их дело: снимок уже сделан, тест уже покрашен. Наш
    // вердикт живёт в VisTest, и ронять чужой прогон из обработчика события
    // значило бы менять его результат задним числом.
    throwOnFail: false,
  });

  const ignore = opts.ignore instanceof RegExp ? opts.ignore : null;
  const problems = [];

  on('after:screenshot', async (details) => {
    // Скриншот падения Cypress делает сам, и называется он «имя теста
    // (failed)». Это не сравнение: отправить его как результат значило бы
    // сравнивать случайный кадр с эталоном и показать регресс на ровном месте.
    if (details.testFailure) return details;

    const name = (opts.name ? opts.name(details) : defaultName(details));
    if (!name || (ignore && ignore.test(name))) return details;

    try {
      const fs = await import('node:fs/promises');
      await client.check(name, await fs.readFile(details.path), {
        browser: browserOf(config, details),
      });
    } catch (e) {
      // Ошибка отправки не должна ронять их прогон — но и молчать о ней
      // нельзя: «в VisTest пусто» без единой строки в логе ищется днями.
      problems.push(`${name}: ${e.message || e}`);
    }
    return details;
  });

  on('after:run', async () => {
    try {
      if (client.runKey && client.results.length) await client.finish();
    } catch (e) {
      problems.push(`run not recorded: ${e.message || e}`);
    }
    const s = client.summary();
    // eslint-disable-next-line no-console
    console.log(`\n[vistest] ${s.total} snapshot(s) sent, ${s.failed} judged a `
      + `regression, ${s.created} recorded as new`
      + (client.runKey ? ` · run ${client.runKey}` : ''));
    for (const p of problems) console.log(`[vistest] ${p}`);
  });

  return client;
}

/**
 * Имя снимка из того, что дал Cypress.
 *
 * `details.name` есть только у именованного `cy.screenshot('login')`. Без
 * имени Cypress составляет путь из заголовков теста, и брать оттуда хвост
 * файла — единственное, что не зависит от их структуры каталогов.
 */
function defaultName(details) {
  const base = details.name
    || (details.path || '').split(/[\\/]/).pop().replace(/\.png$/i, '');
  return base ? `${String(base).replace(/\.png$/i, '')}.png` : '';
}

function browserOf(config, details) {
  return (details && details.browserName)
    || (config && config.browser && config.browser.name)
    || 'chromium';
}
