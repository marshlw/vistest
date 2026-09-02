/* Совместная работа: кто сейчас смотрит, захват разбора, живые отметки. */
let presenceTimer=null,liveTimer=null,claimTimer=null;
function startCollab(){startPresence();startLive();}
function startPresence(){if(presenceTimer)clearInterval(presenceTimer);presenceBeat();presenceTimer=setInterval(presenceBeat,20000);}
async function presenceBeat(){
  if(!state.me)return;
  try{const p=await api('/api/presence',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({viewing:location.hash||'#/decisions'})});paintPresence(p);}catch{}
}
function viewLabel(h){
  if(!h)return '';
  if(h.indexOf('#/compare')===0)return 'snapshot review';
  const key=h.split('/').slice(0,2).join('/');
  return {'#/decisions':'decisions','#/runs':'runs','#/baselines':'baselines',
          '#/tests':'tests','#/dash':'trust','#/projects':'projects',
          '#/doctor':'environment','#/settings':'settings','#/diff':'what changed'}[key]||'';
}
function paintPresence(p){
  const chip=$('#onlineChip');if(!chip)return;state.presence=p;
  const n=p.count||0;
  if(n<=0){chip.style.display='none';return;}
  chip.style.display='flex';chip.innerHTML='<span class="dot"></span>online: '+n;
  chip.title=(p.online||[]).map(o=>{const v=viewLabel(o.viewing);return (o.name||o.login)+(v?' · '+v:'');}).join('\n');
}
function startLive(){if(liveTimer)clearInterval(liveTimer);liveTimer=setInterval(liveTick,12000);}
/* Живое обновление касается СПИСКА прогонов и только его.

   `state.view` — первый сегмент адреса, и у `#/runs/42` он тоже `runs`. На
   странице прогона строк `.run-item` нет ни одной, обновление считало это
   расхождением со свежими данными и звало `SCREENS.runs()` без аргумента —
   то есть рисовало список поверх открытого прогона. Раз в двенадцать секунд,
   без единого действия человека: сидишь в разборе, и тебя выбрасывает
   обратно, и понять, почему это происходит «иногда», невозможно — а это
   просто таймер.

   Правило поэтому не про содержимое экрана, а про адрес: обновляем то, что
   открыто, и никогда не подменяем открытое чем-то другим. */
function liveShouldRepaintRuns(view,hash){
  return view==='runs'&&!/^#\/runs\/.+/.test(String(hash||''));
}
async function liveTick(){
  if(!state.me)return;
  /* В скрытой вкладке обновлять нечего: экрана никто не видит, а запросы идут
     раз в двенадцать секунд весь день. */
  if(document.hidden)return;
  refreshCounts();
  if(liveShouldRepaintRuns(state.view,location.hash)){
    try{
      /* Два расхождения с отрисовкой, и оба приводили к одному: список
         перерисовывался каждые двенадцать секунд без всякой причины, теряя
         прокрутку и открытый прогон.

         Первое — лимит стоял жёстко 60, а экран рисуется по `runLimit`.
         Второе, и оно постояннее: сравнивались НЕОТФИЛЬТРОВАННЫЕ свежие
         идентификаторы с отфильтрованными нарисованными. Стоило включить
         любой фильтр, кроме «All», — наборы переставали совпадать навсегда. */
      const runs=await api('/api/runs?limit='+(state.runLimit||60)
                           +'&project='+encodeURIComponent(state.project));
      const cur=[...document.querySelectorAll('#screen .run-item')].map(x=>x.dataset.id).join(',');
      const fresh=runs.filter(r=>matchRunFilter(r,state.runFilter||'all'))
                      .map(r=>String(r.id)).join(',');
      if(cur!==fresh){SCREENS.runs();return;}
      runs.forEach(r=>{
        const it=document.querySelector('#screen .run-item[data-id="'+r.id+'"]');if(!it)return;
        const st=it.querySelector('.tag');if(!st)return;
        // Общая функция, а не вторая копия правил: копия не знала про
        // `errored`, и прогон с ошибками через двенадцать секунд «выздоравливал».
        const s=runStatus(r);st.className=s.cls;st.textContent=s.text;
      });
    }catch{}
  }
}
/* claim «I am reviewing this snapshot» */
async function takeClaim(id){
  if(claimTimer){clearInterval(claimTimer);claimTimer=null;}
  if(state.claimComp&&state.claimComp!=id)releaseClaim(state.claimComp);
  state.claimComp=id;
  state.claim=await api('/api/comparisons/'+id+'/claim',{method:'POST'}).catch(()=>null);
  claimTimer=setInterval(()=>{
    api('/api/comparisons/'+id+'/claim',{method:'POST'}).then(r=>{state.claim=r;paintClaimBanner();}).catch(()=>{});
  },45000);
}
function releaseClaim(id){if(id)api('/api/comparisons/'+id+'/claim',{method:'DELETE'}).catch(()=>{});}
function claimLeftCheck(){
  if(state.claimComp&&location.hash.indexOf('#/compare/'+state.claimComp)!==0){
    releaseClaim(state.claimComp);state.claimComp=null;
    if(claimTimer){clearInterval(claimTimer);claimTimer=null;}
  }
}
function buildClaimBanner(){
  const c=state.claim;if(!c)return el('div');
  if(c.mine){const d=el('div','claim-banner mine');d.textContent='you hold this snapshot';return d;}
  const d=el('div','claim-banner');
  /* Вся фраза — один элемент. Раньше имя лежало в `<span>`, а остаток был
     голым текстом рядом: во флекс-контейнере такой текст становится
     отдельным элементом, получает свой `gap` в семь пикселей и складывает его
     с обычным пробелом — между именем и словом «is» зияла дыра, похожая на
     непроставленное значение. */
  d.innerHTML='<span><b class="who">'+esc(c.by||'A colleague')+'</b> is already '
    +'reviewing this snapshot. You can continue, but it is better to coordinate.</span>';
  tip(d,'Somebody opened this snapshot for review less than a minute ago. Two people '
    +'answering the same failure is how one answer quietly overwrites the other.');
  return d;
}
function paintClaimBanner(){const h=$('#claimBanner');if(!h)return;h.innerHTML='';h.append(buildClaimBanner());}

