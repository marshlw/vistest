/* VisTest - self-hosted visual regression testing.
 * Copyright (C) 2026 Kirill Kulagin
 * SPDX-License-Identifier: AGPL-3.0-or-later
 *
 * This file is part of VisTest. See LICENSE for the full terms and NOTICE for
 * the trademark and commercial-licensing terms. Removing this header does not
 * remove those obligations.
 */

/* Командная палитра (⌘K): поиск снимка или прогона. */
let palData=null,palItems=[],palSel=0;
async function openPalette(){
  if($('#palBg'))return;
  const bg=el('div','pal-bg');bg.id='palBg';
  const pal=el('div','pal');
  pal.innerHTML=`<div class="pal-in"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="7" cy="7" r="5"/><path d="M11 11l3 3"/></svg>
    <input id="palInput" placeholder="Search: runs, baselines, tests, screens…" autocomplete="off"></div>
    <div class="pal-res" id="palRes"></div>
    <div class="pal-hint"><span><span class="k">↑↓</span>choice</span><span><span class="k">↵</span>open</span><span><span class="k">esc</span>close</span></div>`;
  bg.append(pal);document.body.append(bg);
  bg.onclick=e=>{if(e.target===bg)closePalette();};
  const input=$('#palInput');input.oninput=()=>renderPalette(input.value);input.onkeydown=palKey;
  renderPalette('');setTimeout(()=>input.focus(),20);
  if(!palData){palData=await loadPaletteData();renderPalette(input.value);}
}
function closePalette(){const b=$('#palBg');if(b)b.remove();}
async function loadPaletteData(){
  const d={nav:NAV.map(n=>({type:'Screen',label:n.label,go:'#/'+n.v})),runs:[],baselines:[],tests:[]};
  try{d.runs=(await api('/api/runs?limit=60&project='+encodeURIComponent(state.project))).map(r=>({type:'Run',label:(r.run_key||r.project||('#'+r.id))+' #'+r.id,sub:(r.git_sha||'')+' · '+(r.platform||''),run:r.id}));}catch{}
  try{const b=await api('/api/baselines/detail');(b.platforms||[]).forEach(p=>(p.items||[]).forEach(it=>d.baselines.push({type:'Baseline',label:it.short||it.name,sub:it.url||p.platform,platform:p.platform})));}catch{}
  try{d.tests=((await api('/api/tests')).tests||[]).map(t=>({type:'Test',label:t.name,test:t.name}));}catch{}
  return d;
}
function hlmatch(text,q){
  text=String(text||'');if(!q)return esc(text);
  const i=text.toLowerCase().indexOf(q);if(i<0)return esc(text);
  return esc(text.slice(0,i))+'<mark>'+esc(text.slice(i,i+q.length))+'</mark>'+esc(text.slice(i+q.length));
}
function renderPalette(q){
  const res=$('#palRes');if(!res)return;q=(q||'').trim().toLowerCase();
  const src=palData||{nav:NAV.map(n=>({type:'Screen',label:n.label,go:'#/'+n.v})),runs:[],baselines:[],tests:[]};
  // Snapshots are taken from the open run on the fly — so they are always current.
  const snaps=(state.run&&Array.isArray(state.run.comparisons))
    ? state.run.comparisons.map(c=>({type:'Snapshot',label:c.snapshot_name,sub:'sev '+fmt(c.max_severity,1)+(c.review?' · '+c.review:''),comp:c.id}))
    : [];
  const all=[...src.nav,...snaps,...src.runs,...src.baselines,...src.tests];
  const match=all.filter(x=>!q||((x.label||'')+' '+(x.sub||'')).toLowerCase().includes(q));
  palItems=[];res.innerHTML='';
  if(!match.length){res.innerHTML='<div class="pal-empty">Nothing found</div>';return;}
  [['Screen','Screens'],['Snapshot','Snapshots of this run'],['Run','Runs'],['Baseline','Baselines'],['Test','Tests']].forEach(([type,title])=>{
    const g=match.filter(x=>x.type===type).slice(0,6);if(!g.length)return;
    res.append(el('div','pal-grp',title));
    g.forEach(x=>{const row=el('div','pal-item');
      row.innerHTML=`<div><div class="t">${hlmatch(x.label,q)}</div>${x.sub?`<div class="s">${hlmatch(x.sub,q)}</div>`:''}</div><span class="badge">${esc(type)}</span>`;
      hit(row,()=>activatePalette(x));res.append(row);palItems.push({el:row,item:x});});
  });
  palSel=0;highlightPalette();
}
function highlightPalette(){palItems.forEach((p,i)=>p.el.classList.toggle('on',i===palSel));const c=palItems[palSel];if(c)c.el.scrollIntoView({block:'nearest'});}
function palKey(e){
  if(e.key==='ArrowDown'){e.preventDefault();palSel=Math.min(palItems.length-1,palSel+1);highlightPalette();}
  else if(e.key==='ArrowUp'){e.preventDefault();palSel=Math.max(0,palSel-1);highlightPalette();}
  else if(e.key==='Enter'){e.preventDefault();const c=palItems[palSel];if(c)activatePalette(c.item);}
  else if(e.key==='Escape'){e.preventDefault();closePalette();}
}
function activatePalette(x){
  closePalette();
  if(x.go){location.hash=x.go;}
  else if(x.run){state.pendingRun=x.run;if(location.hash==='#/runs')SCREENS.runs();else location.hash='#/runs';}
  else if(x.platform){state.platform=x.platform;if(location.hash==='#/baselines')SCREENS.baselines();else location.hash='#/baselines';}
  else if(x.test){location.hash='#/tests/'+encodeURIComponent(x.test);}
  else if(x.comp){location.hash='#/compare/'+x.comp;}
}
function initPalette(){
  document.addEventListener('keydown',e=>{
    if((e.metaKey||e.ctrlKey)&&['k','K','л','Л'].includes(e.key)){e.preventDefault();openPalette();}
  });
  const s=$('#search');if(s){s.readOnly=true;s.style.cursor='pointer';s.onclick=openPalette;s.onfocus=openPalette;}
}

