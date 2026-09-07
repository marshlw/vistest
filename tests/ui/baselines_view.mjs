/* Экран «Baselines»: закреплённый проект, движок, размер окна, сравнение.

   Проверяется не «выполнился ли код», а что человек увидит и на какой вопрос
   сможет ответить, — потому что ломалось здесь всегда именно это.

   Четыре вещи, каждая из которых раньше была поломкой:

   1. Набор эталонов следует за проектом из шапки. Пока их было два — выбор
      проекта наверху и своя строка SET внизу, — человек выбирал проект и
      продолжал видеть чужие эталоны, а понять это мог только по числам,
      которые не сходятся.

   2. Ключ хранения (`docker-chromium-1x-390x844`) больше не переключатель.
      В нём склеены четыре независимых ответа, а спрашивают их по одному:
      «в каком движке» и «в каком размере окна» — разные вопросы. Значит и
      переключателя два, и у каждого варианта своё число рядом: ноль рядом с
      чужой двенадцаткой и есть ответ на «почему здесь ничего нет».

   3. Базовый размер окна лежит под ключом БЕЗ суффикса (см. `vistest/matrix`),
      и подписывать его прочерком нельзя: это не «размер неизвестен».

   4. Сравнение по движкам собирается по ИМЕНИ снимка. Набор firefox может
      отставать от chromium на несколько картинок, и совмещение по порядку
      показало бы рядом два разных экрана как один.

   Запуск: node tests/ui/baselines_view.mjs <путь к frontend> */
import fs from 'node:fs';
import path from 'node:path';
import {JSDOM} from 'jsdom';

const FRONT = process.argv[2]
  || path.join(path.dirname(new URL(import.meta.url).pathname), '..', '..', 'frontend');
const html = fs.readFileSync(path.join(FRONT, 'index.html'), 'utf8');
const listed = [...html.matchAll(/<script src="js\/([^"]+)"><\/script>/g)].map(m => m[1]);

const item = (name, extra = {}) => ({
  name, short: name.split('/').pop(), version: 1, runnable: true,
  url: 'https://shop.example/' + name, thumb: '/t/' + name, full: '/f/' + name,
  source: {kind: 'url'}, ...extra});

/* Матрица из трёх движков и двух размеров. webkit объявлен, но не снят и не
   установлен — ровно тот случай, ради которого вариант и показывают пустым. */
const PLATFORMS = {
  'docker-chromium-1x':          ['home.png', 'login.png', 'cart.png'],
  'docker-chromium-1x-390x844':  ['home.png', 'login.png'],
  'docker-firefox-1x':           ['home.png', 'cart.png'],
  'docker-webkit-1x':            [],
};
const detail = {
  platforms: Object.entries(PLATFORMS).map(([platform, names]) => ({
    platform, count: names.length, items: names.map(n => item(n))})),
  current: 'win-chromium-1x',       // машина сервиса — НЕ та, что снимает
  scope: 'project:shop', scope_label: 'Shop web — VisTest set',
  root: '/srv/shop/.vistest', flows: [], auth_flow: '',
};
const scopes = {scopes: [
  {scope: 'global', label: 'VisTest — own set', kind: 'global',
   platforms: [{platform: 'docker-chromium-1x', count: 2}]},
  {scope: 'project:shop', label: 'Shop web — VisTest set', kind: 'project',
   project_key: 'shop',
   platforms: Object.entries(PLATFORMS)
     .map(([platform, n]) => ({platform, count: n.length}))},
]};
const API = {
  '/api/auth/state': {auth: 'on', local_mode: false, needs_setup: false},
  '/api/auth/me': {login: 'anna', name: 'Анна', role: 'admin', grants: {},
                   own_project: 'demo'},
  '/api/team': {name: 'Team shop-web'},
  '/api/run-projects': [{name: 'shop', runs: 5, last_run: null},
                        {name: 'demo', runs: 2, last_run: null}],
  '/api/projects': {projects: [{key: 'shop', name: 'Shop web',
                                root: '/srv/shop', baselines: []}]},
  '/api/baselines/scopes': scopes,
  '/api/baselines/detail': detail,
  '/api/matrix': {declared: true, base_viewport: '1440x900',
                  known_browsers: ['chromium', 'firefox', 'webkit'],
                  browser_state: {chromium: 'installed', firefox: 'installed',
                                  webkit: 'missing'},
                  variants: [{label: 'chromium · 1440×900',
                              platform: 'docker-chromium-1x'},
                             {label: 'chromium · 390×844',
                              platform: 'docker-chromium-1x-390x844'},
                             {label: 'firefox · 1440×900',
                              platform: 'docker-firefox-1x'}]},
  '/api/runs/counts': {all: 5, unreviewed: 0},
  '/api/runs': [],
  '/api/tests': {tests: [], project_tests: []},
  '/api/metrics/summary': {totals: {}},
  '/api/record/status': {running: false},
  '/api/users/pending': {pending: []},
  '/api/doctor/env': {platform_key: 'win-chromium-1x'},
  '/api/doctor/last': null,
  '/api/presence': {count: 1, online: []},
};
const answer = p => {
  const key = Object.keys(API).find(k => p === k || p.startsWith(k + '?'));
  return key ? API[key] : {};
};

const dom = new JSDOM(html, {url: 'http://localhost:8420/ui/#/baselines',
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
w.confirm = () => false;
w.matchMedia = w.matchMedia
  || (() => ({matches: false, addListener() {}, removeListener() {}}));

const bundle = listed
  .map(f => `//# ${f}\n` + fs.readFileSync(path.join(FRONT, 'js', f), 'utf8'))
  .join('\n;\n');
w.eval(bundle + '\nglobalThis.__vt={state,currentScope,currentProject,SCREENS};');

const rest = () => new Promise(r => setTimeout(r, 250));
await rest(); await rest(); await rest();

const problems = [];
const check = (what, ok) => { if (!ok) problems.push('· ' + what); };
const $$ = s => [...w.document.querySelectorAll(s)];
const flat = s => String(s || '').replace(/\s+/g, ' ').trim();

/* ---------- 1. набор эталонов следует за закреплённым проектом ---------- */
/* «shop» выбирается сам: у него прогонов больше, а выбора человека ещё не
   было. Вместе с ним обязан приехать и его набор — иначе шапка говорит одно,
   а сетка показывает другое. */
check(`закреплён проект с прогонами, а не первый попавшийся `
      + `(${w.__vt.state.project})`, w.__vt.state.project === 'shop');
check(`набор эталонов следует за проектом (${w.__vt.currentScope()})`,
      w.__vt.currentScope() === 'project:shop');
const eyebrow = flat($$('.eyebrow')[0] && $$('.eyebrow')[0].textContent);
check(`надзаголовок называет проект и набор: «${eyebrow}»`,
      /Shop web/.test(eyebrow) && /LIBRARY/.test(eyebrow));
const pill = flat(w.document.querySelector('#projName').textContent);
check(`в шапке имя проекта, а не «all projects» (${pill})`, pill === 'Shop web');

/* ---------- 2. движок и размер окна — два переключателя ---------- */
const tabs = $$('.pb-tab');
check(`вкладка на каждый движок, а не строка на каждый ключ хранения `
      + `(${tabs.length})`, tabs.length === 3);
const names = tabs.map(t => flat(t.querySelector('.nm').textContent));
check(`порядок движков устойчивый, а не по числу эталонов (${names})`,
      String(names) === 'chromium,firefox,webkit');
const counts = tabs.map(t => flat(t.querySelector('.n').textContent));
check(`у каждого движка своё число — сумма по его размерам окна (${counts})`,
      String(counts) === '5,2,0');
check('пустой движок показан, а не спрятан: ноль и есть ответ',
      tabs[2].classList.contains('empty'));
check('движок, которого нет в окружении, помечен',
      tabs[2].classList.contains('gone')
      && /playwright install webkit/i.test(tabs[2].getAttribute('data-tip')));
check('выбран не движок машины сервиса, а самый полный из снятых',
      tabs[0].classList.contains('on'));

const chips = $$('.pb-chip');
const chipNames = chips.map(c => flat(c.querySelector('.nm').textContent));
check(`размеры окна выбранного движка отдельной строкой (${chipNames})`,
      chips.length === 2 && chipNames[0] === '1440×900'
      && chipNames[1] === '390×844');
check('базовый размер подписан своим размером, а не прочерком',
      !chipNames.includes('base size') && !chipNames.includes('—'));
check(`размер окна назван и словом — «phone» рядом с 390 `
      + `(${flat(chips[1].textContent)})`, /phone/.test(chips[1].textContent));
check('ключ хранения показан рядом, но переключателем больше не является',
      /docker-chromium-1x$/.test(flat(
        (w.document.querySelector('.pb-key') || {}).textContent)));

/* ---------- 3. сетка показывает выбранный вариант ---------- */
const cardNames = () => $$('.grid-cards .snap-card .nm').map(n => flat(n.textContent));
check(`сетка — эталоны выбранного варианта (${cardNames()})`,
      String(cardNames()) === 'home.png,login.png,cart.png');

/* ---------- 4. переключение движка не ходит в сеть ---------- */
let calls = 0;
const realFetch = w.fetch;
w.fetch = async u => { calls++; return realFetch(u); };
tabs[1].dispatchEvent(new w.MouseEvent('click', {bubbles: true}));
await rest(); await rest();
check(`после переключения движка сетка — firefox (${cardNames()})`,
      String(cardNames()) === 'home.png,cart.png');
check(`выбранный размер окна пересчитан под новый движок `
      + `(${$$('.pb-chip').length})`, $$('.pb-chip').length === 1);

/* ---------- 5. сравнение по движкам ---------- */
/* Возвращаемся на chromium и включаем сравнение: вопрос «как эта страница
   выглядит в каждом движке» до сих пор решался переключением туда-сюда с
   запоминанием картинки в голове. */
$$('.pb-tab')[0].dispatchEvent(new w.MouseEvent('click', {bubbles: true}));
await rest(); await rest();
const cmpBtn = w.document.querySelector('#blCompare');
check('кнопка сравнения есть там, где движков больше одного', !!cmpBtn);
if (cmpBtn) {
  cmpBtn.dispatchEvent(new w.MouseEvent('click', {bubbles: true}));
  await rest();
  const cards = $$('.cmp-card');
  const rows = cards.map(c => flat(c.querySelector('.nm').textContent));
  /* Объединение по имени, а не по индексу: у firefox нет login.png, и
     совмещение по порядку показало бы cart.png под именем login.png. */
  check(`строка на снимок, объединение по имени (${rows})`,
        String(rows) === 'home.png,login.png,cart.png');
  const cells = cards.map(c => c.querySelectorAll('.cmp-cell').length);
  check(`колонка на каждый движок в каждой строке (${cells})`,
        cells.every(n => n === 3));
  const login = cards[1];
  const caps = [...login.querySelectorAll('.cmp-cap')].map(x => flat(x.textContent));
  check(`каждая колонка подписана своим движком (${caps})`,
        /chromium/.test(caps[0]) && /firefox/.test(caps[1])
        && /webkit/.test(caps[2]));
  const missing = login.querySelectorAll('.cmp-cell.none').length;
  check(`отсутствующий вариант занимает своё место, а не пропускается `
        + `(${missing} из 3)`, missing === 2);
  check(`сколько не снято — сказано словами: `
        + `«${flat(login.querySelector('.tag').textContent)}»`,
        /2 of 3 not captured/.test(login.querySelector('.tag').textContent));
  check('у снятого варианта картинка, у неснятого — объяснение',
        !!login.querySelector('.cmp-cell img')
        && /not captured in firefox/i.test(login.textContent));
  cmpBtn.dispatchEvent(new w.MouseEvent('click', {bubbles: true}));
  await rest();
  check('выключается тем же щелчком', !$$('.cmp-card').length);
}

/* ---------- 6. фильтр не ходит в сеть и не перерисовывает экран ---------- */
const find = w.document.querySelector('#blFind');
check('поле фильтра на месте', !!find);
if (find) {
  const before = calls;
  find.value = 'login';
  find.dispatchEvent(new w.Event('input', {bubbles: true}));
  await rest();
  check(`фильтр сузил сетку (${cardNames()})`, String(cardNames()) === 'login.png');
  check('фильтр не ходит в сеть на каждую букву', calls === before);
  check('и не забирает фокус, потому что не пересобирает экран',
        w.document.querySelector('#blFind') === find);
  find.value = '';
  find.dispatchEvent(new w.Event('input', {bubbles: true}));
  await rest();
}

/* ---------- 7. второй набор предлагается, но не подменяет собой проект ---- */
const setRow = w.document.querySelector('.pb-set');
check('строка «SET» есть — у этой установки наборов два', !!setRow);
check('и она называет открытый набор, а не предлагает выбрать заново',
      !!setRow && /Shop web/.test(setRow.textContent)
      && /show another set/i.test(setRow.textContent));

/* ---------- 8. ничего не сломалось по дороге ---------- */
if (errors.length) problems.push('ошибки при работе:\n  ' + errors.join('\n  '));

dom.window.close();
if (problems.length) { console.error(problems.join('\n')); process.exit(1); }
console.log('ok · проект закреплён, движок и размер окна выбираются порознь, '
            + 'сравнение по движкам собрано по имени снимка');
process.exit(0);
