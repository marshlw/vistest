/* Запуск: что спрашивается до первого экрана и в каком порядке.

   Идёт последним и обязан идти последним: здесь вызывается всё, что определено
   выше. Это второе и последнее правило порядка подключения. */
async function boot(){
  /* Раньше всего остального — до навигации и до первого запроса. Если сломано
     что-то из подключённого выше, услышать об этом надо, а не гадать. */
  installErrorSurface();
  buildNav();
  const joinMatch=location.hash.match(/^#\/join\/([^/]+)/);

  /* Состояние двери спрашивается ДО первого запроса за данными.

     Раньше порядок был обратный: интерфейс шёл в `/api/auth/me`, на свежей
     инсталляции получал «local mode, роль admin» и спокойно шёл дальше — а
     дальше первый же роут с данными отвечал 503, и человек видел стену
     «зайдите на машину сервиса». Стена стояла не зря, но выйти из-за неё
     изнутри было нельзя. */
  try{authState=await api('/api/auth/state');}catch{authState={};}
  if(authState.needs_setup&&!authState.local_mode){
    if(joinMatch){renderJoin(joinMatch[1]);return;}
    await showLogin();return;
  }

  try{state.me=await api('/api/auth/me');}catch(e){/* 401 is handled */}
  if(joinMatch){renderJoin(joinMatch[1]);return;}
  if(!state.me){await showLogin();return;}
  await enterApp(state.me);
}

boot();
