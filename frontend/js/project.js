/* VisTest - self-hosted visual regression testing.
 * Copyright (C) 2026 Kirill Kulagin
 * SPDX-License-Identifier: AGPL-3.0-or-later
 *
 * This file is part of VisTest. See LICENSE for the full terms and NOTICE for
 * the trademark and commercial-licensing terms. Removing this header does not
 * remove those obligations.
 */

/* ============================================================== ПРОЕКТ =====
   Проект — один на весь интерфейс, и он закреплён в шапке.

   Раньше «проект» жил в интерфейсе в двух видах и в двух местах:

     · `state.project` — переключатель в шапке со значением «all projects» по
       умолчанию. Им фильтровались прогоны, очередь решений и метрики.
     · `state.scope`   — своя строка «SET» на вкладке «Baselines»: собственный
       набор сервиса или набор подключённого проекта.

   Это ОДНА сущность, разрезанная надвое. Прогоны подключённого проекта лежат
   в истории под его ключом (`external.run_project` → `publish_external_run`
   с `project=project.key`), а эталоны того же проекта — под `project:<тот же
   ключ>`. Держать выборы порознь означало ровно то, на что и жалуются: человек
   выбирает проект в шапке и продолжает видеть чужие эталоны, чужие тесты и
   чужой счётчик в навигации.

   Второе, чего здесь не было: выбор не переживал перезагрузку. `state.project`
   каждый раз начинался с `'*'`, то есть с «показать всё сразу» — с состояния,
   которое и делает экран непонятным, как только проектов становится два.

   Здесь оба выбора сведены в один, а выбор — в `localStorage`. Дальше правило
   короткое: **всё, что показывает экран, показывается про закреплённый
   проект**. Единственное исключение — вкладка «Projects»: это место, где
   проекты подключают, и показывать там один проект значило бы спрятать
   кнопку подключения второго.

   --------------------------------------------------------------------------
   Что такое «проект» в этом файле

   Одна запись сводит три источника, каждый из которых знает про проект своё:

     · `/api/run-projects`      — под каким именем идёт история прогонов;
     · `/api/baselines/scopes`  — где лежат его эталоны и сколько их;
     · `/api/projects`          — как он подключён (путь, адаптер, имя).

   Последний доступен только администратору и только с машины сервиса — его
   отсутствие не должно ломать список, поэтому он необязателен.

   Собственный набор сервиса — тоже проект. Его имя приезжает в `/api/auth/me`
   как `own_project`, и это то же имя, под которым сервис ведёт свою историю
   (`cfg.service.project`). Без него «свой набор» был бы единственным местом,
   где интерфейс не знает, чьё это.
   ========================================================================= */

const PROJ_STORE='vistest.project';

/* localStorage может быть недоступен целиком: приватное окно, запрет на
   хранилище сайта, jsdom без него. Падение здесь унесло бы запуск, а цена
   вопроса — забытый между перезагрузками выбор. */
function prefGet(k){try{return localStorage.getItem(k);}catch{return null;}}
function prefSet(k,v){
  try{if(v==null)localStorage.removeItem(k);else localStorage.setItem(k,v);}catch{}
}

/* Сколько эталонов в наборе — по всем его платформам сразу. */
function scopeTotal(sc){
  return ((sc&&sc.platforms)||[]).reduce((a,p)=>a+(p.count||0),0);
}

/* --------------------------------------------------------------- загрузка --
   Три запроса ОДНОЙ пачкой: они независимы, и цепочка из трёх `await` стоила
   бы три round-trip до первого экрана. Каждый падает сам за себя — список
   проектов, собранный из двух источников вместо трёх, остаётся списком
   проектов; список, не собранный вовсе, оставил бы человека на «Loading…». */
async function loadProjects(){
  const own=((state.me||{}).own_project||'').trim()||'default';
  const [runList,scopeList,connected]=await Promise.all([
    api('/api/run-projects').catch(()=>[]),
    api('/api/baselines/scopes').catch(()=>({scopes:[]})),
    api('/api/projects').catch(()=>null),
  ]);

  const scopes=((scopeList&&scopeList.scopes)||[]).filter(s=>s&&s.scope);
  state.scopes=scopes;

  const map=new Map();
  const at=id=>{
    const key=String(id||'').trim();
    if(!key)return null;
    if(!map.has(key))map.set(key,{
      id:key,label:key,kind:'project',scope:'project:'+key,
      runs:0,baselines:0,lastRun:null,root:'',adapter:'',
      emptyHint:'',setLabel:''});
    return map.get(key);
  };

  /* Наборы эталонов задают костяк: только отсюда известно, что у проекта
     вообще есть набор VisTest, и сколько в нём картинок. */
  scopes.forEach(sc=>{
    const p=at(sc.kind==='global'?own:sc.project_key);
    if(!p)return;
    if(sc.kind==='global'){p.kind='own';p.scope='global';}
    else p.scope=sc.scope;
    p.baselines=scopeTotal(sc);
    p.setLabel=sc.label||'';
    p.emptyHint=sc.empty_hint||'';
  });

  /* Подключение даёт человеческое имя и путь. Ключ подключения — то же самое
     имя, под которым идёт история: иначе строки не сошлись бы. */
  ((connected&&connected.projects)||[]).forEach(pr=>{
    const p=at(pr.key||pr.name);
    if(!p)return;
    p.kind='project';
    p.label=pr.name||pr.key;
    p.root=pr.root||'';
    p.adapter=pr.adapter||'';
  });

  /* История — последней: проект, у которого есть прогоны, обязан попасть в
     список, даже если он не подключён и не имеет набора эталонов. Так в него
     попадают прогоны, приехавшие из чужого CI. */
  (runList||[]).forEach(r=>{
    const p=at(r.name);
    if(!p)return;
    p.runs=r.runs||0;
    p.lastRun=r.last_run||null;
  });

  /* Собственный проект существует всегда, даже пустой: на свежей инсталляции
     он единственный, и пустой список означал бы шапку без единого пункта. */
  const mine=at(own);
  if(mine){
    mine.kind='own';mine.scope='global';
    if(!mine.setLabel)mine.setLabel='VisTest — own set';
  }

  state.projects=[...map.values()].sort(projectOrder);
  selectProject(pickProject(state.projects),{quiet:true});
  return state.projects;
}

/* Обновление чисел уже собранного списка. Порядок при этом НЕ пересчитывается:
   меню, которое переставляет свои пункты раз в двенадцать секунд, — это меню,
   в которое нельзя попасть, не перечитав его целиком. Порядок задаётся один
   раз при входе, числа живут. */
function refreshProjectCounts(runList,scopeList){
  const list=state.projects||[];
  if(!list.length)return;
  (runList||[]).forEach(r=>{
    const p=list.find(x=>x.id===r.name);
    if(p){p.runs=r.runs||0;p.lastRun=r.last_run||null;}
  });
  const own=((state.me||{}).own_project||'').trim();
  ((scopeList&&scopeList.scopes)||[]).forEach(sc=>{
    const id=sc.kind==='global'?own:sc.project_key;
    const p=list.find(x=>x.id===id);
    if(p)p.baselines=scopeTotal(sc);
  });
}

/* Порядок в меню: сначала то, чем пользуются. Прогоны — лучший признак
   «здесь идёт работа», эталоны — второй, имя — последнее, чтобы порядок был
   устойчив и список не перетасовывался между заходами. */
function projectOrder(a,b){
  if(a.runs!==b.runs)return b.runs-a.runs;
  if(a.baselines!==b.baselines)return b.baselines-a.baselines;
  return String(a.label).localeCompare(String(b.label));
}

/* Какой проект открыть, если человек ещё не выбирал — или выбрал тот, которого
   больше нет: проект отключили, а запись в браузере осталась.

   Правило то же, что у `nonEmptyScope`: сохранённый выбор не переписывается,
   пока он существует. Молча подменить его «более полным» — перекладывание
   экрана под ногами, и заметить это человек может только по чужим числам. */
function pickProject(list){
  const all=list||[];
  if(!all.length)return null;
  const saved=prefGet(PROJ_STORE);
  const kept=all.find(p=>p.id===saved);
  if(kept)return kept;
  /* Ни разу не выбирали. Открываем тот, где есть что смотреть: с прогонами,
     иначе с эталонами, иначе первый. Порядок уже такой. */
  return all.find(p=>p.runs||p.baselines)||all[0];
}

function currentProject(){
  return (state.projects||[]).find(p=>p.id===state.project)||null;
}

/* Имя проекта в надзаголовке экрана.

   Оно повторяет то, что стоит в шапке, и это повтор намеренный: надзаголовок
   отвечает на «про что этот экран», и «HEALTH · 30 DAYS» без имени проекта на
   установке с двумя наборами — это заголовок, под которым может быть что
   угодно. Одно слово дешевле, чем привычка сверяться с шапкой. */
function projectLabel(){
  const p=currentProject();
  return (p&&p.label)||state.project||'—';
}

/* Подпись набора эталонов: что именно сейчас показано и чьё оно. */
function scopeLabel(scope){
  const s=scope||currentScope();
  const found=(state.scopes||[]).find(x=>x.scope===s);
  if(found&&found.label)return found.label;
  return s==='global'?'VisTest — own set':String(s||'');
}

/* Набор эталонов текущего проекта.

   По умолчанию он следует за проектом — ради этого всё и затевалось. Но у
   подключённого проекта наборов бывает два: свой комплект PNG в репозитории и
   снятый VisTest. Поэтому ручной переопределитель остаётся — он живёт на
   вкладке «Baselines», виден в шапке и держится ровно до смены проекта. */
function currentScope(){
  if(state.scopeOverride)return state.scopeOverride;
  const p=currentProject();
  return (p&&p.scope)||'global';
}

/* Выбор проекта — единственная точка входа. Здесь же гасится всё, что от
   проекта зависит и что иначе доживёт до следующего экрана чужим:

     · переопределённый набор эталонов — он был выбран для ДРУГОГО проекта;
     · платформа, браузер, размер окна — набор платформ у другого проекта
       другой, и прибитая `state.platform` показала бы пустой экран;
     · кеш палитры и очередь разбора — они собраны по прошлому проекту. */
function selectProject(p,opts){
  opts=opts||{};
  const id=p&&p.id;
  if(!id)return;
  const changed=state.project!==id;
  state.project=id;
  if(changed){
    state.scopeOverride=null;
    state.platform=null;
    state.browser=null;
    state.viewport=null;
    state.blFilter='';
    state.blCompare=false;
    state.triage=null;
    state.queue=null;
    try{palData=null;}catch{}
  }
  state.scope=currentScope();
  prefSet(PROJ_STORE,id);
  paintProjectPill();
  if(opts.quiet)return;
  if(typeof refreshCounts==='function')refreshCounts();
  if(typeof route==='function')route();
}

/* Ручной переопределитель набора — с той же дисциплиной: он именованный, виден
   на экране и снимается одним щелчком. */
function setScopeOverride(scope){
  const p=currentProject();
  state.scopeOverride=(!scope||(p&&scope===p.scope))?null:scope;
  state.scope=currentScope();
  state.platform=null;state.browser=null;state.viewport=null;
  paintProjectPill();
  if(typeof SCREENS!=='undefined'&&SCREENS.baselines&&state.view==='baselines')
    SCREENS.baselines();
}

/* ------------------------------------------------------------- пилюля -----
   Что стоит в шапке и почему именно это.

   Раньше здесь было имя и больше ничего: «all projects ▾». Имя без подписи не
   отвечает на вопрос, который и делает шапку непонятной, — «это фильтр или
   заголовок?». Слово PROJECT моноширинным перед именем отвечает: это выбор, и
   он действует на всё, что ниже. Так же подписаны переключатели внутри
   экранов, и словарь остаётся общим. */
function paintProjectPill(){
  const pill=$('#projPill');if(!pill)return;
  const p=currentProject();
  const nameEl=$('#projName');
  if(nameEl)nameEl.textContent=p?p.label:'no projects';
  const metaEl=$('#projMeta');
  if(metaEl){
    /* Переопределённый набор обязан быть виден в шапке, а не только на той
       вкладке, где его включили: иначе через один экран человек считает чужие
       эталоны своими. */
    const over=state.scopeOverride&&p&&state.scopeOverride!==p.scope;
    metaEl.textContent=over?'other set':'';
    metaEl.hidden=!over;
  }
  const many=(state.projects||[]).length>1;
  /* Единственный проект — не выбор: стрелка убирается, наведение перестаёт
     подсвечивать. `disabled` тут не годится — отключённая кнопка в части
     браузеров не получает событий мыши вовсе, а вместе с ними исчезает и
     подсказка, ради которой пилюля наполовину и существует. */
  pill.classList.toggle('single',!many);
  tip(pill,p
    ? `Everything below is about «${p.label}»: runs, decisions, baselines, `
      +'tests and metrics. The choice is remembered between visits.'
      +(many?' Click to switch.':' It is the only project here.')
    : 'No projects yet — connect a test suite on the «Projects» tab.');
}

/* Меню выбора. Строка отвечает на «что это за проект и есть ли там что
   смотреть»: выбор из двух одинаковых слов ничем не лучше прежнего
   «all projects». */
function projectSwitcher(){
  closeMenus();
  const m=el('div','menu pj-menu');
  m.style.top='45px';m.style.left='8px';
  m.innerHTML='<div class="mi-head">PROJECT · EVERYTHING BELOW FOLLOWS THIS</div>';
  const list=state.projects||[];
  if(!list.length)m.append(el('div','mi off','Nothing connected yet'));
  list.forEach(p=>{
    const b=el('button','pj-mi'+(p.id===state.project?' on':''));
    const kind=p.kind==='own'?'own suite':'connected';
    b.innerHTML=`<span class="pj-dot" aria-hidden="true"></span>
      <span class="pj-body">
        <span class="pj-t">${esc(p.label)}</span>
        <span class="pj-s mono">${esc(kind)}${p.root?' · '+esc(p.root):''}</span>
      </span>
      <span class="pj-n mono">
        <span>${fmtInt(p.runs)} runs</span>
        <span class="faint">${fmtInt(p.baselines)} baselines</span>
      </span>`;
    b.onclick=()=>{closeMenus();selectProject(p);};
    m.append(b);
  });
  m.append(mkDiv());
  m.append(mkItem('Connect or edit projects →',()=>{location.hash='#/projects';}));
  document.body.append(m);stopClose(m);
  return m;
}
