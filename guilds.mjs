import { decryptSession, getCookie } from './_session.mjs';
export default async (req) => {
  const session = decryptSession(getCookie({ headers: Object.fromEntries(req.headers) }, 'iw_session'));
  if (!session) return Response.json({ error: 'Bitte zuerst mit Discord anmelden.' }, { status: 401, headers: { 'Cache-Control': 'no-store' } });
  try {
    const r = await fetch('https://discord.com/api/users/@me/guilds', { headers: { Authorization: `Bearer ${session.accessToken}` } });
    if (r.status === 401) return Response.json({ error: 'Discord-Sitzung abgelaufen. Bitte erneut anmelden.' }, { status: 401 });
    if (!r.ok) return Response.json({ error: 'Discord-Serverliste konnte nicht geladen werden.' }, { status: 502 });
    const guilds = await r.json();
    const manageable = guilds.filter(g => ((BigInt(g.permissions) & 0x8n) === 0x8n) || ((BigInt(g.permissions) & 0x20n) === 0x20n));
    return Response.json({ guilds: manageable.map(g => ({ id: g.id, name: g.name, icon: g.icon, owner: g.owner })) }, { headers: { 'Cache-Control': 'no-store' } });
  } catch (e) { console.error('Guild fetch error:', e?.message || 'unknown'); return Response.json({ error: 'Serverliste vorübergehend nicht verfügbar.' }, { status: 502 }); }
};
