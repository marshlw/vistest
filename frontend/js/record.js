/* VisTest - self-hosted visual regression testing.
 * Copyright (C) 2026 Kirill Kulagin
 * SPDX-License-Identifier: AGPL-3.0-or-later
 *
 * This file is part of VisTest. See LICENSE for the full terms and NOTICE for
 * the trademark and commercial-licensing terms. Removing this header does not
 * remove those obligations.
 */

/* Запись эталонов мышью: модалка, панель, VNC-окно. */
async function recordFlow(){
  let st=null;
  try{st=await api('/api/record/status');}
  catch(e){return toast(String(e.message||e),'err');}
  if(st&&st.running){
    if(confirm('Recording is already running on the service machine. Stop it?')){
      try{await api('/api/record/stop',{method:'POST'});toast('Recording stopped','ok');}
      catch(e){toast(String(e.message||e),'err');}
    }
    return;
  }
  openRecordModal(st);
}
/* Подпись + поле. Подпись — настоящий <label> с `for`: щелчок по слову ставит
   курсор в поле, скринридер называет поле этим словом, а менеджер паролей
   понимает, что перед ним. Без `for` соседство в разметке видит только глаз, а
   на нём одном интерфейс не держится.

   Полю без `id` он выдаётся здесь: помнить про уникальный `id` в каждом из
   двадцати мест, откуда зовут `labeled()`, — это условие, которое рано или
   поздно не выполнят. */
let labeledN=0;
function labeled(lbl,node){
  const d=el('div','field');
  const l=el('label','field-lbl',esc(lbl));
  if(node&&node.tagName&&/^(INPUT|SELECT|TEXTAREA)$/.test(node.tagName)){
    if(!node.id)node.id='fld-'+(++labeledN);
    l.setAttribute('for',node.id);
  }
  d.append(l);
  if(node.classList&&node.classList.contains('inp'))node.style.marginTop='6px';
  d.append(node);
  return d;
}
function chk(lbl){const w=el('label');w.style.cssText='display:flex;align-items:center;gap:7px;cursor:pointer';const i=el('input');i.type='checkbox';w.append(i,document.createTextNode(' '+lbl));return {wrap:w,input:i};}
function vncUrl(port){return location.protocol+'//'+location.hostname+':'+port+'/vnc.html?autoconnect=1&resize=remote&reconnect=1';}
function openRecordModal(st){
  st=st||{};const vnc=st.vnc_port;
  const wrap=el('div');
  wrap.innerHTML=`<div class="mhead"><div><h3>Record baselines with the mouse</h3>
    <div class="msub">The browser opens <b>on the service machine</b>. ${vnc?'Interact with it in <b>recording window</b> (opens in a new tab).':'The recording panel is in the bottom-right corner of the page.'} You capture frames — at the end «Done», baselines and a test are assembled.</div></div>
    <button class="mclose" aria-label="Close">×</button></div>`;
  const url=el('input','inp');url.placeholder='https://my-app.local/checkout';url.style.marginTop='6px';
  const vp=el('input','inp');vp.placeholder='e.g. 1440x900';
  const br=el('select','inp');['chromium','firefox','webkit'].forEach(b=>{const o=el('option');o.value=b;o.textContent=b;br.append(o);});
  const g=el('div','mrow grid2');g.append(labeled('Window',vp),labeled('Browser',br));
  const pom=chk('Page objects (--pom)');const nocode=chk('Baselines only, no code');
  const opts=el('div','mrow');opts.style.cssText='display:flex;gap:20px;font-size:13px;color:var(--muted)';opts.append(pom.wrap,nocode.wrap);
  wrap.append(labeled('Page URL',url),g,opts);
  const acts=el('div','macts');
  const go=el('button','btn accent','Open the browser and record');
  const cancel=el('button','btn','Cancel');cancel.onclick=closeModal;
  acts.append(go);
  if(vnc){const w=el('button','btn','Recording window ↗');w.onclick=()=>window.open(vncUrl(vnc),'vistest-record');acts.append(w);}
  acts.append(cancel);wrap.append(acts);
  openModal(wrap);$('.mclose',wrap).onclick=closeModal;setTimeout(()=>url.focus(),40);
  const submit=()=>{
    const u=url.value.trim();
    if(!u)return toast('Set a URL','err');
    if(!/^https?:\/\//i.test(u)&&!/^file:/i.test(u))return toast('The URL must start with http:// or https://','err');
    if(vnc)window.open(vncUrl(vnc),'vistest-record');   // recording window — right away, on click (otherwise it is blocked)
    const body={url:u};
    if(vp.value.trim())body.viewport=vp.value.trim();
    if(br.value)body.browser=br.value;
    if(pom.input.checked)body.pom=true;
    if(nocode.input.checked)body.no_code=true;
    runJobLog(api('/api/record/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}),
      {title:'Record baselines with the mouse',sub:vnc?'The recording window opened — move the mouse there. At the end «Done».':'The browser opened on the service machine. Capture frames with the panel, at the end — «Done».',
       then:()=>{refreshCounts();if(state.view==='baselines')SCREENS.baselines();if(state.view==='tests')SCREENS.tests();}});
  };
  go.onclick=submit;
  url.addEventListener('keydown',e=>{if(e.key==='Enter')submit();});
}

