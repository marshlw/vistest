/* Экран «Decisions»: не «что красное», а «на что ответить».

   Экран прогона отвечает на первый вопрос. Человек, открывающий VisTest утром,
   задаёт второй, и это не список из двадцати трёх картинок.

   Здесь очередь строится по проекту и по ПРИЧИНАМ: одна правка line-height —
   один вопрос, а не одиннадцать. Ответ на причину закрывает все снимки за ней
   одним запросом, и это единственный способ не приучить человека принимать не
   глядя. Вся арифметика на бэкенде (`api/decisions.py`): считать её здесь
   значило бы завести второе место, где сумма причин может разойтись с числом
   красных. */

SCREENS.decisions = async function(){
  loadingScreen();
  const here=pageGuard();
  /* Экрана «выберите проект» здесь больше нет, и это не упрощение, а следствие.

     Очередь строится для ОДНОГО набора: причина объединяет снимки одного
     проекта, и общее решение для двух чужих друг другу наборов бессмысленно
     (`/api/decisions` отвечает на `project=*` четырёхсотым — намеренно).
     Пока в шапке жило «all projects», этот экран был единственным, кто с ним
     не мирился, и вынужден был спрашивать сам — то есть задавать вопрос,
     который уже задан в шапке, и отвечать на него в обход неё.

     Теперь проект закреплён до первой отрисовки, и спрашивать нечего. */
  let d;
  try{d=await api('/api/decisions?project='+encodeURIComponent(state.project));}
  catch(e){return errScreen(e);}
  if(!here())return;
  state.queue=d;

  const s=$('#screen');s.innerHTML='';
  const page=el('div','page mid');s.append(page);

  const c=d.counts||{};
  const head=el('div','head');
  const left=el('div','grow');
  left.innerHTML=`<div class="eyebrow">QUEUE · ${esc(d.project||'')}</div>
    <h1 class="h1 big">${esc(queueTitle(c))}</h1>
    <div class="lede">${queueLede(c)}</div>`;
  head.append(left);

  if(c.causes){
    const go=el('button','btn accent');
    go.innerHTML='Start triage <span class="kbd">↵</span>';
    go.onclick=()=>startTriage(d);
    head.append(go);
  }
  page.append(head);

  /* Полоса чисел. «Чисто 117 из 142» стоит рядом с «3 решения» намеренно:
     без знаменателя три решения читаются как катастрофа или как мелочь в
     зависимости от настроения, а не от размера набора. */
  const strip=el('div','strip');
  strip.style.gridTemplateColumns='repeat(4,1fr)';
  strip.style.marginTop='22px';
  strip.innerHTML=`
    <div class="cell" data-tip="Distinct causes, not snapshots. Snapshots are grouped by a shared root cause — same selector, same kind of change — because answering the group is the only way to review dozens of failures without learning to approve blindly.">
      <div class="k">TO DECIDE</div>
      <div class="v accent">${fmtInt(c.causes||0)}</div>
      <div class="d">${plural(c.causes||0,'cause','causes','causes')} · ${fmtInt(c.snapshots||0)} snapshots</div></div>
    <div class="cell" data-tip="The picture was never taken, so there is nothing to compare. A snapshot that stopped being checked shows up in no list of failures — which is why it is counted here instead.">
      <div class="k">BLOCKED</div>
      <div class="v warn">${fmtInt(c.blocked||0)}</div>
      <div class="d">never captured — errors</div></div>
    <div class="cell" data-tip="Compared and identical. The denominator matters: three decisions out of a set of five is not three out of a hundred and forty-two.">
      <div class="k">CLEAN</div>
      <div class="v pass">${fmtInt(c.clean||0)}</div>
      <div class="d">of ${fmtInt(c.total||0)} snapshots</div></div>
    <div class="cell" data-tip="The newest run of this project — the one the queue above is built from.">
      <div class="k">RUN</div>
      <div class="v" style="font-size:17px">${d.run?esc(String(d.run.key)):'—'}</div>
      <div class="d">${d.run?esc(runWhen(d.run)):'no runs yet'}</div></div>`;
  page.append(strip);

  if(!c.causes&&!c.blocked){
    page.append(el('div','panel',
      '<div class="empty"><b>Nothing is waiting for a decision.</b>'
      +'<div class="faint" style="margin-top:8px;font-size:12.5px">Everything that failed has an answer. '
      +'New failures land here on their own.</div></div>'));
    page.querySelector('.strip').style.marginBottom='18px';
  }

  if(c.causes){
    page.append(sectionHead('Causes','most severe first'));
    (d.causes||[]).forEach(cause=>page.append(causeCard(cause,d)));
  }

  if(c.blocked){
    page.append(sectionHead('Blocked','no picture was taken — nothing to compare'));
    const box=el('div','panel');
    (d.blocked||[]).forEach(b=>box.append(blockedRow(b)));
    page.append(box);
  }

  /* Enter открывает триаж — то же, что «Start triage». Клавиша названа прямо
     на кнопке: горячая клавиша, о которой знает только автор, не существует. */
  state.queueKeys=e=>{
    if(e.key!=='Enter')return;
    if(/^(INPUT|TEXTAREA)$/.test((e.target||{}).tagName||''))return;
    if(state.view!=='decisions')return;
    if((state.queue&&state.queue.counts.causes)||0)startTriage(state.queue);
  };
  document.removeEventListener('keydown',state.queueKeysBound||(()=>{}));
  state.queueKeysBound=state.queueKeys;
  document.addEventListener('keydown',state.queueKeysBound);
};

function queueTitle(c){
  const n=c.causes||0;
  if(!n&&!(c.blocked||0))return 'Nothing is waiting for you';
  if(!n)return `${c.blocked} ${plural(c.blocked,'snapshot','snapshots','snapshots')} were never captured`;
  return `${n} ${plural(n,'decision is','decisions are','decisions are')} waiting for you`;
}
function queueLede(c){
  if(!(c.causes||0))
    return c.blocked
      ? 'Nothing to compare on them: the picture was never taken. A snapshot that '
        +'stopped being checked shows up in no list of failures.'
      : 'Every failure has an answer. This is the honest kind of empty.';
  return `${fmtInt(c.snapshots)} failed ${plural(c.snapshots,'snapshot','snapshots','snapshots')}, but only `
    +`<b>${c.causes}</b> distinct ${plural(c.causes,'cause','causes','causes')}. `
    +'Answer a cause once and every snapshot behind it closes with it.';
}
function runWhen(r){
  const dur=fmtDur(r.started_at,r.finished_at);
  return (dur!=='—'?dur+' · ':'')+timeAgo(r.started_at);
}

/* Ступени severity — те же три, что и везде в интерфейсе. Заводить здесь
   собственные пороги значило бы, что «красное» на одном экране и «красное» на
   соседнем означают разное. */
function sevClass(v){return v>=55?'sev-hi':v>=32?'sev-mid':'sev-lo';}

function causeCard(c,d){
  const box=el('div','cause');

  const sev=el('div','sev '+sevClass(c.severity||0));
  tip(sev,'Severity blends how much of the page moved with how visible the change is. '
    +'The number below it is how many snapshots share this cause — answering once '
    +'closes all of them.');
  sev.innerHTML=`<div class="n">${Math.round(c.severity||0)}</div>
    <div class="u">SEV</div><div class="x">×${c.count}</div>`;

  const body=el('div','body');
  body.innerHTML=`<div class="ttl"><span class="tag ${tagTone(c)}">${esc(c.tag||'')}</span>
      <h3>${esc(c.title||'')}</h3></div>
    ${c.selector?`<div class="sel">${esc(c.selector)}</div>`:''}
    <div class="why">${esc(c.why||'')}</div>
    <div class="chips">${(c.chips||[]).map(x=>`<span class="chip">${esc(x)}</span>`).join('')}</div>`;

  const ans=el('div','ans');
  ans.append(el('div','k',`ANSWER ONCE · ${c.count} ${plural(c.count,'SNAPSHOT','SNAPSHOTS','SNAPSHOTS')}`));

  /* Роль спрашивается вместе с проектом очереди: она может быть выдана на один
     набор. Показать кнопки всем и ответить 403 по нажатию — это не строгий
     бэкенд, это неправда на экране, да ещё и на одиннадцати снимках сразу. */
  if(!can('reviewer',d&&d.project)){
    const look=el('button','btn sm','Inspect');
    look.onclick=()=>{if(c.open)location.hash='#/compare/'+c.open;};
    ans.append(look,el('div','risk','Answering needs the reviewer role'
      +((d&&d.project)?' in project «'+esc(d.project)+'»':'')
      +'. An administrator grants it on the «Team» tab.'));
    box.append(sev,body,ans);
    return box;
  }

  const ok=el('button','btn dark','Accept as the new normal');
  tip(ok,`Rewrites ${c.count} baseline${c.count===1?'':'s'} on this branch. The next run `
    +'compares against the new picture, and none of these snapshots comes back.');
  ok.onclick=()=>answerCause(c,'approve',ok);
  const bug=el('button','btn danger','Confirm as a bug');
  tip(bug,'Records this as a real regression. Baselines stay as they are, so the '
    +'snapshots keep failing until somebody fixes the page.');
  bug.onclick=()=>answerCause(c,'reject',bug);

  const two=el('div','two');
  const look=el('button','btn sm','Inspect');
  tip(look,'Opens one snapshot of this cause side by side, with the regions the engine '
    +'found. Nothing is written from there either.');
  look.onclick=()=>{if(c.open)location.hash='#/compare/'+c.open;};
  const defer=el('button','btn sm','Defer');
  tip(defer,'Leaves it in the queue and moves on. Nothing is written.');
  defer.onclick=()=>toast('Left in the queue');
  two.append(look,defer);

  ans.append(ok,bug,two,el('div','risk',esc(c.risk||'')));
  box.append(sev,body,ans);
  return box;
}
function tagTone(c){
  const v=c.severity||0;
  return v>=55?'red':v>=32?'amber':'ink';
}


function blockedRow(b){
  const row=el('div','rows');row.className='';
  const d=el('div');
  d.style.cssText='display:grid;grid-template-columns:220px 1fr auto;gap:14px;'
    +'align-items:center;padding:11px 15px;border-bottom:1px solid var(--line3)';
  d.innerHTML=`<div class="mono" style="font-size:12px;font-weight:500">${esc(b.name)}</div>
    <div class="mono" style="font-size:11.5px;color:var(--warn)">${esc(b.error)}</div>`;
  /* «Повторить» ведёт тем же путём, которым снимок и снимался, — через
     `/api/baselines/run`. Кнопка, которая просто грузит адрес, для страницы за
     входом снимет форму логина и назовёт это результатом. */
  const again=el('button','btn sm','Retry');
  again.onclick=()=>retryBlocked(b,again);
  d.append(again);
  return d;
}
async function retryBlocked(b,btn){
  btn.disabled=true;const old=btn.textContent;btn.innerHTML='<span class="spin"></span>';
  try{
    await runJob(api('/api/baselines/run',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({name:b.name,platform:b.platform,
        scope:b.project_key?('project:'+b.project_key):undefined})}),
      {title:'Check '+b.name});
    SCREENS.decisions();refreshCounts();
  }catch(e){toast(String(e.message||e),'err');}
  finally{btn.disabled=false;btn.textContent=old;}
}

/* Триаж — это тот же разбор сравнения, но с очередью под рукой: человек не
   возвращается в список после каждого ответа. Очередь кладётся в state, чтобы
   экран сравнения знал, что за снимком идёт следующий. */
function startTriage(d){
  const first=(d.causes||[])[0];
  if(!first||!first.open)return toast('Nothing to open');
  state.triage={causes:d.causes,at:0,ids:triageIds(d.causes)};
  location.hash='#/compare/'+first.open;
}
function triageIds(causes){
  const out=[];
  (causes||[]).forEach(c=>(c.comparisons||[]).forEach(id=>{
    if(!out.includes(id))out.push(id);}));
  return out;
}

