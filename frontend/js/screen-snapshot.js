/* Страница одного снимка: эталон, ignore-зоны мышью, спека целиком, история. */
function parseSnapshotArg(arg){
  const parts=String(arg||'').split('/');
  if(parts.length<3)return null;
  return {scope:decodeURIComponent(parts[0]),
          platform:decodeURIComponent(parts[1]),
          name:decodeURIComponent(parts.slice(2).join('/'))};
}

SCREENS.snapshot=async function(arg){
  const ref=parseSnapshotArg(arg);
  if(!ref)return errScreen(new Error('No snapshot specified'));
  state.snapRef=ref;
  loadingScreen();
  const here=pageGuard();
  const q='platform='+encodeURIComponent(ref.platform)
    +'&name='+encodeURIComponent(ref.name)
    +(ref.scope&&ref.scope!=='global'?'&scope='+encodeURIComponent(ref.scope):'');
  let d;
  try{d=await api('/api/baselines/card?'+q);}
  catch(e){
    /* Эталона под этим именем в этом хранилище нет — это состояние, а не сбой:
       снимок могли удалить, или прогон был первым и записал `new_baseline` в
       другой набор. Голая «Could not load» тут ничего не объясняет. */
    if(!here())return;
    if(/not found/i.test(String(e.message||e)))return snapshotMissing(ref);
    return errScreen(e);
  }
  if(!here())return;
  state.snap=d;
  renderSnapshot(d);
};

/* «Назад к эталонам» — в тот самый набор и на тот самый вариант.

   Раньше сюда писалось `state.scope` напрямую, и это разъезжалось с шапкой:
   снимок открыт из набора подключённого проекта, а закреплён другой — возврат
   уводил на экран, где этого снимка нет. Набор теперь следует за проектом,
   поэтому и здесь ставится не он, а переопределитель — и только когда набор
   действительно чужой закреплённому. Платформа раскладывается на движок и
   размер окна: экран эталонов выбирает их по отдельности. */
function backToBaselines(ref){
  if(!ref)return void(location.hash='#/baselines');
  const p=currentProject();
  if(ref.scope&&(!p||ref.scope!==p.scope))setScopeOverride(ref.scope);
  if(ref.platform){
    state.platform=ref.platform;
    const pp=parsePlatform(ref.platform);
    state.browser=pp.browser||null;
    state.viewport=pp.viewport;
  }
  location.hash='#/baselines';
}

function snapshotMissing(ref){
  const screen=$('#screen');screen.innerHTML='';
  const s=el('div','page');screen.append(s);
  const card=el('div','panel pad');card.style.marginTop='18px';
  card.innerHTML=`<h3 class="h2">No baseline under this name</h3>
    <p class="lede">
      <span class="mono">${esc(ref.name)}</span> has no baseline in this store
      (<span class="mono">${esc(ref.scope)}</span>, ${esc(ref.platform)}).
      Either it was deleted, or the run that mentions it captured the picture
      into a different set — a comparison remembers the name, not the file.</p>`;
  const go=el('button','btn dark');go.textContent='To the baselines';
  go.style.marginTop='16px';
  go.onclick=()=>backToBaselines(ref);
  card.append(go);s.append(card);
}

function renderSnapshot(d){
  /* Карточка без `baseline` роняла экран на `d.baseline.version` — тем же
     способом, что и `applyTeam()` роняла весь интерфейс на узле, которого нет
     в разметке: обращение к полю ответа, про которое считается, что оно есть
     всегда. Ответ приходит из сети, «всегда» тут не бывает, а цена — пустой
     экран вместо снимка. */
  d=Object.assign({spec:{},baseline:{},source:{}},d||{});
  d.baseline=d.baseline||{};d.spec=d.spec||{};
  const screen=$('#screen');screen.innerHTML='';
  const s=el('div','page');screen.append(s);
  const ref=state.snapRef;

  const back=el('div','back','‹ BASELINES');back.style.cursor='pointer';
  back.onclick=()=>backToBaselines(ref);
  s.append(back);

  const head=el('div','head');head.style.marginTop='10px';
  head.innerHTML=`<div class="grow">
      <h1 class="h1 mono">${esc(d.short||d.name)}</h1>
      <div class="mono" style="font-size:11.5px;color:var(--muted);margin-top:6px">
        ${esc(d.scope_label||d.scope)} · ${esc(d.platform)} · v${d.baseline.version||1}
        ${d.baseline.approved_by?'accepted by '+esc(d.baseline.approved_by):''}</div>
      <div class="mono" id="snapSource" style="font-size:11.5px;color:var(--muted);margin-top:4px"></div>
    </div>`;
  const acts=el('div','acts');acts.style.justifyContent='flex-end';

  /* Имя теста — ссылка, а не подпись. Назвать тест и не дать его открыть значит
     дать половину ответа: посмотреть, что он делает, человек всё равно пойдёт. */
  const srcBox=head.querySelector('#snapSource');
  if(d.source&&d.source.runnable&&d.source.test){
    srcBox.append(document.createTextNode('captured by '));
    const link=el('a','mono',esc(d.source.test));
    link.href='#/tests/'+encodeURIComponent(d.source.file||d.source.test);
    link.title='Open the test';
    srcBox.append(link);
    if(d.source.project_key)srcBox.append(
      document.createTextNode(' · '+d.source.project_key));
  }else{
    srcBox.textContent=d.spec.url?'captured by URL':'source not recorded';
  }

  const viaTest=!!(d.source&&d.source.runnable);
  const scopeArg=d.scope==='global'?undefined:d.scope;
  const fire=(update)=>runJob(api('/api/baselines/run',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({platform:d.platform,name:d.name,scope:scopeArg,update})}),
    {title:(update?'Re-capture ':'Check ')+(d.short||d.name),
     then:()=>SCREENS.snapshot(snapArg())});

  const check=el('button','btn dark',viaTest?'▷ Run the test':'▷ Check now');
  check.disabled=!d.runnable;
  check.title=viaTest
    ? 'Runs '+(d.source.test||'the test that captured this')+' — with its login and steps'
    : d.runnable
    ? 'Loads the bound address and compares'
    : 'Neither a test nor an address — run the test that captures this snapshot, '
      +'or set a URL below';
  check.onclick=()=>fire(false);

  const re=el('button','btn','⟳ Re-capture');
  re.disabled=!d.runnable;
  re.title=viaTest
    ? 'The same test runs and its picture becomes the new baseline'
    : d.runnable
    ? 'The page is opened again and the picture becomes the new baseline'
    : check.title;
  re.onclick=()=>{
    if(!confirm(viaTest
      ? 'Run '+(d.source.test||'the test')+' and accept its picture as the '
        +'baseline?\n\nThe current picture goes into history — a rollback stays '
        +'possible.'
      : 'Re-capture the baseline from the page?\n\nThe current picture '
        +'goes into history — a rollback stays possible.'))return;
    fire(true);
  };

  const hist=el('button','btn','Baseline history');
  hist.onclick=()=>baselineHistoryModal(
    {name:d.name,short:d.short},d.platform,
    d.scope==='global'?undefined:d.scope);

  acts.append(check,re,hist);
  const del=roleButton('admin','btn danger','Delete',()=>{
    deleteBaseline({name:d.name},d.platform,
                   d.scope==='global'?undefined:d.scope);
  },'Deleting a baseline is an administrator action',scopeProject(d.scope));
  if(del)acts.append(del);
  head.append(acts);s.append(head);

  const grid=el('div');
  grid.style.cssText='display:grid;grid-template-columns:1fr 380px;gap:26px;'
    +'align-items:start;margin-top:22px';
  const left=el('div');const right=el('div');
  grid.append(left,right);s.append(grid);

  left.append(buildZoneEditor(d));
  right.append(buildSpecEditor(d));
  s.append(buildSnapshotHistory(d));
}

function snapArg(){
  const r=state.snapRef||{};
  return encodeURIComponent(r.scope||'global')+'/'+encodeURIComponent(r.platform||'')
    +'/'+encodeURIComponent(r.name||'');
}

/* ------- ignore-зоны мышью по картинке -------
   Кнопка «Ignore zone» в разборе падения брала `regions[0]` — то есть
   «игнорируй то, что движок нашёл первым». Человек при этом хочет выделить
   область, а не согласиться с чужим выбором. Здесь она выделяется. */
function buildZoneEditor(d){
  const card=el('div','panel pad');
  card.innerHTML='<div class="rail-h">Baseline &amp; ignore zones</div>';
  const hint=el('div','muted');
  hint.style.cssText='font-size:12.5px;line-height:1.5;margin-bottom:12px';
  hint.innerHTML='Drag on the picture to mark an area the engine must never '
    +'compare — a clock, a carousel, a random avatar. Zones live with the '
    +'snapshot, so they apply to every run, not just the one you were looking at.'
    +'<br>VisTest looks up what you circled and offers to hold the zone by the '
    +'<b>element</b> instead of the rectangle: then it moves with the layout '
    +'rather than staying where the element used to be.';
  card.append(hint);

  const wrap=el('div');
  wrap.style.cssText='position:relative;display:inline-block;max-width:100%;'
    +'background:#101012;border-radius:10px;overflow:hidden;touch-action:none';
  const img=el('img');
  img.src=API+d.baseline.full;
  img.style.cssText='display:block;max-width:100%;height:auto;user-select:none';
  img.draggable=false;
  wrap.append(img);
  card.append(wrap);

  // Зоны хранятся в координатах КАРТИНКИ, а рисуются в координатах экрана:
  // эталон может быть 2000px шириной, а на экране 700. Без пересчёта зона
  // уезжала бы тем сильнее, чем крупнее снимок.
  let boxes=(d.ignore_boxes||[]).map(b=>({...b}));
  const overlay=el('div');
  overlay.style.cssText='position:absolute;inset:0';
  wrap.append(overlay);

  const scale=()=>img.naturalWidth?img.clientWidth/img.naturalWidth:1;
  function paint(){
    overlay.innerHTML='';
    const k=scale();
    boxes.forEach((b,i)=>{
      /* Зона по элементу и зона по координатам выглядят по-разному, и это не
         украшение. Разница между ними — переживёт ли маска ближайший
         редизайн; узнать это, глядя на два одинаковых прямоугольника,
         нельзя. Потерявшая цель — третий вид: она ещё держит координаты, но
         уже не то, ради чего ставилась. */
      const kind=zoneKind(b);
      const r=el('div');
      r.style.cssText=`position:absolute;left:${b.x*k}px;top:${b.y*k}px;`
        +`width:${b.w*k}px;height:${b.h*k}px;border-radius:2px;`
        +(kind==='lost'
          ? 'background:rgba(184,118,11,.20);border:1.5px dashed var(--warn)'
          : kind==='element'
          ? 'background:rgba(216,63,38,.22);border:1.5px solid var(--fail-bright)'
          : 'background:rgba(216,63,38,.14);border:1.5px dashed var(--fail-bright)');
      r.title=zoneTitle(b);
      const tag=el('div','mono');
      tag.style.cssText='position:absolute;left:0;top:-17px;font-size:10px;'
        +'white-space:nowrap;max-width:220px;overflow:hidden;text-overflow:ellipsis;'
        +'color:'+(kind==='lost'?'var(--warn)':'var(--muted)');
      tag.textContent=kind==='lost'?'⚠ lost its element'
        :kind==='element'?(b.selector||'element')
        :'by coordinates';
      r.append(tag);
      const x=el('button','btn sm','×');
      x.title='Remove this zone';
      x.style.cssText='position:absolute;right:-9px;top:-9px;padding:0;width:20px;'
        +'height:20px;line-height:1;justify-content:center;border-radius:50%';
      x.onclick=e=>{e.stopPropagation();boxes.splice(i,1);paint();markDirty();};
      r.append(x);
      overlay.append(r);
    });
    const held=boxes.filter(b=>zoneKind(b)==='element').length;
    const lost=boxes.filter(b=>zoneKind(b)==='lost').length;
    count.textContent=boxes.length
      ? `${boxes.length} zone${boxes.length===1?'':'s'}`
        +(held?` · ${held} by element`:'')
        +(lost?` · ${lost} lost`:'')
      : 'no zones';
    count.style.color=lost?'var(--warn)':'';
  }

  let drawing=null;
  const ghost=el('div');
  ghost.style.cssText='position:absolute;display:none;background:rgba(216,63,38,.18);'
    +'border:1.5px dashed var(--fail-bright);border-radius:2px;pointer-events:none';
  wrap.append(ghost);

  wrap.addEventListener('pointerdown',e=>{
    if(e.target.tagName==='BUTTON')return;
    const rc=wrap.getBoundingClientRect();
    drawing={x0:e.clientX-rc.left,y0:e.clientY-rc.top};
    wrap.setPointerCapture(e.pointerId);
    ghost.style.display='block';
    e.preventDefault();
  });
  wrap.addEventListener('pointermove',e=>{
    if(!drawing)return;
    const rc=wrap.getBoundingClientRect();
    const x=e.clientX-rc.left,y=e.clientY-rc.top;
    Object.assign(ghost.style,{
      left:Math.min(drawing.x0,x)+'px', top:Math.min(drawing.y0,y)+'px',
      width:Math.abs(x-drawing.x0)+'px', height:Math.abs(y-drawing.y0)+'px'});
  });
  const finish=e=>{
    if(!drawing)return;
    const rc=wrap.getBoundingClientRect();
    const x=e.clientX-rc.left,y=e.clientY-rc.top;
    const k=scale()||1;
    const box={x:Math.round(Math.min(drawing.x0,x)/k),
               y:Math.round(Math.min(drawing.y0,y)/k),
               w:Math.round(Math.abs(x-drawing.x0)/k),
               h:Math.round(Math.abs(y-drawing.y0)/k)};
    drawing=null;ghost.style.display='none';
    // Клик без протяжки — это не зона нулевого размера, это промах.
    if(box.w<4||box.h<4)return;
    boxes.push(box);paint();markDirty();
    /* Спрашиваем элемент СРАЗУ после протяжки, а не отдельной кнопкой.
       Кнопка «а теперь привяжите к элементу» — это шаг, который делают один
       раз из десяти и забывают в девяти; а зона без элемента и есть та самая
       зона, которая через месяц глушит не то. */
    offerElement(box,d,patch=>{
      Object.assign(boxes[boxes.length-1]||{},patch);
      paint();markDirty();
    });
  };
  wrap.addEventListener('pointerup',finish);
  wrap.addEventListener('pointercancel',()=>{drawing=null;ghost.style.display='none';});

  const bar=el('div');
  bar.style.cssText='display:flex;gap:10px;align-items:center;margin-top:14px;flex-wrap:wrap';
  const count=el('span','muted');count.style.fontSize='12.5px';
  const save=el('button','btn dark sm','Save zones');
  const clear=el('button','btn sm','Clear all');
  const dirty=el('span','muted');dirty.style.cssText='font-size:12.5px;display:none';
  dirty.textContent='not saved';dirty.style.color='var(--warn)';
  function markDirty(){dirty.style.display='inline';}

  save.onclick=async()=>{
    save.disabled=true;const old=save.textContent;save.innerHTML='<span class="spin"></span>';
    try{
      await api('/api/baselines/ignore-boxes',{method:'PUT',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({platform:d.platform,name:d.name,
          scope:d.scope==='global'?undefined:d.scope,boxes})});
      toast('Ignore zones saved · they apply to the next run','ok');
      dirty.style.display='none';
    }catch(e){toast(String(e.message||e),'err');}
    finally{save.disabled=false;save.textContent=old;}
  };
  clear.onclick=()=>{if(boxes.length){boxes=[];paint();markDirty();}};

  bar.append(save,clear,count,dirty);
  if(!can('reviewer',scopeProject(d.scope))){save.disabled=true;clear.disabled=true;
    bar.append(el('span','muted','viewer role — read only'));}
  card.append(bar);

  const size=el('div','muted');
  size.style.cssText='font-size:12px;margin-top:10px;font-family:var(--mono)';
  size.textContent=[d.baseline.width&&d.baseline.height
      ? `${d.baseline.width}×${d.baseline.height}` : '',
    d.baseline.size_kb?`${d.baseline.size_kb} KB`:'',
    d.baseline.has_mask?'noise mask':''].filter(Boolean).join(' · ');
  card.append(size);

  if(img.complete)setTimeout(paint,0);else img.onload=paint;
  /* Слушатель `resize` один на всё приложение, а не по одному на отрисовку.

     Здесь стояло `window.addEventListener('resize',paint)` — и снималось это
     никогда. Экран снимка перерисовывается на каждое сохранение спеки, на
     каждую пересъёмку и на каждый заход из списка, и каждый раз к окну
     добавлялся ещё один обработчик, держащий за собой выброшенную из
     документа картинку со всеми зонами. К концу дня разбора их набирались
     десятки: память не отдаётся, а любое перетаскивание края окна прогоняет
     весь этот хвост заново.

     Перерисовывать нужно ровно тот редактор, который сейчас на экране, — его
     и держим в одной переменной. Ушёл из документа — забыли. */
  zonePaint={node:wrap,paint};
  installZoneResize();
  return card;
}

/* Чем зона держится — тремя словами, потому что от этого зависит, доживёт ли
   она до следующего редизайна.

   `lost` приезжает с сервера: находит ли селектор свою цель в DOM эталона,
   считается там же, где и при сравнении. Считать это на клиенте значило бы
   завести второе место, где живёт ответ «маска работает или уже нет». */
function zoneKind(b){
  if(b.lost)return 'lost';
  return (b.selector||b.match)?'element':'coordinates';
}
function zoneTitle(b){
  if(zoneKind(b)==='coordinates')
    return 'Held by coordinates. It stays where it is even when the layout moves — '
      +'draw it again to bind it to an element.';
  const where=b.match&&b.match.testid?'data-testid="'+b.match.testid+'"'
    :b.match&&b.match.id?'#'+b.match.id
    :(b.selector||'element');
  if(b.lost)
    return 'This zone no longer finds '+where+' in the baseline DOM. It is still '
      +'holding by coordinates — that is, by where the element used to be.';
  return 'Held by '+where
    +(b.matches>1?` · ${b.matches} elements match`:'')
    +'. It moves with the layout.';
}

/* «Что я обвёл» — сразу после протяжки.

   Сам элемент попасть мышью почти невозможно: промахнулся на два пикселя и
   выделил <span> внутри кнопки. Поэтому сервер возвращает лесенку — узел и
   его родители, — а человек выбирает уровень осмысленно, глядя на подписи. */
async function offerElement(box,d,apply){
  const q={platform:d.platform,name:d.name,
           scope:d.scope==='global'?undefined:d.scope,...box};
  let data;
  try{data=await api('/api/baselines/element-at',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify(q)});}
  catch{return;}
  const nodes=(data&&data.nodes)||[];
  if(!nodes.length){
    /* Снимок снят без DOM — зацепиться не за что, и врать про это нельзя.
       Зона остаётся координатной, а человек узнаёт почему. */
    toast(data&&data.reason
      ? 'Zone held by coordinates: '+data.reason
      : 'Nothing to bind to here — the zone stays by coordinates');
    return;
  }

  const wrap=el('div');
  wrap.innerHTML=`<div class="mhead"><div><h3>Bind the zone to an element?</h3>
    <div class="msub">A zone bound to an element moves with the layout. A zone
      bound to coordinates stays where it is — and after the next redesign it
      hides whatever moved into that rectangle instead.</div></div>
    <button class="mclose" aria-label="Close">×</button></div>`;
  const body=el('div','pf-body');wrap.append(body);

  nodes.forEach((n,i)=>{
    const row=el('label','pick'+(i===0?' on':''));
    const strength=n.holds_by==='data-testid'?'green'
      :n.holds_by==='id'?'':'amber';
    row.innerHTML=`<b class="mono" style="overflow:hidden;text-overflow:ellipsis">${
        esc(n.selector||n.tag||'?')}</b>
      <span class="tag ${strength}">${esc(n.holds_by)}</span>
      <span class="mono faint">${n.w}×${n.h}</span>`
      +(n.text?`<span class="faint" style="flex-basis:100%;font-size:11.5px">${
        esc(n.text)}</span>`:'');
    row.title=n.holds_by==='css path'
      ? 'A path through the tree: it breaks on any restructuring. Works, but '
        +'is the least durable of the three.'
      : 'Survives restructuring — this is what data-testid and id are for.';
    row.onclick=()=>{
      $$('.pick',body).forEach(x=>x.classList.remove('on'));
      row.classList.add('on');
      body.dataset.pick=String(i);
    };
    body.append(row);
  });
  body.dataset.pick='0';

  const foot=el('div','macts');
  const keep=el('button','btn','Keep it by coordinates');
  keep.title='The zone stays exactly where you drew it.';
  keep.onclick=closeModal;
  const bind=el('button','btn accent','Bind to the element');
  bind.onclick=()=>{
    const n=nodes[Number(body.dataset.pick)||0];
    const patch={selector:n.selector||undefined,match:{},
      /* Прямоугольник подтягивается к элементу: человек обводил его на глаз, а
         точные границы знает DOM. Заодно это и есть запасные координаты — те,
         по которым зона будет работать, если элемент однажды исчезнет. */
      x:n.x,y:n.y,w:n.w,h:n.h,matches:1,lost:false};
    if(n.testid)patch.match.testid=n.testid;
    if(n.id)patch.match.id=n.id;
    if(n.tag)patch.match.tag=n.tag;
    if(n.cls)patch.match.cls=n.cls;
    closeModal();
    /* Зона правится через переданное действие, а не напрямую: список зон и
       перерисовка живут внутри редактора, и тянуть их сюда через замыкание
       значило бы, что эта функция может существовать только рядом с ним. */
    apply(patch);
  };
  foot.append(keep,bind);wrap.append(foot);
  openModal(wrap,{wide:true});$('.mclose',wrap).onclick=closeModal;
}

let zonePaint=null,zoneResizeOn=false;
function installZoneResize(){
  if(zoneResizeOn)return;
  zoneResizeOn=true;
  window.addEventListener('resize',()=>{
    if(!zonePaint)return;
    if(!document.body.contains(zonePaint.node)){zonePaint=null;return;}
    zonePaint.paint();
  });
}

/* ------- спека целиком -------
   Меню предлагало «Edit the spec», а правился один URL через prompt(). При
   этом карточка показывала и окно, и шаги, и `/api/baselines/meta` принимал их
   все — задать из интерфейса было нельзя. Снимок за формой логина без шагов не
   переснимется вообще. */
function buildSpecEditor(d){
  const card=el('div','panel pad');
  card.innerHTML='<div class="rail-h">How this snapshot is taken</div>';

  const spec=d.spec||{};
  const field=(label,value,ph,note)=>{
    const w=el('div');w.style.marginBottom='14px';
    w.append(el('label','field-lbl',label));
    const i=el('input','inp');i.value=value==null?'':String(value);
    i.placeholder=ph||'';w.append(i);
    if(note){const n=el('div','muted');n.style.cssText='font-size:11.5px;margin-top:4px';
      n.textContent=note;w.append(n);}
    card.append(w);return i;
  };

  const url=field('Page URL',spec.url,'https://my-app.local/checkout',
    'Without it the snapshot cannot be re-captured or checked.');
  const vp=field('Window',spec.viewport,'1440x900');
  const sel=field('Selector',spec.selector,'.checkout-form',
    'Empty — the whole page.');
  const wait=field('Pause before the shot, ms',spec.wait,'0');

  card.append(el('label','field-lbl','Steps before the shot'));
  const steps=el('textarea','inp');
  steps.rows=Math.max(3,(spec.steps||[]).length+1);
  steps.style.cssText='width:100%;font-family:var(--mono);font-size:12.5px';
  steps.value=JSON.stringify(spec.steps||[],null,1);
  card.append(steps);
  const sh=el('div','muted');
  sh.style.cssText='font-size:11.5px;margin-top:6px;line-height:1.5';
  sh.innerHTML='JSON array. A named scenario from <code>vistest.yaml</code>: '
    +'<code>[{"action":"flow","name":"login"}]</code>.'
    +(d.flows&&d.flows.length?' Available: '+d.flows.map(esc).join(', '):'');
  card.append(sh);

  /* ------- пороги этого снимка -------
     Третий уровень после глобального и проектного. Появился потому, что двух не
     хватало: один шумный дашборд заставлял ослаблять порог для всего набора —
     чинить один снимок ценой чувствительности всех остальных.

     Пустое поле значит «как у всех», и в подсказке стоит унаследованное
     значение. Показать в поле само унаследованное число было бы удобнее на вид
     и хуже по сути: тогда «здесь ничего не задано» и «здесь задано ровно то же»
     выглядят одинаково, а это разные вещи — второе не поедет за общей
     настройкой, когда её изменят. */
  const th=d.thresholds||{};
  const own=th.own||{}, inherited=th.inherited||{}, sources=th.sources||{};
  card.append(el('div','field-lbl','Thresholds for this snapshot'));
  const thHint=el('div','muted');
  thHint.style.cssText='font-size:11.5px;margin:2px 0 10px;line-height:1.5';
  thHint.textContent='Empty — inherited. Fill in only what this snapshot needs '
    +'to differ in, so the rest keeps following the set.';
  card.append(thHint);

  const thRow=el('div');
  thRow.style.cssText='display:flex;gap:12px;flex-wrap:wrap;margin-bottom:14px';
  const thField=(name,label)=>{
    const w=el('div');w.style.flex='1';w.style.minWidth='150px';
    w.append(el('label','field-lbl',label));
    const i=el('input','inp');i.type='number';i.step='0.01';i.min='0';
    i.value=own[name]==null?'':String(own[name]);
    const from=sources[name]||'config';
    i.placeholder=inherited[name]!=null?String(inherited[name]):'';
    w.append(i);
    const n=el('div','muted');n.style.cssText='font-size:11px;margin-top:4px';
    /* Откуда значение — это ответ на вопрос, который человек и задаёт: «это я
       тут выставил или так везде». От ответа зависит, где чинить. */
    n.textContent=from==='snapshot'
      ? 'set here · the set says '+(inherited[name]!=null?inherited[name]:'—')
      : 'from the '+from;
    w.append(n);thRow.append(w);return i;
  };
  const fs=thField('fail_severity','Fail at severity');
  const ar=thField('max_changed_area_pct','Changed area, %');
  card.append(thRow);

  const acts=el('div');acts.style.cssText='display:flex;gap:10px;margin-top:16px';
  const save=el('button','btn dark','Save spec');
  acts.append(save);card.append(acts);

  if(!can('reviewer',scopeProject(d.scope))){
    [url,vp,sel,wait,steps,fs,ar].forEach(i=>{i.disabled=true;});
    save.disabled=true;
    card.append(el('div','info-note','Editing the spec requires the reviewer role'
      +(scopeProject(d.scope)?' in project «'+esc(scopeProject(d.scope))+'»':'')+'.'));
    return card;
  }

  save.onclick=async()=>{
    let parsed;
    try{parsed=JSON.parse(steps.value.trim()||'[]');}
    catch(e){return toast('Steps: not valid JSON — '+e.message,'err');}
    if(!Array.isArray(parsed))return toast('Steps must be an array','err');

    // null стирает поле. Пустая строка для этого не годится: «селектор равен
    // пустой строке» и «селектора нет» — разные вещи, и первая ломает захват.
    const body={platform:d.platform,name:d.name,
      scope:d.scope==='global'?undefined:d.scope,
      url:url.value.trim()||null,
      viewport:vp.value.trim()||null,
      selector:sel.value.trim()||null,
      wait:wait.value.trim()?Number(wait.value):null,
      steps:parsed,
      /* Пустое поле — снять переопределение, а не «ноль». Ноль здесь
         осмысленное значение («падать на любом видимом различии»), и спутать
         его с «не задано» значит превратить пустое поле в самый строгий порог
         из возможных. */
      thresholds:{
        fail_severity:fs.value.trim()===''?null:Number(fs.value),
        max_changed_area_pct:ar.value.trim()===''?null:Number(ar.value)}};
    if(body.wait!=null&&!Number.isFinite(body.wait))
      return toast('Pause must be a number of milliseconds','err');
    for(const [k,v] of Object.entries(body.thresholds))
      if(v!==null&&!Number.isFinite(v))
        return toast(k.replace(/_/g,' ')+' must be a number','err');
    if(Object.values(body.thresholds).every(v=>v===null))body.thresholds=null;

    save.disabled=true;const old=save.textContent;save.innerHTML='<span class="spin"></span>';
    try{
      await api('/api/baselines/meta',{method:'POST',
        headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      toast('Spec saved','ok');
      SCREENS.snapshot(snapArg());
    }catch(e){toast(String(e.message||e),'err');save.disabled=false;save.textContent=old;}
  };
  return card;
}

/* ------- история проверок этого снимка ------- */
function buildSnapshotHistory(d){
  const box=el('div');
  box.innerHTML='<div class="sect-h"><h2>Checks of this snapshot</h2>'
    +'<span class="note">across every run</span></div>';
  const rows=d.history||[];
  if(!rows.length){
    box.append(el('div','card','<div class="empty">This snapshot has never been '
      +'run. Either bring it into a run, or delete it — an untested baseline is '
      +'dead weight.</div>'));
    return box;
  }
  const t=el('div','row-table');
  rows.forEach(h=>{
    const row=el('div','snap-row');
    row.style.gridTemplateColumns='auto auto 1fr auto auto';
    const badge=h.review==='approved'?'<span class="tag purple">accepted</span>'
      :h.review==='rejected'?'<span class="tag red">a bug</span>'
      :h.verdict==='fail'?'<span class="tag red">failed</span>'
      :h.verdict==='error'?'<span class="tag amber">error</span>'
      :h.verdict==='new_baseline'?'<span class="tag purple">new</span>'
      :'<span class="tag green">ok</span>';
    row.innerHTML=`${badge}
      <div class="mono">sev ${fmt(h.max_severity,1)}</div>
      <div class="mono faint">${esc(h.run_key||('#'+h.run_id))} ·
        ${esc(h.branch||'—')} ${esc((h.git_sha||'').slice(0,8))}</div>
      <div class="mono">${pct(h.changed_area_pct,3)}</div>
      <div class="mono faint">${esc(timeAgo(h.created_at))}</div>`;
    hit(row,()=>{location.hash='#/compare/'+h.id;});
    t.append(row);
  });
  box.append(t);
  return box;
}

