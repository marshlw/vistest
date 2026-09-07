/* VisTest - self-hosted visual regression testing.
 * Copyright (C) 2026 Kirill Kulagin
 * SPDX-License-Identifier: AGPL-3.0-or-later
 *
 * This file is part of VisTest. See LICENSE for the full terms and NOTICE for
 * the trademark and commercial-licensing terms. Removing this header does not
 * remove those obligations.
 */

/* Подключение чужого набора: профиль, правила имён, разбор готовых артефактов.

   Всё это — поля, которые человек заполняет один раз и на которые потом
   опирается каждый прогон. Ошибка здесь тихая по определению: форма
   сохранится, тост скажет «Saved», а в описание проекта уедет не то или не
   уедет ничего — и выяснится это через день, на прогоне, который «почему-то
   не находит пар».

   Поэтому проверяется не наличие полей, а то, что реально отправлено: тело
   PATCH и тело запроса на разбор. Заодно — что подпись кнопки зависит от
   раннера: «Install missing libraries» над чужим `npm ci` не мелкая
   неточность, а обещание поставить недостающее в НАШЕ окружение вместо ИХ.

   Запуск: node tests/ui/project_suite_view.mjs <путь к frontend> */
import fs from 'node:fs';
import path from 'node:path';
import {JSDOM} from 'jsdom';

const FRONT = process.argv[2]
  || path.join(path.dirname(new URL(import.meta.url).pathname), '..', '..', 'frontend');
const html = fs.readFileSync(path.join(FRONT, 'index.html'), 'utf8');
const listed = [...html.matchAll(/<script src="js\/([^"]+)"><\/script>/g)].map(m => m[1]);

const sent = [];
const ANSWERS = {
  '/api/suites': {suites: [
    {id: 'playwright', title: 'Playwright Test (TypeScript / JavaScript)',
     runner: 'command', command: ['npx', 'playwright', 'test'],
     note: 'a green run leaves no artifacts at all'},
    {id: 'cypress', title: 'Cypress', runner: 'command', command: [],
     note: ''},
  ]},
};

const dom = new JSDOM(html, {url: 'http://localhost:8420/ui/#/projects',
                             runScripts: 'outside-only'});
const w = dom.window;
w.fetch = async (url, opts = {}) => {
  const p = String(url).replace('http://localhost:8420', '');
  sent.push({path: p, method: (opts.method || 'GET'),
             body: opts.body ? JSON.parse(opts.body) : null});
  const body = ANSWERS[p] || {ok: true, job_id: 'job1'};
  return {ok: true, status: 200, headers: {get: () => 'application/json'},
          json: async () => body, text: async () => JSON.stringify(body),
          clone() { return this; }};
};
w.confirm = () => true;

const bundle = listed
  .map(f => fs.readFileSync(path.join(FRONT, 'js', f), 'utf8')).join('\n;\n');
w.eval(bundle);

const problems = [];
const check = (what, ok) => { if (!ok) problems.push(what); };
const rest = (ms = 80) => new Promise(r => setTimeout(r, ms));
const flat = s => String(s || '').replace(/\s+/g, ' ').trim();

await rest(300);

/* Подключённый набор на Playwright: не pytest, профиль определён сервисом. */
const project = {
  key: 'web', name: 'Web e2e', root: '/srv/web-e2e', mode: 'observe',
  runner: 'command', command: ['npx', 'playwright', 'test'],
  pytest_args: [], env: {}, tests: '',
  suite: 'auto', suite_resolved: 'playwright',
  suite_title: 'Playwright Test (TypeScript / JavaScript)',
  suite_note: 'a green run leaves no artifacts at all',
  search_dirs: ['test-results'], naming: {}, keep_dir: false,
  browser_control: 'profile', browser_refusal: '',
  baseline_counts: {}, baseline_status: {vistest: [], vistest_total: 0},
};

/* ------------------------------------------------------------------ */
/*  Форма настроек: что уедет в описание проекта                       */
/* ------------------------------------------------------------------ */
w.projectEnvModal(project);
await rest(120);

const modal = w.document.querySelector('#modalBg');
check('форма настроек открылась', !!modal);

const selects = [...(modal ? modal.querySelectorAll('select') : [])];
const suite = selects.find(s => [...s.options].some(o => o.value === 'auto'));
check('в форме есть выбор инструмента', !!suite);
check('список профилей пришёл с сервиса',
      !!suite && [...suite.options].some(o => o.value === 'playwright'));
check('сервис спрошен о профилях один раз',
      sent.filter(r => r.path === '/api/suites').length === 1);
check('видно, чем набор опознан',
      flat(modal && modal.textContent).includes('Playwright Test'));

const areas = [...(modal ? modal.querySelectorAll('textarea') : [])];
const dirs = areas.find(a => (a.placeholder || '').includes('test-results'));
check('есть поле каталогов с картинками', !!dirs);
check('каталоги показаны такими, какие есть',
      !!dirs && dirs.value.trim() === 'test-results');

const inputs = [...(modal ? modal.querySelectorAll('input.inp') : [])];
const actual = inputs.find(i => (i.placeholder || '').includes('-actual'));
check('есть поле правила для факта', !!actual);
check('группа имени показана в подсказке правила',
      !!actual && /\(\?P<name>/.test(actual.placeholder));

if (suite && dirs && actual) {
  suite.value = 'cypress';
  dirs.value = 'test-results\n  cypress/snapshots  \n\n';
  actual.value = '(?P<name>.+)-shot\\.png';
}

const save = [...(modal ? modal.querySelectorAll('button') : [])]
  .find(b => flat(b.textContent) === 'Save');
check('в форме есть «Save»', !!save);
sent.length = 0;
if (save) save.dispatchEvent(new w.MouseEvent('click', {bubbles: true}));
await rest(150);

const patch = sent.find(r => r.method === 'PATCH');
check('форма сходила в сервис', !!patch);
if (patch) {
  check(`профиль уехал выбранный, а не показанный (${patch.body.suite})`,
        patch.body.suite === 'cypress');
  check('каталоги очищены от пустых строк и пробелов',
        JSON.stringify(patch.body.search_dirs)
          === JSON.stringify(['test-results', 'cypress/snapshots']));
  check('правило имени уехало',
        patch.body.naming && patch.body.naming.actual === '(?P<name>.+)-shot\\.png');
  check('пустые правила не отправляются пустыми строками',
        patch.body.naming && !('expected' in patch.body.naming));
  check('раннер не потерялся по дороге', patch.body.runner === 'command');
}
w.closeModal();

/* ------------------------------------------------------------------ */
/*  Разбор готовых артефактов                                          */
/* ------------------------------------------------------------------ */
sent.length = 0;
w.ingestModal(project);
await rest(80);

const ing = w.document.querySelector('#modalBg');
check('форма разбора открылась', !!ing);
check('сказано, что ничего не запускается',
      flat(ing && ing.textContent).includes('Nothing is started here'));

const ingDirs = ing && ing.querySelector('textarea');
const ingBrowser = ing && ing.querySelector('select');
check('каталоги подставлены из профиля проекта',
      !!ingDirs && ingDirs.value.trim() === 'test-results');
check('браузер по умолчанию не назначен',
      !!ingBrowser && ingBrowser.value === '');
if (ingBrowser) ingBrowser.value = 'firefox';

const read = [...(ing ? ing.querySelectorAll('button') : [])]
  .find(b => flat(b.textContent) === 'Read');
check('в форме разбора есть «Read»', !!read);
if (read) read.dispatchEvent(new w.MouseEvent('click', {bubbles: true}));
await rest(150);

const ingest = sent.find(r => r.path.endsWith('/ingest'));
check('разбор ушёл на свой роут, а не на прогон', !!ingest);
if (ingest) {
  check('каталоги доехали',
        JSON.stringify(ingest.body.dirs) === JSON.stringify(['test-results']));
  check(`браузер доехал (${ingest.body.browser})`,
        ingest.body.browser === 'firefox');
}
w.closeModal();

/* ------------------------------------------------------------------ */
/*  Подпись кнопки зависит от того, чем набор гоняется                 */
/* ------------------------------------------------------------------ */
const label = (p) => {
  const card = w.projectCard(p);
  const acts = card.querySelector('.acts');
  const b = [...(acts ? acts.querySelectorAll('button') : [])]
    .find(x => /Install/.test(flat(x.textContent)));
  return b ? flat(b.textContent) : '';
};

check('у чужой команды кнопка про ИХ зависимости',
      label(project) === 'Install project dependencies');
check('у pytest-набора подпись прежняя',
      label({...project, runner: 'pytest', suite_resolved: 'pytest'})
        === 'Install missing libraries');

/* ------------------------------------------------------------------ */
/*  Пункт меню, которым разбор и запускается                           */
/* ------------------------------------------------------------------ */
const card = w.projectCard(project);
w.document.body.append(card);
const more = [...card.querySelectorAll('.acts button')]
  .find(b => flat(b.textContent) === '⋯');
check('на карточке есть «⋯»', !!more);
if (more) {
  more.getBoundingClientRect = () => ({top: 100, bottom: 126, left: 900,
                                       right: 980, width: 80, height: 26,
                                       x: 900, y: 100});
  more.dispatchEvent(new w.MouseEvent('click', {bubbles: true}));
  await rest();
  const menu = w.document.querySelector('.menu');
  check('меню открылось', !!menu);
  check('в меню есть разбор готовых артефактов',
        !!menu && [...menu.querySelectorAll('button')]
          .some(b => flat(b.textContent) === 'Read ready artifacts…'));
  w.closeMenus();
}

dom.window.close();
if (problems.length) {
  console.error(problems.join('\n'));
  process.exit(1);
}
console.log('ok · профиль и правила имён уезжают в описание, разбор ходит на свой роут');
process.exit(0);
