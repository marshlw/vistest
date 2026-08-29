/* VisTest - self-hosted visual regression testing.
 * Copyright (C) 2026 Kirill Kulagin
 * SPDX-License-Identifier: AGPL-3.0-or-later
 *
 * This file is part of VisTest. See LICENSE for the full terms and NOTICE for
 * the trademark and commercial-licensing terms. Removing this header does not
 * remove those obligations.
 */

/* Мелочи, которыми пользуются несколько экранов: лайтбокс, заглушки, `runJob`.

   Файл маленький и таким должен остаться. Всё, что нужно ровно одному экрану,
   живёт в этом экране — иначе «shared» за полгода превращается в свалку, куда
   кладут, чтобы не думать, где ему место. */
function lightbox(src){const b=el('div','lightbox');b.append(el('img'));$('img',b).src=src;b.onclick=()=>b.remove();document.body.append(b);}
function missingArtifact(url){const d=el('div','empty','Artifact unavailable'+(url?`<div class="mono faint" style="font-size:11px;margin-top:6px">${esc(url)}</div>`:'')+'<div class="faint" style="font-size:12px;margin-top:6px">For cross-machine runs images may not be saved.</div>');return d;}
function skeleton(){const d=el('div');d.style.cssText='padding:14px';d.innerHTML='<div style="height:12px;width:60%;background:#E4E1DA;border-radius:6px;margin-bottom:10px"></div><div style="height:12px;width:80%;background:#E9E6DF;border-radius:6px;margin-bottom:10px"></div><div style="height:12px;width:45%;background:#E9E6DF;border-radius:6px"></div>';return d;}
function loadingScreen(){$('#screen').innerHTML='<div class="loading-wrap"><span class="spin"></span> Loading…</div>';}
function errScreen(e){$('#screen').innerHTML=`<div class="empty" style="padding:60px"><b>Could not load</b><div class="mono faint" style="font-size:12px;margin-top:10px;white-space:pre-line">${esc(String(e.message||e))}</div></div>`;}

async function runJob(starter,opts={}){
  opts=opts||{};let job_id;
  try{const r=await starter;job_id=r&&(r.job_id||r.id);if(!job_id){if(r&&r.queued===false)return r;return r;}}
  catch(e){toast(String(e.message||e),'err');throw e;}
  const tid=toast((opts.title||'Task')+' — started…');
  let offset=0,last=null,waited=false;
  while(true){
    await new Promise(r=>setTimeout(r,700));
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
