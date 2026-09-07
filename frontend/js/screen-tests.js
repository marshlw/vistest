/* VisTest - self-hosted visual regression testing.
 * Copyright (C) 2026 Kirill Kulagin
 * SPDX-License-Identifier: AGPL-3.0-or-later
 *
 * This file is part of VisTest. See LICENSE for the full terms and NOTICE for
 * the trademark and commercial-licensing terms. Removing this header does not
 * remove those obligations.
 */

/* Экран «Tests»: наши файлы тестов и файлы подключённых проектов.

   Чужие — только для чтения: это чужой репозиторий, у него свой git и своё ревью. */
SCREENS.tests=async function(arg){
  loadingScreen();
  const here=pageGuard();
  /* Тесты — закреплённого проекта, а не всех подключённых разом.

     Роут без `project` сканирует каждое подключение, и на экране оказывались
     файлы чужого репозитория вперемешку со своими: список, по которому нельзя
     сказать, чей он. Свои файлы сервиса приезжают всегда — они не принадлежат
     ни одному подключению и редактируются только здесь. */
  const key=(currentProject()||{}).kind==='project'?state.project:'';
  let data;
  try{data=await api('/api/tests?project='+encodeURIComponent(key));}
  catch(e){return errScreen(e);}
  const tests=data.tests||[];
  /* Тесты подключённых проектов — только чтение. Вкладка показывала ровно то,
     что сгенерировали мы сами, и для подключённого набора отвечала «0» при
     одиннадцати снимках: человек видел, что снимки чем-то сняты, но чем — в
     интерфейсе не было нигде. */
  const theirs=data.project_tests||[];
  if(!here())return;
  const s=$('#screen');s.innerHTML='';
  const page=el('div','page');s.append(page);
  const head=el('div','head');
  const left0=el('div','grow');
  left0.innerHTML=`<div class="eyebrow">LIBRARY · ${esc(projectLabel())}</div>
    <h1 class="h1">Tests</h1>
    <div class="lede">Our own files are editable — plain pytest, assembled from
      baselines and mouse recording. Files from connected projects are read-only:
      that is someone else's repository.
      <div class="mono faint" style="font-size:11.5px;margin-top:5px">${esc(data.dir||'')}</div></div>`;
  head.append(left0);
  const btns=el('div','acts');
  const gen=el('button','btn','Assemble from baselines');
  /* Набор и платформа спрашиваются, а не угадываются. Сборка молча смотрела в
     собственный набор сервиса на платформе самого сервиса и отвечала «0»
     человеку, у которого одиннадцать эталонов лежат в наборе подключённого
     проекта под docker. Угадать правильно она не могла — а вот показать, из
     чего собирает, может. */
  gen.onclick=assembleDialog;
  const rec=el('button','btn accent','Record with the mouse');rec.onclick=recordFlow;
  btns.append(gen,rec);head.append(btns);page.append(head);

  /* Пустой список тестов подключённого проекта имеет ровно три причины:
     смотрели не в тот каталог, шаблоны имён не те, файлов правда нет. Выглядят
     они одинаково — «No tests yet», — а лечатся по-разному. Поэтому каталог,
     шаблоны и то, откуда шаблоны взяты, показываются вслух. */
  const blind=(data.project_notes||[]).filter(n=>!n.found);
  if(!tests.length&&!theirs.length){
    const p=el('div','panel pad');
    p.append(el('div','empty','<b>No tests yet</b>'
      +'<div class="faint" style="margin-top:8px;font-size:12.5px">Record baselines with '
      +'the mouse, or assemble them from already captured baselines.</div>'));
    blind.forEach(n=>p.append(scanNote(n)));
    page.append(p);
    return;
  }
  if(blind.length){
    const p=el('div','panel pad');
    p.innerHTML='<div class="rail-h">CONNECTED PROJECTS WITH NO TEST FILES FOUND</div>';
    blind.forEach(n=>p.append(scanNote(n)));
    page.append(p);
  }
  const grid=el('div','tests-grid');const left=el('div');const right=el('div');
  right.id='testEditor';grid.append(left,right);page.append(grid);

  const addItem=(t,readonly)=>{
    const it=el('div','test-item');
    it.dataset.name=t.name;it.dataset.project=t.project||'';
    const tag=readonly?'<span class="tag">read-only</span>'
      :t.generated?'<span class="tag purple">build</span>'
      :'<span class="tag">manual</span>';
    /* Число тестов рядом с файлом. Человек считает свой набор ТЕСТАМИ, а
       список показывает ФАЙЛЫ: шесть строк против одиннадцати эталонов
       выглядят как потеря, хотя это одиннадцать тестов в шести файлах. */
    const n=t.tests;
    it.innerHTML=`<div class="tn">${esc(t.short||t.name)}</div>
      <div class="tm">${tag}${n!=null?`<span>${n} ${n===1?'test':'tests'}</span>`:''
        }<span>${(t.size/1024).toFixed(1)} KB</span>${
        t.has_backup?'<span class="faint">has .bak</span>':''}</div>`
      +(readonly?`<div class="mono faint" style="font-size:11px;margin-top:3px">${esc(t.name)}</div>`:'');
    hit(it,()=>openTest(t.name,t.project||''));left.append(it);
  };

  if(tests.length){
    left.append(el('div','field-lbl','Ours'));
    tests.forEach(t=>addItem(t,false));
  }
  if(theirs.length){
    const notes={};(data.project_notes||[]).forEach(n=>{notes[n.project]=n;});
    const by={};theirs.forEach(t=>{(by[t.project]=by[t.project]||[]).push(t);});
    Object.keys(by).sort().forEach(key=>{
      const n=notes[key]||{};
      const files=by[key].length;
      const cnt=by[key].reduce((a,t)=>a+(t.tests||0),0);
      /* Заголовок группы называет ОБА числа. Одно из них человек держит в
         голове, другое видит на экране, и пока называется только второе,
         разница читается как потеря. */
      const head=el('div','field-lbl',esc(by[key][0].project_name||key)
        +` <span class="mono faint">${files} ${files===1?'file':'files'}`
        +(cnt?` · ${cnt} ${cnt===1?'test':'tests'}`:'')+'</span>');
      head.style.marginTop=tests.length?'18px':'';
      left.append(head);
      /* Почему только чтение: это чужой репозиторий, у него свой git и своё
         ревью. Кнопки «сохранить» здесь нет и не будет. */
      const note=el('div','muted');
      note.style.cssText='font-size:12px;margin:-4px 0 8px';
      note.textContent='connected project — read-only';
      left.append(note);
      /* Что лежит в репозитории мимо настроенного каталога. В список это не
         попадает: подключение говорит, где тесты, и подменять его догадкой
         нельзя. Но промолчать — значит оставить человека наедине с разницей
         между тем, что он знает, и тем, что видит. */
      if(n.outside)left.append(outsideNote(n));
      by[key].forEach(t=>addItem(t,true));
    });
  }

  const first=tests[0]||theirs[0];
  openTest((arg&&decodeURIComponent(arg))||first.name,
           (arg?'':(first.project||'')));
};
/* Из чего собирать — спрашивается вслух.

   Сборка брала набор и платформу по умолчанию, и умолчания эти взяты не
   оттуда: набор сервиса пуст, платформа сервиса — не та, на которой снимают.
   Человек нажимал кнопку и получал «baselines on the platform: 0» без единой
   подсказки, где они на самом деле лежат. Здесь он видит все наборы с числами
   сразу и выбирает из непустых. */
async function assembleDialog(){
  let scopes={scopes:[]};
  try{scopes=await api('/api/baselines/scopes');}catch(e){return toast(String(e.message||e),'err');}
  const sets=(scopes.scopes||[]).filter(s=>s.scope);
  const total=s=>(s.platforms||[]).reduce((a,p)=>a+(p.count||0),0);
  const wrap=el('div');
  wrap.innerHTML=`<div class="mhead"><div><h3>Assemble tests from baselines</h3>
    <div class="msub">One file per project, built from the baseline passports —
      addresses, window sizes, selectors and steps. Nothing is re-captured.</div></div>
    <button class="mclose" aria-label="Close">×</button></div>`;
  const body=el('div','pf-body');wrap.append(body);
  if(!sets.some(total)){
    body.append(el('div','empty','<b>There is not a single baseline in this '
      +'installation</b><div class="faint" style="margin-top:8px;font-size:12.5px">'
      +'Capture a page by URL on the Baselines screen, record one with the mouse, '
      +'or snap the VisTest set of a connected project.</div>'));
    const m=openModal(wrap);$('.mclose',wrap).onclick=closeModal;return m;
  }
  /* Предвыбор — непустое: тот же выбор, что делает экран «Baselines», и по
     той же причине. Пустой набор по умолчанию не ответ ни там, ни здесь. */
  let scope=nonEmptyScope(sets,currentScope()||'global');
  let platform='';
  const plats=el('div');
  const setBox=el('div');
  body.append(el('div','field-lbl','SET'),setBox,
              el('div','field-lbl','PLATFORM'),plats);
  const paintPlatforms=()=>{
    plats.innerHTML='';
    const here=sets.find(s=>s.scope===scope)||{};
    const list=(here.platforms||[]).filter(p=>p.count);
    if(!list.length){
      plats.append(el('div','muted','This set has no baselines yet.'));
      platform='';return;
    }
    if(!list.some(p=>p.platform===platform))
      platform=list.reduce((a,b)=>(b.count>a.count?b:a)).platform;
    list.forEach(p=>{
      const row=el('label','pick'+(p.platform===platform?' on':''));
      row.innerHTML=`<b class="mono">${esc(p.platform)}</b>
        <span class="mono faint">${p.count} ${p.count===1?'baseline':'baselines'}</span>`;
      row.onclick=()=>{platform=p.platform;paintPlatforms();};
      plats.append(row);
    });
  };
  sets.forEach(s=>{
    const n=total(s);
    const row=el('label','pick'+(s.scope===scope?' on':'')+(n?'':' off'));
    row.innerHTML=`<b>${esc(s.label||s.scope)}</b>
      <span class="mono faint">${n} ${n===1?'baseline':'baselines'}</span>`;
    if(!n)row.title='Nothing to build from in this set';
    else row.onclick=()=>{scope=s.scope;paintPlatforms();
      $$('.pick',setBox).forEach(x=>x.classList.remove('on'));row.classList.add('on');};
    setBox.append(row);
  });
  paintPlatforms();
  const foot=el('div','macts');
  const go=el('button','btn accent','Assemble');
  go.onclick=()=>{
    if(!platform)return toast('This set has no baselines to build from','err');
    closeModal();
    const payload={platform};
    if(scope&&scope!=='global')payload.scope=scope;
    runJobLog(api('/api/tests/codegen',{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}),
      {title:'Assemble tests from baselines',
       sub:(sets.find(s=>s.scope===scope)||{}).label+' · '+platform,
       then:()=>SCREENS.tests()});
  };
  const cancel=el('button','btn','Cancel');cancel.onclick=closeModal;
  foot.append(cancel,go);wrap.append(foot);
  const m=openModal(wrap);$('.mclose',wrap).onclick=closeModal;
  return m;
}
/* «Мы смотрели только сюда» — это ответ, «не нашлось» — нет.

   Файлы за пределами настроенного каталога в список не добавляются: где лежат
   тесты, говорит подключение, и подменять его догадкой нельзя. Но назвать их
   надо: именно здесь у человека расходится то, что он знает о своём наборе, и
   то, что показывает экран. */
function outsideNote(n){
  const box=el('div','muted');
  box.style.cssText='font-size:12px;margin:0 0 10px;line-height:1.5';
  const head=el('div');
  head.innerHTML=`<b>${n.outside}</b> more matching `
    +(n.outside===1?'file':'files')+' elsewhere in the repository, outside '
    +'this connection\'s tests directory — not listed here.';
  box.append(head);
  (n.outside_sample||[]).forEach(p=>{
    const row=el('div','mono faint');
    row.style.cssText='font-size:11px;margin-top:2px';
    row.textContent=p;box.append(row);
  });
  if(n.outside>(n.outside_sample||[]).length){
    const more=el('div','faint');more.style.fontSize='11px';
    more.textContent='…and '+(n.outside-(n.outside_sample||[]).length)+' more';
    box.append(more);
  }
  const hint=el('div');hint.style.cssText='margin-top:6px';
  hint.textContent='Widen the «tests» field of the connection on the Projects '
    +'screen to include them.';
  box.append(hint);
  return box;
}
/* Отчёт о поиске по одному проекту: где искали, чем искали, откуда взяты
   шаблоны. Это не диагностика ради диагностики — это единственный способ
   отличить «каталог не тот» от «файлы называются иначе», не заходя на сервер. */
function scanNote(n){
  const box=el('div');
  box.style.cssText='border-top:1px solid var(--line);margin-top:12px;padding-top:12px';
  const title=el('div');
  title.style.cssText='font-size:13px;font-weight:600;margin-bottom:6px';
  title.textContent=n.project_name||n.project;
  box.append(title);
  const row=(k,v,warn)=>{
    const r=el('div');
    r.style.cssText='display:flex;gap:10px;font-size:12px;padding:2px 0'
      +(warn?';color:var(--warn)':'');
    const key=el('span','faint mono');
    key.style.cssText='min-width:150px;font-size:11px';
    key.textContent=k;
    const val=el('span','mono');val.style.fontSize='11.5px';val.textContent=v;
    r.append(key,val);return r;
  };
  box.append(row('LOOKED IN',n.dir||'—',!n.exists));
  if(!n.exists)box.append(row('','this directory does not exist',true));
  box.append(row('FILE PATTERNS',(n.patterns||[]).join('  ')));
  box.append(row('PATTERNS FROM',n.patterns_from||'—'));
  box.append(row('FOUND',String(n.found||0),!n.found));
  /* Ноль здесь и непустой репозиторий рядом — это не «тестов нет», а
     «настроен не тот каталог», и это разные действия. */
  if(n.outside)box.append(row('ELSEWHERE IN THE REPO',
    n.outside+' matching, outside this directory',true));
  const hint=el('div','faint');
  hint.style.cssText='font-size:12px;margin-top:8px;line-height:1.5';
  hint.textContent=n.exists
    ? 'The patterns come from the project\'s own pytest configuration — if its '
      +'test files are named differently, set python_files there and they will '
      +'show up here. Snapshots are captured by the project\'s pytest either way.'
    : 'Point the project at the right directory on the Projects screen — the '
      +'«tests» field of the connection.';
  box.append(hint);
  return box;
}
async function openTest(name,project){
  $$('#screen .test-item').forEach(x=>x.classList.toggle(
    'active',x.dataset.name===name&&(x.dataset.project||'')===(project||'')));
  const box=$('#testEditor');if(!box)return;box.innerHTML='<div class="loading-wrap"><span class="spin"></span></div>';
  let d;try{d=await api('/api/tests/source?name='+encodeURIComponent(name)
    +(project?'&project='+encodeURIComponent(project):''));}
  catch(e){box.innerHTML='';toast(String(e.message||e),'err');return;}
  renderEditor(d);
}
function renderEditor(d){
  const box=$('#testEditor');box.innerHTML='';
  const readonly=d.editable===false;
  const wrap=el('div','editor-wrap');
  const head=el('div','editor-head');
  head.innerHTML=`<span class="fn">${esc(d.short||d.name)}</span>`
    +(readonly?'<span class="tag">read-only</span>'
      :d.generated?'<span class="tag purple">build</span>'
      :'<span class="tag">manual</span>')
    +(d.project?`<span class="faint mono" style="font-size:11.5px">${esc(d.project)}</span>`:'');
  const sp=el('div','sp');
  const dirty=el('span');dirty.style.cssText='display:none;align-items:center;gap:6px;color:var(--warn);font-size:12.5px';
  dirty.innerHTML='<span class="dirty-dot"></span>not saved';
  const runb=el('button','btn sm','▷ Run');
  /* Отдельная кнопка выбора, а не меню на самом «Run».
     «Прогнать» — действие, которое делают десять раз за час, и превращать его
     в два щелчка ради выбора, который меняют раз в день, значит наказать
     частое ради редкого. */
  const runpick=el('button','btn sm','▾');
  runpick.title='Choose a browser — one, each separately, or all in one run';
  const saveb=el('button','btn dark sm','Save');
  sp.append(dirty,runb,runpick);
  /* Чужой файл не редактируется, и кнопка «Save» у него не появляется вовсе.
     Показать её отключённой значило бы предложить действие, которого нет. */
  if(!readonly)sp.append(saveb);
  head.append(sp);
  const ta=el('textarea','editor');ta.value=d.text||'';ta.spellcheck=false;
  if(readonly){ta.readOnly=true;ta.style.background='var(--panel)';}
  wrap.append(head,ta);box.append(wrap);

  /* Что снимает этот файл — связь в обратную сторону. Карточка снимка называет
     тест; без этого списка ответить на «а что вообще снимает этот тест» можно
     было бы только перебором всех карточек. */
  const shots=d.snapshots||[];
  if(shots.length){
    const rail=el('div','panel pad');rail.style.marginTop='14px';
    rail.innerHTML='<div class="rail-h">SNAPSHOTS IT CAPTURES · '+shots.length+'</div>';
    shots.slice(0,40).forEach(x=>{
      const row=el('div');
      row.style.cssText='display:flex;gap:10px;align-items:center;padding:5px 0;font-size:13px';
      const link=el('a',null,esc(x.name));
      link.href=snapshotHash(x.scope==='global'?undefined:x.scope,x.platform,x.name);
      link.style.cursor='pointer';
      row.append(link,el('span','faint mono',esc(x.platform)));
      row.lastChild.style.fontSize='11.5px';
      /* «Снят этим тестом» и «собран под этот тест» — разные утверждения.
         Собранный тест связан со своими эталонами с момента сборки, но ещё ни
         разу не выполнялся, и выдавать одно за другое нельзя. */
      if(x.via==='generated'){
        const tag=el('span','tag');tag.textContent='built from it';
        tag.title='This test was assembled from this baseline’s passport. '
          +'It has not run yet — the «captured by» link appears after the first run.';
        row.append(tag);
      }
      rail.append(row);
    });
    if(shots.length>40)rail.append(el('div','faint','…and '+(shots.length-40)+' more'));
    box.append(rail);
  }else if(readonly){
    const note=el('div','muted');
    note.style.cssText='font-size:12.5px;margin-top:12px;line-height:1.5';
    note.textContent='No snapshots are linked to this file yet. The link is '
      +'recorded at capture time — run the project once and it appears by itself.';
    box.append(note);
  }
  let saved=d.text||'',mtime=d.mtime;
  const markDirty=()=>{dirty.style.display=ta.value!==saved?'inline-flex':'none';};
  ta.oninput=markDirty;
  ta.onkeydown=e=>{
    if(e.key==='Tab'){e.preventDefault();const a=ta.selectionStart,b=ta.selectionEnd;ta.value=ta.value.slice(0,a)+'    '+ta.value.slice(b);ta.selectionStart=ta.selectionEnd=a+4;markDirty();}
    if((e.ctrlKey||e.metaKey)&&(e.key==='s'||e.key==='ы')){e.preventDefault();doSave();}
  };
  async function doSave(){
    saveb.disabled=true;const old=saveb.textContent;saveb.innerHTML='<span class="spin"></span>';
    try{const r=await api('/api/tests/source',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:d.name,text:ta.value,expected_mtime:mtime})});
      saved=ta.value;mtime=r.mtime;dirty.style.display='none';toast('Saved · previous version in '+d.name+'.bak','ok');}
    catch(e){toast(String(e.message||e),'err');}
    finally{saveb.disabled=false;saveb.textContent=old;}
  }
  saveb.onclick=doSave;
  /* Чужой тест запускается ЧЕРЕЗ СВОЙ ПРОЕКТ: чужим интерпретатором, из чужого
     корня и с их conftest. Запустить его нашим pytest значило бы упереться в
     первый же их импорт. */
  /* Чужой тест запускается ЧЕРЕЗ СВОЙ ПРОЕКТ — чужим интерпретатором, из
     чужого корня и с их conftest. Браузер туда доходит тем же способом, что и
     при прогоне всего проекта, и может не дойти вовсе: об этом честно скажет
     сам роут, а меню покажет причину в подсказке. */
  /* Как это называется в подписи. Подключённый набор может гоняться чем
     угодно — `npx playwright test`, `mvn test`, — и слово «pytest» над
     живым логом чужого инструмента просто неверно. */
  const how_run=(d.runner==='command')?(d.suite||'run'):'pytest';
  const fire=(how={})=>d.project
    ? followRun(api(`/api/projects/${encodeURIComponent(d.project)}/run`,
        {method:'POST',headers:{'Content-Type':'application/json'},
         body:JSON.stringify({only:d.name,...how})}),
        {title:how_run+' '+(d.short||d.name)})
    : followRun(api('/api/tests/run',{method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({path:d.path||('tests/'+d.name),...how})}),
        {title:'pytest '+d.name});
  /* Один прогон — с живым логом: разница между «тесты не запустились» и «тесты
     не дошли до сравнения» видна только в выводе. Несколько прогонов сразу —
     тостами: шесть модалок с логами друг поверх друга не читает никто. */
  runb.onclick=()=>d.project
    ? runJobLog(api(`/api/projects/${encodeURIComponent(d.project)}/run`,
        {method:'POST',headers:{'Content-Type':'application/json'},
         body:JSON.stringify({only:d.name})}),
        {title:how_run+' '+(d.short||d.name),
         sub:d.runner==='command'
           ? 'Runs in the project '+d.project+', by its own command.'
           : 'Runs in the project '+d.project+', with its own interpreter and conftest.'})
    : runJobLog(api('/api/tests/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:d.path||('tests/'+d.name)})}),
        {title:'pytest '+d.name,sub:'Runs on the service machine.'});
  runpick.onclick=e=>runBrowserMenu(e,{
    run:how=>fire(how),
    refusal:d.browser_refusal||'',
    label:'Run '+(d.short||d.name)});
}

