/* Как интерфейс показывает матрицу.

   Половина смысла матрицы — на экране. Шесть строк `checkout.png` подряд с
   одинаковым именем отвечают на вопрос «что упало» словом «всё», и вся
   экономия внимания, ради которой матрица делалась, теряется в списке.

   Проверяется поэтому не «работает ли код», а что именно человек увидит:
   порядок групп, текст заголовка, что стоит первой колонкой у вложенной
   строки, и что переключатель вариантов знает, на каком варианте мы стоим.

   Запуск: node tests/ui/matrix_view.mjs <путь к frontend> */
import fs from 'node:fs';
import path from 'node:path';
import {JSDOM} from 'jsdom';

const FRONT = process.argv[2]
  || path.join(path.dirname(new URL(import.meta.url).pathname), '..', '..', 'frontend');
const html = fs.readFileSync(path.join(FRONT, 'index.html'), 'utf8');
const listed = [...html.matchAll(/<script src="js\/([^"]+)"><\/script>/g)].map(m => m[1]);

const dom = new JSDOM(html, {url: 'http://localhost:8420/ui/#/runs/7',
                             runScripts: 'outside-only'});
const w = dom.window;
w.fetch = async () => ({ok: true, status: 200,
                        headers: {get: () => 'application/json'},
                        json: async () => ({}), text: async () => '{}',
                        clone() { return this; }});

const comps = [
  {id: 1, snapshot_name: 'checkout.png', variant: 'chromium · 1440×900',
   verdict: 'pass', max_severity: 0, ssim: 1, changed_area_pct: 0},
  {id: 2, snapshot_name: 'checkout.png', variant: 'chromium · 390×844',
   verdict: 'fail', max_severity: 61, ssim: 0.93, changed_area_pct: 1.2},
  {id: 3, snapshot_name: 'checkout.png', variant: 'firefox · 1440×900',
   verdict: 'fail', max_severity: 44, ssim: 0.96, changed_area_pct: 0.4},
  {id: 4, snapshot_name: 'login.png', variant: 'chromium · 1440×900',
   verdict: 'pass', max_severity: 0, ssim: 1, changed_area_pct: 0},
];
const run = {id: 7, run_key: 'ui-1', comparisons: comps,
             variants: ['chromium · 1440×900', 'chromium · 390×844',
                        'firefox · 1440×900']};

/* Один eval: классические <script> делят область видимости, а `eval` на файл
   этого не воспроизводит — и `state` из core.js не был бы виден проверке. */
const bundle = listed
  .map(f => fs.readFileSync(path.join(FRONT, 'js', f), 'utf8')).join('\n;\n');
const flat = s => String(s).replace(/\s+/g, ' ').trim();
w.eval(bundle + `
  const groups = groupBySnapshot(${JSON.stringify(comps)});
  const cols = '1fr 120px 90px 100px 110px';
  globalThis.__ = {
    order: groups.map(g => g.name + ':' + g.items.length),
    head:  groupHead(groups[0]).textContent,
    nested: snapshotRow(groups[0].items[0], cols, true).textContent,
    plain:  snapshotRow(groups[0].items[0], cols, false).textContent,
    chip:   variantChip({variants: 3}),
    one:    variantChip({variants: 1}),
    labels: [variantLabel('linux-chromium-1x-390x844'),
             variantLabel('linux-chromium-1x'),
             variantLabel('нечто-непонятное')],
  };
  state.run = ${JSON.stringify(run)};
  const sw = variantSwitch(${JSON.stringify(comps[1])}, ${JSON.stringify(run)});
  globalThis.__.switch = [...sw.querySelectorAll('button')]
    .map(b => b.textContent.trim() + (b.classList.contains('on') ? '*' : ''));
  globalThis.__.alone = variantSwitch(${JSON.stringify(comps[3])},
    {comparisons: [${JSON.stringify(comps[3])}]}).querySelectorAll('button').length;
`);
const got = w.__;
const problems = [];
const check = (what, ok) => { if (!ok) problems.push(what); };

/* Порядок: снимок с падениями — первым. Экран прогона отвечает на «что
   чинить», а не «что там по алфавиту». */
check('порядок групп: ' + got.order,
      got.order[0] === 'checkout.png:3' && got.order[1] === 'login.png:1');

/* Заголовок называет ОБА числа: сколько вариантов и сколько из них красных.
   «2 to decide» без знаменателя читается как две сломанные страницы. */
check('заголовок группы: ' + flat(got.head),
      /checkout\.png/.test(got.head) && /3 variants/.test(got.head)
      && /2 of 3 failed/.test(got.head));

/* У вложенной строки первой колонкой стоит ВАРИАНТ: имя уже в заголовке, и
   повторять его шесть раз значит прятать за ним единственное отличие. */
check('вложенная строка начинается с варианта: ' + flat(got.nested),
      flat(got.nested).startsWith('chromium · 1440×900'));
check('плоская строка сохраняет имя: ' + flat(got.plain),
      flat(got.plain).startsWith('checkout.png'));

check('чип «×3 variants»: ' + flat(got.chip), /×3 variants/.test(got.chip));
check('при одном варианте чипа нет', got.one === '');

check('переключатель вариантов: ' + JSON.stringify(got.switch),
      got.switch.length === 3 && got.switch[1].endsWith('*'));
check('при одном варианте переключателя нет', got.alone === 0);

check('подписи из ключей: ' + JSON.stringify(got.labels),
      got.labels[0] === 'chromium · 390×844' && got.labels[1] === 'chromium'
      && got.labels[2] === 'нечто-непонятное');

dom.window.close();
if (problems.length) { console.error(problems.join('\n')); process.exit(1); }
console.log('ok · ' + got.order.length + ' групп, переключатель на '
            + got.switch.find(x => x.endsWith('*')));
process.exit(0);
