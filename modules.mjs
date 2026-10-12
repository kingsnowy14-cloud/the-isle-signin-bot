export default async () => {
  const base = (process.env.BOT_API_URL || '').replace(/\/$/, '');
  if (base) {
    try {
      const response = await fetch(`${base}/api/modules`, { headers: { 'Accept': 'application/json' }, signal: AbortSignal.timeout(5000) });
      if (response.ok) {
        const data = await response.json();
        return Response.json(data, { headers: { 'Cache-Control': 'no-store' } });
      }
    } catch (error) {
      console.error('Bot module API unavailable:', error?.message || 'unknown error');
    }
  }
  return Response.json({ name: 'IronWolf', updatedAt: null, modules: [], warning: 'BOT_API_URL ist nicht konfiguriert oder die Bot-API ist nicht erreichbar.' }, { headers: { 'Cache-Control': 'no-store' } });
};
