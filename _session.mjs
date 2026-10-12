import crypto from 'node:crypto';
const secret = () => process.env.SESSION_SECRET || '';
export function encryptSession(payload) {
  if (secret().length < 32) throw new Error('SESSION_SECRET must be at least 32 characters');
  const key = crypto.createHash('sha256').update(secret()).digest();
  const iv = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv('aes-256-gcm', key, iv);
  const body = Buffer.concat([cipher.update(JSON.stringify(payload)), cipher.final()]);
  return [iv.toString('base64url'), cipher.getAuthTag().toString('base64url'), body.toString('base64url')].join('.');
}
export function decryptSession(token) {
  if (!token || secret().length < 32) return null;
  try {
    const [iv, tag, body] = token.split('.');
    const key = crypto.createHash('sha256').update(secret()).digest();
    const decipher = crypto.createDecipheriv('aes-256-gcm', key, Buffer.from(iv, 'base64url'));
    decipher.setAuthTag(Buffer.from(tag, 'base64url'));
    const plain = Buffer.concat([decipher.update(Buffer.from(body, 'base64url')), decipher.final()]).toString();
    const session = JSON.parse(plain);
    if (!session.expiresAt || session.expiresAt < Date.now()) return null;
    return session;
  } catch { return null; }
}
export function getCookie(event, name) {
  const raw = event.headers.cookie || event.headers.Cookie || '';
  const found = raw.split(';').map(v => v.trim()).find(v => v.startsWith(`${name}=`));
  return found ? decodeURIComponent(found.slice(name.length + 1)) : null;
}
export function cookie(name, value, maxAge) {
  return `${name}=${encodeURIComponent(value)}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=${maxAge}`;
}
export function clearCookie(name) { return `${name}=; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=0`; }
