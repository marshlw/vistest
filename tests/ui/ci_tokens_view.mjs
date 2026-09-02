/* Экран токенов CI.

   Здесь два утверждения, и оба легко нарушить незаметно.

   **Секрет показывается один раз.** Дальше в базе только хеш, и достать его
   оттуда не может никто. Значит показ обязан быть таким, чтобы его нельзя было
   пролистать: модалкой, которую закрывают руками, а не строкой внизу карточки,
   которая уезжает при следующей перерисовке.

   **Секрет не попадает в список.** Список перерисовывается, копируется в
   тикеты и открыт на чужом мониторе на планёрке. Токен в нём — это токен,
   который утёк.

   Запуск: node tests/ui/ci_tokens_view.mjs <путь к frontend> */
import fs from 'node:fs';
import path from 'node:path';
import {JSDOM} from 'jsdom';

const FRONT = process.argv[2]
  || path.join(path.dirname(new URL(import.meta.url).pathname), '..', '..', 'frontend');
const html = fs.readFileSync(path.join(FRONT, 'index.html'), 'utf8');
const listed = [...html.matchAll(/<script src="js\/([^"]+)"><\/script>/g)].map(m => m[1]);

const SECRET = 'vt_SUPERSECRETVALUE1234567890ab';
const LISTING = {tokens: [
  {id: 1, name: 'shop · GitLab', prefix: 'vt_a1b2c3d4', project: 'shop',
   role: 'reviewer', created_by: 'anna', created_at: '2026-08-01T10:00:00',
   expires_at: null, last_used_at: '2026-08-30T09:00:00', revoked_at: null,
   status: 'active'},
  {id: 2, name: 'старый', prefix: 'vt_z9y8x7w6', project: 'shop',
   role: 'reviewer', created_by: 'anna', created_at: '2026-01-01T10:00:00',
   expires_at: null, last_used_at: null, revoked_at: '2026-08-01T00:00:00',
   status: 'revoked'},
]};

const dom = new JSDOM(html, {url: 'http://localhost:8420/ui/#/runs',
                             runScripts: 'outside-only'});
const w = dom.window;
const calls = [];
w.fetch = async (url, opts) => {
  const p = String(url).replace('http://localhost:8420', '');
  calls.push({p, method: (opts && opts.method) || 'GET'});
  let body = {};
  if (p.startsWith('/api/ci-tokens')) {
    body = (opts && opts.method === 'POST')
      ? {id: 3, name: 'новый', prefix: 'vt_n3w0n3w0', project: 'shop',
         role: 'reviewer', status: 'active', token: SECRET,
         created_at: '2026-09-01T00:00:00', expires_at: null,
         last_used_at: null, revoked_at: null}
      : LISTING;
  } else if (p.startsWith('/api/projects')) {
    body = {projects: [{key: 'shop'}, {key: 'billing'}]};
  }
  return {ok: true, status: 200, headers: {get: () => 'application/json'},
          json: async () => body, text: async () => JSON.stringify(body),
          clone() { return this; }};
};

const bundle = listed
  .map(f => fs.readFileSync(path.join(FRONT, 'js', f), 'utf8')).join('\n;\n');
w.eval(bundle);
await new Promise(r => setTimeout(r, 300));   // дать запуску договорить

const problems = [];
const check = (what, ok) => { if (!ok) problems.push(what); };
const flat = s => String(s || '').replace(/\s+/g, ' ').trim();

const box = w.document.createElement('div');
box.id = 'ciTokensCard';
w.document.body.append(box);
await w.renderCiTokens(box);
await new Promise(r => setTimeout(r, 30));

/* ---------- список ---------- */
const text = flat(box.textContent);
check('в списке оба токена: ' + text.slice(0, 120),
      /shop · GitLab/.test(text) && /старый/.test(text));
check('видно проект и роль', /shop/.test(text) && /reviewer/.test(text));
/* Последнее использование — единственное число, по которому видно, что старый
   токен уже никто не предъявляет, то есть что его пора гасить. */
check('видно последнее использование: ' + text.slice(-200),
      /last used/.test(text));
check('«ни разу» тоже сказано вслух', /never used/.test(text));

const buttons = [...box.querySelectorAll('button')].map(b => flat(b.textContent));
check('у действующего токена есть ротация и отзыв: ' + buttons.join('|'),
      buttons.includes('Rotate') && buttons.includes('Revoke'));
/* У отозванного действий быть не должно: отозвать дважды нельзя, а ротация
   мёртвого токена — это просто новый токен, и просить её здесь незачем.

   Считаем сами кнопки, а не ищем строку по тексту: строку в разметке легко
   спутать с её родителем, и проверка начнёт спрашивать не то. Действующий
   токен здесь один — значит и пар «Ротация + Отозвать» ровно одна. */
const acts = buttons.filter(t => t === 'Rotate' || t === 'Revoke');
check('действия ровно у одного, действующего токена: ' + acts.join('|'),
      acts.length === 2);

/* ---------- выпуск: секрет показывается один раз ---------- */
const mk = [...box.querySelectorAll('button')].find(b => /^Issue$/.test(flat(b.textContent)));
check('есть кнопка выпуска', !!mk);
if (mk) {
  mk.click();
  await new Promise(r => setTimeout(r, 60));

  const modal = w.document.querySelector('#modalBg');
  /* Модалка, а не строка: строку внизу карточки можно пролистать и потерять, а
     потерянный токен — это выпустить новый и вписать в пайплайн заново. */
  check('секрет показан модалкой', !!modal);
  if (modal) {
    const shown = flat(modal.textContent) + ' ' + [...modal.querySelectorAll('input')]
      .map(i => i.value).join(' ');
    check('секрет виден целиком', shown.includes(SECRET));
    check('сказано, что второй раз не покажут: ' + flat(modal.textContent).slice(0, 160),
          /a second time is impossible/i.test(modal.textContent));
    /* Готовая строка для пайплайна: без неё человек идёт искать, каким
       заголовком это отправлять, и находит не сразу. */
    check('показано, куда его вписать',
          /X-VisTest-Token/.test(modal.textContent)
          && /VISTEST_INGEST_TOKEN/.test(modal.textContent));
    check('сказано про привязку к проекту', /shop/.test(modal.textContent));

    const done = [...modal.querySelectorAll('button')]
      .find(b => /Copied it/.test(b.textContent));
    check('модалку закрывают руками', !!done);
    if (done) done.click();
  }

  /* И главное: после закрытия секрета не остаётся нигде. */
  await new Promise(r => setTimeout(r, 30));
  const after = w.document.body.innerHTML
    + [...w.document.querySelectorAll('input')].map(i => i.value).join(' ');
  check('после закрытия секрета нет на странице', !after.includes(SECRET));
}

dom.window.close();
if (problems.length) { console.error(problems.join('\n')); process.exit(1); }
console.log('ok · секрет показан один раз и не остался ни в списке, ни на странице');
process.exit(0);
