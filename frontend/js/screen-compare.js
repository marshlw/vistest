/* VisTest - self-hosted visual regression testing.
 * Copyright (C) 2026 Kirill Kulagin
 * SPDX-License-Identifier: AGPL-3.0-or-later
 *
 * This file is part of VisTest. See LICENSE for the full terms and NOTICE for
 * the trademark and commercial-licensing terms. Removing this header does not
 * remove those obligations.
 */

/* Экран разбора одного сравнения: картинки, регионы, решение.

   Самый большой из экранов, и это не случайность: здесь человек проводит больше
   всего времени и принимает единственное решение, ради которого всё остальное
   существует. */
let blinkTimer=null;
SCREENS.compare=async function(id){
  if(blinkTimer){clearInterval(blinkTimer);blinkTimer=null;}
  loadingScreen();
  let cp;try{cp=await api('/api/comparisons/'+id);}catch(e){return errScreen(e);}
  state.comp=cp;
  state.baselineState=await api(`/api/comparisons/${id}/baseline-state`).catch(()=>null);
  if(!state.thresholds)state.thresholds=await api('/api/settings/thresholds').catch(()=>({}));
  if(!state.run||state.run.id!==cp.run_id){try{state.run=await api('/api/runs/'+cp.run_id);}catch{}}
  await takeClaim(id);
  renderCompare();
};

/* Кто ещё в очереди. Триаж — это разбор ПОДРЯД: человек отвечает и сразу
   видит следующий снимок, а не возвращается в список после каждого ответа.
   Очередь кладётся в `state.triage` экраном решений; если сюда пришли по
   прямой ссылке, очередью становятся падения этого прогона. */
function queueIds(){
  if(state.triage&&(state.triage.ids||[]).length)return state.triage.ids;
  return (state.run&&state.run.comparisons||[])
    .filter(c=>c.verdict==='fail'&&!c.review).map(c=>c.id);
}
function failedList(){return (state.run&&state.run.comparisons||[]).filter(c=>c.verdict==='fail'||c.review);}
/* Причина, к которой относится этот снимок, — если мы пришли из очереди.
   Без неё правая колонка не может честно предложить «ответить за всю группу»:
   она не знает, что за группа и сколько в ней снимков. */
function currentCause(){
  const t=state.triage;const cp=state.comp;
  if(!t||!cp)return null;
  return (t.causes||[]).find(c=>(c.comparisons||[]).includes(cp.id))||null;
}
function queueStep(delta){
  const ids=queueIds();const cp=state.comp;
  const i=ids.indexOf(cp&&cp.id);
  if(i<0)return null;
  const n=i+delta;
  return (n>=0&&n<ids.length)?ids[n]:null;
}
function goQueue(delta){
  const id=queueStep(delta);
  if(id)location.hash='#/compare/'+id;
  else toast(delta>0?'This is the last one in the queue':'This is the first one');
}

function renderCompare(){
  /* Мигание живёт в buildStage, а сбрасывалось только при входе на экран.
     Переключение режимов идёт через renderCompare(), поэтому цепочка
     blink → boxes → blink оставляла два интервала, которые дальше дрались за
     один и тот же <img>. Гасим здесь — это единственная точка перерисовки. */
  if(blinkTimer){clearInterval(blinkTimer);blinkTimer=null;}
  const cp=state.comp,s=$('#screen');s.innerHTML='';

  const grid=el('div','triage');
  const left=el('div','left');const side=el('div','side');
  grid.append(left,side);s.append(grid);

  left.append(buildQueueBar(cp));

  const pad=el('div');pad.style.padding='18px';left.append(pad);

  const cause=currentCause();
  const head=el('div','head');
  const ttl=el('div','grow');
  const eyebrow=cause
    ? `CAUSE ${cause.position||1} OF ${(state.triage.causes||[]).length} · ${esc(cause.tag||'')} · ${cause.count} ${plural(cause.count,'SNAPSHOT','SNAPSHOTS','SNAPSHOTS')}`
    : `${esc(String(cp.platform||''))} · ${esc(String(cp.browser||''))}`;
  const run=state.run||{};
  ttl.innerHTML=`<div class="eyebrow">${eyebrow}</div>
    <h1 class="h1" style="font-size:20px;margin-top:7px">${esc(cp.snapshot_name||'')}</h1>
    <div class="mono" style="font-size:11.5px;color:var(--muted);margin-top:5px">
      ${esc(run.run_key||('#'+(cp.run_id||'')))} · ${esc(run.branch||'—')} ·
      ${esc((run.git_sha||'—').slice(0,7))} · ${esc(cp.platform||'')}</div>`;
  head.append(ttl);
  const cbHolder=el('div');cbHolder.id='claimBanner';cbHolder.append(buildClaimBanner());
  head.append(cbHolder);
  pad.append(head);

  /* Пять чисел вердикта в один ряд. Раньше они лежали строкой мелким текстом
     под заголовком и читались как подпись к картинке; это те самые величины,
     по которым человек и решает, поэтому им место в приборной полосе. */
  const strip=el('div','strip sm');
  strip.style.gridTemplateColumns='repeat(5,1fr)';
  strip.style.marginTop='16px';
  /* Порог рядом с severity: число «82.0» само по себе не отвечает на вопрос
     «это много», а «82 при пороге 50» отвечает сразу. */
  const th=(state.thresholds||{}).fail_severity;
  strip.innerHTML=`
    <div class="cell"><div class="k">SEVERITY</div>
      <div class="v fail">${fmt(cp.max_severity,1)}</div>
      <div class="d">${th!=null?'threshold '+th:'of 100'}</div></div>
    <div class="cell"><div class="k">AREA</div>
      <div class="v">${pct(cp.changed_area_pct,3)}</div><div class="d">of the page</div></div>
    <div class="cell"><div class="k">ΔE00 P95</div>
      <div class="v">${fmt(cp.de_p95)}</div><div class="d">colour distance</div></div>
    <div class="cell"><div class="k">SSIM</div>
      <div class="v">${fmt(cp.ssim,4)}</div><div class="d">structure</div></div>
    <div class="cell"><div class="k">SIZE</div>
      <div class="v ${cp.size_changed?'warn':'pass'}">${cp.size_changed?'changed':'same'}</div>
      <div class="d">page height</div></div>`;
  pad.append(strip);

  const modes=[['slide','Slider','1'],['blink','Blink','2'],['onion','Overlay','3'],
               ['boxes','Boxes','4'],['heat','Heatmap','5'],['side','Side by side','6']];
  const mbar=el('div','bar');mbar.style.marginTop='16px';
  modes.forEach(([k,l,key])=>{
    const b=el('button',state.mode===k?'on':'');
    b.innerHTML=`${esc(l)}<span class="n">${key}</span>`;
    b.onclick=()=>{state.mode=k;renderCompare();};
    mbar.append(b);
  });
  mbar.append(el('div','fill',stageHint(state.mode)));
  pad.append(mbar);

  const stage=buildStage(cp);
  stage.classList.add('canvas');
  stage.style.borderTop='0';
  stage.style.border='1px solid var(--line)';
  stage.style.borderTop='0';
  pad.append(stage);

  pad.append(buildRegions(cp));

  side.append(buildDecision(cp));
  const group=buildGroupPanel(cause);
  if(group)side.append(group);
  side.append(buildWhere(cp));
  side.append(buildHistory(cp));
}
function stageHint(m){return {slide:'drag the handle',blink:'frames alternate',
  onion:'adjust the opacity',boxes:'rectangles are the differences',
  heat:'brighter is a stronger ΔE00',side:'baseline left · current right'}[m]||'';}

/* Полоса очереди: сколько уже разобрано и сколько осталось.

   Точки, а не «4 из 23»: число говорит, где ты, а точки — сколько ещё, и это
   разные вопросы. Человек, который видит, что осталось три, дорабатывает до
   конца; человек, который видит «4 / 23», уходит. */
function buildQueueBar(cp){
  const bar=el('div','qbar');
  const back=el('a','back','‹ QUEUE');back.href='#/decisions';
  bar.append(back);

  const ids=queueIds();
  const at=ids.indexOf(cp.id);
  if(ids.length){
    const dots=el('div','dots');
    ids.slice(0,60).forEach((id,i)=>{
      const d=el('i');
      if(i<at)d.className='done';
      else if(i===at)d.className='now';
      d.title=i===at?'this one':(i<at?'answered':'waiting');
      dots.append(d);
    });
    bar.append(dots);
    const pos=el('div','mono',`${at>=0?at+1:'?'} / ${ids.length}`);
    pos.style.cssText='font-size:11.5px;color:var(--muted)';
    bar.append(pos);
  }

  const nav=el('div');nav.style.cssText='margin-left:auto;display:flex;gap:6px';
  const up=el('button','btn sm');up.innerHTML='K <span class="kbd">↑</span>';
  up.title='Previous in the queue';up.onclick=()=>goQueue(-1);
  const down=el('button','btn sm');down.innerHTML='J <span class="kbd">↓</span>';
  down.title='Next in the queue';down.onclick=()=>goQueue(1);
  nav.append(up,down);bar.append(nav);
  return bar;
}

function buildStage(cp){
  const a=cp.artifacts||{};const stage=el('div','stage');const m=state.mode;
  const has=k=>a[k];
  if(m==='heat'&&has('heatmap')){stage.append(fallbackImg(a.heatmap));return stage;}
  if(m==='boxes'){stage.append(fallbackImg(a.boxes||a.actual));return stage;}
  if(m==='side'&&has('side_by_side')){stage.append(fallbackImg(a.side_by_side));return stage;}
  if(m==='side'){const w=el('div');w.style.cssText='display:grid;grid-template-columns:1fr 1fr;gap:2px';w.append(fallbackImg(a.expected),fallbackImg(a.actual));stage.append(w);return stage;}
  const expected=a.expected,actual=a.actual;
  if(!expected&&!actual){stage.append(missingArtifact());return stage;}
  if(m==='blink'){
    const img=fallbackImg(actual||expected);stage.append(img);
    const im=$('img',stage);let on=true;
    if(im){blinkTimer=setInterval(()=>{on=!on;im.src=on?(actual||expected):(expected||actual);},650);}
    stage.append(el('div','badge-abs','blink'));$('.badge-abs',stage).style.left='12px';
    return stage;
  }
  if(m==='onion'){
    const base=fallbackImg(expected||actual);base.style.width='100%';
    const layer=el('div','layer');const top=el('img');top.src=actual||expected;top.style.cssText='width:100%;height:auto;opacity:.5';layer.append(top);
    stage.append(base,layer);
    const sl=el('input');sl.type='range';sl.min=0;sl.max=100;sl.value=50;sl.style.cssText='position:absolute;left:12px;right:12px;bottom:12px;z-index:6;width:calc(100% - 24px)';
    sl.oninput=()=>{top.style.opacity=sl.value/100;};stage.append(sl);
    return stage;
  }
  // slide — base image fills width and sets stage height; expected clipped on top
  const bimg=el('img');bimg.src=actual||expected;bimg.style.cssText='width:100%;display:block';
  bimg.onerror=()=>{stage.append(missingArtifact(actual||expected));};
  const top=el('div','layer');top.style.width='50%';
  const timg=el('img');timg.src=expected||actual;top.append(timg);
  const handle=el('div','handle');handle.style.left='50%';
  const bl=el('div','badge-abs','BASELINE');bl.style.left='12px';
  const br=el('div','badge-abs','CURRENT');br.style.right='12px';
  stage.append(bimg,top,handle,bl,br);
  const setPct=x=>{const rc=stage.getBoundingClientRect();let p=Math.max(0,Math.min(100,(x-rc.left)/rc.width*100));top.style.width=p+'%';handle.style.left=p+'%';};

  /* Pointer events вместо mouse: тот же код работает пальцем на планшете, а
     мышью — как раньше. И, главное, захват указателя вешается на сам handle,
     а не на window: прежние `window.addEventListener('mousemove')` добавлялись
     при каждом рендере и не снимались никогда, так что после десяти
     переключений режима на каждое движение мыши срабатывало десять
     обработчиков. */
  handle.style.touchAction='none';
  handle.addEventListener('pointerdown',e=>{
    e.preventDefault();
    handle.setPointerCapture(e.pointerId);
    const move=ev=>setPct(ev.clientX);
    const up=ev=>{
      handle.releasePointerCapture(e.pointerId);
      handle.removeEventListener('pointermove',move);
      handle.removeEventListener('pointerup',up);
      handle.removeEventListener('pointercancel',up);
    };
    handle.addEventListener('pointermove',move);
    handle.addEventListener('pointerup',up);
    handle.addEventListener('pointercancel',up);
  });
  stage.onclick=e=>{if(e.target===handle)return;setPct(e.clientX);};
  return stage;
}
function fallbackImg(url){if(!url)return missingArtifact();const w=el('div');const i=el('img');i.src=url;i.style.cssText='width:100%;display:block';i.onerror=()=>w.replaceWith(missingArtifact(url));i.onclick=()=>lightbox(url);i.style.cursor='zoom-in';w.append(i);return w;}

function buildDecision(cp){
  const card=el('div','rail');
  card.append(el('div','k','DECISION'));
  const already=cp.reviewed_by
    ? `<div class="exp">Already answered: <b>${esc(cp.review||'')}</b> — ${esc(cp.reviewed_by)}</div>`
    : '';

  /* Роль спрашивается вместе с ПРОЕКТОМ сравнения: она может быть выдана на
     один набор, и тогда «Принять» здесь работает, а на соседнем проекте — нет.
     Показать кнопки всем и ответить 403 по нажатию — это не строгий бэкенд,
     это неправда на экране: человек не понимает, что произошло. */
  if(!can('reviewer',cp.project)){
    card.append(el('div','exp','Deciding on this snapshot needs the reviewer '
      +'role'+(cp.project?' in project «'+esc(cp.project)+'»':'')
      +'. An administrator grants it on the «Team» tab.'));
    if(already)card.insertAdjacentHTML('beforeend',already);
    return card;
  }

  const ok=el('button','btn dark split wide');
  ok.style.marginTop='10px';
  ok.innerHTML='<span>Accept as baseline</span><span class="kbd">A</span>';
  ok.onclick=()=>decide('approve');
  card.append(ok);
  card.append(el('div','exp','Current view becomes the new normal. Counts towards '
    +'the false-failure metric.'));

  const bug=el('button','btn danger split wide');
  bug.style.marginTop='10px';
  bug.innerHTML='<span>Confirm as a bug</span><span class="kbd">B</span>';
  bug.onclick=()=>decide('reject');
  card.append(bug);
  card.append(el('div','exp','Baseline stays. The snapshot goes into the ticket report.'));

  const two=el('div','two');
  const defer=el('button','btn sm');
  defer.innerHTML='Defer <span class="kbd">D</span>';
  defer.title='Leave it in the queue and move on. Nothing is written.';
  defer.onclick=()=>goQueue(1);
  const mask=el('button','btn sm');
  mask.innerHTML='Mask <span class="kbd">M</span>';
  mask.title='Draw a zone that every future comparison ignores — on the snapshot page';
  mask.onclick=()=>ignoreRegion(cp);
  two.append(defer,mask);
  card.append(two);
  if(already)card.insertAdjacentHTML('beforeend',already);
  return card;
}

/* «Ответить за всю причину» — та самая кнопка, ради которой очередь и
   собиралась по причинам. Она стоит НИЖЕ одиночного решения намеренно: сначала
   человек смотрит на конкретный снимок, и только потом ему предлагают
   распространить ответ. Обратный порядок — это приглашение принять всё, не
   посмотрев ни одного. */
function buildGroupPanel(cause){
  if(!cause||cause.count<2)return null;
  const cp=state.comp;
  if(!can('reviewer',cp&&cp.project))return null;

  const card=el('div','rail group');
  card.append(el('div','k','APPLY TO THE WHOLE CAUSE'));
  const lead=el('div','lead');
  lead.innerHTML=`${esc(cause.title||'The same change')} explains
    <b>${cause.count} ${plural(cause.count,'snapshot','snapshots','snapshots')}</b>.
    One answer closes all of them.`;
  card.append(lead);

  const all=el('button','btn accent wide');
  all.style.marginTop='10px';
  all.textContent=`Accept all ${cause.count}`;
  all.onclick=()=>answerCause(cause,'approve',all);
  card.append(all);

  const keep=el('label','check');
  keep.innerHTML='<input type="checkbox" checked> keep asking me per snapshot';
  keep.title='Unchecked, an answer to one snapshot is applied to the whole cause '
    +'right away. Checked, the group is only ever answered by the button above.';
  card.append(keep);
  return card;
}

/* Куда именно уедет решение. «Принять как эталон» без указания, КАКОЙ эталон,
   — обещание, которое нельзя проверить. Особенно когда у подключённого проекта
   их три: собственный набор сервиса, PNG в их репозитории и комплект VisTest. */
function buildWhere(cp){
  const card=el('div','rail');
  card.append(el('div','k','WHERE IT LANDS'));
  const bs=state.baselineState||{};
  const where={global:'own set',
               project:'project repository',
               vistest:'VisTest set of this project'}[bs.scope]||bs.scope||'—';
  const kv=el('div','kv');
  kv.innerHTML=`set · <b>${esc(where)}</b><br>`
    +(bs.branch?`branch · <b>${esc(bs.branch)}</b><br>`:'')
    +`baseline · <b>${bs.version?('v'+bs.version):'none yet'}`
    +`${bs.approved_by?', accepted '+esc(bs.approved_by):''}</b>`;
  if(bs.directory)kv.title=bs.directory;
  card.append(kv);

  if(bs.scope==='project'){
    card.append(el('div','exp','This run compared against PNGs committed in the '
      +'project repository — accepting shows up in their git diff.'));
  }
  /* Ветка — вторая половина того же обещания. Сказать «собственный набор» и
     записать в наложение ветки значит снова пообещать непроверяемое. */
  if(bs.branch){
    card.append(el('div','exp',bs.inherited
      ? `This snapshot is still inherited from the base branch. Accepting creates a `
        +`copy on ${bs.branch} and leaves the base one untouched.`
      : 'The base branch keeps its own baseline.'));
  }

  const hist=el('button','btn sm wide');
  hist.style.marginTop='10px';
  hist.textContent='History & rollback';
  hist.onclick=()=>baselineVersionsModal(cp);
  card.append(hist);

  /* Отсюда — на страницу снимка: спека, зоны, история по всем прогонам.
     Выйти из разбора падения к самому снимку раньше было нельзя вообще, и это
     ровно тот переход, который делают после «а что это за снимок». */
  const link=snapshotLinkFor(cp);
  if(link){
    const go=el('button','btn sm wide','Snapshot page');
    go.style.marginTop='8px';
    go.title='Spec, ignore zones, full history of this snapshot';
    go.onclick=()=>{location.hash=link;};
    card.append(go);
  }
  return card;
}

/* История версий эталона и откат. Предыдущие версии складывались на диск с
   самого начала (`history/v3.png`), и достать их было нечем: апрув оставался
   необратимым буквально. */
async function baselineVersionsModal(cp){
  const wrap=el('div');
  wrap.innerHTML=`<div class="mhead"><div><h3>Baseline history · ${esc(cp.snapshot_name||'')}</h3>
    <div class="msub">Rolling back writes the old picture as a NEW version — the history is not rewritten.</div></div>
    <button class="mclose" aria-label="Close">×</button></div>`;
  const body=el('div','pf-body');
  body.innerHTML='<div class="jobbar"><span class="spin"></span> loading…</div>';
  wrap.append(body);openModal(wrap,{wide:true});$('.mclose',wrap).onclick=closeModal;

  let data;
  try{data=await api('/api/comparisons/'+cp.id+'/baseline-versions');}
  catch(e){body.innerHTML='';const er=el('div','pf-status bad');er.textContent=String(e.message||e);body.append(er);return;}

  body.innerHTML='';
  const head=el('div','pj-rows');
  head.innerHTML=`<div class="k">Store</div><div class="v">${esc(data.scope||'')}</div>
    <div class="k">Directory</div><div class="v mono" style="word-break:break-all">${esc(data.directory||'')}</div>`;
  body.append(head);

  const versions=data.versions||[];
  if(!versions.length){body.append(el('div','muted','No versions recorded yet'));return;}

  versions.forEach(v=>{
    const row=el('div','hrow');
    row.style.cssText='display:flex;align-items:center;gap:12px;padding:10px 0;border-bottom:1px solid var(--line)';
    const q=`platform=${encodeURIComponent(data.platform||'')}&name=${encodeURIComponent(data.name||'')}&version=${v.version}`
      +(data.scope==='vistest'&&data.project_key?`&scope=project:${encodeURIComponent(data.project_key)}`:'');
    const thumb=el('img');
    thumb.src=API+'/api/baselines/version-image?'+q+'&w=120';
    thumb.style.cssText='width:120px;border:1px solid var(--line);border-radius:4px;cursor:zoom-in';
    thumb.onclick=()=>lightbox(API+'/api/baselines/version-image?'+q);
    thumb.onerror=()=>{thumb.style.display='none';};
    const info=el('div');info.style.flex='1';
    info.innerHTML=`<div><b>v${v.version}</b>${v.current?' <span class="tag green">current</span>':''}
      ${v.restored_from?` <span class="tag purple">rolled back to v${v.restored_from}</span>`:''}</div>
      <div class="muted" style="font-size:12px">${esc(v.approved_by||'—')} · ${esc((v.approved_at||v.updated_at||'').replace('T',' ').slice(0,16))}
      ${v.git_sha?' · '+esc(String(v.git_sha).slice(0,8)):''}</div>`;
    row.append(thumb,info);
    if(v.note){const n=el('div','muted');n.textContent=v.note;n.style.fontSize='12px';info.append(n);}
    if(!v.current&&!v.vcs){
      const back=el('button','btn sm','Roll back to this');
      back.onclick=async()=>{
        if(!confirm(`Roll the baseline back to v${v.version}?\n\nIt becomes the new current version; v${versions[0].version} stays in history.`))return;
        back.disabled=true;
        try{
          await api('/api/baselines/restore',{method:'POST',headers:{'Content-Type':'application/json'},
            body:JSON.stringify({platform:data.platform,name:data.name,version:v.version,
              scope:data.scope==='vistest'&&data.project_key?('project:'+data.project_key):undefined})});
          toast('Baseline rolled back','ok');closeModal();
          state.baselineState=await api(`/api/comparisons/${cp.id}/baseline-state`).catch(()=>null);
          renderCompare();
        }catch(e){toast(String(e.message||e),'err');back.disabled=false;}
      };
      row.append(back);
    }else if(v.vcs){
      row.append(el('span','muted','in git'));
    }
    body.append(row);
  });
}
async function decide(action){
  const cp=state.comp;
  try{
    const body=action==='approve'?{expected_version:state.baselineState?state.baselineState.version:undefined}:{};
    const r=await api(`/api/comparisons/${cp.id}/${action}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    toast(action==='approve'?'Accepted as baseline'+(r.by?' · '+r.by:''):'Marked as a bug','ok');
    refreshCounts();
    releaseClaim(cp.id);state.claimComp=null;if(claimTimer){clearInterval(claimTimer);claimTimer=null;}
    /* Дальше по очереди, а не «обратно в список»: возвращать человека в
       список после каждого ответа значит заставлять его каждый раз заново
       искать, где он остановился. */
    const nextId=queueStep(1);
    location.hash=nextId?('#/compare/'+nextId):'#/decisions';
  }catch(e){toast(String(e.message||e),'err');}
}
/* Куда ведёт снимок этого сравнения. Страница снимка живёт поверх
   `FileBaselineStore`, а PNG в репозитории проекта лежат в чужом плоском
   каталоге, которым распоряжаемся не мы, — для них страницы нет, и врать про
   это не нужно. */
function snapshotLinkFor(cp){
  const bs=state.baselineState||{};
  const scope=bs.scope==='vistest'&&bs.project_key?('project:'+bs.project_key)
    :bs.scope==='global'?'global':null;
  if(!scope||!cp.platform||!cp.snapshot_name)return null;
  return snapshotHash(scope,cp.platform,cp.snapshot_name);
}

/* Кнопка называлась «Ignore zone», а брала `regions[0]` — то есть «игнорируй
   то, что движок нашёл первым». Человек в этот момент хочет выделить область,
   а не согласиться с чужим выбором. Выделение живёт на странице снимка: зона
   принадлежит снимку, а не одному сравнению. */
function ignoreRegion(cp){
  const link=snapshotLinkFor(cp);
  if(link){location.hash=link;return;}
  toast('This run compared against PNGs in the project repository — VisTest '
    +'does not own their spec, so there is no snapshot page for them.','err');
}
function changeSev(v){return v>=55?'red':v>=32?'amber':'ink';}

/* Регионы — карточками в основной колонке, а не строчками в правой панели.

   Разница не косметическая. Регион это место на странице, и рядом с ним должен
   стоять его крупный план: эталон, текущее состояние и карта ΔE00. В узкой
   колонке для трёх картинок места нет, поэтому раньше их там и не было — а
   человек, чтобы увидеть подробность, шёл в «Close-up» ниже и сам сопоставлял
   подписи со строками. */
function buildRegions(cp){
  const regs=cp.regions||[];
  const box=el('div');
  if(!regs.length)return box;

  const sect=el('div','sect');
  sect.innerHTML='<h2 class="h2">Regions</h2><span class="note">baseline · current · ΔE00</span>';
  box.append(sect);

  /* Крупный план берётся по НОМЕРУ из имени артефакта, а не по позиции.
     Регионы приходят из БД отсортированными по severity, а артефакты движок
     нумеровал в своём порядке, — поэтому картинка под строкой регулярно
     относилась к другому месту на странице. */
  const arts=cp.artifacts||{};

  regs.forEach(r=>{
    const card=el('div','panel');card.style.marginBottom='10px';

    const hd=el('div');
    hd.style.cssText='display:flex;align-items:center;gap:10px;padding:9px 13px;'
      +'border-bottom:1px solid var(--line2)';
    hd.innerHTML=`<span class="tag ${changeSev(r.severity||0)}">${esc(String(r.kind||'change').toUpperCase())}</span>
      <span class="mono" style="font-size:12px;font-weight:600">sev ${Math.round(r.severity||0)}</span>
      <span class="mono" style="font-size:11.5px;color:var(--accent);flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(r.selector||'')}</span>
      <span class="mono" style="font-size:11px;color:var(--faint)">${r.w}×${r.h} @ ${r.x},${r.y}</span>`;

    /* Быстрый путь для случая, когда движок нашёл именно то: заглушить ЭТУ
       область, названную явно. Раньше такой кнопки не было вовсе, а общая
       «Ignore zone» молча брала первый регион из списка. */
    if(can('reviewer',cp.project)){
      const ig=el('button','btn sm','Ignore area');
      ig.title=`${r.w}×${r.h} @ ${r.x},${r.y}`;
      ig.onclick=async()=>{
        ig.disabled=true;
        try{
          await api(`/api/comparisons/${cp.id}/ignore-region`,{method:'POST',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify({x:r.x,y:r.y,w:r.w,h:r.h,
              reason:r.selector||r.kind||'via review'})});
          toast('Ignore zone added · applies from the next run','ok');
        }catch(e){toast(String(e.message||e),'err');ig.disabled=false;}
      };
      hd.append(ig);
    }
    card.append(hd);

    const n=r.region_index;
    const art=(n===null||n===undefined)?null:arts['region_'+Number(n)];
    if(art){
      const fig=el('div','canvas');
      const img=el('img');img.src=art;img.style.cssText='width:100%;display:block;cursor:zoom-in';
      img.onclick=()=>lightbox(art);
      img.onerror=()=>fig.replaceWith(missingArtifact(art));
      fig.append(img);card.append(fig);
    }
    if(r.element_text||r.caption){
      const txt=el('div',null,esc(r.element_text||r.caption));
      txt.style.cssText='padding:9px 13px;color:var(--ink2);font-size:12.5px';
      card.append(txt);
    }
    box.append(card);
  });

  /* Крупные планы, которым не нашлось региона (старые сравнения без
     region_index), — отдельным блоком и без выдуманных подписей. */
  const orphan=Object.keys(arts).filter(k=>k.startsWith('region_'))
    .filter(k=>!regs.some(r=>r.region_index!=null&&('region_'+Number(r.region_index))===k));
  if(orphan.length){
    box.append(sectionHead('Close-up','fragments this comparison saved without a region number'));
    const grid=el('div','grid');
    grid.style.gridTemplateColumns='repeat(auto-fill,minmax(280px,1fr))';
    orphan.sort((x,y)=>Number(x.slice(7))-Number(y.slice(7))).forEach(k=>{
      const fig=el('div');fig.style.padding='0';
      const img=el('img');img.src=arts[k];img.style.cssText='width:100%;display:block;cursor:zoom-in';
      img.onclick=()=>lightbox(arts[k]);
      img.onerror=()=>fig.replaceWith(missingArtifact(arts[k]));
      fig.append(img);grid.append(fig);
    });
    box.append(grid);
  }
  return box;
}

function buildHistory(cp){
  const h=cp.history||[];
  const card=el('div','rail');
  card.append(el('div','k','THIS SNAPSHOT OVER TIME'));
  if(!h.length){card.append(el('div','exp','No history yet.'));return card;}
  h.forEach(x=>{
    const col=x.verdict==='fail'?'var(--fail)':x.verdict==='new_baseline'?'var(--purple)'
      :x.review==='approved'?'var(--accent)':'var(--pass)';
    const row=el('div','hist');
    row.innerHTML=`<i style="background:${col}"></i>
      <span style="color:var(--ink2)">${esc((x.created_at||'').replace('T',' ').slice(5,16))} ${esc(x.branch||'')}</span>
      <span style="color:${col};font-weight:600">${fmt(x.max_severity,1)}</span>`;
    card.append(row);
  });
  return card;
}

/* Горячие клавиши разбора. Названы прямо на кнопках: клавиша, о которой знает
   только автор, не существует. */
document.addEventListener('keydown',e=>{
  if(state.view!=='compare'||!state.comp)return;
  if(/^(INPUT|TEXTAREA|SELECT)$/.test((e.target||{}).tagName||''))return;
  /* Модалка, палитра, лайтбокс — «A» поверх открытой истории эталона
     принимала эталон. Решение необратимо ровно настолько, насколько человек
     его не собирался принимать. */
  if($('#modalBg')||$('#palBg')||$('.lightbox')||$('#blockNotice'))return;
  const k=e.key.toLowerCase();
  if(k==='a'||k==='ф'){decide('approve');}
  else if(k==='b'||k==='и'){decide('reject');}
  else if(k==='d'||k==='в'){goQueue(1);}
  else if(k==='m'||k==='ь'){ignoreRegion(state.comp);}
  else if(k==='j'||k==='о'){goQueue(1);}
  else if(k==='k'||k==='л'){goQueue(-1);}
  else if(k>='1'&&k<='6'){
    const modes=['slide','blink','onion','boxes','heat','side'];
    state.mode=modes[Number(k)-1];renderCompare();
  }else return;
  e.preventDefault();
});
