/* Интерфейс обязан ДОЙТИ до первого экрана.

   Всё остальное про фронт проверяется чтением файлов: что подключено то, что
   лежит, что имена не сталкиваются, что селектор находит узел. Ни одна из этих
   проверок не отвечает на единственный вопрос, который задаёт человек,
   открывший адрес: нарисовалось ли хоть что-нибудь.

   Стоило это дорого. `applyTeam()` писал имя команды в `.brand .sub` — узла с
   таким классом в разметке не было. Пока имя пустое, ветка не выполнялась, и
   полгода всё выглядело исправным. В тот день, когда администратор заполнил
   «Team profile», функция упала на `null`; зовут её из запуска ДО навигации,
   и исключение унесло с собой первую отрисовку целиком. Вся команда увидела
   «Loading…» — и по виду это была поломка сервиса, а не одна строка во фронте.

   Здесь страница поднимается в jsdom, сервис отвечает правдоподобно, и
   проверяется ровно три вещи: при запуске не вылетело ни одной ошибки, `#screen`
   не остался на «Loading…», имя команды доехало до шапки. Без браузера, за
   полсекунды.

   Запуск: node tests/ui/boot_smoke.mjs <путь к frontend> */
import fs from 'node:fs';
import path from 'node:path';
import {JSDOM} from 'jsdom';

const FRONT = process.argv[2]
  || path.join(path.dirname(new URL(import.meta.url).pathname), '..', '..', 'frontend');
const html = fs.readFileSync(path.join(FRONT, 'index.html'), 'utf8');
const listed = [...html.matchAll(/<script src="js\/([^"]+)"><\/script>/g)].map(m => m[1]);

/* Ответы сервиса — как у команды, которая заполнила своё имя. Пустой профиль
   не воспроизвёл бы ту самую поломку: она ждала первого непустого имени. */
const API = {
  '/api/auth/state': {auth: 'on', local_mode: false, needs_setup: false,
                      registration: 'closed'},
  '/api/auth/me': {login: 'anna', name: 'Анна', role: 'admin', grants: {},
                   own_project: 'demo'},
  '/api/team': {name: 'Team shop-web', brand_color: '#C2410C'},
  '/api/runs/counts': {all: 3, unreviewed: 1, fail: 1, error: 0, new: 0, clean: 2},
  '/api/runs': [{id: 1, run_key: 'demo#1', branch: 'main', git_sha: 'abc1234',
                 failed: 1, total: 5, started_at: new Date().toISOString(),
                 finished_at: new Date().toISOString()}],
  '/api/run-projects': [{name: 'demo', runs: 3, last_run: new Date().toISOString()}],
  '/api/metrics/summary': {totals: {}},
  '/api/baselines/scopes': {scopes: []},
  '/api/projects': {projects: []},
  '/api/tests': {tests: [], project_tests: []},
  '/api/users/pending': {pending: []},
  '/api/doctor/env': {python: '3.12', platform_key: 'linux-chromium-1x'},
  '/api/doctor/last': null,
  '/api/presence': {count: 1, online: []},
};
const answer = p => {
  const key = Object.keys(API).find(k => p === k || p.startsWith(k + '?'));
  return key ? API[key] : {};
};

const dom = new JSDOM(html, {url: 'http://localhost:8420/ui/#/runs',
                             runScripts: 'outside-only', pretendToBeVisual: true});
const w = dom.window;
const errors = [];
w.addEventListener('error',
  e => errors.push('error: ' + ((e.error && e.error.message) || e.message)));
w.addEventListener('unhandledrejection',
  e => errors.push('rejection: ' + ((e.reason && e.reason.message) || e.reason)));
w.fetch = async url => {
  const body = answer(String(url).replace('http://localhost:8420', ''));
  return {ok: true, status: 200, headers: {get: () => 'application/json'},
          json: async () => body, text: async () => JSON.stringify(body),
          clone() { return this; }};
};
w.matchMedia = w.matchMedia
  || (() => ({matches: false, addListener() {}, removeListener() {}}));

/* Классические <script> делят одну область видимости, в том числе для `const`
   и `let` верхнего уровня. `eval` на файл этого не воспроизводит — склеиваем,
   как их видит браузер. */
const bundle = listed
  .map(f => `//# ${f}\n` + fs.readFileSync(path.join(FRONT, 'js', f), 'utf8'))
  .join('\n;\n');
try { w.eval(bundle); } catch (e) { errors.push('запуск: ' + e.message); }

await new Promise(r => setTimeout(r, 600));

const text = ((w.document.querySelector('#screen') || {}).textContent || '').trim();
const stuck = /^Loading…?$/.test(text);
const team = (w.document.querySelector('.brand .sub') || {}).textContent || '';

const problems = [];
if (errors.length) problems.push('ошибки при запуске:\n  ' + errors.join('\n  '));
if (stuck) problems.push('#screen остался на «Loading…» — первой отрисовки не было');
if (!team.trim()) problems.push('имя команды не доехало до шапки');

/* Выход обязан быть явным. Интерфейс живой: `startCollab()` заводит
   `setInterval` на присутствие и на живое обновление списка, и node с ними
   не завершится никогда — проверка не упала бы, а зависла, что в CI выглядит
   куда хуже честного провала. */
dom.window.close();
if (problems.length) { console.error(problems.join('\n')); process.exit(1); }
console.log('ok · экран: ' + text.slice(0, 60).replace(/\s+/g, ' ')
            + ' · команда: ' + team);
process.exit(0);
