/* VisTest - self-hosted visual regression testing.
 * Copyright (C) 2026 Kirill Kulagin
 * SPDX-License-Identifier: AGPL-3.0-or-later
 *
 * This file is part of VisTest. See LICENSE for the full terms and NOTICE for
 * the trademark and commercial-licensing terms. Removing this header does not
 * remove those obligations.
 */

/* Основа: выборка узлов, `api()`, тосты, экранирование.

   Всё остальное опирается на этот файл, а он — ни на что. Поэтому он идёт первым,
   и это единственное жёсткое правило в порядке подключения. */
const API = location.origin;
const $  = (s,r=document)=>r.querySelector(s);
const $$ = (s,r=document)=>[...r.querySelectorAll(s)];
function el(t,c,h){const n=document.createElement(t);if(c)n.className=c;if(h!=null)n.innerHTML=h;return n;}
/* Кликабельный <div> → кнопка для клавиатуры и скринридера.

   Половина навигации в этом интерфейсе — `.run-item`, `.snap-row`, `.plat`,
   `.test-item` — была обычными <div> с `onclick`. С клавиатуры они недостижимы
   (Tab о них не знает), для скринридера невидимы (это «группа», а не действие),
   и списки прогонов, снимков, платформ и тестов проходились только мышью.

   Обработчик Enter/Space один на документ, а не по одному на элемент: их сотни,
   они пересоздаются на каждой перерисовке, и вешать на каждый по слушателю
   значит копить их до конца сессии. */
function hit(node,handler,label){
  node.setAttribute('role','button');
  node.setAttribute('tabindex','0');
  if(label)node.setAttribute('aria-label',label);
  node.onclick=handler;
  return node;
}
document.addEventListener('keydown',e=>{
  if(e.key!=='Enter'&&e.key!==' ')return;
  const t=e.target;
  if(!t||!t.getAttribute||t.getAttribute('role')!=='button')return;
  if(t.tagName==='BUTTON'||t.tagName==='A'||t.tagName==='INPUT')return;
  /* Пробел на кнопке прокручивает страницу — ровно то, чего от нажатия не
     ждут. Enter не прокручивает, но и его лучше не отдавать дальше. */
  e.preventDefault();
  t.click();
});
/* Апостроф экранируется наравне с кавычкой: сегодня все атрибуты в шаблонах
   двойные, но одна правка «title='...'» — и дыра открыта, а найти её потом
   можно только целенаправленным поиском. */
const esc=s=>String(s==null?'':s).replace(/[&<>"']/g,
  m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
const fmt=(v,d=2)=>v==null||v===''?'—':Number(v).toFixed(d);
const pct=(v,d=2)=>v==null?'—':Number(v).toFixed(d)+'%';
function fmtInt(n){return n==null?'—':String(Math.round(n)).replace(/\B(?=(\d{3})+(?!\d))/g,' ');}

const state={view:'runs',project:'*',me:null,runs:[],run:null,comp:null,
  mode:'slide',baselines:{platforms:[],current:''},platform:null,job:null,
  // scope — какой набор эталонов открыт на вкладке «Baselines»:
  // 'global' — собственный набор сервиса, 'project:<key>' — комплект,
  // снятый VisTest для подключённого проекта.
  //
  // `null` — «не выбирали ни разу», и это не то же самое, что 'global'.
  // Пока здесь стояло 'global', экран не мог отличить осознанный выбор
  // собственного набора от умолчания и открывал пустой набор человеку, у
  // которого все эталоны лежат в наборе подключённого проекта.
  scope:null,
  runFilter:'all', runLimit:60,
  counts:{}, rig:null};

async function api(p,opts){
  let r;
  try{ r=await fetch(API+p,opts); }
  catch(netErr){ throw new Error('The service is unavailable — check whether `python run.py ui`.'); }
  if(!r.ok){
    if(r.status===401 && !p.startsWith('/api/auth/')){showLogin('Session expired — sign in again');throw new Error('Sign-in required');}
    if(r.status===403){let d='';try{d=(await r.clone().json()).detail||'';}catch{}throw new Error(typeof d==='string'&&d?d:'Not enough rights');}
    /* 503 — сервис отказывается обслуживать инсталляцию без администратора,
       когда запрос пришёл не с петли. Это не «сломалось», а единственное
       состояние, из которого нельзя выйти изнутри интерфейса: пользователя
       ещё нет, значит и войти некем. Показываем это отдельно, иначе человек
       получает форму входа, которую невозможно пройти. */
    if(r.status===503){let d='';try{d=(await r.clone().json()).detail||'';}catch{}blockingNotice(d||'The service is not configured yet');throw new Error(d||'Service unavailable');}
    if(r.status===409){let d=null;try{d=(await r.clone().json()).detail;}catch{}if(d&&typeof d==='object')throw new Error(`${d.error||'Conflict'}\n${d.message||''}`);}
    /* 404 бывает двух совершенно разных видов, и раньше оба показывались как
       «сервис не знает такого роута, у вас старый код». Для отсутствующего
       прогона, снимка или задачи это неправда, и неправда обидная: человек
       идёт перезапускать сервис вместо того, чтобы прочитать «такого прогона
       больше нет». Отличаем по ответу: FastAPI на неизвестный путь отдаёт
       ровно `Not Found`, а роут, который знает про свой объект, объясняет
       словами. */
    if(r.status===404 && p.startsWith('/api/')){
      let d='';try{d=(await r.clone().json()).detail||'';}catch{}
      if(typeof d==='string'&&d&&d!=='Not Found')throw new Error(d);
      throw new Error('The service does not know the route ('+p+'). Looks like an old version of the code is running — restart: python run.py ui');
    }
    let t='';try{t=(await r.clone().json()).detail;}catch{t=await r.text();}
    throw new Error(typeof t==='string'?t:JSON.stringify(t));
  }
  if(r.status===204)return null;
  const ct=r.headers.get('content-type')||'';
  return ct.includes('json')?r.json():r.text();
}

/* Полноэкранное объяснение вместо формы входа: показывается один раз и не
   перекрывается следующими запросами, которые упрутся в то же самое. */
function blockingNotice(text){
  if($('#blockNotice'))return;
  hideLogin();
  const bg=el('div');bg.id='blockNotice';
  bg.style.cssText='position:fixed;inset:0;background:var(--bg);z-index:220;display:flex;'
    +'align-items:center;justify-content:center;padding:24px';
  const box=el('div','card');box.style.cssText='width:520px;max-width:100%;padding:28px';
  box.innerHTML=`<h3 style="font-size:19px">The service is not ready to be used over the network</h3>
    <p class="muted" style="margin-top:12px;font-size:14px;line-height:1.6;white-space:pre-line">${esc(text)}</p>`;
  bg.append(box);document.body.append(bg);
}

/* ------- toast ------- */
let toastN=0;
function toast(msg,kind){const n=el('div','toast'+(kind?' '+kind:''),esc(msg));$('#toast').append(n);
  const id=++toastN;setTimeout(()=>n.remove(),kind==='err'?6000:3200);return id;}

