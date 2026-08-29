/* VisTest - self-hosted visual regression testing.
 * Copyright (C) 2026 Kirill Kulagin
 * SPDX-License-Identifier: AGPL-3.0-or-later
 *
 * This file is part of VisTest. See LICENSE for the full terms and NOTICE for
 * the trademark and commercial-licensing terms. Removing this header does not
 * remove those obligations.
 */

/* Экран «Runs»: история. Список — таблица, прогон — страница.

   Раньше это была раскладка «список слева, карточка справа»: на карточку
   оставалась половина ширины, а список висел рядом, когда в нём уже не было
   нужды. Теперь список отвечает на «когда и что», а прогон открывается целой
   страницей и отвечает на «что именно» — с причинами и снимками во всю ширину.

   Разбор здесь не начинается: он живёт в очереди решений, где собран по всему
   проекту, а не по одному прогону. Отсюда на него есть ссылка. */

/* Ярлык источника эталонов прогона. Без него, глядя на прогон, нельзя понять,
   с чем он сравнивался, — а от этого зависит, куда уйдёт «принять как эталон». */
const SCOPE_LABEL={global:'own set',project:'project PNGs',vistest:'VisTest set'};
function scopeTag(r){
  const s=r.baseline_scope||'global';
  if(s==='global')return '';
  return `<span class="tag ${s==='vistest'?'purple':'amber'}" title="This run compared against ${esc(SCOPE_LABEL[s]||s)}">${esc(SCOPE_LABEL[s]||s)}</span>`;
}

const RUN_FILTERS=[['all','All'],['fail','Needs decisions'],['error','With errors'],
                   ['new','New baselines'],['clean','Clean']];
const FILTER_TONE={all:'',fail:'var(--accent)',error:'var(--warn)',
                   new:'var(--purple)',clean:'var(--pass)'};

SCREENS.runs=async function(arg){
  /* Аргумент — id прогона: `#/runs/42`. Появился ради комментария в PR/MR:
     ссылка оттуда вела на список, а список показывает последние шестьдесят,
     так что назавтра комментарий к позавчерашнему MR открывал чужой прогон,
     выглядящий как свой. */
  if(arg&&/^\d+$/.test(arg))return renderRunPage(Number(arg));

  loadingScreen();
  state.runFilter=state.runFilter||'all';
  let runs;
  try{runs=await api('/api/runs?limit='+(state.runLimit||60)+'&project='+encodeURIComponent(state.project));}
  catch(e){return errScreen(e);}
  state.runs=runs;

  const s=$('#screen');s.innerHTML='';
  const page=el('div','page');s.append(page);

  const total=state.runCounts||null;
  const head=el('div','head');
  const left=el('div','grow');
  left.innerHTML=`<div class="eyebrow">HISTORY</div>
    <h1 class="h1">Runs</h1>
    <div class="lede">${total?fmtInt(total.all)+' runs in the history. ':''}Open one to see its
      causes, or go to <a href="#/decisions">Decisions</a> for what still needs an answer.</div>`;
  head.append(left);

  const acts=el('div','acts');
  /* Массовая чистка требует роли admin, а кнопка показывалась всем: reviewer
     нажимал и получал голый 403 без объяснения. Кнопка, которая не может
     сработать, — это не «строгий бэкенд», это неправда на экране. */
  const clean=roleButton('admin','btn','Clear old ones',cleanupRuns,
                         'Bulk cleanup of history is an administrator action');
  if(clean)acts.append(clean);
  head.append(acts);
  page.append(head);

  if(!runs.length){
    page.append(el('div','panel',
      '<div class="empty"><b>No runs yet</b>'
      +'<div class="faint" style="margin-top:10px;font-size:12.5px;line-height:1.9">'
      +'Everything the engine checked lands here — with images, regions and history.<br>'
      +'Record baselines with the mouse on the «Baselines» tab, run <code>python run.py test</code>, '
      +'or connect a suite on the «Projects» tab.</div></div>'));
    return;
  }

  /* Счётчики берутся с бэкенда и считают ВСЮ историю. Раньше они считались
     здесь по массиву `runs` — то есть по одной странице в шестьдесят записей.
     «С падениями 3» при трёхстах в истории читается как факт, и это тот
     случай, когда неверное число хуже отсутствующего. */
  const bar=el('div','bar');
  RUN_FILTERS.forEach(([k,label])=>{
    const n=total?(total[k]||0):runs.filter(r=>matchRunFilter(r,k)).length;
    const b=el('button',state.runFilter===k?'on':'');
    b.innerHTML=`${esc(label)}<span class="n"${FILTER_TONE[k]?` style="color:${FILTER_TONE[k]}"`:''}>${fmtInt(n)}</span>`;
    b.onclick=()=>{state.runFilter=k;SCREENS.runs();};
    bar.append(b);
  });
  bar.append(el('div','fill','sorted newest first'));
  page.append(bar);

  const shown=runs.filter(r=>matchRunFilter(r,state.runFilter));
  const cols='170px 116px 1fr 110px 90px 80px 34px';
  const rows=el('div','rows flush');
  const th=el('div','th');th.style.display='grid';
  th.style.gridTemplateColumns=cols;th.style.gap='12px';
  th.innerHTML='<div>RUN</div><div>VERDICT</div><div>BRANCH · COMMIT</div>'
    +'<div class="r">FAIL / TOTAL</div><div class="r">DURATION</div>'
    +'<div class="r">AGE</div><div></div>';
  rows.append(th);

  if(!shown.length)rows.append(el('div','empty','Nothing matches this filter'));

  shown.forEach(r=>{
    const tr=el('div','tr click run-item');tr.dataset.id=r.id;
    tr.style.display='grid';tr.style.gridTemplateColumns=cols;tr.style.gap='12px';
    const nm=(r.run_key||r.project||('#'+r.id));
    tr.innerHTML=`<div class="mono" style="font-size:12.5px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(nm)}</div>
      <div>${runStatus(r).html}</div>
      <div class="mono" style="font-size:11.5px;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(r.branch||'—')} · ${esc((r.git_sha||'—').slice(0,7))}</div>
      <div class="m r"${(r.failed||0)?' style="color:var(--fail)"':''}>${fmtInt(r.failed||0)} / ${fmtInt(r.total||0)}</div>
      <div class="m r">${esc(fmtDur(r.started_at,r.finished_at))}</div>
      <div class="mono r" style="font-size:11.5px;color:var(--faint)">${esc(timeAgo(r.started_at))}</div>`;
    hit(tr,()=>{location.hash='#/runs/'+r.id;},`Run ${nm}, open it`);

    /* Удаление одного прогона. Роут `DELETE /api/runs/{id}` существовал с
       самого начала и не был подключён ни к одной кнопке: почистить историю
       можно было только массово, «всё старше N дней». */
    const del=el('button','btn sm','×');
    del.title='Delete this run';
    del.setAttribute('aria-label',`Delete run ${nm}`);
    del.style.cssText='padding:2px 7px;opacity:.4';
    del.onmouseenter=()=>del.style.opacity='1';
    del.onmouseleave=()=>del.style.opacity='.4';
    del.onclick=e=>{e.stopPropagation();deleteRun(r);};
    tr.append(del);
    rows.append(tr);
  });
  page.append(rows);

  if(runs.length>=(state.runLimit||60)){
    const more=el('button','btn','Load 30 more');
    more.style.marginTop='12px';
    more.onclick=()=>{state.runLimit=(state.runLimit||60)+30;SCREENS.runs();};
    page.append(more);
  }
};

/* Статус прогона — ОДНА функция на два места.
   Живое обновление держало собственную копию этой логики (см. `liveTick`), и
   копия отстала: она не знала про `errored`, поэтому через двенадцать секунд
   после отрисовки прогон с ошибками сам собой становился «clean». Второго
   места, где это можно рассинхронизировать, больше нет. */
function runStatus(r){
  let cls='tag green',text='CLEAN';
  if((r.failed||0)>0){cls='tag accent';text=`${r.failed} TO DECIDE`;}
  else if((r.errored||0)>0){cls='tag amber';
    text=`${r.errored} ${plural(r.errored,'ERROR','ERRORS','ERRORS')}`;}
  else if((r.new_baselines||0)>0){cls='tag purple';text=`${r.new_baselines} NEW`;}
  return {cls,text,html:`<span class="${cls}">${esc(text)}</span>`};
}

function matchRunFilter(r,kind){
  if(kind==='fail')return (r.failed||0)>0;
  if(kind==='error')return (r.errored||0)>0;
  if(kind==='new')return (r.new_baselines||0)>0;
  if(kind==='clean')return !(r.failed||0)&&!(r.errored||0);
  return true;
}

async function deleteRun(r){
  const nm=(r.run_key||('#'+r.id));
  if(!confirm(`Delete run ${nm}?\n\nIts comparisons and images are removed. Baselines are not touched — a run is a history of checks, not the source of truth.`))return;
  try{
    const res=await api('/api/runs/'+r.id,{method:'DELETE'});
    toast(`Run deleted · ${Math.round((res.freed_kb||0)/1024)} MB freed`,'ok');
    if(state.run&&state.run.id===r.id)state.run=null;
    SCREENS.runs();refreshCounts();
  }catch(e){toast(String(e.message||e),'err');}
}

/* --------------------------------------------------------------- прогон -- */
async function renderRunPage(id){
  loadingScreen();
  let r;
  try{r=await api('/api/runs/'+id);}
  catch(e){
    /* Пустая панель вместо прогона — единственное, что видел человек,
       пришедший по ссылке на удалённый прогон: ни ошибки, ни объяснения. */
    $('#screen').innerHTML=`<div class="page narrow"><div class="panel pad">
      <h3 class="h2">Run #${esc(String(id))} is not here</h3>
      <div class="lede">It was deleted, or the retention window has passed.</div>
      <a href="#/runs" class="btn" style="margin-top:14px;display:inline-flex">‹ All runs</a>
      </div></div>`;
    return;
  }
  state.run=r;

  const s=$('#screen');s.innerHTML='';
  const page=el('div','page');s.append(page);
  const back=el('a','back','‹ RUNS');back.href='#/runs';page.append(back);

  const nm=r.run_key||r.project||('#'+r.id);
  const head=el('div','head');head.style.marginTop='10px';
  const left=el('div','grow');
  left.innerHTML=`<h1 class="h1 mono">${esc(nm)}</h1>
    <div class="mono" style="font-size:11.5px;color:var(--muted);margin-top:6px">
      ${esc(r.branch||'—')} · ${esc(r.git_sha||'—')} · ${esc(r.platform||'')} ${esc(r.browser||'')}
      · ${esc(timeAgo(r.started_at))} ${scopeTag(r)}</div>`;
  head.append(left);

  const acts=el('div','acts');
  const diff=el('button','btn','⇄ What changed');
  diff.title='Compare this run with another one: what went red here, what '
    +'stopped being checked, what was already red before.';
  diff.onclick=()=>{location.hash='#/diff/'+r.id;};
  const rep=el('button','btn','Report for a ticket');
  rep.title='One HTML with embedded images — opens without a network.';
  rep.onclick=()=>window.open(API+'/api/runs/'+r.id+'/report.html','_blank');
  const ju=el('button','btn','JUnit for CI');
  ju.title='One XML that GitHub, GitLab, Jenkins and TeamCity show as a test list. '
    +'Failures already accepted as normal are not counted as failures.';
  ju.onclick=()=>window.open(API+'/api/runs/'+r.id+'/junit.xml','_blank');
  acts.append(diff,rep,ju);
  head.append(acts);
  page.append(head);

  const errComps=(r.comparisons||[]).filter(c=>c.verdict==='error');
  const strip=el('div','strip');
  strip.style.gridTemplateColumns='repeat(5,1fr)';
  strip.style.marginTop='18px';
  strip.innerHTML=`
    <div class="cell"><div class="k">SNAPSHOTS</div><div class="v">${fmtInt(r.total)}</div><div class="d">checked</div></div>
    <div class="cell"><div class="k">FAILED</div><div class="v ${r.failed?'fail':''}">${fmtInt(r.failed)}</div><div class="d">against the baseline</div></div>
    <div class="cell"><div class="k">ERRORS</div><div class="v ${errComps.length?'warn':''}">${fmtInt(errComps.length)}</div><div class="d">never captured</div></div>
    <div class="cell"><div class="k">NEW</div><div class="v ${r.new_baselines?'purple':''}">${fmtInt(r.new_baselines)}</div><div class="d">baselines written</div></div>
    <div class="cell"><div class="k">DURATION</div><div class="v">${esc(fmtDur(r.started_at,r.finished_at))}</div><div class="d">${esc(timeAgo(r.started_at))}</div></div>`;
  page.append(strip);

  if(errComps.length){
    // Ошибка съёмки показывается первой и с причиной, а не прячется за
    // «чисто»: снимок не проверен, и это не хорошая новость.
    const eb=el('div','panel');eb.style.marginTop='18px';
    eb.append(el('div','th','⚠ '+errComps.length+' '+plural(errComps.length,'SNAPSHOT WAS','SNAPSHOTS WERE','SNAPSHOTS WERE')+' NOT CHECKED'));
    errComps.forEach(c=>{
      /* Класс `snap-row` тот же, что у обычных строк снимков: для человека и
         для теста это одна и та же вещь — строка про снимок, которую можно
         открыть. Ошибочная отличается тем, что смотреть в ней нечего. */
      const row=el('div','tr click snap-row is-err');
      row.style.cssText='display:grid;grid-template-columns:260px 1fr;gap:14px;padding:9px 15px;cursor:pointer';
      row.innerHTML=`<div class="mono" style="font-size:12px">${esc(c.snapshot_name||'?')}</div>
        <div class="mono" style="font-size:11.5px;color:var(--warn)">${esc(c.error||'unknown error')}</div>`;
      hit(row,()=>{location.hash='#/compare/'+c.id;},`${c.snapshot_name}, open it`);
      eb.append(row);
    });
    page.append(eb);
  }

  const groups=el('div');page.append(groups);
  api('/api/runs/'+r.id+'/clusters').then(cl=>{
    const list=cl.clusters||[];
    if(!list.length)return;
    groups.append(sectionHead('Causes in this run',
      'an answer to a cause closes every snapshot behind it'));
    list.forEach(g=>groups.append(renderCluster(g,r)));
  }).catch(()=>{});

  const comps=(r.comparisons||[]).filter(c=>c.verdict!=='error');
  if(comps.length){
    page.append(sectionHead('Snapshots',String(comps.length)));
    const rows=el('div','rows');
    const cols='1fr 120px 90px 100px 110px';
    const th=el('div','th');th.style.display='grid';
    th.style.gridTemplateColumns=cols;th.style.gap='12px';
    th.innerHTML='<div>SNAPSHOT</div><div>VERDICT</div><div class="r">SEV</div>'
      +'<div class="r">AREA</div><div class="r">SSIM</div>';
    rows.append(th);
    comps.forEach(cp=>{
      const tr=el('div','tr click snap-row');
      tr.style.display='grid';tr.style.gridTemplateColumns=cols;tr.style.gap='12px';
      tr.innerHTML=`<div class="mono sn" style="font-size:12px;font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(cp.snapshot_name)}</div>
        <div>${verdictTag(cp)}</div>
        <div class="m r">${fmt(cp.max_severity,1)}</div>
        <div class="m r">${pct(cp.changed_area_pct,3)}</div>
        <div class="m r">${fmt(cp.ssim,4)}</div>`;
      hit(tr,()=>{location.hash='#/compare/'+cp.id;},
          `${cp.snapshot_name||'snapshot'}, open the review`);
      rows.append(tr);
    });
    page.append(rows);
  }
}

function verdictTag(cp){
  if(cp.review==='approved')return '<span class="tag purple">ACCEPTED</span>';
  if(cp.review==='rejected')return '<span class="tag red">BUG</span>';
  if(cp.verdict==='fail')return '<span class="tag accent">TO DECIDE</span>';
  if(cp.verdict==='error')return '<span class="tag amber">ERROR</span>';
  if(cp.verdict==='new_baseline')return '<span class="tag purple">NEW</span>';
  return '<span class="tag green">OK</span>';
}

function clusterSev(v){return v>=55?'sev-hi':v>=32?'sev-mid':'sev-lo';}
function renderCluster(g,run){
  const comps=clusterComps(g,run);
  const sev=Math.round(g.max_severity||0);
  const cnt=g.snapshot_count||comps.length||0;

  const box=el('div','cause');
  const sv=el('div','sev '+clusterSev(sev));
  sv.innerHTML=`<div class="n">${sev}</div><div class="u">SEV</div><div class="x">×${cnt}</div>`;

  const body=el('div','body');
  const heads=comps.map(x=>String(x.snapshot_name||'').split('/')[0])
                   .filter((v,i,a)=>v&&a.indexOf(v)===i).slice(0,6);
  body.innerHTML=`<div class="ttl"><span class="tag ${sev>=55?'red':sev>=32?'amber':'ink'}">${esc((g.kind||'change').toUpperCase())}</span>
      <h3>${esc(g.description||'Change group')}</h3></div>
    ${g.common?`<div class="sel">${esc(g.common)}</div>`:''}
    <div class="why">${esc(g.advice||'')}</div>
    <div class="chips">${heads.map(h=>`<span class="chip">${esc(h)}</span>`).join('')}</div>`;

  const ans=el('div','ans');
  ans.append(el('div','k',`ANSWER ONCE · ${cnt} ${plural(cnt,'SNAPSHOT','SNAPSHOTS','SNAPSHOTS')}`));
  const ok=el('button','btn dark','Accept as the new normal');
  ok.onclick=()=>groupDecision(comps,'approve',ok);
  const bug=el('button','btn danger','Confirm as a bug');
  bug.onclick=()=>groupDecision(comps,'reject',bug);
  const two=el('div','two');
  const look=el('button','btn sm','Inspect');
  look.onclick=()=>{if(comps[0])location.hash='#/compare/'+comps[0].id;else toast('No open snapshot to review');};
  const defer=el('button','btn sm','Defer');
  defer.onclick=()=>toast('Left as is');
  two.append(look,defer);
  ans.append(ok,bug,two,el('div','risk',
    `the decision applies to all ${cnt} ${plural(cnt,'snapshot','snapshots','snapshots')}`));

  box.append(sv,body,ans);
  return box;
}
function clusterComps(g,run){
  const comps=(run.comparisons||[]);const snaps=g.snapshots||[];
  if(!snaps.length)return [];
  const out=[];
  snaps.forEach(sn=>{
    if(sn&&typeof sn==='object'){const id=sn.id||sn.comparison_id;const found=comps.find(c=>c.id===id)||{id,snapshot_name:sn.snapshot_name||sn.name||''};if(id)out.push(found);}
    else{const found=comps.find(c=>c.snapshot_name===sn||c.id===sn);if(found)out.push(found);}
  });
  return out;
}
async function groupDecision(comps,action,btn){
  if(!comps.length)return toast('No snapshots in the group','err');
  if(!confirm(`Apply «${action==='approve'?'accept as normal':'it is a bug'}» to ${comps.length} snapshots?`))return;
  btn.disabled=true;const old=btn.textContent;btn.innerHTML='<span class="spin"></span>';
  try{
    /* Один запрос вместо N. Раньше это был цикл из отдельных POST: сорок
       round-trip на одну группу, каждый мог упасть сам по себе и оставить
       группу решённой наполовину. И ни один из них не нёс expected_version —
       то есть массовый апрув тихо обходил защиту от чужого решения. */
    const r=await api('/api/comparisons/bulk',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({ids:comps.map(c=>c.id),action})});
    const done=r.counts?r.counts.done:0,bad=r.counts?r.counts.failed:0;
    if(bad){
      const why=(r.failed||[]).slice(0,3).map(f=>`#${f.id}: ${f.error}`).join('\n');
      toast(`Done ${done}, ${bad} could not be applied\n${why}`,'err');
    }else{
      toast(`Done: ${done} processed`,'ok');
    }
  }catch(e){toast(String(e.message||e),'err');}
  finally{btn.disabled=false;btn.textContent=old;}
  if(state.run)renderRunPage(state.run.id);
  refreshCounts();
}

async function cleanupRuns(){
  const days=prompt('Delete runs older than how many days? (the last 20 are kept)','30');
  if(days==null)return;
  try{const r=await api('/api/runs/cleanup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({days:Number(days),keep_last:20})});
    toast(`Runs deleted: ${r.deleted||0}, freed ${Math.round((r.freed_kb||0)/1024)} MB`,'ok');SCREENS.runs();refreshCounts();}
  catch(e){toast(String(e.message||e),'err');}
}
