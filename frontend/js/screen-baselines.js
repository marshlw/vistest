/* Экран «Baselines»: наборы, платформы, карточки снимков, съёмка по адресу. */
SCREENS.baselines=async function(){
  loadingScreen();
  const here=pageGuard();
  let scopes={scopes:[]};
  try{scopes=await api('/api/baselines/scopes');}catch{}
  /* Набор по умолчанию — собственный набор сервиса. Но у человека, который
     только что подключил проект, собственный набор пуст, а одиннадцать
     эталонов лежат в наборе проекта: экран отвечал «0 baselines» ровно там,
     где их видно на соседней вкладке. Пока набор не выбран руками, открываем
     непустой — выбор человека приоритетнее и не переписывается. */
  const scope=state.scope||nonEmptyScope(scopes.scopes,'global');
  const sq=scope!=='global'?'&scope='+encodeURIComponent(scope):'';
  let d;
  try{
    d=await api('/api/baselines/detail?'+(state.platform?'platform='+encodeURIComponent(state.platform):'')+sq);
  }catch(e){return errScreen(e);}

  const plats=d.platforms||[];
  /* То же и с платформой: `d.current` — платформа МАШИНЫ СЕРВИСА, а снимает
     не она. Windows-сервис и docker-эталоны не совпадают ни разу, и
     платформа по умолчанию оказывалась пустой. */
  let cur=state.platform||d.current||(plats[0]&&plats[0].platform);
  if(!state.platform){
    const has=plats.filter(p=>(p.count||0)>0);
    if(has.length&&!has.some(p=>p.platform===cur))
      cur=has.reduce((a,b)=>(b.count>a.count?b:a)).platform;
  }
  const platObj=plats.find(p=>p.platform===cur)||plats[0]||{items:[]};
  const items=platObj.items||[];
  const runnable=items.filter(i=>i.runnable).length;

  if(!here())return;
  const s=$('#screen');s.innerHTML='';
  const page=el('div','page');s.append(page);

  const head=el('div','head');
  const left=el('div','grow');
  left.innerHTML=`<div class="eyebrow">LIBRARY · ${esc(d.scope_label||'own set')} · ${esc(cur||'')}</div>
    <h1 class="h1">${fmtInt(platObj.count||items.length)} ${plural(platObj.count||items.length,'baseline','baselines','baselines')}</h1>
    <div class="lede">${runnable} can be re-captured by URL or by their test. The rest
      only exist inside a test run.</div>`;
  head.append(left);

  const acts=el('div','acts');
  const rec=el('button','btn accent','Record with the mouse');rec.onclick=recordFlow;
  const cap=el('button','btn','Capture by URL');
  cap.onclick=()=>{const f=$('#snUrl');if(f){f.scrollIntoView({block:'center'});f.focus();}};
  const checkAll=el('button','btn','Check all · '+(runnable||items.length));
  checkAll.disabled=!runnable;
  if(!runnable)checkAll.title='No snapshot here has an address or a test bound yet.';
  checkAll.onclick=()=>runJob(api('/api/suite/run',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({platform:cur,scope})}),
    {title:'Baseline check',then:()=>SCREENS.baselines()});
  acts.append(rec,cap,checkAll);head.append(acts);
  page.append(head);
  refreshRecordBtn(rec);
  /* Прогон по всей матрице — отдельной кнопкой, а не режимом у «Check all».
     Это разные по цене действия: одно проверяет открытый набор, второе —
     шесть наборов и вшестеро дольше. Кнопка появляется только там, где
     матрица описана: предлагать её при одном варианте значит обещать то,
     чего нет. */
  addMatrixRun(acts,scope,runnable||items.length);

  /* Набор и платформа — одной строкой над сеткой, а не колонкой слева.

     Слева они забирали двести пикселей ширины на каждом экране, включая те,
     где их не переключают неделями. Здесь они читаются как то, чем и являются:
     подпись «что сейчас показано», по которой можно щёлкнуть. */
  const bar=el('div','bar');
  const sets=scopes.scopes||[];
  if(sets.length>1){
    const seg=el('div','seg');
    seg.append(el('span','k','SET'));
    sets.forEach((sc,i)=>{
      const n=(sc.platforms||[]).reduce((a,x)=>a+(x.count||0),0);
      if(i)seg.append(el('span','sep','|'));
      const on=sc.scope===scope;
      const a=el(on?'b':'a',null,`${esc(sc.label||sc.scope)} <span class="mono faint" style="font-size:11px">${n}</span>`);
      /* Что такое «набор» — вопрос, который задают ровно один раз и без ответа
         на который переключатель бессмыслен. */
      tip(a,sc.scope==='global'
        ? 'Captured by VisTest and stored outside your repository. These are the '
          +'pictures VisTest owns.'
        : 'Comes from a connected project’s own baseline set — the PNG files that '
          +'live in its repository. VisTest reads them in place and never edits them.'
        );
      if(sc.empty_hint)tip(a,sc.empty_hint);
      if(!on){a.href='#';a.onclick=e=>{e.preventDefault();state.scope=sc.scope;state.platform=null;SCREENS.baselines();};}
      seg.append(a);
    });
    bar.append(seg);
  }
  if(plats.length){
    const seg=el('div','seg');
    seg.append(el('span','k','PLATFORM'));
    plats.forEach((pl,i)=>{
      if(i)seg.append(el('span','sep','|'));
      const on=pl.platform===cur;
      const a=el(on?'b':'a',null,`${esc(variantLabel(pl.platform))} <span class="mono faint" style="font-size:11px">${pl.count||0}</span>`);
      /* Подпись человеческая, ключ — в подсказке. Ключ это адрес каталога, и
         с матрицей их становится шесть: список из
         `linux-chromium-1x-390x844` перестаёт читаться ровно тогда, когда
         переключаться между ними и начинают. */
      a.dataset.platform=pl.platform;
      if(!on){a.href='#';a.onclick=e=>{e.preventDefault();state.platform=pl.platform;SCREENS.baselines();};}
      /* Одинаковые эталоны на разных машинах возможны только в docker: отрисовка
         шрифтов в Windows и Linux физически разная. */
      tip(a,pl.platform+' — baselines of one variant are only comparable within it. '
        +'Font rendering on Windows and on Linux is physically different, so the same '
        +'page captured on two machines is two different pictures.');
      seg.append(a);
    });
    bar.append(seg);
  }
  const find=el('div','find');
  find.innerHTML='<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="7" cy="7" r="4.6"/><path d="M10.5 10.5L14 14"/></svg>';
  const fi=el('input');fi.placeholder='Filter by name, selector, viewport';
  fi.value=state.blFilter||'';
  fi.oninput=()=>{state.blFilter=fi.value;paintCards();};
  find.append(fi);bar.append(find);
  page.append(bar);

  const gc=el('div','grid-cards');
  page.append(gc);

  function paintCards(){
    const q=(state.blFilter||'').trim().toLowerCase();
    const shown=!q?items:items.filter(it=>
      [it.name,it.short,it.viewport,it.selector,it.url,
       (it.source||{}).test].some(v=>String(v||'').toLowerCase().includes(q)));
    gc.innerHTML='';
    if(!shown.length){
      const e=el('div');e.style.cssText='grid-column:1/-1;background:var(--panel)';
      e.append(el('div','empty',q?'Nothing matches the filter'
        :'There are no baselines on this platform yet'));
      gc.append(e);return;
    }
    shown.forEach(it=>gc.append(baselineCard(it,cur,scope)));
  }
  paintCards();

  /* Съёмка новой страницы — под сеткой, а не над ней. Форма на четыре поля
     каждый раз отодвигала вниз то, ради чего на экран и заходят. */
  page.append(sectionHead('Capture a baseline of a new page',
    'the browser opens on the service machine'));
  const snap=el('div','panel pad');
  snap.append(el('div','lede','Log in and navigate where you need — the recording '
    +'panel survives transitions and does not get into the baseline.'));
  const row=el('div');
  row.style.cssText='display:grid;grid-template-columns:2fr 1fr 1fr 1fr auto;'
    +'gap:12px;margin-top:14px;align-items:end';
  row.innerHTML=`<div><label class="flabel">PAGE URL</label><input class="inp mono" id="snUrl" placeholder="https://my-app.local/checkout"></div>
    <div><label class="flabel">NAME</label><input class="inp mono" id="snName" placeholder="checkout"></div>
    <div><label class="flabel">WINDOW</label><input class="inp mono" id="snVp" placeholder="1440×900"></div>
    <div><label class="flabel">STEPS</label><input class="inp mono" id="snFlow" placeholder="flow: login"></div>`;
  const snb=el('button','btn dark','Capture');
  snb.onclick=()=>{
    const url=$('#snUrl').value.trim();if(!url)return toast('Set a URL','err');
    /* Тело запроса собирается как `targets: [...]` — именно этого роут и ждёт.
       Раньше поля уходили верхним уровнем, `targets` приходил пустым, и кнопка
       отвечала «at least one target with a url is required» при любом нажатии.
       Поле «Steps» тоже уходило как `flow` и молча игнорировалось. */
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

function baselineCard(it,platform,scope){
  const c=el('div','snap-card');
  const th=el('div','thumb');
  if(it.thumb){const im=el('img');im.src=it.thumb;im.onerror=()=>{im.remove();};th.append(im);}
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

   Спрашивается у сервиса, а не собирается по конфигу на клиенте: матрица
   живёт в `vistest.yaml` на машине сервиса, и второе место, где её
   раскрывают, разошлось бы с первым на первой же правке базового размера. */
async function addMatrixRun(acts,scope,count){
  let m;
  try{m=await api('/api/matrix');}catch{return;}
  if(!m||!m.declared||(m.variants||[]).length<2)return;
  if(!document.body.contains(acts))return;

  const n=m.variants.length;
  const b=el('button','btn accent',`Run the matrix · ${n} variants`);
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
      {title:`Matrix check · ${n} variants`,then:()=>SCREENS.baselines()});
  };
  acts.append(b);
}
