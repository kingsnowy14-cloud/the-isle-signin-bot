"""Emergency Hamburg public Roblox server monitor for IronWolf.

Reads only Roblox's public server listing. Each Discord guild keeps its own
selected Job IDs, status channel, message ID and role allow-list.
"""
import asyncio
import json
import os
import threading
from datetime import datetime, timezone

import discord
import requests
from discord import app_commands
from discord.ext import commands, tasks

from storage import data_path
from premium import guild_plan

PLACE_ID = "7711635737"
UNIVERSE_ID = "2992873140"
ROBLOX_SERVERS_URL = f"https://games.roblox.com/v1/games/{PLACE_ID}/servers/Public"
REFRESH_SECONDS = 300
MAX_PAGES_PER_REFRESH = 20
CONFIG_PATH = data_path("emergency_hamburg.json")
_CONFIG_LOCK = threading.RLock()
_HTTP = requests.Session()
_HTTP.headers.update({"User-Agent": "IronWolf-EmergencyHamburg-Monitor/1.0"})


def load_monitor_config():
    """Load per-guild monitor settings without exposing other guilds."""
    with _CONFIG_LOCK:
        try:
            value = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}


def save_monitor_config(value):
    """Atomically persist the complete per-guild monitor configuration."""
    if not isinstance(value, dict):
        raise TypeError("Monitor configuration must be a dictionary.")
    with _CONFIG_LOCK:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        temp = CONFIG_PATH.with_name(CONFIG_PATH.name + ".tmp")
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, CONFIG_PATH)


def server_limit_for_guild(guild_config):
    """Free: 1 server, Premium: 5, Premium+: 20 selected public servers."""
    plan = guild_plan(guild_config)
    return {"free": 1, "premium": 5, "premium_plus": 20}.get(plan, 1)


def fetch_public_servers(max_pages=5, target_ids=None):
    """Fetch public server pages, stopping early when all requested Job IDs appear."""
    wanted = {str(value) for value in (target_ids or [])}
    found = {}
    cursor = None
    pages = max(1, min(int(max_pages), 20))
    for _ in range(pages):
        params = {"sortOrder": "Desc", "limit": 100}
        if cursor:
            params["cursor"] = cursor
        response = _HTTP.get(ROBLOX_SERVERS_URL, params=params, timeout=(5, 12))
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After", "60")
            raise RuntimeError("Roblox API rate limit (429); retry after " + str(retry_after) + " seconds.")
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("data", []) if isinstance(payload, dict) else []
        for row in rows:
            if not isinstance(row, dict) or not row.get("id"):
                continue
            item = {
                "id": str(row["id"]),
                "playing": int(row.get("playing", 0) or 0),
                "maxPlayers": int(row.get("maxPlayers", 0) or 0),
                "fps": row.get("fps"),
                "ping": row.get("ping"),
            }
            found[item["id"]] = item
        cursor = payload.get("nextPageCursor") if isinstance(payload, dict) else None
        if wanted and wanted.issubset(found):
            break
        if not cursor:
            break
    return list(found.values())


def build_status_embed(guild, configured, server_map, error=None):
    """Create the persistent per-guild Discord status embed."""
    selected = configured.get("servers", [])
    total_players = 0
    total_slots = 0
    online = 0
    embed = discord.Embed(
        title="🚨 Emergency Hamburg · Öffentliche Server",
        description="Roblox-Server, die für diesen Discord-Server verbunden wurden.",
        color=discord.Color.blurple(),
        timestamp=datetime.now(timezone.utc),
    )
    if not selected:
        embed.description = "Noch keine Roblox-Server im Dashboard verbunden."
    for item in selected[:20]:
        job_id = str(item.get("job_id", ""))
        stats = server_map.get(job_id)
        name = str(item.get("name") or ("Server " + job_id[:8]))[:100]
        if stats is None:
            value = "🔴 **Offline / nicht in der öffentlichen Liste gefunden**"
        else:
            playing = int(stats.get("playing", 0) or 0)
            maximum = int(stats.get("maxPlayers", 0) or 0)
            total_players += playing
            total_slots += maximum
            online += 1
            parts = [f"🟢 **Online** · {playing}/{maximum} Spieler"]
            if stats.get("ping") is not None:
                try:
                    parts.append(f"Ping: {round(float(stats['ping']))} ms")
                except (TypeError, ValueError):
                    pass
            if stats.get("fps") is not None:
                try:
                    parts.append(f"FPS: {round(float(stats['fps']))}")
                except (TypeError, ValueError):
                    pass
            value = "\n".join(parts)
        embed.add_field(name=name, value=value, inline=False)
    if selected:
        embed.add_field(
            name="Zusammenfassung",
            value=(
                f"**Online:** {online}/{len(selected)} verbundene Server\n"
                f"**Spieler:** {total_players}\n"
                f"**Auslastung:** {total_players}/{total_slots} Plätze"
                if total_slots else
                f"**Online:** {online}/{len(selected)} verbundene Server\n**Spieler:** {total_players}"
            ),
            inline=False,
        )
    if error:
        embed.add_field(name="API-Hinweis", value="Die letzte Aktualisierung war nicht vollständig. Vorhandene Werte können veraltet sein.\n" + str(error)[:500], inline=False)
    embed.set_footer(text="IronWolf · Automatische Aktualisierung alle 5 Minuten")
    return embed


class EmergencyHamburg(commands.Cog):
    """Guild-isolated public Roblox server status publisher."""

    def __init__(self, bot):
        self.bot = bot
        self._refresh_lock = asyncio.Lock()

    async def cog_load(self):
        if not self.refresh_loop.is_running():
            self.refresh_loop.start()

    def cog_unload(self):
        self.refresh_loop.cancel()

    async def interaction_check(self, interaction):
        if interaction.guild_id is None:
            return False
        settings = load_monitor_config().get(str(interaction.guild_id), {})
        member = interaction.user
        permissions = getattr(member, "guild_permissions", None)
        if permissions and (permissions.administrator or permissions.manage_guild):
            return True
        allowed = {str(value) for value in settings.get("allowed_role_ids", [])}
        member_roles = {str(role.id) for role in getattr(member, "roles", [])}
        if allowed.intersection(member_roles):
            return True
        await interaction.response.send_message(
            "Du benötigst Administrator-/Serververwaltungsrechte oder eine freigegebene Rolle.",
            ephemeral=True,
        )
        return False

    @app_commands.command(name="eh-status", description="Zeigt die Emergency-Hamburg-Überwachung dieses Discord-Servers.")
    @app_commands.guild_only()
    async def eh_status(self, interaction: discord.Interaction):
        settings = load_monitor_config().get(str(interaction.guild_id), {})
        servers = settings.get("servers", [])
        channel_id = settings.get("channel_id")
        channel_text = f"<#{channel_id}>" if str(channel_id or "").isdigit() else "Nicht konfiguriert"
        await interaction.response.send_message(
            f"**Emergency Hamburg Monitor**\nVerbundene Roblox-Server: **{len(servers)}**\nStatuskanal: {channel_text}\nAktualisierung: alle 5 Minuten\nKonfiguration: Dashboard → Gaming → Emergency Hamburg Monitor",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="eh-refresh", description="Aktualisiert die Emergency-Hamburg-Statusnachricht jetzt.")
    @app_commands.guild_only()
    async def eh_refresh(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        ok = await self.update_guild(interaction.guild)
        await interaction.followup.send(
            "Statusnachricht wurde aktualisiert." if ok else "Aktualisierung nicht möglich. Prüfe die Dashboard-Konfiguration und den Roblox-API-Status.",
            ephemeral=True,
        )

    async def update_guild(self, guild, server_map=None, api_error=None):
        if guild is None:
            return False
        async with self._refresh_lock:
            all_settings = load_monitor_config()
            settings = all_settings.get(str(guild.id), {})
            if not settings.get("enabled", True):
                return False
            channel_id = settings.get("channel_id")
            selected = settings.get("servers", [])
            if not str(channel_id or "").isdigit() or not selected:
                return False
            channel = guild.get_channel(int(channel_id))
            if not isinstance(channel, discord.TextChannel):
                return False
            error = api_error
            if server_map is None:
                target_ids = [item.get("job_id") for item in selected if item.get("job_id")]
                try:
                    rows = await asyncio.to_thread(
                        fetch_public_servers,
                        MAX_PAGES_PER_REFRESH,
                        target_ids,
                    )
                    server_map = {item["id"]: item for item in rows}
                except Exception as exc:
                    server_map = {}
                    error = f"{type(exc).__name__}: {exc}"
                    print(f"[EH MONITOR] Roblox API error for guild {guild.id}: {error}", flush=True)
            embed = build_status_embed(guild, settings, server_map, error=error)
            message = None
            message_id = settings.get("message_id")
            if str(message_id or "").isdigit():
                try:
                    message = await channel.fetch_message(int(message_id))
                except discord.NotFound:
                    message = None
                except (discord.Forbidden, discord.HTTPException) as exc:
                    print(f"[EH MONITOR] Cannot fetch status message in guild {guild.id}: {type(exc).__name__}: {exc}", flush=True)
                    return False
            try:
                if message is None:
                    message = await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
                else:
                    await message.edit(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            except (discord.Forbidden, discord.HTTPException) as exc:
                print(f"[EH MONITOR] Cannot publish status in guild {guild.id}: {type(exc).__name__}: {exc}", flush=True)
                return False
            settings["message_id"] = str(message.id)
            settings["last_updated"] = datetime.now(timezone.utc).isoformat()
            settings["last_error"] = error
            all_settings[str(guild.id)] = settings
            save_monitor_config(all_settings)
            return True

    @tasks.loop(seconds=REFRESH_SECONDS)
    async def refresh_loop(self):
        guilds = list(self.bot.guilds)
        settings = load_monitor_config()
        target_ids = set()
        for guild in guilds:
            guild_settings = settings.get(str(guild.id), {})
            if guild_settings.get("enabled", True):
                target_ids.update(
                    str(item.get("job_id"))
                    for item in guild_settings.get("servers", [])
                    if item.get("job_id")
                )
        server_map = {}
        api_error = None
        if target_ids:
            try:
                rows = await asyncio.to_thread(
                    fetch_public_servers,
                    MAX_PAGES_PER_REFRESH,
                    target_ids,
                )
                server_map = {item["id"]: item for item in rows}
            except Exception as exc:
                api_error = f"{type(exc).__name__}: {exc}"
                print(f"[EH MONITOR] Shared Roblox API refresh failed: {api_error}", flush=True)
        for guild in guilds:
            try:
                await self.update_guild(guild, server_map=server_map, api_error=api_error)
            except Exception as exc:
                print(f"[EH MONITOR] Unexpected refresh error for guild {guild.id}: {type(exc).__name__}: {exc}", flush=True)

    @refresh_loop.before_loop
    async def before_refresh(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(EmergencyHamburg(bot))
