/* VisTest - self-hosted visual regression testing.
 * Copyright (C) 2026 Kirill Kulagin
 * SPDX-License-Identifier: AGPL-3.0-or-later
 *
 * This file is part of VisTest. See LICENSE for the full terms and NOTICE for
 * the trademark and commercial-licensing terms. Removing this header does not
 * remove those obligations.
 */

/* Каркас: перехват необработанных ошибок, модалки, задачи с живым логом,
   навигация, переключатель проектов, счётчики в боковой панели.

   То, что живёт вокруг экранов и не принадлежит ни одному из них. */
/* Глобальных обработчиков ошибок здесь больше нет — они в `core.js`,
   в `installErrorSurface()`, и это единственное место.

   Здесь стояла вторая пара, и вреда от неё было больше, чем пользы: её
   `preventDefault()` на `unhandledrejection` гасил штатный вывод браузера
   «Uncaught (in promise)» — то есть ровно ту строчку со стеком, за которой
   человек и открывает консоль. Оставалось `console.warn` без стека и тост из
   `core.js` — и поиск причины начинался с восстановления того, что браузер
   уже написал бы сам. */

/* ------- modal windows ------- */
function openModal(node,opts={}){
  closeModal();
  const bg=el('div','modal-bg');bg.id='modalBg';
  const m=el('div','modal'+(opts.wide?' wide':''));
  m.append(node);bg.append(m);
  bg.onclick=e=>{if(e.target===bg)closeModal();};
  document.body.append(bg);
  document.addEventListener('keydown',escClose);
  return m;
}
function closeModal(){const b=$('#modalBg');if(b)b.remove();document.removeEventListener('keydown',escClose);}
function escClose(e){if(e.key==='Escape')closeModal();}

/* ------- task with a live log in a modal ------- */
function logline(level,text){const d=el('div','l-'+(level||'info'));d.textContent=text;return d;}
async function cancelJob(job_id){
  try{await api(`/api/jobs/${job_id}/cancel`,{method:'POST'});toast('Cancellation requested');}
  catch(e){toast(String(e.message||e),'err');}
}
async function runJobLog(starter,opts={}){
  const title=opts.title||'Task';
  const wrap=el('div');
  wrap.innerHTML=`<div class="mhead"><div><h3>${esc(title)}</h3><div class="msub">${esc(opts.sub||'')}</div></div><button class="mclose" aria-label="Close">×</button></div>`;
  const bar=el('div','jobbar');bar.innerHTML='<span class="spin"></span> starting…';
  const logEl=el('div','joblog');wrap.append(bar,logEl);
  const m=openModal(wrap);$('.mclose',wrap).onclick=closeModal;
  let job_id;
  try{const r=await starter;job_id=r&&(r.job_id||r.id);
    if(!job_id){bar.textContent='done';if(opts.then)opts.then(r);return r;}}
  catch(e){bar.innerHTML='';logEl.append(logline('error',String(e.message||e)));return null;}

  /* Отмена. Роут `POST /api/jobs/{id}/cancel` есть с самого начала, проверяет
     владельца — и не вызывался из интерфейса ни разу. Прогон чужого pytest
     идёт минутами; единственным способом его прекратить был перезапуск
     сервиса. Ровно та же болезнь, что была у «удалить прогон». */
  const stop=el('button','btn sm','Cancel the task');
  stop.style.marginLeft='auto';
  stop.onclick=()=>{stop.disabled=true;cancelJob(job_id);};
  bar.append(stop);

  let offset=0,last=null;
  while(true){
    await new Promise(r=>setTimeout(r,700));
    /* Окно закрыли — опрос прекращается. Раньше цикл продолжал ходить в сеть
       раз в 700 мс до конца сессии, и таких циклов накапливалось столько,
       сколько задач человек за день запустил. */
    if(!document.body.contains(m))return last;
    /* «Connection to the task lost» стояло здесь на ЛЮБУЮ ошибку опроса — и
       чаще всего врало: связь была цела, а задача исчезала вместе с процессом
       при перезапуске сервиса. Теперь задача переживает перезапуск и сама
       объясняет, что с ней случилось; сюда попадает только то, что осталось. */
    let j;try{j=await api(`/api/jobs/${job_id}?since=${offset}`);}
    catch(e){bar.innerHTML='<span class="l-warn">'+esc(String(e.message||e))+'</span>';break;}
    (j.log||[]).forEach(L=>logEl.append(logline(L.level,L.text)));
    logEl.scrollTop=logEl.scrollHeight;offset=j.log_offset??offset;last=j;
    if(j.status!=='running'&&j.status!=='queued'){
      bar.innerHTML=j.status==='done'?'<span class="l-ok">done</span>':j.status==='cancelled'?'canceled':'<span class="l-error">finished with an error</span>';
      if(j.status!=='done'&&j.error)logEl.append(logline('error',j.error));
      break;
    }
  }
  if(opts.then)opts.then(last);
  return last;
}

/* ============================================================ navigation == */
/* Навигация сгруппирована по вопросу, а не по объекту.

   Плоский список из семи пунктов заставлял держать в голове, где что лежит.
   Группы отвечают на вопросы, которые человек и задаёт: «что от меня требуется»
   (REVIEW), «что у нас есть» (LIBRARY), «можно ли этому верить» (HEALTH), «как
   это настроено» (SETUP). Порядок групп — порядок частоты обращения. */
const ICONS={
  decisions:'<path d="M2 8.5h3l1.5 2.5h3L14 8.5M2 8.5l2-5h8l2 5v4H2z"/>',
  runs:'<path d="M2.5 4h11M2.5 8h11M2.5 12h7"/>',
  diff:'<path d="M5.5 3.5v9M10.5 3.5v9M2.5 6h6M7.5 10h6"/>',
  baselines:'<rect x="2.5" y="3" width="11" height="10"/><path d="M2.5 10l3-2.5 2.5 2 2.5-1.5 3 2"/>',
  tests:'<path d="M5.5 4.5L2.5 8l3 3.5M10.5 4.5l3 3.5-3 3.5M9 3l-2 10"/>',
  dash:'<path d="M2.5 13V7M6.5 13V3M10.5 13V9M14.5 13V5"/>',
  doctor:'<path d="M2 8h3l1.5 4 3-8L13 8h1"/>',
  projects:'<path d="M2.5 5.5h4L8 7h5.5v6h-11z"/>',
  settings:'<circle cx="8" cy="8" r="2.2"/><path d="M8 1.8v2.2M8 12v2.2M1.8 8h2.2M12 8h2.2M3.6 3.6l1.5 1.5M11 11l1.5 1.5M12.4 3.6L10.9 5.1M5.1 11l-1.5 1.5"/>'
};
/* Подсказка у пункта объясняет, на какой вопрос он отвечает, а не пересказывает
   его название. «Runs — список прогонов» не стоит того, чтобы всплывать; «чем
   очередь решений отличается от списка прогонов» — стоит, потому что именно
   об этом спрашивают в первый день. */
const NAV=[
  {grp:'REVIEW'},
  {v:'decisions',label:'Decisions',count:'failed',
   tip:'Distinct causes, not snapshots. One answer here closes every snapshot behind that cause.'},
  {v:'runs',label:'Runs',count:'runs',
   tip:'Everything the engine has checked, newest first. Open one to see what it found.'},
  /* «Что сломала эта ветка» — вопрос, который задают перед мержем, и до сих
     пор экран с ответом можно было найти только изнутри прогона. В навигации
     его не было, значит для большинства его не было вовсе. */
  {v:'diff',label:'What changed',count:'broke',
   tip:'Two runs side by side, split by how each snapshot’s verdict moved. Answers «what did this branch break», which a single run cannot.'},
  {grp:'LIBRARY'},
  {v:'baselines',label:'Baselines',count:'baselines',
   tip:'The pictures every run is compared against. Accepting a failure rewrites one of these.'},
  {v:'tests',label:'Tests',count:'tests',
   tip:'Plain pytest files — ours are editable here, files from connected projects are read-only.'},
  {grp:'HEALTH'},
  {v:'dash',label:'Trust',count:'trust',
   tip:'How often a red run turns out to be nothing. Above 20 % people start accepting without looking.'},
  {v:'doctor',label:'Environment',count:'noise',
   tip:'How many failures an unchanged page produces on this stand. Everything else is only as good as this number.'},
  {grp:'SETUP'},
  {v:'projects',label:'Projects',count:'projects',
   tip:'Suites connected from your own repositories. Their code is never edited.'},
  {v:'settings',label:'Settings',count:'requests',
   tip:'Threshold, retention, notifications, and who has access.'},
];
/* Пропустить навигацию: восемь пунктов, одинаковых на каждом экране. */
$('#skipLink').onclick=()=>{
  const box=$('#screen');
  if(!box)return;
  box.setAttribute('tabindex','-1');   /* иначе фокус туда не встанет */
  box.focus();
};

function buildNav(){
  const nav=$('#nav');nav.innerHTML='';
  NAV.forEach(n=>{
    if(n.grp){nav.append(el('div','grp',esc(n.grp)));return;}
    const a=el('a');a.dataset.v=n.v;a.href='#/'+n.v;
    if(n.tip)a.setAttribute('data-tip',n.tip);
    a.innerHTML=`<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[n.v]}</svg><span class="lbl">${esc(n.label)}</span><span class="count" data-count="${n.count||''}"></span>`;
    /* Схлопнутый сайдбар прячет подпись — тогда название пункта живёт только
       в подсказке и в имени для скринридера, иначе от пункта остаётся
       безымянная иконка. */
    a.setAttribute('aria-label',n.label);
    a.onclick=()=>{location.hash='#/'+n.v;};
    nav.append(a);
  });
}
function paintCounts(){
  const c=state.counts;
  $$('#nav a').forEach(a=>{
    const badge=$('.count',a),key=badge.dataset.count;
    badge.className='count';badge.textContent='';
    /* Оранжевый бейдж = «требует вашего ответа». Он стоит ровно на двух
       пунктах, и это не украшение: если им пометить ещё и число эталонов,
       словарь цвета сломается и человек перестанет замечать оба. */
    /* Число рядом с пунктом объясняется подсказкой, а не заголовком браузера:
       «23» без единицы измерения — это не сообщение, а загадка, и ответ на неё
       должен приходить сразу, а не через секундную паузу. Подсказка уточняет
       смысл пункта, поэтому она собирается из объяснения самого пункта и того,
       что означает конкретное число. */
    const base=NAV.find(n=>n.v===a.dataset.v);
    const say=extra=>a.setAttribute('data-tip',
      extra?extra+(base&&base.tip?' — '+base.tip:''):(base&&base.tip)||'');
    if(key==='failed'){
      const v=c.failed||0;badge.textContent=v||'';
      if(v)badge.classList.add('alert');
      say(v?`${v} snapshot${v===1?'':'s'} nobody has answered yet.`
           :'Nothing is waiting for a decision.');
    }else if(key==='requests'){
      const v=c.requests||0;badge.textContent=v||'';
      if(v)badge.classList.add('alert');
      say(v?`${v} access request${v===1?'':'s'} waiting for you.`:'');
    }else if(key==='broke'){
      /* Счётчик у «What changed» — сколько снимков позеленело→покраснело в
         последнем прогоне относительно предыдущего. Пусто, пока сравнивать
         не с чем: ноль здесь означал бы «проверили, всё цело». */
      const v=c.broke;
      if(v==null)return;
      badge.textContent=v||'';
      if(v)badge.classList.add('bad');
      say(v?`${v} snapshot${v===1?'':'s'} went from green to red in the newest run.`:'');
    }else if(key==='trust'){
      /* Доля ложных падений — единственное число, которое стоит показывать
         прямо в навигации: оно отвечает, стоит ли верить всему остальному. */
      if(c.trust==null)return;
      badge.textContent=Math.round(c.trust*100)+'%';
      badge.classList.add(c.trust>0.2?'warn':'ok');
      say(`${Math.round(c.trust*100)}% of reviewed failures turned out to be false.`);
    }else if(key==='noise'){
      if(c.noise==null)return;
      badge.textContent=(c.noise*100).toFixed(1)+'%';
      badge.classList.add(c.noise>=0.05?'warn':'ok');
      say(`An unchanged page produces ${(c.noise*100).toFixed(1)}% failures on this stand.`);
    }else if(key){
      badge.textContent=c[key]!=null?c[key]:'';
    }
  });
}
/* Крошка набрана моноширинным в нижнем регистре — это адрес, а не заголовок:
   заголовок у страницы свой, и дублировать его в шапке значит спорить с ним. */
const VIEW_TITLES={decisions:'decisions',runs:'runs',baselines:'baselines',
  tests:'tests',dash:'trust',doctor:'environment',projects:'projects',
  settings:'settings',compare:'decisions / triage',snapshot:'baselines / snapshot',
  diff:'runs / what changed'};
/* Какой пункт подсвечивается, когда экран — продолжение другого. */
/* `diff` отсюда убран: у него теперь свой пункт в навигации, и подсвечивать
   вместо него «Runs» значило бы показывать человеку, что он находится не там,
   где находится. */
const NAV_OF={compare:'decisions',snapshot:'baselines'};
function setActiveNav(v){
  /* `aria-current` рядом с классом: подсветка говорит «вы здесь» глазами, а
     скринридеру до этого не говорил никто. */
  $$('#nav a').forEach(a=>{
    const on=a.dataset.v===v;
    a.classList.toggle('active',on);
    if(on)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');
  });
}

const SCREENS={};/* filled by screen modules below */
function route(){
  const jm=location.hash.match(/^#\/join\/([^/]+)/);
  if(jm){renderJoin(jm[1]);return;}
  claimLeftCheck();
  if(state.me)presenceBeat();
  const h=location.hash.replace(/^#\/?/,'')||'decisions';
  /* Аргумент — ВСЁ после первого слэша, а не второй сегмент. Раньше стояло
     `h.split('/')` с разбором в две переменные, и экран, которому нужно больше
     одного значения, выразить было нельзя. Имя снимка при этом само содержит
     слэш (`shop.example/login.png`), так что тут нужен не второй сегмент, а
     хвост целиком. Для `#/compare/12` и `#/tests/a.py` поведение прежнее. */
  const cut=h.indexOf('/');
  const seg=cut<0?h:h.slice(0,cut);
  const arg=cut<0?undefined:h.slice(cut+1);
  const v=SCREENS[seg]?seg:'decisions';
  /* Мигание гасилось только внутри экрана сравнения. Уход отсюда на «Runs» в
     режиме «Blink» оставлял setInterval, который до конца сессии дёргал `src`
     у выброшенного из документа <img>. */
  if(v!=='compare'&&blinkTimer){clearInterval(blinkTimer);blinkTimer=null;}
  state.view=v;
  $('#crumb').textContent=VIEW_TITLES[v]||v;
  setActiveNav(NAV_OF[v]||v);
  closeMenus();
  SCREENS[v](arg);
}
window.addEventListener('hashchange',route);

/* --------- project switcher --------- */
$('#projPill').onclick=async e=>{
  closeMenus();e.stopPropagation();
  const m=el('div','menu');m.style.top='58px';m.style.left='26px';
  m.innerHTML='<div class="mi-head">PROJECT</div>';
  m.append(mkProj('*','all projects'));
  try{(await api('/api/run-projects')).forEach(p=>m.append(mkProj(p.name,`${p.name}`,p.runs)));}catch{}
  document.body.append(m);stopClose(m);
};
function mkProj(val,label,runs){
  const b=el('button',null,`${esc(label)}${runs!=null?` <span class="faint mono" style="margin-left:auto;font-size:11px">${runs}</span>`:''}`);
  b.style.justifyContent='space-between';
  if(val===state.project)b.style.fontWeight='700';
  b.onclick=()=>{closeMenus();state.project=val;$('#projName').textContent=label;palData=null;refreshCounts();route();};
  return b;
}

/* --------- sidebar counts + rig --------- */
/* Счётчики боковой панели — ОДНОЙ пачкой, а не семью запросами подряд.

   Здесь стояла цепочка из семи `await` друг за другом: каждый ждал предыдущий,
   хотя ни один не зависит от его ответа. На локальной машине это незаметно, а
   через VPN до сервера в другом городе — семь round-trip по 150 мс, то есть
   секунда на каждое обновление. И обновление это не разовое: `liveTick` зовёт
   `refreshCounts()` раз в двенадцать секунд, пока вкладка открыта, —
   получалось около тридцати пяти запросов в минуту на человека, который просто
   оставил VisTest открытым.

   Ответы независимы, значит и запросы независимы. Каждый по-прежнему падает
   сам за себя: недоступный роут гасит свой бейдж, а не всю панель. */
const quiet=p=>p.catch(()=>null);
async function refreshCounts(){
  const scope=state.project;
  const [c,m,rp,sc,pj,t,pend]=await Promise.all([
    /* Бейдж у «Runs» читается как «сколько ждёт меня». Стояло же там поле
       `failed` САМОГО СВЕЖЕГО прогона: последний прогон зелёный — бейдж пуст,
       сколько бы падений ни лежало неразобранными позади. Теперь это число
       считает бэкенд по всей истории, вместе со счётчиками фильтров: считать
       их на клиенте по загруженной странице значило показывать «3» там, где
       триста. */
    quiet(api('/api/runs/counts?project='+encodeURIComponent(scope))),
    // Доля ложных падений в навигации. Метрика по одному проекту; при «всех
    // проектах» смешивать её нельзя — доверие к красному у каждого набора своё.
    scope!=='*'
      ? quiet(api('/api/metrics/summary?project='+encodeURIComponent(scope)))
      : Promise.resolve(null),
    // Ключи проектов, у которых есть прогоны: по ним настраивается порог на
    // проект. Берём отсюда, а не из /api/projects — тот требует роль admin и,
    // по умолчанию, запроса с машины сервиса.
    quiet(api('/api/run-projects')),
    /* Счётчик в навигации — по ВСЕЙ установке, а не по текущему набору.
       `/api/baselines/detail` без параметров отвечает про собственный набор
       сервиса, и рядом с подключённым проектом на одиннадцать эталонов в меню
       стоял ноль. Число в навигации отвечает на «есть ли у меня эталоны
       вообще», а не на «что сейчас открыто». */
    quiet(api('/api/baselines/scopes')),
    quiet(api('/api/projects')),
    /* Тесты — свои И подключённых проектов: в меню стоял ноль у человека,
       у которого одиннадцать тестов лежат в подключённом репозитории. */
    quiet(api('/api/tests')),
    // Только администратору: остальным этот роут ответит 403, и поймать его
    // молча здесь правильнее, чем показывать бейдж, которого для них нет.
    can('admin') ? quiet(api('/api/users/pending')) : Promise.resolve(null),
  ]);

  if(c){state.runCounts=c;
        state.counts.failed=c.unreviewed||0;
        state.counts.runs=c.all||0;}
  state.counts.trust=m?(m.totals||{}).false_fail_rate:null;
  if(rp)state.projectKeys=rp.map(p=>p.name).filter(Boolean);
  if(sc)state.counts.baselines=(sc.scopes||[]).reduce((a,x)=>a
    +(x.platforms||[]).reduce((n,p)=>n+(p.count||0),0),0);
  if(pj)state.counts.projects=(pj.projects||[]).length;
  if(t)state.counts.tests=(t.tests||[]).length+(t.project_tests||[]).length;
  if(pend)state.counts.requests=(pend.pending||[]).length;
  paintCounts();
}
async function loadRig(){
  try{const env=await api('/api/doctor/env');
    $('#rigEnv').textContent=(env.docker?'docker · ':'')+(env.platform_key||'—');
    $('#rigDot').style.background=env.editable===false?'#B8760B':'#3FB768';
  }catch{}
  try{const d=await api('/api/doctor/last');const rp=d&&d.report;
    if(rp){const ff=(rp.false_fail!=null?rp.false_fail:rp.noise_ratio);
      state.counts.noise=ff;
      /* «когда мерили» стоит рядом с числом не для красоты: замер
         полугодовой давности описывает другой стенд, а выглядит как факт. */
      $('#rigNoise').textContent='noise '+(ff*100).toFixed(1)+'%'
        +(d.mtime?' · measured '+timeAgo(d.mtime*1000):'');
      $('#rigDot').style.background=ff<0.05?'var(--pass)':'var(--warn)';
      paintCounts();}
  }catch{}
}

