"""IronWolf add-on tools: player files, team roles, security alerts, events and control."""
import json
import time
from datetime import datetime, timezone
from storage import data_path
import discord
from discord import app_commands
from discord.ext import commands

AKTEN = data_path("akten.json")
EVENTS = data_path("ironwolf_events.json")
SECURITY = data_path("security_state.json")

def read(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default

def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

def admin():
    return app_commands.checks.has_permissions(manage_guild=True)

class PlayerFiles(commands.Cog):
    def __init__(self, bot): self.bot = bot

    @app_commands.command(name="akte", description="Fügt einen Eintrag zur Spielerakte hinzu")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(moderate_members=True)
    async def akte(self, interaction: discord.Interaction, mitglied: discord.Member, grund: str):
        if mitglied.bot or mitglied.id == interaction.user.id:
            await interaction.response.send_message("Bitte wähle ein anderes menschliches Mitglied.", ephemeral=True); return
        db = read(AKTEN, {})
        records = db.setdefault(f"{interaction.guild_id}:{mitglied.id}", [])
        records.append({"member_id": mitglied.id, "moderator_id": interaction.user.id, "reason": grund[:1000], "created_at": datetime.now(timezone.utc).isoformat()})
        write(AKTEN, db)
        await interaction.response.send_message(f"📁 Akteneintrag #{len(records)} für {mitglied.mention} gespeichert.", ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(name="akte-ansehen", description="Zeigt die letzten Einträge einer Spielerakte")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(moderate_members=True)
    async def akte_ansehen(self, interaction: discord.Interaction, mitglied: discord.Member):
        records = read(AKTEN, {}).get(f"{interaction.guild_id}:{mitglied.id}", [])
        if not records:
            await interaction.response.send_message("Für dieses Mitglied gibt es keine Akteneinträge.", ephemeral=True); return
        lines = [f"#{i} · {r.get('created_at', '')[:10]} — {discord.utils.escape_markdown(r.get('reason', 'Kein Grund'))}" for i, r in enumerate(records[-10:], max(1, len(records)-9))]
        await interaction.response.send_message(embed=discord.Embed(title=f"📁 Spielerakte · {mitglied}", description="\n".join(lines)[:4000], color=discord.Color.dark_teal()), ephemeral=True)

class TeamTools(commands.Cog):
    def __init__(self, bot): self.bot = bot

    @app_commands.command(name="team-rolle", description="Vergibt oder entfernt eine Teamrolle")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_roles=True)
    async def team_rolle(self, interaction: discord.Interaction, mitglied: discord.Member, rolle: discord.Role, vergeben: bool):
        if rolle.is_default() or rolle.managed or rolle >= interaction.guild.me.top_role:
            await interaction.response.send_message("Diese Rolle kann der Bot aufgrund der Rollen-Hierarchie nicht verwalten.", ephemeral=True); return
        try:
            if vergeben:
                await mitglied.add_roles(rolle, reason=f"Teamverwaltung durch {interaction.user}")
                msg = f"{rolle.name} wurde vergeben."
            else:
                await mitglied.remove_roles(rolle, reason=f"Teamverwaltung durch {interaction.user}")
                msg = f"{rolle.name} wurde entfernt."
        except discord.Forbidden:
            msg = "Discord hat die Änderung abgelehnt. Prüfe Bot-Rechte und Rollen-Hierarchie."
        await interaction.response.send_message(msg, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(name="team-info", description="Zeigt die Mitgliederzahl einer Teamrolle")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_roles=True)
    async def team_info(self, interaction: discord.Interaction, rolle: discord.Role):
        members = [m.mention for m in rolle.members[:35]]
        await interaction.response.send_message(embed=discord.Embed(title=f"👥 Team · {rolle.name}", description="\n".join(members) or "Keine Mitglieder mit dieser Rolle.", color=discord.Color.blurple()).add_field(name="Mitglieder", value=str(len(rolle.members))), ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

class SecurityTools(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.joins = {}

    async def notify(self, guild, text):
        cfg = read(SECURITY, {}).get(str(guild.id), {})
        channel = guild.get_channel(cfg.get("log_channel_id")) if cfg.get("log_channel_id") else None
        if isinstance(channel, discord.TextChannel):
            try: await channel.send(text, allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException: pass

    @commands.Cog.listener()
    async def on_member_join(self, member):
        if member.bot: return
        now = time.time()
        recent = [t for t in self.joins.get(member.guild.id, []) if now-t < 20] + [now]
        self.joins[member.guild.id] = recent
        cfg = read(SECURITY, {}).get(str(member.guild.id), {})
        threshold = max(3, min(50, int(cfg.get("raid_threshold", 8))))
        if cfg.get("anti_raid_enabled", True) and len(recent) >= threshold:
            await self.notify(member.guild, f"⚠️ Raid-Warnung: {len(recent)} Beitritte innerhalb von 20 Sekunden. Bitte Server-Aktivität prüfen.")

    @app_commands.command(name="security-konfigurieren", description="Setzt Security-Logkanal und Raid-Warnschwelle")
    @app_commands.guild_only()
    @admin()
    async def security_config(self, interaction: discord.Interaction, log_kanal: discord.TextChannel, raid_schwelle: app_commands.Range[int, 3, 50] = 8, anti_raid: bool = True):
        db = read(SECURITY, {})
        db[str(interaction.guild_id)] = {"log_channel_id": log_kanal.id, "raid_threshold": raid_schwelle, "anti_raid_enabled": anti_raid}
        write(SECURITY, db)
        await interaction.response.send_message(f"🛡️ Logs: {log_kanal.mention} · Anti-Raid: {'AN' if anti_raid else 'AUS'} · Schwelle: {raid_schwelle}/20 Sekunden.", ephemeral=True)

    @app_commands.command(name="ironwolf-security-status", description="Zeigt die IronWolf-Security-Konfiguration")
    @app_commands.guild_only()
    @admin()
    async def security_status(self, interaction: discord.Interaction):
        cfg = read(SECURITY, {}).get(str(interaction.guild_id), {})
        channel = interaction.guild.get_channel(cfg.get("log_channel_id")) if cfg.get("log_channel_id") else None
        await interaction.response.send_message(f"Anti-Raid: {'AN' if cfg.get('anti_raid_enabled', True) else 'AUS'}\nSchwelle: {cfg.get('raid_threshold', 8)} Beitritte/20 Sekunden\nLogkanal: {channel.mention if channel else 'Nicht konfiguriert'}", ephemeral=True)

class EventTools(commands.Cog):
    def __init__(self, bot): self.bot = bot

    @app_commands.command(name="event-erstellen", description="Erstellt und veröffentlicht ein Serverevent")
    @app_commands.guild_only()
    @admin()
    async def event_create(self, interaction: discord.Interaction, titel: str, zeit: str, kanal: discord.TextChannel, beschreibung: str = ""):
        try: start = datetime.strptime(zeit.strip(), "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        except ValueError:
            await interaction.response.send_message("Zeitformat: JJJJ-MM-TT HH:MM (UTC).", ephemeral=True); return
        if start <= datetime.now(timezone.utc):
            await interaction.response.send_message("Das Event muss in der Zukunft liegen.", ephemeral=True); return
        db = read(EVENTS, {})
        record = {"title": titel[:200], "description": beschreibung[:1500], "starts_at": start.isoformat(), "channel_id": kanal.id, "created_by": interaction.user.id}
        db.setdefault(str(interaction.guild_id), []).append(record); write(EVENTS, db)
        embed = discord.Embed(title=f"📅 {record['title']}", description=record["description"] or "Keine weiteren Details.", color=discord.Color.green())
        embed.add_field(name="Beginn", value=f"<t:{int(start.timestamp())}:F> · <t:{int(start.timestamp())}:R>")
        try: await kanal.send(embed=embed)
        except discord.Forbidden:
            await interaction.response.send_message("Event gespeichert, aber im Zielkanal fehlen Senderechte.", ephemeral=True); return
        await interaction.response.send_message(f"Event gespeichert und in {kanal.mention} veröffentlicht.", ephemeral=True)

    @app_commands.command(name="events", description="Listet kommende IronWolf-Events")
    @app_commands.guild_only()
    async def events_list(self, interaction: discord.Interaction):
        items = []
        for r in read(EVENTS, {}).get(str(interaction.guild_id), []):
            try:
                start = datetime.fromisoformat(r["starts_at"])
                if start > datetime.now(timezone.utc): items.append((start, r))
            except (KeyError, TypeError, ValueError): pass
        items.sort(key=lambda x: x[0])
        if not items:
            await interaction.response.send_message("Keine zukünftigen Events gespeichert.", ephemeral=True); return
        lines = [f"• **{discord.utils.escape_markdown(r['title'])}** — <t:{int(t.timestamp())}:F>" for t, r in items[:10]]
        await interaction.response.send_message(embed=discord.Embed(title="📅 Kommende Events", description="\n".join(lines), color=discord.Color.green()), ephemeral=True)

class ControlTools(commands.Cog):
    def __init__(self, bot): self.bot = bot

    @app_commands.command(name="control", description="Zeigt geladene IronWolf-Module und Serverstatus")
    @app_commands.guild_only()
    @admin()
    async def control(self, interaction: discord.Interaction):
        names = sorted(self.bot.cogs.keys())
        embed = discord.Embed(title="🎛️ IronWolf Control Center", description="\n".join("• " + n for n in names)[:1000] or "Keine Module", color=discord.Color.blurple())
        embed.add_field(name="Server", value=interaction.guild.name)
        embed.add_field(name="Mitglieder", value=str(interaction.guild.member_count or 0))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="control-daten", description="Prüft, welche Bot-Datendateien vorhanden sind")
    @app_commands.guild_only()
    @admin()
    async def control_data(self, interaction: discord.Interaction):
        paths = [AKTEN, EVENTS, SECURITY, data_path("config.json"), data_path("data.json"), data_path("economy.json")]
        await interaction.response.send_message("**Datenstatus**\n" + "\n".join(f"{p.name}: {'vorhanden' if p.exists() else 'noch nicht erstellt'}" for p in paths), ephemeral=True)

async def setup(bot):
    await bot.add_cog(PlayerFiles(bot))
    await bot.add_cog(TeamTools(bot))
    await bot.add_cog(SecurityTools(bot))
    await bot.add_cog(EventTools(bot))
    await bot.add_cog(ControlTools(bot))
