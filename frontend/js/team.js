/* VisTest - self-hosted visual regression testing.
 * Copyright (C) 2026 Kirill Kulagin
 * SPDX-License-Identifier: AGPL-3.0-or-later
 *
 * This file is part of VisTest. See LICENSE for the full terms and NOTICE for
 * the trademark and commercial-licensing terms. Removing this header does not
 * remove those obligations.
 */

/* Команда: профиль, пользователи, заявки на доступ, приглашения, аудит. */
function sectionH(t,note){const d=el('div','sect-h');d.innerHTML=`<h2>${esc(t)}</h2>`+(note?`<span class="note">${esc(note)}</span>`:'');return d;}
function errCard(e){return el('div','set-card','<div class="empty">'+esc(String(e&&e.message||e))+'</div>');}
function applyTeam(team){
  if(!team)return;state.team=team;
  document.title='VisTest'+(team.name?' · '+team.name:'')+' — visual regressions';
  if(team.name)$('.brand .sub').textContent=team.name.toUpperCase().slice(0,28);
  if(team.brand_color&&/^#[0-9a-fA-F]{6}$/.test(team.brand_color)){
    document.documentElement.style.setProperty('--accent',team.brand_color);
    $('.brand .mark').style.background=team.brand_color;
  }
}
async function renderSelfSettings(s){
  const team=await api('/api/team').catch(()=>({name:''}));
  const c=el('div','set-card');
  c.innerHTML=`<div class="muted" style="font-size:13px">Team: <b>${esc(team.name||'—')}</b> · you are signed in as <b>${esc(state.me.login)}</b> (${esc(state.me.role)}).</div>`;
  const b=el('button','btn dark','Change password');b.style.marginTop='12px';b.onclick=changePassword;c.append(b);
  s.append(c);
}
/* ------- заявки на доступ -------
   Регистрация открыта, но заявка ничего не даёт: пока администратор её не
   одобрил, войти по ней нельзя. Ровно поэтому открытая регистрация здесь
   безопасна — очередь к администратору это не доступ.

   Роль назначается прямо при одобрении, одним действием. «Одобрить как-нибудь,
   а потом сходить выставить роль» — это два шага, второй из которых забывают,
   и человек остаётся с правами, которых ему не давали осознанно. */
async function renderRequests(card){
  card.innerHTML='<div class="jobbar"><span class="spin"></span> loading…</div>';
  let data,conf;
  try{
    data=await api('/api/users/pending');
    conf=await api('/api/settings/registration');
  }catch(e){card.innerHTML='';card.append(el('div','empty',esc(String(e.message||e))));return;}

  card.innerHTML='';
  const head=el('div');
  head.style.cssText='display:flex;align-items:center;justify-content:space-between;gap:14px';
  head.innerHTML='<div class="muted" style="font-size:13px">Anyone can sign up, '
    +'but a request is not access: it is a queue to you. Nothing is granted '
    +'until you approve it and pick a role.</div>';
  const sw=chk('Open registration');
  sw.input.checked=!!conf.open;
  sw.wrap.style.cssText='display:flex;align-items:center;gap:8px;cursor:pointer;'
    +'font-weight:600;white-space:nowrap';
  sw.input.onchange=async()=>{
    try{
      await api('/api/settings/registration',{method:'PUT',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({open:sw.input.checked})});
      toast(sw.input.checked?'Registration is open':'Registration is closed','ok');
    }catch(e){toast(String(e.message||e),'err');sw.input.checked=!sw.input.checked;}
  };
  head.append(sw.wrap);card.append(head);

  const list=(data.pending||[]);
  if(!list.length){
    const empty=el('div','muted',conf.open
      ? 'No requests waiting. New sign-ups will appear here.'
      : 'Registration is closed — new people can only come in by invite.');
    empty.style.cssText='font-size:13px;margin-top:14px';
    card.append(empty);return;
  }

  const tbl=el('div');tbl.style.marginTop='12px';
  list.forEach(u=>{
    const row=el('div','env-row');
    row.style.gridTemplateColumns='1fr auto auto auto';
    row.innerHTML=`<div><div style="font-weight:600">${esc(u.name||u.login)}</div>
      <div class="mono" style="font-size:12px;color:var(--muted2)">${esc(u.login)} · ${esc(timeAgo(u.created_at))}</div></div>`;
    const role=el('select','inp');role.style.cssText='margin:0;width:130px';
    ['viewer','reviewer','admin'].forEach(r=>{
      const o=el('option',null,r);o.value=r;role.append(o);});
    const ok=el('button','btn dark sm','Approve');
    ok.onclick=async()=>{
      ok.disabled=true;
      try{
        await api(`/api/users/${encodeURIComponent(u.login)}/approve`,
          {method:'POST',headers:{'Content-Type':'application/json'},
           body:JSON.stringify({role:role.value})});
        toast(`${u.login} can sign in as ${role.value}`,'ok');
        renderRequests(card);refreshCounts();
      }catch(e){toast(String(e.message||e),'err');ok.disabled=false;}
    };
    const no=el('button','btn sm','Reject');
    no.title='The request is deleted, and the same person can apply again';
    no.onclick=async()=>{
      if(!confirm(`Reject the request from ${u.login}?`))return;
      try{
        await api(`/api/users/${encodeURIComponent(u.login)}/reject`,
                  {method:'POST'});
        renderRequests(card);refreshCounts();
      }catch(e){toast(String(e.message||e),'err');}
    };
    row.append(role,ok,no);tbl.append(row);
  });
  card.append(tbl);
}

async function renderTeamAdmin(s){
  // team profile
  const team=await api('/api/team').catch(()=>({name:'',brand_color:'#C2410C'}));
  s.append(sectionH('Team profile'));
  const pc=el('div','set-card');
  pc.innerHTML='<div class="muted" style="font-size:13px">Name and color — in the header and reports.</div>';
  const nm=el('input','inp');nm.value=team.name||'';nm.placeholder='e.g. Team shop-web';nm.style.margin='0';
  const col=el('input');col.type='color';col.value=/^#[0-9a-fA-F]{6}$/.test(team.brand_color||'')?team.brand_color:'#C2410C';
  col.style.cssText='width:46px;height:42px;border:1px solid var(--line);border-radius:10px;background:none;padding:2px;cursor:pointer';
  const save=el('button','btn dark','Save');
  save.onclick=async()=>{try{const r=await api('/api/team',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:nm.value.trim(),brand_color:col.value})});toast('Team profile saved','ok');applyTeam(r);}catch(e){toast(String(e.message||e),'err');}};
  const row=el('div');row.style.cssText='display:flex;gap:12px;align-items:end;margin-top:12px';
  row.append(labeled('Name',nm),labeled('Color',col),save);pc.append(row);s.append(pc);

  // access requests
  s.append(sectionH('Access requests','people who signed up and are waiting'));
  const rq=el('div','set-card');rq.id='requestsCard';await renderRequests(rq);
  s.append(rq);

  // people
  s.append(sectionH('Team','people with access to this instance'));
  let data;try{data=await api('/api/users');}catch(e){s.append(errCard(e));return;}
  const uc=el('div','set-card');
  const uh=el('div');uh.style.cssText='display:flex;align-items:center;justify-content:space-between';
  uh.innerHTML='<div class="muted" style="font-size:13px">The role defines what is allowed everywhere: '
    +'viewer — view, reviewer — accept and run, admin — everything. '
    +'A right on a single project is added next to the name and only ever raises the role.</div>';
  const addu=el('button','btn sm','Add');addu.onclick=addUserModal;uh.append(addu);uc.append(uh);
  const tbl=el('div');tbl.style.marginTop='10px';
  (data.users||[]).forEach(u=>tbl.append(userRow(u,data.roles||['viewer','reviewer','admin'],
                                                 data.projects||[])));
  uc.append(tbl);s.append(uc);

  // invites
  s.append(sectionH('Invites','a one-time link with a role'));
  const ic=el('div','set-card');ic.id='invitesCard';await renderInvites(ic);s.append(ic);

  // audit
  s.append(sectionH('Audit log'));
  const ac=el('div','set-card');
  const au=await api('/api/audit?limit=40').catch(()=>({entries:[]}));
  if(!(au.entries||[]).length)ac.append(el('div','muted','Empty'));
  (au.entries||[]).forEach(e=>{
    const r=el('div');r.style.cssText='display:grid;grid-template-columns:150px 130px 1fr;gap:12px;padding:8px 0;border-bottom:1px solid var(--line3);font-size:13px';
    r.innerHTML=`<span class="mono faint">${esc((e.at||'').replace('T',' ').slice(0,16))}</span><span class="mono">${esc(e.who)}</span><span>${esc(e.action)}${e.target?' · '+esc(e.target):''}</span>`;
    ac.append(r);
  });
  s.append(ac);
}
function userRow(u,roles,projects){
  const box=el('div');
  const r=el('div');r.style.cssText='display:grid;grid-template-columns:1fr auto auto auto;gap:12px;align-items:center;padding:11px 0 6px';
  const w=el('div');
  w.innerHTML=`<div><b>${esc(u.login)}</b>${u.name&&u.name!==u.login?' · '+esc(u.name):''}${u.active?'':' <span class="tag red">disabled</span>'}</div>
    <div class="faint" style="font-size:12px">${u.last_login?'sign in '+esc(String(u.last_login).slice(0,10)):'has not signed in yet'}</div>`;
  const sel=el('select','inp');sel.style.cssText='margin:0;width:auto;padding:6px 10px';
  roles.forEach(rr=>{const o=el('option');o.value=rr;o.textContent=rr;sel.append(o);});sel.value=u.role;
  sel.onchange=async()=>{try{await api('/api/users/'+encodeURIComponent(u.login),{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({role:sel.value})});toast('Role updated','ok');}catch(e){toast(String(e.message||e),'err');sel.value=u.role;}};
  const pw=el('button','btn sm','Password');pw.onclick=()=>resetPw(u.login);
  const dis=el('button','btn sm',u.active?'Disable':'disabled');dis.disabled=!u.active;
  dis.onclick=async()=>{if(!confirm('Disable the user '+u.login+'?'))return;try{await api('/api/users/'+encodeURIComponent(u.login),{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({active:false})});toast('Disabled','ok');SCREENS.settings();}catch(e){toast(String(e.message||e),'err');}};
  r.append(w,sel,pw,dis);
  box.append(r,projectRights(u,roles,projects||[]));
  box.style.borderBottom='1px solid var(--line3)';
  return box;
}

/* Права на отдельные проекты.

   Глобальная роль отвечает на «что человек может везде», и до подключения
   второго набора тестов этого хватало. Дальше `reviewer` стал означать «может
   переписать эталон ЛЮБОГО проекта», а утверждение необратимо: оно меняет то,
   с чем сравнивают дальше, и на экране свой набор от чужого не отличается.

   Право только ПОДНИМАЕТ роль в одном проекте, поэтому сценарий изоляции
   ровно один: глобально все viewer, а reviewer выдаётся точечно. Об этом
   сказано прямо в подсказке — иначе администратор выдаёт права поверх
   глобального reviewer и удивляется, что ничего не изменилось. */
function projectRights(u,roles,projects){
  const wrap=el('div','grants');
  const grants=u.grants||{};
  const keys=Object.keys(grants).sort();

  keys.forEach(k=>{
    const chip=el('span','grant');
    chip.innerHTML=`<span class="p">${esc(k)}</span><span class="r">${esc(grants[k])}</span>`;
    const x=el('button','btn sm','×');
    x.title='Revoke the right on '+k;
    x.setAttribute('aria-label',`Revoke ${grants[k]} on ${k} for ${u.login}`);
    x.onclick=async()=>{
      try{
        await api(`/api/users/${encodeURIComponent(u.login)}/rights/${encodeURIComponent(k)}`,
                  {method:'DELETE'});
        toast('Right revoked','ok');SCREENS.settings();
      }catch(e){toast(String(e.message||e),'err');}
    };
    chip.append(x);wrap.append(chip);
  });

  if(!projects.length){
    if(!keys.length)return wrap;
    return wrap;
  }

  const pick=el('select','inp');pick.style.cssText='margin:0;width:auto;padding:4px 8px;font-size:12px';
  pick.setAttribute('aria-label','Project to grant a right on');
  projects.filter(p=>!(p in grants)).forEach(p=>{
    const o=el('option');o.value=p;o.textContent=p;pick.append(o);});
  if(!pick.options.length){
    wrap.append(el('span','faint','every project already has a right'));
    return wrap;
  }
  const role=el('select','inp');role.style.cssText='margin:0;width:auto;padding:4px 8px;font-size:12px';
  roles.forEach(rr=>{const o=el('option');o.value=rr;o.textContent=rr;role.append(o);});
  role.value='reviewer';
  const add=el('button','btn sm','+ right');
  add.title='Raise this person to the chosen role in one project only. '
    +'A right never lowers the global role.';
  add.onclick=async()=>{
    try{
      await api(`/api/users/${encodeURIComponent(u.login)}/rights/${encodeURIComponent(pick.value)}`,
        {method:'PUT',headers:{'Content-Type':'application/json'},
         body:JSON.stringify({role:role.value})});
      toast('Right granted','ok');SCREENS.settings();
    }catch(e){toast(String(e.message||e),'err');}
  };
  wrap.append(pick,role,add);
  return wrap;
}
async function resetPw(login){
  const np=prompt('New password for '+login+' (at least 8 characters)');if(!np)return;
  try{await api('/api/users/'+encodeURIComponent(login),{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:np})});toast('Password reset','ok');}
  catch(e){toast(String(e.message||e),'err');}
}
function addUserModal(){
  const wrap=el('div');
  wrap.innerHTML='<div class="mhead"><h3>Add a user</h3><button class="mclose" aria-label="Close">×</button></div><div class="msub">An account with a password. For self sign-up an invite is better — then only the person knows the password.</div>';
  const login=el('input','inp');login.placeholder='login';
  const name=el('input','inp');name.placeholder='name (optional)';
  const role=el('select','inp');['viewer','reviewer','admin'].forEach(x=>{const o=el('option');o.value=x;o.textContent=x;role.append(o);});role.value='viewer';
  const pass=el('input','inp');pass.type='password';pass.placeholder='password (≥8)';
  wrap.append(labeled('Login',login),labeled('Name',name),labeled('Role',role),labeled('Password',pass));
  const acts=el('div','macts');const go=el('button','btn dark','Create');const c=el('button','btn','Cancel');c.onclick=closeModal;acts.append(go,c);wrap.append(acts);
  openModal(wrap);$('.mclose',wrap).onclick=closeModal;setTimeout(()=>login.focus(),40);
  go.onclick=async()=>{try{await api('/api/users',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({login:login.value.trim(),name:name.value.trim(),role:role.value,password:pass.value})});toast('User created','ok');closeModal();SCREENS.settings();}catch(e){toast(String(e.message||e),'err');}};
}
async function renderInvites(box){
  box.innerHTML='';
  const head=el('div');head.style.cssText='display:flex;align-items:center;justify-content:space-between;gap:12px';
  head.innerHTML='<div class="muted" style="font-size:13px">Create a link, send it to the person — they set their own login and password.</div>';
  const role=el('select','inp');role.style.cssText='margin:0;width:auto;padding:6px 10px';['viewer','reviewer','admin'].forEach(x=>{const o=el('option');o.value=x;o.textContent=x;role.append(o);});role.value='reviewer';
  const add=el('button','btn dark sm','Create a link');
  const ctr=el('div');ctr.style.cssText='display:flex;gap:8px;align-items:center';ctr.append(role,add);head.append(ctr);box.append(head);
  const list=el('div');list.style.marginTop='12px';box.append(list);
  add.onclick=async()=>{try{const inv=await api('/api/invites',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({role:role.value})});
    const url=inviteUrl(inv.token);navigator.clipboard&&navigator.clipboard.writeText(url).catch(()=>{});toast('Link created and copied','ok');renderInvites(box);}
    catch(e){toast(String(e.message||e),'err');}};
  const data=await api('/api/invites').catch(()=>({invites:[]}));
  const active=(data.invites||[]).filter(i=>i.status==='active');
  if(!active.length){list.append(el('div','muted','No active invites'));return;}
  active.forEach(i=>{
    const url=inviteUrl(i.token);
    const r=el('div');r.style.cssText='display:flex;gap:10px;align-items:center;padding:9px 0;border-bottom:1px solid var(--line3)';
    r.innerHTML=`<span class="tag purple">${esc(i.role)}</span><span class="mono faint" style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px">${esc(url)}</span>`;
    const copy=el('button','btn sm','Copy');copy.onclick=()=>{navigator.clipboard&&navigator.clipboard.writeText(url).then(()=>toast('Copied','ok'));};
    const rev=el('button','btn sm','Revoke');rev.onclick=async()=>{try{await api('/api/invites/'+encodeURIComponent(i.token),{method:'DELETE'});}catch(e){}renderInvites(box);};
    r.append(copy,rev);list.append(r);
  });
}
function inviteUrl(token){return location.origin+location.pathname+'#/join/'+token;}

/* ------- sign in by invite ------- */
async function renderJoin(token){
  closeMenus();closeModal();
  const prev=$('#joinOverlay');if(prev)prev.remove();
  let info;try{info=await api('/api/auth/invite/'+encodeURIComponent(token));}catch(e){info={valid:false};}
  const bg=el('div');bg.id='joinOverlay';
  bg.style.cssText='position:fixed;inset:0;background:var(--bg);z-index:210;display:flex;align-items:center;justify-content:center;padding:24px';
  const box=el('div','card');box.style.cssText='width:390px;padding:28px';
  if(!info.valid){
    box.innerHTML='<h3 style="font-size:19px">The link is invalid</h3><p class="muted" style="margin-top:8px;font-size:14px">The invite has expired or has already been used. Ask an administrator to create a new one.</p>';
    const b=el('button','btn dark','To the sign-in page');b.style.cssText='margin-top:16px;width:100%;justify-content:center';
    b.onclick=()=>{location.hash='';location.reload();};box.append(b);bg.append(box);document.body.append(bg);return;
  }
  box.innerHTML=`<div style="font-size:11px;letter-spacing:1.5px;color:var(--muted2);font-weight:700">INVITE${info.team?' · '+esc(info.team.toUpperCase()):''}</div>
    <h3 style="font-size:21px;margin-top:6px">Join the team</h3>
    <p class="muted" style="font-size:13px;margin-top:6px">Role: <b>${esc(info.role)}</b>. Choose a login and password — this becomes your account.</p>`;
  const login=el('input','inp');login.placeholder='login';login.style.marginTop='14px';login.autocomplete='username';
  const name=el('input','inp');name.placeholder='name (optional)';
  const pass=el('input','inp');pass.type='password';pass.placeholder='password (at least 8 characters)';pass.autocomplete='new-password';
  const msg=el('div');msg.style.cssText='color:var(--fail);font-size:13px;min-height:16px;margin-top:10px';
  const go=el('button','btn dark','Create an account and sign in');go.style.cssText='width:100%;justify-content:center;margin-top:4px';
  box.append(labeled('Login',login),labeled('Name',name),labeled('Password',pass),msg,go);
  bg.append(box);document.body.append(bg);setTimeout(()=>login.focus(),40);
  go.onclick=async()=>{msg.textContent='';
    try{await api('/api/auth/accept-invite',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token,login:login.value.trim(),name:name.value.trim(),password:pass.value})});
      location.hash='#/runs';location.reload();}
    catch(e){msg.textContent=String(e.message||e);}};
}

