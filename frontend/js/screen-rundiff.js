/* VisTest - self-hosted visual regression testing.
 * Copyright (C) 2026 Kirill Kulagin
 * SPDX-License-Identifier: AGPL-3.0-or-later
 *
 * This file is part of VisTest. See LICENSE for the full terms and NOTICE for
 * the trademark and commercial-licensing terms. Removing this header does not
 * remove those obligations.
 */

/* Экран «What changed»: два прогона рядом, разложенные по изменению состояния.

   Прогон отвечает на вопрос «что красное сейчас». Перед мержем спрашивают
   другое — «что сломала эта ветка», — и до сих пор ответ добывался глазами по
   двум вкладкам. Отсюда и адрес `#/diff/<head>/<base>`: ссылка на разбор
   должна класться в MR так же, как кладётся ссылка на прогон.

   Разбор целиком на бэкенде (`vistest/api/rundiff.py`), здесь только показ. */

const DIFF_GROUPS = {
  broke:        {title:'Broke here',
                 note:'green before, red now — this is the answer to «what did this branch break»',
                 tone:'red'},
  gone:         {title:'No longer checked',
                 note:'the snapshot is not in the newer run at all: a deleted test, a renamed snapshot, or a fixture that failed before the capture',
                 tone:'amber'},
  still_failing:{title:'Red in both',
                 note:'not this branch — but a growing severity is',
                 tone:'muted'},
  added:        {title:'New snapshots',
                 note:'there was nothing to break: they simply appeared',
                 tone:'purple'},
  fixed:        {title:'Fixed',
                 note:'red before, green now',
                 tone:'green'},
};

SCREENS.diff = async function(arg){
  /* `#/diff/42` — сравнить с предыдущим прогоном, `#/diff/42/37` — с
     конкретным. Второй сегмент необязателен намеренно: «что изменилось с
     прошлого раза» — самый частый вопрос, и требовать для него выбрать базу
     руками значит заставить человека сначала узнать её номер. */
  const parts=String(arg||'').split('/').filter(Boolean);
  let head=parts[0];
  const base=parts[1];
  /* Без аргумента — самый свежий прогон набора. Пункт «What changed» в
     навигации ведёт именно сюда, и требовать от человека сначала узнать номер
     прогона значит отправить его обратно в список за числом, которое
     интерфейс и так знает. Раньше здесь стоял безусловный уход на «Runs», то
     есть на экран, с которого только что пришли. */
  if(!head){
    loadingScreen();
    let recent=state.runs||[];
    if(!recent.length){
      try{recent=await api('/api/runs?limit=2&project='
                           +encodeURIComponent(state.project));}
      catch(e){return errScreen(e);}
    }
    if(!recent.length)return noRunsToDiff();
    head=recent[0].id;
    /* Адрес обязан назвать прогон: ссылка на «что изменилось» кладётся в MR, и
       завтра «последний прогон» будет уже другим. `replace` — чтобы «назад»
       вернуло туда, откуда пришли, а не на этот же экран. */
    location.replace('#/diff/'+head);
    return;
  }

  loadingScreen();
  const here=pageGuard();
  let d;
  try{d=await api('/api/runs/'+encodeURIComponent(head)+'/diff'
                  +(base?'?base='+encodeURIComponent(base):''));}
  catch(e){return diffError(head,e);}

  /* Список прогонов для выбора базы. Приходя сюда по ссылке из MR, человек
     не был на вкладке «Runs», и `state.runs` пуст — выбор базы тогда сводился
     бы к одному пункту «предыдущий», то есть отсутствовал. */
  if(!(state.runs||[]).length){
    try{state.runs=await api('/api/runs?limit=60&project='
                             +encodeURIComponent(state.project));}
    catch(e){state.runs=[];}
  }

  if(!here())return;
  const screen=$('#screen');screen.innerHTML='';
  const s=el('div','page');screen.append(s);
  s.append(diffHead(d,head));
  (d.warnings||[]).forEach(w=>s.append(el('div','err-banner',
    `<div class="err-head">⚠ ${esc(w)}</div>`)));
  s.append(diffSummary(d));

  let empty=true;
  (d.order||[]).forEach(name=>{
    const items=(d.groups||{})[name]||[];
    if(!items.length)return;
    empty=false;
    s.append(diffGroup(name,items));
  });
  if(empty){
    s.append(el('div','panel',
      '<div class="empty">Nothing moved between these two runs.'
      +'<div class="faint" style="font-size:12.5px;margin-top:8px">The same snapshots with the same verdicts.</div></div>'));
  }
};

/* Сравнивать нечего, потому что сравнивать нечего: прогонов ещё нет. Это не
   ошибка и говорить о ней как об ошибке не нужно — нужно сказать, откуда
   берутся прогоны. */
function noRunsToDiff(){
  const screen=$('#screen');screen.innerHTML='';
  const page=el('div','page narrow');screen.append(page);
  const head=el('div','head');
  head.innerHTML=`<div class="grow"><div class="eyebrow">RUNS · WHAT CHANGED</div>
    <h1 class="h1">Two runs, side by side</h1>
    <div class="lede">This screen answers «what did this branch break»: it takes two
      runs and splits every snapshot by how its verdict moved between them.</div></div>`;
  page.append(head);
  const box=el('div','panel');box.style.marginTop='18px';
  box.innerHTML='<div class="empty"><b>There are no runs yet to compare</b>'
    +'<div class="what">A comparison needs two runs of the same set. '
    +'Run <code>python run.py test</code>, or start a suite from the «Projects» tab '
    +'— the second run is where this screen starts working.</div></div>';
  const acts=el('div','acts');
  const go=el('button','btn dark','Go to runs');
  go.onclick=()=>{location.hash='#/runs';};
  acts.append(go);
  $('.empty',box).append(acts);
  page.append(box);
}

function diffError(head,e){
  const msg=String(e.message||e);
  const screen=$('#screen');screen.innerHTML='';
  const page=el('div','page narrow');screen.append(page);
  const card=el('div','panel pad');
  card.innerHTML=`<h3 class="h2">Nothing to compare</h3>
    <p class="lede">${esc(msg)}</p>
    <p class="lede" style="font-size:12.5px">Open the run and pick another base,
      or go back to the list.</p>`;
  const back=el('button','btn');back.style.marginTop='14px';
  back.textContent='‹ Back to the run';
  back.onclick=()=>{location.hash='#/runs/'+head;};
  card.append(back);page.append(card);
}

function runChip(r,label){
  const nm=esc(r.run_key||('#'+r.id));
  return `<div class="dchip"><div class="k">${esc(label)}</div>
    <div class="v">${nm} <span class="faint">#${esc(String(r.id))}</span></div>
    <div class="m">${esc(r.branch||'—')} · ${esc(r.git_sha||'—')} · ${esc(timeAgo(r.started_at))}</div></div>`;
}

function diffHead(d,head){
  const box=el('div','rd-head');
  const left=el('div');left.style.flex='1';
  left.innerHTML=`<div class="eyebrow">RUNS · COMPARISON</div>
    <h1 class="h1">What changed</h1>
    <div class="dchips">${runChip(d.base,'compared against')}<span class="darrow">→</span>${runChip(d.head,'this run')}</div>`;
  box.append(left);

  const right=el('div');
  right.style.cssText='display:flex;flex-direction:column;align-items:flex-end;gap:8px';
  const row=el('div');row.style.cssText='display:flex;gap:8px;align-items:center';

  /* Смена базы прямо здесь. Иначе «сравнить не с предыдущим, а с мастером»
     означает уйти в список, найти номер прогона глазами и собрать адрес
     руками — то есть не делается. */
  const sel=el('select','inp');
  sel.style.cssText='width:auto;min-width:230px;font-size:13px';
  sel.setAttribute('aria-label','Run to compare against');
  sel.innerHTML='<option value="">previous run</option>';
  /* База может не попасть в загруженный список — другой проект, другая
     страница истории. Тогда селектор показал бы «предыдущий прогон»
     выбранным, хотя сравнение идёт совсем с другим: подпись врала бы ровно о
     том, ради чего этот экран и открыт. */
  const runs=(state.runs||[]).slice();
  if(d.base&&!runs.some(r=>r.id===d.base.id))runs.unshift(d.base);
  runs.filter(r=>r.id!==d.head.id).forEach(r=>{
    const o=el('option');o.value=r.id;
    o.textContent=`${r.run_key||('#'+r.id)} · ${r.branch||'—'} · ${timeAgo(r.started_at)}`;
    if(r.id===d.base.id)o.selected=true;
    sel.append(o);
  });
  sel.onchange=()=>{location.hash='#/diff/'+head+(sel.value?'/'+sel.value:'');};

  const back=el('button','btn sm','← The run');
  back.onclick=()=>{location.hash='#/runs/'+d.head.id;};
  row.append(sel,back);
  right.append(row,el('div','rd-note','the link to this comparison can be pasted into the merge request'));
  box.append(right);
  return box;
}

function diffSummary(d){
  const c=d.counts||{};
  const attention=c.attention||0;
  const gone=c.gone||0;
  const sum=el('div','summary');
  if(d.clean)sum.classList.add('is-clean');

  /* Заголовок отвечает ровно на тот вопрос, ради которого сюда пришли, —
     и «чисто» здесь означает «эта ветка ничего не сломала», а не «всё
     зелёное». Прогон может быть красным целиком и при этом ничего не менять. */
  const title=d.clean
    ? 'This run broke nothing compared with the other one'
    : (attention||c.broke)
      ? `${c.broke} ${plural(c.broke,'snapshot','snapshots','snapshots')} went red here`
      : `${gone} ${plural(gone,'snapshot','snapshots','snapshots')} stopped being checked`;

  const lead=d.clean?0:(c.broke||gone);
  sum.innerHTML=`<div><div class="big">${fmtInt(lead)}</div>
      <div class="biglbl">${d.clean?'nothing broke':'need a look'}</div></div>
    <div><h3>${esc(title)}</h3>
    <p class="stext">Snapshots that were already red in the other run are not this branch's doing — they are listed separately so «the branch is clean» does not come to mean «the branch is no worse than a broken master».</p>
    <div class="kpirow">
      <div class="kpi"><div class="v" style="color:var(--fail)">${fmtInt(c.broke)}</div><div class="k">broke</div></div>
      <div class="kpi"><div class="v" style="color:var(--warn)">${fmtInt(c.gone)}</div><div class="k">no longer checked</div></div>
      <div class="kpi"><div class="v">${fmtInt(c.still_failing)}</div><div class="k">red in both</div></div>
      <div class="kpi"><div class="v" style="color:var(--purple)">${fmtInt(c.added)}</div><div class="k">new</div></div>
      <div class="kpi"><div class="v" style="color:var(--pass)">${fmtInt(c.fixed)}</div><div class="k">fixed</div></div>
      <div class="kpi"><div class="v">${fmtInt(c.same)}</div><div class="k">unchanged</div></div>
    </div>
    ${(c.broke&&attention<c.broke)?`<p class="stext" style="margin-top:10px">${attention} of them ${attention===1?'is':'are'} still unreviewed — the rest already have a decision.</p>`:''}
    </div>`;
  return sum;
}

/* Имя нарочно не `verdictTag`: так называется метка вердикта на странице
   прогона, а область видимости у файлов одна. Здесь метка другая — она
   описывает СТОРОНУ сравнения и умеет говорить «снимка не было». */
function sideTag(side){
  if(!side)return '<span class="tag">absent</span>';
  if(side.review==='approved')return '<span class="tag purple">accepted</span>';
  if(side.verdict==='fail')return '<span class="tag red">failed</span>';
  if(side.verdict==='error')return '<span class="tag amber">error</span>';
  if(side.verdict==='new_baseline')return '<span class="tag purple">new</span>';
  return '<span class="tag green">ok</span>';
}

function diffGroup(name,items){
  const g=DIFF_GROUPS[name]||{title:name,note:'',tone:''};
  const box=el('div','row-table');
  box.innerHTML=`<div class="sect-h"><h2>${esc(g.title)}</h2>
    <span class="note">${items.length} · ${esc(g.note)}</span></div>`;
  items.forEach(i=>{
    const row=el('div','snap-row');
    /* Четыре колонки одинаковой ширины во всех группах: человек читает этот
       экран сверху вниз через пять таблиц подряд, и «сломалось» с «починилось»
       он сравнивает по вертикали. Разъехавшиеся колонки это сравнение
       уничтожают. */
    row.style.gridTemplateColumns='1fr 190px 92px 78px';
    const delta=i.delta_severity;
    const dtxt=delta==null?'':(delta>0?'+':'')+fmt(delta,1);
    const dcls=i.worse?'style="color:var(--fail);font-weight:600"':'';
    row.innerHTML=`<div class="sn">${esc(i.name)}${i.browser?`<span class="faint" style="font-weight:400"> · ${esc(i.browser)}</span>`:''}</div>
      <div class="mono">${sideTag(i.base)} → ${sideTag(i.head)}</div>
      <div class="mono">sev ${fmt((i.head||i.base||{}).severity,1)}</div>
      <div class="mono" ${dcls}>${dtxt?'Δ '+esc(dtxt):''}</div>`;
    /* Открывать надо ту сторону, где снимок есть. Для исчезнувшего это
       база — единственное место, где на него ещё можно посмотреть. */
    const open=(i.head&&i.head.comparison_id)||(i.base&&i.base.comparison_id);
    if(open)hit(row,()=>{location.hash='#/compare/'+open;},
                `${i.name}, open the review`);
    else row.style.cursor='default';
    box.append(row);
  });
  return box;
}
