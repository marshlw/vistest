/* VisTest - self-hosted visual regression testing.
 * Copyright (C) 2026 Kirill Kulagin
 * SPDX-License-Identifier: AGPL-3.0-or-later
 *
 * This file is part of VisTest. See LICENSE for the full terms and NOTICE for
 * the trademark and commercial-licensing terms. Removing this header does not
 * remove those obligations.
 */

/* Экран входа, регистрация, роли, меню профиля.

   Одна коробка на три режима: вход, заявка на доступ и «занять инсталляцию».
   Здесь же `can()` и `roleButton()` — проверка прав на стороне интерфейса. Она
   не про безопасность (настоящая стоит на сервере), а про честность экрана:
   кнопка, которую бэкенд заведомо отвергнет, — это не строгость, это ложь. */
/* Одна коробка, три режима.

   `setup`    — активных пользователей нет: заводим первого администратора.
   `signin`   — обычный вход.
   `register` — заявка на доступ; она ничего не даёт, пока админ не одобрит,
                и ровно поэтому регистрация может быть открытой.

   Режим спрашивается у `/api/auth/state` — публичного роута, который для того
   и появился. Раньше интерфейс выяснял это методом тыка: бил в первый
   попавшийся роут с данными и разбирал код ответа, а свежая инсталляция по
   сети отвечала 503 с советом сходить на машину сервиса — то есть стеной,
   выйти из-за которой изнутри было нельзя. */
let authState={};
let loginMode='signin';

function setLoginMode(mode){
  loginMode=mode;
  const setup=mode==='setup', reg=mode==='register';
  $('#lgTitle').textContent=setup?'Set up VisTest'
    :reg?'Request access':'Sign in to VisTest';
  $('#lgSub').textContent=setup
    ? 'No administrator here yet. The account you create now becomes one, and this form closes for good.'
    : reg
    ? 'An administrator approves the request — until then you cannot sign in.'
    : 'Review access to runs and baselines.';
  $('#lgSubmit').textContent=setup?'Create the administrator'
    :reg?'Send the request':'Sign in';
  /* `hidden` вместо `style.display`: поле, спрятанное инлайновым стилем,
     остаётся в порядке обхода Tab и в дереве доступности — человек с
     клавиатуры попадал курсором в невидимое поле «SETUP TOKEN» между логином и
     паролем и не понимал, куда делся фокус. */
  $('#lgNameField').hidden=!(setup||reg);
  $('#lgTokenField').hidden=!(setup&&authState.setup_token_required);
  $('#lgPass').autocomplete=(setup||reg)?'new-password':'current-password';
  /* Переключатель прячется там, где выбирать не из чего: на пустой
     инсталляции второго варианта нет, а при закрытой регистрации — тем более.
     Показать вкладку, которая ответит отказом, значит соврать. */
  $('#lgTabs').classList.toggle('hidden',
    setup||authState.registration!=='open');
  $$('#lgTabs .lg-tab').forEach(b=>b.classList.toggle('on',b.dataset.tab===mode));
  $('#lgMsg').textContent='';$('#lgOk').textContent='';
}

$$('#lgTabs .lg-tab').forEach(b=>{
  b.onclick=()=>{setLoginMode(b.dataset.tab);$('#lgLogin').focus();};
});

async function showLogin(msg){
  $('#login').classList.remove('hidden');
  try{authState=await api('/api/auth/state');}catch{authState={};}
  setLoginMode(authState.needs_setup?'setup':'signin');
  if(msg)$('#lgMsg').textContent=msg;
  (authState.needs_setup?$('#lgName'):$('#lgLogin')).focus();
}
function hideLogin(){$('#login').classList.add('hidden');}

/* Единственная дверь в приложение — и после входа, и на старте.

   До этого таких дверей было две: `boot()` со своей цепочкой и `enterApp()`
   со своей. Расходились они молча и уже расходились: правку в одной забывали
   перенести в другую, и «после логина» вело себя не так, как «после
   перезагрузки страницы».

   Сначала — экран (`route()`), потом всё остальное. Порядок здесь не про
   скорость: пока `route()` стоял последним, любая осечка выше означала, что
   человек не увидит НИЧЕГО, и отличить это от повисшего сервиса было нельзя.
   Каждый шаг-украшение обёрнут `safely`: он может не получиться, но не может
   отменить экран. */
async function enterApp(me){
  state.me=me;hideLogin();
  safely('paintUser',paintUser);
  if(!location.hash||location.hash.startsWith('#/join'))location.hash='#/runs';
  route();
  safely('team',async()=>applyTeam(await api('/api/team').catch(()=>null)));
  safely('counts',refreshCounts);
  safely('rig',loadRig);
  safely('collab',startCollab);
  safely('palette',initPalette);
}

$('#loginForm').onsubmit=async e=>{
  e.preventDefault();$('#lgMsg').textContent='';$('#lgOk').textContent='';
  const btn=$('#lgSubmit');btn.disabled=true;
  const body={login:$('#lgLogin').value.trim(),password:$('#lgPass').value,
              name:$('#lgName').value.trim(),token:$('#lgToken').value.trim()};
  const headers={'Content-Type':'application/json'};
  try{
    if(loginMode==='register'){
      const r=await api('/api/auth/register',
        {method:'POST',headers,body:JSON.stringify(body)});
      /* Заявка принята — и человеку надо сказать, что дальше ничего не
         произойдёт само. Молча вернуть его к форме входа значило бы отправить
         пробовать пароль, который ещё не работает. */
      $('#lgOk').textContent=(r&&r.message)||'The request is in.';
      setTimeout(()=>{const t=$('#lgOk').textContent;setLoginMode('signin');
                      $('#lgOk').textContent=t;},1400);
      return;
    }
    const path=loginMode==='setup'?'/api/auth/setup':'/api/auth/login';
    const me=await api(path,{method:'POST',headers,body:JSON.stringify(body)});
    if(loginMode==='setup')authState.needs_setup=false;
    await enterApp(me);
  }catch(err){$('#lgMsg').textContent=String(err.message||err);}
  finally{btn.disabled=false;}
};
/* ------- роли ------- */
/* Кнопка, которую бэкенд заведомо отвергнет, — это не «строгий бэкенд», это
   неправда в интерфейсе: человек нажимает и получает голый 403 без единой
   подсказки, что делать. Так вели себя массовая чистка прогонов и удаление
   эталона (обе требуют admin) и вся вкладка «Projects».
   Проверка здесь — про честность экрана, а не про безопасность: настоящая
   проверка стоит на сервере и никуда не делась. */
const ROLE_RANK={viewer:0,reviewer:1,admin:2};
/* Второй аргумент — проект, над которым действует кнопка. Роль может быть
   выдана не на всю инсталляцию, а на один набор: глобально человек `viewer`,
   а в своём проекте `reviewer`. Без учёта проекта интерфейс прятал бы от него
   ровно те кнопки, которыми он и должен пользоваться, — и права выглядели бы
   невыданными. Действующая роль здесь считается так же, как на сервере:
   максимум из глобальной и выданной. */
function can(role,project){
  const me=state.me||{};
  let mine=me.role||'viewer';
  const key=(project||'').trim();
  if(key&&me.grants&&me.grants[key]&&
     (ROLE_RANK[me.grants[key]]??-1)>(ROLE_RANK[mine]??-1))mine=me.grants[key];
  return (ROLE_RANK[mine]??-1)>=(ROLE_RANK[role]??99);
}
/* Набор эталонов → проект, на который спрашивается право.

   Собственный набор сервиса (`global`) тоже чей-то: его проект приезжает в
   `/api/auth/me` как `own_project`. Иначе «свой набор» был бы единственным
   местом, где интерфейс не знает, чьё это, — и гасил бы кнопки у человека, у
   которого право как раз есть. */
function scopeProject(scope){
  const s=String(scope||'').trim();
  if(s.startsWith('project:'))return s.slice('project:'.length);
  return (state.me&&state.me.own_project)||'';
}
/* Кнопка только для нужной роли: остальным она не показывается вовсе.
   Вернуть `null` честнее, чем показать disabled: «у вас нет прав» на кнопке,
   которой человек и не собирался пользоваться, — просто шум на экране. */
function roleButton(role,cls,label,onclick,why,project){
  if(!can(role,project))return null;
  const b=el('button',cls,label);
  if(why)b.title=why;
  b.onclick=onclick;
  return b;
}

function paintUser(){
  const me=state.me||{};const nm=me.name||me.login||'?';
  const initials=(nm.match(/\b\p{L}/gu)||[nm[0]||'?']).slice(0,2).join('').toUpperCase();
  $('#avatar').textContent=initials;
  $('#avatar').title=(me.anonymous?'local mode — ':'')+nm+' · '+(me.role||'');
}
$('#avatar').onclick=e=>{
  closeMenus();const me=state.me||{};
  const m=el('div','menu');m.style.top='58px';m.style.right='26px';
  m.innerHTML=`<div class="mi-head">${esc(me.name||me.login||'—')} · ${esc(me.role||'')}</div>`;
  if(me.anonymous){
    m.append(mkItem('Local mode (no sign-in)',null,true));
    /* Единственный способ выйти из локального режима лежал в командной строке
       на машине сервиса. Здесь он в одно нажатие — и это тот же экран, что
       видит человек, раскативший сервис по сети. */
    if(authState.needs_setup)m.append(mkDiv(),mkItem('Create an administrator…',
      async()=>{await showLogin();setLoginMode('setup');$('#lgName').focus();}));
  }
  else{
    const pw=mkItem('Change password',changePassword);const lo=mkItem('Sign out',doLogout);
    m.append(pw,mkDiv(),lo);
  }
  document.body.append(m);stopClose(m);
};
/* Пункт меню и разделитель.

   Классы здесь не украшение. В стилях с самого начала описаны `.menu .mi`,
   `.menu .mi.off` и `.menu .div`, а собирались пункты голыми `<button>` и
   `<div class="divider">` — то есть ни одно из этих правил не применялось ни
   разу. Меню выходило столбиком системных кнопок с чужим шрифтом, без
   подсветки под курсором и без единого видимого разделителя между «прогнать
   в firefox» и «прогнать во всех». */
function mkItem(label,fn,disabled){
  const b=el('button','mi',esc(label));
  if(disabled){b.disabled=true;b.classList.add('off');}
  else b.onclick=()=>{closeMenus();fn&&fn();};
  return b;
}
function mkDiv(){return el('div','div');}
async function doLogout(){try{await api('/api/auth/logout',{method:'POST'});}catch{}location.reload();}
async function changePassword(){
  const oldp=prompt('Current password');if(oldp==null)return;const np=prompt('New password');if(!np)return;
  try{await api('/api/auth/password',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({old:oldp,new:np})});toast('Password changed','ok');}
  catch(e){toast(String(e.message||e),'err');}
}
function closeMenus(){$$('.menu').forEach(m=>m.remove());}
function stopClose(m){m.addEventListener('click',e=>e.stopPropagation());}
document.addEventListener('click',closeMenus);

