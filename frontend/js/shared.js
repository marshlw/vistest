/* Мелочи, которыми пользуются несколько экранов: лайтбокс, заглушки, `runJob`.

   Файл маленький и таким должен остаться. Всё, что нужно ровно одному экрану,
   живёт в этом экране — иначе «shared» за полгода превращается в свалку, куда
   кладут, чтобы не думать, где ему место. */
function lightbox(src){const b=el('div','lightbox');b.append(el('img'));$('img',b).src=src;b.onclick=()=>b.remove();document.body.append(b);}
function missingArtifact(url){const d=el('div','empty','Artifact unavailable'+(url?`<div class="mono faint" style="font-size:11px;margin-top:6px">${esc(url)}</div>`:'')+'<div class="faint" style="font-size:12px;margin-top:6px">For cross-machine runs images may not be saved.</div>');return d;}
function skeleton(){const d=el('div');d.style.cssText='padding:14px';d.innerHTML='<div style="height:12px;width:60%;background:#E4E1DA;border-radius:6px;margin-bottom:10px"></div><div style="height:12px;width:80%;background:#E9E6DF;border-radius:6px;margin-bottom:10px"></div><div style="height:12px;width:45%;background:#E9E6DF;border-radius:6px"></div>';return d;}
/* Ожидание: полосы на месте будущих строк вместо кружка посреди пустоты.

   Кружок отвечает «идёт загрузка» и не отвечает «загрузка чего»; за ним
   страница ещё и прыгает, когда данные приезжают и занимают своё место.
   Полосы отвечают на оба вопроса сразу и держат высоту. */
function loadingScreen(rows){
  const n=rows||6;
  let ln='';
  for(let i=0;i<n;i++)ln+='<div class="ln"></div>';
  $('#screen').innerHTML='<div class="page"><div class="skel" role="status" '
    +'aria-live="polite" aria-label="Loading">'+ln+'</div></div>';
}
/* Экран ошибки — это тупик, если из него некуда пойти.

   Здесь была одна фраза «Could not load» и текст ошибки моноширинным.
   Ошибки тут бывают ровно двух родов: «сервис не отвечает» (тогда помогает
   повтор — сеть моргнула, сервис перезапускался) и «такого объекта больше
   нет» (тогда помогает уход в список). Обе кнопки стоят всегда: угадывать род
   ошибки по её тексту — это разбор чужих строк, который разойдётся с
   бэкендом при первой же правке формулировки. */
function errScreen(e){
  const box=$('#screen');box.innerHTML='';
  const page=el('div','page narrow');box.append(page);
  const card=el('div','panel');
  const body=el('div','empty');
  body.innerHTML='<b>Could not load</b>'
    +`<div class="what mono faint" style="white-space:pre-line">${esc(String(e&&e.message||e))}</div>`;
  const acts=el('div','acts');
  const again=el('button','btn dark','Try again');
  tip(again,'Repeats the same request. Worth one press: a service restart and a '
    +'blinking network both look exactly like this.');
  again.onclick=()=>route();
  const back=el('button','btn','Go to runs');
  back.onclick=()=>{location.hash='#/runs';};
  acts.append(again,back);
  body.append(acts);card.append(body);page.append(card);
}

async function runJob(starter,opts={}){
  opts=opts||{};let job_id;
  try{const r=await starter;job_id=r&&(r.job_id||r.id);if(!job_id){if(r&&r.queued===false)return r;return r;}}
  catch(e){toast(String(e.message||e),'err');throw e;}
  const tid=toast((opts.title||'Task')+' — started…');
  let offset=0,last=null,waited=false;
  /* У опроса есть край, и он не декоративный.

     Здесь стояло `while(true)` без единого выхода, кроме ответа сервера.
     Задача, застрявшая в очереди (общий предел занят, или тот же проект
     гоняется), опрашивалась раз в 700 мс до конца сессии — а сессия у людей
     живёт днями. Каждая такая кнопка оставляла за собой вечный цикл, они
     копились за день, и к вечеру вкладка стучала в сервис несколько раз в
     секунду без единого действия человека.

     Край взят с запасом от серверного `VISTEST_JOB_TIMEOUT_S` (час по
     умолчанию): задача, которая столько идёт, будет закрыта сторожем на
     сервере, и опрашивать её дальше уже нечего. Молчать при этом нельзя —
     иначе кнопка снова заканчивается ничем. */
  const until=Date.now()+2*3600*1000;
  while(true){
    await new Promise(r=>setTimeout(r,700));
    if(Date.now()>until){
      toast((opts.title||'Task')+' — no answer for two hours; open «Runs» to '
        +'see how it ended','err');
      break;
    }
    /* Молчаливый `break` означал, что запущенная кнопкой задача могла
       закончиться ничем, и человек об этом не узнавал вовсе. */
    let j;try{j=await api(`/api/jobs/${job_id}?since=${offset}`);}
    catch(e){toast((opts.title||'Task')+': '+String(e.message||e),'err');break;}
    last=j;offset=j.log_offset??offset;
    // «queued» — задача принята и ждёт своей очереди (тот же проект уже
    // гоняется, либо занят общий предел). Раньше она попадала в ветку ошибки:
    // человек получал красный тост через секунду после нажатия, хотя прогон
    // спокойно шёл дальше в фоне. Отсюда и ощущение «запуск очень быстрый».
    if(j.status==='queued'){
      if(!waited){waited=true;toast((opts.title||'Task')+' — in the queue: another run of this project is finishing');}
      continue;
    }
    if(j.status!=='running'){
      if(j.status==='done')toast((opts.title||'Task')+' — done',(opts.okKind||'ok'));
      else if(j.status==='cancelled')toast((opts.title||'Task')+' — canceled');
      else toast((opts.title||'Task')+': '+(j.error||'error'),'err');
      break;
    }
  }
  if(opts.then)opts.then(last);
  return last;
}


/* ------- то, чем пользуется больше одного экрана -------

   Склонение, «сколько времени назад» и длительность прогона нужны очереди
   решений, списку прогонов и разбору сравнения. Каждая из этих функций
   когда-то жила внутри одного экрана и вызывалась из соседнего — так уже
   ломалась вкладка «Runs»: `plural is not defined` роняла отрисовку всего
   списка, и экран просто не дорисовывался, без единой ошибки на виду. */
function plural(n,one,few,many){
  return n%10===1&&n%100!==11?one:(n%10>=2&&n%10<=4&&(n%100<10||n%100>=20)?few:many);
}
function timeAgo(ts){
  if(!ts)return '—';let d=new Date(ts);if(isNaN(d))return String(ts);
  const s=(Date.now()-d.getTime())/1000;
  if(s<90)return 'just now';if(s<5400)return Math.round(s/60)+' min ago';
  if(s<172800)return Math.round(s/3600)+' h ago';return Math.round(s/86400)+' days ago';
}
function fmtDur(a,b){
  if(!a||!b)return '—';
  const d=(new Date(b)-new Date(a))/1000;
  if(isNaN(d)||d<0)return '—';
  return Math.floor(d/60)+':'+String(Math.round(d%60)).padStart(2,'0');
}


/* ------- общее для нескольких экранов -------

   Функция, которую зовут с двух экранов, живёт здесь, а не на одном из
   них. Причина не в чистоте: у интерфейса нет сборщика, файлы делят одну
   область видимости, и вызов через границу экрана держится только на том,
   что оба файла доехали. Достаточно одному приехать из кэша старым — и
   кнопка на соседнем экране перестаёт делать что бы то ни было, молча.
   Здесь же место общего кода объявлено, и тест это стережёт. */

/* Было в screen-decisions.js. */
function sectionHead(title,note){
  const d=el('div','sect');
  d.innerHTML=`<h2 class="h2">${esc(title)}</h2><span class="note">${esc(note||'')}</span>`;
  return d;
}

/* Было в screen-snapshot.js. */
/* ============================================================== SNAPSHOT ==
   Снимок как объект первого класса.

   До сих пор снимок был строкой-ключом, размазанной по трём экранам: спека —
   в карточке на вкладке «Baselines», история — только если повезёт наткнуться
   на нужное сравнение, версии эталона — в модалке из меню «⋯», ignore-зоны
   видны одним числом на чипе. Ответить на вопрос «что это за снимок и что с
   ним происходило» было негде, а править из интерфейса можно было ровно один
   URL — через `prompt()`.

   Здесь всё в одном месте: эталон, спека целиком, зоны игнорирования (мышью
   по картинке), история проверок и кнопки «прогнать только его» и
   «переснять».
   ========================================================================= */
function snapshotHash(scope,platform,name){
  return '#/snapshot/'+encodeURIComponent(scope||'global')
    +'/'+encodeURIComponent(platform||'')
    +'/'+encodeURIComponent(name||'');
}

/* Было в screen-baselines.js. */
/* Какой набор открыть, если человек ещё ни одного не выбирал.

   Правило одно и узкое: набор по умолчанию остаётся набором по умолчанию,
   пока в нём хоть что-то есть. Подменять непустой выбор «более полным» нельзя
   — это перекладывание экрана под ногами. А вот пустой набор при непустом
   соседнем не показывает ничего и ни о чём не сообщает. */
function nonEmptyScope(list,want){
  const total=sc=>(sc.platforms||[]).reduce((a,p)=>a+(p.count||0),0);
  const all=(list||[]).filter(sc=>sc&&sc.scope);
  const here=all.find(sc=>sc.scope===want);
  if(!here||total(here))return want;
  const full=all.filter(sc=>total(sc)>0);
  if(!full.length)return want;
  return full.reduce((a,b)=>(total(b)>total(a)?b:a)).scope;
}

/* Было в screen-decisions.js. */
/* Ответ на причину — ОДИН запрос на все её сравнения.

   Цикл из одиннадцати POST здесь был бы не «медленнее», а хуже по смыслу:
   каждый может упасть сам по себе и оставить причину решённой наполовину, а
   защита от чужого решения (expected_version) в такой цикл не помещается. */
async function answerCause(c,action,btn){
  const what=action==='approve'?'accept as the new normal':'confirm as a bug';
  if(!confirm(`Apply «${what}» to ${c.count} ${plural(c.count,'snapshot','snapshots','snapshots')}?\n\n${c.risk||''}`))return;
  const old=btn.textContent;btn.disabled=true;btn.innerHTML='<span class="spin"></span>';
  try{
    const r=await api('/api/comparisons/bulk',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({ids:c.comparisons,action})});
    const done=(r.counts||{}).done||0,bad=(r.counts||{}).failed||0;
    if(bad){
      const why=(r.failed||[]).slice(0,3).map(f=>`#${f.id}: ${f.error}`).join('\n');
      toast(`Done ${done}, ${bad} could not be applied\n${why}`,'err');
    }else toast(`Done: ${done} closed`,'ok');
  }catch(e){toast(String(e.message||e),'err');btn.disabled=false;btn.textContent=old;return;}
  refreshCounts();
  /* Ответ мог прийти и из триажа — тогда мы не на экране очереди, и
     перерисовать его на месте нельзя: получился бы экран решений под адресом
     сравнения, из которого «назад» ведёт неизвестно куда. */
  if(state.view==='decisions')SCREENS.decisions();
  else location.hash='#/decisions';
}

/* Было в screen-baselines.js. */
async function baselineHistoryModal(it,platform,scope){
  closeMenus();
  const wrap=el('div');
  wrap.innerHTML=`<div class="mhead"><div><h3>History · ${esc(it.short||it.name)}</h3>
    <div class="msub">Rolling back writes the old picture as a new version — nothing is erased.</div></div><button class="mclose" aria-label="Close">×</button></div>`;
  const body=el('div','pf-body');body.innerHTML='<div class="jobbar"><span class="spin"></span> loading…</div>';
  wrap.append(body);openModal(wrap,{wide:true});$('.mclose',wrap).onclick=closeModal;
  const q=`platform=${encodeURIComponent(platform)}&name=${encodeURIComponent(it.name)}`
    +(scope&&scope!=='global'?`&scope=${encodeURIComponent(scope)}`:'');
  let data;
  try{data=await api('/api/baselines/versions?'+q);}
  catch(e){body.innerHTML='';body.append(el('div','pf-status bad',String(e.message||e)));return;}
  body.innerHTML='';
  (data.versions||[]).forEach(v=>{
    const row=el('div');
    row.style.cssText='display:flex;align-items:center;gap:12px;padding:10px 0;border-bottom:1px solid var(--line)';
    const img=el('img');img.src=API+'/api/baselines/version-image?'+q+'&version='+v.version+'&w=120';
    img.style.cssText='width:120px;border:1px solid var(--line);border-radius:4px;cursor:zoom-in';
    img.onclick=()=>lightbox(API+'/api/baselines/version-image?'+q+'&version='+v.version);
    img.onerror=()=>{img.style.display='none';};
    const info=el('div');info.style.flex='1';
    info.innerHTML=`<div><b>v${v.version}</b>${v.current?' <span class="tag green">current</span>':''}</div>
      <div class="muted" style="font-size:12px">${esc(v.approved_by||'—')} · ${esc((v.updated_at||'').replace('T',' ').slice(0,16))}</div>`;
    row.append(img,info);
    if(!v.current&&!v.vcs){
      const back=el('button','btn sm','Roll back');
      back.onclick=async()=>{
        if(!confirm(`Roll «${it.name}» back to v${v.version}?`))return;
        back.disabled=true;
        try{await api('/api/baselines/restore',{method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({platform,name:it.name,version:v.version,scope})});
          toast('Rolled back','ok');closeModal();SCREENS.baselines();}
        catch(e){toast(String(e.message||e),'err');back.disabled=false;}
      };
      row.append(back);
    }else if(v.vcs){row.append(el('span','muted','in git'));}
    body.append(row);
  });
  if(!(data.versions||[]).length)body.append(el('div','muted','No versions yet'));
}

/* Было в screen-baselines.js. */
async function deleteBaseline(it,platform,scope){
  if(!confirm(`Delete baseline «${it.name}»?`))return;
  try{const r=await api('/api/baselines/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({platform,names:[it.name],scope})});toast(`Deleted: ${r.deleted||0}`,'ok');SCREENS.baselines();refreshCounts();}
  catch(e){toast(String(e.message||e),'err');}
}


/* Ключ платформы → подпись для человека.

   `linux-chromium-1x-390x844` это адрес каталога с эталонами, и до матрицы
   таких адресов было один-два — их можно было показывать как есть. С матрицей
   их шесть, восемнадцать или больше, и список ключей перестаёт читаться ровно
   там, где по нему начинают переключаться.

   Разбирается по форме, а не по списку известных браузеров: ключ мог быть
   записан версией, которая знала другой их набор, и отвечать «не знаю» на
   собственные данные — худшее из поведений. Не разобрался — показываем как
   есть, это всегда правда. То же правило и на бэкенде (`vistest/matrix.py`),
   и повторено здесь оно потому, что подпись нужна и там, где данные приходят
   одним ключом без разбора, — например в списке платформ на «Baselines». */
function variantLabel(platform){
  const m=String(platform||'').match(
    /^([^-]+)-([^-]+)-([\d.]+)x(?:-(\d{2,5})x(\d{2,5}))?$/);
  if(!m)return String(platform||'');
  return m[4]?`${m[2]} · ${m[4]}×${m[5]}`:m[2];
}


/* ------- как запускать: в одном браузере, в каждом, или во всех -------

   Кнопка «Run» была одна и означала «в том браузере, который выберет код».
   Для набора, который проверяет вёрстку, это половина ответа: страница ломается
   в firefox и не ломается в chromium ровно так же часто, как наоборот.

   Три вида запуска — это три разных вопроса, и путать их нельзя:

     · «прогони в firefox»      — один прогон, я разбираюсь с одним движком;
     · «по одному на браузер»   — N прогонов, каждый со своим вердиктом и своим
                                  статус-чеком в CI; идут ПАРАЛЛЕЛЬНО, потому
                                  что наборы эталонов у браузеров разные;
     · «общий по всем»          — ОДИН прогон и один ответ на вопрос «эта
                                  страница сломалась?»; браузеры внутри идут
                                  подряд.

   Меню одно на два экрана — «Tests» и «Projects». Две копии разошлись бы на
   первой правке, а список браузеров приезжает с сервера: движки поддерживает
   Playwright, и знать про них обязан тот, кто его запускает. */
let knownBrowsers=null,browserState=null;
async function browsersAvailable(){
  if(knownBrowsers)return knownBrowsers;
  try{
    const m=await api('/api/matrix');
    knownBrowsers=(m&&m.known_browsers||[]).filter(Boolean);
    /* Не только «какие движки бывают», но и «какие здесь есть».

       Раньше приезжал один список — тот, что Playwright УМЕЕТ. Меню предлагало
       три движка в образе, где установлен один, человек выбирал firefox, и
       дальше всё выглядело поломкой VisTest: набор эталонов пустой, следующий
       прогон отвечает «набор не снят — снимите», съёмка снова ничего не
       снимает. Настоящая причина — одна строка глубоко в выводе Playwright про
       отсутствующий бинарь — не показывалась нигде. */
    browserState=(m&&m.browser_state)||null;
  }catch{knownBrowsers=[];browserState=null;}
  /* Пустой ответ — не повод показать пустое меню: движки известны и без
     сервиса, а молчащий список читается как «многобраузерности нет». */
  if(!knownBrowsers.length)knownBrowsers=['chromium','firefox','webkit'];
  return knownBrowsers;
}

/* Почему этот движок нельзя выбрать. Пусто — можно.

   Отвечает только про то, что сервис знает наверняка. Прогон подключённого
   проекта идёт ИХ playwright'ом, и наш набор движков ему не указ — поэтому
   пункт остаётся живым, а не отключается: сказать «нельзя» там, где можно,
   хуже, чем промолчать. */
function browserMissing(name){
  const st=browserState&&browserState[name];
  if(!st||st==='installed')return '';
  if(st==='no-playwright')
    return 'Playwright is not installed in the VisTest environment, so this '
      +'engine cannot be used for VisTest\u2019s own capture. A connected '
      +'project runs with its own Playwright and may still work.';
  return name+' is not installed in the VisTest environment: Playwright ships '
    +'the library, the browsers are downloaded separately. Run \u00abplaywright '
    +'install '+name+'\u00bb. A connected project runs with its own Playwright '
    +'and may still work.';
}

/* `refusal` — почему многобраузерный запуск невозможен для этой цели.
   Пусто — возможен. Непусто — пункты показываются отключёнными с этим текстом
   в подсказке: спрятать их совсем значило бы оставить человека гадать, почему
   у соседнего проекта выбор есть, а у его нет. */
async function runBrowserMenu(e,{run,refusal='',label='Run',at=null}){
  /* Якорь можно передать отдельно от события.

     Меню открывается и из ПУНКТА ДРУГОГО МЕНЮ — «⋯ → снять набор, выбрать
     браузеры». Пункт к этому моменту уже закрыт вместе со своим меню, его
     прямоугольник обнулился, и позиция бралась бы из ничего: меню появлялось
     бы в левом верхнем углу, далеко от того места, куда человек только что
     нажал. Поэтому вызывающий может снять прямоугольник заранее. */
  if(e&&e.stopPropagation)e.stopPropagation();
  closeMenus();
  const list=await browsersAvailable();
  const m=el('div','menu');
  const rc=at||((e&&e.target&&e.target.getBoundingClientRect)
    ? e.target.getBoundingClientRect() : {bottom:70,left:70});
  /* Координаты ставятся ЗДЕСЬ ЖЕ, рядом со снятием прямоугольника.

     `.menu` в стилях — `position:fixed` без `top`/`left`: без явных координат
     блок остаётся на своём месте в потоке, а место это — конец `body`, то
     есть ниже экрана. Меню при этом создаётся, наполняется и добавляется в
     документ — и не видно ни одного пункта. Снаружи это читается как «кнопка
     перестала работать», хотя нажатие дошло и запрос ушёл.

     Отступ слева отрицательный: якорь — узкая кнопка «▾», а меню шире её в
     несколько раз; выровняв по левому краю кнопки, мы уводим его за правый
     край карточки. `Math.max(8,…)` держит его в окне, когда карточка стоит у
     самого левого края. */
  m.style.top=(rc.bottom+6)+'px';
  m.style.left=Math.max(8,rc.left-60)+'px';
  m.innerHTML='<div class="mi-head">'+esc(label.toUpperCase())+'</div>';

  m.append(mkItem('As the tests decide',()=>run({})));
  m.append(mkDiv());
  list.forEach(b=>{
    /* Движок, которого в окружении нет, показывается — но с меткой и
       объяснением. Спрятать его значило бы оставить человека с вопросом
       «почему у меня только chromium», на который экран обязан отвечать сам. */
    const gone=browserMissing(b);
    const item=mkItem(b+(gone?'  · not installed':''),
                      ()=>run({browsers:[b]}),!!refusal);
    if(gone)item.classList.add('warn');
    item.title=refusal||gone||'';
    if(!item.title)item.removeAttribute('title');
    m.append(item);
  });
  m.append(mkDiv());

  const each=mkItem(`Each browser separately · ${list.length} runs`,
    ()=>run({browsers:list,mode:'separate'}),!!refusal);
  each.title=refusal||`${list.length} independent runs, each with its own `
    +'verdict. They go in parallel — the baselines of different browsers are '
    +'different sets, so they cannot write over each other.';
  m.append(each);

  const all=mkItem('All browsers · one run',
    ()=>run({browsers:list,mode:'together'}),!!refusal);
  all.title=refusal||'One run and one answer to «did this page break?». The '
    +'browsers go one after another inside it.';
  m.append(all);

  document.body.append(m);stopClose(m);
}

/* Ответ роута может нести один прогон или список. Следить надо за всеми:
   человек нажал «по одному на браузер» и вправе видеть, чем кончился каждый, а
   не только первый. */
function followRun(started,opts={}){
  return Promise.resolve(started).then(r=>{
    const jobs=(r&&r.jobs)||[];
    if(jobs.length>1){
      toast(`${jobs.length} runs started in parallel: `
        +jobs.map(j=>j.browser).join(', '));
      return Promise.all(jobs.map(j=>runJob(Promise.resolve({job_id:j.job_id}),
        {...opts,title:(opts.title||'Run')+' · '+j.browser})));
    }
    return runJob(Promise.resolve(r),opts);
  },e=>{toast(String(e.message||e),'err');throw e;});
}

/* ---------------------------------------------------------------- разделы --
   Оглавление для длинной страницы.

   «Settings» — это восемь панелей подряд: секреты, пороги, эталоны на ветку,
   уведомления, хранилище, автоочистка, команда, заявки. Дойти до автоочистки
   можно было только прокруткой, а узнать, что она вообще есть, — только
   прокруткой до конца. Экран, о содержимом которого нельзя судить, не
   доскроллив его, для человека равен своей верхней трети.

   Оглавление собирается ИЗ УЖЕ СОБРАННОЙ страницы, по заголовкам разделов, а
   не из отдельного списка: список — это второе место, где перечислены
   разделы, и он разъезжается с первым при первой же правке. Раздел, у
   которого нет заголовка на экране, не попадает в оглавление, и это
   правильно: пункт, ведущий в никуда, хуже отсутствующего.

   Не вкладки, а якоря: вкладка прячет семь разделов из восьми, и «поискать
   глазами по странице» перестаёт работать вовсе — а это то, чем человек
   пользуется, когда не помнит названия. */
function sectionRail(page,opts){
  const heads=$$('.sect-h h2, .sect h2',page);
  if(heads.length<3)return null;   /* три раздела ещё держатся в голове */

  const rail=el('nav','rail-nav');
  rail.setAttribute('aria-label',(opts&&opts.label)||'Sections of this page');
  const links=[];
  heads.forEach((h,i)=>{
    const holder=h.closest('.sect-h,.sect');
    if(!holder)return;
    /* Идентификатор ставится здесь, а не в разметке экранов: иначе каждый
       экран обязан помнить про оглавление, которого он не просил. */
    if(!holder.id)holder.id='sect-'+i+'-'+String(h.textContent||'').toLowerCase()
      .replace(/[^a-z0-9]+/g,'-').replace(/^-|-$/g,'');
    const a=el('a',null,esc(h.textContent||''));
    a.href='#';
    a.onclick=e=>{
      e.preventDefault();
      /* `scrollIntoView` по контейнеру экрана, а не по окну: прокручивается
         `#screen`, окно стоит на месте. */
      holder.scrollIntoView({block:'start',behavior:'smooth'});
      /* Фокус переезжает вместе с прокруткой — иначе Tab после щелчка
         продолжает обход с начала страницы, а не с того раздела, куда
         человека только что привели. */
      holder.setAttribute('tabindex','-1');holder.focus({preventScroll:true});
    };
    links.push([a,holder]);
    rail.append(a);
  });
  if(!links.length)return null;

  links[0][0].classList.add('on');

  /* Подсветка текущего раздела. `IntersectionObserver` вместо слушателя
     прокрутки: последний срабатывает десятки раз за один поворот колеса, и
     каждый раз считает геометрию всех восьми заголовков.

     Наблюдателя может не быть вовсе — в jsdom, которым гоняется дымовая
     проверка запуска, его нет. Оглавление без подсветки остаётся оглавлением:
     по нему всё так же переходят. Оглавление, которое уронило экран целиком,
     — нет. */
  if(typeof IntersectionObserver!=='function')return rail;
  const io=new IntersectionObserver(()=>{
    /* Текущий — самый нижний из тех, чьи заголовки уже над линией взгляда.
       «Первый видимый» ошибается на длинных разделах: пока панель занимает
       весь экран, её заголовок давно уехал вверх. */
    let cur=links[0][0];
    links.forEach(([a,holder])=>{if(holder.getBoundingClientRect().top<170)cur=a;});
    links.forEach(([a])=>a.classList.toggle('on',a===cur));
  },{root:$('#screen'),rootMargin:'-150px 0px -70% 0px',threshold:[0,1]});
  links.forEach(([,holder])=>io.observe(holder));
  /* Наблюдатель живёт ровно столько, сколько живёт страница: экраны
     пересобирают `#screen` целиком, и без отписки наблюдатели копились бы по
     одному на каждый заход в настройки. */
  if(typeof MutationObserver==='function'){
    const stop=new MutationObserver(()=>{
      if(!rail.isConnected){io.disconnect();stop.disconnect();}});
    stop.observe($('#screen'),{childList:true});
  }
  return rail;
}
