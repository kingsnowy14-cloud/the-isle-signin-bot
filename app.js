const $ = (s) => document.querySelector(s);
$('#year').textContent = new Date().getFullYear();
const fallbackInvite = 'https://discord.com/oauth2/authorize?client_id=YOUR_DISCORD_CLIENT_ID&scope=bot%20applications.commands&permissions=8';
async function loadConfig(){
  try { const r=await fetch('/api/config',{cache:'no-store'}); if(r.ok){const c=await r.json(); if(c.inviteUrl) $('#inviteButton').href=c.inviteUrl; return;} } catch {}
  $('#inviteButton').href=fallbackInvite;
}
const icons={moderation:'⚔',logs:'▤','anti-raid':'⛨','anti-nuke':'⬡',tickets:'▣',automod:'⌁',welcome:'✦',levels:'↗',music:'♫',custom:'✧','core-commands':'⌘'};
function renderModules(data){
  const modules=(data.modules||[]).filter(m=>m && m.name && m.enabled!==false);
  const total=Number.isFinite(Number(data.totalCommands))?Number(data.totalCommands):modules.reduce((sum,m)=>sum+(Number(m.commandCount)||0),0);
  $('#moduleCount').textContent=`${modules.length} Module · ${total} Befehle`;
  $('#moduleGrid').innerHTML=modules.length?modules.map((m,index)=>{
    const commands=Array.isArray(m.commands)?m.commands:[];
    const commandList=commands.length?`<details class="module-commands"><summary>Befehle anzeigen (${Number(m.commandCount)||commands.length})</summary><ul>${commands.map(c=>`<li><code>${escapeHtml(c.name||'Befehl')}</code>${c.description?`<span>${escapeHtml(c.description)}</span>`:''}</li>`).join('')}</ul></details>`:'';
    const status=m.detected===false?'WARTET AUF BEFEHLE':'● ERKANNT';
    return `<article class="module-card"><div class="module-icon">${icons[m.id]||'◇'}</div><h3>${escapeHtml(m.name)}</h3><p>${escapeHtml(m.description||'Ein IronWolf-Modul für deinen Discord-Server.')}</p><div class="module-meta"><span class="tag">${status}</span><span class="command-count">${Number(m.commandCount)||0} Befehle</span></div>${commandList}</article>`;
  }).join(''):'<article class="module-card"><h3>Module momentan nicht erreichbar</h3><p>Die Website konnte keine Modulliste laden. Prüfe BOT_API_URL in den Netlify-Umgebungsvariablen und den Status der Railway-API.</p></article>';
  const date=data.updatedAt?new Date(data.updatedAt):null;
  const stamp=date&&!Number.isNaN(date.valueOf())?`API aktualisiert: ${date.toLocaleString('de-AT')}`:'Modulübersicht · Aktualisierung automatisch alle 60 Sekunden';
  $('#moduleUpdated').textContent=`${stamp} · ${total} Befehle insgesamt`;
}
function escapeHtml(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
async function refreshModules(){
  try {const r=await fetch('/api/modules',{cache:'no-store'}); if(!r.ok) throw new Error('API unavailable'); renderModules(await r.json());}
  catch {try{const r=await fetch('/modules.json',{cache:'no-store'});const data=await r.json();renderModules({...data,warning:'Fallback-Datei'});}catch{renderModules({modules:[]});}}
}
loadConfig(); refreshModules(); setInterval(refreshModules,60000);
