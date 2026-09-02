/* VisTest - self-hosted visual regression testing.
 * Copyright (C) 2026 Kirill Kulagin
 * SPDX-License-Identifier: AGPL-3.0-or-later
 *
 * This file is part of VisTest. See LICENSE for the full terms and NOTICE for
 * the trademark and commercial-licensing terms. Removing this header does not
 * remove those obligations.
 */

/* Выпадашки выбора браузера на карточке проекта — от НАЖАТИЯ, а не от вызова.

   `run_menu_view.mjs` рядом проверяет содержимое меню и зовёт `runBrowserMenu`
   напрямую. Этого оказалось мало: меню собиралось правильно, а на карточке
   «переставало раскрываться». Ломалось между кнопкой и меню — в позиции.

   `.menu` в стилях — `position:fixed` БЕЗ `top`/`left`. Такой блок остаётся на
   своём месте в потоке, а место у добавленного в `body` — за концом страницы,
   ниже экрана. Меню при этом есть в документе, пункты в нём правильные, все
   проверки «меню открылось» проходят — и человек не видит ничего. Поэтому
   здесь проверяется именно то, что отличает видимое меню от невидимого:
   явные координаты. Кнопки берутся из настоящей карточки, собранной
   `projectCard()`, и нажимаются как нажал бы человек.

   Заодно ловится и вся дорога до меню: обработчик на кнопке, `browserPick`,
   `stopPropagation` (без него общий слушатель на `document` закрыл бы меню
   тем же нажатием, которым его открыли).

   Запуск: node tests/ui/project_card_menu.mjs <путь к frontend> */
import fs from 'node:fs';
import path from 'node:path';
import {JSDOM} from 'jsdom';

const FRONT = process.argv[2]
  || path.join(path.dirname(new URL(import.meta.url).pathname), '..', '..', 'frontend');
const html = fs.readFileSync(path.join(FRONT, 'index.html'), 'utf8');
const listed = [...html.matchAll(/<script src="js\/([^"]+)"><\/script>/g)].map(m => m[1]);

const dom = new JSDOM(html, {url: 'http://localhost:8420/ui/#/projects',
                             runScripts: 'outside-only'});
const w = dom.window;
w.fetch = async () => ({ok: true, status: 200,
                        headers: {get: () => 'application/json'},
                        json: async () => ({
                          known_browsers: ['chromium', 'firefox', 'webkit'],
                          /* Что из этого здесь ЕСТЬ. Раньше приезжал только
                             список «что бывает», и меню предлагало движки,
                             которых в образе нет. */
                          browser_state: {chromium: 'installed',
                                          firefox: 'not-installed',
                                          webkit: 'no-playwright'}}),
                        text: async () => '{}', clone() { return this; }});

const bundle = listed
  .map(f => fs.readFileSync(path.join(FRONT, 'js', f), 'utf8')).join('\n;\n');
/* `state` объявлен через `const`, то есть живёт в лексическом окружении этого
   `eval`, а не свойством `window`. Мостик ставится ВНУТРИ той же вычисляемой
   строки — снаружи до него не дотянуться ничем. */
w.eval(bundle + '\n;window.__vt={get state(){return state;}};');

const problems = [];
const check = (what, ok) => { if (!ok) problems.push(what); };
const rest = () => new Promise(r => setTimeout(r, 60));
const flat = s => String(s || '').replace(/\s+/g, ' ').trim();

/* `boot()` асинхронный, и `route()` на его пути закрывает открытые меню. */
await new Promise(r => setTimeout(r, 300));

const project = {
  key: 'shop', name: 'Shop', root: '/srv/shop', mode: 'adapter',
  tests: 'tests/visual', python: 'python3',
  baseline_counts: {'docker-chromium-1x': 11},
  /* Набор снят для firefox; каталог chromium пуст — наследство отвергнутых
     прогонов, которые создавали его до всех проверок. Ровно та карточка, с
     которой пришла жалоба. */
  baseline_status: {current_platform: 'docker-chromium-1x',
                    best_platform: 'docker-firefox-1x',
                    vistest: [{platform: 'docker-chromium-1x', count: 0},
                              {platform: 'docker-firefox-1x', count: 11}],
                    vistest_total: 11},
};

/* jsdom не считает раскладку: у всех элементов прямоугольник нулевой, и «меню
   встало у кнопки» от «меню встало у пункта» так не отличить. Поэтому
   координаты назначаются руками — кнопке одни, пунктам меню другие. */
const RECT = {btn: 240, item: 430};
const place = (node, top) => {
  node.getBoundingClientRect = () => ({top, bottom: top + 26, left: 900,
                                       right: 980, width: 80, height: 26,
                                       x: 900, y: top});
};

const card = w.projectCard(project);
w.document.body.append(card);

const acts = card.querySelector('.acts');
check('у карточки есть строка действий', !!acts);
const buttons = acts ? [...acts.querySelectorAll('button')] : [];

/* Ровно те две кнопки, на которые жалуются: «▾» после каждой кнопки прогона. */
const picks = buttons.filter(b => String(b.textContent || '').trim() === '▾');
check(`на карточке две кнопки выбора браузера, найдено ${picks.length}`,
      picks.length === 2);

/* Второе, чем «меню собралось» отличается от «меню видно»: классы, которые
   знает таблица стилей. Пункты собирались голыми `<button>`, а в `ui.css`
   лежали правила для `.menu .mi` и `.menu .div` — столбик системных кнопок без
   подсветки и без разделителей. Имена сверяются с самим файлом стилей: разойтись
   они могут только молча. */
const css = fs.readFileSync(path.join(FRONT, 'ui.css'), 'utf8');
check('в стилях есть правило для пункта меню', /\.menu\s+\.mi\b/.test(css));
check('в стилях есть правило для разделителя', /\.menu\s+\.div\b/.test(css));
check('в стилях есть правило для отключённого пункта', /\.menu\s+\.mi\.off\b/.test(css));

const styled = (m, who) => {
  const items = [...m.querySelectorAll('button')];
  check(`${who}: пункты помечены классом .mi`,
        items.length > 0 && items.every(b => b.classList.contains('mi')));
  check(`${who}: разделители помечены классом .div`,
        [...m.children].some(n => n.classList && n.classList.contains('div')));
};

const inWindow = (m, who) => {
  /* jsdom не считает раскладку, поэтому проверяется не «видно», а то, без чего
     видно быть не может: координаты проставлены и это числа в пределах окна. */
  const top = m.style.top, left = m.style.left;
  check(`${who}: у меню задан top (было «${top}»)`, /^-?\d/.test(top));
  check(`${who}: у меню задан left (было «${left}»)`, /^-?\d/.test(left));
  const t = parseFloat(top), l = parseFloat(left);
  check(`${who}: меню не уехало за левый край (left=${left})`, !(l < 0));
  check(`${who}: меню не уехало выше окна (top=${top})`, !(t < 0));
};

for (let i = 0; i < picks.length; i++) {
  const b = picks[i];
  const who = `кнопка «▾» №${i + 1} (${(b.title || b.getAttribute('data-tip') || '').slice(0, 40)})`;

  /* Нажатие настоящее: событие всплывает до `document`, где висит `closeMenus`. */
  b.dispatchEvent(new w.MouseEvent('click', {bubbles: true, cancelable: true}));
  await rest();

  const m = w.document.querySelector('.menu');
  check(`${who}: меню появилось в документе`, !!m);
  if (!m) continue;
  check(`${who}: в меню есть движки`,
        [...m.querySelectorAll('button')]
          .some(x => /^firefox\b/.test(String(x.textContent).trim())));
  inWindow(m, who);
  styled(m, who);
  w.closeMenus();
}

/* Второе нажатие по той же кнопке не должно оставлять два меню друг на друге. */
if (picks.length) {
  picks[0].dispatchEvent(new w.MouseEvent('click', {bubbles: true, cancelable: true}));
  await rest();
  picks[0].dispatchEvent(new w.MouseEvent('click', {bubbles: true, cancelable: true}));
  await rest();
  check('повторное нажатие оставляет одно меню, а не стопку',
        w.document.querySelectorAll('.menu').length === 1);
  w.closeMenus();
}

/* И то же для меню «⋯ → снять набор, выбрать браузеры»: якорь там передаётся
   отдельно от события, и координаты берутся из него. */
const at = {bottom: 220, left: 340};
await w.runBrowserMenu(null, {at, run: () => {}, label: 'Snap'});
await rest();
const fromItem = w.document.querySelector('.menu');
check('меню из пункта другого меню появилось', !!fromItem);
if (fromItem) {
  inWindow(fromItem, 'меню из пункта «⋯»');
  styled(fromItem, 'меню из пункта «⋯»');
  check(`оно стоит у переданного якоря, а не в углу (top=${fromItem.style.top})`,
        parseFloat(fromItem.style.top) >= 220);
}


/* ---------- кнопки одной строки — одной высоты ---------- */
/* «Фронт разъезжается, кнопки разные»: у «▾» стоял свой `padding` поверх
   `.btn`, и две кнопки из восьми были ниже остальных ровно там, где глаз
   считывает их как одну группу. Высоту задаёт `.btn`, один на всю строку. */
picks.forEach((b, i) => {
  check(`кнопка «▾» №${i + 1} не переопределяет отступы вручную `
        + `(style="${b.getAttribute('style') || ''}")`,
        !/padding/.test(b.getAttribute('style') || ''));
  check(`кнопка «▾» №${i + 1} — обычная .btn`, b.classList.contains('btn'));
});
check('в стилях есть узкий вариант кнопки', /\.btn\.pick\b/.test(css));
/* И он сужает ТОЛЬКО по горизонтали: вертикальный отступ здесь — это ровно та
   поломка, которую чинили. */
const pickRule = (css.match(/\.btn\.pick\{([^}]*)\}/) || [])[1] || '';
check(`.btn.pick не трогает высоту (${pickRule})`,
      !/padding-(top|bottom)/.test(pickRule)
      && !/padding\s*:/.test(pickRule));

/* ---------- «⋯ → выбрать браузеры» встаёт у кнопки «⋯» ---------- */
/* Пункт стоит на середине открытого меню, примерно на двести пикселей ниже
   кнопки. Взяв его прямоугольник, подменю появлялось в пустоте посреди
   страницы — родительское меню к этому моменту уже закрыто, и рядом с новым
   меню нет ничего, на что человек в этот момент смотрел. */
w.closeMenus();
const more = buttons[buttons.length - 1];
check('последняя кнопка строки — «⋯»', /⋯|\.\.\./.test(more.textContent));
place(more, RECT.btn);
more.dispatchEvent(new w.MouseEvent('click', {bubbles: true, cancelable: true}));
await rest();

const dots = w.document.querySelector('.menu');
check('меню «⋯» открылось', !!dots);
if (dots) {
  const items = [...dots.querySelectorAll('button')];
  items.forEach(b => place(b, RECT.item));
  const pickSnap = items.find(b => /pick browsers/i.test(b.textContent));
  check('в меню «⋯» есть «снять набор, выбрать браузеры»', !!pickSnap);
  if (pickSnap) {
    styled(dots, 'меню «⋯»');
    pickSnap.dispatchEvent(new w.MouseEvent('click', {bubbles: true,
                                                      cancelable: true}));
    await rest();
    const sub = w.document.querySelector('.menu');
    check('подменю выбора браузеров появилось', !!sub);
    if (sub) {
      const top = parseFloat(sub.style.top);
      check(`подменю встало у кнопки «⋯» (${RECT.btn}), а не у пункта `
            + `(${RECT.item}); top=${sub.style.top}`,
            top < RECT.item);
      inWindow(sub, 'подменю из «⋯»');

      /* И движок, которого в окружении нет, назван до нажатия, а не после. */
      const ff = [...sub.querySelectorAll('button')]
        .find(b => /^firefox/.test(b.textContent.trim()));
      check('firefox в меню помечен как неустановленный: '
            + flat(ff && ff.textContent),
            !!ff && /not installed/i.test(ff.textContent));
      check('и объяснение содержит команду: ' + flat(ff && ff.title),
            !!ff && /playwright install firefox/.test(ff.title || ''));
      const cr = [...sub.querySelectorAll('button')]
        .find(b => /^chromium/.test(b.textContent.trim()));
      check('установленный движок ничем не помечен: ' + flat(cr && cr.textContent),
            !!cr && !/not installed/i.test(cr.textContent));
    }
  }
}
w.closeMenus();

/* ---------- «Open VisTest snapshots» открывает НЕПУСТОЙ набор ---------- */
/* Здесь бралась платформа, совпавшая с `current_platform`, а тот — платформа
   машины сервиса, то есть всегда chromium. Сняли набор для firefox, нажали —
   и попали на вкладку эталонов с прибитым chromium. Снаружи это ровно «снятые
   эталоны не появляются в эталонах». */
const openBtn = [...card.querySelectorAll('button')]
  .find(b => /Open VisTest snapshots/i.test(b.textContent));
check('на карточке есть «Open VisTest snapshots»', !!openBtn);
if (openBtn) {
  w.__vt.state.platform = null;
  openBtn.dispatchEvent(new w.MouseEvent('click', {bubbles: true, cancelable: true}));
  await rest();
  const platform = w.__vt.state.platform, scope = w.__vt.state.scope;
  check(`открывается снятый набор, а не пустой (${platform})`,
        platform === 'docker-firefox-1x');
  check(`и набор проекта (${scope})`, scope === 'project:shop');
}

/* ---------- пустой набор и отсутствующий — разные вещи ---------- */
/* Строка читалась «11 on docker-chromium-1x (11), docker-firefox-1x (0)» —
   то есть «наборы есть у всех, у части пустые». На самом деле пустые каталоги
   оставляла за собой каждая отвергнутая попытка прогона. */
const setLine = flat(card.querySelector('[data-vt-set]').textContent);
check(`строка набора не выдаёт пустой каталог за набор: «${setLine}»`,
      /not captured yet/i.test(setLine) && !/chromium \(0\)/.test(setLine));

dom.window.close();
if (problems.length) { console.error(problems.join('\n')); process.exit(1); }
console.log('ok · выпадашки открываются у кнопки, набор открывается непустой');
process.exit(0);
