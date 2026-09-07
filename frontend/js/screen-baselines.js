/* VisTest - self-hosted visual regression testing.
 * Copyright (C) 2026 Kirill Kulagin
 * SPDX-License-Identifier: AGPL-3.0-or-later
 *
 * This file is part of VisTest. See LICENSE for the full terms and NOTICE for
 * the trademark and commercial-licensing terms. Removing this header does not
 * remove those obligations.
 */

/* ============================================================ BASELINES ====
   Экран «Baselines»: картинки, с которыми сравнивается каждый прогон.

   --------------------------------------------------------------------------
   Что здесь было не так

   Над сеткой стояли три переключателя подряд — SET, PLATFORM и фильтр, — и из
   них ровно один был понятен без объяснения.

   · SET дублировал выбор проекта из шапки. Одна и та же сущность в двух местах
     означала, что человек, выбравший проект наверху, продолжал видеть чужие
     эталоны внизу. Выбор переехал в шапку целиком (`project.js`), здесь от
     него осталась подпись «что показано» и ссылка «показать другой набор» —
     для единственного случая, когда наборов у проекта правда два.

   · PLATFORM был плоским списком ключей ХРАНЕНИЯ:
     `linux-chromium-1x`, `linux-chromium-1x-390x844`, `linux-firefox-1x`, …
     Ключ безупречен как адрес каталога и негоден как переключатель: в нём
     склеены четыре независимых ответа, а спрашивают их по одному. С матрицей
     таких строк становится шесть или восемнадцать, и выбрать из них «firefox
     на телефоне» можно только вычитав каждую по буквам.

     Вопросов на самом деле два, и они разного рода:
        «в каком движке?»  — chromium / firefox / webkit;
        «в каком размере?» — 1440×900 / 390×844 / …
     Поэтому и переключателя два, один под другим. Ключ платформы при этом
     никуда не делся — он собирается из выбранного и показан рядом мелким
     моноширинным: это адрес на диске, и он нужен, когда идут смотреть файлы.

   · Между движками нельзя было сравнить. Эталон chromium и эталон firefox —
     физически разные картинки (отрисовка шрифтов разная), и вопрос «а как эта
     страница выглядит в каждом» задают постоянно. Ответом было переключение
     туда-сюда с запоминанием картинки в голове. Режим «Compare browsers»
     кладёт варианты одного снимка в ряд.

   --------------------------------------------------------------------------
   Одна выборка на все переключения

   `/api/baselines/detail` спрашивается БЕЗ `platform` — то есть про все
   платформы набора сразу, — и дальше переключение движка, размера и режима
   сравнения не ходит в сеть вовсе. Раньше каждый щелчок по платформе
   перерисовывал экран через запрос; для того, чем щёлкают по десять раз
   подряд, сравнивая, это неверная цена.
   ========================================================================= */

SCREENS.baselines=async function(){
  loadingScreen();
  const here=pageGuard();

  const scope=currentScope();
  const q=scope&&scope!=='global'?'?scope='+encodeURIComponent(scope):'';
  let d,matrix;
  try{
    [d,matrix]=await Promise.all([
      api('/api/baselines/detail'+q),
      /* Матрица нужна для двух подписей и одной кнопки. Её отсутствие — не
         повод не показать эталоны: без неё базовый размер называется «base
         size», а кнопки прогона по матрице просто нет. */
      api('/api/matrix').catch(()=>null),
    ]);
  }catch(e){return errScreen(e);}
  if(!here())return;

  /* --------------------------------------------------- разбор платформ --- */
  const plats=(d.platforms||[]).map(pl=>({...parsePlatform(pl.platform),
                                          platform:pl.platform,
                                          count:pl.count||0,
                                          items:pl.items||[]}));
  const base=(matrix&&matrix.base_viewport)||'';
  const bstate=(matrix&&matrix.browser_state)||{};
  const browsers=groupByBrowser(plats);

  /* Что выбрано. Приоритет: явный выбор человека → платформа, на которую его
     привели с другого экрана → платформа машины сервиса → самая полная.

     Платформа сервиса стоит НЕ первой намеренно: снимает не она. Windows-
     сервис и docker-эталоны не совпадают ни разу, и «текущая» платформа
     оказывалась пустой ровно там, где эталоны есть. */
  if(state.platform&&!state.browser&&plats.some(p=>p.platform===state.platform)){
    const pp=parsePlatform(state.platform);
    state.browser=pp.browser;state.viewport=pp.viewport;
  }
  const bcur=pickBrowser(browsers,state.browser,d.current);
  const vps=bcur?bcur.viewports:[];
  const vcur=pickViewport(vps,state.viewport);
  const platObj=(vcur&&vcur.plat)||null;
  const cur=platObj?platObj.platform:'';
  /* Запоминаем разобранный вариант — но только если он есть. Затирать выбор
     пустотой нельзя: набор мог не приехать (сеть моргнула, набор ещё не снят),
     а платформу человеку только что поставила кнопка с другого экрана. */
  if(cur)state.platform=cur;
  const items=platObj?platObj.items:[];
  const runnable=items.filter(i=>i.runnable).length;

  const s=$('#screen');s.innerHTML='';
  const page=el('div','page');s.append(page);

  /* ------------------------------------------------------------ шапка --- */
  const head=el('div','head');
  const left=el('div','grow');
  const total=plats.reduce((a,p)=>a+p.count,0);
  left.innerHTML=`<div class="eyebrow">LIBRARY · ${esc(projectLabel())} · ${esc(scopeLabel(scope))}</div>
    <h1 class="h1">${fmtInt(total)} ${plural(total,'baseline','baselines','baselines')}</h1>
    <div class="lede">The pictures every run is compared against.
      ${browsers.length>1?`Spread over ${browsers.length} engines`:'One engine'}${
        plats.length>browsers.length?` and ${plats.length} browser × window variants`:''} —
      pick one below, or put them side by side.</div>`;
  head.append(left);

  const acts=el('div','acts');
  const rec=el('button','btn accent','Record with the mouse');rec.onclick=recordFlow;
  const cap=el('button','btn','Capture by URL');
  cap.onclick=()=>{const f=$('#snUrl');if(f){f.scrollIntoView({block:'center'});f.focus();}};
  const checkAll=el('button','btn','Check all · '+(runnable||items.length));
  checkAll.disabled=!runnable;
  if(!runnable)checkAll.title='No snapshot here has an address or a test bound yet.';
  else tip(checkAll,'Checks every snapshot of the selected variant — '
    +variantTitle(bcur,vcur,base)+'. Compares only; nothing is overwritten.');
  checkAll.onclick=()=>runJob(api('/api/suite/run',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({platform:cur,scope})}),
    {title:'Baseline check',then:()=>SCREENS.baselines()});
  acts.append(rec,cap,checkAll);
  /* Прогон по всей матрице — отдельной кнопкой, а не режимом у «Check all».
     Это разные по цене действия: одно проверяет открытый вариант, второе —
     все и во столько же раз дольше. */
  addMatrixRun(acts,scope,runnable||items.length,matrix);
  head.append(acts);
  page.append(head);
  refreshRecordBtn(rec);

  /* ------------------------------------------------- выбор варианта ----- */
  page.append(variantBar({browsers,bcur,vps,vcur,base,bstate,plats,scope,
                          count:items.length}));

  /* ------------------------------------------------------------ сетка --- */
  const gc=el('div','grid-cards');
  page.append(gc);

  function paintCards(){
    gc.className=state.blCompare?'cmp-list':'grid-cards';
    gc.innerHTML='';
    const f=(state.blFilter||'').trim().toLowerCase();
    const match=it=>!f||[it.name,it.short,it.viewport,it.selector,it.url,
      (it.source||{}).test].some(v=>String(v||'').toLowerCase().includes(f));

    if(state.blCompare){
      const rows=compareRows(browsers,vcur,match);
      if(!rows.length)return gc.append(emptyCards(f,bcur,vcur,base));
      rows.forEach(r=>gc.append(compareCard(r,scope)));
      return;
    }
    const shown=items.filter(match);
    if(!shown.length)return gc.append(emptyCards(f,bcur,vcur,base));
    shown.forEach(it=>gc.append(baselineCard(it,cur,scope)));
  }
  paintCards();
  /* Фильтр и переключатель режима перерисовывают ТОЛЬКО сетку. Полная
     перерисовка экрана на каждую букву забирала бы с собой фокус из поля. */
  const fi=$('#blFind');
  if(fi)fi.oninput=e=>{state.blFilter=e.target.value;paintCards();};
  const cmp=$('#blCompare');
  if(cmp)cmp.onclick=()=>{
    state.blCompare=!state.blCompare;
    cmp.classList.toggle('on',state.blCompare);
    cmp.setAttribute('aria-pressed',String(state.blCompare));
    paintCards();
  };

  /* ------------------------------------------------- съёмка новой ------- */
  /* Форма на четыре поля стоит ПОД сеткой: над ней она каждый раз отодвигала
     вниз то, ради чего на экран и заходят. */
  page.append(sectionHead('Capture a baseline of a new page',
    'the browser opens on the service machine · '+variantTitle(bcur,vcur,base)));
  const snap=el('div','panel pad');
  snap.append(el('div','lede','Log in and navigate where you need — the recording '
    +'panel survives transitions and does not get into the baseline.'));
  const row=el('div');
  row.style.cssText='display:grid;grid-template-columns:2fr 1fr 1fr 1fr auto;'
    +'gap:12px;margin-top:14px;align-items:end';
  row.innerHTML=`<div><label class="flabel" for="snUrl">PAGE URL</label><input class="inp mono" id="snUrl" placeholder="https://my-app.local/checkout"></div>
    <div><label class="flabel" for="snName">NAME</label><input class="inp mono" id="snName" placeholder="checkout"></div>
    <div><label class="flabel" for="snVp">WINDOW</label><input class="inp mono" id="snVp" placeholder="${esc(viewportLabel(vcur&&vcur.viewport,base))}"></div>
    <div><label class="flabel" for="snFlow">STEPS</label><input class="inp mono" id="snFlow" placeholder="flow: login"></div>`;
  const snb=el('button','btn dark','Capture');
  snb.onclick=()=>{
    const url=$('#snUrl').value.trim();if(!url)return toast('Set a URL','err');
    /* Тело запроса собирается как `targets: [...]` — именно этого роут и ждёт.
       Раньше поля уходили верхним уровнем, `targets` приходил пустым, и кнопка
       отвечала «at least one target with a url is required» при любом нажатии. */
    const flow=$('#snFlow').value.trim().replace(/^flow:\s*/i,'');
    const target={url};
    const nm=$('#snName').value.trim();if(nm)target.name=nm;
    const vp=$('#snVp').value.trim();if(vp)target.viewport=vp;
    if(flow)target.steps=[{action:'flow',name:flow}];
    runJob(api('/api/baselines/snap',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({targets:[target],platform:cur,scope})}),
      {title:'Baseline capture',then:()=>SCREENS.baselines()});
  };
  row.append(snb);snap.append(row);page.append(snap);
};

/* ====================================================== выбор варианта ==== */
/* Движки в устойчивом порядке, а не по числу эталонов.

   Порядок по числу выглядит разумно ровно до первого прогона: снял набор для
   firefox — и вкладки поменялись местами под курсором. Переключатель, который
   переставляет сам себя, заставляет каждый раз перечитывать его целиком. */
const BROWSER_ORDER=['chromium','chrome','firefox','webkit','safari'];
function groupByBrowser(plats){
  const map=new Map();
  plats.forEach(p=>{
    /* Неразобранный ключ — тоже вариант, и прятать его нельзя: под ним лежат
       чьи-то эталоны. Он становится собственной «группой» со своим именем. */
    const name=p.parsed?p.browser:p.raw;
    if(!map.has(name))map.set(name,{name,count:0,parsed:p.parsed,viewports:[]});
    const g=map.get(name);
    g.count+=p.count;
    g.viewports.push({viewport:p.viewport,w:p.w,h:p.h,count:p.count,plat:p,
                      os:p.os,scale:p.scale});
  });
  const out=[...map.values()];
  out.forEach(g=>g.viewports.sort((a,b)=>{
    /* Базовый размер (без суффикса) — первым: под ним лежит всё, что снято до
       матрицы, и он же чаще всего единственный. Дальше по ширине. */
    if(!a.viewport!==!b.viewport)return a.viewport?1:-1;
    return (a.w||0)-(b.w||0);
  }));
  return out.sort((a,b)=>{
    const ia=BROWSER_ORDER.indexOf(a.name),ib=BROWSER_ORDER.indexOf(b.name);
    if(ia!==ib)return (ia<0?99:ia)-(ib<0?99:ib);
    return String(a.name).localeCompare(String(b.name));
  });
}

function pickBrowser(browsers,want,serverPlatform){
  if(!browsers.length)return null;
  const named=browsers.find(b=>b.name===want);
  if(named)return named;
  const mine=parsePlatform(serverPlatform||'').browser;
  const same=browsers.find(b=>b.name===mine&&b.count);
  if(same)return same;
  const full=browsers.filter(b=>b.count);
  if(full.length)return full.reduce((a,b)=>(b.count>a.count?b:a));
  return browsers[0];
}
function pickViewport(vps,want){
  if(!vps||!vps.length)return null;
  const named=vps.find(v=>v.viewport===want);
  if(named)return named;
  const full=vps.filter(v=>v.count);
  if(full.length)return full.reduce((a,b)=>(b.count>a.count?b:a));
  return vps[0];
}
function variantTitle(b,v,base){
  if(!b)return 'no variants';
  return b.name+' · '+viewportLabel(v&&v.viewport,base);
}

/* Две строки над сеткой: движок, потом размер окна. Именно в этом порядке —
   движок меняет картинку целиком, размер только раскладку внутри неё. */
function variantBar(o){
  const box=el('div','platbar');

  /* --- строка 1: движок + поиск --- */
  const r1=el('div','pb-row');
  r1.append(el('span','pb-k','BROWSER'));
  const tabs=el('div','pb-tabs');
  tabs.setAttribute('role','tablist');
  o.browsers.forEach(b=>{
    const on=o.bcur&&b.name===o.bcur.name;
    const t=el('button','pb-tab'+(on?' on':'')+(b.count?'':' empty'));
    t.setAttribute('role','tab');
    t.setAttribute('aria-selected',String(!!on));
    t.innerHTML=browserMark(b.name)
      +`<span class="nm">${esc(b.name)}</span>`
      +`<span class="n">${fmtInt(b.count)}</span>`;
    /* Движок, которого в окружении нет, показывается — но с меткой и
       объяснением. Спрятать его значило бы оставить человека с вопросом
       «почему у меня только chromium», на который экран обязан отвечать сам. */
    const gone=o.bstate[b.name]&&o.bstate[b.name]!=='installed';
    if(gone)t.classList.add('gone');
    tip(t,(gone?'Not installed in the VisTest environment — «playwright install '
        +b.name+'». Baselines already captured are still shown here. ':'')
      +'Baselines of one engine are only comparable within it: font rendering '
      +'in '+b.name+' and in the other engines is physically different, so the '
      +'same page captured twice is two different pictures.');
    if(!on)t.onclick=()=>{state.browser=b.name;state.viewport=null;SCREENS.baselines();};
    tabs.append(t);
  });
  r1.append(tabs);
  const find=el('label','pb-find');
  find.innerHTML='<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><circle cx="7" cy="7" r="4.6"/><path d="M10.5 10.5L14 14"/></svg>'
    +`<input id="blFind" placeholder="Filter ${o.count} by name, selector, viewport" aria-label="Filter baselines">`;
  r1.append(find);
  box.append(r1);
  const fi=$('input',find);if(fi)fi.value=state.blFilter||'';

  /* --- строка 2: размер окна + сравнение + ключ платформы --- */
  const r2=el('div','pb-row');
  r2.append(el('span','pb-k','WINDOW'));
  const chips=el('div','pb-chips');
  if(!o.vps.length)chips.append(el('span','muted','nothing captured for this engine'));
  o.vps.forEach(v=>{
    const on=o.vcur&&v.viewport===o.vcur.viewport;
    const c=el('button','pb-chip'+(on?' on':'')+(v.count?'':' empty'));
    const kind=viewportKind(v.w);
    c.innerHTML=`<span class="nm">${esc(viewportLabel(v.viewport,o.base))}</span>`
      +(kind?`<span class="kd">${esc(kind)}</span>`:'')
      +`<span class="n">${fmtInt(v.count)}</span>`;
    tip(c,v.viewport
      ? `Window ${viewportLabel(v.viewport,o.base)} — its own set of baselines, stored under «${v.plat.platform}».`
      : 'The base window size. Its baselines are stored without a size suffix — '
        +'that is where everything captured before the matrix existed still lies.');
    if(!on)c.onclick=()=>{state.viewport=v.viewport;SCREENS.baselines();};
    chips.append(c);
  });
  r2.append(chips);

  const right=el('div','pb-right');
  /* Сравнение по движкам предлагается только там, где есть что сравнивать. */
  if(o.browsers.length>1){
    const cmp=el('button','pb-cmp'+(state.blCompare?' on':''),'Compare browsers');
    cmp.id='blCompare';
    cmp.setAttribute('aria-pressed',String(!!state.blCompare));
    tip(cmp,'Puts the same snapshot side by side in every engine, at this window '
      +'size. They are separate baselines and are never compared with each other '
      +'by the engine — this is for your eyes.');
    right.append(cmp);
  }
  /* Ключ платформы. Он больше не переключатель, но он всё ещё адрес каталога
     на диске, и его спрашивают ровно тогда, когда идут смотреть файлы. */
  if(o.vcur&&o.vcur.plat){
    const k=el('span','pb-key mono',esc(o.vcur.plat.platform));
    tip(k,'The storage key: the folder these baselines lie in, under the set’s root. '
      +'Machine and pixel density are part of it — baselines captured on Windows '
      +'are never compared with Linux ones.');
    right.append(k);
  }
  r2.append(right);
  box.append(r2);

  /* --- строка 3: набор, если у проекта их больше одного --- */
  const other=(state.scopes||[]).filter(x=>x.scope&&x.scope!==o.scope);
  const p=currentProject();
  const overridden=state.scopeOverride&&p&&state.scopeOverride!==p.scope;
  if(other.length||overridden){
    const r3=el('div','pb-row pb-note');
    r3.append(el('span','pb-k','SET'));
    const line=el('div','pb-set');
    line.innerHTML=`<b>${esc(scopeLabel(o.scope))}</b>`;
    if(overridden){
      line.innerHTML+=' <span class="tag amber">not this project’s own set</span>';
      const back=el('button','lnk','back to '+scopeLabel(p.scope));
      back.onclick=()=>setScopeOverride(null);
      line.append(back);
    }else if(other.length){
      const sw=el('button','lnk','show another set');
      tip(sw,'A connected project can have two: the PNG files that live in its own '
        +'repository, and the set VisTest captured itself. They are different '
        +'pictures, and which one a run compared against is written on the run.');
      sw.onclick=e=>scopeMenu(e,o.scope);
      line.append(sw);
    }
    r3.append(line);
    box.append(r3);
  }
  return box;
}

function scopeMenu(e,scope){
  e.stopPropagation();closeMenus();
  const m=el('div','menu');const rc=e.target.getBoundingClientRect();
  m.style.top=(rc.bottom+6)+'px';m.style.left=Math.max(8,rc.left-40)+'px';
  m.innerHTML='<div class="mi-head">BASELINE SET</div>';
  (state.scopes||[]).forEach(sc=>{
    const n=scopeTotal(sc);
    const it=mkItem(`${sc.label||sc.scope}  ·  ${n}`,()=>setScopeOverride(sc.scope));
    if(sc.scope===scope)it.classList.add('on');
    if(sc.empty_hint)it.title=sc.empty_hint;
    m.append(it);
  });
  document.body.append(m);stopClose(m);
}

function emptyCards(filter,bcur,vcur,base){
  const e=el('div','empty-cards');
  if(filter){e.append(el('div','empty','Nothing matches the filter'));return e;}
  e.append(el('div','empty','<b>No baselines for '
    +esc(variantTitle(bcur,vcur,base))+'</b>'
    +'<div class="faint" style="margin-top:8px;font-size:12.5px;line-height:1.8">'
    +'Other engines and window sizes may well have them — the counts are on the '
    +'switches above.<br>To fill this one: run the matrix, or capture a page by '
    +'URL below. A variant with no baseline records one on its first run and '
    +'appears as «new», not as a failure.</div>'));
  return e;
}

/* ================================================ сравнение по движкам ==== */
/* Один снимок — строка, движки — колонки.

   Собирается по ИМЕНИ снимка, а не по индексу: набор firefox может отставать
   от chromium на несколько картинок, и совмещение по порядку показало бы рядом
   два разных экрана как один. Отсутствующий вариант — это ответ, а не пропуск:
   он и есть то, что человек ищет, открывая сравнение. */
function compareRows(browsers,vcur,match){
  const want=vcur?vcur.viewport:'';
  const cols=browsers.map(b=>({
    name:b.name,
    plat:(b.viewports.find(v=>v.viewport===want)||{}).plat||null}));
  const order=[];const byName=new Map();
  cols.forEach(c=>((c.plat&&c.plat.items)||[]).forEach(it=>{
    if(!byName.has(it.name)){
      byName.set(it.name,{name:it.name,short:it.short,cells:new Map(),any:it});
      order.push(it.name);
    }
    byName.get(it.name).cells.set(c.name,it);
  }));
  return order.map(n=>{
    const r=byName.get(n);
    r.cols=cols.map(c=>({browser:c.name,
                         platform:c.plat?c.plat.platform:'',
                         item:r.cells.get(c.name)||null}));
    return r;
  }).filter(r=>match(r.any));
}

function compareCard(row,scope){
  const c=el('div','cmp-card');
  const head=el('div','cmp-head');
  const nm=el('button','nm',esc(row.short||row.name));
  tip(nm,'Opens the snapshot page of the first engine that has it: the spec, the '
    +'ignore zones and everything that ever happened to it.');
  const first=row.cols.find(x=>x.item);
  nm.onclick=()=>{if(first)location.hash=snapshotHash(scope,first.platform,row.name);};
  head.append(nm);
  const missing=row.cols.filter(x=>!x.item).length;
  if(missing)head.append(el('span','tag amber',
    missing+' of '+row.cols.length+' not captured'));
  c.append(head);

  const strip=el('div','cmp-strip');
  strip.style.gridTemplateColumns='repeat('+row.cols.length+',minmax(0,1fr))';
  row.cols.forEach(col=>{
    const cell=el('div','cmp-cell'+(col.item?'':' none'));
    const cap=el('div','cmp-cap');
    cap.innerHTML=browserMark(col.browser)+`<span>${esc(col.browser)}</span>`
      +(col.item?`<span class="mono faint">v${col.item.version||1}</span>`:'');
    cell.append(cap);
    const th=el('div','thumb');
    if(col.item&&col.item.thumb){
      const im=el('img');im.src=col.item.thumb;im.loading='lazy';
      im.alt=(row.short||row.name)+' in '+col.browser;
      im.onerror=()=>{im.remove();th.append(el('div','ph','image unavailable'));};
      im.onclick=()=>col.item.full&&lightbox(col.item.full);
      th.append(im);
    }else{
      th.append(el('div','ph',col.platform
        ? 'not captured in '+col.browser
        : col.browser+' has nothing at this window size'));
    }
    cell.append(th);
    if(col.item){
      const open=el('button','lnk','open');
      open.onclick=()=>{location.hash=snapshotHash(scope,col.platform,col.item.name);};
      cell.append(open);
    }
    strip.append(cell);
  });
  c.append(strip);
  return c;
}

/* ==================================================== карточка снимка ===== */
function baselineCard(it,platform,scope){
  const c=el('div','snap-card');
  const th=el('div','thumb');
  if(it.thumb){const im=el('img');im.src=it.thumb;im.loading='lazy';
               im.alt=it.short||it.name;im.onerror=()=>{im.remove();};th.append(im);}
  th.append(el('div','ver','v'+(it.version||1)));
  th.querySelector('img')&&(th.querySelector('img').onclick=()=>it.full&&lightbox(it.full));
  c.append(th);
  /* Имя — кнопка, а не <div> с обработчиком: карточек на экране полторы сотни,
     и до сих пор ни одна из них не открывалась с клавиатуры. */
  const nm=el('button','nm',esc(it.short||it.name));
  tip(nm,'Opens the snapshot page: its baseline, the spec it was captured by, its '
    +'ignore zones and everything that ever happened to it.');
  nm.onclick=()=>{location.hash=snapshotHash(scope,platform,it.name);};
  c.append(nm);
  /* Чем снят снимок — первой строкой, вместо «URL is not set».

     Раньше здесь стоял адрес, а его отсутствие читалось как «снимок неполный,
     впишите url». Для снимка, снятого чужим тестом, это был плохой совет:
     впишешь адрес — и «перепроверить» начнёт грузить страницу входа вместо
     той, ради которой тест логинился. */
  c.append(sourceLine(it));
  const chips=el('div','chips');
  if(it.viewport)chips.append(el('span','c',esc(it.viewport)));
  chips.append(el('span','c',it.steps_summary||(it.steps?('flow: '+it.steps):'without steps')));
  if(it.has_mask)chips.append(el('span','c','noise mask'));
  else if(it.ignore_boxes&&it.ignore_boxes.length)chips.append(el('span','c','ignore zones: '+it.ignore_boxes.length));
  else if(it.runnable===false)chips.append(el('span','c','nothing to repeat'));
  else chips.append(el('span','c','ok'));
  c.append(chips);
  const acts=el('div','acts');
  const viaTest=!!(it.source&&it.source.runnable);
  const chk=el('button','btn sm','Check');
  chk.disabled=it.runnable===false;
  const how=viaTest
    ? 'Runs the test that captured this snapshot — with its login and its steps.'
    : it.runnable===false
    ? 'This snapshot remembers neither a test nor an address, so there is nothing to repeat. It can only be re-captured inside a test run.'
    : 'Loads the address bound to this snapshot and compares.';
  tip(chk,how+(it.runnable===false?'':' Compares only — the baseline is not touched.'));
  chk.onclick=()=>runBaseline(it,platform,scope,false);
  const re=el('button','btn sm','Re-capture');re.disabled=it.runnable===false;
  tip(re,how+(it.runnable===false?'':' Overwrites the baseline with what comes back — '
    +'no comparison, no decision.'));
  re.onclick=()=>runBaseline(it,platform,scope,true);
  const more=el('button','btn sm','⋯');
  more.setAttribute('aria-label','More actions for '+(it.short||it.name));
  tip(more,'Versions and rollback, ignore zones, the capture spec, delete.');
  more.onclick=e=>baselineMenu(e,it,platform,scope);
  acts.append(chk,re,more);c.append(acts);
  return c;
}
/* Одна кнопка на два способа: решает не интерфейс, а сам снимок.

   Держать выбор на клиенте значило бы, что «Check» из списка, «Check» с
   карточки снимка и «Check all» решают его каждый по-своему — и разойдутся они
   молча, на первом же изменении. */
function runBaseline(it,platform,scope,update){
  const via=(it.source&&it.source.runnable)?' via '+(it.source.test||'its test'):'';
  return runJob(api('/api/baselines/run',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({platform,name:it.name,scope,update})}),
    {title:(update?'Re-capture ':'Check ')+it.name+via,
     then:()=>SCREENS.baselines()});
}

/* Строка под именем снимка: чем он снят. */
function sourceLine(it){
  const src=it.source||{};
  const box=el('div','path');
  if(src.runnable&&src.test){
    box.innerHTML='<span class="src-tag">test</span> '+esc(src.test)
      +(src.project_key?' <span class="faint">· '+esc(src.project_key)+'</span>':'');
    box.title='Captured by this test — «Check» runs it again';
    return box;
  }
  if(it.url){
    box.innerHTML='<span class="src-tag">url</span> '+esc(it.url);
    return box;
  }
  /* Снимки, снятые до появления этой записи: врать про них «by URL» нельзя,
     половина из них снята тестами. Честный ответ — «неизвестно», и что с этим
     делать. */
  box.innerHTML='<span class="src-tag warn">?</span> '
    +(src.kind==='unknown'
      ? 'source not recorded — run the test once and the link appears'
      : 'neither a test nor an address');
  box.classList.add('faint');
  return box;
}

function baselineMenu(e,it,platform,scope){
  e.stopPropagation();closeMenus();
  const m=el('div','menu');const rc=e.target.getBoundingClientRect();
  m.style.top=(rc.bottom+6)+'px';m.style.left=(rc.left-120)+'px';
  m.append(mkItem('Open the snapshot page',
    ()=>{location.hash=snapshotHash(scope,platform,it.name);}));
  m.append(mkItem('History & rollback',()=>baselineHistoryModal(it,platform,scope)));
  // `/api/baselines/delete` требует роль admin. Пункт меню, который заведомо
  // ответит 403, — не строгость бэкенда, а неправда в меню.
  if(can('admin',scopeProject(scope)))m.append(mkItem('Delete baseline',()=>deleteBaseline(it,platform,scope)));
  document.body.append(m);stopClose(m);
}
async function toggleRecord(){
  let st;try{st=await api('/api/record/status');}catch(e){return toast(String(e.message||e),'err');}
  try{
    if(st.running){await api('/api/record/stop',{method:'POST'});toast('Recording stopped','ok');}
    else{await runJob(api('/api/record/start',{method:'POST'}),{title:'Baseline recording'});}
    SCREENS.baselines();
  }catch(e){toast(String(e.message||e),'err');}
}
async function refreshRecordBtn(btn){try{const st=await api('/api/record/status');if(st.running){btn.classList.add('primary');btn.classList.remove('dark');btn.textContent='■ Stop recording';}}catch{}}

/* Кнопка «прогнать всю матрицу» и строка о том, во что она раскрывается.

   Матрица приезжает уже загруженной: экран спрашивает `/api/matrix` один раз,
   ради подписи базового размера и списка установленных движков. Второй запрос
   за тем же ответом был лишним round-trip на каждый заход. Раскрывает матрицу
   по-прежнему сервис, а не клиент: она живёт в `vistest.yaml`, и второе место,
   где её разбирают, разошлось бы с первым на первой же правке. */
function addMatrixRun(acts,scope,count,m){
  if(!m||!m.declared||(m.variants||[]).length<2)return;
  const n=m.variants.length;
  const b=el('button','btn accent','Run the matrix · '+n+' variants');
  b.title=m.variants.map(v=>v.label+'  →  '+v.platform).join('\n')
    +`\n\n${count} snapshots × ${n} variants = ${count*n} checks, one run.`;
  b.onclick=()=>{
    if(!confirm(`Run ${count} snapshots across ${n} variants?\n\n`
      +m.variants.map(v=>'· '+v.label).join('\n')
      +`\n\nThat is ${count*n} checks in one run. Variants that have no `
      +`baseline yet will record one — they appear as «new», not as a failure.`))return;
    runJob(api('/api/suite/run',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({matrix:true,scope})}),
      {title:'Matrix check · '+n+' variants',then:()=>SCREENS.baselines()});
  };
  acts.append(b);
}
