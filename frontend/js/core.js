/* Основа: выборка узлов, `api()`, тосты, экранирование.

   Всё остальное опирается на этот файл, а он — ни на что. Поэтому он идёт первым,
   и это единственное жёсткое правило в порядке подключения. */
const API = location.origin;
const $  = (s,r=document)=>r.querySelector(s);
const $$ = (s,r=document)=>[...r.querySelectorAll(s)];
function el(t,c,h){const n=document.createElement(t);if(c)n.className=c;if(h!=null)n.innerHTML=h;return n;}
/* Кликабельный <div> → кнопка для клавиатуры и скринридера.

   Половина навигации в этом интерфейсе — `.run-item`, `.snap-row`, `.plat`,
   `.test-item` — была обычными <div> с `onclick`. С клавиатуры они недостижимы
   (Tab о них не знает), для скринридера невидимы (это «группа», а не действие),
   и списки прогонов, снимков, платформ и тестов проходились только мышью.

   Обработчик Enter/Space один на документ, а не по одному на элемент: их сотни,
   они пересоздаются на каждой перерисовке, и вешать на каждый по слушателю
   значит копить их до конца сессии. */
function hit(node,handler,label){
  node.setAttribute('role','button');
  node.setAttribute('tabindex','0');
  if(label)node.setAttribute('aria-label',label);
  node.onclick=handler;
  return node;
}
document.addEventListener('keydown',e=>{
  if(e.key!=='Enter'&&e.key!==' ')return;
  const t=e.target;
  if(!t||!t.getAttribute||t.getAttribute('role')!=='button')return;
  if(t.tagName==='BUTTON'||t.tagName==='A'||t.tagName==='INPUT')return;
  /* Пробел на кнопке прокручивает страницу — ровно то, чего от нажатия не
     ждут. Enter не прокручивает, но и его лучше не отдавать дальше. */
  e.preventDefault();
  t.click();
});
/* Апостроф экранируется наравне с кавычкой: сегодня все атрибуты в шаблонах
   двойные, но одна правка «title='...'» — и дыра открыта, а найти её потом
   можно только целенаправленным поиском. */
const esc=s=>String(s==null?'':s).replace(/[&<>"']/g,
  m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
const fmt=(v,d=2)=>v==null||v===''?'—':Number(v).toFixed(d);
const pct=(v,d=2)=>v==null?'—':Number(v).toFixed(d)+'%';
function fmtInt(n){return n==null?'—':String(Math.round(n)).replace(/\B(?=(\d{3})+(?!\d))/g,' ');}

const state={view:'runs',me:null,runs:[],run:null,comp:null,
  mode:'slide',baselines:{platforms:[],current:''},platform:null,job:null,

  /* project — ЗАКРЕПЛЁННЫЙ проект: тот, про который показывает всё, что ниже
     шапки. Пусто ровно до ответа `/api/auth/me`; сводного режима «все
     проекты» больше нет — он и делал экран нечитаемым, как только проектов
     становилось два. Заполняется и хранится в `project.js`. */
  project:'', projects:[], scopes:[],

  // scope — какой набор эталонов открыт на вкладке «Baselines»:
  // 'global' — собственный набор сервиса, 'project:<key>' — комплект,
  // снятый VisTest для подключённого проекта.
  //
  // Отдельным выбором он больше не является: набор следует за проектом.
  // Здесь лежит уже вычисленное значение (`currentScope()`), чтобы экранам не
  // приходилось знать про правило вывода.
  scope:null,
  /* …кроме одного случая: у подключённого проекта наборов бывает два — свой
     комплект PNG в репозитории и снятый VisTest. Ручной переопределитель
     живёт здесь, виден в шапке и снимается сменой проекта. */
  scopeOverride:null,

  /* Что открыто на «Baselines». Ключ платформы (`linux-chromium-1x-390x844`)
     разобран на две части: движок и размер окна выбираются по отдельности,
     потому что вопросы это разные — «как это выглядит в firefox» и «как это
     выглядит на телефоне». */
  browser:null, viewport:null, blCompare:false,

  runFilter:'all', runLimit:60,
  counts:{}, rig:null};

async function api(p,opts){
  let r;
  try{ r=await fetch(API+p,opts); }
  catch(netErr){ throw new Error('The service is unavailable — check whether `python run.py ui`.'); }
  if(!r.ok){
    if(r.status===401 && !p.startsWith('/api/auth/')){showLogin('Session expired — sign in again');throw new Error('Sign-in required');}
    if(r.status===403){let d='';try{d=(await r.clone().json()).detail||'';}catch{}throw new Error(typeof d==='string'&&d?d:'Not enough rights');}
    /* 503 — сервис отказывается обслуживать инсталляцию без администратора,
       когда запрос пришёл не с петли. Это не «сломалось», а единственное
       состояние, из которого нельзя выйти изнутри интерфейса: пользователя
       ещё нет, значит и войти некем. Показываем это отдельно, иначе человек
       получает форму входа, которую невозможно пройти. */
    if(r.status===503){let d='';try{d=(await r.clone().json()).detail||'';}catch{}blockingNotice(d||'The service is not configured yet');throw new Error(d||'Service unavailable');}
    if(r.status===409){let d=null;try{d=(await r.clone().json()).detail;}catch{}if(d&&typeof d==='object')throw new Error(`${d.error||'Conflict'}\n${d.message||''}`);}
    /* 404 бывает двух совершенно разных видов, и раньше оба показывались как
       «сервис не знает такого роута, у вас старый код». Для отсутствующего
       прогона, снимка или задачи это неправда, и неправда обидная: человек
       идёт перезапускать сервис вместо того, чтобы прочитать «такого прогона
       больше нет». Отличаем по ответу: FastAPI на неизвестный путь отдаёт
       ровно `Not Found`, а роут, который знает про свой объект, объясняет
       словами. */
    if(r.status===404 && p.startsWith('/api/')){
      let d='';try{d=(await r.clone().json()).detail||'';}catch{}
      if(typeof d==='string'&&d&&d!=='Not Found')throw new Error(d);
      throw new Error('The service does not know the route ('+p+'). Looks like an old version of the code is running — restart: python run.py ui');
    }
    let t='';try{t=(await r.clone().json()).detail;}catch{t=await r.text();}
    throw new Error(typeof t==='string'?t:JSON.stringify(t));
  }
  if(r.status===204)return null;
  const ct=r.headers.get('content-type')||'';
  return ct.includes('json')?r.json():r.text();
}

/* «Я всё ещё на том экране, ради которого пошёл в сеть?»

   Каждый экран — это `await`, а адрес за время ожидания меняется: человек
   листает очередь разбора клавишами J/K, жмёт «назад», выбирает снимок в
   палитре. Два запроса при этом идут одновременно, и рисует тот, кто ответил
   ПОСЛЕДНИМ, — а это не тот, кого ждут. Симптом ровно такой, каким его и
   приносят: «нажал ещё раз — открылся предыдущий снимок», «список прогонов
   лёг поверх открытого прогона». Списать это на «подтормаживает» легко, найти
   — почти нет: воспроизводится только на медленной сети.

   Правило одно и короткое: адрес, под которым экран пошёл за данными, — это
   его право рисовать. Изменился, пока ждали, — рисует уже другой, и мешать
   ему нельзя. Ответ при этом не выбрасывается зря: он уже в кеше запросов
   браузера, и возврат назад будет мгновенным. */
function pageGuard(){
  const at=location.hash;
  return ()=>location.hash===at;
}

/* Полноэкранное объяснение вместо формы входа: показывается один раз и не
   перекрывается следующими запросами, которые упрутся в то же самое. */
function blockingNotice(text){
  if($('#blockNotice'))return;
  hideLogin();
  const bg=el('div');bg.id='blockNotice';
  bg.style.cssText='position:fixed;inset:0;background:var(--bg);z-index:220;display:flex;'
    +'align-items:center;justify-content:center;padding:24px';
  const box=el('div','card');box.style.cssText='width:520px;max-width:100%;padding:28px';
  box.innerHTML=`<h3 style="font-size:19px">The service is not ready to be used over the network</h3>
    <p class="muted" style="margin-top:12px;font-size:14px;line-height:1.6;white-space:pre-line">${esc(text)}</p>`;
  bg.append(box);document.body.append(bg);
}

/* ------- toast ------- */
let toastN=0;
function toast(msg,kind){const n=el('div','toast'+(kind?' '+kind:''),esc(msg));$('#toast').append(n);
  const id=++toastN;setTimeout(()=>n.remove(),kind==='err'?6000:3200);return id;}

/* ------- поломка обязана быть слышной -------

   У интерфейса нет сборщика: файлы делят одну область видимости и подключаются
   обычными `<script>`. Отсюда поломка, которой не бывает в собранном коде —
   **тихая**. Достаточно, чтобы один файл приехал из кэша браузера старым, и
   обработчик кнопки зовёт функцию, которой в нём ещё нет. Кнопка нажимается,
   экран цел, консоль человек не открывает — и «ничего не происходит».

   «Ничего не происходит» — худший из возможных ответов: он не отличим ни от
   «задумалось», ни от «не нажалось», и искать по нему нечего. Поэтому любая
   не пойманная ошибка — и синхронная, и из промиса — становится тостом с
   именем файла и строкой. Дальше вопрос звучит уже как «почему в этом файле
   нет этой функции», а на него ответ есть.

   Тост один на минуту на одно и то же место: ошибка в обработчике `mousemove`
   иначе завалит экран собой. */
const seenErrors={};
function reportBreak(what,where){
  const key=String(what)+'@'+where;
  const now=Date.now();
  if(seenErrors[key]&&now-seenErrors[key]<60000)return;
  seenErrors[key]=now;
  toast(what+(where?' — '+where:''),'err');
}
/* Всё, что не решает, увидит ли человек экран, — под `safely`.

   Раньше запуск был прямой цепочкой: `paintUser()`, `applyTeam()`, счётчики,
   стенд, `route()`. Любое исключение в её начале не давало дойти до `route()`
   — то есть до первой отрисовки вообще, — и человек оставался на «Loading…».
   Ровно это и случилось с `applyTeam()`: одна строка про класс, которого нет
   в разметке, гасила весь интерфейс.

   Порядок теперь другой и правило одно: сперва экран, потом украшения. Экран
   рисуется даже тогда, когда счётчики не сосчитались, а шум стенда не
   приехал; поломка при этом не молчит — она уходит в тост через
   `installErrorSurface()`, с именем файла и строкой. */
function safely(what, fn){
  try{
    const out=fn();
    if(out&&typeof out.then==='function')
      return out.catch(e=>{reportBreak(String(e&&e.message||e),what);});
  }catch(e){reportBreak(String(e&&e.message||e),what);}
  return Promise.resolve();
}

function installErrorSurface(){
  window.addEventListener('error',e=>{
    if(e.target&&e.target.tagName==='SCRIPT'){
      /* Файл вообще не загрузился. Всё, что он определял, отсутствует
         целиком, и следующая поломка будет выглядеть случайной. */
      reportBreak('Interface file did not load',
        String(e.target.src||'').split('/').pop());
      return;
    }
    /* Файл и строка — если они есть. Пустое «— :1» на месте имени файла
       выглядит как обрезанное сообщение и уводит от вопроса, а не к нему. */
    const file=String(e.filename||'').split('/').pop();
    reportBreak(String((e.error&&e.error.message)||e.message||'Script error'),
      file?file+(e.lineno?':'+e.lineno:''):'');
  },true);
  window.addEventListener('unhandledrejection',e=>{
    const r=e.reason;
    reportBreak(String((r&&r.message)||r||'Request failed'),'');
  });
}

