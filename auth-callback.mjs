import crypto from 'node:crypto';
import { encryptSession, getCookie, cookie } from './_session.mjs';
export default async (req) => {
  const url = new URL(req.url);
  const code = url.searchParams.get('code');
  const state = url.searchParams.get('state');
  const storedState = getCookie({ headers: Object.fromEntries(req.headers) }, 'iw_oauth_state');
  const origin = process.env.URL || url.origin;
  const fail = (msg) => new Response(`<h1>Anmeldung fehlgeschlagen</h1><p>${msg}</p><p><a href="/">Zurück zu IronWolf</a></p>`, { status: 400, headers: { 'Content-Type': 'text/html; charset=utf-8', 'Set-Cookie': 'iw_oauth_state=; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=0' } });
  if (!code || !state || !storedState || Buffer.byteLength(state) !== Buffer.byteLength(storedState) || !crypto.timingSafeEqual(Buffer.from(state), Buffer.from(storedState))) return fail('Ungültiger oder abgelaufener OAuth-Status. Bitte erneut anmelden.');
  const clientId = process.env.DISCORD_CLIENT_ID;
  const clientSecret = process.env.DISCORD_CLIENT_SECRET;
  const redirectUri = process.env.DISCORD_REDIRECT_URI;
  if (!clientId || !clientSecret || !redirectUri || (process.env.SESSION_SECRET || '').length < 32) return fail('Die OAuth-Umgebungsvariablen sind nicht vollständig konfiguriert.');
  try {
    const tokenRes = await fetch('https://discord.com/api/oauth2/token', { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: new URLSearchParams({ client_id: clientId, client_secret: clientSecret, grant_type: 'authorization_code', code, redirect_uri: redirectUri }) });
    const token = await tokenRes.json();
    if (!tokenRes.ok || !token.access_token) return fail('Discord hat den Token-Austausch abgelehnt. Prüfe Redirect URI und Client-Daten.');
    const userRes = await fetch('https://discord.com/api/users/@me', { headers: { Authorization: `Bearer ${token.access_token}` } });
    const user = await userRes.json();
    if (!userRes.ok) return fail('Dein Discord-Profil konnte nicht gelesen werden.');
    const session = encryptSession({ user: { id: user.id, username: user.global_name || user.username, avatar: user.avatar }, accessToken: token.access_token, refreshToken: token.refresh_token, tokenExpiresAt: Date.now() + (token.expires_in || 3600) * 1000, expiresAt: Date.now() + 8 * 60 * 60 * 1000 });
    return new Response(null, { status: 302, headers: { Location: `${origin}/dashboard.html`, 'Set-Cookie': cookie('iw_session', session, 28800), 'Cache-Control': 'no-store' } });
  } catch (error) { console.error('OAuth callback error:', error?.message || 'unknown'); return fail('Verbindungsfehler bei der Discord-Anmeldung.'); }
};
