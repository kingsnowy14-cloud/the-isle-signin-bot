"""FiveM Roleplay tools for IronWolf: server status and Discord-side whitelist."""
import asyncio
import json
import re
import urllib.request
import urllib.error
from datetime import datetime, timezone

import discord
from discord import app_commands


def _normalise_code(value):
    value = str(value or "").strip()
    match = re.search(r"(?:cfx\.re/join/)?([a-zA-Z0-9]{4,12})$", value)
    return match.group(1) if match else None


def _fetch_server(code):
    url = "https://servers-frontend.fivem.net/api/servers/single/" + code
    request = urllib.request.Request(url, headers={"User-Agent": "IronWolf-FiveM/1.0", "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = json.loads(response.read(2_000_000).decode("utf-8"))
    data = payload.get("Data") or {}
    if not data:
        raise ValueError("Der Cfx.re-Server wurde nicht gefunden oder ist nicht öffentlich.")
    players = data.get("clients", 0)
    maximum = (data.get("vars") or {}).get("sv_maxClients", "?")
    return {
        "hostname": str(data.get("hostname") or "FiveM-Server")[:180],
        "players": int(players or 0),
        "maximum": str(maximum),
        "endpoint": (data.get("connectEndPoints") or [""])[0],
        "updated": datetime.now(timezone.utc),
    }


def setup_fivem(bot, guild_config, data, save_json, data_file):
    """Register FiveM slash commands; whitelist entries are stored per Discord guild."""

    @bot.tree.command(name="fivem-setup", description="Richtet die FiveM-Serververbindung für IronWolf ein")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(administrator=True)
    async def fivem_setup(interaction: discord.Interaction, cfx_code: str,
                          log_kanal: discord.TextChannel = None):
        code = _normalise_code(cfx_code)
        if not code:
            await interaction.response.send_message(
                "Ungültiger Cfx.re-Code. Nutze den Code aus cfx.re/join/… oder nur den Servercode.",
                ephemeral=True,
            )
            return
        cfg = guild_config(interaction.guild_id)
        cfg["fivem"] = {
            "cfx_code": code,
            "log_channel_id": log_kanal.id if log_kanal else None,
            "configured_at": datetime.now(timezone.utc).isoformat(),
        }
        save_json(bot.CONFIG_FILE, bot.config)
        await interaction.response.send_message(
            "FiveM-Verbindung gespeichert. Prüfe sie jetzt mit **/fivem-status**. "
            "Die Whitelist wird zunächst in IronWolf verwaltet und nicht automatisch an txAdmin/Framework übertragen.",
            ephemeral=True,
        )

    @bot.tree.command(name="fivem-status", description="Zeigt den Live-Status des konfigurierten FiveM-Servers")
    @app_commands.guild_only()
    async def fivem_status(interaction: discord.Interaction):
        cfg = guild_config(interaction.guild_id).get("fivem", {})
        code = cfg.get("cfx_code")
        if not code:
            await interaction.response.send_message("Noch nicht eingerichtet. Ein Administrator muss **/fivem-setup** ausführen.", ephemeral=True)
            return
        await interaction.response.defer(thinking=True)
        try:
            status = await asyncio.to_thread(_fetch_server, code)
        except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            await interaction.followup.send("FiveM-Status konnte nicht geladen werden: " + str(exc)[:250], ephemeral=True)
            return
        embed = discord.Embed(
            title="🎮 " + status["hostname"],
            description=f"**Spieler:** {status['players']} / {status['maximum']}\n**Cfx.re-Code:** `{code}`",
            color=discord.Color.green() if status["players"] else discord.Color.orange(),
            timestamp=status["updated"],
        )
        if status["endpoint"]:
            embed.add_field(name="Verbindungs-Endpunkt", value="`" + status["endpoint"][:100] + "`", inline=False)
        embed.set_footer(text="Live-Abfrage über die öffentliche Cfx.re-Serverliste")
        await interaction.followup.send(embed=embed)

    @bot.tree.command(name="fivem-whitelist", description="Verwaltet die FiveM-Whitelist in IronWolf")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.choices(aktion=[
        app_commands.Choice(name="Hinzufügen", value="add"),
        app_commands.Choice(name="Entfernen", value="remove"),
        app_commands.Choice(name="Anzeigen", value="list"),
    ])
    async def fivem_whitelist(interaction: discord.Interaction, aktion: app_commands.Choice[str],
                              steam_id: str = None, discord_user: discord.Member = None):
        guild_key = str(interaction.guild_id)
        store = data.setdefault("fivem_whitelist", {})
        entries = store.setdefault(guild_key, {})
        if aktion.value == "list":
            if not entries:
                await interaction.response.send_message("Die FiveM-Whitelist ist noch leer.", ephemeral=True)
                return
            lines = []
            for steam, item in list(entries.items())[-40:]:
                linked = item.get("discord_id")
                lines.append(f"• `{steam}`" + (f" — <@{linked}>" if linked else ""))
            await interaction.response.send_message("**FiveM-Whitelist (IronWolf)**\n" + "\n".join(lines)[:1800], ephemeral=True)
            return
        steam = re.sub(r"\s+", "", str(steam_id or ""))
        if not re.fullmatch(r"\d{15,20}", steam):
            await interaction.response.send_message("Bitte eine gültige SteamID64 (15–20 Ziffern) angeben.", ephemeral=True)
            return
        if aktion.value == "add":
            entries[steam] = {
                "steam_id": steam,
                "discord_id": discord_user.id if discord_user else None,
                "added_by": interaction.user.id,
                "added_at": datetime.now(timezone.utc).isoformat(),
            }
            save_json(data_file, data)
            await interaction.response.send_message(
                f"SteamID64 `{steam}` zur IronWolf-Whitelist hinzugefügt."
                + (f" Verknüpft mit {discord_user.mention}." if discord_user else ""),
                ephemeral=True,
            )
        else:
            if steam not in entries:
                await interaction.response.send_message("Diese SteamID64 steht nicht auf der Whitelist.", ephemeral=True)
                return
            entries.pop(steam, None)
            save_json(data_file, data)
            await interaction.response.send_message(f"SteamID64 `{steam}` wurde aus der IronWolf-Whitelist entfernt.", ephemeral=True)

    print("[FIVEM] FiveM Roleplay commands registered.", flush=True)
