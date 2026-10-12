export default async () => {
  const clientId = process.env.DISCORD_CLIENT_ID || '';
  const permissions = process.env.DISCORD_BOT_PERMISSIONS || '0';
  const inviteUrl = clientId ? `https://discord.com/oauth2/authorize?client_id=${encodeURIComponent(clientId)}&scope=bot%20applications.commands&permissions=${encodeURIComponent(permissions)}` : null;
  return Response.json({ inviteUrl, clientId });
};
