/* VisTest - self-hosted visual regression testing.
 * Copyright (C) 2026 Kirill Kulagin
 * SPDX-License-Identifier: AGPL-3.0-or-later
 *
 * This file is part of VisTest. See LICENSE for the full terms and NOTICE for
 * the trademark and commercial-licensing terms. Removing this header does not
 * remove those obligations.
 */

/**
 * Репортёр Playwright: ноль правок в их тестах.
 *
 *   // playwright.config.ts
 *   reporters: [['list'], ['vistest-client/playwright-reporter',
 *                          { apiUrl: process.env.VISTEST_API_URL,
 *                            project: 'web-e2e' }]]
 *
 * Почему это работает без единой строки в тестах. При падении
 * `toHaveScreenshot()` Playwright сам прикладывает к результату теста три
 * вложения — `<имя>-expected.png`, `<имя>-actual.png`, `<имя>-diff.png`.
 * Репортёр их забирает и отправляет в VisTest, который считает вердикт своим
 * движком: регионы, классы изменений, severity, история, ревью с апрувом.
 *
 * ЧЕГО ЭТОТ ПУТЬ НЕ ДАЁТ, и об этом надо говорить вслух: вложения появляются
 * ТОЛЬКО при падении. Снимок, который прошёл, в отчёт не попадает вовсе, и
 * узнать о нём отсюда нельзя ничем. «Прошедший снимок — тоже результат» —
 * наша собственная формулировка, и здесь она не выполняется. Если нужен
 * каждый снимок, вызов клиента в тесте (`vt.checkPage`) — единственный
 * способ; репортёр честно закрывает случай «переписывать тесты никто не
 * будет».
 *
 * Первый эталон берётся из их же `expected`. Иначе первый прогон отвечал бы
 * «новый эталон» на каждый снимок и не сравнивал бы ничего — то есть день
 * первый выглядел бы как «инструмент не работает». Отключается опцией
 * `seedBaselines: false`.
 */

import { VisTest } from './vistest.mjs';

const KIND = /^(?<base>.+?)-(?<kind>expected|actual|diff)\.png$/;

export default class VisTestReporter {
  constructor(options = {}) {
    this.options = options;
    this.client = new VisTest({
      apiUrl: options.apiUrl,
      project: options.project,
      token: options.token,
      runKey: options.runKey || process.env.VISTEST_RUN_ID
        || process.env.CI_JOB_ID || process.env.GITHUB_RUN_ID,
      // Репортёр не имеет права ронять их прогон: он читает результат, а не
      // выносит его. Вердикт живёт в VisTest, а падение теста уже случилось.
      throwOnFail: false,
    });
    this.seedBaselines = options.seedBaselines !== false;
    /** @type {{name:string, actual:string, expected:string|null, browser:string}[]} */
    this.pending = [];
    this.problems = [];
  }

  printsToStdio() {
    return false;
  }

  onTestEnd(test, result) {
    const browser = browserOf(test);
    const groups = new Map();
    for (const a of result.attachments || []) {
      const m = KIND.exec(a.name || '');
      if (!m || !a.path) continue;
      const g = groups.get(m.groups.base) || {};
      g[m.groups.kind] = a.path;
      groups.set(m.groups.base, g);
    }
    for (const [base, files] of groups) {
      if (!files.actual) continue;          // сравнивать нечего
      this.pending.push({
        name: this.snapshotName(test, base),
        actual: files.actual,
        expected: files.expected || null,
        browser,
      });
    }
  }

  /**
   * Как снимок называется в VisTest.
   *
   * По умолчанию — так же, как у них. Спека в имени выключена намеренно: имя
   * снимка — это ключ истории, и менять его форму значило бы разорвать
   * прошлое снимка надвое. Включать `prefixWithSpec`, если два теста законно
   * держат по своему `login.png`.
   */
  snapshotName(test, base) {
    const clean = String(base).replace(/\.png$/i, '');
    if (!this.options.prefixWithSpec) return `${clean}.png`;
    const file = (test.location && test.location.file) || '';
    const spec = file.split(/[\\/]/).pop().replace(/\.[^.]+$/, '');
    return `${spec}/${clean}.png`;
  }

  async onEnd() {
    if (!this.pending.length) return;
    const fs = await import('node:fs/promises');

    for (const item of this.pending) {
      try {
        if (this.seedBaselines && item.expected) {
          await this.seed(item, fs);
        }
        await this.client.check(item.name, await fs.readFile(item.actual),
                                { browser: item.browser });
      } catch (e) {
        this.problems.push(`${item.name}: ${e.message || e}`);
      }
    }

    try {
      if (this.client.runKey) await this.client.finish();
    } catch (e) {
      this.problems.push(`run not recorded: ${e.message || e}`);
    }

    const s = this.client.summary();
    // eslint-disable-next-line no-console
    console.log(`\n[vistest] ${s.total} snapshot(s) sent, ${s.failed} judged a `
      + `regression, ${s.created} recorded as new`
      + (this.client.runKey ? ` · run ${this.client.runKey}` : ''));
    for (const p of this.problems) console.log(`[vistest] ${p}`);
    if (!this.pending.length) {
      console.log('[vistest] Playwright attaches the pictures only for a '
        + 'FAILED comparison — a green run leaves nothing to review.');
    }
  }

  /** Записать их `expected` как эталон, если у нас его ещё нет. */
  async seed(item, fs) {
    const form = new FormData();
    form.append('name', item.name);
    form.append('project', this.client.project);
    form.append('browser', item.browser);
    form.append('image', new Blob([await fs.readFile(item.expected)],
      { type: 'image/png' }), 'expected.png');
    const url = `${this.client.apiUrl}/api/baselines/seed`;
    const res = await fetch(url, {
      method: 'POST', body: form, headers: this.client.headers(),
    });
    // 409 — эталон уже есть, и это штатный ответ, а не ошибка: перезаписывать
    // наш эталон их картинкой на каждом падении значило бы принимать регресс
    // автоматически.
    if (!res.ok && res.status !== 409) {
      throw new Error(`seeding the baseline: ${res.status} ${await res.text()}`);
    }
  }
}

function browserOf(test) {
  try {
    const project = test.parent && test.parent.project && test.parent.project();
    return (project && ((project.use && project.use.browserName) || project.name))
      || 'chromium';
  } catch {
    return 'chromium';
  }
}
