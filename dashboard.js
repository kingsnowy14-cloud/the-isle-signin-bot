const statusEl=document.querySelector('#status'), grid=document.querySelector('#guildGrid');
async function run(){
 try{
  try { const configRes = await fetch('/api/config', {cache:'no-store'}); if (configRes.ok) { const config = await configRes.json(); window.IRONWOLF_CLIENT_ID = config.clientId || ''; window.IRONWOLF_INVITE_URL = config.inviteUrl || ''; } } catch {}
  const mr=await fetch('/api/me',{cache:'no-store'}); if(!mr.ok){statusEl.innerHTML='Du bist nicht angemeldet. <a class="accent" href="/api/auth-start">Mit Discord anmelden →</a>';return;}
  const me=await mr.json(); document.querySelector('#username').textContent=me.user.username; document.querySelector('#avatar').textContent=(me.user.username||'IW').slice(0,2).toUpperCase();
  const gr=await fetch('/api/guilds',{cache:'no-store'}); const data=await gr.json(); if(!gr.ok){statusEl.textContent=data.error||'Serverliste konnte nicht geladen werden.';return;}
  if(!data.guilds.length){statusEl.textContent='Keine Server mit Administrator- oder Server-Verwalten-Berechtigung gefunden.';return;}
  statusEl.textContent=`${data.guilds.length} Server gefunden. Die Einstellungen werden nach Verbindung der Bot-API hier verfügbar.`;
  grid.innerHTML=data.guilds.map(g=>`<article class="guild"><div class="avatar">${escapeHtml(g.name.slice(0,2).toUpperCase())}</div><h3>${escapeHtml(g.name)}</h3><p>Server-ID: ${escapeHtml(g.id)}</p><p class="warn">Bot-Konfiguration: API noch nicht verbunden</p><a class="button button-ghost" href="${escapeHtml(window.IRONWOLF_INVITE_URL || '#')}">Bot einladen ↗</a></article>`).join('');
 }catch{statusEl.textContent='Verbindungsfehler. Bitte lade die Seite neu.';}
}
function escapeHtml(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}run();
