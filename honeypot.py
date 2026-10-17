"""Eigenständiges IronWolf-Honeypot-Modul."""
import discord
from discord import app_commands
from discord.ext import commands
from datetime import timedelta

import bot as core


class HoneypotStatsView(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=300)
        self.guild_id = guild_id

    @discord.ui.button(label="Statistik aktualisieren", style=discord.ButtonStyle.secondary, emoji="🔄")
    async def refresh_stats(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild_id != self.guild_id:
            await interaction.response.send_message("Diese Statistik gehört zu einem anderen Server.", ephemeral=True)
            return
        await interaction.response.edit_message(embed=stats_embed(self.guild_id), view=self)


def stats_embed(guild_id: int) -> discord.Embed:
    stats = core.data.setdefault("honeypot_stats", {}).setdefault(str(guild_id), {"ban": 0, "kick": 0, "timeout": 0, "failed": 0})
    embed = discord.Embed(title="🍯 IronWolf · Honeypot-Statistik", color=discord.Color.orange())
    embed.add_field(name="Gebannt", value=str(stats.get("ban", 0)), inline=True)
    embed.add_field(name="Gekickt", value=str(stats.get("kick", 0)), inline=True)
    embed.add_field(name="Timeouts", value=str(stats.get("timeout", 0)), inline=True)
    embed.add_field(name="Fehlgeschlagen", value=str(stats.get("failed", 0)), inline=True)
    embed.set_footer(text="Statistik wird dauerhaft gespeichert")
    return embed


class Honeypot(commands.Cog):
    """Fallenkanal, Aktionen, Veröffentlichung und Statistik – unabhängig von anderen Cogs."""

    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return
        cfg = core.guild_config(message.guild.id)
        channel_id = cfg.get("honeypot_channel")
        if not cfg.get("honeypot_enabled", False) or not channel_id:
            return
        if message.channel.id != int(channel_id):
            return
        member = message.author
        if not isinstance(member, discord.Member) or member.id == message.guild.owner_id:
            return
        if member.guild_permissions.administrator:
            return

        action = str(cfg.get("honeypot_action", "kick")).strip().lower()
        if action not in {"ban", "kick", "timeout"}:
            action = "kick"
        stats = core.data.setdefault("honeypot_stats", {}).setdefault(
            str(message.guild.id), {"ban": 0, "kick": 0, "timeout": 0, "failed": 0}
        )
        reason = f"Honeypot ausgelöst in #{message.channel.name}"
        try:
            await message.delete()
        except (discord.Forbidden, discord.HTTPException):
            pass

        succeeded = False
        try:
            if action == "ban":
                await member.ban(reason=reason, delete_message_seconds=0)
            elif action == "timeout":
                minutes = max(1, min(40320, int(cfg.get("honeypot_timeout_minutes", 60))))
                await member.timeout(timedelta(minutes=minutes), reason=reason)
            else:
                await member.kick(reason=reason)
            stats[action] = int(stats.get(action, 0)) + 1
            succeeded = True
        except (discord.Forbidden, discord.HTTPException, ValueError, TypeError) as exc:
            stats["failed"] = int(stats.get("failed", 0)) + 1
            print(f"[HONEYPOT] {type(exc).__name__}: {exc}", flush=True)
        core.save_json(core.DATA_FILE, core.data)

        log_id = cfg.get("honeypot_log_channel") or cfg.get("security_log_channel") or cfg.get("log_channel_id")
        log_channel = message.guild.get_channel(int(log_id)) if log_id else None
        if isinstance(log_channel, discord.TextChannel):
            embed = discord.Embed(title="🍯 Honeypot ausgelöst", color=discord.Color.red())
            embed.add_field(name="Mitglied", value=f"{member} ({member.id})", inline=False)
            embed.add_field(name="Aktion", value=action.upper(), inline=True)
            embed.add_field(name="Ergebnis", value="Erfolgreich" if succeeded else "Fehlgeschlagen", inline=True)
            embed.add_field(name="Statistik", value=f"Ban: {stats.get('ban', 0)} · Kick: {stats.get('kick', 0)} · Timeout: {stats.get('timeout', 0)}", inline=False)
            try:
                await log_channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            except (discord.Forbidden, discord.HTTPException):
                pass

    @app_commands.command(name="honeypot_stats", description="Zeigt die Honeypot-Statistik dieses Servers.")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    async def honeypot_stats(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=stats_embed(interaction.guild_id), view=HoneypotStatsView(interaction.guild_id))

    @app_commands.command(name="honeypot_publish", description="Veröffentlicht die konfigurierte Honeypot-Nachricht.")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    async def honeypot_publish(self, interaction: discord.Interaction):
        cfg = core.guild_config(interaction.guild_id)
        channel_id = cfg.get("honeypot_channel")
        channel = interaction.guild.get_channel(int(channel_id)) if channel_id else None
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("Bitte zuerst im Honeypot-Dashboard einen Textkanal auswählen.", ephemeral=True)
            return
        message = str(cfg.get("honeypot_message") or "🍯 Honeypot aktiv. Bitte hier nicht schreiben.")[:1900]
        try:
            await channel.send(message, allowed_mentions=discord.AllowedMentions.none())
            await interaction.response.send_message(f"Honeypot-Nachricht wurde in {channel.mention} veröffentlicht.", ephemeral=True)
        except (discord.Forbidden, discord.HTTPException):
            await interaction.response.send_message("Nachricht konnte nicht gesendet werden. Bitte Kanalrechte prüfen.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(Honeypot(bot))
