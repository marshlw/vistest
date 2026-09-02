/* Зоны игнорирования на экране.

   Половина смысла привязки к элементу — на этом экране. Зона по элементу и
   зона по координатам это два одинаковых прямоугольника на картинке, а разница
   между ними в том, переживёт ли маска ближайший редизайн. Не показать эту
   разницу — значит оставить всё как было: человек рисует рамку и не знает, что
   он получил.

   Проверяется поэтому не «работает ли код», а что именно человек увидит и что
   уедет на сервер после его выбора.

   Запуск: node tests/ui/zones_view.mjs <путь к frontend> */
import fs from 'node:fs';
import path from 'node:path';
import {JSDOM} from 'jsdom';

const FRONT = process.argv[2]
  || path.join(path.dirname(new URL(import.meta.url).pathname), '..', '..', 'frontend');
const html = fs.readFileSync(path.join(FRONT, 'index.html'), 'utf8');
const listed = [...html.matchAll(/<script src="js\/([^"]+)"><\/script>/g)].map(m => m[1]);

/* Адрес нейтральный: проверяются функции редактора зон, а не маршрутизация.
   С адресом снимка `boot()` ушёл бы рисовать экран на подставном ответе и
   упал бы на нём — то есть тест сообщал бы о другом. */
const dom = new JSDOM(html, {url: 'http://localhost:8420/ui/#/runs',
                             runScripts: 'outside-only'});
const w = dom.window;

/* Ответ сервера на «что я обвёл»: лесенка из узла и его родителя. Пустой
   вариант — эталон, снятый без DOM. */
let ELEMENTS = {nodes: [
  {selector: 'body > form > button.btn', testid: 'submit', id: null,
   tag: 'button', cls: 'btn primary', text: 'Войти',
   x: 10, y: 10, w: 30, h: 20, score: 0.92, holds_by: 'data-testid'},
  {selector: 'body > form', testid: null, id: null, tag: 'form', cls: 'login',
   text: null, x: 0, y: 0, w: 90, h: 60, score: 0.21, holds_by: 'css path'},
]};
w.fetch = async () => ({ok: true, status: 200,
                        headers: {get: () => 'application/json'},
                        json: async () => ELEMENTS,
                        text: async () => JSON.stringify(ELEMENTS),
                        clone() { return this; }});

const bundle = listed
  .map(f => fs.readFileSync(path.join(FRONT, 'js', f), 'utf8')).join('\n;\n');
w.eval(bundle);

const problems = [];
const check = (what, ok) => { if (!ok) problems.push(what); };
const flat = s => String(s || '').replace(/\s+/g, ' ').trim();

/* ---------- чем зона держится ---------- */
const byElement = {x: 1, y: 1, w: 9, h: 9, selector: 'div.a',
                   match: {testid: 'submit'}, matches: 1, lost: false};
const byCoords = {x: 1, y: 1, w: 9, h: 9};
const lost = {x: 1, y: 1, w: 9, h: 9, match: {testid: 'gone'}, lost: true};

check('zoneKind различает три вида: '
      + [w.zoneKind(byElement), w.zoneKind(byCoords), w.zoneKind(lost)].join(','),
      w.zoneKind(byElement) === 'element' && w.zoneKind(byCoords) === 'coordinates'
      && w.zoneKind(lost) === 'lost');

/* Подпись обязана называть ПОСЛЕДСТВИЕ, а не вид зоны: «held by coordinates»
   само по себе не отвечает на вопрос, надо ли с этим что-то делать. */
check('подпись координатной зоны говорит, чем это грозит: ' + flat(w.zoneTitle(byCoords)),
      /layout moves/.test(w.zoneTitle(byCoords)));
check('подпись потерянной зоны говорит, что цель не найдена: ' + flat(w.zoneTitle(lost)),
      /no longer finds/.test(w.zoneTitle(lost)));
check('подпись зоны по элементу называет, за что держится: ' + flat(w.zoneTitle(byElement)),
      /submit/.test(w.zoneTitle(byElement)));

/* ---------- диалог выбора элемента ---------- */
const d = {platform: 'linux-chromium-1x', name: 'a.png', scope: 'global'};
let patch = null;
await w.offerElement({x: 10, y: 10, w: 30, h: 20}, d, p => { patch = p; });
await new Promise(r => setTimeout(r, 30));

const modal = w.document.querySelector('#modalBg');
check('после протяжки предлагается привязка', !!modal);
if (modal) {
  const rows = [...modal.querySelectorAll('.pick')];
  check('в лесенке оба уровня, не только лучший: ' + rows.length, rows.length === 2);
  /* Устойчивость зацепки видна ДО выбора: data-testid переживёт перестройку
     вёрстки, путь из шести тегов — нет, и человек имеет право знать разницу. */
  check('устойчивость зацепки подписана: ' + flat(rows[0] && rows[0].textContent),
        /data-testid/.test(rows[0].textContent) && /css path/.test(rows[1].textContent));

  const buttons = [...modal.querySelectorAll('button')];
  const keep = buttons.find(b => /coordinates/i.test(b.textContent));
  const bind = buttons.find(b => /Bind/i.test(b.textContent));
  check('есть выход «оставить по координатам»', !!keep);
  check('есть «привязать к элементу»', !!bind);

  if (bind) {
    bind.click();
    check('привязка вернула заплатку', !!patch);
    if (patch) {
      /* testid уезжает в match — это то, по чему зона будет опознаваться;
         координаты подтягиваются к элементу и становятся запасными. */
      check('в заплатке testid: ' + JSON.stringify(patch.match),
            patch.match && patch.match.testid === 'submit');
      check('координаты подтянуты к элементу: '
            + [patch.x, patch.y, patch.w, patch.h].join(','),
            patch.x === 10 && patch.y === 10 && patch.w === 30 && patch.h === 20);
      check('зона больше не считается потерянной', patch.lost === false);
    }
    check('модалка закрылась', !w.document.querySelector('#modalBg'));
  }
}

/* ---------- эталон без DOM ---------- */
ELEMENTS = {nodes: [], reason: 'no DOM snapshot was captured with this baseline'};
let patch2 = null;
await w.offerElement({x: 1, y: 1, w: 5, h: 5}, d, p => { patch2 = p; });
await new Promise(r => setTimeout(r, 30));
/* Пустое меню читалось бы как «ничего не нашлось», а это другое состояние:
   зацепиться не за что в принципе, и зона остаётся координатной. */
check('без DOM меню не показывается', !w.document.querySelector('#modalBg'));
check('без DOM зона не трогается', patch2 === null);
check('без DOM человеку сказали причину: ' + flat(w.document.querySelector('#toast').textContent),
      /DOM/.test(w.document.querySelector('#toast').textContent));

dom.window.close();
if (problems.length) { console.error(problems.join('\n')); process.exit(1); }
console.log('ok · три вида зон, лесенка из 2 уровней, привязка по data-testid');
process.exit(0);
