/* VisTest - self-hosted visual regression testing.
 * Copyright (C) 2026 Kirill Kulagin
 * SPDX-License-Identifier: AGPL-3.0-or-later
 *
 * This file is part of VisTest. See LICENSE for the full terms and NOTICE for
 * the trademark and commercial-licensing terms. Removing this header does not
 * remove those obligations.
 */

/* Экран «Projects»: подключение чужих наборов тестов, прогон, диагностика. */
SCREENS.projects=async function(){
  loadingScreen();
  /* Вкладка требует роль admin И запроса с машины сервиса: подключение
     проекта запускает на сервере произвольный процесс. Ограничение осознанное,
     но голый 403 посреди пустого экрана выглядит поломкой, а не решением, —
     поэтому объясняем, что именно закрыто и чем открывается. */
  let data;
  try{data=await api('/api/projects');}
  catch(e){return projectsBlocked(e);}
  const projects=data.projects||[];
  const screen=$('#screen');screen.innerHTML='';
  const s=el('div','page narrow');screen.append(s);
  s.innerHTML='<div class="eyebrow">SETUP</div>'
    +'<h1 class="h1">Projects</h1>'
    +'<div class="lede">Your tests stay yours: the code is not edited, baselines are '
    +'read in place, in git. VisTest substitutes just one method — the one your '
    +'project uses to compare a screenshot against a baseline.</div>';
  /* Подключение — одной склеенной строкой, как в остальных местах интерфейса:
     путь и кнопка читаются как одно действие, а не как форма из двух частей. */
  const bar=el('div','joined');
  const inp=el('input');
  inp.placeholder='D:\\project\\my-tests — path on the service machine';
  inp.style.flex='1';
  const btn=el('button',null,'Inspect');
  btn.onclick=async()=>{
    const root=inp.value.trim();if(!root)return toast('Set a path','err');
    btn.disabled=true;const t=btn.textContent;btn.textContent='Inspecting…';
    try{showProposal(await api('/api/projects/discover',{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify({root})}));}
    catch(e){toast(String(e.message||e),'err');}
    finally{btn.disabled=false;btn.textContent=t;}};
  bar.append(inp,btn);s.append(bar);

  const add=el('div','panel flush');add.style.padding='14px 16px';
  add.append(el('div','lede','VisTest will find baseline folders, pytest markers '
    +'and the comparison point on its own. Nothing is saved until you press '
    +'«Connect».'));

  /* Архив — второй путь для случая, когда репозитория на машине сервиса нет.
     Он стоит ниже и подписан как альтернатива: два равноправных поля ввода
     рядом заставляли бы выбирать до того, как человек понял разницу. */
  const zrow=el('div');
  zrow.style.cssText='display:flex;gap:10px;margin-top:12px;align-items:center;flex-wrap:wrap';
  const lbl=el('span','faint','or upload an archive:');lbl.style.fontSize='12.5px';
  const fileInp=el('input');fileInp.type='file';fileInp.accept='.zip';
  fileInp.style.fontSize='12.5px';
  const nameInp=el('input','inp mono');nameInp.placeholder='name (optional)';
  nameInp.style.cssText='max-width:200px';
  const zbtn=el('button','btn','Upload .zip');
  zrow.append(lbl,fileInp,nameInp,zbtn);add.append(zrow);
  zbtn.onclick=async()=>{
    const f=fileInp.files&&fileInp.files[0];
    if(!f)return toast('Choose a .zip archive','err');
    const fd=new FormData();fd.append('file',f);
    if(nameInp.value.trim())fd.append('name',nameInp.value.trim());
    zbtn.disabled=true;const t=zbtn.textContent;zbtn.textContent='Uploading…';
    try{showProposal(await api('/api/projects/upload',{method:'POST',body:fd}));}
    catch(e){toast(String(e.message||e),'err');}
    finally{zbtn.disabled=false;zbtn.textContent=t;}};
  add.append(el('div','fhint','The archive is unpacked into the service data; '
    +'project files are not modified.'));
  const preview=el('div');preview.id='projPreview';add.append(preview);
  s.append(add);

  if(!projects.length){s.append(el('div','panel','<div class="empty">No connected projects yet</div>'));return;}
  projects.forEach(p=>s.append(projectCard(p)));
};

/* Вкладка требует роль admin И запроса с машины сервиса: подключение проекта
   запускает на сервере произвольный процесс. Ограничение осознанное, но голый
   403 посреди пустого экрана выглядит поломкой, а не решением, — поэтому
   говорим, что именно закрыто и чем открывается. */
function projectsBlocked(e){
  const text=String(e&&e.message||e);
  const screen=$('#screen');screen.innerHTML='';
  const s=el('div','page narrow');screen.append(s);
  s.innerHTML='<div class="eyebrow">SETUP</div><h1 class="h1">Projects</h1>';
  const card=el('div','panel pad');card.style.marginTop='18px';
  const local=/local machine|VISTEST_PROJECTS_UI/i.test(text);
  card.innerHTML=`<h3 class="h2">${local
      ? 'This tab is open only on the service machine'
      : 'Not enough rights for this tab'}</h3>
    <p class="muted" style="margin-top:10px;line-height:1.6">${local
      ? 'Connecting a project starts an arbitrary process on the server, so by '
        +'default it is allowed only from the machine the service runs on. '
        +'Opening it to the network is a deliberate act by the administrator: '
        +'<code>VISTEST_PROJECTS_UI=all</code>.'
      : 'Connecting and configuring test suites is an administrator action. '
        +'Running an already connected suite is available to a reviewer.'}</p>
    <p class="mono faint" style="font-size:12px;margin-top:14px;white-space:pre-line">${esc(text)}</p>`;
  s.append(card);
}
function showProposal(r){
  const box=$('#projPreview');if(!box)return;box.innerHTML='';
  const p=Object.assign({},(r&&r.proposal)||{});
  if(!p.key){toast('The inspection returned nothing','err');return;}
  const card=el('div','panel pad');card.style.marginTop='16px';
  card.append(el('div','flabel','FOUND — CHECK THE NAME AND CONNECT'));
  const nm=el('input','inp');nm.value=p.name||p.key||'';nm.placeholder='project name';nm.style.marginTop='6px';
  card.append(labeled('Name',nm));
  const tests=p.tests||p.tests_glob||(r.discovered&&r.discovered.tests)||'—';
  const rows=el('div','pj-rows');rows.style.marginTop='12px';
  rows.innerHTML=`<div class="k">Key</div><div class="v">${esc(p.key)}</div>
    <div class="k">Root</div><div class="v">${esc(p.root||r.root||'—')}</div>
    <div class="k">Tests</div><div class="v">${esc(tests)}</div>`;
  card.append(rows);
  const probs=p.problems||(r.discovered&&r.discovered.problems)||[];
  if(probs.length)card.append(el('div','muted','Remarks: '+probs.map(esc).join('; ')));
  const acts=el('div');acts.style.cssText='display:flex;gap:10px;margin-top:14px';
  const connect=el('button','btn dark','Connect');
  const cancel=el('button','btn','Cancel');cancel.onclick=()=>box.innerHTML='';
  acts.append(connect,cancel);card.append(acts);box.append(card);
  connect.onclick=async()=>{
    p.name=nm.value.trim()||p.name||p.key;
    connect.disabled=true;connect.textContent='Connecting…';
    try{await api('/api/projects/'+encodeURIComponent(p.key),{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});
      toast('Project connected','ok');box.innerHTML='';SCREENS.projects();refreshCounts();}
    catch(e){toast(String(e.message||e),'err');connect.disabled=false;connect.textContent='Connect';}
  };
}
function projectCard(p){
  const c=el('div','pj-card');
  // p.adapter may come as an object (adapter config) — then esc gives
  // «[object Object]». We take a human-readable label from the mode.
  const adapter=(p.mode==='observe'||p.baseline_status==='external')?'review of ready PNG'
    :p.mode==='adapter'?'comparison by the VisTest engine'
    :(typeof p.adapter==='string'&&p.adapter)?p.adapter
    :'comparison by the VisTest engine';
  const tagcls=/PNG|ready/.test(adapter)?'amber':'green';
  const bc=p.baseline_counts||{};const bcount=typeof bc==='number'?bc:Object.values(bc).reduce((a,b)=>a+(b||0),0);

  /* Комплект, снятый самим VisTest, приходил в `baseline_status` с самого
     начала и не рисовался нигде. Из-за этого нельзя было понять, снят ли он
     вообще, сколько в нём снимков и на какой платформе, — а «Run on VisTest
     baselines» при этом предлагался кнопкой. */
  const bs=p.baseline_status||{};
  const vs=bs.vistest||[];
  const vtotal=bs.vistest_total!=null?bs.vistest_total:vs.reduce((a,x)=>a+(x.count||0),0);
  const here=vs.find(x=>x.platform===bs.current_platform);
  const vtText=vtotal
    ? `${vtotal} on ${vs.map(x=>esc(x.platform)+' ('+x.count+')').join(', ')}`
      +(here?'':' — nothing for this platform yet')
    : 'not captured yet';

  c.innerHTML=`<div class="ph"><span class="nm">${esc(p.name||p.key)}</span><span class="tag ${tagcls}">${esc(adapter)}</span><span class="root">${esc(p.root||'')}</span></div>
    <div class="pj-rows">
      <div class="k">Tests</div><div class="v">${esc(p.tests||p.tests_glob||'—')}</div>
      <div class="k">Interpreter</div><div class="v">${esc(p.python||p.interpreter||'—')}</div>
      <div class="k">Project PNGs</div><div class="v">${esc(p.baselines_summary||(bcount?bcount+' items':'—'))}</div>
      <div class="k">VisTest set</div><div class="v" data-vt-set>${vtText}</div>
      <div class="k">Last run</div><div class="v">${esc(p.last_run||'—')}</div>
    </div>`;

  if(vtotal){
    const open=el('button','btn sm','Open VisTest snapshots');
    open.onclick=()=>{
      state.scope='project:'+(p.key||p.name);
      if(here)state.platform=here.platform;
      location.hash='#/baselines';
      if(state.view==='baselines')SCREENS.baselines();
    };
    /* Ключ проекта приходит извне и попадал прямо в CSS-селектор: точка в
       ключе делала его селектором класса, а ключ с цифры — синтаксической
       ошибкой и пустой карточкой. Ищем по атрибуту. */
    const holder=c.querySelector('[data-vt-set]');
    if(holder){holder.append(document.createTextNode(' '));holder.append(open);}
  }

  const acts=el('div','acts');
  const runP=el('button','btn dark','Run on project baselines');
  runP.onclick=()=>runProject(p,{baselines:'project'});
  const runV=el('button','btn','Run on VisTest baselines');
  runV.onclick=()=>runProject(p,{baselines:'vistest'});
  const noise=el('button','btn','Measure noise');
  noise.onclick=()=>{location.hash='#/doctor';};
  const deps=el('button','btn','Install missing libraries');
  deps.onclick=()=>installDeps(p);
  /* Снять страницу движком VisTest, не заводя ради этого теста. Кнопка стоит
     на карточке проекта, а не на «Baselines»: вопрос звучит как «добавь этот
     экран проекта в набор», и набор здесь уже выбран — сам проект. */
  const shot=el('button','btn','Capture a screenshot');
  shot.onclick=()=>captureDialog(p,vs,bs.current_platform);
  const more=el('button','btn ghost icon','⋯');more.onclick=e=>projectMenu(e,p);
  acts.append(runP,runV,shot,noise,deps,more);c.append(acts);
  c.append(el('div','foot','«Run on project baselines» compares against the project own PNGs; «Run on VisTest baselines» against the set VisTest captured (snap it first from «⋯» if empty). More actions are under «⋯».'));
  return c;
}
/* Съёмка страницы движком VisTest — прямо в набор проекта.

   Что здесь спрашивается и почему именно это. Адрес и имя — очевидно. Область
   кадра — потому что это и есть формат снимка: длинный отчёт снимают целиком,
   шапку элементом, бесконечную ленту только экраном (Chromium физически не
   отдаёт полотно в тридцать тысяч пикселей). Размер окна — потому что вёрстка
   у ширины 1440 и 390 разная, и снимок без размера ничего не фиксирует.
   Шаги — потому что почти всё интересное лежит за входом.

   Всё это уходит в паспорт снимка и потому воспроизводится кнопкой
   «Re-capture» — иначе пересъёмка вернула бы другой кадр того же адреса, и
   разница читалась бы как изменение страницы. */
const VIEWPORT_PRESETS=['1440x900','1920x1080','1280x800','834x1112','390x844'];
function captureDialog(p,sets,current){
  const key=p.key||p.name;
  const scope='project:'+key;
  const plats=(sets||[]).map(x=>x.platform);
  const wrap=el('div');
  wrap.innerHTML=`<div class="mhead"><div><h3>Capture a screenshot · ${esc(p.name||key)}</h3>
    <div class="msub">The browser opens on the service machine and the shot goes
      into this project's VisTest set — with its passport, so it can be
      re-captured and checked by a button.</div></div>
    <button class="mclose" aria-label="Close">×</button></div>`;
  const body=el('div','pf-body');wrap.append(body);

  const fields=el('div');
  fields.innerHTML=`<label class="flabel">PAGE URL</label>
    <input class="inp mono" id="capUrl" placeholder="https://my-app.local/checkout">
    <div class="grid2" style="margin-top:10px">
      <div><label class="flabel">NAME</label>
        <input class="inp mono" id="capName" placeholder="checkout"></div>
      <div><label class="flabel">WINDOW</label>
        <input class="inp mono" id="capVp" list="capVpList" value="1440x900">
        <datalist id="capVpList">${VIEWPORT_PRESETS.map(v=>`<option value="${v}">`).join('')}</datalist></div>
    </div>`;
  body.append(fields);

  body.append(el('div','field-lbl','WHAT GOES INTO THE FRAME'));
  let area='full';
  const areaBox=el('div');body.append(areaBox);
  const sel=el('div');
  sel.style.marginTop='10px';
  sel.innerHTML='<label class="flabel">ELEMENT SELECTOR</label>'
    +'<input class="inp mono" id="capSel" placeholder="header.site-header">';
  const AREAS=[
    ['full','The whole page','scrolled end to end'],
    ['viewport','The window only','first screen — for infinite feeds'],
    ['element','One element','by CSS selector'],
  ];
  const paintAreas=()=>{
    areaBox.innerHTML='';
    AREAS.forEach(([id,label,note])=>{
      const row=el('label','pick'+(id===area?' on':''));
      row.innerHTML=`<b>${label}</b><span class="mono faint">${note}</span>`;
      row.onclick=()=>{area=id;paintAreas();
        sel.style.display=area==='element'?'':'none';};
      areaBox.append(row);
    });
  };
  paintAreas();
  body.append(sel);sel.style.display='none';

  const rest=el('div');
  rest.style.marginTop='10px';
  rest.innerHTML=`<div class="grid2">
    <div><label class="flabel">STEPS BEFORE THE SHOT</label>
      <input class="inp mono" id="capFlow" placeholder="flow: login"></div>
    <div><label class="flabel">EXTRA WAIT, MS</label>
      <input class="inp mono" id="capWait" placeholder="0"></div>
  </div>`;
  body.append(rest);

  /* Платформа — не выбор вкуса: эталоны одной платформы сравнимы только внутри
     неё, отрисовка шрифтов в Windows и Linux физически разная. Показываем ту,
     на которой снимает сервис, и уже имеющиеся у проекта. */
  const note=el('div','info-note');
  note.textContent='The shot lands on the platform of the service machine'
    +(current?' — '+current:'')+'.'
    +(plats.length?' This project already has: '+plats.join(', ')+'.':'')
    +' Baselines of one platform are only comparable within it.';
  body.append(note);

  const foot=el('div','macts');
  const cancel=el('button','btn','Cancel');cancel.onclick=closeModal;
  const go=el('button','btn accent','Capture');
  go.onclick=()=>{
    const url=($('#capUrl',wrap).value||'').trim();
    if(!url)return toast('Set a URL','err');
    const target={url,area};
    const nm=($('#capName',wrap).value||'').trim();if(nm)target.name=nm;
    const vp=($('#capVp',wrap).value||'').trim();if(vp)target.viewport=vp;
    if(area==='element'){
      const s=($('#capSel',wrap).value||'').trim();
      if(!s)return toast('A selector is required to capture one element','err');
      target.selector=s;
    }
    const flow=($('#capFlow',wrap).value||'').trim().replace(/^flow:\s*/i,'');
    if(flow)target.steps=[{action:'flow',name:flow}];
    const wait=parseInt(($('#capWait',wrap).value||'').trim(),10);
    if(wait>0)target.wait=wait;
    closeModal();
    runJobLog(api('/api/baselines/snap',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({targets:[target],scope,update:true})}),
      {title:'Capture a screenshot · '+(p.name||key),
       sub:'into the VisTest set of this project',
       then:()=>SCREENS.projects()});
  };
  foot.append(cancel,go);wrap.append(foot);
  const m=openModal(wrap);$('.mclose',wrap).onclick=closeModal;
  setTimeout(()=>{const f=$('#capUrl',wrap);if(f)f.focus();},30);
  return m;
}
function runProject(p,opts){
  const key=p.key||p.name;
  const label=opts.baselines==='vistest'?'VisTest baselines':(opts.baselines==='project'?'project baselines':'baselines');
  // Прогон чужих тестов показываем с живым логом, а не одним тостом: разница
  // между «тесты не запустились» и «тесты не дошли до сравнения» видна только
  // в выводе, и именно её приходится выяснять чаще всего.
  return runJobLog(api(`/api/projects/${encodeURIComponent(key)}/run`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(opts)}),
    {title:'Run '+(p.name||key)+' · '+label,
     sub:'their pytest, their output — as is',
     then:()=>SCREENS.projects()});
}
async function installDeps(p){
  const key=p.key||p.name;
  await runJobLog(api(`/api/projects/${encodeURIComponent(key)}/deps`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({})}),{title:'Install missing libraries',sub:'installing only what preflight reported as missing'});
  SCREENS.projects();
}
async function showPreflight(p){
  const key=p.key||p.name;
  const wrap=el('div');
  wrap.innerHTML=`<div class="mhead"><div><h3>Test collection · ${esc(p.name||key)}</h3><div class="msub">bringing up your conftest and resolving fixtures, running nothing</div></div><button class="mclose" aria-label="Close">×</button></div>`;
  const body=el('div','pf-body');body.innerHTML='<div class="jobbar"><span class="spin"></span> collecting…</div>';
  wrap.append(body);openModal(wrap,{wide:true});$('.mclose',wrap).onclick=closeModal;
  const render=(r)=>{
    body.innerHTML='';
    const ok=r.ok&&r.collected>0;
    const head=el('div','pf-status '+(ok?'ok':'bad'));
    head.innerHTML=ok
      ? `✓ Tests collected: <b>${r.collected}</b> — can be run`
      : (r.collected?`Collected ${r.collected}, but there are remarks`:'✗ Not a single test was collected');
    body.append(head);
    const rows=el('div','pj-rows');rows.style.marginTop='12px';
    rows.innerHTML=`<div class="k">Interpreter</div><div class="v mono">${esc(r.interpreter||'—')}${r.own_interpreter?' (VisTest environment)':''}</div>
      <div class="k">pytest</div><div class="v">${r.pytest?'yes':'no'}</div>
      <div class="k">Collected</div><div class="v">${r.collected||0}</div>`;
    body.append(rows);
    if(r.hint){const h=el('div','pf-hint');h.textContent=r.hint;body.append(h);}
    const chips=(title,arr)=>{if(!arr||!arr.length)return;const w=el('div','pf-chips');w.innerHTML=`<span class="pf-lbl">${esc(title)}</span>`+arr.map(x=>`<span class="chip">${esc(x)}</span>`).join('');body.append(w);};
    chips('No modules:',r.missing);
    chips('No plugins:',r.plugins);
    chips('Fixtures:',r.missing_fixtures);
    if(r.problems&&r.problems.length)chips('Remarks:',r.problems);
    // «Install dependencies» — if there is something to install and this is the VisTest environment.
    const canInstall=(r.missing&&r.missing.length)||(r.plugins&&r.plugins.length);
    const acts=el('div');acts.style.cssText='display:flex;gap:10px;margin-top:16px;flex-wrap:wrap';
    if(canInstall){
      const dep=el('button','btn dark','Install dependencies');
      dep.onclick=async()=>{await runJobLog(api(`/api/projects/${encodeURIComponent(key)}/deps`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({})}),{title:'Installing dependencies',sub:(r.missing||[]).concat(r.plugins||[]).join(', ')});load();};
      acts.append(dep);
    }
    const re=el('button','btn','Rebuild');re.onclick=()=>{load();};
    acts.append(re);
    if(ok){const runb=el('button','btn dark','Run');runb.onclick=()=>{closeModal();runJob(api(`/api/projects/${encodeURIComponent(key)}/run`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({})}),{title:'Run '+(p.name||key),then:()=>SCREENS.projects()});};acts.append(runb);}
    body.append(acts);
    if(r.output&&r.output.length){
      const det=el('details','pf-out');det.innerHTML='<summary>Build output</summary>';
      const pre=el('pre');pre.textContent=(Array.isArray(r.output)?r.output.join('\n'):String(r.output)).slice(-4000);
      det.append(pre);body.append(det);
    }
  };
  async function load(){
    body.innerHTML='<div class="jobbar"><span class="spin"></span> collecting…</div>';
    try{render(await api(`/api/projects/${encodeURIComponent(key)}/check`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({})}));}
    catch(e){body.innerHTML='';const er=el('div','pf-status bad');er.textContent='Could not collect: '+String(e.message||e);body.append(er);}
  }
  load();
}
function projectMenu(e,p){
  e.stopPropagation();closeMenus();const m=el('div','menu');const rc=e.target.getBoundingClientRect();
  m.style.top=(rc.bottom+6)+'px';m.style.left=(rc.left-140)+'px';
  const key=encodeURIComponent(p.key||p.name);
  m.append(mkItem('Environment & arguments',()=>{closeMenus();projectEnvModal(p);}));
  m.append(mkItem('Collect tests (diagnose)',()=>{closeMenus();showPreflight(p);}));
  m.append(mkItem('Run like in CI',()=>runProject(p,{ci:true})));
  m.append(mkDiv());
  m.append(mkItem('Snap VisTest baselines',()=>{if(confirm('Capture the VisTest baseline set now? All snapshots will be recorded as the new baseline.'))runJobLog(api(`/api/projects/${key}/run`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({baselines:'vistest',update_baselines:true})}),{title:'Snap VisTest baselines',sub:'every snapshot of this run becomes the baseline',then:()=>SCREENS.projects()});}));
  m.append(mkItem('Reset baselines',()=>{if(confirm('Reset the project baselines?'))runJob(api(`/api/projects/${key}/baselines/reset`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({})}),{title:'Baseline reset'});}));
  m.append(mkDiv());
  m.append(mkItem('Disconnect the project',async()=>{if(confirm('Disconnect the project from VisTest?')){try{await api('/api/projects/'+key,{method:'DELETE'});toast('Disconnected','ok');SCREENS.projects();refreshCounts();}catch(err){toast(String(err.message||err),'err');}}}));
  document.body.append(m);stopClose(m);
}

/* Переменные окружения и аргументы pytest подключённого проекта.
   Ровно то место, куда указывает диагностика упавшего прогона: она называет
   переменную, которую читают их фикстуры, — и до сих пор завести её можно было
   только правкой projects.yaml руками на сервере. */
function projectEnvModal(p){
  const key=p.key||p.name;
  const wrap=el('div');
  wrap.innerHTML=`<div class="mhead"><div><h3>Environment &amp; arguments · ${esc(p.name||key)}</h3>
    <div class="msub">What their pytest gets on this run. Saved into the project description, applied on the next run.</div></div>
    <button class="mclose" aria-label="Close">×</button></div>`;
  const body=el('div','pf-body');wrap.append(body);
  openModal(wrap,{wide:true});$('.mclose',wrap).onclick=closeModal;

  // --- переменные окружения ---
  body.append(el('div','field-lbl','Environment variables'));
  const rows=el('div');
  const row=(name,value)=>{
    const r=el('div','env-edit');
    const n=el('input','inp');n.value=name||'';n.placeholder='ACME_PASSWORD';n.style.margin='0';n.dataset.k='name';
    const v=el('input','inp');v.value=value||'';v.placeholder='value or ${VISTEST_PASSWORD}';v.style.margin='0';v.dataset.k='value';
    const del=el('button','btn sm','Delete');del.onclick=()=>r.remove();
    r.append(n,v,del);return r;
  };
  Object.entries(p.env||{}).forEach(([n,v])=>rows.append(row(n,v)));
  if(!Object.keys(p.env||{}).length)rows.append(row('',''));
  body.append(rows);
  const addVar=el('button','btn','Add a variable');
  addVar.onclick=()=>rows.append(row('',''));
  addVar.style.marginTop='4px';body.append(addVar);

  body.append(el('div','info-note',
    'A value like <code>${VISTEST_PASSWORD}</code> is substituted from «Settings → Secrets» at run time. '
    + 'Use it instead of typing the password here: the project description lives in <code>projects.yaml</code>, '
    + 'which is meant to be committed to git. An <b>empty value does not mean an empty string</b> — it REMOVES '
    + 'the variable from the run environment.'));

  // --- чем запускать ---
  const runLbl=el('div','field-lbl','How the tests are started');runLbl.style.marginTop='22px';
  body.append(runLbl);
  const runner=el('select','inp');runner.style.cssText='margin:0;max-width:340px';
  runner.innerHTML='<option value="pytest">pytest (Python) — comparison inside the test</option>'
    +'<option value="command">your own command — review of ready PNGs</option>';
  runner.value=p.runner||'pytest';
  body.append(runner);

  const argsLbl=el('div','field-lbl','');argsLbl.style.marginTop='18px';
  body.append(argsLbl);
  const args=el('textarea','inp');
  args.style.cssText='margin:0;width:100%;font-family:var(--mono);font-size:13px';
  body.append(args);
  const argsHint=el('div','muted','');
  argsHint.style.cssText='font-size:12.5px;margin-top:8px';
  body.append(argsHint);

  const paint=()=>{
    const cmd=runner.value==='command';
    argsLbl.textContent=cmd?'Run command':'pytest arguments';
    const list=cmd?(p.command||[]):(p.pytest_args||[]);
    args.rows=Math.max(4,list.length+1);
    args.value=list.join('\n');
    args.placeholder=cmd?'npx\nplaywright\ntest':'-m\nscreenshot';
    argsHint.innerHTML=cmd
      ? 'One argument per line: <code>npx</code>, <code>playwright</code>, <code>test</code> — three lines. Runs from the project root, inside the VisTest container, so the tool has to exist there. Interception is impossible outside a Python process, so such a project always works in review mode: VisTest picks up the «baseline / actual» PNG pairs left after the run.'
      : 'One argument per line, exactly as on the command line: <code>-m</code> and <code>screenshot</code> are two lines, not one.';
  };
  paint();
  runner.onchange=()=>{
    // Запомним, что человек уже напечатал, чтобы переключение туда-обратно
    // не стирало набранное.
    const list=args.value.split('\n').map(s=>s.trim()).filter(Boolean);
    if(runner.value==='command')p.pytest_args=list;else p.command=list;
    paint();
  };

  // --- сохранение ---
  const acts=el('div');acts.style.cssText='display:flex;gap:10px;margin-top:20px';
  const save=el('button','btn dark','Save');
  const cancel=el('button','btn','Cancel');cancel.onclick=closeModal;
  acts.append(save,cancel);body.append(acts);

  save.onclick=async()=>{
    const env={};
    let bad='';
    $$('.env-edit',rows).forEach(r=>{
      const n=$('[data-k=name]',r).value.trim();
      if(!n)return;
      if(!/^[A-Za-z_][A-Za-z0-9_]*$/.test(n))bad=n;
      env[n]=$('[data-k=value]',r).value;
    });
    if(bad)return toast('Invalid variable name: '+bad,'err');
    const list=args.value.split('\n').map(s=>s.trim()).filter(Boolean);
    const cmd=runner.value==='command';
    if(cmd&&!list.length)return toast('The run command is empty','err');
    const patch={env,runner:runner.value};
    patch[cmd?'command':'pytest_args']=list;
    save.disabled=true;const t=save.textContent;save.textContent='Saving…';
    try{
      await api(`/api/projects/${encodeURIComponent(key)}`,{method:'PATCH',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify(patch)});
      toast('Saved','ok');closeModal();SCREENS.projects();
    }catch(e){toast(String(e.message||e),'err');save.disabled=false;save.textContent=t;}
  };
}

