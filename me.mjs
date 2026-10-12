import { decryptSession, getCookie } from './_session.mjs';
export default async (req) => {
  const session = decryptSession(getCookie({ headers: Object.fromEntries(req.headers) }, 'iw_session'));
  if (!session) return Response.json({ authenticated: false }, { status: 401, headers: { 'Cache-Control': 'no-store' } });
  return Response.json({ authenticated: true, user: session.user }, { headers: { 'Cache-Control': 'no-store' } });
};
