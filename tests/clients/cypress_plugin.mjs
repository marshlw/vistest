/* VisTest - self-hosted visual regression testing.
 * Copyright (C) 2026 Kirill Kulagin
 * SPDX-License-Identifier: AGPL-3.0-or-later
 */

/**
 * Плагин Cypress: на какие снимки он реагирует и что отправляет.
 *
 * Cypress сюда не ставится: `setupNodeEvents` — это функция, которой передают
 * `on`, а `on` всего лишь запоминает обработчики. Вызвать их можно самому, и
 * проверяется ровно то, что ломается тихо: что снимок падения не уходит как
 * результат, что прошедший — уходит, и что прогон закрывается один раз.
 *
 * Запуск: node tests/clients/cypress_plugin.mjs <clients-dir>
 */

import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const clients = process.argv[2] || 'clients';
const { default: vistest } =
  await import(pathToFileURL(path.resolve(clients, 'cypress.mjs')));

const tmp = await fs.mkdtemp(path.join(os.tmpdir(), 'vistest-cypress-'));
const shot = async (name) => {
  const p = path.join(tmp, name);
  await fs.writeFile(p, Buffer.from([0x89, 0x50, 0x4e, 0x47]));
  return p;
};

let calls = [];
globalThis.fetch = async (url, init = {}) => {
  calls.push({ path: new URL(String(url)).pathname,
               headers: init.headers || {} });
  return {
    ok: true, status: 200,
    async json() {
      return { name: 'x.png', verdict: 'pass', metrics: {}, regions: [],
               artifacts: {}, platform: 'linux-1x' };
    },
    async text() { return ''; },
  };
};

function harness(opts = {}) {
  const handlers = {};
  const on = (event, fn) => { handlers[event] = fn; };
  const config = { browser: { name: 'electron' } };
  const client = vistest(on, config, { apiUrl: 'http://x', project: 'shop',
                                       token: 'tok', runKey: 'ci-9', ...opts });
  return { handlers, client };
}

/* ------------------------------------------------------------------ */
/*  Прошедший снимок — тоже результат. В этом весь смысл плагина.       */
/* ------------------------------------------------------------------ */
{
  calls = [];
  const { handlers, client } = harness();
  await handlers['after:screenshot']({
    path: await shot('login.png'), name: 'login', testFailure: false,
  });

  assert.equal(client.results.length, 1,
    'у Playwright зелёный прогон не оставляет ничего — здесь оставляет');
  assert.deepEqual(calls.map(c => c.path), ['/api/check']);
  assert.equal(calls[0].headers['X-VisTest-Token'], 'tok');
}

/* ------------------------------------------------------------------ */
/*  Скриншот падения Cypress делает сам — это не сравнение             */
/* ------------------------------------------------------------------ */
{
  calls = [];
  const { handlers, client } = harness();
  await handlers['after:screenshot']({
    path: await shot('crash.png'), testFailure: true,
  });

  assert.equal(calls.length, 0,
    'сравнить случайный кадр с эталоном значит показать регресс на ровном месте');
  assert.equal(client.results.length, 0);
}

/* ------------------------------------------------------------------ */
/*  Имя: своё, если названо; иначе из файла                            */
/* ------------------------------------------------------------------ */
{
  calls = [];
  const { handlers } = harness({
    name: (d) => `checkout/${d.name}.png`,
  });
  await handlers['after:screenshot']({
    path: await shot('a.png'), name: 'cart', testFailure: false,
  });
  assert.deepEqual(calls.map(c => c.path), ['/api/check']);

  calls = [];
  const plain = harness();
  await plain.handlers['after:screenshot']({
    path: await shot('Login -- opens.png'), testFailure: false,
  });
  assert.equal(plain.client.results.length, 1, 'без имени берём хвост пути');
}

/* ------------------------------------------------------------------ */
/*  Ненужное отсеивается до отправки                                   */
/* ------------------------------------------------------------------ */
{
  calls = [];
  const { handlers } = harness({ ignore: /^tmp-/ });
  await handlers['after:screenshot']({
    path: await shot('tmp-debug.png'), name: 'tmp-debug', testFailure: false,
  });
  assert.equal(calls.length, 0);
}

/* ------------------------------------------------------------------ */
/*  Прогон закрывается один раз, в конце                               */
/* ------------------------------------------------------------------ */
{
  calls = [];
  const { handlers } = harness();
  await handlers['after:screenshot']({
    path: await shot('one.png'), name: 'one', testFailure: false });
  await handlers['after:screenshot']({
    path: await shot('two.png'), name: 'two', testFailure: false });
  await handlers['after:run']({});

  assert.deepEqual(calls.map(c => c.path),
    ['/api/check', '/api/check', '/api/runs'],
    'две проверки и один прогон, а не три записи истории');
}

/* ------------------------------------------------------------------ */
/*  Пустой прогон в историю не пишется                                 */
/* ------------------------------------------------------------------ */
{
  calls = [];
  const { handlers } = harness();
  await handlers['after:run']({});
  assert.equal(calls.length, 0, 'нечего записывать — не ходим в сервис');
}

await fs.rm(tmp, { recursive: true, force: true });
console.log('OK');
