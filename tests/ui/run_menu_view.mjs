/* Меню запуска: в одном браузере, в каждом, или во всех.

   Три вида запуска — это три разных вопроса, и на экране их надо различать
   словами, а не порядком пунктов:

     · «прогони в firefox»    — один прогон, разбираюсь с одним движком;
     · «по одному на браузер» — N прогонов, каждый со своим вердиктом; идут
                                параллельно, потому что наборы эталонов разные;
     · «общий по всем»        — ОДИН прогон и один ответ.

   Отдельно проверяется отказ. Проект, который запускается своей командой
   (`npx playwright test`), назвать браузер не даёт. Пункты в этом случае
   обязаны быть видимыми и отключёнными с причиной: спрятать их совсем значит
   оставить человека гадать, почему у соседнего проекта выбор есть, а у его нет.

   Запуск: node tests/ui/run_menu_view.mjs <путь к frontend> */
import fs from 'node:fs';
import path from 'node:path';
import {JSDOM} from 'jsdom';

const FRONT = process.argv[2]
  || path.join(path.dirname(new URL(import.meta.url).pathname), '..', '..', 'frontend');
const html = fs.readFileSync(path.join(FRONT, 'index.html'), 'utf8');
const listed = [...html.matchAll(/<script src="js\/([^"]+)"><\/script>/g)].map(m => m[1]);

const dom = new JSDOM(html, {url: 'http://localhost:8420/ui/#/runs',
                             runScripts: 'outside-only'});
const w = dom.window;
w.fetch = async () => ({ok: true, status: 200,
                        headers: {get: () => 'application/json'},
                        json: async () => ({known_browsers: ['chromium', 'firefox', 'webkit']}),
                        text: async () => '{}', clone() { return this; }});

const bundle = listed
  .map(f => fs.readFileSync(path.join(FRONT, 'js', f), 'utf8')).join('\n;\n');
w.eval(bundle);

const problems = [];
const check = (what, ok) => { if (!ok) problems.push(what); };
const flat = s => String(s || '').replace(/\s+/g, ' ').trim();

/* Дать запуску интерфейса договорить.

   `boot()` асинхронный, и на его пути стоит `route()`, а тот закрывает все
   открытые меню — это его работа. Открыть меню, пока запуск ещё в полёте,
   значит проверять гонку вместо меню. */
await new Promise(r => setTimeout(r, 300));

const anchor = w.document.createElement('button');
w.document.body.append(anchor);
const fakeEvent = () => ({target: anchor, stopPropagation() {}});

/* ---------- обычный проект: выбор доступен ---------- */
const asked = [];
await w.runBrowserMenu(fakeEvent(), {run: how => asked.push(how), label: 'Run shop'});
await new Promise(r => setTimeout(r, 20));

let menu = w.document.querySelector('.menu');
check('меню открылось', !!menu);
if (menu) {
  const items = [...menu.querySelectorAll('button')];
  const labels = items.map(b => flat(b.textContent));

  /* Список движков приезжает с сервера, а не зашит в интерфейс. */
  check('в меню все три движка: ' + labels.join(' | '),
        labels.includes('chromium') && labels.includes('firefox')
        && labels.includes('webkit'));
  /* Пункт «как решат тесты» обязателен: до этой правки он был единственным
     поведением, и отнимать его нельзя. */
  check('есть «как решат тесты»', labels.some(l => /As the tests decide/i.test(l)));
  check('есть «по одному на браузер»: ' + labels.join(' | '),
        labels.some(l => /Each browser separately/i.test(l)));
  check('есть «все браузеры, один прогон»',
        labels.some(l => /All browsers/i.test(l)));

  /* Разница между видами обязана быть сказана словами, а не подразумеваться
     порядком пунктов. */
  const each = items.find(b => /Each browser separately/i.test(b.textContent));
  const all = items.find(b => /All browsers/i.test(b.textContent));
  check('«по одному» объясняет, что прогонов несколько: ' + flat(each && each.title),
        /parallel/i.test(each.title) && /own verdict/i.test(each.title));
  check('«все» объясняет, что прогон один: ' + flat(all && all.title),
        /One run/i.test(all.title));

  /* И главное — что уедет на сервер. */
  items.find(b => flat(b.textContent) === 'firefox').click();
  each.click();
  all.click();
  check('запросы собраны верно: ' + JSON.stringify(asked),
        asked.length === 3
        && JSON.stringify(asked[0]) === JSON.stringify({browsers: ['firefox']})
        && asked[1].mode === 'separate' && asked[1].browsers.length === 3
        && asked[2].mode === 'together' && asked[2].browsers.length === 3);
}

/* ---------- проект со своей командой: отказ ---------- */
w.closeMenus();
const refusal = 'Проект «Shop» запускается своей командой, и куда в ней вписать браузер, знаем не мы.';
const blocked = [];
await w.runBrowserMenu(fakeEvent(), {run: how => blocked.push(how), refusal});
await new Promise(r => setTimeout(r, 20));

menu = w.document.querySelector('.menu');
if (menu) {
  const items = [...menu.querySelectorAll('button')];
  const ff = items.find(b => flat(b.textContent) === 'firefox');
  const each = items.find(b => /Each browser separately/i.test(b.textContent));
  check('при отказе пункты видны, но отключены',
        !!ff && ff.disabled === true && each.disabled === true);
  check('причина отказа стоит в подсказке: ' + flat(ff && ff.title),
        /своей командой/.test(ff.title));
  /* «Как решат тесты» остаётся живым: отказ касается ВЫБОРА браузера, а не
     прогона вообще. */
  const asIs = items.find(b => /As the tests decide/i.test(b.textContent));
  check('обычный прогон остаётся доступным', !!asIs && asIs.disabled !== true);
  ff.click();
  check('отключённый пункт ничего не запускает', blocked.length === 0);
}

dom.window.close();
if (problems.length) { console.error(problems.join('\n')); process.exit(1); }
console.log('ok · три вида запуска, отказ объяснён и ничего не запускает');
process.exit(0);
