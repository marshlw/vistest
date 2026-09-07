/* VisTest - self-hosted visual regression testing.
 * Copyright (C) 2026 Kirill Kulagin
 * SPDX-License-Identifier: AGPL-3.0-or-later
 *
 * This file is part of VisTest. See LICENSE for the full terms and NOTICE for
 * the trademark and commercial-licensing terms. Removing this header does not
 * remove those obligations.
 */

/* Экран «Environment»: сколько падений даёт неизменившаяся страница.

   Единственный экран, который отвечает на вопрос до всех остальных вопросов.
   Пока стенд даёт свои проценты ложных падений, любое красное на других
   экранах означает не «сломалось», а «может быть, сломалось», — и человек,
   который это однажды понял, перестаёт верить всему сразу. Поэтому число
   отсюда продублировано в сайдбаре: смотреть на него надо не тогда, когда за
   ним пришли. */
SCREENS.doctor=async function(){
  loadingScreen();
  const here=pageGuard();
  let env,last;
  try{env=await api('/api/doctor/env');}catch(e){return errScreen(e);}
  last=await api('/api/doctor/last').catch(()=>null);
  if(!here())return;

  const s=$('#screen');s.innerHTML='';
  const page=el('div','page narrow');s.append(page);
  page.innerHTML=`<div class="eyebrow">HEALTH · STAND</div>
    <h1 class="h1">How many failures does an unchanged page produce?</h1>
    <div class="lede">The engine loads one URL several times and compares the shots
      against each other. Anything it finds is noise, not a regression.</div>`;

  const bar=el('div','joined');
  const url=el('input');url.placeholder='https://my-app.local/checkout';
  url.style.flex='1';url.value=(last&&last.report&&last.report.target)||'';
  const runs=el('input');runs.style.cssText='width:76px;text-align:right';runs.value='8';
  const go=el('button',null,'Measure noise');
  go.onclick=()=>{
    if(!url.value.trim())return toast('Set a URL','err');
    runJob(api('/api/doctor/run',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({url:url.value.trim(),runs:Number(runs.value)||8})}),
      {title:'Noise measurement',then:()=>SCREENS.doctor()});
  };
  bar.append(url,runs,el('div','lbl','loads'),go);
  page.append(bar);

  if(last&&last.report){
    const rp=last.report;
    const ff=rp.false_fail!=null?rp.false_fail:(rp.noise_ratio||0);
    const good=ff<0.05;
    const total=rp.runs||rp.size||8;
    const box=el('div','panel flush');box.style.padding='16px 18px';

    const top=el('div');
    top.style.cssText='display:flex;align-items:baseline;gap:12px;flex-wrap:wrap';
    top.innerHTML=`<div class="mono" style="font-size:34px;font-weight:600;letter-spacing:-1px;color:${good?'var(--pass)':'var(--warn)'}">${(ff*100).toFixed(1)}%</div>
      <div style="font-size:15px;font-weight:600">${good?'the stand can be trusted':'the stand has its own noise'}</div>
      <div class="mono" style="margin-left:auto;font-size:11px;color:var(--muted)">${last.mtime?'measured '+esc(timeAgo(last.mtime*1000)):''} · ${total} loads</div>`;
    box.append(top);
    if(rp.verdict)box.append(el('div','lede',esc(rp.verdict)));

    /* Две полосы рядом — единственный честный способ показать, что делает
       подавление: одно число «0.4%» не отвечает на вопрос «а без него
       сколько». Без ответа заслуга движка не видна, а с ним видно, что стенд
       шумит и починить его всё равно стоит. */
    const raw=rp.raw_false_fail!=null?rp.raw_false_fail
      :(rp.raw_noise!=null?rp.raw_noise:null);
    if(raw!=null){
      box.append(noiseBar('without suppression',raw,'var(--fail)',total));
      box.append(noiseBar('with VisTest suppression',ff,'var(--pass)',total));
    }
    page.append(box);

    const sources=rp.sources||[];
    if(sources.length){
      page.append(sectionHead('What exactly is noisy','by how often it occurred'));
      const rows=el('div','panel');
      sources.forEach(src=>{
        const sup=src.suppressed!==false&&src.suppressed!=='no';
        const r=el('div','tr');
        r.style.cssText='display:grid;grid-template-columns:130px 190px 1fr 120px;'
          +'gap:12px;align-items:center;padding:11px 15px';
        r.innerHTML=`<span class="tag ${sup?'green':'red'}" style="justify-self:start">${sup?'SUPPRESSED':'NOT SUPPRESSED'}</span>
          <div style="font-size:12.5px;font-weight:500">${esc(src.kind||src.name||'')}</div>
          <div><div class="mono" style="font-size:11.5px;color:var(--accent)">${esc(src.where||src.selector||'')}</div>
            <div style="font-size:11.5px;color:var(--muted);margin-top:2px">${esc(src.hint||'')}</div></div>
          <div class="mono r" style="font-size:11.5px;color:var(--ink2)">${esc(src.seen||'')}</div>`;
        rows.append(r);
      });
      page.append(rows);
    }
  }

  page.append(sectionHead('Environment','everything below must be identical between runs'));
  const et=el('div','panel');
  const rows=[['python',env.python||'—',env.python?'ok':'missing'],
    ['baseline platform',env.platform_key||'—',env.docker?'docker':'ok'],
    ['data directory',env.root||'—','ok'],
    ['vistest package',env.module||'—',env.editable?'linked':'copy'],
    ['git',env.git||'not found',env.git?'ok':'optional']];
  rows.forEach(([k,v,tag])=>et.append(envFactRow(k,v,tag,!/missing|copy/.test(tag))));
  (env.dependencies||[]).forEach(d=>et.append(envFactRow(
    d.name,d.present?'installed':'missing',
    d.present?'ok':(d.required?'required':'optional'),
    !!d.present||!d.required)));
  page.append(et);

  /* Чем эта инсталляция способна запустить ЧУЖОЙ набор.

     Подключение с собственной командой выполняет её внутри нашего
     контейнера. Пока этого списка не было, ответ на «почему npx не найден»
     находился только в логе упавшего прогона — то есть уже после того, как
     человек всё настроил и нажал запуск. */
  const rts=env.runtimes||[];
  if(rts.length){
    page.append(sectionHead('Runtimes for connected suites',
      'a suite started by its own command runs inside this container — its tool has to be here'));
    const rt=el('div','panel');
    rts.forEach(r=>rt.append(envFactRow(
      r.name,r.path||'not found',r.path?'ok':'optional',true)));
    page.append(rt);
  }
};

/* Имя нарочно не `envRow`: так называется строка редактора секретов в
   настройках, и общая область видимости у файлов одна. Две функции с одним
   именем — это не «переопределение», а тихая замена одной другой в
   зависимости от порядка подключения: таблица окружения показывала список
   секретов и выглядела при этом совершенно правдоподобно. */
function envFactRow(k,v,tag,ok){
  const r=el('div','tr');
  r.style.cssText='display:grid;grid-template-columns:180px 1fr 90px;gap:12px;'
    +'align-items:center;padding:9px 15px;font-family:var(--mono);font-size:12px';
  r.innerHTML=`<div style="color:var(--faint)">${esc(k)}</div>
    <div style="overflow:hidden;text-overflow:ellipsis">${esc(v)}</div>
    <div class="r" style="font-size:10px;letter-spacing:.08em;color:${ok?'var(--pass)':'var(--warn)'}">${esc(String(tag).toUpperCase())}</div>`;
  return r;
}
function noiseBar(label,val,color,total){
  const d=el('div');
  d.style.cssText='display:grid;grid-template-columns:190px 1fr 110px;gap:12px;'
    +'align-items:center;margin-top:11px;font-size:12.5px';
  d.innerHTML=`<div>${esc(label)}</div>
    <div class="meter"><i style="width:${Math.min(100,val*100)}%;background:${color}"></i></div>
    <div class="mono r" style="font-size:11.5px;color:var(--ink2)">${Math.round(val*total)} of ${total} · ${Math.round(val*100)}%</div>`;
  return d;
}
