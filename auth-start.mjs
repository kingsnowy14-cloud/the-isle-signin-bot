import crypto from 'node:crypto';
import { cookie } from './_session.mjs';
export default async (req) => {
  const id = process.env.DISCORD_CLIENT_ID;
  const redirect = process.env.DISCORD_REDIRECT_URI;
  if (!id || !redirect) return new Response('Bitte DISCORD_CLIENT_ID und DISCORD_REDIRECT_URI in Netlify setzen.', { status: 503 });
  const state = crypto.randomBytes(24).toString('base64url');
  const url = new URL('https://discord.com/oauth2/authorize');
  url.searchParams.set('client_id', id);
  url.searchParams.set('response_type', 'code');
  url.searchParams.set('redirect_uri', redirect);
  url.searchParams.set('scope', 'identify guilds');
  url.searchParams.set('state', state);
  url.searchParams.set('prompt', 'consent');
  return new Response(null, { status: 302, headers: { Location: url.toString(), 'Set-Cookie': cookie('iw_oauth_state', state, 600), 'Cache-Control': 'no-store' } });
};
