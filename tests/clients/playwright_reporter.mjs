/* VisTest - self-hosted visual regression testing.
 * Copyright (C) 2026 Kirill Kulagin
 * SPDX-License-Identifier: AGPL-3.0-or-later
 */

/**
 * Репортёр Playwright: что он на самом деле отправляет.
 *
 * Playwright сюда не ставится и не нужен: репортёр — это объект с методами
 * `onTestEnd` и `onEnd`, и вызвать их можно самому. Проверяется ровно то, что
 * ломается молча: какие вложения он считает результатом, каким именем их
 * называет и в каком порядке ходит в сервис.
 *
 * Запуск: node tests/clients/playwright_reporter.mjs <clients-dir>
 */

import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const clients = process.argv[2] || 'clients';
const { default: VisTestReporter } =
  await import(pathToFileURL(path.resolve(clients, 'playwright-reporter.mjs')));

/* ------------------------------------------------------------------ */
const tmp = await fs.mkdtemp(path.join(os.tmpdir(), 'vistest-reporter-'));
const file = async (name) => {
  const p = path.join(tmp, name);
  await fs.writeFile(p, Buffer.from([0x89, 0x50, 0x4e, 0x47]));
  return p;
};

const calls = [];
function stubFetch({ seedStatus = 200 } = {}) {
  globalThis.fetch = async (url, init = {}) => {
    calls.push({ url: String(url), headers: init.headers || {} });
    if (String(url).endsWith('/api/baselines/seed')) {
      return { ok: seedStatus === 200, status: seedStatus,
               async text() { return 'exists'; },
               async json() { return { ok: true }; } };
    }
    if (String(url).endsWith('/api/check')) {
      return { ok: true, status: 200,
               async json() {
                 return { name: 'login.png', verdict: 'fail', metrics: {},
                          regions: [], artifacts: {}, platform: 'linux-1x' };
               } };
    }
    return { ok: true, status: 200, async json() { return { id: 7 }; } };
  };
}

const testCase = (specFile = '/repo/tests/checkout.spec.ts') => ({
  location: { file: specFile },
  parent: { project: () => ({ name: 'firefox', use: { browserName: 'firefox' } }) },
});

/* ------------------------------------------------------------------ */
/*  Вложения: результатом считается ФАКТ, а не всё подряд               */
/* ------------------------------------------------------------------ */
{
  const r = new VisTestReporter({ apiUrl: 'http://x', project: 'shop' });
  r.onTestEnd(testCase(), {
    attachments: [
      { name: 'login-expected.png', path: await file('e.png') },
      { name: 'login-actual.png', path: await file('a.png') },
      { name: 'login-diff.png', path: await file('d.png') },
      // Скриншот падения, который Playwright прикладывает сам: это не
      // сравнение, и отправить его как результат значило бы сравнивать
      // случайную картинку с эталоном.
      { name: 'screenshot', path: await file('s.png') },
      // Трасса: вообще не картинка.
      { name: 'trace', path: await file('t.zip') },
    ],
  });

  assert.equal(r.pending.length, 1, 'один снимок, а не пять вложений');
  assert.equal(r.pending[0].name, 'login.png');
  assert.equal(r.pending[0].browser, 'firefox', 'браузер — часть ключа платформы');
  assert.ok(r.pending[0].expected, 'их эталон приехал вместе с фактом');
}

/* ------------------------------------------------------------------ */
/*  Без факта отправлять нечего                                        */
/* ------------------------------------------------------------------ */
{
  const r = new VisTestReporter({ apiUrl: 'http://x', project: 'shop' });
  r.onTestEnd(testCase(), {
    attachments: [{ name: 'login-expected.png', path: await file('e2.png') }],
  });
  assert.equal(r.pending.length, 0);
}

/* ------------------------------------------------------------------ */
/*  Имя снимка по умолчанию не меняет форму                            */
/* ------------------------------------------------------------------ */
{
  const plain = new VisTestReporter({ apiUrl: 'http://x' });
  assert.equal(plain.snapshotName(testCase(), 'login'), 'login.png');

  const prefixed = new VisTestReporter({ apiUrl: 'http://x', prefixWithSpec: true });
  assert.equal(prefixed.snapshotName(testCase(), 'login'),
               'checkout.spec/login.png',
               'спека в имени — лечение коллизии, а не поведение по умолчанию');
}

/* ------------------------------------------------------------------ */
/*  Порядок вызовов: сначала посеять эталон, потом сравнить            */
/* ------------------------------------------------------------------ */
{
  calls.length = 0;
  stubFetch();
  const r = new VisTestReporter({ apiUrl: 'http://x', project: 'shop',
                                  token: 'tok', runKey: 'ci-1' });
  r.onTestEnd(testCase(), {
    attachments: [
      { name: 'login-expected.png', path: await file('e3.png') },
      { name: 'login-actual.png', path: await file('a3.png') },
    ],
  });
  await r.onEnd();

  const paths = calls.map(c => new URL(c.url).pathname);
  assert.deepEqual(paths, ['/api/baselines/seed', '/api/check', '/api/runs'],
    'эталон, затем сравнение, затем один прогон в истории');
  for (const c of calls) {
    assert.equal(c.headers['X-VisTest-Token'], 'tok',
      'токен обязан ехать в каждом запросе: без него общая инсталляция ответит 401');
  }
}

/* ------------------------------------------------------------------ */
/*  Эталон уже есть — 409 штатный ответ, а не ошибка                   */
/* ------------------------------------------------------------------ */
{
  calls.length = 0;
  stubFetch({ seedStatus: 409 });
  const r = new VisTestReporter({ apiUrl: 'http://x', project: 'shop',
                                  runKey: 'ci-2' });
  r.onTestEnd(testCase(), {
    attachments: [
      { name: 'login-expected.png', path: await file('e4.png') },
      { name: 'login-actual.png', path: await file('a4.png') },
    ],
  });
  await r.onEnd();

  assert.equal(r.problems.length, 0,
    'перезаписывать наш эталон их картинкой нельзя, и отказ — не ошибка');
  assert.ok(calls.some(c => c.url.endsWith('/api/check')),
    'сравнение всё равно состоялось');
}

/* ------------------------------------------------------------------ */
/*  Зелёный прогон Playwright не оставляет ничего                      */
/* ------------------------------------------------------------------ */
{
  calls.length = 0;
  stubFetch();
  const r = new VisTestReporter({ apiUrl: 'http://x', project: 'shop' });
  r.onTestEnd(testCase(), { attachments: [] });
  await r.onEnd();

  assert.equal(calls.length, 0, 'нечего отправлять — не ходим в сервис');
}

await fs.rm(tmp, { recursive: true, force: true });
console.log('OK');
