/* VisTest - self-hosted visual regression testing.
 * Copyright (C) 2026 Kirill Kulagin
 * SPDX-License-Identifier: AGPL-3.0-or-later
 *
 * This file is part of VisTest. See LICENSE for the full terms and NOTICE for
 * the trademark and commercial-licensing terms. Removing this header does not
 * remove those obligations.
 */

/* Экран «Metrics»: гистограмма severity, ложные падения, шум, дорогие снимки. */
SCREENS.dash=async function(){
  loadingScreen();
  const here=pageGuard();
  const days=state.dashDays||30;
  let m;try{m=await api('/api/metrics/summary?days='+days+'&project='+encodeURIComponent(state.project));}catch(e){return errScreen(e);}
  if(!here())return;
  const t=m.totals||{};
  const s=$('#screen');s.innerHTML='';
  const page=el('div','page mid');s.append(page);
  const head=el('div','head');
  const left0=el('div','grow');
  left0.innerHTML=`<div class="eyebrow">HEALTH · ${days} DAYS</div>
    <h1 class="h1">Can a red run be trusted?</h1>`;
  head.append(left0);
  const ctr=el('div','acts');
  const dd=el('button','btn',days+' days ▾');dd.onclick=e=>daysMenu(e);
  const pub=el('button','btn','Export');
  pub.title='Statistics for publishing — one HTML with the numbers and how they were counted';
  pub.onclick=()=>window.open(API+'/api/metrics/evidence?format=html','_blank');
  ctr.append(dd,pub);head.append(ctr);page.append(head);

  /* Герой. Раньше здесь было безусловное число: при нулевом числе разобранных
     падений печаталось «0% ложных», и это читалось как результат, хотя
     означало «никто не смотрел». */
  const ff=t.false_fail_rate;
  const hero=el('div','hero');
  const approved=t.approved||0,rejected=t.rejected||0,failed=t.failed||0;
  const notReviewed=t.unreviewed!=null?t.unreviewed:Math.max(0,failed-approved-rejected);
  const tot=Math.max(1,approved+notReviewed+rejected);
  const ci=t.confidence||{};
  const ciText=(ff!=null&&ci.low!=null)
    ? `between ${Math.round(ci.low*100)}% and ${Math.round(ci.high*100)}% · ${ci.n} reviewed`
    : 'not enough reviewed failures yet';
  hero.innerHTML=`<div style="display:flex;align-items:flex-start;gap:20px;flex-wrap:wrap">
      <div class="mono" style="font-size:46px;font-weight:600;line-height:.9;letter-spacing:-2px;color:${ff!=null&&ff>0.2?'var(--warn)':'var(--pass)'}">${ff!=null?Math.round(ff*100)+'%':'n/a'}</div>
      <div style="flex:1;min-width:260px">
        <div style="font-size:16px;font-weight:600">${ff!=null?'of reviewed failures turned out to be false':'nothing reviewed yet'}</div>
        <div class="lede">Above 20% people start approving without looking. Two levers:
          raise the threshold, or mask the noisy snapshots listed on the right.
          ${t.enough_data===false?'<b>Too little data to draw a conclusion.</b>':''}</div>
      </div>
      <div class="mono" style="font-size:11px;color:var(--muted);text-align:right;line-height:1.7">
        ${t.pass_rate!=null?'passed '+Math.round(t.pass_rate*100)+'%':'nothing judged'}<br>${esc(ciText)}</div>
    </div>
    <div class="split-bar" style="margin-top:16px">
      <div style="width:${approved/tot*100}%;background:var(--warn)"></div>
      <div style="width:${notReviewed/tot*100}%;background:#E8D6B4"></div>
      <div style="width:${rejected/tot*100}%;background:var(--line)"></div>
    </div>
    <div class="legend">
      <span><i style="background:var(--warn)"></i>accepted ${approved}</span>
      <span><i style="background:#E8D6B4"></i>pending ${notReviewed}</span>
      <span><i style="background:var(--line)"></i>real bugs ${rejected}</span></div>`;
  page.append(hero);

  const ttr=t.time_to_review||{};
  const dur=t.duration_ms||{};
  const kc=el('div','kpi-cards');
  const cards=[
    {v:t.pass_rate!=null?Math.round(t.pass_rate*100)+'%':'—',k:'Passes',
     d:'Of comparisons that could pass or fail. New baselines and errors are excluded — they were never judged.',c:'var(--pass)'},
    {v:fmtInt(t.comparisons),k:'Comparisons',d:'Checked in '+days+' days. Under 30 — too early to draw conclusions.'},
    {v:fmtInt(t.failed),k:'Failures',d:`${approved} accepted as normal, ${rejected} confirmed as a bug, ${notReviewed} waiting.`,c:'var(--fail)'},
    // Ошибки были невидимы: они не попадали в сводку вообще и просто топили pass_rate.
    {v:fmtInt(t.errored),k:'Errors',d:'Capture or engine failed — nothing was compared at all.',c:t.errored?'var(--warn)':''},
    {v:(dur.p50!=null?fmtInt(dur.p50)+' ms':'—'),k:'Comparison p50',
     d:`p95 ${dur.p95!=null?fmtInt(dur.p95)+' ms':'—'}. The mean alone lies here: one full-page shot drags it away.`},
    {v:(ttr.median_hours!=null?fmt(ttr.median_hours,1)+' h':'—'),k:'Time to review',
     d:`${ttr.waiting||0} failures still waiting. If this grows, the number above stops meaning anything.`},
  ];
  cards.forEach(c=>{const d=el('div','kpi-card');d.innerHTML=`<div class="k">${esc(String(c.k).toUpperCase())}</div><div class="v"${c.c?` style="color:${c.c}"`:''}>${c.v}</div><div class="d">${c.d}</div>`;kc.append(d);});
  page.append(kc);

  const p2=el('div','panel2');
  // trend
  const trend=m.trend||[];
  const tbar=el('div','pcard');
  tbar.innerHTML='<h3>Trend by day</h3><div class="d">A bar is comparisons per day, red is failed. A steady red strip day after day is not regressions but noise.</div>';
  const bars=el('div','bars');
  const maxT=Math.max(1,...trend.map(x=>x.total||x.count||0));
  trend.forEach(x=>{
    const total=x.total||x.count||0,fail=x.failed||x.fail||0;
    const b=el('div','b');
    const h=total/maxT*140;
    b.innerHTML=`<div class="fail" style="height:${fail/maxT*140}px"></div><div class="ok" style="height:${Math.max(0,h-fail/maxT*140)}px"></div>`;
    b.title=`${x.date||x.day||''}: ${total} / ${fail} failed`;
    bars.append(b);
  });
  tbar.append(bars);
  if(trend.length){const xr=el('div','bars-x');xr.innerHTML=`<span>${esc(trend[0].date||trend[0].day||'')}</span><span>${esc(trend[trend.length-1].date||trend[trend.length-1].day||'')}</span>`;tbar.append(xr);}
  p2.append(tbar);
  // noise sources
  const nz=el('div','pcard');
  nz.innerHTML='<h3>What is noisiest most often</h3><div class="d">Snapshots that fail and then get accepted — each row is either a mask to add or a snapshot to delete.</div>';
  const src=noiseSources(m);const nrows=el('div','noise-row');
  const maxN=Math.max(1,...src.map(x=>x[1]));
  src.slice(0,5).forEach(([name,val],i)=>{
    const nr=el('div','nr');const col=i<2?'var(--fail)':i<4?'var(--warn)':'var(--line)';
    nr.innerHTML=`<div class="nl"><span>${esc(name)}</span><span>${esc(String(val))}</span></div><div class="track"><div class="fill" style="width:${val/maxN*100}%;background:${col}"></div></div>`;
    nrows.append(nr);
  });
  if(!src.length)nrows.append(el('div','muted','No data yet'));
  nz.append(nrows);p2.append(nz);
  page.append(p2);

  /* Гистограмма severity против порога. Бэкенд считал её с самого начала, а
     дашборд не рисовал — при том что это ровно тот график, с которого
     начинается настройка: он отвечает на вопрос «куда ставить порог». */
  const sev=m.severity||{};
  if((sev.buckets||[]).length&&sev.total){
    const card=el('div','pcard');card.style.marginTop='14px';
    card.innerHTML=`<h3>Severity of failures against the threshold</h3>
      <div class="d">The threshold is <b>${fmt(sev.threshold,0)}</b>. Bars left of it are noise the engine already lets through; bars piled right next to it are a matter of taste, not regressions. A gap between two clusters is where the threshold belongs.</div>`;
    const maxB=Math.max(1,...sev.buckets.map(b=>b.n||0));
    const bars=el('div','bars');bars.style.alignItems='flex-end';
    /* Линия порога прямо на графике. График без неё отвечает на «как
       распределены severity», а вопрос у человека другой: «где сейчас стоит
       порог и что он отсекает». */
    const span=Math.max(1,(sev.buckets[sev.buckets.length-1].to||100));
    const thr=el('div','thr');
    thr.style.left=Math.min(100,Math.max(0,(sev.threshold||0)/span*100))+'%';
    thr.title='threshold '+fmt(sev.threshold,0);
    bars.append(thr);
    sev.buckets.forEach(b=>{
      const col=el('div','b');
      const h=(b.n||0)/maxB*120;
      const app=(b.approved||0)/maxB*120;
      const past=b.from>=(sev.threshold||0);
      col.innerHTML=`<div class="fail" style="height:${Math.max(0,h-app)}px;background:${past?'var(--fail)':'var(--line)'}"></div>
        <div class="ok" style="height:${app}px;background:var(--warn)"></div>`;
      col.title=`severity ${b.from}–${b.to}: ${b.n} failures, ${b.approved} accepted as normal`;
      bars.append(col);
    });
    card.append(bars);
    const xr=el('div','bars-x');
    xr.innerHTML=`<span>sev 0</span><span style="color:var(--accent)">threshold ${fmt(sev.threshold,0)}</span><span>sev ${span}</span>`;
    card.append(xr);
    card.append(el('div','stage-hint','amber — accepted as normal by a human. Amber to the right of the threshold means real regressions are being waved through; grey to the left means the threshold could go lower.'));
    page.append(card);
  }

  // Медленные снимки и мёртвый груз — тоже считались и не показывались.
  const p3=el('div','panel2');p3.style.marginTop='14px';
  p3.append(listCard('Slowest snapshots',
    'Usually full-page canvases. They set the p95 above.',
    (m.slowest||[]).map(x=>[x.name,fmtInt(x.avg_ms)+' ms'])));
  p3.append(listCard('Not run for a long time',
    'Dead weight nobody dares delete. Either bring them back into a run, or remove them.',
    (m.stale||[]).slice(0,8).map(x=>[x.name,x.last_run?timeAgo(x.last_run):'never'])));
  page.append(p3);

  // Что реально ловит баги — обратная сторона flaky.
  const val=m.valuable||[];
  if(val.length){
    page.append(listCard('Snapshots that caught real bugs',
      'Confirmed as a bug by a human. This is what the whole set is paid for.',
      val.map(x=>[x.name,`${x.caught} caught`]),true));
  }

  // Разбивка по проектам — при «все проекты» всё раньше схлопывалось в одно число.
  const bp=m.by_project||[];
  if(state.project==='*'&&bp.length>1){
    page.append(listCard('By project',
      'Which suite the failures actually come from.',
      bp.map(x=>[x.project,`${x.failed} fail · ${x.unreviewed} waiting · ${x.comparisons} total`]),true));
  }
};

function listCard(title,note,rows,wide){
  const c=el('div','pcard');
  if(wide)c.style.marginTop='14px';
  c.innerHTML=`<h3>${esc(title)}</h3><div class="d">${esc(note)}</div>`;
  if(!rows.length){c.append(el('div','muted','No data yet'));return c;}
  const box=el('div');box.style.marginTop='10px';
  rows.forEach(([left,right])=>{
    const r=el('div');
    r.style.cssText='display:flex;justify-content:space-between;gap:12px;padding:6px 0;border-bottom:1px solid var(--line);font-size:13px';
    r.innerHTML=`<span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(left)}</span><span class="mono muted">${esc(right)}</span>`;
    box.append(r);
  });
  c.append(box);return c;
}

function noiseSources(m){
  /* `by_kind` приходит с бэкенда СПИСКОМ `[{kind, n, avg_severity}]`, а здесь
     стоял `Object.entries(...)`. На массиве это даёт пары ["0",{...}] — подписи
     превращались в «0, 1, 2», значения в объекты, ширины полос в NaN. То есть
     панель разваливалась всегда, когда список flaky пуст. */
  if(Array.isArray(m.flaky)&&m.flaky.length)
    return m.flaky.map(f=>[f.name||f.selector||'?',f.approved_fails||f.fails||0])
                  .sort((a,b)=>b[1]-a[1]);
  const bk=m.by_kind;
  if(Array.isArray(bk))return bk.map(x=>[x.kind||'?',x.n||0]).sort((a,b)=>b[1]-a[1]);
  return Object.entries(bk||{}).sort((a,b)=>b[1]-a[1]);
}
function daysMenu(e){
  e.stopPropagation();closeMenus();const m=el('div','menu');const rc=e.target.getBoundingClientRect();
  m.style.top=(rc.bottom+6)+'px';m.style.left=rc.left+'px';
  [7,14,30,90].forEach(d=>m.append(mkItem('For '+d+' days',()=>{state.dashDays=d;SCREENS.dash();})));
  document.body.append(m);stopClose(m);
}

