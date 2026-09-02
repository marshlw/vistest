/* VisTest - self-hosted visual regression testing.
 * Copyright (C) 2026 Kirill Kulagin
 * SPDX-License-Identifier: AGPL-3.0-or-later
 *
 * This file is part of VisTest. See LICENSE for the full terms and NOTICE for
 * the trademark and commercial-licensing terms. Removing this header does not
 * remove those obligations.
 */

/* Экран «Settings»: секреты, пороги, эталон на ветку, уведомления, ретеншен.

   Самый разнородный файл в наборе — но резать его дальше нечего: каждая панель
   здесь по сорок строк, и восемь файлов по сорок строк искать труднее, чем один
   раздел внутри одного. */
SCREENS.settings=async function(){
  loadingScreen();
  const here=pageGuard();
  /* Роль спрашивается той же функцией, что и везде, а не сравнением строк:
     `role==='admin'` не знает про права, выданные на один проект, и разошлось
     бы с остальным интерфейсом при первой же выдаче. */
  const admin=can('admin');
  if(!here())return;
  const screen=$('#screen');screen.innerHTML='';
  /* Страница в общей рамке. Без неё настройки шли во всю ширину окна и
     единственные из всех экранов начинались без полей — читалось как сбой
     вёрстки, а не как решение. */
  const s=el('div','page narrow');screen.append(s);
  s.innerHTML='<div class="eyebrow">SETUP</div><h1 class="h1">Settings</h1>';
  if(admin)await renderTeamAdmin(s);
  if(!admin){await renderSelfSettings(s);return;}
  let env,storage;
  try{env=await api('/api/env');}catch(e){s.append(errCard(e));return;}
  storage=await api('/api/storage').catch(()=>null);

  // secrets
  s.append(el('div','sect-h','<h2>Secrets</h2>'));
  const sc=el('div','set-card');
  sc.innerHTML='<div class="muted" style="font-size:13px;margin-bottom:14px">Logins and passwords for steps before the snapshot. Stored in <code>.vistest/secrets.env</code> — the file is in .gitignore and does not go outside. In steps write <code>${VISTEST_PASSWORD}</code>.</div>';
  const rows=el('div');rows.id='envRows';
  (env.vars||[]).forEach(v=>rows.append(envRow(v)));
  sc.append(rows);
  const save=el('button','btn dark','Save');
  save.onclick=()=>saveEnv();
  const add=el('button','btn','Add a variable');
  add.onclick=()=>rows.append(envRow({name:'',value:'',secret:false}));
  const ab=el('div');ab.style.cssText='display:flex;gap:10px;margin-top:6px';ab.append(save,add);sc.append(ab);
  if(env.warning)sc.append(el('div','info-note',esc(env.warning)));
  s.append(sc);

  // thresholds
  s.append(el('div','sect-h','<h2>Verdict thresholds</h2>'));
  const thHolder=el('div');thHolder.id='thresholdCard';s.append(thHolder);
  renderThresholds(thHolder);

  // branch baselines
  s.append(el('div','sect-h','<h2>Baselines per branch</h2>'));
  const brHolder=el('div');brHolder.id='branchCard';s.append(brHolder);
  renderBranches(brHolder);

  // notifications
  s.append(el('div','sect-h','<h2>Notifications</h2>'));
  const nfHolder=el('div');nfHolder.id='notifyCard';s.append(nfHolder);
  renderNotifications(nfHolder);

  // storage
  s.append(el('div','sect-h','<h2>Storage</h2>'));
  const stc=el('div','set-card');
  const gb=kb=>kb>=1048576?(kb/1048576).toFixed(1)+' GB':Math.round(kb/1024)+' MB';
  const st=storage||{};
  const sr=el('div','storage-row');
  sr.innerHTML=`<div><div class="v">${gb(st.artifacts_kb||0)}</div><div class="k">artifacts</div></div>
    <div><div class="v">${gb(st.baselines_kb||0)}</div><div class="k">baselines</div></div>
    <div><div class="v">${fmtInt(st.runs_in_db||0)}</div><div class="k">runs in the database</div></div>`;
  stc.append(sr);
  const cl=el('div');cl.style.cssText='display:flex;gap:14px;align-items:center;margin-top:16px';
  const clb=el('button','btn','Delete runs older than 30 days');clb.onclick=cleanupRuns;
  cl.append(clb,el('span','muted','the last 20 runs are kept regardless; baselines are not touched'));
  cl.querySelector('.muted').style.fontSize='12.5px';
  stc.append(cl);s.append(stc);

  // retention
  s.append(el('div','sect-h','<h2>Automatic cleanup</h2>'));
  const rtHolder=el('div');rtHolder.id='retentionCard';s.append(rtHolder);
  renderRetention(rtHolder);

  /* Оглавление ставится последним и в самое начало: собрать его можно только
     когда разделы уже на странице, а место ему — под заголовком, где его
     ищут. Панели, которые дозагружаются (`renderThresholds` и соседи),
     заголовки свои уже отдали — они добавлены выше синхронно. */
  const rail=sectionRail(s,{label:'Settings sections'});
  if(rail)s.querySelector('.h1').after(rail);
};
/* ------- ретеншен -------
   Единственное фоновое действие, которое УДАЛЯЕТ данные. Отсюда весь тон
   панели: выключено по умолчанию, «показать, что уйдёт» рядом с переключателем,
   и прямым текстом сказано, чего оно не тронет.

   Кнопка «показать, что уйдёт» здесь не удобство: включить автоудаление
   вслепую значит узнать, что оно означало, назавтра и по пустому списку. */
async function renderRetention(box){
  box.innerHTML='';
  const card=el('div','set-card');box.append(card);
  card.innerHTML='<div class="jobbar"><span class="spin"></span> loading…</div>';

  let cfg;
  try{cfg=await api('/api/settings/retention');}
  catch(e){card.innerHTML='';card.append(el('div','empty',esc(String(e.message||e))));return;}

  card.innerHTML='';
  const intro=el('div','muted');
  intro.style.cssText='font-size:13px;line-height:1.55';
  intro.innerHTML='A full-page diff is several megabytes of PNG, so the history '
    +'eats tens of gigabytes in a couple of months. The button above does the '
    +'same thing, but somebody has to remember to press it.<br>'
    +'<b>An unreviewed failure is never deleted</b> — not by the term, not by '
    +'the quota, not by the button. It is a question nobody has answered, and '
    +'deleting it means nobody ever will. Baselines are not touched at all.';
  card.append(intro);

  const row=el('div');
  row.style.cssText='display:flex;gap:14px;align-items:end;margin-top:16px;flex-wrap:wrap';

  const toggle=chk('Clean up automatically');
  toggle.input.checked=!!cfg.enabled;
  toggle.wrap.style.cssText='display:flex;align-items:center;gap:8px;cursor:pointer;'
    +'font-weight:600;padding-bottom:9px';

  const num=(value,width)=>{const i=el('input','inp');i.type='number';i.min='0';
    i.value=value;i.style.cssText='margin:0;width:'+width;return i;};
  const days=num(cfg.days!=null?cfg.days:60,'110px');
  const keep=num(cfg.keep_last!=null?cfg.keep_last:50,'110px');
  const quota=num(cfg.max_gb!=null?cfg.max_gb:0,'110px');

  const field=(label,input)=>{const w=el('div');
    w.append(el('label','field-lbl',label),input);return w;};
  const save=el('button','btn dark','Save');
  const dry=el('button','btn','Show what would go');
  row.append(toggle.wrap,field('Older than, days',days),
             field('Always keep last',keep),field('Disk limit, GB (0 = none)',quota),
             save,dry);
  card.append(row);

  const note=el('div','muted');
  note.style.cssText='font-size:12.5px;margin-top:10px;line-height:1.5';
  note.textContent='«Always keep last» counts per project, so a busy project '
    +'cannot push a quiet one out of protection. The disk limit answers a '
    +'different question than the term does — «we are running out of space» — '
    +'so it deletes the oldest regardless of age, and only as many as needed.';
  card.append(note);

  if(cfg.last_run){
    const done=el('div','muted','Last run: '+esc(cfg.last_run)
                  +(cfg.last_result?' · '+esc(cfg.last_result):''));
    done.style.cssText='font-size:12.5px;margin-top:8px';
    card.append(done);
  }

  const out=el('div');out.style.marginTop='12px';card.append(out);

  if(!can('admin')){
    [toggle.input,days,keep,quota,save,dry].forEach(x=>{x.disabled=true;});
    card.append(el('div','info-note','Only an administrator can change this.'));
    return;
  }

  const body=()=>JSON.stringify({enabled:toggle.input.checked,
                                 days:Number(days.value)||1,
                                 keep_last:Number(keep.value)||1,
                                 max_gb:Number(quota.value)||0});
  save.onclick=async()=>{
    save.disabled=true;const old=save.textContent;save.innerHTML='<span class="spin"></span>';
    try{
      await api('/api/settings/retention',{method:'PUT',
        headers:{'Content-Type':'application/json'},body:body()});
      toast('Saved','ok');renderRetention(box);
    }catch(e){toast(String(e.message||e),'err');save.disabled=false;save.textContent=old;}
  };
  dry.onclick=async()=>{
    dry.disabled=true;const old=dry.textContent;dry.innerHTML='<span class="spin"></span>';
    try{
      /* Сохраняем сначала: считать по числам, которых в базе ещё нет, значит
         показать список для прошлой политики и назвать его будущим. */
      await api('/api/settings/retention',{method:'PUT',
        headers:{'Content-Type':'application/json'},body:body()});
      const r=await api('/api/settings/retention/preview',{method:'POST'});
      out.innerHTML='';
      const line=el('div','info-note');
      /* Причина называется в обоих случаях. «Ничего не уйдёт» без объяснения
         читается как «политика не работает», и следующим действием человек
         выкручивает срок в единицу — а не уходило ничего потому, что все
         кандидаты держатся неразобранным падением. */
      const spared=r.kept_unreviewed
        ? ` ${r.kept_unreviewed} run(s) stay because a failure in them is still unreviewed.`
        : '';
      line.textContent=(r.deleted
        ? `${r.deleted} run(s) would be deleted.`
        : 'Nothing would be deleted with these settings.')+spared;
      out.append(line);
    }catch(e){toast(String(e.message||e),'err');}
    finally{dry.disabled=false;dry.textContent=old;}
  };
}

/* ------- уведомления -------
   Единственное, что уходит из сервиса наружу само. Всё остальное здесь
   работает через «человек придёт и посмотрит», и это верно ровно до первого
   дня, когда он не пришёл: двенадцать падений лежат третьи сутки, а никто не
   знает, что смотреть надо было позавчера.

   Кнопка «Отправить сейчас» здесь не украшение: без неё первая проверка
   вебхука случается через сутки, а опечатка в адресе выглядит ровно как
   «разбирать нечего» — то есть как тишина. */
async function renderNotifications(box){
  box.innerHTML='';
  const card=el('div','set-card');box.append(card);
  card.innerHTML='<div class="jobbar"><span class="spin"></span> loading…</div>';

  let cfg;
  try{cfg=await api('/api/settings/notifications');}
  catch(e){card.innerHTML='';card.append(el('div','empty',esc(String(e.message||e))));return;}

  card.innerHTML='';
  const intro=el('div','muted');
  intro.style.cssText='font-size:13px;line-height:1.55';
  intro.innerHTML='A digest of failures nobody has looked at goes to a webhook — '
    +'one URL, an ordinary POST, no tokens kept here. Slack and Mattermost take '
    +'the <code>text</code> field as is.<br>'
    +'It stays quiet when there is nothing to review, and quiet when the list has '
    +'not changed since last time: a channel that repeats itself daily is a '
    +'channel nobody opens on the day it matters.';
  card.append(intro);

  const row=el('div');
  row.style.cssText='display:flex;gap:14px;align-items:end;margin-top:16px;flex-wrap:wrap';

  const toggle=chk('Send a digest');
  toggle.input.checked=!!cfg.enabled;
  toggle.wrap.style.cssText='display:flex;align-items:center;gap:8px;cursor:pointer;'
    +'font-weight:600;padding-bottom:9px';

  const hook=el('input','inp');
  hook.value=cfg.webhook||'';
  hook.placeholder='https://chat.example/hooks/…';
  hook.style.cssText='margin:0;width:340px';
  const hookWrap=el('div');
  hookWrap.append(el('label','field-lbl','Webhook URL'),hook);

  const hours=el('input','inp');
  hours.type='number';hours.min='0';hours.step='1';
  hours.value=cfg.after_hours!=null?cfg.after_hours:24;
  hours.style.cssText='margin:0;width:110px';
  const hoursWrap=el('div');
  hoursWrap.append(el('label','field-lbl','Waiting longer than, h'),hours);

  const save=el('button','btn dark','Save');
  const test=el('button','btn','Send now');
  row.append(toggle.wrap,hookWrap,hoursWrap,save,test);
  card.append(row);

  const note=el('div','muted');
  note.style.cssText='font-size:12.5px;margin-top:10px;line-height:1.5';
  note.textContent='A failure reviewed the same day is ordinary work, not a debt '
    +'— that is what the threshold is for. One snapshot red across twenty runs '
    +'counts as one question, not twenty.';
  card.append(note);

  /* Сломавшийся вебхук — худший из отказов: канал тих, и тишина читается как
     «всё разобрано». Поэтому причина последней неудачи видна здесь. */
  if(cfg.last_error){
    const err=el('div','info-note');
    err.style.color='#9A5B12';
    err.textContent='The last attempt failed: '+cfg.last_error;
    card.append(err);
  }else if(cfg.last_sent){
    card.append(el('div','muted','Last sent: '+esc(cfg.last_sent)));
    card.lastChild.style.cssText='font-size:12.5px;margin-top:8px';
  }

  if(!can('admin')){
    toggle.input.disabled=true;hook.disabled=true;hours.disabled=true;
    save.disabled=true;test.disabled=true;
    card.append(el('div','info-note','Only an administrator can change this.'));
    return;
  }

  const body=()=>JSON.stringify({enabled:toggle.input.checked,
                                 webhook:hook.value.trim(),
                                 after_hours:Number(hours.value)||0});
  save.onclick=async()=>{
    save.disabled=true;const old=save.textContent;save.innerHTML='<span class="spin"></span>';
    try{
      await api('/api/settings/notifications',{method:'PUT',
        headers:{'Content-Type':'application/json'},body:body()});
      toast('Saved','ok');renderNotifications(box);
    }catch(e){toast(String(e.message||e),'err');save.disabled=false;save.textContent=old;}
  };
  test.onclick=async()=>{
    test.disabled=true;const old=test.textContent;test.innerHTML='<span class="spin"></span>';
    try{
      /* Сначала сохраняем: проверять адрес, которого в базе ещё нет, значит
         проверять предыдущий и показать зелёный ответ про чужой вебхук. */
      await api('/api/settings/notifications',{method:'PUT',
        headers:{'Content-Type':'application/json'},body:body()});
      const r=await api('/api/settings/notifications/test',{method:'POST'});
      if(r.sent)toast('Sent: '+r.count+' waiting','ok');
      else toast(r.error?('Webhook failed: '+r.error):(r.reason||'nothing sent'),
                 r.error?'err':undefined);
    }catch(e){toast(String(e.message||e),'err');}
    finally{test.disabled=false;test.textContent=old;renderNotifications(box);}
  };
}

/* ------- эталон на ветку -------
   Эталон был один на платформу, а веток у команды много: намеренный редизайн,
   принятый в ветке, ломал main, а обновлённый main ломал ветку. Режим
   выключен по умолчанию — команде с одной веткой он не даёт ничего, а вопросов
   добавляет («почему мой апрув не изменил эталон, который я вижу»). */
async function renderBranches(box){
  box.innerHTML='';
  const card=el('div','set-card');box.append(card);
  card.innerHTML='<div class="jobbar"><span class="spin"></span> loading…</div>';

  let cfg,list;
  try{
    cfg=await api('/api/settings/branches');
    list=await api('/api/branches');
  }catch(e){card.innerHTML='';card.append(el('div','empty',esc(String(e.message||e))));return;}

  card.innerHTML='';
  const intro=el('div','muted');
  intro.style.cssText='font-size:13px;line-height:1.55';
  intro.innerHTML='A branch does not get its own set — it gets an <b>overlay</b>: '
    +'only the snapshots it has redefined. Everything else is inherited from the '
    +'base branch. Accepting a failure on a branch therefore cannot change the '
    +'base baseline, which is the whole point.';
  card.append(intro);

  const row=el('div');
  row.style.cssText='display:flex;gap:14px;align-items:end;margin-top:16px;flex-wrap:wrap';

  const toggle=chk('Baselines per branch');
  toggle.input.checked=!!cfg.enabled;
  toggle.wrap.style.cssText='display:flex;align-items:center;gap:8px;cursor:pointer;'
    +'font-weight:600;padding-bottom:9px';

  const base=el('input','inp');
  base.value=cfg.default_branch||'main';
  base.style.cssText='margin:0;width:200px';
  const baseWrap=el('div');
  baseWrap.append(el('label','field-lbl','Base branch'),base);

  const save=el('button','btn dark','Save');
  row.append(toggle.wrap,baseWrap,save);
  card.append(row);

  const note=el('div','muted');
  note.style.cssText='font-size:12.5px;margin-top:10px;line-height:1.5';
  note.textContent='A run without a branch — a baseline captured by URL from the '
    +'interface, for instance — counts as the base one: giving it an overlay of '
    +'its own would split a set the person sees as one.';
  card.append(note);

  if(!can('admin')){
    toggle.input.disabled=true;base.disabled=true;save.disabled=true;
    card.append(el('div','info-note','Only an administrator can change this.'));
  }else{
    save.onclick=async()=>{
      save.disabled=true;const old=save.textContent;save.innerHTML='<span class="spin"></span>';
      try{
        await api('/api/settings/branches',{method:'PUT',
          headers:{'Content-Type':'application/json'},
          body:JSON.stringify({enabled:toggle.input.checked,
                               default_branch:base.value.trim()||'main'})});
        toast('Saved · applies to the next run','ok');
        renderBranches(box);
      }catch(e){toast(String(e.message||e),'err');save.disabled=false;save.textContent=old;}
    };
  }

  const branches=list.branches||[];
  if(!cfg.enabled&&!branches.length)return;

  const tbl=el('div');tbl.style.marginTop='20px';
  tbl.append(el('div','field-lbl','Branches with baselines of their own'));
  if(!branches.length){
    tbl.append(el('div','muted','None yet — every branch is still fully '
      +'inherited from '+esc(cfg.default_branch||'main')+'.'));
    card.append(tbl);return;
  }

  branches.forEach(b=>{
    const r=el('div');
    r.style.cssText='display:flex;gap:12px;align-items:center;padding:11px 0;'
      +'border-bottom:1px solid var(--line3)';
    const info=el('div');info.style.flex='1';
    info.innerHTML=`<div class="mono" style="font-weight:600">${esc(b.branch)}</div>
      <div class="faint" style="font-size:12px">${b.snapshots} own snapshot${b.snapshots===1?'':'s'}
      ${b.updated_at?'· '+esc(String(b.updated_at).replace('T',' ').slice(0,16)):''}</div>`;
    r.append(info);

    /* «Влить» и «выбросить» — противоположные решения, и путать их нельзя,
       поэтому это две кнопки с разными вопросами, а не одна с выбором. */
    const merge=roleButton('admin','btn sm','Promote to '+(cfg.default_branch||'main'),
      async()=>{
        if(!confirm(`Promote ${b.snapshots} baseline(s) from «${b.branch}» into `
          +`«${cfg.default_branch||'main'}»?\n\nThis is what you do after the branch `
          +`is merged. The previous pictures go into history — a rollback stays possible.`))return;
        try{
          const res=await api('/api/branches/promote',{method:'POST',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify({branch:b.branch,delete_after:true})});
          toast(`Promoted ${(res.promoted||[]).length} baseline(s)`,'ok');
          renderBranches(box);
        }catch(e){toast(String(e.message||e),'err');}
      },'Copies the branch pictures into the base set');
    if(merge)r.append(merge);

    const drop=roleButton('admin','btn sm danger','Discard',async()=>{
      if(!confirm(`Discard the overlay of «${b.branch}»?\n\nIts ${b.snapshots} `
        +`baseline(s) are deleted. The base set is not touched — but the decisions `
        +`made on this branch are lost.`))return;
      try{
        await api('/api/branches/'+b.branch.split('/').map(encodeURIComponent).join('/'),
                  {method:'DELETE'});
        toast('Overlay discarded','ok');renderBranches(box);
      }catch(e){toast(String(e.message||e),'err');}
    },'Deletes the branch overlay, leaving the base set alone');
    if(drop)r.append(drop);

    tbl.append(r);
  });
  card.append(tbl);
}

/* ------- пороги вердикта -------
   Здесь стоял ползунок, который не отправлял никуда ни одного запроса: он
   двигался, показывал число, и на этом всё заканчивалось — значение умирало
   при следующей перерисовке экрана. Начальное значение (35) даже не совпадало
   с настоящим дефолтом (25), так что показанное число было неверным ещё до
   того, как его трогали.

   Теперь настройка настоящая. И вместе со значением экран обязан говорить, ОТКУДА
   оно взялось: «35» без ответа на «почему 35» — то же непроверяемое обещание,
   что и «принять как эталон» без указания, какой именно эталон. */
const THRESHOLD_LABEL={
  fail_severity:'Failure threshold',
  max_changed_area_pct:'Changed area, %',
};
const SOURCE_LABEL={
  config:'from vistest.yaml',
  global:'set here, for everyone',
  project:'set here, for this project',
};

async function renderThresholds(box,project){
  box.innerHTML='';
  const card=el('div','set-card');box.append(card);
  card.innerHTML='<div class="jobbar"><span class="spin"></span> loading…</div>';

  let data;
  try{data=await api('/api/settings/thresholds'
    +(project?'?project='+encodeURIComponent(project):''));}
  catch(e){card.innerHTML='';card.append(el('div','empty',esc(String(e.message||e))));return;}

  card.innerHTML='';
  card.append(el('div','muted',
    'Where the line between «a difference» and «a failure» runs. '
    +'The severity histogram on the Metrics tab is the picture that answers '
    +'where to put it: a gap between two clusters is where the threshold belongs.'));
  card.querySelector('.muted').style.cssText='font-size:13px;line-height:1.5';

  /* Переключатель «для всех / для проекта». Порог на проект нужен ровно
     потому, что шумят проекты по-разному, и один общий порог всегда чей-то
     компромисс. */
  const scopeRow=el('div');
  scopeRow.style.cssText='display:flex;gap:8px;align-items:center;margin-top:14px;flex-wrap:wrap';
  scopeRow.append(el('span','field-lbl','Applies to'));
  const pick=el('select','inp');
  /* Ширина по содержимому — да, отступы — нет: справа в поле нарисована
     стрелка, и `padding:6px 10px` заезжал текстом прямо под неё. Отступы у
     `select.inp` заданы в `ui.css` и учитывают её. */
  pick.style.cssText='margin:0;width:auto';
  pick.id='thresholdScope';
  tip(pick,'A per-project threshold overrides the shared one. Projects are noisy in '
    +'different ways, and one number for all of them is always somebody’s compromise.');
  const opts=[['','everyone']].concat((state.projectKeys||[]).map(k=>[k,k]));
  opts.forEach(([v,label])=>{const o=el('option');o.value=v;o.textContent=label;pick.append(o);});
  pick.value=project||'';
  pick.onchange=()=>renderThresholds(box,pick.value||null);
  scopeRow.append(pick);
  card.append(scopeRow);

  const edit=!!can('admin');
  const draft={};

  Object.keys(data.editable||{}).forEach(name=>{
    const spec=data.editable[name];
    const value=(data.values||{})[name];
    const source=(data.sources||{})[name];
    const row=el('div');row.style.marginTop='22px';

    const head=el('div');
    head.style.cssText='display:flex;align-items:baseline;gap:10px;flex-wrap:wrap';
    const num=el('div');
    num.style.cssText='font-family:var(--mono);font-size:26px;font-weight:700';
    num.textContent=`${Number(value)}${spec.unit||''}`;
    const lbl=el('div');lbl.textContent=THRESHOLD_LABEL[name]||name;
    lbl.style.cssText='font-weight:600';
    const src=el('span','tag '+(source==='config'?'':'indigo'));
    src.textContent=SOURCE_LABEL[source]||source;
    // Проектный порог перекрывает общий — говорим это прямо, иначе человек
    // меняет общий и не понимает, почему для его проекта ничего не изменилось.
    if(source==='project')src.title='Overrides the value set for everyone';
    head.append(num,lbl,src);
    row.append(head);

    const hint=el('div','muted');hint.textContent=spec.hint||'';
    hint.style.cssText='font-size:12.5px;margin-top:4px;line-height:1.5';
    row.append(hint);

    if(edit){
      const sl=el('input','slider');sl.type='range';
      sl.min=spec.min;sl.max=spec.max;
      sl.step=name==='max_changed_area_pct'?0.01:1;
      sl.value=value;
      const paint=v=>{
        const pctFill=(v-spec.min)/((spec.max-spec.min)||1)*100;
        sl.style.setProperty('--fillpct',pctFill+'%');
        num.textContent=`${v}${spec.unit||''}`;
      };
      paint(Number(value));
      sl.oninput=()=>{draft[name]=Number(sl.value);paint(Number(sl.value));};
      row.append(sl);
      const xr=el('div','slider-x');
      xr.innerHTML=`<span>${spec.min}${esc(spec.unit||'')}</span>`
        +`<span>${spec.max}${esc(spec.unit||'')}</span>`;
      row.append(xr);
    }
    card.append(row);
  });

  if(!edit){
    card.append(el('div','info-note',
      'Only an administrator can change these: the threshold is shared, and a '
      +'quietly shifted one changes the verdicts of everyone else’s runs.'));
    return;
  }

  const acts=el('div');acts.style.cssText='display:flex;gap:10px;margin-top:22px;align-items:center;flex-wrap:wrap';
  const save=el('button','btn dark','Save');
  save.onclick=async()=>{
    if(!Object.keys(draft).length)return toast('Nothing changed');
    save.disabled=true;const old=save.textContent;save.innerHTML='<span class="spin"></span>';
    try{
      await api('/api/settings/thresholds',{method:'PUT',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({project:project||undefined,values:draft})});
      toast('Thresholds saved · they apply to the next run','ok');
      renderThresholds(box,project);
    }catch(e){toast(String(e.message||e),'err');save.disabled=false;save.textContent=old;}
  };
  acts.append(save);

  /* «Вернуть как в конфиге» — это не «поставить ноль». Ноль здесь осмысленное
     значение («падать на любом видимом различии»), и без отдельной кнопки
     вернуться к тому, что написано в vistest.yaml, было бы нельзя. */
  /* `data.sources` спрашивается через `||{}`, как и `data.editable` двадцатью
     строками выше. Без этого ответ старого сервиса, в котором поля ещё нет,
     не «показывал на одну кнопку меньше», а валил `Object.values(undefined)` —
     то есть уносил с собой весь экран настроек целиком, вместе с командой,
     заявками на доступ и сроком хранения. */
  const anyOverride=Object.values(data.sources||{}).some(x=>x!=='config');
  if(anyOverride){
    const reset=el('button','btn','Back to vistest.yaml');
    reset.title='Removes the override; the value from the config applies again';
    reset.onclick=async()=>{
      const cleared={};Object.keys(data.editable).forEach(n=>{cleared[n]=null;});
      try{
        await api('/api/settings/thresholds',{method:'PUT',
          headers:{'Content-Type':'application/json'},
          body:JSON.stringify({project:project||undefined,values:cleared})});
        toast('Overrides removed','ok');renderThresholds(box,project);
      }catch(e){toast(String(e.message||e),'err');}
    };
    acts.append(reset);
  }
  acts.append(el('span','muted','applies to the next run, not to history'));
  acts.querySelector('.muted').style.fontSize='12.5px';
  card.append(acts);
}

function envRow(v){
  const r=el('div','env-edit');
  const n=el('input','inp');n.value=v.name||'';n.placeholder='VISTEST_USER';n.style.margin='0';n.dataset.k='name';
  const val=el('input','inp');val.value=v.secret?'':(v.value||'');val.placeholder=v.secret?'••••••••':'value';val.style.margin='0';val.dataset.k='value';val.dataset.secret=v.secret?'1':'';
  if(v.secret)val.type='password';
  const del=el('button','btn sm','Delete');del.onclick=()=>r.remove();
  r.append(n,val,del);return r;
}
async function saveEnv(){
  const vars=[];
  $$('#envRows .env-edit').forEach(r=>{
    const name=$('[data-k=name]',r).value.trim();const valEl=$('[data-k=value]',r);
    if(!name)return;
    if(valEl.dataset.secret&&valEl.value==='')return vars.push({name,keep:true});// unchanged secret
    vars.push({name,value:valEl.value});
  });
  try{await api('/api/env',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({vars})});toast('Saved','ok');SCREENS.settings();}
  catch(e){toast(String(e.message||e),'err');}
}

