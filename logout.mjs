import { clearCookie } from './_session.mjs';
export default async () => new Response(null, { status: 302, headers: { Location: '/', 'Set-Cookie': clearCookie('iw_session'), 'Cache-Control': 'no-store' } });
