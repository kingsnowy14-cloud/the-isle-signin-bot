import os
import json
import hashlib
import html
import io
import zipfile
import time
import asyncio
import random
import shutil
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse
from PIL import Image, ImageDraw, ImageFont
from storage import data_path
from premium import guild_has_feature, account_plan, expired_grants, mark_expiry_dm_sent
from collections import defaultdict, deque
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone, timedelta

# Install:
# discord.py>=2.4.0,<3.0.0
# Set your bot token as the DISCORD_TOKEN environment variable.

TOKEN = os.getenv("DISCORD_TOKEN")
# Optional: set your Discord server ID in Railway for instant guild slash-command sync.
GUILD_ID = os.getenv("GUILD_ID")
CONFIG_FILE = data_path("config.json")
DATA_FILE = data_path("data.json")

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents, case_insensitive=True)
_automation_worker_task = None
_birthday_worker_task = None


async def dashboard_command_enabled(guild_id, key):
    """Check whether a command is disabled for this server in the web dashboard."""
    if guild_id is None:
        return True
    cfg = config.get(str(guild_id), {})
    return key not in set(cfg.get("disabled_commands", []))


async def dashboard_module_enabled(guild_id, module_name):
    """Check whether a Cog module is disabled for this server."""
    if guild_id is None or not module_name:
        return True
    cfg = config.get(str(guild_id), {})
    return module_name not in set(cfg.get("disabled_modules", []))


@bot.check
async def dashboard_prefix_command_check(ctx):
    guild_id = ctx.guild.id if ctx.guild else None
    if not await dashboard_command_enabled(guild_id, f"prefix:!{ctx.command.name}"):
        return False
    cog = getattr(ctx, "cog", None)
    module_name = cog.__class__.__name__ if cog is not None else None
    # Seasonal prefix commands are available without a premium feature flag.
    return await dashboard_module_enabled(guild_id, module_name)


async def dashboard_slash_command_check(interaction):
    command = interaction.command
    if command is None or interaction.guild_id is None:
        return True
    key = f"slash:/{command.qualified_name}"
    if not await dashboard_command_enabled(interaction.guild_id, key):
        message = "Dieser Befehl wurde für diesen Server im Web-Dashboard deaktiviert."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
        return False
    cog = getattr(command, "binding", None)
    module_name = cog.__class__.__name__ if cog is not None else None
    premium_feature = {"SecurityTools": "security", "EventTools": "automation"}.get(module_name)
    # Seasonal slash commands are available without a premium feature flag.
    if premium_feature and not guild_has_feature(guild_config(interaction.guild_id), premium_feature):
        message = "Dieses Pro-Modul benötigt ein aktives, mit diesem Server verknüpftes Premium+-Konto."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
        return False
    if not await dashboard_module_enabled(interaction.guild_id, module_name):
        message = "Dieses Modul wurde für diesen Server im Web-Dashboard deaktiviert."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
        return False
    return True


bot.tree.interaction_check = dashboard_slash_command_check


MODULE_STATE_FILE = data_path("module_change_state.json")
MODULE_LOG_FILE = data_path("module_changes.json")
REPO_DIR = Path(__file__).resolve().parent


def record_module_changes():
    """Detect changed top-level Python modules on startup and save an audit trail."""
    try:
        state = json.loads(MODULE_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        state = {}
    try:
        logs = json.loads(MODULE_LOG_FILE.read_text(encoding="utf-8"))
        if not isinstance(logs, list):
            logs = []
    except (OSError, json.JSONDecodeError):
        logs = []

    current = {}
    for module_path in sorted(REPO_DIR.glob("*.py")):
        try:
            digest = hashlib.sha256(module_path.read_bytes()).hexdigest()
        except OSError:
            continue
        name = module_path.name
        current[name] = digest
        previous = state.get(name)
        if previous != digest:
            logs.append({
                "module": name,
                "change": "Ersterfassung" if previous is None else "Modul geändert",
                "detected_at": datetime.now(timezone.utc).isoformat(),
                "sha256": digest,
            })

    for name in state:
        if name not in current:
            logs.append({
                "module": name,
                "change": "Modul entfernt",
                "detected_at": datetime.now(timezone.utc).isoformat(),
                "sha256": None,
            })

    # Keep the audit log bounded while retaining recent history.
    logs = logs[-500:]
    MODULE_STATE_FILE.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    MODULE_LOG_FILE.write_text(json.dumps(logs, ensure_ascii=False, indent=2), encoding="utf-8")


@bot.group(name="IronWolf", invoke_without_command=True)
@commands.guild_only()
@commands.has_guild_permissions(manage_guild=True)
async def ironwolf_group(ctx):
    await ctx.reply("Verwendung: `!IronWolf logs`", mention_author=False)


@ironwolf_group.command(name="logs")
@commands.guild_only()
@commands.has_guild_permissions(manage_guild=True)
async def ironwolf_logs(ctx):
    try:
        logs = json.loads(MODULE_LOG_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logs = []
    if not logs:
        await ctx.reply("Noch keine Moduländerungen protokolliert. Beim Bot-Start wird der erste Stand erfasst.", mention_author=False)
        return
    lines = []
    for entry in reversed(logs[-15:]):
        stamp = entry.get("detected_at", "Zeit unbekannt").replace("T", " ")[:19]
        lines.append(f"• **{entry.get('module', 'Unbekannt')}** — {entry.get('change', 'Änderung')} — {stamp} UTC")
    text = "\n".join(lines)
    if len(text) > 1900:
        text = text[:1890] + "…"
    embed = discord.Embed(
        title="🐺 IronWolf · Moduländerungen",
        description=text,
        color=discord.Color.blurple(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_footer(text="Letzte 15 Einträge · Änderungen werden beim Bot-Start erkannt")
    await ctx.reply(embed=embed, mention_author=False)


def create_embed(*args, **kwargs):
    """Create an embed with the standard bot attribution footer."""
    embed = discord.Embed(*args, **kwargs)
    embed.set_footer(text="Created by KingSnowy")
    return embed


def load_json(path, default):
    """Load JSON defensively and preserve malformed files for recovery."""
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return default
    except OSError as exc:
        print(f"[STORAGE] Could not read {path.name}: {type(exc).__name__}: {exc}")
        return default

    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        value = None

    # The bot expects dictionaries for config/data. A valid JSON value with the
    # wrong root type is just as unsafe as malformed JSON; preserve it first.
    if not isinstance(value, type(default)):
        if raw:
            try:
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                backup = path.with_name(f"{path.name}.corrupt-{stamp}.bak")
                shutil.copy2(path, backup)
                print(f"[STORAGE] Invalid JSON structure preserved at {backup.name}")
            except OSError as exc:
                print(f"[STORAGE] Could not back up {path.name}: {type(exc).__name__}: {exc}")
        return default
    return value


def save_json(path, data):
    """Write JSON atomically so interrupted writes do not truncate saved data."""
    path = Path(path)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)
    except OSError as exc:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        print(f"[STORAGE] Could not save {path.name}: {type(exc).__name__}: {exc}")
        raise


config = load_json(CONFIG_FILE, {})
data = load_json(DATA_FILE, {"languages": {}, "registrations": {}})
# Stats are stored per server and per user in data.json.
data.setdefault("stats", {})
data.setdefault("moderation_warnings", {})
data.setdefault("giveaways", {})
data.setdefault("daily_claims", {})
data.setdefault("mod_cases", {})
data.setdefault("suggestions", {})
data.setdefault("events", {})
data.setdefault("social_alert_seen", {})
data.setdefault("polls", {})
# Expose the shared persistent stats store to the dashboard for read-only metrics.
bot.dashboard_data = data
# Invite-use snapshots are kept in memory and refreshed after each join.
_invite_cache = {}
# Active voice sessions are kept in memory so bot downtime is not counted.
_active_voice_sessions = {}


def get_user_stats(guild_id, user_id):
    """Return persistent stats for one member, creating defaults when needed."""
    server_stats = data.setdefault("stats", {}).setdefault(str(guild_id), {})
    return server_stats.setdefault(str(user_id), {"messages": 0, "voice_seconds": 0})


def format_duration(seconds):
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days} Tage, {hours} Std. und {minutes} Min."
    if hours:
        return f"{hours} Std. und {minutes} Min."
    return f"{minutes} Min."


# Defaults are merged recursively into existing per-server settings. Existing values,
# unknown keys and module-specific configuration are deliberately never overwritten.
DEFAULT_GUILD_CONFIG = {
    "registration_channel": None,
    "ticket_category": None,
    "ticket_log_channel": None,
    "support_role": None,
    "ticket_panels": {},
    "ticket_counter": 0,
    "security_log_channel": None,
    "honeypot_channel": None,
    "anti_nuke_enabled": False,
    "anti_nuke_threshold": 4,
    "anti_raid_enabled": False,
    "raid_threshold": 8,
    "trusted_security_role": None,
    "welcome_channel": None,
    "goodbye_channel": None,
    "welcome_message": "Willkommen {mention} auf **{server}**! Du bist Mitglied Nr. **{count}**.",
    "welcome_embed": False,
    "welcome_bots_enabled": False,
    "self_roles": [],
    "birthday_channel": None,
    "levelup_channel": None,
    "birthdays": {},
    "birthday_greeted_year": {},
    "leave_enabled": True,
    "leave_message": "👋 **{user}** hat **{server}** verlassen. Wir wünschen dir alles Gute!",
    "leave_embed": False,
    "invite_tracker_enabled": False,
    "invite_log_channel": None,
    "social_links": {},
    "automod_enabled": False,
    "automod_threshold": 6,
    "automod_window_seconds": 8,
    "automod_timeout_minutes": 2,
    "xp_enabled": True,
    "xp_cooldown_seconds": 60,
    "xp_min": 5,
    "xp_max": 15,
    "verify_channel": None,
    "verified_role": None,
    "unverified_role": None,
    "level_roles": {},
    "tempvoice_trigger": None,
    "tempvoice_category": None,
    "social_alerts": {},
    "ticket_auto_close_hours": 0,
    "automation_announcement": {
        "enabled": False,
        "channel_id": None,
        "message": "",
        "interval_minutes": 1440,
        "next_run_at": None,
    },
}


def _merge_missing_defaults(existing, defaults):
    """Add newly introduced defaults without resetting any saved server values."""
    changed = False
    for key, default_value in defaults.items():
        if key not in existing:
            existing[key] = json.loads(json.dumps(default_value))
            changed = True
        elif isinstance(default_value, dict) and isinstance(existing.get(key), dict):
            if _merge_missing_defaults(existing[key], default_value):
                changed = True
    return changed


def guild_config(guild_id):
    """Return a backward-compatible per-server config, migrating new keys in place."""
    gid = str(guild_id)
    cfg = config.get(gid)
    if not isinstance(cfg, dict):
        cfg = {}
        config[gid] = cfg
        changed = True
    else:
        changed = False
    changed = _merge_missing_defaults(cfg, DEFAULT_GUILD_CONFIG) or changed
    if changed:
        save_json(CONFIG_FILE, config)
        print(f"[CONFIG] Merged missing defaults for guild {gid}; existing settings were preserved.", flush=True)
    return cfg


def migrate_all_guild_configs():
    """Apply new defaults to every known server while preserving existing settings."""
    changed = False
    for guild_id, cfg in list(config.items()):
        if not str(guild_id).isdigit():
            continue
        if not isinstance(cfg, dict):
            config[guild_id] = {}
            cfg = config[guild_id]
            changed = True
        changed = _merge_missing_defaults(cfg, DEFAULT_GUILD_CONFIG) or changed
    # Ensure newly joined servers get a complete default config too.
    for guild in getattr(bot, "guilds", []):
        cfg = config.get(str(guild.id))
        if not isinstance(cfg, dict):
            config[str(guild.id)] = {}
            cfg = config[str(guild.id)]
            changed = True
        changed = _merge_missing_defaults(cfg, DEFAULT_GUILD_CONFIG) or changed
    if changed:
        save_json(CONFIG_FILE, config)
        print(f"[CONFIG] Safely merged defaults for existing servers; no saved settings were replaced.", flush=True)



def _safe_automation_interval(job):
    try:
        value = int(job.get("interval_minutes", 1440) or 1440)
    except (TypeError, ValueError):
        value = 1440
    return max(60, min(10080, value))


def cleanup_expired_trial_premium(guild_id):
    """Remove trial-only Premium configuration after its 15-day trial expires."""
    cfg = guild_config(guild_id)
    if not isinstance(cfg, dict) or not cfg.get("premium_trial_used"):
        return False
    raw_expiry = cfg.get("premium_trial_expires_at")
    try:
        expiry = datetime.fromisoformat(str(raw_expiry or "").replace("Z", "+00:00"))
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return False
    if expiry > datetime.now(timezone.utc):
        return False
    # Never remove settings belonging to a currently paid/authorized plan.
    if cfg.get("premium_server_override") in {"premium", "premium_plus"}:
        return False
    if account_plan(cfg.get("premium_owner_id")) != "free":
        return False

    changed = False
    # Stop Premium+ automation and discard its configured schedule.
    if cfg.get("automation_announcement"):
        cfg["automation_announcement"] = {
            "enabled": False, "channel_id": None, "message": "",
            "interval_minutes": 1440, "next_run_at": None,
        }
        changed = True
    if cfg.get("automation_templates"):
        cfg["automation_templates"] = []
        changed = True
    # Keep the basic ticket panel usable, but remove Premium branding and
    # reduce each remaining panel to the one-button Free configuration.
    panels = cfg.get("ticket_panels")
    if isinstance(panels, dict):
        for panel in panels.values():
            if not isinstance(panel, dict):
                continue
            for key in ("brand_color", "brand_logo_url", "brand_logo_file", "brand_logo_filename"):
                if key in panel:
                    old_path = panel.pop(key, None)
                    if key == "brand_logo_file" and old_path:
                        try:
                            Path(old_path).unlink(missing_ok=True)
                        except OSError:
                            pass
                    changed = True
            labels = panel.get("labels")
            if isinstance(labels, list) and len(labels) > 1:
                panel["labels"] = labels[:1]
                changed = True
        # Extra panels are a Premium feature; retain the first configured panel.
        if len(panels) > 1:
            first_key = next(iter(panels))
            cfg["ticket_panels"] = {first_key: panels[first_key]}
            changed = True
    # Premium+ security switches default to off after the trial.
    for key in ("anti_nuke_enabled", "anti_raid_enabled"):
        if cfg.get(key):
            cfg[key] = False
            changed = True
    if changed:
        save_json(CONFIG_FILE, config)
        print(f"[PREMIUM] Trial Premium settings cleaned for guild {guild_id}.", flush=True)
    return changed


async def premium_automation_worker():
    """Run announcements and notify users when annual manual Premium expires."""
    while not bot.is_closed():
        await asyncio.sleep(60)
        try:
            for user_id, grant in expired_grants():
                try:
                    user = await bot.fetch_user(int(user_id))
                    plan_label = "Premium"
                    await user.send(f"Dein {plan_label}-Zugang bei IronWolf ist abgelaufen. Deine Premium-Funktionen wurden deaktiviert. Wenn du Premium erneut möchtest, wende dich bitte an das IronWolf-Team.")
                    mark_expiry_dm_sent(user_id)
                    print(f"[PREMIUM] Expiry DM sent to user {user_id}.", flush=True)
                except discord.Forbidden:
                    print(f"[PREMIUM] Could not DM expired user {user_id}; DMs are closed.", flush=True)
                    mark_expiry_dm_sent(user_id)
                except (discord.HTTPException, ValueError) as exc:
                    print(f"[PREMIUM] Could not notify expired user {user_id}: {type(exc).__name__}: {exc}", flush=True)
        except Exception as exc:
            print(f"[PREMIUM] Expiry check failed: {type(exc).__name__}: {exc}", flush=True)
        now = datetime.now(timezone.utc)
        for guild in list(bot.guilds):
            cfg = guild_config(guild.id)
            try:
                cleanup_expired_trial_premium(guild.id)
                cfg = guild_config(guild.id)
            except Exception as exc:
                print(f"[PREMIUM] Trial cleanup failed for guild {guild.id}: {type(exc).__name__}: {exc}", flush=True)
            if not guild_has_feature(cfg, "automation"):
                continue
            job = cfg.get("automation_announcement", {})
            if not isinstance(job, dict) or not job.get("enabled"):
                continue
            try:
                next_run = datetime.fromisoformat(str(job.get("next_run_at", "")).replace("Z", "+00:00"))
                if next_run.tzinfo is None:
                    next_run = next_run.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                interval = _safe_automation_interval(job)
                job["next_run_at"] = (now + timedelta(minutes=interval)).isoformat()
                save_json(CONFIG_FILE, config)
                continue
            if next_run > now:
                continue
            try:
                channel_id = int(job.get("channel_id") or 0)
                channel = guild.get_channel(channel_id)
                if channel is None and channel_id:
                    channel = await bot.fetch_channel(channel_id)
                if not isinstance(channel, discord.TextChannel):
                    raise ValueError("Configured automation channel is unavailable")
                message = str(job.get("message", "")).strip()
                if not message or len(message) > 1800:
                    raise ValueError("Configured automation message is empty or too long")
                await channel.send(message, allowed_mentions=discord.AllowedMentions.none())
                interval = _safe_automation_interval(job)
                job["next_run_at"] = (now + timedelta(minutes=interval)).isoformat()
                save_json(CONFIG_FILE, config)
                print(f"[AUTOMATION] Sent scheduled announcement in guild {guild.id}.", flush=True)
            except (discord.Forbidden, discord.HTTPException, ValueError, TypeError) as exc:
                job["next_run_at"] = (now + timedelta(minutes=15)).isoformat()
                save_json(CONFIG_FILE, config)
                print(f"[AUTOMATION] Could not send announcement in guild {guild.id}: {type(exc).__name__}: {exc}", flush=True)


def user_language(user_id):
    return data.setdefault("languages", {}).get(str(user_id), "en")


TEXTS = {
    "en": {
        "dinosaur": "Dinosaur",
        "skin": "Skin",
        "location": "Location",
        "modal_title": "Server sign-in",
        "signin_success": "Your sign-in has been submitted.",
        "signout_success": "Your sign-out has been submitted.",
        "need_signin": "You do not have an active sign-in.",
        "signin_first": "You need to sign in first.",
        "signin_modal": "Sign In",
        "signout_modal": "Sign Out",
        "submit": "Submit",
        "language_saved": "Your private form and confirmation language is now English.",
    },
    "de": {
        "dinosaur": "Dinosaurier",
        "skin": "Skin",
        "location": "Standort",
        "modal_title": "Server-Anmeldung",
        "signin_success": "Deine Anmeldung wurde übermittelt.",
        "signout_success": "Deine Abmeldung wurde übermittelt.",
        "need_signin": "Du bist aktuell nicht angemeldet.",
        "signin_first": "Du musst dich zuerst anmelden.",
        "signin_modal": "Anmelden",
        "signout_modal": "Abmelden",
        "submit": "Absenden",
        "language_saved": "Deine private Formular- und Bestätigungssprache ist jetzt Deutsch.",
    },
    "cs": {
        "dinosaur": "Dinosaurus",
        "skin": "Skin",
        "location": "Lokace",
        "modal_title": "Přihlášení na server",
        "signin_success": "Tvé přihlášení bylo odesláno.",
        "signout_success": "Tvé odhlášení bylo odesláno.",
        "need_signin": "Momentálně nejsi přihlášen/a.",
        "signin_first": "Nejprve se musíš přihlásit.",
        "signin_modal": "Přihlásit se",
        "signout_modal": "Odhlásit se",
        "submit": "Odeslat",
        "language_saved": "Jazyk soukromého formuláře a potvrzení je nyní čeština.",
    },
}


def set_user_language(user_id, language):
    data.setdefault("languages", {})[str(user_id)] = language
    save_json(DATA_FILE, data)


def set_registration(user_id, guild_id, registration):
    data.setdefault("registrations", {}).setdefault(str(guild_id), {})[str(user_id)] = registration
    save_json(DATA_FILE, data)


def get_registration(user_id, guild_id):
    return data.setdefault("registrations", {}).setdefault(str(guild_id), {}).get(str(user_id))


async def send_public_registration(guild, user, action, dinosaur="", skin="", location=""):
    cfg = guild_config(guild.id)
    channel_id = cfg.get("registration_channel")
    channel = guild.get_channel(channel_id) if channel_id else None
    if not isinstance(channel, discord.TextChannel):
        return
    # Public registration announcements are ALWAYS in English.
    if action == "Sign In":
        embed = create_embed(
            title="✅ Player Signed In",
            description=f"**Player:** {user.mention}\n**Dinosaur:** {discord.utils.escape_markdown(dinosaur)}\n"
                        f"**Skin:** {discord.utils.escape_markdown(skin)}\n"
                        f"**Location:** {discord.utils.escape_markdown(location)}",
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc),
        )
    else:
        embed = create_embed(
            title="🚪 Player Signed Out",
            description=f"**Player:** {user.mention}\n**Dinosaur:** {discord.utils.escape_markdown(dinosaur or 'Not provided')}\n"
                        f"**Skin:** {discord.utils.escape_markdown(skin or 'Not provided')}\n"
                        f"**Location:** {discord.utils.escape_markdown(location or 'Not provided')}",
            color=discord.Color.orange(),
            timestamp=datetime.now(timezone.utc),
        )
    embed.set_footer(text="Created by KingSnowy")
    await channel.send(embed=embed)


class LanguageSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Deutsch", value="de", emoji="🇩🇪"),
            discord.SelectOption(label="English", value="en", emoji="🇬🇧"),
            discord.SelectOption(label="Čeština", value="cs", emoji="🇨🇿"),
        ]
        super().__init__(placeholder="Choose your language / Sprache wählen / Vyber jazyk",
                         min_values=1, max_values=1, options=options,
                         custom_id="registration:language")

    async def callback(self, interaction: discord.Interaction):
        language = self.values[0]
        set_user_language(interaction.user.id, language)
        await interaction.response.send_message(TEXTS[language]["language_saved"], ephemeral=True)


class LanguageView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(LanguageSelect())


class RegistrationModal(discord.ui.Modal):
    def __init__(self, action: str, language: str):
        self.action = action
        self.language = language if language in TEXTS else "en"
        t = TEXTS[self.language]
        super().__init__(title=t["modal_title"], timeout=300)
        self.dinosaur = discord.ui.TextInput(
            label=t["dinosaur"], placeholder="e.g. T. rex", max_length=100, required=(action == "Sign In")
        )
        self.skin = discord.ui.TextInput(
            label=t["skin"], placeholder="Enter your skin", max_length=100, required=False
        )
        self.location = discord.ui.TextInput(
            label=t["location"], placeholder="Enter your location", max_length=150, required=(action == "Sign In")
        )
        self.add_item(self.dinosaur)
        self.add_item(self.skin)
        self.add_item(self.location)

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("This form can only be used in a server.", ephemeral=True)
            return
        current = get_registration(interaction.user.id, guild.id)
        t = TEXTS[self.language]
        dinosaur = str(self.dinosaur.value).strip()
        skin = str(self.skin.value).strip()
        location = str(self.location.value).strip()

        if self.action == "Sign In":
            registration = {
                "dinosaur": dinosaur,
                "skin": skin,
                "location": location,
                "signed_in_at": datetime.now(timezone.utc).isoformat(),
            }
            set_registration(interaction.user.id, guild.id, registration)
            await send_public_registration(guild, interaction.user, "Sign In", dinosaur, skin, location)
            await interaction.response.send_message(t["signin_success"], ephemeral=True)
        else:
            if not current:
                await interaction.response.send_message(t["need_signin"], ephemeral=True)
                return
            await send_public_registration(
                guild, interaction.user, "Sign Out",
                current.get("dinosaur", ""), current.get("skin", ""), current.get("location", "")
            )
            data.setdefault("registrations", {}).setdefault(str(guild.id), {}).pop(str(interaction.user.id), None)
            save_json(DATA_FILE, data)
            await interaction.response.send_message(t["signout_success"], ephemeral=True)


class RegistrationView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Sign In", style=discord.ButtonStyle.success, emoji="✅",
                       custom_id="registration:sign_in")
    async def sign_in(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(RegistrationModal("Sign In", user_language(interaction.user.id)))

    @discord.ui.button(label="Sign Out", style=discord.ButtonStyle.danger, emoji="🚪",
                       custom_id="registration:sign_out")
    async def sign_out(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(RegistrationModal("Sign Out", user_language(interaction.user.id)))


# ----------------------------- TICKET SYSTEM -----------------------------

# Prevent duplicate ticket channels when a modal is submitted twice in quick succession.
_ticket_creations_in_progress = set()

class TicketCreateModal(discord.ui.Modal):
    def __init__(self, panel_key: str, button_index: int, button_label: str):
        super().__init__(title=f"Create ticket: {button_label[:35]}")
        self.panel_key = panel_key
        self.button_index = button_index
        self.button_label = button_label
        self.reason = discord.ui.TextInput(
            label="How can we help?",
            placeholder="Describe your issue",
            style=discord.TextStyle.paragraph,
            max_length=1000,
            required=True,
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        # Acknowledge the modal submission immediately so Discord does not time out
        # while the channel is being created and ticket data is being saved.
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("Use this inside a server.", ephemeral=True)
            return
        # Limit ticket creation per user across all ticket panels in this server.
        creation_key = (guild.id, interaction.user.id)
        if creation_key in _ticket_creations_in_progress:
            await interaction.followup.send("Dein Ticket wird bereits erstellt. Bitte warte kurz.", ephemeral=True)
            return
        _ticket_creations_in_progress.add(creation_key)

        cfg = guild_config(guild.id)
        panel = cfg.get("ticket_panels", {}).get(self.panel_key)
        if not panel:
            _ticket_creations_in_progress.discard(creation_key)
            await interaction.followup.send("This ticket panel is no longer configured.", ephemeral=True)
            return
        # Count only tickets that are still open (their channels still exist).
        user_open_tickets = 0
        for ticket_channel_id, ticket_meta in data.get("tickets", {}).items():
            if str(ticket_meta.get("owner_id")) != str(interaction.user.id):
                continue
            ticket_channel = guild.get_channel(int(ticket_channel_id))
            if ticket_channel is not None:
                user_open_tickets += 1

        if user_open_tickets >= 5:
            _ticket_creations_in_progress.discard(creation_key)
            await interaction.followup.send(
                "Du kannst maximal 5 offene Tickets gleichzeitig haben. Bitte schließe zuerst ein bestehendes Ticket.",
                ephemeral=True,
            )
            return

        category_id = panel.get("category_id") or cfg.get("ticket_category")
        category = guild.get_channel(category_id) if category_id else None
        if category_id and not isinstance(category, discord.CategoryChannel):
            category = None

        cfg["ticket_counter"] = int(cfg.get("ticket_counter", 0)) + 1
        ticket_number = cfg["ticket_counter"]
        safe_name = "".join(c.lower() if c.isalnum() else "-" for c in interaction.user.name).strip("-")
        channel_name = f"ticket-{ticket_number:04d}-{safe_name[:30] or 'user'}"
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_channels=True),
        }
        support_role_id = panel.get("support_role_id") or cfg.get("support_role")
        support_role = guild.get_role(support_role_id) if support_role_id else None
        if support_role:
            overwrites[support_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        try:
            channel = await guild.create_text_channel(
                channel_name, category=category, overwrites=overwrites or {},
                topic=f"Ticket owner ID: {interaction.user.id} | Ticket number: {ticket_number}"
            )
        except discord.Forbidden:
            _ticket_creations_in_progress.discard(creation_key)
            await interaction.followup.send(
                "I don't have permission to create ticket channels. Check Manage Channels permission.",
                ephemeral=True
            )
            return
        except discord.HTTPException as exc:
            _ticket_creations_in_progress.discard(creation_key)
            await interaction.followup.send(
                f"Discord could not create the ticket channel (HTTP {exc.status}). Please try again or check the bot's permissions.",
                ephemeral=True
            )
            return

        ticket_meta = {
            "owner_id": interaction.user.id,
            "claimed_by": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "reason": str(self.reason.value),
            "label": self.button_label,
            "ticket_number": ticket_number,
            "support_role_id": support_role_id,
        }
        data.setdefault("tickets", {})[str(channel.id)] = ticket_meta
        save_json(DATA_FILE, data)

        brand_color = discord.Color.blurple()
        if guild_has_feature(cfg, "branding"):
            try:
                brand_color = discord.Color(int(str(panel.get("brand_color", "5865F2")).lstrip("#"), 16))
            except (TypeError, ValueError):
                brand_color = discord.Color.blurple()
        embed = create_embed(
            title=f"🎫 {self.button_label}",
            description=f"Hello {interaction.user.mention}!\n\n**Issue:**\n{self.reason.value}",
            color=brand_color,
            timestamp=datetime.now(timezone.utc),
        )
        logo_url = panel.get("brand_logo_url", "") if guild_has_feature(cfg, "branding") else ""
        if isinstance(logo_url, str) and logo_url.startswith("https://") and len(logo_url) <= 500:
            embed.set_thumbnail(url=logo_url)
        embed.add_field(name="Ticket owner", value=interaction.user.mention, inline=True)
        embed.add_field(name="Ticket number", value=str(ticket_number), inline=True)
        await channel.send(
            content=f"{interaction.user.mention}" + (f" {support_role.mention}" if support_role else ""),
            embed=embed,
            view=TicketControlView(),
        )
        _ticket_creations_in_progress.discard(creation_key)
        await interaction.followup.send(f"Your ticket has been created: {channel.mention}", ephemeral=True)


class TicketPanelView(discord.ui.View):
    def __init__(self, panel_key: str, labels):
        super().__init__(timeout=None)
        self.panel_key = panel_key
        for index, label in enumerate(labels[:5]):
            button = discord.ui.Button(
                label=label[:80],
                style=discord.ButtonStyle.primary,
                custom_id=f"ticket:create:{panel_key}:{index}",
            )
            async def callback(interaction: discord.Interaction, i=index, text=label):
                await interaction.response.send_modal(TicketCreateModal(self.panel_key, i, text))
            button.callback = callback
            self.add_item(button)


class TicketControlView(discord.ui.View):
    def __init__(self, claimed: bool = False):
        super().__init__(timeout=None)

        # Keep both actions visible at all times; callbacks validate the current state.
        claim_button = discord.ui.Button(
            label="Claim",
            style=discord.ButtonStyle.primary,
            emoji="🙋",
            custom_id="ticket:claim",
        )
        claim_button.callback = self.claim
        self.add_item(claim_button)

        release_button = discord.ui.Button(
            label="Freigeben",
            style=discord.ButtonStyle.secondary,
            emoji="🔓",
            custom_id="ticket:release",
        )
        release_button.callback = self.release
        self.add_item(release_button)

        priority_button = discord.ui.Button(
            label="Priorität umschalten",
            style=discord.ButtonStyle.secondary,
            emoji="🚩",
            custom_id="ticket:priority",
        )
        priority_button.callback = self.priority
        self.add_item(priority_button)

        close_button = discord.ui.Button(
            label="Close ticket",
            style=discord.ButtonStyle.danger,
            emoji="🔒",
            custom_id="ticket:close",
        )
        close_button.callback = self.close
        self.add_item(close_button)

    async def claim(self, interaction: discord.Interaction):
        meta = data.setdefault("tickets", {}).get(str(interaction.channel_id))
        if not meta:
            await interaction.response.send_message("Ticket data not found.", ephemeral=True)
            return
        if not is_support(interaction.user, meta, interaction.guild):
            await interaction.response.send_message("Only the configured support team can claim tickets.", ephemeral=True)
            return
        if meta.get("claimed_by"):
            await interaction.response.send_message(
                f"This ticket is already claimed by <@{meta['claimed_by']}>.", ephemeral=True
            )
            return

        meta["claimed_by"] = interaction.user.id
        save_json(DATA_FILE, data)
        try:
            await interaction.channel.edit(
                topic=f"Ticket owner ID: {meta['owner_id']} | Claimed by ID: {interaction.user.id}"
            )
        except discord.HTTPException:
            pass

        await interaction.response.edit_message(
            content=f"🎫 **Ticket übernommen von:** {interaction.user.mention}",
            view=TicketControlView(claimed=True),
        )
        await interaction.channel.send(
            f"🙋 {interaction.user.mention} hat dieses Ticket übernommen."
        )

    async def release(self, interaction: discord.Interaction):
        meta = data.setdefault("tickets", {}).get(str(interaction.channel_id))
        if not meta:
            await interaction.response.send_message("Ticket data not found.", ephemeral=True)
            return
        if not is_support(interaction.user, meta, interaction.guild):
            await interaction.response.send_message("Only the configured support team can release tickets.", ephemeral=True)
            return
        if not meta.get("claimed_by"):
            await interaction.response.send_message("This ticket is not currently claimed.", ephemeral=True)
            return

        previous_claimer = meta.get("claimed_by")
        meta["claimed_by"] = None
        save_json(DATA_FILE, data)
        try:
            await interaction.channel.edit(
                topic=f"Ticket owner ID: {meta['owner_id']} | Unclaimed"
            )
        except discord.HTTPException:
            pass

        previous_claimer_mention = f"<@{previous_claimer}>" if previous_claimer else "Unbekannt"
        await interaction.response.edit_message(
            content=(
                f"🔓 **Ticket freigegeben von:** {interaction.user.mention}\n"
                f"**Zuvor übernommen von:** {previous_claimer_mention}"
            ),
            view=TicketControlView(claimed=False),
        )
        await interaction.channel.send(
            f"🔓 {interaction.user.mention} hat das Ticket freigegeben. "
            f"Zuvor übernommen von: {previous_claimer_mention}. Das Ticket kann jetzt erneut übernommen werden."
        )

    async def priority(self, interaction: discord.Interaction):
        meta = data.setdefault("tickets", {}).get(str(interaction.channel_id))
        if not meta:
            await interaction.response.send_message("Ticket data not found.", ephemeral=True)
            return
        if not is_support(interaction.user, meta, interaction.guild):
            await interaction.response.send_message("Only the configured support team can change ticket priority.", ephemeral=True)
            return
        cfg = guild_config(interaction.guild_id)
        if not guild_has_feature(cfg, "ticket"):
            await interaction.response.send_message(
                "🚩 Die Prioritätsfunktion gehört zum Premium-Ticket-System. Öffne die Premium-Seite im Dashboard für Details.",
                ephemeral=True,
            )
            return
        channel = interaction.channel
        is_priority = not bool(meta.get("priority", False))
        meta["priority"] = is_priority
        save_json(DATA_FILE, data)
        current_name = channel.name
        clean_name = current_name.removeprefix("priority-")
        new_name = ("priority-" if is_priority else "") + clean_name
        try:
            await channel.edit(name=new_name[:100], reason=f"Ticket priority changed by {interaction.user}")
        except discord.HTTPException:
            pass
        await interaction.response.send_message(
            "🚩 Ticket als **Priorität** markiert." if is_priority else "Ticket-Priorität entfernt.",
            ephemeral=True,
        )

    async def close(self, interaction: discord.Interaction):
        meta = data.setdefault("tickets", {}).get(str(interaction.channel_id))
        if not meta:
            await interaction.response.send_message("Ticket data not found.", ephemeral=True)
            return
        if not is_support(interaction.user, meta, interaction.guild):
            await interaction.response.send_message("Only the configured support team can close tickets.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await close_ticket(interaction.channel, interaction.user, meta)
        await interaction.followup.send("Ticket closed and transcript logged (if a log channel is configured).", ephemeral=True)


def is_support(member, meta, guild):
    if not guild or not isinstance(member, discord.Member):
        return False
    role_id = meta.get("support_role_id") or guild_config(guild.id).get("support_role")
    if role_id and any(role.id == role_id for role in member.roles):
        return True
    return member.guild_permissions.administrator


async def close_ticket(channel, closed_by, meta):
    guild = channel.guild
    cfg = guild_config(guild.id)
    messages = []
    try:
        async for message in channel.history(limit=None, oldest_first=True):
            content = html.escape(message.content or "")
            attachments = " ".join(html.escape(a.url) for a in message.attachments)
            stamp = message.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
            messages.append(
                f"<div class='message'><b>{html.escape(str(message.author))}</b> "
                f"<small>{stamp}</small><p>{content}</p><p>{attachments}</p></div>"
            )
    except discord.HTTPException:
        messages.append("<p>Could not fetch the full message history.</p>")

    owner = guild.get_member(meta.get("owner_id")) if meta.get("owner_id") else None
    claimer = guild.get_member(meta.get("claimed_by")) if meta.get("claimed_by") else None
    transcript = f"""<!doctype html><html><head><meta charset="utf-8">
<title>Ticket transcript</title><style>
body{{font-family:Arial,sans-serif;background:#17191c;color:#eee;padding:24px}}
.message{{background:#24272b;padding:12px;margin:10px 0;border-radius:8px;overflow-wrap:anywhere}}
small{{color:#aaa;margin-left:12px}}p{{white-space:pre-wrap}}
</style></head><body><h1>Ticket transcript: {html.escape(channel.name)}</h1>
<p>Server: {html.escape(guild.name)}</p>
<p>Ticket owner: {html.escape(str(owner) if owner else str(meta.get('owner_id')))}</p>
<p>Claimed by: {html.escape(str(claimer) if claimer else str(meta.get('claimed_by') or 'Nobody'))}</p>
<p>Closed by: {html.escape(str(closed_by))}</p>
<p>Reason: {html.escape(meta.get('reason', ''))}</p>
<hr>{''.join(messages)}</body></html>"""
    # HTML in a ZIP prevents Discord from showing the raw HTML source as a preview.
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{channel.name}-transcript.html", transcript.encode("utf-8"))
    archive_bytes = archive_buffer.getvalue()
    archive_buffer.seek(0)
    file = discord.File(archive_buffer, filename=f"{channel.name}-transcript.zip")
    log_channel_id = cfg.get("ticket_log_channel")
    log_channel = guild.get_channel(log_channel_id) if log_channel_id else None
    if isinstance(log_channel, discord.TextChannel):
        embed = create_embed(title="🔒 Ticket closed", color=discord.Color.red(), timestamp=datetime.now(timezone.utc))
        embed.add_field(name="Ticket", value=channel.name, inline=True)
        embed.add_field(name="Owner", value=f"<@{meta.get('owner_id')}>", inline=True)
        embed.add_field(name="Closed by", value=closed_by.mention, inline=True)
        embed.add_field(name="Claimed by", value=f"<@{meta['claimed_by']}>" if meta.get("claimed_by") else "Nobody", inline=True)
        await log_channel.send(embed=embed, file=file)
    # Premium Ticket-System: send a second transcript copy to the ticket creator by DM.
    if guild_has_feature(cfg, "ticket") and meta.get("owner_id"):
        owner_user = owner or guild.get_member(int(meta["owner_id"]))
        if owner_user is None:
            try:
                owner_user = await bot.fetch_user(int(meta["owner_id"]))
            except (discord.HTTPException, ValueError, TypeError):
                owner_user = None
        if owner_user is not None:
            try:
                await owner_user.send(
                    f"Dein Ticket **{channel.name}** auf **{guild.name}** wurde geschlossen. Hier ist dein Transcript.",
                    file=discord.File(io.BytesIO(archive_bytes), filename=f"{channel.name}-transcript.zip"),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except (discord.Forbidden, discord.HTTPException):
                pass
    data.setdefault("tickets", {}).pop(str(channel.id), None)
    save_json(DATA_FILE, data)
    await channel.delete(reason=f"Ticket closed by {closed_by}")



# ----------------------------- LEVEL SYSTEM -----------------------------

import random


def get_user_level(xp: int) -> int:
    """Use the same cumulative XP curve as /rank and /level."""
    return calculate_level(max(0, int(xp)))


def xp_for_level(level: int) -> int:
    """Total XP required to reach a level."""
    return 100 * level * level


def add_message_xp(guild_id: int, user_id: int):
    """Award configured XP with a per-server cooldown and return only new level-ups."""
    cfg = guild_config(guild_id)
    if not cfg.get("xp_enabled", True):
        return 0, 0
    cooldown = max(10, min(600, int(cfg.get("xp_cooldown_seconds", 60) or 60)))
    xp_min = max(1, min(100, int(cfg.get("xp_min", 5) or 5)))
    xp_max = max(xp_min, min(100, int(cfg.get("xp_max", 15) or 15)))
    stats = get_user_stats(guild_id, user_id)
    now = time.time()
    last_xp_at = float(stats.get("last_xp_at", 0))
    old_xp = max(0, int(stats.get("xp", 0)))
    old_level = calculate_level(old_xp)
    if now - last_xp_at < cooldown:
        return 0, 0
    earned_xp = random.randint(xp_min, xp_max)
    stats["xp"] = old_xp + earned_xp
    stats["level"] = calculate_level(stats["xp"])
    stats["last_xp_at"] = now

    new_level = stats["level"]
    save_json(DATA_FILE, data)
    return earned_xp, new_level if new_level > old_level else 0

XP_COOLDOWN = 60
XP_MIN = 15
XP_MAX = 25

# Zeitpunkte werden nur im Speicher gehalten.
# Die XP selbst werden dauerhaft in data.json gespeichert.
_level_cooldowns = {}


def get_level_xp(guild_id, user_id):
    """Get or create persistent XP data for a member."""
    server_stats = data.setdefault("stats", {}).setdefault(str(guild_id), {})
    stats = server_stats.setdefault(
        str(user_id),
        {"messages": 0, "voice_seconds": 0}
    )

    stats.setdefault("xp", 0)
    stats.setdefault("level", 0)
    return stats


def xp_for_next_level(level):
    """XP required to reach the next level."""
    return 100 * (level + 1) ** 2


def calculate_level(xp):
    """Calculate level from total XP."""
    level = 0
    while xp >= xp_for_next_level(level):
        xp -= xp_for_next_level(level)
        level += 1
    return level


def total_xp_for_level(level):
    """Total XP needed to reach a level."""
    return sum(xp_for_next_level(i) for i in range(level))


async def award_message_xp(message):
    """Award XP with a cooldown and announce level-ups."""
    if not message.guild or message.author.bot:
        return

    content = (message.content or "").strip()
    if not content:
        return

    key = (message.guild.id, message.author.id)
    now = time.monotonic()

    last_award = _level_cooldowns.get(key, 0)
    if now - last_award < XP_COOLDOWN:
        return

    _level_cooldowns[key] = now

    stats = get_level_xp(message.guild.id, message.author.id)
    old_level = int(stats.get("level", 0))

    stats["xp"] = int(stats.get("xp", 0)) + random.randint(XP_MIN, XP_MAX)
    new_level = calculate_level(stats["xp"])
    stats["level"] = new_level

    save_json(DATA_FILE, data)

    if new_level > old_level:
        embed = create_embed(
            title="🎉 Levelaufstieg!",
            description=(
                f"Glückwunsch {message.author.mention}!\n\n"
                f"Du bist jetzt **Level {new_level}**!\n"
                f"⭐ XP insgesamt: **{stats['xp']}**"
            ),
            color=discord.Color.gold(),
        )
        await message.channel.send(embed=embed)
        cfg = guild_config(message.guild.id)
        role_id = cfg.get("level_roles", {}).get(str(new_level))
        role = message.guild.get_role(int(role_id)) if role_id else None
        if role and isinstance(message.author, discord.Member):
            try:
                await message.author.add_roles(role, reason=f"Level {new_level} erreicht")
            except (discord.Forbidden, discord.HTTPException):
                pass


@bot.command(name="level", aliases=["lvl"])
async def level_command(ctx, member: discord.Member = None):
    """Show a member's level and XP progress."""
    if ctx.guild is None:
        await ctx.reply("Dieser Befehl funktioniert nur auf einem Server.")
        return

    target = member or ctx.author
    stats = get_level_xp(ctx.guild.id, target.id)

    xp = int(stats.get("xp", 0))
    level = calculate_level(xp)
    current_level_xp = total_xp_for_level(level)
    next_level_xp = total_xp_for_level(level + 1)

    progress = xp - current_level_xp
    required = next_level_xp - current_level_xp
    percent = min(100, int(progress / required * 100)) if required else 100

    filled = int(percent / 10)
    progress_bar = "🟩" * filled + "⬛" * (10 - filled)

    embed = create_embed(
        title=f"🏆 Level von {target.display_name}",
        color=discord.Color.gold(),
    )
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(name="Level", value=f"**{level}**", inline=True)
    embed.add_field(name="Gesamt-XP", value=f"**{xp:,}**".replace(",", "."), inline=True)
    embed.add_field(
        name="Fortschritt",
        value=(
            f"{progress_bar}\n"
            f"**{progress} / {required} XP** ({percent} %)"
        ),
        inline=False,
    )

    await ctx.reply(embed=embed, mention_author=False)


@bot.command(name="leaderboard", aliases=["lb", "rangliste"])
async def leaderboard_command(ctx):
    """Show the ten highest-ranking members of this server."""
    if ctx.guild is None:
        await ctx.reply("Dieser Befehl funktioniert nur auf einem Server.")
        return

    server_stats = data.setdefault("stats", {}).get(str(ctx.guild.id), {})

    members = []
    for user_id, stats in server_stats.items():
        xp = int(stats.get("xp", 0))
        level = calculate_level(xp)
        members.append((int(user_id), xp, level))

    members.sort(key=lambda entry: entry[1], reverse=True)
    members = members[:10]

    if not members:
        await ctx.reply("Noch gibt es keine Leveldaten auf diesem Server.")
        return

    lines = []
    for rank, (user_id, xp, level) in enumerate(members, start=1):
        member = ctx.guild.get_member(user_id)
        name = member.display_name if member else f"Unbekannt ({user_id})"
        name = discord.utils.escape_markdown(name)

        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, f"`#{rank}`")
        lines.append(
            f"{medal} **{name}** — Level **{level}** · **{xp:,} XP**".replace(",", ".")
        )

    embed = create_embed(
        title=f"🏆 Rangliste — {ctx.guild.name}",
        description="\n".join(lines),
        color=discord.Color.gold(),
    )
    await ctx.reply(embed=embed, mention_author=False)


def _verify_code_hash(code: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", code.encode("utf-8"), salt, 200_000).hex()


def _make_captcha_image(code: str) -> io.BytesIO:
    """Render a randomized verification challenge as an in-memory PNG."""
    image = Image.new("RGB", (420, 150), (245, 247, 250))
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=52)
    small_font = ImageFont.load_default(size=18)

    # Light visual noise makes simple automated OCR less reliable while keeping
    # the characters readable for people.
    for _ in range(18):
        x1, y1 = random.randint(0, 420), random.randint(0, 150)
        x2, y2 = random.randint(0, 420), random.randint(0, 150)
        draw.line((x1, y1, x2, y2), fill=(random.randint(175, 220), random.randint(175, 220), random.randint(175, 220)), width=2)
    for _ in range(700):
        x, y = random.randint(0, 419), random.randint(0, 149)
        shade = random.randint(190, 235)
        draw.point((x, y), fill=(shade, shade, shade))

    # Draw each character with a small independent rotation.
    char_width = 58
    start_x = (420 - char_width * len(code)) // 2
    for index, char in enumerate(code):
        tile = Image.new("RGBA", (70, 86), (0, 0, 0, 0))
        tile_draw = ImageDraw.Draw(tile)
        tile_draw.text((12, 10), char, font=font, fill=(25, 43, 75, 255), stroke_width=1)
        tile = tile.rotate(random.randint(-14, 14), resample=Image.Resampling.BICUBIC, expand=False)
        image.paste(tile, (start_x + index * char_width, 28), tile)

    draw.text((12, 8), "VERIFIZIERUNGSCODE", font=small_font, fill=(70, 80, 95))
    draw.rectangle((1, 1, 418, 148), outline=(120, 130, 145), width=2)
    output = io.BytesIO()
    image.save(output, format="PNG")
    output.seek(0)
    return output


def _new_captcha_code(length: int = 5) -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(random.SystemRandom().choice(alphabet) for _ in range(length))


class CaptchaChoiceView(discord.ui.View):
    """One-use, user-bound captcha challenge with three choices."""
    def __init__(self, user_id: int, answer: str, choices: list[str]):
        super().__init__(timeout=120)
        self.user_id = user_id
        self.answer = answer
        for choice in choices:
            button = discord.ui.Button(label=choice, style=discord.ButtonStyle.primary)
            async def callback(interaction: discord.Interaction, selected=choice):
                if interaction.user.id != self.user_id:
                    await interaction.response.send_message(
                        "Diese Verifizierungsaufgabe gehört zu einer anderen Person. Drücke selbst auf den Verifizierungsbutton.",
                        ephemeral=True,
                    )
                    return
                self.stop()
                if selected != self.answer:
                    await interaction.response.send_message(
                        "❌ Leider falsch. Klicke im Verifizierungs-Panel erneut, um eine neue Aufgabe zu erhalten.",
                        ephemeral=True,
                    )
                    return
                if not interaction.guild or not isinstance(interaction.user, discord.Member):
                    await interaction.response.send_message("Das funktioniert nur auf einem Server.", ephemeral=True)
                    return
                # Acknowledge before Discord role edits, which may take time.
                await interaction.response.defer(ephemeral=True, thinking=True)
                cfg = guild_config(interaction.guild.id)
                await finish_verification(interaction, cfg)
            button.callback = callback
            self.add_item(button)


async def send_captcha_challenge(interaction: discord.Interaction):
    """Acknowledge the click immediately, then deliver the generated captcha."""
    await interaction.response.defer(ephemeral=True, thinking=True)
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    answer = _new_captcha_code()
    choices = {answer}
    while len(choices) < 3:
        candidate = "".join(random.SystemRandom().choice(alphabet) for _ in range(5))
        choices.add(candidate)
    choice_list = list(choices)
    random.SystemRandom().shuffle(choice_list)

    image_file = discord.File(_make_captcha_image(answer), filename="verification-captcha.png")
    embed = create_embed(
        title="🧩 Verifizierung",
        description="Lies den Code im Bild und drücke unten die passende von drei Antworten. Die Aufgabe läuft nach 2 Minuten ab.",
        color=discord.Color.blurple(),
    )
    embed.set_image(url="attachment://verification-captcha.png")
    await interaction.edit_original_response(
        embed=embed,
        attachments=[image_file],
        view=CaptchaChoiceView(interaction.user.id, answer, choice_list),
    )


async def finish_verification(interaction: discord.Interaction, cfg):
    """Apply roles and respond correctly whether the interaction was deferred or not."""
    async def respond(message: str):
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    verified_id = cfg.get("verified_role")
    unverified_id = cfg.get("unverified_role")
    verified_role = interaction.guild.get_role(int(verified_id)) if verified_id else None
    unverified_role = interaction.guild.get_role(int(unverified_id)) if unverified_id else None
    if not verified_role:
        await respond("Das Verifizierungs-Rollen-Setup ist unvollständig. Bitte Admin informieren.")
        return
    try:
        if unverified_role and unverified_role in interaction.user.roles:
            await interaction.user.remove_roles(unverified_role, reason="Mitglied hat Verifizierung abgeschlossen")
        if verified_role not in interaction.user.roles:
            await interaction.user.add_roles(verified_role, reason="Mitglied hat Verifizierung abgeschlossen")
    except (discord.Forbidden, discord.HTTPException) as exc:
        await respond(f"Rolle konnte nicht geändert werden: {type(exc).__name__}. Bitte Bot-Rollenposition prüfen.")
        return
    await respond("✅ Du bist jetzt verifiziert. Willkommen!")


class DashboardMessageView(discord.ui.View):
    """Persistent buttons created by the dashboard message builder."""
    def __init__(self, guild_id: int, buttons: list[dict]):
        super().__init__(timeout=None)
        self.guild_id = int(guild_id)
        for index, item in enumerate(buttons[:5]):
            label = str(item.get("label", "Button"))[:80]
            if item.get("type") == "link":
                self.add_item(discord.ui.Button(
                    label=label,
                    style=discord.ButtonStyle.link,
                    url=str(item.get("url", "")),
                ))
            elif item.get("type") == "role":
                role_id = int(item.get("role_id", 0))
                button = discord.ui.Button(
                    label=label,
                    style=discord.ButtonStyle.primary,
                    custom_id=f"iwrole:{self.guild_id}:{role_id}:{index}",
                )
                async def callback(interaction: discord.Interaction, rid=role_id):
                    await self.toggle_role(interaction, rid)
                button.callback = callback
                self.add_item(button)

    async def toggle_role(self, interaction: discord.Interaction, role_id: int):
        if not interaction.guild or interaction.guild.id != self.guild_id or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Dieser Button gehört zu einem anderen Server.", ephemeral=True)
            return
        role = interaction.guild.get_role(role_id)
        me = interaction.guild.me
        if role is None or role.is_default() or role.managed or me is None or role >= me.top_role:
            await interaction.response.send_message("Diese Rolle kann aktuell nicht vergeben werden. Bitte informiere das Team.", ephemeral=True)
            return
        try:
            if role in interaction.user.roles:
                await interaction.user.remove_roles(role, reason="Dashboard-Rollenbutton")
                message = f"Die Rolle {role.mention} wurde entfernt."
            else:
                await interaction.user.add_roles(role, reason="Dashboard-Rollenbutton")
                message = f"Die Rolle {role.mention} wurde dir gegeben."
            await interaction.response.send_message(message, ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("Der Bot darf diese Rolle nicht verwalten. Bitte Rollenposition und Berechtigungen prüfen.", ephemeral=True)
        except discord.HTTPException:
            await interaction.response.send_message("Discord konnte die Rolle gerade nicht ändern. Bitte versuche es erneut.", ephemeral=True)


def build_dashboard_message_view(guild_id: int, buttons: list[dict]):
    return DashboardMessageView(guild_id, buttons) if buttons else None


bot.build_dashboard_message_view = build_dashboard_message_view


class VerifyView(discord.ui.View):
    """Persistent verification panel; supports one-click and image-captcha verification."""
    def __init__(self, label: str = "Regeln akzeptieren / Verifizieren", emoji: str = "✅"):
        super().__init__(timeout=None)
        button = discord.ui.Button(
            label=(label or "Verifizieren")[:80],
            style=discord.ButtonStyle.success,
            emoji=emoji or None,
            custom_id="verify:accept",
        )
        button.callback = self.verify_button
        self.add_item(button)

    async def verify_button(self, interaction: discord.Interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Das funktioniert nur auf einem Server.", ephemeral=True)
            return
        cfg = guild_config(interaction.guild.id)
        verified_id = cfg.get("verified_role")
        if not verified_id or not interaction.guild.get_role(int(verified_id)):
            await interaction.response.send_message(
                "Das Verifizierungs-Rollen-Setup ist unvollständig. Bitte Admin informieren.",
                ephemeral=True,
            )
            return
        if cfg.get("verify_mode", "click") == "code":
            await send_captcha_challenge(interaction)
            return
        # Role changes can take longer than Discord's initial interaction window.
        await interaction.response.defer(ephemeral=True, thinking=True)
        await finish_verification(interaction, cfg)


# ----------------------------- SLASH COMMANDS -----------------------------

@bot.tree.command(name="sprache", description="Set your private registration form language")
@app_commands.describe(sprache="Choose a language")
@app_commands.choices(sprache=[
    app_commands.Choice(name="Deutsch", value="de"),
    app_commands.Choice(name="English", value="en"),
    app_commands.Choice(name="Čeština", value="cs"),
])
async def sprache(interaction: discord.Interaction, sprache: app_commands.Choice[str]):
    set_user_language(interaction.user.id, sprache.value)
    await interaction.response.send_message(TEXTS[sprache.value]["language_saved"], ephemeral=True)


@bot.tree.command(name="kanal-set", description="Set the public channel for sign-in/sign-out announcements")
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(kanal="Channel where public sign-in and sign-out messages are posted")
async def kanal_set(interaction: discord.Interaction, kanal: discord.TextChannel):
    guild_config(interaction.guild_id)["registration_channel"] = kanal.id
    save_json(CONFIG_FILE, config)
    await interaction.response.send_message(f"Registration announcement channel set to {kanal.mention}.", ephemeral=True)


@bot.tree.command(name="ticket-log-set", description="Set the channel for ticket transcripts")
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(kanal="Channel for closed ticket transcripts")
async def ticket_log_set(interaction: discord.Interaction, kanal: discord.TextChannel):
    guild_config(interaction.guild_id)["ticket_log_channel"] = kanal.id
    save_json(CONFIG_FILE, config)
    await interaction.response.send_message(f"Ticket log channel set to {kanal.mention}.", ephemeral=True)


@bot.tree.command(name="ticket-support-set", description="Set the support role allowed to claim and close tickets")
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(rolle="Support role")
async def ticket_support_set(interaction: discord.Interaction, rolle: discord.Role):
    guild_config(interaction.guild_id)["support_role"] = rolle.id
    save_json(CONFIG_FILE, config)
    await interaction.response.send_message(f"Support role set to {rolle.mention}.", ephemeral=True)


@bot.tree.command(name="ticket-category-set", description="Set the default category for ticket channels")
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(kategorie="Category where new tickets are created")
async def ticket_category_set(interaction: discord.Interaction, kategorie: discord.CategoryChannel):
    guild_config(interaction.guild_id)["ticket_category"] = kategorie.id
    save_json(CONFIG_FILE, config)
    await interaction.response.send_message(f"Default ticket category set to **{kategorie.name}**.", ephemeral=True)


@bot.tree.command(name="ticketcreat", description="Create a ticket panel with up to five buttons")
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(
    kanal="Channel where the panel should be posted",
    titel="Panel title",
    beschreibung="Panel description",
    button1="First button label (required)",
    button2="Second button label (optional)",
    button3="Third button label (optional)",
    button4="Fourth button label (optional)",
    button5="Fifth button label (optional)",
    kategorie="Optional category for this panel's tickets",
    support_rolle="Optional support role for this panel",
)
async def ticketcreat(
    interaction: discord.Interaction,
    kanal: discord.TextChannel,
    titel: str,
    beschreibung: str,
    button1: str,
    button2: str = None,
    button3: str = None,
    button4: str = None,
    button5: str = None,
    kategorie: discord.CategoryChannel = None,
    support_rolle: Optional[discord.Role] = None,
):
    labels = [x.strip() for x in [button1, button2, button3, button4, button5] if x and x.strip()]
    if not labels:
        await interaction.response.send_message("Add at least one button label.", ephemeral=True)
        return
    cfg = guild_config(interaction.guild_id)
    panel_key = str(int(datetime.now(timezone.utc).timestamp()))
    cfg["ticket_panels"][panel_key] = {
        "title": titel,
        "description": beschreibung,
        "labels": labels,
        "category_id": kategorie.id if kategorie else cfg.get("ticket_category"),
        "support_role_id": support_rolle.id if support_rolle else cfg.get("support_role"),
        "channel_id": kanal.id,
    }
    save_json(CONFIG_FILE, config)
    embed = create_embed(title=titel, description=beschreibung, color=discord.Color.blurple())
    embed.set_footer(text="Choose a button to open a ticket. • Created by KingSnowy")
    await kanal.send(embed=embed, view=TicketPanelView(panel_key, labels))
    await interaction.response.send_message(f"Ticket panel posted in {kanal.mention}.", ephemeral=True)


@bot.tree.command(name="ticketbearbeiten", description="Edit the title, description and button labels of a ticket panel")
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(
    panel_id="Panel ID shown by the bot when created (timestamp key)",
    titel="New panel title",
    beschreibung="New panel description",
    button1="First button label (required)",
    button2="Second button label (optional)",
    button3="Third button label (optional)",
    button4="Fourth button label (optional)",
    button5="Fifth button label (optional)",
)
async def ticketbearbeiten(
    interaction: discord.Interaction,
    panel_id: str,
    titel: str,
    beschreibung: str,
    button1: str,
    button2: str = None,
    button3: str = None,
    button4: str = None,
    button5: str = None,
):
    cfg = guild_config(interaction.guild_id)
    panel = cfg.get("ticket_panels", {}).get(panel_id)
    if not panel:
        await interaction.response.send_message("Panel ID not found in this server's config.", ephemeral=True)
        return
    labels = [x.strip() for x in [button1, button2, button3, button4, button5] if x and x.strip()]
    panel.update({"title": titel, "description": beschreibung, "labels": labels})
    save_json(CONFIG_FILE, config)
    channel = interaction.guild.get_channel(panel.get("channel_id"))
    if not isinstance(channel, discord.TextChannel):
        await interaction.response.send_message("Panel channel not found. The panel config was updated.", ephemeral=True)
        return
    embed = create_embed(title=titel, description=beschreibung, color=discord.Color.blurple())
    embed.set_footer(text=f"Edited panel ID: {panel_id} • Created by KingSnowy")
    message = await channel.send(embed=embed, view=TicketPanelView(panel_id, labels))
    await interaction.response.send_message(
        f"Updated panel posted in {channel.mention}. New message ID: `{message.id}`. Panel ID: `{panel_id}`",
        ephemeral=True
    )





@bot.tree.command(name="help", description="Zeigt alle aktuell registrierten Befehle")
async def help_command(interaction: discord.Interaction):
    """Zeigt registrierte Slash- und Prefix-Befehle in navigierbaren Seiten."""
    slash_commands = []
    prefix_commands = []

    def collect_slash(commands_list, parent=""):
        for command in commands_list:
            full_name = f"{parent} {command.name}".strip()
            if full_name == "help":
                continue
            if isinstance(command, app_commands.Group):
                collect_slash(command.commands, full_name)
            else:
                description = getattr(command, "description", None) or "Keine Beschreibung vorhanden."
                slash_commands.append((f"/{full_name}", description, "Slash"))

    collect_slash(bot.tree.get_commands())

    for command in bot.commands:
        if command.hidden or command.name == "help":
            continue
        description = (command.help or command.brief or "Keine Beschreibung vorhanden.").strip()
        for command_name in [command.name, *command.aliases]:
            prefix_commands.append((f"!{command_name}", description, "Prefix"))

    entries = sorted(slash_commands + prefix_commands, key=lambda item: item[0].casefold())
    if not entries:
        await interaction.response.send_message("Es sind derzeit keine Befehle registriert.", ephemeral=True)
        return

    # Keep pages comfortably below Discord's embed field limits.
    page_size = 10
    page_entries = [entries[i:i + page_size] for i in range(0, len(entries), page_size)]
    pages = []
    for page_number, chunk in enumerate(page_entries, start=1):
        embed = create_embed(
            title="📖 Hilfe – aktuelle Befehle",
            description=(
                "Alle aktuell registrierten Befehle. Nutze die Buttons zum Blättern.\n"
                "**Slash-Befehle** beginnen mit `/`, **Prefix-Befehle** beginnen mit `!`."
            ),
            color=discord.Color.blurple(),
        )
        for name, description, kind in chunk:
            description = description[:1000]
            embed.add_field(name=f"{name} · {kind}", value=description, inline=False)
        embed.set_footer(
            text=f"{len(slash_commands)} Slash • {len(prefix_commands)} Prefix • Seite {page_number}/{len(page_entries)}"
        )
        pages.append(embed)

    class HelpPaginationView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=180)
            self.page = 0
            self.owner_id = interaction.user.id
            self._update_buttons()

        def _update_buttons(self):
            self.previous_button.disabled = self.page <= 0
            self.next_button.disabled = self.page >= len(pages) - 1

        async def interaction_check(self, button_interaction: discord.Interaction) -> bool:
            if button_interaction.user.id != self.owner_id:
                await button_interaction.response.send_message(
                    "Diese Hilfe-Navigation gehört einer anderen Person.", ephemeral=True
                )
                return False
            return True

        @discord.ui.button(label="◀ Zurück", style=discord.ButtonStyle.secondary)
        async def previous_button(self, button_interaction: discord.Interaction, button: discord.ui.Button):
            self.page = max(0, self.page - 1)
            self._update_buttons()
            await button_interaction.response.edit_message(embed=pages[self.page], view=self)

        @discord.ui.button(label="Weiter ▶", style=discord.ButtonStyle.primary)
        async def next_button(self, button_interaction: discord.Interaction, button: discord.ui.Button):
            self.page = min(len(pages) - 1, self.page + 1)
            self._update_buttons()
            await button_interaction.response.edit_message(embed=pages[self.page], view=self)

    view = HelpPaginationView()
    await interaction.response.send_message(embed=pages[0], view=view, ephemeral=True)



# ----------------------------- CLAN ACTIVITY TRACKING -----------------------------

def _activity_timestamp(stats):
    """Return the most recent recorded message/voice activity as an aware datetime."""
    values = []
    for key in ("last_message_at", "last_voice_at", "last_activity_at"):
        raw = stats.get(key)
        if not raw:
            continue
        try:
            parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            values.append(parsed.astimezone(timezone.utc))
        except (TypeError, ValueError):
            continue
    return max(values) if values else None


def _member_last_activity(member):
    stats = get_user_stats(member.guild.id, member.id)
    recorded = _activity_timestamp(stats)
    joined = member.joined_at
    if joined is not None:
        if joined.tzinfo is None:
            joined = joined.replace(tzinfo=timezone.utc)
        joined = joined.astimezone(timezone.utc)
    return max([value for value in (recorded, joined) if value is not None], default=None)


def _activity_age_text(last_activity, now=None):
    if last_activity is None:
        return "Keine Aktivität erfasst"
    now = now or datetime.now(timezone.utc)
    age = max(0, int((now - last_activity).total_seconds()))
    return f"vor {format_duration(age)}"


@bot.tree.command(name="aktivitaet", description="Zeigt Nachrichten, Voice-Zeit und letzte Aktivität eines Mitglieds")
@app_commands.guild_only()
@app_commands.describe(mitglied="Mitglied, dessen Aktivität du ansehen möchtest")
async def clan_activity(interaction: discord.Interaction, mitglied: discord.Member | None = None):
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("Dieser Befehl funktioniert nur auf einem Server.", ephemeral=True)
        return
    target = mitglied or interaction.user
    stats = get_user_stats(guild.id, target.id)
    voice_seconds = int(stats.get("voice_seconds", 0))
    started_at = _active_voice_sessions.get((guild.id, target.id))
    if started_at is not None:
        voice_seconds += max(0, int((datetime.now(timezone.utc) - started_at).total_seconds()))
    last_activity = _member_last_activity(target)
    embed = create_embed(
        title=f"🦖 Clan-Aktivität · {target.display_name}",
        color=discord.Color.blurple(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(name="💬 Nachrichten", value=f"{int(stats.get('messages', 0)):,}".replace(",", "."), inline=True)
    embed.add_field(name="🔊 Voice-Zeit", value=format_duration(voice_seconds), inline=True)
    embed.add_field(name="🕒 Letzte Aktivität", value=_activity_age_text(last_activity), inline=False)
    if last_activity:
        embed.add_field(name="📅 Datum", value=discord.utils.format_dt(last_activity, style="F"), inline=False)
    embed.set_footer(text="Aktivität wird ab Aktivierung der Erfassung gespeichert. • Created by KingSnowy")
    await interaction.response.send_message(embed=embed, ephemeral=(target.id != interaction.user.id))


@bot.tree.command(name="inaktive", description="Listet Mitglieder ohne kürzliche Aktivität auf")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(tage="Als inaktiv gelten Mitglieder ohne Aktivität seit mindestens so vielen Tagen (1–365)")
async def inactive_members(interaction: discord.Interaction, tage: app_commands.Range[int, 1, 365] = 14):
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("Dieser Befehl funktioniert nur auf einem Server.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=int(tage))
    inactive = []
    for member in guild.members:
        if member.bot:
            continue
        # _member_last_activity uses the later of tracked activity and join
        # date, so recent re-joins are not marked inactive based on old history.
        last_activity = _member_last_activity(member)
        if last_activity is None or last_activity < cutoff:
            inactive.append((last_activity, member))
    inactive.sort(key=lambda item: item[0] or datetime.min.replace(tzinfo=timezone.utc))
    lines = [f"• {member.mention} — {_activity_age_text(last_activity, now)}" for last_activity, member in inactive[:40]]
    if not lines:
        description = f"Keine Mitglieder mit mindestens {tage} Tagen ohne erfasste Aktivität gefunden."
    else:
        description = "\n".join(lines)
        if len(inactive) > 40:
            description += f"\n… und {len(inactive) - 40} weitere."
    embed = create_embed(
        title=f"💤 Inaktive Mitglieder · {tage}+ Tage",
        description=description[:4000],
        color=discord.Color.orange() if inactive else discord.Color.green(),
        timestamp=now,
    )
    embed.set_footer(text=f"{len(inactive)} inaktive Mitglieder gefunden · Beitrittsdatum ist Basis, wenn keine Aktivität vorliegt")
    await interaction.followup.send(embed=embed, ephemeral=True)


@bot.tree.command(name="aktivitaetskanal", description="Legt den Kanal für Clan-Aktivitätsberichte fest")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(kanal="Textkanal, in dem Aktivitätsberichte veröffentlicht werden")
async def set_activity_log_channel(interaction: discord.Interaction, kanal: discord.TextChannel):
    cfg = guild_config(interaction.guild_id)
    cfg["activity_log_channel"] = kanal.id
    save_json(CONFIG_FILE, config)
    await interaction.response.send_message(
        f"✅ Aktivitätsberichte werden in {kanal.mention} veröffentlicht.", ephemeral=True
    )


@bot.tree.command(name="aktivitaetsbericht", description="Erstellt und protokolliert einen Clan-Aktivitätsbericht")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(tage="Zeitraum für die Inaktivitätsübersicht (1–365 Tage)")
async def activity_report(interaction: discord.Interaction, tage: app_commands.Range[int, 1, 365] = 14):
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("Dieser Befehl funktioniert nur auf einem Server.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=int(tage))
    members = [m for m in guild.members if not m.bot]
    active = []
    inactive = []
    total_messages = 0
    total_voice_seconds = 0
    for member in members:
        stats = get_user_stats(guild.id, member.id)
        total_messages += int(stats.get("messages", 0))
        total_voice_seconds += int(stats.get("voice_seconds", 0))
        started_at = _active_voice_sessions.get((guild.id, member.id))
        if started_at:
            total_voice_seconds += max(0, int((now - started_at).total_seconds()))
        recorded_activity = _activity_timestamp(stats)
        last_activity = recorded_activity or _member_last_activity(member)
        if recorded_activity and recorded_activity >= cutoff:
            active.append((last_activity, member))
        else:
            inactive.append((last_activity, member))
    active.sort(key=lambda item: item[0], reverse=True)
    inactive.sort(key=lambda item: item[0] or datetime.min.replace(tzinfo=timezone.utc))
    top_lines = [f"• {member.display_name} — {_activity_age_text(last_activity, now)}" for last_activity, member in active[:10]]
    embed = create_embed(
        title=f"📊 Clan-Aktivitätsbericht · letzte {tage} Tage",
        description=(
            f"**Mitglieder:** {len(members)}\n"
            f"**Aktiv im Zeitraum:** {len(active)}\n"
            f"**Inaktiv / keine Aktivität:** {len(inactive)}\n"
            f"**Erfasste Nachrichten gesamt:** {total_messages:,}\n"
            f"**Erfasste Voice-Zeit gesamt:** {format_duration(total_voice_seconds)}\n\n"
            f"**Zuletzt aktive Mitglieder**\n" + ("\n".join(top_lines) if top_lines else "Noch keine Aktivität erfasst.")
        ).replace(",", "."),
        color=discord.Color.blurple(),
        timestamp=now,
    )
    embed.set_footer(text="Statistiken werden ab Aktivierung der Erfassung gesammelt. • Created by KingSnowy")
    cfg = guild_config(guild.id)
    channel_id = cfg.get("activity_log_channel")
    log_channel = guild.get_channel(int(channel_id)) if channel_id else None
    if isinstance(log_channel, discord.TextChannel):
        try:
            await log_channel.send(embed=embed)
            await interaction.followup.send(f"✅ Aktivitätsbericht wurde in {log_channel.mention} protokolliert.", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send(
                f"❌ Ich kann nicht in {log_channel.mention} schreiben. Prüfe die Kanalrechte.", ephemeral=True
            )
    else:
        await interaction.followup.send(
            "ℹ️ Es ist kein Aktivitätskanal eingerichtet. Bericht wird dir privat angezeigt. "
            "Nutze zuerst /aktivitaetskanal, um einen Protokollkanal festzulegen.",
            embed=embed,
            ephemeral=True,
        )


# ----------------------------- PREFIX COMMANDS -----------------------------

@bot.command(name="stats")
async def stats_command(ctx, member: discord.Member = None):
    """Show message and voice-channel statistics for yourself or another member."""
    if ctx.guild is None:
        await ctx.reply("Dieser Befehl funktioniert nur auf einem Server.", mention_author=False)
        return

    target = member or ctx.author
    stats = get_user_stats(ctx.guild.id, target.id)
    voice_seconds = int(stats.get("voice_seconds", 0))

    # Include the current voice session if the member is connected right now.
    started_at = _active_voice_sessions.get((ctx.guild.id, target.id))
    if started_at is not None:
        voice_seconds += max(
            0, int((datetime.now(timezone.utc) - started_at).total_seconds())
        )

    embed = create_embed(
        title=f"📊 Stats von {target.display_name}",
        color=discord.Color.blurple(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(
        name="💬 Nachrichten",
        value=f"{int(stats.get('messages', 0)):,}".replace(",", "."),
        inline=True,
    )
    embed.add_field(
        name="🔊 Zeit im Sprachkanal",
        value=format_duration(voice_seconds),
        inline=True,
    )
    embed.set_footer(text="Die Statistiken werden ab Aktivierung dieses Systems gezählt. • Created by KingSnowy")
    await ctx.reply(embed=embed, mention_author=False)


@bot.command(name="anmeldepannel", aliases=["anmeldepanel"])
@commands.has_guild_permissions(manage_guild=True)
async def anmeldepannel(ctx):
    embed = create_embed(
        title="🦖 The Isle — Server Sign-In",
        description=(
            "Welcome to the server registration panel!\n"
            "Willkommen beim Server-Anmeldepanel!\n"
            "Vítejte v registračním panelu serveru!\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "**How to use / Anleitung / Návod**\n"
            "1. Choose your private language below.\n"
            "2. Press **Sign In** and fill in Dinosaur, Skin and Location.\n"
            "3. When you leave, press **Sign Out**.\n\n"
            "1. Wähle unten deine private Sprache.\n"
            "2. Drücke **Sign In** und fülle Dinosaurier, Skin und Standort aus.\n"
            "3. Wenn du gehst, drücke **Sign Out**.\n\n"
            "1. Níže si vyber jazyk soukromého formuláře.\n"
            "2. Klikni na **Sign In** a vyplň dinosaura, skin a lokaci.\n"
            "3. Při odchodu klikni na **Sign Out**.\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Language selection changes only your private form and confirmation. "
            "Public sign-in and sign-out announcements are always in English."
        ),
        color=discord.Color.blurple(),
    )
    embed.set_footer(text="Created by KingSnowy")
    await ctx.send(embed=embed, view=RegistrationView())
    language_embed = create_embed(
        title="🌐 Language / Sprache / Jazyk",
        description="Choose the language for your private form and confirmation.",
        color=discord.Color.teal(),
    )
    await ctx.send(embed=language_embed, view=LanguageView())


@anmeldepannel.error
async def registration_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.reply("You need the **Manage Server** permission to create this panel.", mention_author=False)
    else:
        await ctx.reply(f"Command error: `{error}`", mention_author=False)




# ----------------------------- MODERATION -----------------------------

def _moderation_log(guild_id):
    return data.setdefault("moderation_warnings", {}).setdefault(str(guild_id), {})


def _can_moderate(interaction: discord.Interaction, member: discord.Member):
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return False, "Dieser Befehl funktioniert nur auf einem Server."
    if member.id == interaction.user.id:
        return False, "Du kannst diese Aktion nicht gegen dich selbst ausführen."
    if member.id == interaction.guild.owner_id:
        return False, "Der Serverinhaber kann nicht moderiert werden."
    if interaction.user.id != interaction.guild.owner_id and member.top_role >= interaction.user.top_role:
        return False, "Deine höchste Rolle muss über der höchsten Rolle des Zielmitglieds liegen."
    if interaction.guild.me and member.top_role >= interaction.guild.me.top_role:
        return False, "Die höchste Rolle des Bots muss über der höchsten Rolle des Zielmitglieds liegen."
    return True, ""


@bot.tree.command(name="warn", description="Warne ein Mitglied und speichere den Grund")
@app_commands.checks.has_permissions(moderate_members=True)
@app_commands.describe(mitglied="Mitglied, das verwarnt werden soll", grund="Grund der Verwarnung")
async def moderation_warn(interaction: discord.Interaction, mitglied: discord.Member, grund: str):
    ok, error = _can_moderate(interaction, mitglied)
    if not ok:
        await interaction.response.send_message(error, ephemeral=True); return
    records = _moderation_log(interaction.guild_id).setdefault(str(mitglied.id), [])
    records.append({"moderator_id": interaction.user.id, "reason": grund[:1000], "timestamp": datetime.now(timezone.utc).isoformat()})
    save_json(DATA_FILE, data)
    case_id = _new_mod_case(interaction.guild_id, mitglied.id, interaction.user.id, "WARN", grund)
    try:
        await mitglied.send(f"Du wurdest auf **{interaction.guild.name}** verwarnt. Grund: {grund[:1000]}")
    except (discord.Forbidden, discord.HTTPException):
        pass
    await interaction.response.send_message(f"⚠️ {mitglied.mention} wurde verwarnt. Verwarnungen insgesamt: **{len(records)}**. Grund: {grund[:500]}", ephemeral=True)
    await security_log(interaction.guild, "⚠️ Mitglied verwarnt", f"Mitglied: {mitglied.mention} (`{mitglied.id}`)\nModerator: {interaction.user.mention}\nGrund: {grund[:800]}", discord.Color.orange())


@bot.tree.command(name="warnings", description="Zeige gespeicherte Verwarnungen eines Mitglieds")
@app_commands.checks.has_permissions(moderate_members=True)
async def moderation_warnings(interaction: discord.Interaction, mitglied: discord.Member):
    records = _moderation_log(interaction.guild_id).get(str(mitglied.id), [])
    if not records:
        await interaction.response.send_message(f"Für {mitglied.mention} sind keine Verwarnungen gespeichert.", ephemeral=True); return
    lines = [f"**{i}.** {r.get('reason','Kein Grund')} — <@{r.get('moderator_id','0')}> · {r.get('timestamp','')[:10]}" for i, r in enumerate(records[-15:], 1)]
    await interaction.response.send_message(embed=create_embed(title=f"Verwarnungen: {mitglied}", description="\n".join(lines)[:4000], color=discord.Color.orange()), ephemeral=True)


@bot.tree.command(name="clear-warnings", description="Lösche alle gespeicherten Verwarnungen eines Mitglieds")
@app_commands.checks.has_permissions(moderate_members=True)
async def moderation_clear_warnings(interaction: discord.Interaction, mitglied: discord.Member):
    _moderation_log(interaction.guild_id).pop(str(mitglied.id), None)
    save_json(DATA_FILE, data)
    await interaction.response.send_message(f"Verwarnungen für {mitglied.mention} wurden gelöscht.", ephemeral=True)


@bot.tree.command(name="timeout", description="Schalte ein Mitglied vorübergehend stumm")
@app_commands.checks.has_permissions(moderate_members=True)
@app_commands.describe(mitglied="Zielmitglied", minuten="Dauer in Minuten (1–10080)", grund="Grund")
async def moderation_timeout(interaction: discord.Interaction, mitglied: discord.Member, minuten: app_commands.Range[int, 1, 10080], grund: str = "Kein Grund angegeben"):
    ok, error = _can_moderate(interaction, mitglied)
    if not ok:
        await interaction.response.send_message(error, ephemeral=True); return
    try:
        await mitglied.timeout(timedelta(minutes=minuten), reason=f"{interaction.user}: {grund[:400]}")
    except (discord.Forbidden, discord.HTTPException) as exc:
        await interaction.response.send_message(f"Timeout fehlgeschlagen: {type(exc).__name__}. Prüfe Bot-Rechte und Rollenposition.", ephemeral=True); return
    case_id = _new_mod_case(interaction.guild_id, mitglied.id, interaction.user.id, f"TIMEOUT {minuten}m", grund)
    await interaction.response.send_message(f"🔇 {mitglied.mention} erhielt einen Timeout für **{minuten} Minuten**. Fall `#{case_id}`. Grund: {grund[:500]}")
    await security_log(interaction.guild, "🔇 Timeout", f"Mitglied: {mitglied.mention}\nModerator: {interaction.user.mention}\nDauer: {minuten} Minuten\nGrund: {grund[:800]}", discord.Color.orange())


@bot.tree.command(name="untimeout", description="Entferne den Timeout eines Mitglieds")
@app_commands.checks.has_permissions(moderate_members=True)
async def moderation_untimeout(interaction: discord.Interaction, mitglied: discord.Member, grund: str = "Kein Grund angegeben"):
    ok, error = _can_moderate(interaction, mitglied)
    if not ok:
        await interaction.response.send_message(error, ephemeral=True); return
    try:
        await mitglied.timeout(None, reason=f"{interaction.user}: {grund[:400]}")
    except (discord.Forbidden, discord.HTTPException) as exc:
        await interaction.response.send_message(f"Timeout konnte nicht entfernt werden: {type(exc).__name__}.", ephemeral=True); return
    case_id = _new_mod_case(interaction.guild_id, mitglied.id, interaction.user.id, "UNTIMEOUT", grund)
    await interaction.response.send_message(f"🔊 Timeout von {mitglied.mention} wurde entfernt. Fall `#{case_id}`.")


@bot.tree.command(name="kick", description="Entferne ein Mitglied vom Server")
@app_commands.checks.has_permissions(kick_members=True)
async def moderation_kick(interaction: discord.Interaction, mitglied: discord.Member, grund: str = "Kein Grund angegeben"):
    ok, error = _can_moderate(interaction, mitglied)
    if not ok:
        await interaction.response.send_message(error, ephemeral=True); return
    try:
        await mitglied.kick(reason=f"{interaction.user}: {grund[:400]}")
    except (discord.Forbidden, discord.HTTPException) as exc:
        await interaction.response.send_message(f"Kick fehlgeschlagen: {type(exc).__name__}.", ephemeral=True); return
    case_id = _new_mod_case(interaction.guild_id, mitglied.id, interaction.user.id, "KICK", grund)
    await interaction.response.send_message(f"👢 {mitglied} wurde entfernt. Fall `#{case_id}`. Grund: {grund[:500]}")
    await security_log(interaction.guild, "👢 Mitglied gekickt", f"Mitglied: {mitglied} (`{mitglied.id}`)\nModerator: {interaction.user.mention}\nGrund: {grund[:800]}", discord.Color.orange())


@bot.tree.command(name="ban", description="Sperre ein Mitglied vom Server")
@app_commands.checks.has_permissions(ban_members=True)
@app_commands.describe(mitglied="Zielmitglied", grund="Grund", nachrichten_tage="Nachrichten der letzten 0–7 Tage löschen")
async def moderation_ban(interaction: discord.Interaction, mitglied: discord.Member, grund: str = "Kein Grund angegeben", nachrichten_tage: app_commands.Range[int, 0, 7] = 0):
    ok, error = _can_moderate(interaction, mitglied)
    if not ok:
        await interaction.response.send_message(error, ephemeral=True); return
    try:
        await interaction.guild.ban(mitglied, reason=f"{interaction.user}: {grund[:400]}", delete_message_seconds=nachrichten_tage * 86400)
    except (discord.Forbidden, discord.HTTPException) as exc:
        await interaction.response.send_message(f"Ban fehlgeschlagen: {type(exc).__name__}.", ephemeral=True); return
    case_id = _new_mod_case(interaction.guild_id, mitglied.id, interaction.user.id, "BAN", grund)
    await interaction.response.send_message(f"🔨 {mitglied} wurde gebannt. Fall `#{case_id}`. Grund: {grund[:500]}")
    await security_log(interaction.guild, "🔨 Mitglied gebannt", f"Mitglied: {mitglied} (`{mitglied.id}`)\nModerator: {interaction.user.mention}\nGrund: {grund[:800]}", discord.Color.red())


@bot.tree.command(name="purge", description="Lösche bis zu 100 Nachrichten im aktuellen Kanal")
@app_commands.checks.has_permissions(manage_messages=True)
@app_commands.describe(anzahl="Anzahl der Nachrichten (1–100)")
async def moderation_purge(interaction: discord.Interaction, anzahl: app_commands.Range[int, 1, 100]):
    if not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message("Dieser Befehl funktioniert nur in Textkanälen.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    try:
        deleted = await interaction.channel.purge(limit=anzahl)
        await interaction.followup.send(f"🧹 {len(deleted)} Nachrichten gelöscht.", ephemeral=True)
    except (discord.Forbidden, discord.HTTPException) as exc:
        await interaction.followup.send(f"Löschen fehlgeschlagen: {type(exc).__name__}.", ephemeral=True)


@bot.tree.command(name="clean", description="Bereinige gezielt Nachrichten im aktuellen Textkanal")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_messages=True)
@app_commands.describe(
    typ="Welche Nachrichten sollen gelöscht werden?",
    anzahl="Wie viele Nachrichten maximal durchsuchen (1–100)",
    mitglied="Optional: nur Nachrichten dieses Mitglieds löschen",
)
@app_commands.choices(typ=[
    app_commands.Choice(name="Alle Nachrichten", value="all"),
    app_commands.Choice(name="Nur Bot-Nachrichten", value="bots"),
    app_commands.Choice(name="Nur Links", value="links"),
    app_commands.Choice(name="Nur Nachrichten mit Anhängen", value="attachments"),
    app_commands.Choice(name="Nur Befehlsnachrichten (! oder /)", value="commands"),
])
async def moderation_clean(
    interaction: discord.Interaction,
    typ: app_commands.Choice[str],
    anzahl: app_commands.Range[int, 1, 100] = 50,
    mitglied: discord.Member | None = None,
):
    """Clean selected messages from the current channel; never deletes channels or roles."""
    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel):
        await interaction.response.send_message(
            "❌ Dieser Befehl funktioniert nur in normalen Textkanälen.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True, thinking=True)

    def should_delete(message: discord.Message) -> bool:
        if mitglied is not None and message.author.id != mitglied.id:
            return False
        if typ.value == "all":
            return True
        if typ.value == "bots":
            return message.author.bot
        if typ.value == "links":
            content = (message.content or "").lower()
            return "http://" in content or "https://" in content or "www." in content or "discord.gg/" in content
        if typ.value == "attachments":
            return bool(message.attachments)
        if typ.value == "commands":
            content = (message.content or "").lstrip()
            return content.startswith("!") or content.startswith("/") or content.startswith(bot.command_prefix if isinstance(bot.command_prefix, str) else "!")
        return False

    try:
        deleted = await channel.purge(limit=anzahl, check=should_delete, bulk=True, reason=f"/clean von {interaction.user} ({interaction.user.id}); Typ: {typ.value}")
        await interaction.followup.send(
            f"🧹 **Bereinigung abgeschlossen**\n"
            f"**Kanal:** {channel.mention}\n"
            f"**Auswahl:** {typ.name}\n"
            f"**Durchsuchte Nachrichten:** maximal {anzahl}\n"
            f"**Gelöscht:** {len(deleted)} Nachricht(en)"
            + (f"\n**Mitgliedsfilter:** {mitglied.mention}" if mitglied else ""),
            ephemeral=True,
        )
    except discord.Forbidden:
        await interaction.followup.send(
            "❌ Mir fehlen Rechte zum Nachrichtenverlauf-Lesen oder Nachrichten-Verwalten in diesem Kanal.",
            ephemeral=True,
        )
    except discord.HTTPException as exc:
        await interaction.followup.send(
            f"❌ Discord konnte die Bereinigung nicht abschließen ({type(exc).__name__}).",
            ephemeral=True,
        )


@bot.tree.command(name="slowmode", description="Setze den Nachrichten-Abstand für den aktuellen Kanal")
@app_commands.checks.has_permissions(manage_channels=True)
@app_commands.describe(sekunden="0 deaktiviert Slowmode; maximal 21600 Sekunden")
async def moderation_slowmode(interaction: discord.Interaction, sekunden: app_commands.Range[int, 0, 21600]):
    if not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message("Dieser Befehl funktioniert nur in Textkanälen.", ephemeral=True); return
    try:
        await interaction.channel.edit(slowmode_delay=sekunden, reason=f"Slowmode von {interaction.user}")
    except (discord.Forbidden, discord.HTTPException) as exc:
        await interaction.response.send_message(f"Slowmode konnte nicht geändert werden: {type(exc).__name__}.", ephemeral=True); return
    await interaction.response.send_message(f"Slowmode gesetzt: **{sekunden} Sekunden**.")


# ----------------------------- WELCOME -----------------------------

@bot.tree.command(name="welcome-set", description="Setze den Begrüßungskanal")
@app_commands.checks.has_permissions(manage_guild=True)
async def welcome_set(interaction: discord.Interaction, kanal: discord.TextChannel):
    guild_config(interaction.guild_id)["welcome_channel"] = kanal.id
    save_json(CONFIG_FILE, config)
    await interaction.response.send_message(f"Begrüßungen werden künftig in {kanal.mention} gesendet.", ephemeral=True)


@bot.tree.command(name="welcome-message", description="Lege den Begrüßungstext fest")
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(text="Platzhalter: {mention}, {user}, {server}, {count}")
async def welcome_message(interaction: discord.Interaction, text: str):
    if len(text) > 1500:
        await interaction.response.send_message("Der Text darf maximal 1500 Zeichen lang sein.", ephemeral=True); return
    guild_config(interaction.guild_id)["welcome_message"] = text
    save_json(CONFIG_FILE, config)
    await interaction.response.send_message("Begrüßungstext gespeichert. Platzhalter: `{mention}`, `{user}`, `{server}`, `{count}`.", ephemeral=True)


@bot.tree.command(name="welcome-bots", description="Schalte Begrüßungsnachrichten für Bots ein oder aus")
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(aktiv="Ja = Bots begrüßen, Nein = Bots nicht begrüßen")
async def welcome_bots(interaction: discord.Interaction, aktiv: bool):
    cfg = guild_config(interaction.guild_id)
    cfg["welcome_bots_enabled"] = aktiv
    save_json(CONFIG_FILE, config)
    status = "aktiviert" if aktiv else "deaktiviert"
    await interaction.response.send_message(
        f"Begrüßungsnachrichten für Bots sind jetzt **{status}**.", ephemeral=True
    )


# ----------------------------- SELF ROLES & BIRTHDAYS -----------------------------

# Selbstrollen werden nicht mehr als separate Slash-Befehle angeboten.

class BirthdayRegisterModal(discord.ui.Modal, title="Geburtstag eintragen"):
    monat = discord.ui.TextInput(label="Geburtsmonat (1–12)", placeholder="z. B. 7", min_length=1, max_length=2)
    tag = discord.ui.TextInput(label="Geburtstag (1–31)", placeholder="z. B. 24", min_length=1, max_length=2)

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("Das funktioniert nur auf einem Server.", ephemeral=True)
            return
        import calendar
        try:
            month = int(str(self.monat.value).strip())
            day = int(str(self.tag.value).strip())
            if not 1 <= month <= 12 or not 1 <= day <= calendar.monthrange(2000, month)[1]:
                raise ValueError
        except (TypeError, ValueError):
            await interaction.response.send_message("Bitte gib einen gültigen Monat und Tag ein (z. B. Monat 7, Tag 24).", ephemeral=True)
            return
        cfg = guild_config(interaction.guild.id)
        cfg.setdefault("birthdays", {})[str(interaction.user.id)] = {"month": month, "day": day}
        cfg.setdefault("birthday_greeted_year", {}).pop(str(interaction.user.id), None)
        save_json(CONFIG_FILE, config)
        try:
            await refresh_birthday_calendar(interaction.guild, cfg)
        except Exception as exc:
            print(f"[BIRTHDAY] Calendar refresh failed for guild {interaction.guild.id}: {type(exc).__name__}: {exc}", flush=True)
        await interaction.response.send_message(
            f"🎂 Dein Geburtstag wurde als **{day:02d}.{month:02d}.** gespeichert. Die Discord-Geburtstagsliste wurde aktualisiert.",
            ephemeral=True,
        )


class BirthdayRegisterView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Geburtstag eintragen", style=discord.ButtonStyle.primary, emoji="🎂", custom_id="ironwolf:birthday:register")
    async def register_birthday(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild is None:
            await interaction.response.send_message("Bitte nutze diesen Button auf einem Server.", ephemeral=True)
            return
        await interaction.response.send_modal(BirthdayRegisterModal())


async def refresh_birthday_calendar(guild, cfg=None):
    """Edit the persistent Discord birthday calendar message in place."""
    cfg = cfg or guild_config(guild.id)
    channel_id = cfg.get("birthday_signup_channel")
    message_id = cfg.get("birthday_calendar_message_id")
    channel = guild.get_channel(int(channel_id)) if channel_id else None
    if not isinstance(channel, discord.TextChannel) or not message_id:
        return False

    month_names = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
                   "September", "Oktober", "November", "Dezember"]
    entries = [[] for _ in range(12)]
    for user_id, birthday in cfg.get("birthdays", {}).items():
        if not isinstance(birthday, dict):
            continue
        try:
            month, day = int(birthday.get("month", 0)), int(birthday.get("day", 0))
            if not 1 <= month <= 12 or not 1 <= day <= 31:
                continue
            member = guild.get_member(int(user_id))
            name = member.display_name if member else f"Mitglied ({user_id})"
            entries[month - 1].append((day, name, int(user_id)))
        except (TypeError, ValueError):
            continue

    lines = []
    for index, month_name in enumerate(month_names):
        people = sorted(entries[index], key=lambda item: (item[0], item[1].casefold()))
        lines.append(f"**{month_name}**")
        lines.extend(f"• {day:02d}. <@{user_id}>" for day, _name, user_id in people)
        if not people:
            lines.append("• _Noch keine Geburtstage_")
        lines.append("")

    description = "\n".join(lines).strip()
    if len(description) > 4000:
        description = description[:3970] + "\n… Weitere Einträge im Web-Dashboard."
    embed = discord.Embed(
        title="🎂 Geburtstagskalender",
        description=description,
        color=discord.Color.from_rgb(83, 160, 255),
    )
    embed.set_footer(text="Wird automatisch aktualisiert, wenn ein Geburtstag eingetragen wird.")
    try:
        message = await channel.fetch_message(int(message_id))
        await message.edit(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        return True
    except (discord.NotFound, discord.Forbidden, discord.HTTPException, ValueError) as exc:
        print(f"[BIRTHDAY] Could not update calendar message in guild {guild.id}: {type(exc).__name__}: {exc}", flush=True)
        return False


# Expose the view and refresh helper to the dashboard module.
bot.BirthdayRegisterView = BirthdayRegisterView
bot.refresh_birthday_calendar = refresh_birthday_calendar


@bot.tree.command(name="geburtstag-kanal", description="Lege den Kanal für automatische Geburtstagsgrüße fest")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
async def birthday_channel_set(interaction: discord.Interaction, kanal: discord.TextChannel | None = None):
    cfg = guild_config(interaction.guild_id)
    cfg["birthday_channel"] = kanal.id if kanal else None
    save_json(CONFIG_FILE, config)
    await interaction.response.send_message("Geburtstagsgrüße " + (f"aktiviert in {kanal.mention}." if kanal else "deaktiviert."), ephemeral=True)

@bot.tree.command(name="geburtstag", description="Speichere deinen Geburtstag für Server-Grüße")
@app_commands.guild_only()
@app_commands.describe(monat="Geburtsmonat (1–12)", tag="Tag (1–31)")
async def birthday_register(interaction: discord.Interaction, monat: app_commands.Range[int, 1, 12], tag: app_commands.Range[int, 1, 31]):
    import calendar
    if tag > calendar.monthrange(2000, monat)[1]:
        await interaction.response.send_message("Dieses Datum gibt es nicht. Bitte Monat und Tag prüfen.", ephemeral=True)
        return
    cfg = guild_config(interaction.guild_id)
    cfg.setdefault("birthdays", {})[str(interaction.user.id)] = {"month": int(monat), "day": int(tag)}
    save_json(CONFIG_FILE, config)
    await interaction.response.send_message(f"Dein Geburtstag wurde als {tag:02d}.{monat:02d} gespeichert. Es wird nur Tag und Monat gespeichert.", ephemeral=True)

@bot.tree.command(name="geburtstag-loeschen", description="Lösche deinen gespeicherten Geburtstag")
@app_commands.guild_only()
async def birthday_delete(interaction: discord.Interaction):
    cfg = guild_config(interaction.guild_id)
    cfg.setdefault("birthdays", {}).pop(str(interaction.user.id), None)
    save_json(CONFIG_FILE, config)
    try:
        await refresh_birthday_calendar(interaction.guild, cfg)
    except Exception as exc:
        print(f"[BIRTHDAY] Calendar refresh after delete failed: {type(exc).__name__}: {exc}", flush=True)
    await interaction.response.send_message("Dein gespeicherter Geburtstag wurde gelöscht.", ephemeral=True)

async def birthday_worker():
    """Send birthday greetings once per year and keep the optional birthday role for that day."""
    await bot.wait_until_ready()
    while not bot.is_closed():
        now = datetime.now(timezone.utc)
        year_key = str(now.year)
        for guild in list(bot.guilds):
            cfg = guild_config(guild.id)
            birthdays = cfg.setdefault("birthdays", {})
            role_id = cfg.get("birthday_role_id")
            birthday_role = guild.get_role(int(role_id)) if role_id else None
            if birthday_role:
                for member in list(guild.members):
                    if member.bot or birthday_role not in member.roles:
                        continue
                    date = birthdays.get(str(member.id), {})
                    is_today = isinstance(date, dict) and date.get("month") == now.month and date.get("day") == now.day
                    if not is_today:
                        try:
                            await member.remove_roles(birthday_role, reason="Geburtstagsrolle gilt nur am Geburtstag")
                        except (discord.Forbidden, discord.HTTPException) as exc:
                            print(f"[BIRTHDAY] Could not remove birthday role from {member.id} in guild {guild.id}: {type(exc).__name__}: {exc}")
            greeted = cfg.setdefault("birthday_greeted_year", {})
            changed = False
            for user_id, date in list(birthdays.items()):
                if not isinstance(date, dict) or date.get("month") != now.month or date.get("day") != now.day:
                    continue
                member = guild.get_member(int(user_id))
                if member is None or member.bot:
                    continue
                if birthday_role and birthday_role not in member.roles:
                    try:
                        await member.add_roles(birthday_role, reason="Geburtstagsrolle für den Geburtstag")
                    except (discord.Forbidden, discord.HTTPException) as exc:
                        print(f"[BIRTHDAY] Could not assign birthday role to {user_id} in guild {guild.id}: {type(exc).__name__}: {exc}")
                if greeted.get(user_id) == year_key:
                    continue
                channel_id = cfg.get("birthday_channel")
                channel = guild.get_channel(int(channel_id)) if channel_id else None
                if not isinstance(channel, discord.TextChannel):
                    continue
                try:
                    greeting = str(cfg.get("birthday_message") or "🎂 Alles Gute zum Geburtstag, {mention}! Wir wünschen dir einen tollen Tag! 🎉")
                    greeting = greeting.replace("{mention}", member.mention).replace("{user}", member.display_name).replace("{server}", guild.name)
                    await channel.send(greeting[:1900], allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False))
                    greeted[user_id] = year_key
                    changed = True
                except (discord.Forbidden, discord.HTTPException) as exc:
                    print(f"[BIRTHDAY] Could not greet member {user_id} in guild {guild.id}: {type(exc).__name__}: {exc}")
            if changed:
                save_json(CONFIG_FILE, config)
        await asyncio.sleep(3600)

# ----------------------------- BACKUP -----------------------------

@bot.tree.command(name="backup", description="Erstelle ein ZIP-Backup der Bot-Konfigurations- und Datendateien")
@app_commands.checks.has_permissions(administrator=True)
async def backup_command(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for filename in (CONFIG_FILE, DATA_FILE):
            path = Path(filename)
            if path.exists():
                archive.write(path, arcname=path.name)
    buffer.seek(0)
    try:
        await interaction.followup.send("Hier ist dein Backup von `config.json` und `data.json`.", file=discord.File(buffer, filename=f"bot-backup-{interaction.guild_id}.zip"), ephemeral=True)
    except discord.HTTPException:
        await interaction.followup.send("Backup konnte nicht gesendet werden. Prüfe Dateigröße und Bot-Verbindung.", ephemeral=True)


# ----------------------------- GIVEAWAY -----------------------------

async def _giveaway_participants(message: discord.Message):
    """Return unique non-bot participants who reacted with 🎉."""
    entrants = {}
    for reaction in message.reactions:
        if str(reaction.emoji) == "🎉":
            async for user in reaction.users():
                if not user.bot:
                    entrants[user.id] = user
    return list(entrants.values())


def _giveaway_embed(record, entrants=None):
    description = record.get("description", "")
    lines = []
    if description:
        lines.append(description[:1500])
        lines.append("")
    lines.extend([
        f"**Preis:** {record.get('prize', 'Giveaway')}",
        f"**Gewinner:** {record.get('winners', 1)}",
        f"**Endet:** <t:{int(record.get('ends_at', time.time()))}:R>",
    ])
    if record.get("show_participants", False):
        entrants = entrants or []
        names = [f"• {discord.utils.escape_markdown(getattr(u, 'display_name', str(u)))}" for u in entrants[:20]]
        if len(entrants) > 20:
            names.append(f"… und {len(entrants) - 20} weitere")
        participant_text = "\n".join(names) if names else "Noch keine Teilnehmer."
        lines.extend(["", f"**Teilnehmer ({len(entrants)}):**", participant_text])
    lines.extend(["", "Reagiere mit 🎉, um teilzunehmen!"])
    return create_embed(title="🎉 GIVEAWAY", description="\n".join(lines)[:4000], color=discord.Color.gold())


async def _refresh_giveaway_participants(payload):
    if str(payload.emoji) != "🎉" or payload.guild_id is None or payload.user_id == bot.user.id:
        return
    record = data.setdefault("giveaways", {}).get(str(payload.message_id))
    if not record or record.get("ended") or not record.get("show_participants", False):
        return
    channel = bot.get_channel(payload.channel_id)
    if not isinstance(channel, discord.TextChannel):
        return
    try:
        message = await channel.fetch_message(payload.message_id)
        entrants = await _giveaway_participants(message)
        await message.edit(embed=_giveaway_embed(record, entrants))
    except (discord.Forbidden, discord.NotFound, discord.HTTPException):
        pass


async def _refresh_poll(payload: discord.RawReactionActionEvent):
    if payload.guild_id is None or (bot.user and payload.user_id == bot.user.id):
        return
    record = data.setdefault("polls", {}).get(str(payload.message_id))
    if not record:
        return
    channel = bot.get_channel(payload.channel_id)
    if not isinstance(channel, discord.TextChannel):
        return
    try:
        message = await channel.fetch_message(payload.message_id)
        counts = []
        for emoji in record.get("emojis", []):
            reaction = discord.utils.get(message.reactions, emoji=emoji)
            counts.append(max(0, (reaction.count - 1) if reaction else 0))
        total = sum(counts)
        bars = []
        for index, option in enumerate(record.get("options", [])):
            count = counts[index] if index < len(counts) else 0
            percent = (count / total * 100) if total else 0
            filled = round(percent / 10)
            bar = "█" * filled + "░" * (10 - filled)
            bars.append(f"{record['emojis'][index]} **{option}**\n`{bar}` **{percent:.1f}%** · {count} vote(s)")
        embed = create_embed(
            title=f"📊 {record.get('question', 'Abstimmung')}",
            description="\n\n".join(bars) + f"\n\n**Stimmen insgesamt:** {total}",
            color=discord.Color.blurple(),
        )
        await message.edit(embed=embed)
    except (discord.Forbidden, discord.NotFound, discord.HTTPException, IndexError):
        pass


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    await _refresh_giveaway_participants(payload)
    await _refresh_poll(payload)


@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    await _refresh_giveaway_participants(payload)
    await _refresh_poll(payload)


async def _finish_giveaway(guild_id: int, channel_id: int, message_id: int, winners_count: int, prize: str):
    try:
        await asyncio.sleep(1)
        record = data.get("giveaways", {}).get(str(message_id))
        if not record:
            return
        wait_seconds = max(0, float(record.get("ends_at", time.time())) - time.time())
        await asyncio.sleep(wait_seconds)
        guild = bot.get_guild(guild_id)
        channel = guild.get_channel(channel_id) if guild else None
        if not isinstance(channel, discord.TextChannel):
            return
        try:
            message = await channel.fetch_message(message_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return
        entrants = await _giveaway_participants(message)
        winners = random.sample(entrants, min(winners_count, len(entrants))) if entrants else []
        record["ended"] = True
        record["winners"] = [u.id for u in winners]
        save_json(DATA_FILE, data)
        try:
            await message.edit(embed=_giveaway_embed(record, entrants))
        except discord.HTTPException:
            pass
        if winners:
            await channel.send(f"🎉 Giveaway beendet! Preis: **{prize}** — Gewinner: {', '.join(u.mention for u in winners)}")
        else:
            await channel.send(f"Giveaway für **{prize}** beendet. Es gab keine gültigen Teilnehmer.")
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        print(f"Giveaway task error: {type(exc).__name__}: {exc}")


@bot.tree.command(name="giveaway-start", description="Starte ein Giveaway mit Laufzeit in Tagen")
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(
    preis="Zu gewinnender Preis",
    tage="Laufzeit in Tagen (1–365)",
    beschreibung="Optionale Beschreibung des Giveaways",
    gewinner="Anzahl der Gewinner (1–10)",
    teilnehmer_anzeigen="Teilnehmerliste in der Giveaway-Nachricht anzeigen",
)
async def giveaway_start(
    interaction: discord.Interaction,
    preis: str,
    tage: app_commands.Range[int, 1, 365],
    beschreibung: str = "",
    gewinner: app_commands.Range[int, 1, 10] = 1,
    teilnehmer_anzeigen: bool = False,
):
    if interaction.guild_id is None or interaction.channel is None:
        await interaction.response.send_message("Dieser Befehl funktioniert nur auf einem Server.", ephemeral=True)
        return
    await interaction.response.send_message("Giveaway wird erstellt …", ephemeral=True)
    ends_at = time.time() + tage * 86400
    record = {
        "guild_id": interaction.guild_id,
        "channel_id": interaction.channel_id,
        "ends_at": ends_at,
        "winners": gewinner,
        "prize": preis[:200],
        "description": beschreibung[:1500],
        "show_participants": teilnehmer_anzeigen,
        "ended": False,
    }
    message = await interaction.channel.send(embed=_giveaway_embed(record, []))
    await message.add_reaction("🎉")
    data.setdefault("giveaways", {})[str(message.id)] = record
    save_json(DATA_FILE, data)
    asyncio.create_task(_finish_giveaway(interaction.guild_id, interaction.channel_id, message.id, gewinner, preis[:200]))
    await interaction.followup.send(f"Giveaway gestartet: {message.jump_url}", ephemeral=True)


# ----------------------------- SOCIAL LINKS -----------------------------

@bot.tree.command(name="social-set", description="Speichere einen Social-Media-Link für diesen Server")
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.choices(plattform=[app_commands.Choice(name="YouTube", value="youtube"), app_commands.Choice(name="Twitch", value="twitch"), app_commands.Choice(name="TikTok", value="tiktok"), app_commands.Choice(name="Instagram", value="instagram"), app_commands.Choice(name="Website", value="website")])
@app_commands.describe(plattform="Plattform", link="Vollständiger Link mit https://")
async def social_set(interaction: discord.Interaction, plattform: app_commands.Choice[str], link: str):
    if not (link.startswith("https://") or link.startswith("http://")) or len(link) > 500:
        await interaction.response.send_message("Bitte gib einen vollständigen Link mit `https://` ein.", ephemeral=True); return
    guild_config(interaction.guild_id).setdefault("social_links", {})[plattform.value] = link
    save_json(CONFIG_FILE, config)
    await interaction.response.send_message(f"{plattform.name}-Link gespeichert.", ephemeral=True)


@bot.tree.command(name="social", description="Zeige die gespeicherten Social-Media-Links des Servers")
async def social_show(interaction: discord.Interaction):
    links = guild_config(interaction.guild_id).get("social_links", {})
    if not links:
        await interaction.response.send_message("Noch keine Social-Links eingerichtet. Ein Admin kann `/social-set` verwenden.", ephemeral=True); return
    embed = create_embed(title=f"🌐 Social Links — {interaction.guild.name}", color=discord.Color.blurple())
    for platform, link in sorted(links.items()):
        embed.add_field(name=platform.capitalize(), value=f"[Hier öffnen]({link})", inline=True)
    await interaction.response.send_message(embed=embed)


# ----------------------------- EXPANDED COMMUNITY FEATURES -----------------------------

_automod_messages = defaultdict(deque)
_raid_join_times = defaultdict(deque)
_social_alert_task_started = False


def _new_mod_case(guild_id: int, user_id: int, moderator_id: int, action: str, reason: str):
    cases = data.setdefault("mod_cases", {}).setdefault(str(guild_id), [])
    case_id = (cases[-1]["id"] + 1) if cases else 1
    cases.append({"id": case_id, "user_id": user_id, "moderator_id": moderator_id,
                  "action": action, "reason": reason[:800], "timestamp": datetime.now(timezone.utc).isoformat()})
    save_json(DATA_FILE, data)
    return case_id


@bot.tree.command(name="modlogs", description="Zeige Moderationsfälle eines Mitglieds")
@app_commands.checks.has_permissions(moderate_members=True)
async def modlogs_command(interaction: discord.Interaction, mitglied: discord.Member):
    cases = [c for c in data.setdefault("mod_cases", {}).get(str(interaction.guild_id), []) if int(c.get("user_id", 0)) == mitglied.id]
    warnings = _moderation_log(interaction.guild_id).get(str(mitglied.id), [])
    lines = [f"`#{c['id']}` **{c['action']}** — {c['reason'][:180]} · {c['timestamp'][:10]}" for c in cases[-12:]]
    lines.extend([f"**Warnung** — {w.get('reason','Kein Grund')[:180]} · {w.get('timestamp','')[:10]}" for w in warnings[-5:]])
    if not lines:
        await interaction.response.send_message("Keine gespeicherten Moderationsfälle gefunden.", ephemeral=True)
        return
    await interaction.response.send_message(embed=create_embed(title=f"Moderationsakte: {mitglied}", description="\n".join(lines)[:4000], color=discord.Color.orange()), ephemeral=True)


@bot.tree.command(name="case", description="Zeige einen Moderationsfall anhand seiner Nummer")
@app_commands.checks.has_permissions(moderate_members=True)
async def case_command(interaction: discord.Interaction, nummer: app_commands.Range[int, 1, 1000000]):
    cases = data.setdefault("mod_cases", {}).get(str(interaction.guild_id), [])
    case = next((c for c in cases if int(c.get("id", 0)) == nummer), None)
    if not case:
        await interaction.response.send_message("Dieser Fall wurde nicht gefunden. Fälle werden ab jetzt gespeichert.", ephemeral=True)
        return
    embed = create_embed(title=f"Moderationsfall #{case['id']}", description=f"**Aktion:** {case['action']}\n**Mitglied:** <@{case['user_id']}>\n**Moderator:** <@{case['moderator_id']}>\n**Grund:** {case['reason']}\n**Zeit:** {case['timestamp']}", color=discord.Color.orange())
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="unban", description="Entbanne ein Mitglied anhand seiner Discord-ID")
@app_commands.checks.has_permissions(ban_members=True)
async def unban_command(interaction: discord.Interaction, user_id: str, grund: str = "Kein Grund angegeben"):
    if not user_id.isdigit():
        await interaction.response.send_message("Bitte eine numerische Discord-ID eingeben.", ephemeral=True)
        return
    try:
        user = await bot.fetch_user(int(user_id))
        await interaction.guild.unban(user, reason=f"{interaction.user}: {grund[:400]}")
    except discord.NotFound:
        await interaction.response.send_message("Dieses Mitglied ist nicht gebannt.", ephemeral=True)
        return
    except (discord.Forbidden, discord.HTTPException) as exc:
        await interaction.response.send_message(f"Entbannen fehlgeschlagen: {type(exc).__name__}.", ephemeral=True)
        return
    case_id = _new_mod_case(interaction.guild_id, user.id, interaction.user.id, "UNBAN", grund)
    await interaction.response.send_message(f"🔓 {user} wurde entbannt. Fall `#{case_id}`.", ephemeral=True)


@bot.tree.command(name="lockdown", description="Sperre den aktuellen Textkanal für normale Mitglieder")
@app_commands.checks.has_permissions(manage_channels=True)
async def lockdown_command(interaction: discord.Interaction):
    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel):
        await interaction.response.send_message("Nur in normalen Textkanälen möglich.", ephemeral=True)
        return
    overwrite = channel.overwrites_for(interaction.guild.default_role)
    cfg = guild_config(interaction.guild_id)
    saved = cfg.setdefault("lockdown_overwrites", {})
    saved[str(channel.id)] = overwrite.send_messages
    overwrite.send_messages = False
    try:
        await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite, reason=f"Lockdown von {interaction.user}")
        save_json(CONFIG_FILE, config)
    except (discord.Forbidden, discord.HTTPException) as exc:
        await interaction.response.send_message(f"Lockdown fehlgeschlagen: {type(exc).__name__}.", ephemeral=True)
        return
    await interaction.response.send_message(f"🔒 {channel.mention} wurde gesperrt. Mit `/unlockdown` kannst du es wieder öffnen.")


@bot.tree.command(name="unlockdown", description="Hebt den Lockdown im aktuellen Textkanal auf")
@app_commands.checks.has_permissions(manage_channels=True)
async def unlockdown_command(interaction: discord.Interaction):
    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel):
        await interaction.response.send_message("Nur in normalen Textkanälen möglich.", ephemeral=True)
        return
    cfg = guild_config(interaction.guild_id)
    saved = cfg.setdefault("lockdown_overwrites", {})
    if str(channel.id) not in saved:
        await interaction.response.send_message("Für diesen Kanal ist kein gespeicherter Lockdown vorhanden.", ephemeral=True)
        return
    previous = saved.pop(str(channel.id))
    overwrite = channel.overwrites_for(interaction.guild.default_role)
    overwrite.send_messages = previous
    try:
        await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite, reason=f"Lockdown aufgehoben von {interaction.user}")
        save_json(CONFIG_FILE, config)
    except (discord.Forbidden, discord.HTTPException) as exc:
        await interaction.response.send_message(f"Freigabe fehlgeschlagen: {type(exc).__name__}.", ephemeral=True)
        return
    await interaction.response.send_message(f"🔓 {channel.mention} wurde wieder freigegeben.")


@bot.tree.command(name="automod-set", description="Schalte einfachen Anti-Spam-Schutz ein oder aus")
@app_commands.checks.has_permissions(manage_guild=True)
async def automod_set(interaction: discord.Interaction, aktiviert: bool):
    guild_config(interaction.guild_id)["automod_enabled"] = aktiviert
    save_json(CONFIG_FILE, config)
    await interaction.response.send_message(f"AutoMod ist jetzt **{'aktiviert' if aktiviert else 'deaktiviert'}**. Aktiv: 6 Nachrichten innerhalb von 8 Sekunden lösen Löschen und einen 2-Minuten-Timeout aus.", ephemeral=True)


@bot.tree.command(name="security-status", description="Zeige den Sicherheitsstatus des Servers")
@app_commands.checks.has_permissions(manage_guild=True)
async def security_status(interaction: discord.Interaction):
    cfg = guild_config(interaction.guild_id)
    embed = create_embed(title="🛡️ Security Center", color=discord.Color.green())
    embed.add_field(name="Anti-Raid", value="Aktiv" if cfg.get("anti_raid_enabled", False) else "Aus", inline=True)
    embed.add_field(name="Anti-Nuke", value="Aktiv" if cfg.get("anti_nuke_enabled", False) else "Aus", inline=True)
    embed.add_field(name="AutoMod", value="Aktiv" if cfg.get("automod_enabled", False) else "Aus", inline=True)
    embed.add_field(name="Security-Log", value=f"<#{cfg['security_log_channel']}>" if cfg.get("security_log_channel") else "Nicht gesetzt", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="daily", description="Hole dir einmal alle 24 Stunden XP")
async def daily_command(interaction: discord.Interaction):
    if not interaction.guild_id:
        await interaction.response.send_message("Nur auf einem Server verfügbar.", ephemeral=True)
        return
    key = f"{interaction.guild_id}:{interaction.user.id}"
    now = time.time()
    last = float(data.setdefault("daily_claims", {}).get(key, 0))
    if now - last < 86400:
        hours = int((86400 - (now - last)) // 3600)
        minutes = int(((86400 - (now - last)) % 3600) // 60)
        await interaction.response.send_message(f"Deine tägliche Belohnung ist bereits abgeholt. Komm in **{hours} Std. {minutes} Min.** wieder.", ephemeral=True)
        return
    stats = get_level_xp(interaction.guild_id, interaction.user.id)
    reward = random.randint(40, 80)
    stats["xp"] = int(stats.get("xp", 0)) + reward
    stats["level"] = calculate_level(stats["xp"])
    data["daily_claims"][key] = now
    save_json(DATA_FILE, data)
    await interaction.response.send_message(f"🎁 Du hast **{reward} XP** erhalten! Gesamt: **{stats['xp']} XP**.")


@bot.tree.command(name="rank", description="Zeige XP, Level und Fortschritt eines Mitglieds")
async def rank_command(interaction: discord.Interaction, mitglied: discord.Member = None):
    target = mitglied or interaction.user
    stats = get_level_xp(interaction.guild_id, target.id)
    xp = int(stats.get("xp", 0)); level = calculate_level(xp)
    start = total_xp_for_level(level); next_level = total_xp_for_level(level + 1)
    required = max(1, next_level - start); progress = max(0, xp - start)
    embed = create_embed(title=f"🏆 Rangkarte: {target.display_name}", color=discord.Color.gold())
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(name="Level", value=str(level), inline=True)
    embed.add_field(name="XP", value=f"{xp:,}".replace(",", "."), inline=True)
    embed.add_field(name="Fortschritt", value=f"{min(100, int(progress / required * 100))}% · {progress}/{required} XP", inline=False)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="level-role-set", description="Vergib eine Rolle automatisch ab einem Level")
@app_commands.checks.has_permissions(manage_guild=True)
async def level_role_set(interaction: discord.Interaction, level: app_commands.Range[int, 1, 1000], rolle: discord.Role):
    if rolle.is_default() or rolle >= interaction.guild.me.top_role:
        await interaction.response.send_message("Die Bot-Rolle muss über der Zielrolle stehen.", ephemeral=True)
        return
    guild_config(interaction.guild_id).setdefault("level_roles", {})[str(level)] = rolle.id
    save_json(CONFIG_FILE, config)
    await interaction.response.send_message(f"Ab Level **{level}** wird {rolle.mention} vergeben, sobald das Mitglied dieses Level erreicht.", ephemeral=True)


@bot.tree.command(name="tempvoice-setup", description="Richte den Beitrittskanal für temporäre Voice-Räume ein")
@app_commands.checks.has_permissions(manage_channels=True)
async def tempvoice_setup(interaction: discord.Interaction, trigger: discord.VoiceChannel, kategorie: discord.CategoryChannel = None):
    cfg = guild_config(interaction.guild_id)
    cfg["tempvoice_trigger"] = trigger.id
    cfg["tempvoice_category"] = kategorie.id if kategorie else (trigger.category_id or None)
    save_json(CONFIG_FILE, config)
    await interaction.response.send_message(f"Temp-Voice aktiviert. Wenn jemand {trigger.mention} betritt, wird ein eigener Raum erstellt.", ephemeral=True)


@bot.tree.command(name="nachricht", description="Sendet eine Nachricht als Bot in einen Discord-Kanal")
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(
    kanal="Kanal, in den die Bot-Nachricht gesendet werden soll",
    text="Der Nachrichtentext (maximal 2000 Zeichen)",
)
async def nachricht(interaction: discord.Interaction, kanal: discord.TextChannel, text: str):
    if not interaction.guild or kanal.guild.id != interaction.guild.id:
        await interaction.response.send_message("Bitte wähle einen Kanal von diesem Server.", ephemeral=True)
        return
    if not text.strip():
        await interaction.response.send_message("Der Nachrichtentext darf nicht leer sein.", ephemeral=True)
        return
    try:
        await kanal.send(
            text[:2000],
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except discord.Forbidden:
        await interaction.response.send_message(
            "Ich darf in diesem Kanal keine Nachrichten senden. Prüfe meine Kanalrechte.",
            ephemeral=True,
        )
        return
    except discord.HTTPException:
        await interaction.response.send_message(
            "Die Nachricht konnte nicht gesendet werden. Bitte prüfe den Text und versuche es erneut.",
            ephemeral=True,
        )
        return
    await interaction.response.send_message(f"✅ Nachricht wurde als Bot in {kanal.mention} gesendet.", ephemeral=True)


@bot.tree.command(name="verify", description="Richtet das Verifizierungs-Panel vollständig ein")
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(
    kanal="Kanal, in dem das Verifizierungs-Panel erscheinen soll",
    rolle_geben="Rolle, die nach erfolgreicher Verifizierung vergeben wird",
    rolle_entfernen="Rolle, die nach erfolgreicher Verifizierung entfernt wird (optional)",
    modus="Ein Klick oder Bild-Captcha mit drei Auswahlmöglichkeiten",
    titel="Titel des Verifizierungs-Embeds",
    beschreibung="Beschreibung des Verifizierungs-Embeds",
    button_name="Text auf dem Verifizierungsbutton",
    emoji="Emoji auf dem Verifizierungsbutton",
)
@app_commands.choices(modus=[
    app_commands.Choice(name="Nur Knopf drücken", value="click"),
    app_commands.Choice(name="Bild-Captcha (3 Antworten)", value="code"),
])
async def verify_command(
    interaction: discord.Interaction,
    kanal: discord.TextChannel,
    rolle_geben: discord.Role,
    rolle_entfernen: discord.Role = None,
    modus: app_commands.Choice[str] = None,
    titel: str = "🔐 Server-Verifizierung",
    beschreibung: str = "Lies die Serverregeln und klicke auf den Button, um dich zu verifizieren.",
    button_name: str = "Verifizieren",
    emoji: str = "✅",
):
    if not interaction.guild:
        await interaction.response.send_message("Dieser Befehl funktioniert nur auf einem Server.", ephemeral=True)
        return
    bot_member = interaction.guild.me
    if (
        not bot_member
        or rolle_geben.is_default()
        or rolle_geben >= bot_member.top_role
        or (rolle_entfernen and (rolle_entfernen.is_default() or rolle_entfernen >= bot_member.top_role))
    ):
        await interaction.response.send_message(
            "Die Bot-Rolle muss über beiden ausgewählten Rollen stehen. @everyone kann nicht verwendet werden.",
            ephemeral=True,
        )
        return
    if rolle_entfernen and rolle_entfernen.id == rolle_geben.id:
        await interaction.response.send_message(
            "Die Rolle zum Vergeben und die Rolle zum Entfernen müssen unterschiedlich sein.",
            ephemeral=True,
        )
        return
    mode_value = modus.value if modus else "click"
    panel_title = titel.strip()[:256] or "🔐 Server-Verifizierung"
    panel_description = beschreibung.strip()[:4000] or "Klicke auf den Button, um dich zu verifizieren."
    panel_button = button_name.strip()[:80] or "Verifizieren"
    panel_emoji = emoji.strip()[:100] if emoji else "✅"

    cfg = guild_config(interaction.guild_id)
    cfg["verify_channel"] = kanal.id
    cfg["verified_role"] = rolle_geben.id
    cfg["unverified_role"] = rolle_entfernen.id if rolle_entfernen else None
    cfg["verify_mode"] = mode_value
    cfg["verify_button_label"] = panel_button
    cfg["verify_button_emoji"] = panel_emoji
    cfg["verify_embed_title"] = panel_title
    cfg["verify_embed_description"] = panel_description
    save_json(CONFIG_FILE, config)
    embed = create_embed(
        title=panel_title,
        description=panel_description,
        color=discord.Color.green(),
    )
    try:
        verification_message = await kanal.send(embed=embed, view=VerifyView(panel_button, panel_emoji))
        cfg["verify_message_id"] = verification_message.id
        save_json(CONFIG_FILE, config)
    except discord.Forbidden:
        await interaction.response.send_message(
            "Ich kann in diesem Kanal keine Nachricht senden. Prüfe meine Kanalrechte.",
            ephemeral=True,
        )
        return
    except (discord.HTTPException, ValueError):
        await interaction.response.send_message(
            "Das Verifizierungs-Panel konnte nicht gesendet werden. Prüfe bitte das Emoji und die Texte.",
            ephemeral=True,
        )
        return
    await interaction.response.send_message(
        f"✅ Verifizierungs-Panel wurde in {kanal.mention} erstellt. "
        f"Rolle vergeben: {rolle_geben.mention}; Rolle entfernen: {rolle_entfernen.mention if rolle_entfernen else 'keine'}.",
        ephemeral=True,
    )


@bot.tree.command(name="verify-setup", description="Richtet die Verifizierung, Methode und Button ein")
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(
    kanal="Kanal, in dem das Verifizierungs-Panel erscheint",
    verifiziert="Rolle für erfolgreich verifizierte Mitglieder",
    unverified="Optionale Rolle für noch nicht verifizierte Mitglieder",
    modus="Ein Klick oder Bild-Captcha mit drei Auswahlmöglichkeiten",
    button_text="Beschriftung des Verifizierungsbuttons",
    emoji="Emoji für den Verifizierungsbutton",
)
@app_commands.choices(modus=[
    app_commands.Choice(name="Ein Klick", value="click"),
    app_commands.Choice(name="Code eingeben", value="code"),
])
async def verify_setup(
    interaction: discord.Interaction,
    kanal: discord.TextChannel,
    verifiziert: discord.Role,
    unverified: discord.Role = None,
    modus: app_commands.Choice[str] = None,
    button_text: str = "Regeln akzeptieren / Verifizieren",
    emoji: str = "✅",
):
    bot_member = interaction.guild.me if interaction.guild else None
    if not bot_member or verifiziert.is_default() or verifiziert >= bot_member.top_role or (unverified and unverified >= bot_member.top_role):
        await interaction.response.send_message("Die Bot-Rolle muss über den Verifizierungsrollen stehen.", ephemeral=True)
        return
    mode_value = modus.value if modus else "click"
    cfg = guild_config(interaction.guild_id)
    cfg["verify_channel"] = kanal.id
    cfg["verified_role"] = verifiziert.id
    cfg["unverified_role"] = unverified.id if unverified else None
    cfg["verify_mode"] = mode_value
    cfg["verify_button_label"] = button_text
    cfg["verify_button_emoji"] = emoji
    cfg["verify_embed_title"] = "✅ Server-Verifizierung"
    cfg["verify_embed_description"] = (
        "Lies die Serverregeln und klicke auf den Button. "
        + ("Anschließend löst du ein Bild-Captcha, indem du den passenden Code aus drei Antworten auswählst."
           if mode_value == "code" else "Du wirst direkt verifiziert.")
    )
    save_json(CONFIG_FILE, config)
    embed = create_embed(
        title="✅ Server-Verifizierung",
        description=(
            "Lies die Serverregeln und klicke auf den Button. "
            + ("Anschließend löst du ein Bild-Captcha, indem du den passenden Code aus drei Antworten auswählst."
               if mode_value == "code"
               else "Du wirst direkt verifiziert.")
        ),
        color=discord.Color.green(),
    )
    verification_message = await kanal.send(embed=embed, view=VerifyView(button_text, emoji))
    cfg["verify_message_id"] = verification_message.id
    save_json(CONFIG_FILE, config)
    await interaction.response.send_message(f"Verifizierungs-Panel in {kanal.mention} erstellt.", ephemeral=True)


@bot.tree.command(name="poll", description="Erstelle eine Abstimmung mit zwei bis vier Optionen")
@app_commands.checks.has_permissions(manage_messages=True)
@app_commands.describe(frage="Frage der Abstimmung", option1="Erste Option", option2="Zweite Option", option3="Option 3 (optional)", option4="Option 4 (optional)")
async def poll_command(interaction: discord.Interaction, frage: str, option1: str, option2: str, option3: str = None, option4: str = None):
    options = [o for o in (option1, option2, option3, option4) if o]
    if len(options) < 2 or len(options) > 4:
        await interaction.response.send_message("Bitte zwei bis vier Optionen angeben.", ephemeral=True)
        return
    emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"][:len(options)]
    chart_lines = [f"{emojis[i]} **{o[:250]}**\n`{'░' * 10}` **0.0%** · 0 Stimmen" for i, o in enumerate(options)]
    embed = create_embed(
        title=f"📊 {frage[:250]}",
        description="\n\n".join(chart_lines) + "\n\n**Stimmen insgesamt:** 0",
        color=discord.Color.blurple(),
    )
    await interaction.response.send_message("Abstimmung erstellt.", ephemeral=True)
    msg = await interaction.channel.send(embed=embed)
    data.setdefault("polls", {})[str(msg.id)] = {
        "question": frage[:250],
        "options": [o[:250] for o in options],
        "emojis": emojis,
        "guild_id": interaction.guild_id,
        "channel_id": interaction.channel_id,
    }
    save_json(DATA_FILE, data)
    for emoji in emojis:
        try:
            await msg.add_reaction(emoji)
        except discord.HTTPException:
            pass


@bot.tree.command(name="suggest", description="Reiche einen Community-Vorschlag ein")
@app_commands.describe(vorschlag="Dein Vorschlag für den Server")
async def suggest_command(interaction: discord.Interaction, vorschlag: str):
    if not interaction.guild_id:
        await interaction.response.send_message("Nur auf einem Server verfügbar.", ephemeral=True); return
    cfg = guild_config(interaction.guild_id)
    channel_id = cfg.get("suggestion_channel")
    channel = interaction.guild.get_channel(int(channel_id)) if channel_id else None
    data.setdefault("suggestions", {}).setdefault(str(interaction.guild_id), []).append({"user_id": interaction.user.id, "text": vorschlag[:1500], "timestamp": datetime.now(timezone.utc).isoformat()})
    save_json(DATA_FILE, data)
    if isinstance(channel, discord.TextChannel):
        embed = create_embed(title="💡 Neuer Vorschlag", description=vorschlag[:3500], color=discord.Color.blurple())
        embed.set_author(name=str(interaction.user), icon_url=interaction.user.display_avatar.url)
        msg = await channel.send(embed=embed)
        await msg.add_reaction("👍"); await msg.add_reaction("👎")
        await interaction.response.send_message(f"Vorschlag eingereicht: {msg.jump_url}", ephemeral=True)
    else:
        await interaction.response.send_message("Vorschlag gespeichert. Ein Admin kann mit `/suggestion-channel-set` einen öffentlichen Kanal festlegen.", ephemeral=True)


@bot.tree.command(name="suggestion-channel-set", description="Lege den Kanal für Community-Vorschläge fest")
@app_commands.checks.has_permissions(manage_guild=True)
async def suggestion_channel_set(interaction: discord.Interaction, kanal: discord.TextChannel):
    guild_config(interaction.guild_id)["suggestion_channel"] = kanal.id
    save_json(CONFIG_FILE, config)
    await interaction.response.send_message(f"Vorschläge werden in {kanal.mention} veröffentlicht.", ephemeral=True)


@bot.tree.command(name="event-create", description="Veröffentliche eine Community-Veranstaltung")
@app_commands.checks.has_permissions(manage_events=True)
async def event_create(interaction: discord.Interaction, titel: str, datum: str, beschreibung: str):
    embed = create_embed(title=f"📅 {titel[:250]}", description=f"**Wann:** {datum[:200]}\n\n{beschreibung[:3000]}\n\nReagiere mit 📅, wenn du teilnehmen möchtest.", color=discord.Color.blurple())
    await interaction.response.send_message("Event veröffentlicht.", ephemeral=True)
    msg = await interaction.channel.send(embed=embed)
    await msg.add_reaction("📅")
    data.setdefault("events", {}).setdefault(str(interaction.guild_id), []).append({"message_id": msg.id, "title": titel[:250], "date": datum[:200], "created_by": interaction.user.id})
    save_json(DATA_FILE, data)


@bot.tree.command(name="ticket-stats", description="Zeige Support-Ticket-Statistiken")
@app_commands.checks.has_permissions(manage_guild=True)
async def ticket_stats(interaction: discord.Interaction):
    tickets = data.setdefault("tickets", {})
    guild_tickets = []
    for channel_id, meta in tickets.items():
        channel = interaction.guild.get_channel(int(channel_id))
        if channel is not None:
            guild_tickets.append(meta)
    open_count = len(guild_tickets)
    claimed = sum(1 for t in guild_tickets if t.get("claimed_by"))
    embed = create_embed(title="🎫 Support-Statistik", color=discord.Color.blurple())
    embed.add_field(name="Offene/gespeicherte Tickets", value=str(open_count), inline=True)
    embed.add_field(name="Übernommen", value=str(claimed), inline=True)
    embed.add_field(name="Ticket-System", value="Aktiv", inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


def _normalize_youtube_source(source):
    """Accept YouTube channel/RSS URLs and normalize them to a safe HTTPS feed."""
    raw_source = str(source).strip()
    # Preserve the legacy command's support for URLs entered without a scheme.
    if raw_source.startswith(("youtube.com/", "www.youtube.com/")):
        raw_source = "https://" + raw_source
    try:
        parsed = urlparse(raw_source)
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or parsed.hostname not in {"youtube.com", "www.youtube.com"}:
        return None
    if parsed.username or parsed.password:
        return None

    if parsed.path == "/feeds/videos.xml":
        channel_id = parse_qs(parsed.query).get("channel_id", [""])[0]
    else:
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) != 2 or parts[0] != "channel":
            return None
        channel_id = parts[1]

    if not re.fullmatch(r"UC[A-Za-z0-9_-]{10,}", channel_id):
        return None
    return f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"


def _normalize_social_alerts(cfg):
    """Normalize legacy platform-keyed alerts to one entry per platform/source."""
    alerts = cfg.setdefault("social_alerts", {})
    normalized = {}
    for old_key, alert in list(alerts.items()):
        if not isinstance(alert, dict):
            continue
        platform = str(alert.get("platform") or (old_key if old_key in ("youtube", "twitch") else str(old_key).split(":", 1)[0])).lower()
        source = str(alert.get("source", "")).strip()
        if platform not in ("youtube", "twitch") or not source:
            continue
        key = f"{platform}:{source.casefold()}"
        alert["platform"] = platform
        alert.setdefault("enabled", True)
        alert.setdefault("role_id", None)
        alert.setdefault("last_check_at", None)
        alert.setdefault("last_success_at", None)
        alert.setdefault("last_error", None)
        alert.setdefault("is_live", False)
        alert.setdefault("live_title", None)
        alert.setdefault("live_game", None)
        alert.setdefault("live_viewers", None)
        alert.setdefault("live_since", None)
        normalized[key] = alert
    if normalized != alerts:
        cfg["social_alerts"] = normalized
    return cfg["social_alerts"]


def _social_alert_role(guild, alert):
    role_id = alert.get("role_id")
    try:
        return guild.get_role(int(role_id)) if role_id else None
    except (TypeError, ValueError):
        return None


def _social_alert_mentions(role):
    if role is None:
        return discord.AllowedMentions.none()
    return discord.AllowedMentions(roles=[role], users=False, everyone=False, replied_user=False)


@bot.tree.command(name="social-alert-set", description="Fügt einen YouTube- oder Twitch-Creator hinzu")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.choices(plattform=[app_commands.Choice(name="YouTube RSS", value="youtube"), app_commands.Choice(name="Twitch", value="twitch")])
@app_commands.describe(plattform="Plattform", kanal="Ankündigungskanal", quelle="YouTube-RSS-/Channel-URL oder Twitch-Benutzername", rolle="Optional: Rolle, die bei neuen Inhalten erwähnt wird")
async def social_alert_set(interaction: discord.Interaction, plattform: app_commands.Choice[str], kanal: discord.TextChannel, quelle: str, rolle: discord.Role = None):
    source = quelle.strip()
    if plattform.value == "youtube":
        normalized_source = _normalize_youtube_source(source)
        if normalized_source is None:
            await interaction.response.send_message(
                "Ungültige YouTube-Quelle. Verwende eine HTTPS-URL von youtube.com mit /channel/CHANNEL_ID oder /feeds/videos.xml?channel_id=CHANNEL_ID.",
                ephemeral=True,
            )
            return
        source = normalized_source
    elif not re.fullmatch(r"[A-Za-z0-9_]{1,25}", source):
        await interaction.response.send_message("Für Twitch bitte nur den Kanalnamen angeben.", ephemeral=True)
        return

    cfg = guild_config(interaction.guild_id)
    alerts = _normalize_social_alerts(cfg)
    key = f"{plattform.value}:{source.casefold()}"
    previous = alerts.get(key, {})
    alerts[key] = {
        **previous,
        "platform": plattform.value,
        "channel_id": kanal.id,
        "source": source,
        "role_id": rolle.id if rolle else None,
        "enabled": True,
    }
    save_json(CONFIG_FILE, config)
    role_text = f" · Ping-Rolle: {rolle.mention}" if rolle else " · kein Rollen-Ping"
    await interaction.response.send_message(
        f"✅ {plattform.name}-Alert für **{source}** gespeichert in {kanal.mention}{role_text}. Mehrere Creator pro Plattform sind möglich.",
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


@bot.tree.command(name="social-alert-aus", description="Deaktiviert alle Alerts einer Plattform")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.choices(plattform=[app_commands.Choice(name="YouTube", value="youtube"), app_commands.Choice(name="Twitch", value="twitch")])
async def social_alert_disable(interaction: discord.Interaction, plattform: app_commands.Choice[str]):
    cfg = guild_config(interaction.guild_id)
    alerts = _normalize_social_alerts(cfg)
    matching = [alert for alert in alerts.values() if alert.get("platform") == plattform.value and alert.get("enabled")]
    if not matching:
        await interaction.response.send_message(f"Keine aktiven {plattform.name}-Alerts gefunden.", ephemeral=True)
        return
    for alert in matching:
        alert["enabled"] = False
    save_json(CONFIG_FILE, config)
    await interaction.response.send_message(f"⏸️ {len(matching)} {plattform.name}-Alert(s) deaktiviert. Die Einstellungen bleiben gespeichert.", ephemeral=True)


@bot.tree.command(name="social-alert-test", description="Sendet eine Testnachricht für einen Social Alert")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.choices(plattform=[app_commands.Choice(name="YouTube", value="youtube"), app_commands.Choice(name="Twitch", value="twitch")])
@app_commands.describe(quelle="Optional: Creator-Quelle, wenn mehrere Alerts derselben Plattform existieren")
async def social_alert_test(interaction: discord.Interaction, plattform: app_commands.Choice[str], quelle: Optional[str] = None):
    alerts = _normalize_social_alerts(guild_config(interaction.guild_id))
    matches = [(key, alert) for key, alert in alerts.items() if alert.get("platform") == plattform.value and (not quelle or str(alert.get("source", "")).casefold() == quelle.strip().casefold())]
    if not matches:
        await interaction.response.send_message("Für diese Plattform/Quelle ist kein Alert eingerichtet.", ephemeral=True)
        return
    _, alert = matches[0]
    channel = interaction.guild.get_channel(int(alert.get("channel_id", 0)))
    if not isinstance(channel, discord.TextChannel):
        await interaction.response.send_message("Der konfigurierte Kanal wurde nicht gefunden.", ephemeral=True)
        return
    role = _social_alert_role(interaction.guild, alert)
    embed = create_embed(
        title=f"🧪 Social-Alert-Test · {plattform.name}",
        description=f"**Quelle:** {str(alert.get('source', 'unbekannt'))[:180]}\n**Status:** {'aktiv' if alert.get('enabled') else 'pausiert'}\n**Ping-Rolle:** {role.mention if role else 'Keine'}",
        color=discord.Color.blurple(),
        timestamp=datetime.now(timezone.utc),
    )
    try:
        await channel.send(content=role.mention if role else None, embed=embed, allowed_mentions=_social_alert_mentions(role))
    except (discord.Forbidden, discord.HTTPException) as exc:
        await interaction.response.send_message(f"Test konnte nicht gesendet werden ({type(exc).__name__}). Prüfe Kanal- und Rollenrechte.", ephemeral=True)
        return
    await interaction.response.send_message(f"Testnachricht für **{alert.get('source')}** in {channel.mention} gesendet. Die externe API wird dabei nicht geprüft.", ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


@bot.tree.command(name="social-alert-status", description="Zeigt Creator, Kanal und Alert-Status")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
async def social_alert_status(interaction: discord.Interaction):
    alerts = _normalize_social_alerts(guild_config(interaction.guild_id))
    embed = create_embed(title="📡 Social-Alerts · Übersicht", color=discord.Color.blurple())
    if not alerts:
        embed.description = "Noch keine Alerts eingerichtet. Nutze /social-alert-set für jeden Creator."
    else:
        for key, alert in list(sorted(alerts.items()))[:25]:
            channel = interaction.guild.get_channel(int(alert.get("channel_id", 0)))
            role = _social_alert_role(interaction.guild, alert)
            state = "🟢 Aktiv" if alert.get("enabled") else "⏸️ Pausiert"
            last_check = alert.get("last_check_at") or "Noch nie"
            last_error = str(alert.get("last_error") or "Keiner")[:160]
            embed.add_field(
                name=f"{str(alert.get('platform', key)).capitalize()} · {str(alert.get('source', 'Quelle'))[:80]} · {state}",
                value=f"**Kanal:** {channel.mention if channel else 'Nicht gefunden'}\n**Ping:** {role.mention if role else 'Aus'}\n**Letzter Check:** {last_check}\n**Fehler:** {last_error}",
                inline=False,
            )
        if len(alerts) > 25:
            embed.set_footer(text=f"Zeige 25 von {len(alerts)} Alerts. Diagnose enthält weitere Details.")
    await interaction.response.send_message(embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


@bot.tree.command(name="social-alert-diagnose", description="Prüft Alert-Checks, Fehler und Twitch-Zugangsdaten")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
async def social_alert_diagnose(interaction: discord.Interaction):
    alerts = _normalize_social_alerts(guild_config(interaction.guild_id))
    client_ok = bool(os.getenv("TWITCH_CLIENT_ID"))
    token_ok = bool(os.getenv("TWITCH_ACCESS_TOKEN"))
    embed = create_embed(title="🩺 Social-Alert-Diagnose", color=discord.Color.blurple())
    embed.add_field(name="Twitch Client ID", value="✅ Vorhanden" if client_ok else "❌ TWITCH_CLIENT_ID fehlt", inline=True)
    embed.add_field(name="Twitch Access Token", value="✅ Vorhanden" if token_ok else "❌ TWITCH_ACCESS_TOKEN fehlt", inline=True)
    if not alerts:
        embed.description = "Keine Creator konfiguriert."
    for key, alert in list(sorted(alerts.items()))[:20]:
        last_check = alert.get("last_check_at") or "Noch kein Check"
        last_success = alert.get("last_success_at") or "Noch kein erfolgreicher Check"
        error = str(alert.get("last_error") or "Keiner")[:180]
        if alert.get("platform") == "twitch" and not (client_ok and token_ok):
            error = "Twitch-Zugangsdaten fehlen (siehe oben)."
        embed.add_field(
            name=f"{str(alert.get('platform', key)).capitalize()} · {str(alert.get('source', 'Quelle'))[:70]}",
            value=f"**Aktiv:** {'Ja' if alert.get('enabled') else 'Nein'}\n**Letzter Check:** {last_check}\n**Letzter Erfolg:** {last_success}\n**Letzter Fehler:** {error}",
            inline=False,
        )
    if len(alerts) > 20:
        embed.set_footer(text=f"Zeige 20 von {len(alerts)} Alerts.")
    await interaction.response.send_message(embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


@bot.tree.command(name="social-live", description="Zeigt erkannte Live-Twitch-Creator dieses Servers")
@app_commands.guild_only()
async def social_live(interaction: discord.Interaction):
    alerts = _normalize_social_alerts(guild_config(interaction.guild_id))
    live = [a for a in alerts.values() if a.get("platform") == "twitch" and a.get("enabled") and a.get("is_live")]
    embed = create_embed(title="🔴 Twitch · Live-Übersicht", color=discord.Color.purple())
    if not live:
        embed.description = "Aktuell wird kein konfigurierter Twitch-Creator als live erkannt. Die Übersicht aktualisiert sich, sobald der nächste Check läuft."
    else:
        for alert in live[:25]:
            name = str(alert.get("source", "Twitch"))
            title = str(alert.get("live_title") or "Livestream läuft")[:180]
            value = f"**{title}**\n[▶ Stream öffnen](https://twitch.tv/{name})"
            if alert.get("live_game"):
                value += f"\n**Kategorie:** {str(alert['live_game'])[:100]}"
            if alert.get("live_viewers") is not None:
                value += f"\n**Zuschauer:** {alert['live_viewers']}"
            if alert.get("live_since"):
                value += f"\n**Seit:** {alert['live_since']}"
            embed.add_field(name=f"🔴 {name}", value=value, inline=False)
        if len(live) > 25:
            embed.set_footer(text=f"Zeige 25 von {len(live)} Live-Creatorn.")
    await interaction.response.send_message(embed=embed, ephemeral=False, allowed_mentions=discord.AllowedMentions.none())


async def social_alert_loop():
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            import aiohttp
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
                for guild in list(bot.guilds):
                    cfg = guild_config(guild.id)
                    alerts = _normalize_social_alerts(cfg)
                    for alert_key, alert in list(alerts.items()):
                        if not alert.get("enabled"):
                            continue
                        platform = alert.get("platform")
                        source = str(alert.get("source", ""))
                        channel = guild.get_channel(int(alert.get("channel_id", 0)))
                        if not isinstance(channel, discord.TextChannel):
                            alert["last_check_at"] = datetime.now(timezone.utc).isoformat()
                            alert["last_error"] = "Ankündigungskanal nicht gefunden oder kein Textkanal."
                            save_json(CONFIG_FILE, config)
                            continue
                        now_iso = datetime.now(timezone.utc).isoformat()
                        alert["last_check_at"] = now_iso
                        key = f"{guild.id}:{platform}:{source}"
                        try:
                            role = _social_alert_role(guild, alert)
                            if platform == "youtube":
                                url = _normalize_youtube_source(source)
                                if url is None:
                                    alert["last_error"] = "Ungültige oder nicht vertrauenswürdige YouTube-Quelle; bitte /social-alert-set erneut verwenden."
                                    save_json(CONFIG_FILE, config)
                                    continue
                                async with session.get(url) as resp:
                                    if resp.status != 200:
                                        alert["last_error"] = f"YouTube RSS antwortete mit HTTP {resp.status}."
                                        save_json(CONFIG_FILE, config)
                                        continue
                                    root = ET.fromstring(await resp.text())
                                ns = {"atom": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
                                entries = root.findall("atom:entry", ns)
                                parsed_entries = []
                                for entry in entries:
                                    video_id = entry.findtext("yt:videoId", default="", namespaces=ns)
                                    if not video_id:
                                        continue
                                    title = entry.findtext("atom:title", default="Neues YouTube-Video", namespaces=ns)
                                    link_el = entry.find("atom:link", ns)
                                    link = link_el.attrib.get("href", "") if link_el is not None else f"https://www.youtube.com/watch?v={video_id}"
                                    published = entry.findtext("atom:published", default="", namespaces=ns)
                                    parsed_entries.append({"id": video_id, "title": title, "url": link, "published": published})
                                alert["last_success_at"] = now_iso
                                alert["last_error"] = None
                                seen = data.setdefault("social_alert_seen", {}).get(key)
                                seen_ids = seen if isinstance(seen, list) else ([seen] if isinstance(seen, str) and seen else [])
                                current_ids = [item["id"] for item in parsed_entries]
                                if current_ids and not seen_ids:
                                    # First check seeds the feed to avoid announcing older uploads.
                                    data["social_alert_seen"][key] = current_ids[:10]
                                elif current_ids:
                                    new_entries = []
                                    for item in parsed_entries:
                                        if item["id"] in seen_ids:
                                            break
                                        new_entries.append(item)
                                    for item in reversed(new_entries):
                                        embed = create_embed(
                                            title="📺 Neues YouTube-Video",
                                            description=f"**{item['title'][:250]}**\n[▶ Video ansehen]({item['url']})",
                                            color=discord.Color.red(),
                                        )
                                        video_id = item.get("id")
                                        if video_id:
                                            embed.set_thumbnail(url=f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg")
                                        if item["published"]:
                                            try:
                                                embed.timestamp = datetime.fromisoformat(item["published"].replace("Z", "+00:00"))
                                            except ValueError:
                                                pass
                                        await channel.send(content=role.mention if role else None, embed=embed, allowed_mentions=_social_alert_mentions(role))
                                    data["social_alert_seen"][key] = (current_ids + seen_ids)[:10]
                                save_json(DATA_FILE, data)
                            elif platform == "twitch":
                                client_id = os.getenv("TWITCH_CLIENT_ID")
                                token = os.getenv("TWITCH_ACCESS_TOKEN")
                                if not client_id or not token:
                                    missing = []
                                    if not client_id:
                                        missing.append("TWITCH_CLIENT_ID")
                                    if not token:
                                        missing.append("TWITCH_ACCESS_TOKEN")
                                    alert["last_error"] = "Fehlende Umgebungsvariablen: " + ", ".join(missing)
                                    save_json(CONFIG_FILE, config)
                                    continue
                                headers = {"Client-ID": client_id, "Authorization": f"Bearer {token}"}
                                async with session.get("https://api.twitch.tv/helix/streams", params={"user_login": source}, headers=headers) as resp:
                                    if resp.status != 200:
                                        if resp.status in (401, 403):
                                            alert["last_error"] = (
                                                f"Twitch-Zugangsdaten ungültig oder abgelaufen (HTTP {resp.status}); "
                                                "TWITCH_CLIENT_ID und TWITCH_ACCESS_TOKEN prüfen/erneuern."
                                            )
                                        elif resp.status == 429:
                                            retry_after = resp.headers.get("Retry-After") or resp.headers.get("Ratelimit-Reset")
                                            alert["last_error"] = (
                                                "Twitch-API-Rate-Limit erreicht"
                                                + (f"; API-Angabe zur Rücksetzung: {retry_after}" if retry_after else "")
                                            )
                                        else:
                                            alert["last_error"] = f"Twitch API antwortete mit HTTP {resp.status}."
                                        save_json(CONFIG_FILE, config)
                                        continue
                                    body = await resp.json()
                                stream = body.get("data", [])
                                current = stream[0] if stream else None
                                live_id = current.get("id") if current else None
                                alert["last_success_at"] = now_iso
                                alert["last_error"] = None
                                alert["is_live"] = bool(current)
                                alert["live_title"] = current.get("title") if current else None
                                alert["live_game"] = current.get("game_name") if current else None
                                alert["live_viewers"] = current.get("viewer_count") if current else None
                                alert["live_since"] = current.get("started_at") if current else None
                                seen = data.setdefault("social_alert_seen", {}).get(key)
                                if live_id and live_id != seen:
                                    embed = create_embed(
                                        title=f"🔴 {current.get('user_name', source)} ist jetzt live!",
                                        description=f"**{current.get('title') or 'Livestream gestartet'}**\n[▶ Zum Stream](https://twitch.tv/{source})",
                                        color=discord.Color.purple(),
                                    )
                                    game_name = current.get("game_name")
                                    if game_name:
                                        embed.add_field(name="🎮 Kategorie", value=str(game_name)[:200], inline=True)
                                    viewers = current.get("viewer_count")
                                    if viewers is not None:
                                        embed.add_field(name="👀 Zuschauer", value=str(viewers), inline=True)
                                    thumbnail = current.get("thumbnail_url")
                                    if thumbnail:
                                        embed.set_image(url=thumbnail.replace("{width}", "640").replace("{height}", "360"))
                                    if current.get("started_at"):
                                        try:
                                            embed.timestamp = datetime.fromisoformat(current["started_at"].replace("Z", "+00:00"))
                                        except ValueError:
                                            pass
                                    await channel.send(content=role.mention if role else None, embed=embed, allowed_mentions=_social_alert_mentions(role))
                                data["social_alert_seen"][key] = live_id
                                save_json(DATA_FILE, data)
                            else:
                                alert["last_error"] = f"Unbekannte Plattform: {platform}"
                                save_json(CONFIG_FILE, config)
                        except (aiohttp.ClientError, asyncio.TimeoutError, ET.ParseError, ValueError, discord.HTTPException) as exc:
                            alert["last_error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
                            save_json(CONFIG_FILE, config)
                            print(f"[SOCIAL ALERT] {platform}:{source} check failed: {type(exc).__name__}")
                        except Exception as exc:
                            alert["last_error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
                            save_json(CONFIG_FILE, config)
                            print(f"[SOCIAL ALERT] {platform}:{source} unexpected failure: {type(exc).__name__}")
                    save_json(CONFIG_FILE, config)
        except Exception as exc:
            print(f"[SOCIAL ALERT] Loop error: {type(exc).__name__}: {exc}")
        await asyncio.sleep(120)


# ----------------------------- SECURITY: HONEYPOT / ANTI-NUKE / ANTI-RAID -----------------------------

# Rolling counters live in memory; they reset when the bot restarts.
_nuke_events = defaultdict(deque)       # (guild_id, actor_id) -> timestamps
_raid_joins = defaultdict(deque)        # guild_id -> (timestamp, member_id)
_raid_lockdown_until = {}               # guild_id -> monotonic timestamp
_nuke_handled = set()                   # avoid handling the same audit entry twice
_nuke_handled_order = deque()           # bounds memory use for long-running bots


async def security_log(guild: discord.Guild, title: str, description: str, color=discord.Color.orange()):
    cfg = guild_config(guild.id)
    channel_id = cfg.get("security_log_channel")
    channel = guild.get_channel(channel_id) if channel_id else None
    if isinstance(channel, discord.TextChannel):
        try:
            embed = create_embed(title=title, description=description, color=color,
                                  timestamp=datetime.now(timezone.utc))
            await channel.send(embed=embed)
        except discord.HTTPException:
            pass


def is_security_trusted(member: discord.Member, cfg: dict):
    if member.id == member.guild.owner_id:
        return True
    role_id = cfg.get("trusted_security_role")
    if role_id and any(role.id == role_id for role in member.roles):
        return True
    return False


async def get_audit_actor(guild: discord.Guild, action: discord.AuditLogAction, target_id=None):
    # Audit entries can appear a moment after the Discord event.
    for attempt in range(4):
        try:
            async for entry in guild.audit_logs(limit=8, action=action):
                if target_id is None or getattr(entry.target, "id", None) == target_id:
                    if entry.id not in _nuke_handled:
                        _nuke_handled.add(entry.id)
                        _nuke_handled_order.append(entry.id)
                        # Audit-log deduplication must not grow without bound.
                        while len(_nuke_handled_order) > 5000:
                            expired_id = _nuke_handled_order.popleft()
                            _nuke_handled.discard(expired_id)
                        return entry.user, entry.id, entry.reason
        except (discord.Forbidden, discord.HTTPException):
            return None, None, None
        if attempt < 3:
            await asyncio.sleep(0.7)
    return None, None, None


async def record_nuke_action(guild: discord.Guild, action_name: str, action: discord.AuditLogAction,
                             target_id=None, target_name="unknown"):
    cfg = guild_config(guild.id)
    if not cfg.get("anti_nuke_enabled", False):
        return
    actor, entry_id, reason = await get_audit_actor(guild, action, target_id)
    if actor is None or actor.bot:
        return
    member = guild.get_member(actor.id)
    if member is None or is_security_trusted(member, cfg):
        return

    now = time.monotonic()
    key = (guild.id, actor.id)
    events = _nuke_events[key]
    while events and now - events[0] > 60:
        events.popleft()
    events.append(now)

    threshold = max(2, min(10, int(cfg.get("anti_nuke_threshold", 4))))
    await security_log(
        guild,
        "🛡️ Anti-Nuke: Aktion erkannt",
        f"**Akteur:** {actor.mention} (`{actor.id}`)\n"
        f"**Aktion:** {discord.utils.escape_markdown(action_name)}\n"
        f"**Ziel:** {discord.utils.escape_markdown(str(target_name))}\n"
        f"**Aktionen in 60 Sekunden:** {len(events)}/{threshold}\n"
        f"**Audit-Grund:** {discord.utils.escape_markdown(reason or 'Keiner')}",
    )

    if len(events) >= threshold:
        try:
            await member.kick(reason=f"Anti-Nuke: {len(events)} destructive actions in 60 seconds")
            await security_log(
                guild, "🚨 Anti-Nuke: Mitglied entfernt",
                f"{actor.mention} wurde nach **{len(events)} verdächtigen Aktionen innerhalb von 60 Sekunden** gekickt.",
                discord.Color.red(),
            )
            events.clear()
        except (discord.Forbidden, discord.HTTPException) as exc:
            await security_log(
                guild, "⚠️ Anti-Nuke konnte nicht eingreifen",
                f"Verdächtiger Akteur: {actor.mention}\n"
                f"Der Bot konnte ihn nicht kicken. Prüfe die Bot-Rollenposition und die Berechtigung **Mitglieder entfernen**.\n"
                f"Fehler: `{type(exc).__name__}`",
                discord.Color.red(),
            )


@bot.event
async def on_message(message: discord.Message):
    # Handle regular member messages only when they belong to a server.
    if message.guild and not message.author.bot:
        guild = message.guild
        cfg = guild_config(guild.id)
        if cfg.get("automod_enabled", False):
            now_mono = time.monotonic()
            key = (guild.id, message.author.id)
            recent = _automod_messages[key]
            window_seconds = max(3, min(30, int(cfg.get("automod_window_seconds", 8) or 8)))
            threshold = max(3, min(20, int(cfg.get("automod_threshold", 6) or 6)))
            timeout_minutes = max(1, min(60, int(cfg.get("automod_timeout_minutes", 2) or 2)))
            recent.append(now_mono)
            while recent and now_mono - recent[0] > window_seconds:
                recent.popleft()
            if len(recent) >= threshold and not message.author.guild_permissions.manage_messages:
                try:
                    await message.delete()
                    await message.author.timeout(timedelta(minutes=timeout_minutes), reason="AutoMod: Nachrichten-Spam")
                    recent.clear()
                    await security_log(guild, "🛡️ AutoMod: Spam erkannt", f"Mitglied: {message.author.mention} (`{message.author.id}`)\nKanal: {message.channel.mention}\nAktion: Nachrichten gelöscht und 2 Minuten Timeout.", discord.Color.orange())
                    return
                except (discord.Forbidden, discord.HTTPException):
                    pass

        # Count messages and award XP with a cooldown.
        stats = get_user_stats(guild.id, message.author.id)
        stats["messages"] = int(stats.get("messages", 0)) + 1
        activity_now = datetime.now(timezone.utc).isoformat()
        stats["last_message_at"] = activity_now
        stats["last_activity_at"] = activity_now
        earned_xp, reached_level = add_message_xp(guild.id, message.author.id)
        save_json(DATA_FILE, data)

        if reached_level:
            embed = create_embed(
                title="🎉 Levelaufstieg!",
                description=(
                    f"Glückwunsch {message.author.mention}!\n"
                    f"Du hast **Level {reached_level}** erreicht!"
                ),
                color=discord.Color.gold(),
            )
            levelup_channel_id = guild_config(guild.id).get("levelup_channel")
            levelup_channel = guild.get_channel(int(levelup_channel_id)) if levelup_channel_id else None
            target_channel = levelup_channel if isinstance(levelup_channel, discord.TextChannel) else message.channel
            await target_channel.send(embed=embed)
            level_role_id = guild_config(guild.id).get("level_roles", {}).get(str(reached_level))
            level_role = guild.get_role(int(level_role_id)) if level_role_id else None
            if level_role and isinstance(message.author, discord.Member):
                try:
                    await message.author.add_roles(level_role, reason=f"Level {reached_level} erreicht")
                except (discord.Forbidden, discord.HTTPException):
                    pass

        # Honeypot: log the violation, remove recent messages, then kick.
        cfg = guild_config(guild.id)
        honeypot_id = cfg.get("honeypot_channel")
        if honeypot_id and message.channel.id == honeypot_id:
            member = message.author if isinstance(message.author, discord.Member) else None
            if member is not None and member.id != guild.owner_id:
                reason = (
                    f"Honeypot-Regelverstoß: Nachricht im Honeypot-Kanal "
                    f"#{getattr(message.channel, 'name', 'unbekannt')}"
                )
                cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
                deleted_count = 0
                failed_channels = []
                original_message = message.content or "[Keine Textnachricht / möglicherweise nur ein Anhang]"
                original_message = discord.utils.escape_markdown(original_message[:700])

                try:
                    dm_embed = create_embed(
                        title="🚫 Du wurdest vom Server entfernt",
                        description=(
                            f"Du wurdest von **{guild.name}** gekickt, weil du im "
                            f"versteckten Honeypot-Kanal {message.channel.mention} geschrieben hast.\n\n"
                            "**Grund:** Dieser Kanal dient der Erkennung von Mitgliedern, "
                            "die verborgene Moderations-/Sicherheitskanäle aufrufen oder verwenden.\n\n"
                            "Zusätzlich werden deine Nachrichten aus den letzten 24 Stunden "
                            "auf diesem Server entfernt."
                        ),
                        color=discord.Color.red(),
                    )
                    await member.send(embed=dm_embed)
                except (discord.Forbidden, discord.HTTPException):
                    pass

                channels = list(guild.text_channels)
                try:
                    channels.extend(thread for thread in guild.threads if thread not in channels)
                except (AttributeError, discord.HTTPException):
                    pass

                for channel in channels:
                    if not hasattr(channel, "history"):
                        continue
                    try:
                        async for old_message in channel.history(limit=None, after=cutoff, oldest_first=False):
                            if old_message.author.id != member.id:
                                continue
                            try:
                                await old_message.delete()
                                deleted_count += 1
                            except (discord.Forbidden, discord.HTTPException):
                                if channel.name not in failed_channels:
                                    failed_channels.append(channel.name)
                    except (discord.Forbidden, discord.HTTPException, discord.InvalidData):
                        if getattr(channel, "name", None) and channel.name not in failed_channels:
                            failed_channels.append(channel.name)

                kick_succeeded = False
                kick_error = None
                try:
                    await member.kick(reason=reason)
                    kick_succeeded = True
                except discord.Forbidden:
                    kick_error = "Fehlende Berechtigung oder Bot-Rolle steht nicht hoch genug."
                except discord.HTTPException as exc:
                    kick_error = f"Discord-Fehler: {type(exc).__name__}"

                log_description = (
                    f"**Mitglied:** {member.mention} (`{member.id}`)\n"
                    f"**Kanal:** {message.channel.mention} (`{message.channel.id}`)\n"
                    f"**Grund:** Nachricht im Honeypot-Kanal\n"
                    f"**Nachricht:** {original_message}\n"
                    f"**Nachrichten aus den letzten 24 Stunden gelöscht:** {deleted_count}\n"
                    f"**Kick:** {'Erfolgreich' if kick_succeeded else 'FEHLGESCHLAGEN'}"
                )
                if failed_channels:
                    log_description += (
                        "\n**Kanäle mit fehlgeschlagenen Löschungen/fehlendem Zugriff:** "
                        + ", ".join(f"`{discord.utils.escape_markdown(name)}`" for name in failed_channels[:20])
                    )
                if kick_error:
                    log_description += f"\n**Fehler:** {kick_error}"

                await security_log(
                    guild,
                    "🍯 Honeypot ausgelöst — Mitglied gekickt",
                    log_description,
                    discord.Color.red(),
                )
                # Do not allow a honeypot message to execute a prefix command.
                return

    await bot.process_commands(message)


@bot.event
async def on_voice_state_update(
    member: discord.Member,
    before: discord.VoiceState,
    after: discord.VoiceState,
):
    """Track voice time and manage configured temporary voice channels."""
    cfg = guild_config(member.guild.id)
    trigger_id = cfg.get("tempvoice_trigger")
    if after.channel and trigger_id and after.channel.id == int(trigger_id):
        category_id = cfg.get("tempvoice_category")
        category = member.guild.get_channel(int(category_id)) if category_id else after.channel.category
        try:
            temp_channel = await member.guild.create_voice_channel(
                name=f"{member.display_name[:80]}'s Raum", category=category,
                reason="Temporärer Voice-Kanal erstellt")
            data.setdefault("temp_voice_channels", {})[str(temp_channel.id)] = member.guild.id
            save_json(DATA_FILE, data)
            await member.move_to(temp_channel, reason="In eigenen temporären Voice-Kanal verschieben")
        except (discord.Forbidden, discord.HTTPException) as exc:
            print(f"[TEMPVOICE] Could not create/move channel: {type(exc).__name__}: {exc}")
    if before.channel and before.channel.id != (int(trigger_id) if trigger_id else -1):
        if str(before.channel.id) in data.setdefault("temp_voice_channels", {}) and len(before.channel.members) == 0:
            try:
                await before.channel.delete(reason="Temporärer Voice-Kanal ist leer")
            except (discord.Forbidden, discord.HTTPException):
                pass
            data["temp_voice_channels"].pop(str(before.channel.id), None)
            save_json(DATA_FILE, data)
    if member.bot:
        return

    key = (member.guild.id, member.id)
    now = datetime.now(timezone.utc)

    # Voice joins/leaves count as activity and are stored with a UTC timestamp.
    if before.channel != after.channel and (before.channel is None or after.channel is None):
        stats = get_user_stats(member.guild.id, member.id)
        activity_now = now.isoformat()
        stats["last_voice_at"] = activity_now
        stats["last_activity_at"] = activity_now
        save_json(DATA_FILE, data)

    # Leaving voice: add the completed session to the persistent total.
    if before.channel is not None and after.channel is None:
        started_at = _active_voice_sessions.pop(key, None)
        if started_at is not None:
            elapsed = max(0, int((now - started_at).total_seconds()))
            stats = get_user_stats(member.guild.id, member.id)
            stats["voice_seconds"] = int(stats.get("voice_seconds", 0)) + elapsed
            save_json(DATA_FILE, data)

    # Joining voice starts a new session. Moving between voice channels keeps
    # the same session running and does not double-count time.
    elif before.channel is None and after.channel is not None:
        _active_voice_sessions[key] = now


async def build_bot_welcome_embeds(guild: discord.Guild):
    """Create a welcome message and a live list of registered slash commands."""
    welcome = discord.Embed(
        title="🐺 Danke, dass du IronWolf hinzugefügt hast!",
        description=(
            f"Ich freue mich, auf **{discord.utils.escape_markdown(guild.name)}** dabei zu sein!\n\n"
            "Ich biete unter anderem Spieler-Anmeldung/Abmeldung, Tickets, Moderation und Sicherheit, "
            "Verifizierung, Umfragen, Vorschläge, Events, Statistiken, temporäre Voice-Channels, "
            "Giveaways, Wirtschaft und weitere Community-Funktionen. Welche Funktionen verfügbar sind, "
            "hängt teilweise von der Server-Konfiguration ab.\n\n"
            "Nutze **/** in Discord, um die Slash-Befehle zu sehen. Die vollständige Liste steht unten."
        ),
        color=discord.Color.blurple(),
        timestamp=datetime.now(timezone.utc),
    )
    welcome.set_footer(text="IronWolf · Created by KingSnowy")

    lines = []
    def collect(commands_list, prefix=""):
        for command in commands_list:
            qualified = f"{prefix} {command.name}".strip()
            description = (getattr(command, "description", None) or "Keine Beschreibung").replace("\n", " ").strip()
            lines.append(f"**/{qualified}** — {description}")
            if isinstance(command, app_commands.Group):
                collect(command.commands, qualified)

    collect(bot.tree.get_commands())
    lines = sorted(set(lines), key=str.casefold)
    if not lines:
        lines = ["Die Slash-Befehle werden gerade geladen. Bitte prüfe sie in Discord mit **/**."]

    embeds = [welcome]
    page, length = [], 0
    for line in lines:
        if length + len(line) + 1 > 3500 and page:
            embed = discord.Embed(
                title="📖 IronWolf-Befehle",
                description="\n".join(page),
                color=discord.Color.blurple(),
            )
            embed.set_footer(text=f"Seite {len(embeds)} · Created by KingSnowy")
            embeds.append(embed)
            page, length = [], 0
        page.append(line)
        length += len(line) + 1
    if page:
        embed = discord.Embed(
            title="📖 IronWolf-Befehle" if len(embeds) == 1 else "📖 IronWolf-Befehle (Fortsetzung)",
            description="\n".join(page),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="Slash-Befehle · Created by KingSnowy")
        embeds.append(embed)
    return embeds


async def notify_bot_server_change(guild: discord.Guild, joined: bool):
    """Send bot join/leave notices to a configured home-server text channel."""
    channel_id = os.getenv("IRONWOLF_LOG_CHANNEL_ID", "").strip()
    if not channel_id.isdigit():
        print("[SERVER-LOG] Set IRONWOLF_LOG_CHANNEL_ID to the destination text channel ID.")
        return
    try:
        channel = bot.get_channel(int(channel_id))
        if channel is None:
            channel = await bot.fetch_channel(int(channel_id))
        if not hasattr(channel, "send"):
            print(f"[SERVER-LOG] Configured channel {channel_id} cannot receive messages.")
            return
        embed = discord.Embed(
            title="🟢 IronWolf wurde hinzugefügt" if joined else "🔴 IronWolf wurde entfernt",
            description=(
                f"**Server:** {discord.utils.escape_markdown(guild.name)}\n"
                f"**Server-ID:** `{guild.id}`\n"
                f"**Mitglieder:** {guild.member_count if guild.member_count is not None else 'unbekannt'}"
            ),
            color=discord.Color.green() if joined else discord.Color.red(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text="IronWolf · Server-Protokoll")
        await channel.send(embed=embed)
        print(f"[SERVER-LOG] Sent {'join' if joined else 'leave'} notice for guild {guild.id}.")
    except (discord.HTTPException, discord.Forbidden, ValueError) as exc:
        print(f"[SERVER-LOG] Could not send server change notice: {type(exc).__name__}: {exc}")


@bot.event
async def on_guild_join(guild: discord.Guild):
    await notify_bot_server_change(guild, joined=True)
    """Thank the server owner by DM and post the command list in the new server."""
    embeds = await build_bot_welcome_embeds(guild)

    try:
        await guild.owner.send(embeds=embeds[:10])
        print(f"[WELCOME] Sent IronWolf welcome DM to owner of guild {guild.id}.")
    except (discord.Forbidden, discord.HTTPException, AttributeError) as exc:
        print(f"[WELCOME] Could not DM owner of guild {guild.id}: {type(exc).__name__}: {exc}")

    target = guild.system_channel
    if target is None or not target.permissions_for(guild.me).send_messages:
        target = next(
            (
                channel for channel in guild.text_channels
                if channel.permissions_for(guild.me).send_messages
                and channel.permissions_for(guild.me).embed_links
            ),
            None,
        )
    if target is None:
        print(f"[WELCOME] No channel available to post welcome message in guild {guild.id}.")
        return

    # Discord allows at most 10 embeds per message; send further pages in batches.
    try:
        for start in range(0, len(embeds), 10):
            await target.send(embeds=embeds[start:start + 10])
        print(f"[WELCOME] Posted IronWolf welcome and command list in guild {guild.id}.")
    except (discord.Forbidden, discord.HTTPException) as exc:
        print(f"[WELCOME] Could not post welcome in guild {guild.id}: {type(exc).__name__}: {exc}")


@bot.event
async def on_guild_remove(guild: discord.Guild):
    """Notify the configured home server when IronWolf leaves a guild."""
    await notify_bot_server_change(guild, joined=False)


@bot.event
async def on_member_join(member: discord.Member):
    cfg = guild_config(member.guild.id)
    if cfg.get("anti_raid_enabled", False) and not member.bot:
        now_mono = time.monotonic()
        joins = _raid_join_times[member.guild.id]
        joins.append(now_mono)
        while joins and now_mono - joins[0] > 60:
            joins.popleft()
        raid_threshold = max(3, min(30, int(cfg.get("raid_threshold", 8) or 8)))
        if len(joins) >= raid_threshold and (len(joins) == raid_threshold or len(joins) % raid_threshold == 0):
            await security_log(member.guild, "Anti-Raid: Beitrittswelle erkannt", f"{len(joins)} Mitglieder sind innerhalb von 60 Sekunden beigetreten. Alarm ausgelöst; automatische Quarantäne ist nicht konfiguriert.", discord.Color.red())
    # Attribute this join to an invite by comparing its use count with the last snapshot.
    if cfg.get("invite_tracker_enabled") and cfg.get("invite_log_channel") and guild_has_feature(cfg, "invite_tracker"):
        guild_id = member.guild.id
        previous = _invite_cache.get(guild_id, {})
        try:
            current_invites = await member.guild.invites()
            used_invite = next(
                (invite for invite in current_invites
                 if invite.code in previous and invite.uses is not None
                 and invite.uses > previous[invite.code]),
                None,
            )
            _invite_cache[guild_id] = {
                invite.code: (invite.uses or 0) for invite in current_invites
            }
            log_channel = member.guild.get_channel(int(cfg["invite_log_channel"]))
            if isinstance(log_channel, discord.TextChannel):
                embed = discord.Embed(
                    title="🐺 IronWolf · Invite Tracker",
                    description=f"**{discord.utils.escape_markdown(member.display_name)}** ist dem Server beigetreten.",
                    color=discord.Color.from_rgb(70, 195, 255),
                    timestamp=datetime.now(timezone.utc),
                )
                embed.add_field(name="👤 Neues Mitglied", value=f"{member.mention}\n`{member}`", inline=False)
                if used_invite:
                    inviter = used_invite.inviter
                    embed.add_field(
                        name="🤝 Eingeladen von",
                        value=inviter.mention if inviter else "Unbekannt",
                        inline=True,
                    )
                    embed.add_field(name="🔗 Invite-Code", value=f"`{used_invite.code}`", inline=True)
                    embed.add_field(name="📊 Verwendungen", value=str(used_invite.uses or 0), inline=True)
                else:
                    embed.add_field(
                        name="🔎 Einladung",
                        value="Die verwendete Einladung konnte nicht eindeutig erkannt werden (z. B. Vanity-URL oder einmaliger Invite).",
                        inline=False,
                    )
                embed.set_thumbnail(url=member.display_avatar.url)
                embed.set_footer(text=f"IronWolf Invite Tracker · {member.guild.name}")
                await log_channel.send(
                    embed=embed,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
        except (discord.Forbidden, discord.HTTPException) as exc:
            print(f"[INVITE TRACKER] Could not read invites for guild {guild_id}: {type(exc).__name__}: {exc}")
    unverified_id = cfg.get("unverified_role")
    unverified_role = member.guild.get_role(int(unverified_id)) if unverified_id else None
    if unverified_role and not member.bot:
        try:
            await member.add_roles(unverified_role, reason="Neue Mitglieder müssen sich verifizieren")
        except (discord.Forbidden, discord.HTTPException) as exc:
            print(f"[VERIFY] Could not assign unverified role: {type(exc).__name__}: {exc}")
    # Welcome messages for bots can be toggled per server with /welcome-bots.
    should_welcome = (not member.bot) or cfg.get("welcome_bots_enabled", False)
    welcome_channel_id = cfg.get("welcome_channel")
    if should_welcome and welcome_channel_id:
        welcome_channel = member.guild.get_channel(int(welcome_channel_id))
        # The channel may not be in cache; fetch it as a fallback.
        if welcome_channel is None:
            try:
                welcome_channel = await bot.fetch_channel(int(welcome_channel_id))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
                print(f"[WELCOME] Could not find/access channel {welcome_channel_id} in guild {member.guild.id}: {type(exc).__name__}: {exc}")
                welcome_channel = None
        if isinstance(welcome_channel, discord.TextChannel):
            template = cfg.get("welcome_message") or "Willkommen {mention} auf **{server}**! Du bist Mitglied Nr. **{count}**."
            welcome_text = (template.replace("{mention}", member.mention)
                            .replace("{user}", discord.utils.escape_markdown(member.display_name))
                            .replace("{server}", discord.utils.escape_markdown(member.guild.name))
                            .replace("{count}", str(member.guild.member_count or len(member.guild.members))))
            try:
                if cfg.get("welcome_embed", False):
                    welcome_embed = discord.Embed(
                        title="👋 Willkommen!",
                        description=welcome_text[:4000],
                        color=discord.Color.from_rgb(70, 195, 255),
                        timestamp=datetime.now(timezone.utc),
                    )
                    welcome_embed.set_thumbnail(url=member.display_avatar.url)
                    welcome_embed.set_footer(text=f"{member.guild.name} · Willkommen")
                    await welcome_channel.send(embed=welcome_embed, allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False))
                else:
                    await welcome_channel.send(
                        welcome_text[:1900],
                        allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
                    )
                print(f"[WELCOME] Sent welcome for {member} ({member.id}) in #{welcome_channel.name} ({member.guild.id}).")
            except (discord.Forbidden, discord.HTTPException) as exc:
                print(f"[WELCOME] Sending failed in guild {member.guild.id}, channel {welcome_channel_id}: {type(exc).__name__}: {exc}")
        elif welcome_channel is not None:
            print(f"[WELCOME] Configured channel {welcome_channel_id} is not a text channel (guild {member.guild.id}).")
    elif should_welcome:
        print(f"[WELCOME] No welcome channel configured for guild {member.guild.id}. Run /welcome-set in Discord.")
    # Preserve the existing anti-raid behavior: bot joins do not count as member raids.
    if member.bot or not cfg.get("anti_raid_enabled", False):
        return

    now = time.monotonic()
    joins = _raid_joins[member.guild.id]
    joins.append((now, member.id))
    while joins and now - joins[0][0] > 10:
        joins.popleft()

    threshold = max(3, min(30, int(cfg.get("raid_threshold", 8))))
    if len(joins) >= threshold and now >= _raid_lockdown_until.get(member.guild.id, 0):
        _raid_lockdown_until[member.guild.id] = now + 60
        await security_log(
            member.guild, "🚨 Anti-Raid: Beitrittswelle erkannt",
            f"**{len(joins)} Mitglieder in 10 Sekunden.**\n"
            f"Raid-Schutz für 60 Sekunden aktiviert. Neu beitretende Nicht-Bots werden in dieser Zeit "
            f"für 10 Minuten timeoutet, sofern die Bot-Berechtigungen dies erlauben.",
            discord.Color.red(),
        )

    if now < _raid_lockdown_until.get(member.guild.id, 0):
        try:
            await member.timeout(datetime.now(timezone.utc) + timedelta(minutes=10),
                                 reason="Anti-Raid: member joined during raid lockdown")
            await security_log(
                member.guild, "🧯 Anti-Raid: Beitritt eingeschränkt",
                f"{member.mention} (`{member.id}`) wurde während des Raid-Schutzes für 10 Minuten timeoutet.",
                discord.Color.orange(),
            )
        except (discord.Forbidden, discord.HTTPException):
            await security_log(
                member.guild, "⚠️ Anti-Raid: Timeout fehlgeschlagen",
                f"{member.mention} ist während des Raid-Schutzes beigetreten, konnte aber nicht timeoutet werden. "
                "Prüfe die Bot-Berechtigungen und Rollenposition.",
                discord.Color.red(),
            )


@bot.event
async def on_guild_channel_delete(channel: discord.abc.GuildChannel):
    await record_nuke_action(channel.guild, "Channel gelöscht", discord.AuditLogAction.channel_delete,
                             channel.id, channel.name)


@bot.event
async def on_guild_channel_create(channel: discord.abc.GuildChannel):
    await record_nuke_action(channel.guild, "Channel erstellt", discord.AuditLogAction.channel_create,
                             channel.id, channel.name)


@bot.event
async def on_guild_role_delete(role: discord.Role):
    await record_nuke_action(role.guild, "Rolle gelöscht", discord.AuditLogAction.role_delete,
                             role.id, role.name)


@bot.event
async def on_guild_role_create(role: discord.Role):
    await record_nuke_action(role.guild, "Rolle erstellt", discord.AuditLogAction.role_create,
                             role.id, role.name)


@bot.event
async def on_member_ban(guild: discord.Guild, user: discord.User):
    await record_nuke_action(guild, "Mitglied gebannt", discord.AuditLogAction.ban,
                             user.id, str(user))


@bot.event
async def on_member_remove(member: discord.Member):
    # Send the configured goodbye message when a member leaves or is removed.
    cfg = guild_config(member.guild.id)
    leave_channel_id = cfg.get("goodbye_channel")
    if cfg.get("leave_enabled", True) and leave_channel_id:
        channel = member.guild.get_channel(int(leave_channel_id))
        if isinstance(channel, discord.TextChannel):
            template = cfg.get("leave_message") or "👋 **{user}** hat **{server}** verlassen. Wir wünschen dir alles Gute!"
            leave_text = (template.replace("{user}", discord.utils.escape_markdown(member.display_name))
                          .replace("{server}", discord.utils.escape_markdown(member.guild.name))
                          .replace("{count}", str(member.guild.member_count or len(member.guild.members))))
            try:
                if cfg.get("leave_embed", False):
                    goodbye_embed = discord.Embed(
                        title="👋 Auf Wiedersehen",
                        description=leave_text[:4000],
                        color=discord.Color.from_rgb(130, 150, 175),
                        timestamp=datetime.now(timezone.utc),
                    )
                    goodbye_embed.set_thumbnail(url=member.display_avatar.url)
                    goodbye_embed.set_footer(text=f"{member.guild.name} · Auf Wiedersehen")
                    await channel.send(embed=goodbye_embed, allowed_mentions=discord.AllowedMentions.none())
                else:
                    await channel.send(leave_text[:1900], allowed_mentions=discord.AllowedMentions.none())
            except (discord.Forbidden, discord.HTTPException) as exc:
                print(f"[GOODBYE] Sending failed in guild {member.guild.id}: {type(exc).__name__}: {exc}")
    # Fires for both voluntary leaves and kicks; only a matching kick audit entry is counted.
    await record_nuke_action(member.guild, "Mitglied gekickt", discord.AuditLogAction.kick,
                             member.id, str(member))


@bot.event
async def on_guild_role_update(before: discord.Role, after: discord.Role):
    dangerous = discord.Permissions(administrator=True, manage_guild=True, manage_roles=True,
                                    manage_channels=True, ban_members=True, kick_members=True)
    if (before.permissions.value & dangerous.value) != (after.permissions.value & dangerous.value):
        await record_nuke_action(after.guild, "Gefährliche Rollenrechte geändert",
                                 discord.AuditLogAction.role_update, after.id, after.name)


@bot.event
async def on_guild_channel_update(before: discord.abc.GuildChannel, after: discord.abc.GuildChannel):
    # Channel permission overwrites can be used to hide or expose many channels.
    if before.overwrites != after.overwrites:
        await record_nuke_action(after.guild, "Channel-Berechtigungen geändert",
                                 discord.AuditLogAction.overwrite_update, after.id, after.name)


@bot.event
async def on_webhooks_update(channel: discord.abc.GuildChannel):
    await record_nuke_action(channel.guild, "Webhooks geändert",
                             discord.AuditLogAction.webhook_create, None, channel.name)


@bot.event
async def on_member_join_bot_security(member: discord.Member):
    # This named helper is intentionally not a Discord event handler.
    return


@bot.tree.command(name="security-setup", description="Aktiviert den grundlegenden IronWolf-Sicherheitsschutz")
@app_commands.guild_only()
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(
    log_kanal="Privater Kanal für Sicherheitsmeldungen",
    raid_schwelle="Beitritte innerhalb von 10 Sekunden (3–30)",
    anti_spam="Nachrichten-Spam automatisch eindämmen"
)
async def security_setup(
    interaction: discord.Interaction,
    log_kanal: discord.TextChannel,
    raid_schwelle: app_commands.Range[int, 3, 30] = 8,
    anti_spam: bool = True,
):
    """One-command setup for the existing anti-raid, anti-nuke and AutoMod protections."""
    cfg = guild_config(interaction.guild_id)
    cfg["security_log_channel"] = log_kanal.id
    cfg["anti_raid_enabled"] = True
    cfg["raid_threshold"] = int(raid_schwelle)
    cfg["anti_nuke_enabled"] = True
    cfg["anti_nuke_threshold"] = max(2, min(10, int(cfg.get("anti_nuke_threshold", 4))))
    cfg["automod_enabled"] = anti_spam
    save_json(CONFIG_FILE, config)

    # Report missing permissions during setup so admins can fix protection gaps.
    warnings = []
    bot_member = interaction.guild.get_member(bot.user.id) if bot.user else None
    if bot_member is None:
        warnings.append("Bot-Mitglied konnte nicht ermittelt werden; Rechte bitte manuell prüfen.")
    else:
        log_permissions = log_kanal.permissions_for(bot_member)
        if not log_permissions.view_channel or not log_permissions.send_messages or not log_permissions.embed_links:
            warnings.append(f"Im Log-Kanal {log_kanal.mention} fehlen dem Bot möglicherweise Kanal ansehen, Nachrichten senden oder Links einbetten.")
        if not bot_member.guild_permissions.view_audit_log:
            warnings.append("Audit-Log ansehen fehlt: Anti-Nuke kann Akteure nicht zuverlässig zuordnen.")
        if not bot_member.guild_permissions.kick_members:
            warnings.append("Mitglieder entfernen fehlt: Anti-Nuke kann erkannte Akteure nicht entfernen.")
        if not bot_member.guild_permissions.moderate_members:
            warnings.append("Mitglieder moderieren fehlt: Anti-Raid und AutoMod können Timeouts nicht setzen.")

    embed = create_embed(
        title="🛡️ IronWolf Security eingerichtet",
        description=(
            f"**Sicherheitslogs:** {log_kanal.mention}\n"
            f"**Anti-Raid:** Aktiv · {raid_schwelle} Beitritte/10 Sekunden; "
            "Raid-Schutz schränkt neue Mitglieder vorübergehend ein.\n"
            f"**Anti-Nuke:** Aktiv · Schwelle {cfg['anti_nuke_threshold']} verdächtige Aktionen/60 Sekunden.\n"
            f"**AutoMod Anti-Spam:** {'Aktiv' if anti_spam else 'Deaktiviert'} · "
            "6 Nachrichten/8 Sekunden lösen eine Löschung und einen 2-Minuten-Timeout aus.\n\n"
            "Hinweis: Der Bot benötigt passende Moderations- und Audit-Log-Berechtigungen "
            "sowie eine ausreichend hohe Rollenposition."
            + (("\n\n⚠️ **Berechtigungsprüfung**\n" + "\n".join(f"• {item}" for item in warnings)) if warnings else "\n\n✅ Die grundlegenden Berechtigungen wurden geprüft.")
        ),
        color=discord.Color.green(),
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="security-set-log", description="Set the security log channel")
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(kanal="Channel for honeypot, anti-nuke and anti-raid logs")
async def security_set_log(interaction: discord.Interaction, kanal: discord.TextChannel):
    cfg = guild_config(interaction.guild_id)
    cfg["security_log_channel"] = kanal.id
    save_json(CONFIG_FILE, config)
    await interaction.response.send_message(f"Security logs will be sent to {kanal.mention}.", ephemeral=True)


@bot.tree.command(name="security-set-honeypot", description="Set the honeypot channel")
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(kanal="A hidden channel that ordinary members should not write in")
async def security_set_honeypot(interaction: discord.Interaction, kanal: discord.TextChannel):
    cfg = guild_config(interaction.guild_id)
    cfg["honeypot_channel"] = kanal.id
    save_json(CONFIG_FILE, config)
    await interaction.response.send_message(
        f"Honeypot channel set to {kanal.mention}. Members who write there will be kicked, their messages from the last 24 hours will be deleted where the bot has access, and the incident will be logged.",
        ephemeral=True,
    )


@bot.tree.command(name="security-set-raid", description="Set the anti-raid join threshold")
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(anzahl="Join count within 10 seconds (3–30)")
async def security_set_raid(interaction: discord.Interaction, anzahl: app_commands.Range[int, 3, 30]):
    cfg = guild_config(interaction.guild_id)
    cfg["raid_threshold"] = int(anzahl)
    save_json(CONFIG_FILE, config)
    await interaction.response.send_message(
        f"Anti-raid threshold set to {anzahl} joins in 10 seconds. During a detected raid, new members are timeouted for 10 minutes for 60 seconds.",
        ephemeral=True,
    )


@bot.tree.command(name="security-set-antinuke", description="Set the anti-nuke action threshold")
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(anzahl="Destructive actions within 60 seconds before the actor is kicked (2–10)")
async def security_set_antinueke(interaction: discord.Interaction, anzahl: app_commands.Range[int, 2, 10]):
    cfg = guild_config(interaction.guild_id)
    cfg["anti_nuke_threshold"] = int(anzahl)
    save_json(CONFIG_FILE, config)
    await interaction.response.send_message(
        f"Anti-nuke threshold set to {anzahl} tracked actions within 60 seconds.",
        ephemeral=True,
    )


@bot.tree.command(name="security-toggle", description="Enable or disable anti-nuke or anti-raid")
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(system="Choose a security system", aktiviert="Enable or disable it")
@app_commands.choices(system=[
    app_commands.Choice(name="Anti-Nuke", value="anti_nuke_enabled"),
    app_commands.Choice(name="Anti-Raid", value="anti_raid_enabled"),
])
async def security_toggle(interaction: discord.Interaction, system: app_commands.Choice[str], aktiviert: bool):
    cfg = guild_config(interaction.guild_id)
    cfg[system.value] = aktiviert
    save_json(CONFIG_FILE, config)
    await interaction.response.send_message(
        f"{system.name} {'enabled' if aktiviert else 'disabled'}.", ephemeral=True
    )


@bot.tree.command(name="security-set-trusted-role", description="Set a role exempt from anti-nuke actions")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(rolle="Trusted role (use carefully)")
async def security_set_trusted_role(interaction: discord.Interaction, rolle: discord.Role):
    cfg = guild_config(interaction.guild_id)
    cfg["trusted_security_role"] = rolle.id
    save_json(CONFIG_FILE, config)
    await interaction.response.send_message(
        f"Members with {rolle.mention} are exempt from anti-nuke. Only assign this role to trusted staff.",
        ephemeral=True,
    )


# BEGIN INLINE APPLICATIONS SYSTEM
# The application panel and review workflow live in this entrypoint so Railway
# does not depend on loading a separate applications extension.
"""Persistent application panels and staff review workflow for IronWolf."""
import json
from pathlib import Path
from datetime import datetime, timezone
import discord
from discord import app_commands
from discord.ext import commands

CONFIG_PATH = data_path("applications_config.json")
RECORDS_PATH = data_path("applications.json")

def read_json(path, default):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, type(default)) else default
    except (OSError, json.JSONDecodeError):
        return default

CONFIG = read_json(CONFIG_PATH, {"guilds": {}})
RECORDS = read_json(RECORDS_PATH, {"applications": {}})

def save_config():
    CONFIG_PATH.write_text(json.dumps(CONFIG, ensure_ascii=False, indent=2), encoding="utf-8")

def save_records():
    RECORDS_PATH.write_text(json.dumps(RECORDS, ensure_ascii=False, indent=2), encoding="utf-8")

def guild_settings(guild_id):
    return CONFIG.setdefault("guilds", {}).setdefault(str(guild_id), {})

def safe_text(value, limit=1000):
    return discord.utils.escape_mentions(str(value or ""))[:limit]

# Bewerbungen laufen als persönliches Frage-Antwort-Gespräch in den DMs.
ACTIVE_APPLICATIONS = {}

class ApplicationPanelView(discord.ui.View):
    def __init__(self, cog, guild_id):
        super().__init__(timeout=None)
        self.add_item(ApplicationStartButton(cog, guild_id))

class ApplicationStartButton(discord.ui.Button):
    def __init__(self, cog, guild_id):
        super().__init__(label="Jetzt bewerben", emoji="📝", style=discord.ButtonStyle.success, custom_id="ironwolf:application:start:{}".format(guild_id))
        self.cog, self.guild_id = cog, guild_id

    async def callback(self, interaction):
        if not interaction.guild or interaction.guild.id != self.guild_id:
            await interaction.response.send_message("Bitte nutze das Panel auf dem richtigen Server.", ephemeral=True)
            return
        questions = guild_settings(self.guild_id).get("questions") or ["Wie lautet dein Name?", "Wie alt bist du?", "Warum möchtest du dich bewerben?", "Welche Erfahrungen bringst du mit?", "Warum sollten wir dich auswählen?"]
        if any(row.get("guild_id") == self.guild_id and row.get("user_id") == interaction.user.id and row.get("status") == "pending" for row in RECORDS.setdefault("applications", {}).values()):
            await interaction.response.send_message("Du hast bereits eine offene Bewerbung. Warte bitte auf die Rückmeldung des Teams.", ephemeral=True)
            return
        try:
            dm = interaction.user.dm_channel or await interaction.user.create_dm()
            ACTIVE_APPLICATIONS[(self.guild_id, interaction.user.id)] = {"questions": questions, "answers": [], "index": 0}
            await dm.send("👋 **Deine Bewerbung für {}**\nIch stelle dir die Fragen einzeln. Antworte einfach direkt hier in diesem Chat. Schreibe abbrechen, um aufzuhören.\n\n**Frage 1/{}:** {}".format(interaction.guild.name, len(questions), questions[0]))
        except (discord.Forbidden, discord.HTTPException):
            ACTIVE_APPLICATIONS.pop((self.guild_id, interaction.user.id), None)
            await interaction.response.send_message("Ich kann dir keine DM senden. Bitte aktiviere Direktnachrichten vom Server und versuche es erneut.", ephemeral=True)
            return
        await interaction.response.send_message("📩 Ich habe dir eine DM geschickt. Beantworte dort die Fragen nacheinander.", ephemeral=True)

class ApplicationReviewView(discord.ui.View):
    def __init__(self, cog, app_id):
        super().__init__(timeout=None)
        self.add_item(ApplicationDecisionButton(cog, app_id, "approve"))
        self.add_item(ApplicationDecisionButton(cog, app_id, "reject"))

class ApplicationDecisionButton(discord.ui.Button):
    def __init__(self, cog, app_id, decision):
        self.cog, self.app_id, self.decision = cog, app_id, decision
        super().__init__(label="Annehmen" if decision == "approve" else "Ablehnen", emoji="✅" if decision == "approve" else "❌", style=discord.ButtonStyle.success if decision == "approve" else discord.ButtonStyle.danger, custom_id="ironwolf:application:{}:{}".format(decision, app_id))

    async def callback(self, interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Nur Teammitglieder auf dem Server können Bewerbungen bearbeiten.", ephemeral=True)
            return
        settings = guild_settings(interaction.guild.id)
        role_id = settings.get("review_role_id")
        allowed_role = role_id and any(role.id == role_id for role in interaction.user.roles)
        if not (interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.manage_guild or allowed_role):
            await interaction.response.send_message("Dir fehlt die Berechtigung, Bewerbungen zu bearbeiten.", ephemeral=True)
            return
        row = RECORDS.setdefault("applications", {}).get(self.app_id)
        if not row or row.get("guild_id") != interaction.guild.id:
            await interaction.response.send_message("Bewerbung nicht gefunden.", ephemeral=True)
            return
        if row.get("status") != "pending":
            await interaction.response.send_message("Diese Bewerbung wurde bereits bearbeitet.", ephemeral=True)
            return
        row["status"] = "accepted" if self.decision == "approve" else "rejected"
        row["reviewed_by"] = interaction.user.id
        row["reviewed_at"] = datetime.now(timezone.utc).isoformat()
        save_records()
        embed = interaction.message.embeds[0] if interaction.message and interaction.message.embeds else discord.Embed(title="Bewerbung")
        embed.color = discord.Color.green() if self.decision == "approve" else discord.Color.red()
        embed.description = "Bewerber: <@{}>\nStatus: {}\nBearbeitet von: {}".format(row["user_id"], "✅ Angenommen" if self.decision == "approve" else "❌ Abgelehnt", interaction.user.mention)
        await interaction.response.edit_message(embed=embed, view=None)
        applicant = interaction.guild.get_member(row["user_id"])
        if applicant:
            try:
                await applicant.send("Deine Bewerbung auf {} wurde {}.".format(interaction.guild.name, "angenommen" if self.decision == "approve" else "abgelehnt"))
            except (discord.Forbidden, discord.HTTPException):
                pass
        log_channel = interaction.guild.get_channel(settings.get("log_channel_id") or 0)
        if isinstance(log_channel, discord.TextChannel):
            log = discord.Embed(title="📋 Bewerbung bearbeitet", description="Bewerber: <@{}>\nEntscheidung: {}\nBearbeiter: {}\nID: {}".format(row["user_id"], row["status"], interaction.user.mention, self.app_id), color=discord.Color.green() if self.decision == "approve" else discord.Color.red(), timestamp=datetime.now(timezone.utc))
            await log_channel.send(embed=log, allowed_mentions=discord.AllowedMentions.none())
        await interaction.followup.send("Entscheidung gespeichert und protokolliert.", ephemeral=True)

class Applications(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or message.guild is not None:
            return
        matching = next(((key, value) for key, value in ACTIVE_APPLICATIONS.items() if key[1] == message.author.id), None)
        if not matching:
            return
        key, session = matching
        guild_id, user_id = key
        if message.content.strip().lower() in {"abbrechen", "abbruch", "cancel"}:
            ACTIVE_APPLICATIONS.pop(key, None)
            await message.channel.send("Deine Bewerbung wurde abgebrochen. Du kannst jederzeit erneut auf Jetzt bewerben klicken.")
            return
        session["answers"].append({"question": session["questions"][session["index"]], "answer": message.content.strip() or "[Keine Textantwort]"})
        session["index"] += 1
        if session["index"] < len(session["questions"]):
            await message.channel.send("**Frage {}/{}:** {}".format(session["index"] + 1, len(session["questions"]), session["questions"][session["index"]]))
            return
        if any(row.get("guild_id") == guild_id and row.get("user_id") == user_id and row.get("status") == "pending" for row in RECORDS.setdefault("applications", {}).values()):
            ACTIVE_APPLICATIONS.pop(key, None)
            await message.channel.send("Du hast bereits eine offene Bewerbung. Warte bitte auf die Rückmeldung des Teams.")
            return
        settings = guild_settings(guild_id)
        guild = self.bot.get_guild(guild_id)
        channel = guild.get_channel(settings.get("review_channel_id") or 0) if guild else None
        if not isinstance(channel, discord.TextChannel):
            ACTIVE_APPLICATIONS.pop(key, None)
            await message.channel.send("Der Bewerbungs-Prüfkanal ist nicht eingerichtet. Bitte informiere das Team.")
            return
        app_id = "{}-{}-{}".format(guild_id, user_id, int(datetime.now(timezone.utc).timestamp()))
        row = {"id": app_id, "guild_id": guild_id, "user_id": user_id, "answers": session["answers"], "status": "pending", "created_at": datetime.now(timezone.utc).isoformat(), "reviewed_by": None}
        RECORDS["applications"][app_id] = row
        save_records()
        embed = discord.Embed(title="📨 Neue Bewerbung", description="**Bewerber:** <@{}>\n**Status:** ⏳ Offen".format(user_id), color=discord.Color.blurple(), timestamp=datetime.now(timezone.utc))
        for answer in row["answers"]:
            embed.add_field(name=safe_text(answer["question"], 256), value=safe_text(answer["answer"], 1000) or "—", inline=False)
        embed.set_footer(text="Bewerbungs-ID: " + app_id)
        try:
            ping_role_id = settings.get("ping_role_id")
            review_message = await channel.send(content=f"<@&{ping_role_id}>" if ping_role_id else None, embed=embed, view=ApplicationReviewView(self, app_id), allowed_mentions=discord.AllowedMentions(roles=True, users=False, everyone=False))
            row["review_message_id"] = review_message.id
            save_records()
        except (discord.Forbidden, discord.HTTPException):
            RECORDS["applications"].pop(app_id, None)
            save_records()
            ACTIVE_APPLICATIONS.pop(key, None)
            await message.channel.send("Die Bewerbung konnte nicht ans Team gesendet werden. Bitte informiere einen Administrator.")
            return
        ACTIVE_APPLICATIONS.pop(key, None)
        await message.channel.send("✅ Vielen Dank! Deine Bewerbung wurde eingereicht. Das Team prüft sie und meldet sich bei dir.")

    async def cog_load(self):
        for guild_id in CONFIG.get("guilds", {}):
            try:
                self.bot.add_view(ApplicationPanelView(self, int(guild_id)))
            except (TypeError, ValueError):
                pass
        for app_id, row in RECORDS.get("applications", {}).items():
            if row.get("status") == "pending":
                self.bot.add_view(ApplicationReviewView(self, app_id))

    @app_commands.command(name="bewerbung-panel", description="Erstellt ein anpassbares Bewerbungs-Panel")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(
        kanal="Kanal für das öffentliche Bewerbungs-Panel",
        review_kanal="Privater Kanal für eingehende Bewerbungen",
        log_kanal="Kanal für Entscheidungsprotokolle",
        titel="Titel des Panels",
        beschreibung="Beschreibung des Panels",
        frage1="Erste Formularfrage (max. 45 Zeichen)",
        frage2="Zweite Formularfrage (optional)",
        frage3="Dritte Formularfrage (optional)",
        frage4="Vierte Formularfrage (optional)",
        frage5="Fünfte Formularfrage (optional)",
        review_rolle="Optionale Rolle, die Bewerbungen prüfen darf",
        ping_rolle="Rolle, die bei jeder neuen Bewerbung angepingt wird",
    )
    async def application_panel(
        self, interaction: discord.Interaction, kanal: discord.TextChannel,
        review_kanal: discord.TextChannel, log_kanal: discord.TextChannel,
        titel: str = "🐺 IronWolf – Bewerbungen",
        beschreibung: str = "Du möchtest dich bewerben? Klicke auf den Button. Ich stelle dir die Fragen anschließend einzeln per DM.",
        frage1: str = None, frage2: str = None, frage3: str = None,
        frage4: str = None, frage5: str = None, review_rolle: discord.Role = None,
        ping_rolle: discord.Role = None
    ):
        if not interaction.guild:
            await interaction.response.send_message("Nur auf einem Server verfügbar.", ephemeral=True)
            return
        questions = [q.strip() for q in (frage1, frage2, frage3, frage4, frage5) if q and q.strip()]
        if not questions:
            questions = ["Wie lautet dein RP-Name?", "Wie lautet dein Roblox-Name?", "Wie alt bist du (OOC)?", "Welche Erfahrung bringst du mit?", "Warum möchtest du beitreten?"]
        if any(len(question) > 45 for question in questions):
            await interaction.response.send_message("Jede Frage darf höchstens 45 Zeichen lang sein. Bitte kürze die Fragen.", ephemeral=True)
            return
        gid = interaction.guild.id
        guild_settings(gid).update({
            "panel_channel_id": kanal.id, "review_channel_id": review_kanal.id,
            "log_channel_id": log_kanal.id, "review_role_id": review_rolle.id if review_rolle else None,
            "ping_role_id": ping_rolle.id if ping_rolle else None,
            "panel_title": titel[:256], "panel_description": beschreibung[:4000], "questions": questions
        })
        save_config()
        embed = discord.Embed(title=titel[:256], description=beschreibung[:4000], color=discord.Color.dark_blue())
        embed.set_footer(text="IronWolf RP • Bewerbungen")
        self.bot.add_view(ApplicationPanelView(self, gid))
        await kanal.send(embed=embed, view=ApplicationPanelView(self, gid))
        await interaction.response.send_message(
            "✅ Angepasstes Bewerbungs-Panel in {} erstellt. {} Fragen gespeichert. Prüfkanal: {} · Logkanal: {}".format(kanal.mention, len(questions), review_kanal.mention, log_kanal.mention),
            ephemeral=True
        )


    @app_commands.command(name="bewerbung-fragen", description="Legt bis zu 20 individuelle Bewerbungsfragen fest")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(
        frage1="1. Frage (leer lassen, um keine weitere Frage hinzuzufügen)",
        frage2="2. Frage (leer lassen, um keine weitere Frage hinzuzufügen)",
        frage3="3. Frage (leer lassen, um keine weitere Frage hinzuzufügen)",
        frage4="4. Frage (leer lassen, um keine weitere Frage hinzuzufügen)",
        frage5="5. Frage (leer lassen, um keine weitere Frage hinzuzufügen)",
        frage6="6. Frage (leer lassen, um keine weitere Frage hinzuzufügen)",
        frage7="7. Frage (leer lassen, um keine weitere Frage hinzuzufügen)",
        frage8="8. Frage (leer lassen, um keine weitere Frage hinzuzufügen)",
        frage9="9. Frage (leer lassen, um keine weitere Frage hinzuzufügen)",
        frage10="10. Frage (leer lassen, um keine weitere Frage hinzuzufügen)",
        frage11="11. Frage (leer lassen, um keine weitere Frage hinzuzufügen)",
        frage12="12. Frage (leer lassen, um keine weitere Frage hinzuzufügen)",
        frage13="13. Frage (leer lassen, um keine weitere Frage hinzuzufügen)",
        frage14="14. Frage (leer lassen, um keine weitere Frage hinzuzufügen)",
        frage15="15. Frage (leer lassen, um keine weitere Frage hinzuzufügen)",
        frage16="16. Frage (leer lassen, um keine weitere Frage hinzuzufügen)",
        frage17="17. Frage (leer lassen, um keine weitere Frage hinzuzufügen)",
        frage18="18. Frage (leer lassen, um keine weitere Frage hinzuzufügen)",
        frage19="19. Frage (leer lassen, um keine weitere Frage hinzuzufügen)",
        frage20="20. Frage (leer lassen, um keine weitere Frage hinzuzufügen)"
    )
    async def application_questions(
        self, interaction: discord.Interaction,
        frage1: str = None,
        frage2: str = None,
        frage3: str = None,
        frage4: str = None,
        frage5: str = None,
        frage6: str = None,
        frage7: str = None,
        frage8: str = None,
        frage9: str = None,
        frage10: str = None,
        frage11: str = None,
        frage12: str = None,
        frage13: str = None,
        frage14: str = None,
        frage15: str = None,
        frage16: str = None,
        frage17: str = None,
        frage18: str = None,
        frage19: str = None,
        frage20: str = None
    ):
        if not interaction.guild:
            await interaction.response.send_message("Nur auf einem Server verfügbar.", ephemeral=True)
            return
        questions = [q.strip() for q in (frage1, frage2, frage3, frage4, frage5, frage6, frage7, frage8, frage9, frage10, frage11, frage12, frage13, frage14, frage15, frage16, frage17, frage18, frage19, frage20) if q and q.strip()]
        if not questions:
            await interaction.response.send_message("Gib mindestens eine Frage an.", ephemeral=True)
            return
        if any(len(q) > 45 for q in questions):
            await interaction.response.send_message("Jede Frage darf höchstens 45 Zeichen lang sein.", ephemeral=True)
            return
        guild_settings(interaction.guild.id)["questions"] = questions
        save_config()
        await interaction.response.send_message(
            "✅ Gespeichert: {} Bewerbungsfragen. Die Fragen werden beim nächsten Klick auf „Jetzt bewerben“ verwendet. Du beantwortest die Fragen einzeln per DM.".format(len(questions)),
            ephemeral=True
        )



# END INLINE APPLICATIONS SYSTEM


@bot.tree.command(name="ironwolf-profil", description="Ändert IronWolfs Profilbild und/oder Banner")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(
    server_id="Zur Bestätigung: die ID dieses Discord-Servers",
    profilbild_url="Direkte HTTPS-Bild-URL für das neue Profilbild",
    banner_url="Direkte HTTPS-Bild-URL für den neuen Banner",
)
async def ironwolf_profile(
    interaction: discord.Interaction,
    server_id: str,
    profilbild_url: str = None,
    banner_url: str = None,
):
    if interaction.guild is None or not server_id.isdigit() or int(server_id) != interaction.guild.id:
        await interaction.response.send_message(
            "❌ Die eingegebene Server-ID stimmt nicht mit diesem Server überein. Es wurde nichts geändert.",
            ephemeral=True,
        )
        return

    # Use the same allowlist as the nickname guard. Only listed guilds may
    # change the bot's global avatar/banner through this command.
    raw_ids = os.getenv("IRONWOLF_NICKNAME_ALLOWED_GUILD_IDS", "")
    allowed_guild_ids = {
        int(item.strip())
        for item in raw_ids.split(",")
        if item.strip().isdigit()
    }
    if interaction.guild.id not in allowed_guild_ids:
        await interaction.response.send_message(
            "⛔ Dieser Server ist nicht für Profilbild- oder Banneränderungen freigegeben. "
            "Bitte wende dich an den Bot-Betreiber.",
            ephemeral=True,
        )
        return
    if not profilbild_url and not banner_url:
        await interaction.response.send_message(
            "Gib mindestens eine Profilbild-URL oder Banner-URL an.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True, thinking=True)

    import base64
    import requests

    async def image_data(url):
        if not url.lower().startswith("https://"):
            raise ValueError("Bitte nur direkte HTTPS-Bild-URLs verwenden.")
        def download():
            response = requests.get(url, timeout=15, stream=True, allow_redirects=True)
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "").split(";")[0].lower()
            if content_type not in {"image/png", "image/jpeg", "image/gif", "image/webp"}:
                raise ValueError("Die URL muss direkt auf eine PNG-, JPEG-, GIF- oder WebP-Bilddatei zeigen.")
            chunks = []
            total = 0
            for chunk in response.iter_content(65536):
                total += len(chunk)
                if total > 8 * 1024 * 1024:
                    raise ValueError("Das Bild darf höchstens 8 MB groß sein.")
                chunks.append(chunk)
            return content_type, b"".join(chunks)
        mime, raw = await asyncio.to_thread(download)
        return "data:{};base64,{}".format(mime, base64.b64encode(raw).decode("ascii"))

    payload = {}
    try:
        if profilbild_url:
            payload["avatar"] = await image_data(profilbild_url)
        if banner_url:
            payload["banner"] = await image_data(banner_url)

        def update_profile():
            return requests.patch(
                "https://discord.com/api/v10/users/@me",
                headers={"Authorization": "Bot " + TOKEN, "Content-Type": "application/json"},
                json=payload,
                timeout=20,
            )
        response = await asyncio.to_thread(update_profile)
        if response.status_code >= 400:
            detail = " ".join(response.text.split())[:700]
            await interaction.followup.send(
                "❌ Discord hat die Profiländerung abgelehnt (HTTP {}). {}\n"
                "Hinweis: Discord unterstützt nicht bei jedem Bot-Konto das Ändern des Banners über die API.".format(response.status_code, detail),
                ephemeral=True,
            )
            return
        changed = []
        if profilbild_url:
            changed.append("Profilbild")
        if banner_url:
            changed.append("Banner")
        await interaction.followup.send(
            "✅ Discord hat die Änderung für {} angenommen.".format(" und ".join(changed)),
            ephemeral=True,
        )
    except (ValueError, requests.RequestException) as exc:
        await interaction.followup.send("❌ Profiländerung fehlgeschlagen: {}".format(str(exc)[:700]), ephemeral=True)

@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    """Revert IronWolf's server nickname unless that guild ID is explicitly allowlisted."""
    if bot.user is None or after.id != bot.user.id or before.nick == after.nick:
        return

    # Comma-separated Discord server IDs in Railway:
    # IRONWOLF_NICKNAME_ALLOWED_GUILD_IDS=123456789012345678,234567890123456789
    raw_ids = os.getenv("IRONWOLF_NICKNAME_ALLOWED_GUILD_IDS", "")
    allowed_guild_ids = set()
    for item in raw_ids.split(","):
        item = item.strip()
        if item.isdigit():
            allowed_guild_ids.add(int(item))

    if after.guild.id in allowed_guild_ids:
        return

    me = after.guild.me
    if me is None or not me.guild_permissions.change_nickname:
        print(
            f"[nickname-guard] Cannot restore IronWolf nickname in guild "
            f"{after.guild.id}: bot lacks Change Nickname permission."
        )
        return

    try:
        await after.edit(
            nick=before.nick,
            reason="IronWolf nickname guard: server ID is not allowlisted",
        )
        print(f"[nickname-guard] Restored IronWolf nickname in guild {after.guild.id}.")
    except (discord.Forbidden, discord.HTTPException) as exc:
        print(
            f"[nickname-guard] Failed to restore nickname in guild "
            f"{after.guild.id}: {type(exc).__name__}: {exc}"
        )




# ---------------------- IRONWOLF FEATURES (INLINE) ----------------------
AKTEN = data_path("akten.json")
EVENTS = data_path("ironwolf_events.json")
SECURITY = data_path("security_state.json")
SERVER_LOGS = data_path("server_logs.json")

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
        if cfg.get("anti_raid_enabled", False) and len(recent) >= threshold:
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

class ServerLogs(commands.Cog):
    """Konfigurierbare Aktivitätslogs mit betroffenen Objekten und Audit-Log-Akteuren."""

    def __init__(self, bot):
        self.bot = bot

    async def log(self, guild, title, description, color=discord.Color.blurple(), actor=None, target=None):
        db = read(SERVER_LOGS, {})
        cfg = db.get(str(guild.id), {})
        channel = guild.get_channel(cfg.get("channel_id")) if cfg.get("channel_id") else None
        if not isinstance(channel, discord.TextChannel):
            return
        embed = discord.Embed(
            title=title,
            description=description[:4000],
            color=color,
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Server", value=f"{guild.name} (ID: {guild.id})", inline=False)
        if actor is not None:
            actor_text = f"{actor} (ID: {actor.id})"
            if isinstance(actor, discord.Member):
                actor_text = f"{actor.mention} · {actor} (ID: {actor.id})"
            embed.add_field(name="Ausgeführt von", value=actor_text[:1024], inline=True)
        else:
            embed.add_field(name="Ausgeführt von", value="Nicht eindeutig ermittelbar", inline=True)
        if target is not None:
            target_text = str(target)
            if hasattr(target, "id"):
                target_text += f" (ID: {target.id})"
            embed.add_field(name="Betroffen", value=target_text[:1024], inline=True)
        embed.set_footer(text="IronWolf Server-Logs")
        try:
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except (discord.Forbidden, discord.HTTPException):
            pass

    async def audit_actor(self, guild, action, target_id=None, *, channel_id=None, max_age=20):
        """Return a likely audit-log executor only when action/target/time match."""
        try:
            async for entry in guild.audit_logs(limit=8, action=action):
                if (datetime.now(timezone.utc) - entry.created_at).total_seconds() > max_age:
                    continue
                if target_id is not None and getattr(entry.target, "id", None) != target_id:
                    continue
                if channel_id is not None:
                    extra_channel = getattr(entry.extra, "channel", None)
                    if getattr(extra_channel, "id", None) != channel_id:
                        continue
                return entry.user
        except (discord.Forbidden, discord.HTTPException, AttributeError):
            return None
        return None

    @app_commands.command(name="serverlogs", description="Legt den Kanal für Server-Aktivitätslogs fest")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    async def serverlogs(self, interaction: discord.Interaction, kanal: discord.TextChannel):
        db = read(SERVER_LOGS, {})
        db[str(interaction.guild_id)] = {"channel_id": kanal.id}
        write(SERVER_LOGS, db)
        await interaction.response.send_message(
            f"Server-Logs werden ab jetzt in {kanal.mention} gespeichert. Die Einstellung bleibt nach Neustarts erhalten.",
            ephemeral=True,
        )
        await self.log(
            interaction.guild, "Server-Logs aktiviert",
            f"Log-Kanal: {kanal.mention} · ID {kanal.id}",
            discord.Color.green(), actor=interaction.user, target=kanal,
        )

    @app_commands.command(name="serverlogs-aus", description="Deaktiviert die Server-Aktivitätslogs")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    async def serverlogs_aus(self, interaction: discord.Interaction):
        db = read(SERVER_LOGS, {})
        old_cfg = db.get(str(interaction.guild_id), {})
        old_channel = interaction.guild.get_channel(old_cfg.get("channel_id")) if old_cfg.get("channel_id") else None
        # Write the disabled setting first, then report directly to the previous log channel.
        db.pop(str(interaction.guild_id), None)
        write(SERVER_LOGS, db)
        await interaction.response.send_message("Server-Logs wurden deaktiviert.", ephemeral=True)
        if isinstance(old_channel, discord.TextChannel):
            embed = discord.Embed(
                title="Server-Logs deaktiviert",
                description=f"Deaktiviert von {interaction.user.mention} · ID {interaction.user.id}",
                color=discord.Color.orange(),
                timestamp=datetime.now(timezone.utc),
            )
            embed.add_field(name="Server", value=f"{interaction.guild.name} (ID: {interaction.guild.id})", inline=False)
            embed.add_field(name="Betroffen", value=f"Log-Konfiguration für {old_channel.mention} (ID: {old_channel.id})", inline=False)
            try:
                await old_channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            except (discord.Forbidden, discord.HTTPException):
                pass

    @commands.Cog.listener()
    async def on_member_join(self, member):
        await self.log(member.guild, "Mitglied beigetreten", f"Mitglied: {member.mention}\nName: {member}\nBenutzer-ID: {member.id}\nAccount erstellt: <t:{int(member.created_at.timestamp())}:F>", discord.Color.green(), target=member)

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        actor = await self.audit_actor(member.guild, discord.AuditLogAction.kick, member.id)
        title = "Mitglied entfernt (Kick möglich)" if actor else "Mitglied verlassen"
        details = f"Mitglied: {member}\nBenutzer-ID: {member.id}\nLetzter bekannter Nickname: {member.nick or 'Keiner'}"
        await self.log(member.guild, title, details, discord.Color.red(), actor=actor, target=member)

    @commands.Cog.listener()
    async def on_message_delete(self, message):
        if message.guild is None or (message.author and message.author.bot):
            return
        author = f"{message.author} · ID {message.author.id}" if message.author else "Unbekannt"
        content = discord.utils.escape_mentions(message.content or "[Text nicht im Cache verfügbar]")
        actor = None
        if message.author:
            actor = await self.audit_actor(
                message.guild, discord.AuditLogAction.message_delete,
                message.author.id, channel_id=message.channel.id,
            )
        await self.log(
            message.guild, "Nachricht gelöscht",
            f"Kanal: {message.channel.mention} · ID {message.channel.id}\nNachrichtenautor: {author}\n"
            f"Nachrichten-ID: {message.id}\nInhalt:\n{content}",
            discord.Color.red(), actor=actor, target=f"Nachricht von {author} in #{message.channel.name}",
        )

    @commands.Cog.listener()
    async def on_bulk_message_delete(self, messages):
        messages = list(messages)
        if not messages:
            return
        guild = messages[0].guild
        if guild is None:
            return
        channel = messages[0].channel
        actor = await self.audit_actor(guild, discord.AuditLogAction.message_bulk_delete, channel_id=channel.id)
        await self.log(
            guild, "Mehrere Nachrichten gelöscht",
            f"Kanal: {channel.mention} · ID {channel.id}\nAnzahl Nachrichten: {len(messages)}",
            discord.Color.red(), actor=actor, target=f"{len(messages)} Nachrichten in #{channel.name}",
        )

    @commands.Cog.listener()
    async def on_message_edit(self, before, after):
        if after.guild is None or (after.author and after.author.bot) or before.content == after.content:
            return
        old = discord.utils.escape_mentions(before.content or "[leer/nicht verfügbar]")
        new = discord.utils.escape_mentions(after.content or "[leer/nicht verfügbar]")
        await self.log(
            after.guild, "Nachricht bearbeitet",
            f"Kanal: {after.channel.mention} · ID {after.channel.id}\nAutor: {after.author} · ID {after.author.id}\n"
            f"Nachrichten-ID: {after.id}\nVorher: {old}\nNachher: {new}",
            actor=after.author, target=f"Nachricht {after.id} in #{after.channel.name}",
        )

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        if before.nick != after.nick:
            actor = await self.audit_actor(after.guild, discord.AuditLogAction.member_update, after.id)
            await self.log(
                after.guild, "Nickname geändert",
                f"Mitglied: {after.mention} · ID {after.id}\nVorher: {before.nick or 'Kein Nickname'}\nNachher: {after.nick or 'Kein Nickname'}",
                actor=actor, target=after,
            )
        added = [role for role in after.roles if role not in before.roles and not role.is_default()]
        removed = [role for role in before.roles if role not in after.roles and not role.is_default()]
        if added or removed:
            changes = ["+ Rolle hinzugefügt: " + role.name + f" (ID: {role.id})" for role in added]
            changes += ["- Rolle entfernt: " + role.name + f" (ID: {role.id})" for role in removed]
            actor = await self.audit_actor(after.guild, discord.AuditLogAction.member_role_update, after.id)
            await self.log(after.guild, "Mitgliedsrollen geändert", f"Mitglied: {after.mention} · ID {after.id}\n" + "\n".join(changes), actor=actor, target=after)

    @commands.Cog.listener()
    async def on_member_ban(self, guild, user):
        actor = await self.audit_actor(guild, discord.AuditLogAction.ban, user.id)
        await self.log(guild, "Mitglied gebannt", f"Benutzer: {user} · ID {user.id}", discord.Color.red(), actor=actor, target=user)

    @commands.Cog.listener()
    async def on_member_unban(self, guild, user):
        actor = await self.audit_actor(guild, discord.AuditLogAction.unban, user.id)
        await self.log(guild, "Mitglied entbannt", f"Benutzer: {user} · ID {user.id}", discord.Color.green(), actor=actor, target=user)

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel):
        actor = await self.audit_actor(channel.guild, discord.AuditLogAction.channel_create, channel.id)
        await self.log(channel.guild, "Kanal erstellt", f"Name: #{channel.name}\nTyp: {type(channel).__name__}\nKategorie: {getattr(getattr(channel, 'category', None), 'name', 'Keine')}\nKanal-ID: {channel.id}", discord.Color.green(), actor=actor, target=channel)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        actor = await self.audit_actor(channel.guild, discord.AuditLogAction.channel_delete, channel.id)
        await self.log(channel.guild, "Kanal gelöscht", f"Name: {channel.name}\nTyp: {type(channel).__name__}\nKanal-ID: {channel.id}", discord.Color.red(), actor=actor, target=f"#{channel.name}")

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before, after):
        changes = []
        if before.name != after.name:
            changes.append(f"Name: {before.name} → {after.name}")
        if getattr(before, "category_id", None) != getattr(after, "category_id", None):
            changes.append(f"Kategorie: {getattr(getattr(before, 'category', None), 'name', 'Keine')} → {getattr(getattr(after, 'category', None), 'name', 'Keine')}")
        if getattr(before, "topic", None) != getattr(after, "topic", None):
            changes.append(f"Thema: {getattr(before, 'topic', None) or 'Keins'} → {getattr(after, 'topic', None) or 'Keins'}")
        if changes:
            actor = await self.audit_actor(after.guild, discord.AuditLogAction.channel_update, after.id)
            await self.log(after.guild, "Kanal geändert", "\n".join(changes) + f"\nKanal-ID: {after.id}", actor=actor, target=after)

    @commands.Cog.listener()
    async def on_guild_role_create(self, role):
        actor = await self.audit_actor(role.guild, discord.AuditLogAction.role_create, role.id)
        await self.log(role.guild, "Rolle erstellt", f"Name: {role.name}\nFarbe: {role.colour}\nBerechtigungen: {', '.join(p for p, enabled in role.permissions if enabled) or 'Keine'}\nRollen-ID: {role.id}", discord.Color.green(), actor=actor, target=role)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role):
        actor = await self.audit_actor(role.guild, discord.AuditLogAction.role_delete, role.id)
        await self.log(role.guild, "Rolle gelöscht", f"Name: {role.name}\nRollen-ID: {role.id}", discord.Color.red(), actor=actor, target=role.name)

    @commands.Cog.listener()
    async def on_guild_role_update(self, before, after):
        changes = []
        if before.name != after.name:
            changes.append(f"Name: {before.name} → {after.name}")
        if before.colour != after.colour:
            changes.append(f"Farbe: {before.colour} → {after.colour}")
        if before.permissions != after.permissions:
            changes.append("Berechtigungen wurden geändert")
        if before.hoist != after.hoist:
            changes.append(f"Separat anzeigen: {before.hoist} → {after.hoist}")
        if before.mentionable != after.mentionable:
            changes.append(f"Erwähnbar: {before.mentionable} → {after.mentionable}")
        if changes:
            actor = await self.audit_actor(after.guild, discord.AuditLogAction.role_update, after.id)
            await self.log(after.guild, "Rolle geändert", "\n".join(changes) + f"\nRollen-ID: {after.id}", actor=actor, target=after)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if before.channel == after.channel:
            return
        if before.channel is None and after.channel is not None:
            title = "Voice-Kanal betreten"
            details = f"Mitglied: {member.mention} · ID {member.id}\nKanal: {after.channel.name} · ID {after.channel.id}"
        elif before.channel is not None and after.channel is None:
            title = "Voice-Kanal verlassen"
            details = f"Mitglied: {member.mention} · ID {member.id}\nKanal: {before.channel.name} · ID {before.channel.id}"
        else:
            title = "Voice-Kanal gewechselt"
            details = f"Mitglied: {member.mention} · ID {member.id}\nVon: {before.channel.name} · ID {before.channel.id}\nNach: {after.channel.name} · ID {after.channel.id}"
        await self.log(member.guild, title, details, actor=member, target=member)



# ------------------------- SERVER BUILD TEMPLATES -------------------------
# These commands create missing roles/categories/channels only. Existing server
# content is never deleted or renamed, so they are safe to run more than once.

async def _ensure_server_role(guild, name, color, permissions=None):
    role = discord.utils.get(guild.roles, name=name)
    if role is not None:
        return role, False
    role = await guild.create_role(
        name=name,
        colour=color,
        permissions=permissions or discord.Permissions.none(),
        reason="Server template setup command",
    )
    return role, True


def _normalize_channel_overwrites(overwrites):
    """Return a plain dict accepted by discord.py channel creation methods."""
    if overwrites is None:
        return {}
    if not isinstance(overwrites, dict):
        raise TypeError(
            f"Channel overwrites must be a dict, got {type(overwrites).__name__}"
        )
    return dict(overwrites)


async def _ensure_server_category(guild, name, overwrites=None):
    category = discord.utils.get(guild.categories, name=name)
    if category is not None:
        return category, False
    category = await guild.create_category(
        name,
        overwrites=_normalize_channel_overwrites(overwrites),
        reason="Server template setup command",
    )
    return category, True


async def _ensure_server_text(guild, category, name, topic=None, overwrites=None):
    channel = discord.utils.get(category.text_channels, name=name)
    if channel is not None:
        return channel, False
    channel = await guild.create_text_channel(
        name,
        category=category,
        topic=topic,
        overwrites=_normalize_channel_overwrites(overwrites),
        reason="Server template setup command",
    )
    return channel, True


async def _ensure_server_voice(guild, category, name, overwrites=None):
    channel = discord.utils.get(category.voice_channels, name=name)
    if channel is not None:
        return channel, False
    channel = await guild.create_voice_channel(
        name,
        category=category,
        overwrites=_normalize_channel_overwrites(overwrites),
        reason="Server template setup command",
    )
    return channel, True


async def _build_server_template(ctx, role_specs, channel_specs, template_title):
    guild = ctx.guild
    if guild is None:
        await ctx.reply("Dieser Befehl funktioniert nur auf einem Discord-Server.", mention_author=False)
        return

    status = await ctx.reply(
        f"🏗️ **{template_title}** wird eingerichtet. Vorhandene Kanäle und Rollen bleiben erhalten …",
        mention_author=False,
    )
    created_roles = 0
    created_categories = 0
    created_channels = 0
    role_map = {}
    try:
        for name, color, permissions in role_specs:
            role, created = await _ensure_server_role(guild, name, color, permissions)
            role_map[name] = role
            created_roles += int(created)

        everyone = guild.default_role
        admin_role = role_map.get("Administrator")
        moderator_role = role_map.get("Moderator")
        staff_overwrites = {
            everyone: discord.PermissionOverwrite(view_channel=False),
        }
        if admin_role:
            staff_overwrites[admin_role] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True,
                manage_channels=True, manage_messages=True
            )
        if moderator_role:
            staff_overwrites[moderator_role] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True,
                manage_messages=True
            )

        for category_name, channels in channel_specs:
            is_staff = category_name in {"🛡️ TEAM", "🛡️ STAFF", "🔒 TEAM-BEREICH"}
            category, created = await _ensure_server_category(
                guild, category_name, staff_overwrites if is_staff else None
            )
            created_categories += int(created)
            for channel_type, channel_name, topic in channels:
                if channel_type == "voice":
                    _, made = await _ensure_server_voice(
                        guild, category, channel_name,
                        staff_overwrites if is_staff else None
                    )
                else:
                    _, made = await _ensure_server_text(
                        guild, category, channel_name, topic,
                        staff_overwrites if is_staff else None
                    )
                created_channels += int(made)

        await status.edit(content=(
            f"✅ **{template_title}** fertig!\\n"
            f"Neu erstellt: **{created_roles} Rollen**, **{created_categories} Kategorien**, "
            f"**{created_channels} Kanäle**.\\n"
            "Bereits vorhandene Inhalte wurden beibehalten. Prüfe bitte Rollen-Hierarchie "
            "und Berechtigungen und weise Teamrollen den richtigen Personen zu.\\n\\n"
            "**Sinnvolle nächste Schritte:**\\n"
            "• `/bewerbung-panel` – Bewerbungen für Teammitglieder einrichten\\n"
            "• `/security-konfigurieren` – Sicherheits- und Raid-Logkanal festlegen\\n"
            "• `/serverlogs` – Aktivitätslogs in einem Kanal aktivieren\\n"
            "• `/event-erstellen` – ein Serverevent veröffentlichen\\n"
            "• `/bewerbung-fragen` – Bewerbungsfragen für das Team anpassen\\n"
            "• Danach: Kanalrechte prüfen, Teamrollen vergeben und Regeln/Willkommensnachricht anpassen."
        ))
    except discord.Forbidden:
        await status.edit(content=(
            "❌ Discord hat die Einrichtung abgelehnt. Gib dem Bot die Berechtigungen "
            "**Rollen verwalten** und **Kanäle verwalten** und stelle sicher, dass seine "
            "höchste Rolle über den Rollen liegt, die er verwalten soll."
        ))
    except discord.HTTPException as exc:
        await status.edit(content=f"⚠️ Discord konnte die Einrichtung nicht vollständig abschließen: {exc}. Bereits erstellte Elemente bleiben bestehen; du kannst den Befehl nach Behebung erneut ausführen.")


@bot.command(name="deleteserver")
@commands.guild_only()
@commands.has_guild_permissions(administrator=True)
async def deleteserver(ctx, bestaetigung: str = ""):
    """Deletes every channel and category after an explicit confirmation."""
    if bestaetigung.casefold() != "bestätigen":
        await ctx.reply(
            "⚠️ **Achtung: Dieser Befehl löscht alle Kanäle und Kategorien dieses Servers.** "
            "Rollen und Mitglieder bleiben bestehen. Wenn du sicher bist, tippe "
            "`!deleteserver BESTÄTIGEN`. Der Vorgang kann nicht rückgängig gemacht werden.",
            mention_author=False,
        )
        return

    guild = ctx.guild
    # Notify the administrator privately because the command's channel will be deleted too.
    try:
        await ctx.author.send(
            f"🧨 Der Löschvorgang für **{guild.name}** wurde gestartet. "
            "Alle Serverkanäle und Kategorien werden entfernt."
        )
    except discord.HTTPException:
        pass

    deleted = 0
    failed = 0
    # Delete regular channels first, then categories to avoid category dependencies.
    channels = sorted(
        list(guild.channels),
        key=lambda channel: isinstance(channel, discord.CategoryChannel),
    )
    for channel in channels:
        try:
            await channel.delete(reason=f"Server cleanup requested by {ctx.author} ({ctx.author.id})")
            deleted += 1
        except (discord.Forbidden, discord.HTTPException):
            failed += 1

    try:
        await ctx.author.send(
            f"✅ Löschvorgang für **{guild.name}** beendet. "
            f"Gelöscht: **{deleted}** Kanäle/Kategorien. Fehlgeschlagen: **{failed}**."
        )
    except discord.HTTPException:
        pass


@deleteserver.error
async def deleteserver_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.reply("❌ Nur Server-Administratoren dürfen diesen Befehl verwenden.", mention_author=False)
    elif isinstance(error, commands.NoPrivateMessage):
        await ctx.reply("Dieser Befehl funktioniert nur auf einem Discord-Server.", mention_author=False)
    else:
        await ctx.reply(f"❌ Fehler bei !deleteserver: {error}", mention_author=False)


@bot.command(name="serverbau")
@commands.guild_only()
@commands.has_guild_permissions(manage_guild=True)
async def serverbau(ctx):
    """Build a general-purpose Discord community server structure."""
    roles = [
        ("Administrator", discord.Colour.from_rgb(220, 55, 55), discord.Permissions(administrator=True)),
        ("Moderator", discord.Colour.from_rgb(230, 125, 45), discord.Permissions(moderate_members=True, manage_messages=True, kick_members=True, ban_members=True)),
        ("Support", discord.Colour.from_rgb(70, 160, 220), discord.Permissions.none()),
        ("Mitglied", discord.Colour.from_rgb(80, 190, 120), discord.Permissions.none()),
        ("Neuling", discord.Colour.from_rgb(160, 160, 160), discord.Permissions.none()),
        ("Bot", discord.Colour.from_rgb(130, 100, 200), discord.Permissions.none()),
    ]
    channels = [
        ("📌 START HIER", [
            ("text", "willkommen", "Willkommen auf dem Server! Lies bitte die Regeln."),
            ("text", "regeln", "Die Serverregeln und Verhaltensrichtlinien."),
            ("text", "ankündigungen", "Offizielle Neuigkeiten und Ankündigungen."),
            ("text", "rollen-auswahl", "Informationen zu Rollen und Benachrichtigungen."),
        ]),
        ("💬 COMMUNITY", [
            ("text", "allgemein", "Allgemeiner Chat für alle Mitglieder."),
            ("text", "vorstellungen", "Stell dich der Community vor."),
            ("text", "medien", "Teile Bilder, Videos und andere Medien."),
            ("text", "fragen-und-hilfe", "Fragen stellen und Hilfe bekommen."),
            ("text", "vorschlaege", "Ideen und Verbesserungsvorschläge."),
            ("voice", "Allgemeiner Voice", None),
            ("voice", "Gaming Voice", None),
        ]),
        ("🎉 AKTIVITÄTEN", [
            ("text", "events", "Community-Events und Termine."),
            ("text", "umfragen", "Abstimmungen und Community-Entscheidungen."),
            ("voice", "Event Voice", None),
        ]),
        ("🛡️ TEAM", [
            ("text", "team-chat", "Interner Chat für das Serverteam."),
            ("text", "mod-log", "Moderationsnotizen und interne Meldungen."),
            ("text", "reports", "Meldungen zur Bearbeitung durch das Team."),
            ("voice", "Team Voice", None),
        ]),
    ]
    await _build_server_template(ctx, roles, channels, "Normaler Discord-Server")


@bot.command(name="serverRPbau")
@commands.guild_only()
@commands.has_guild_permissions(manage_guild=True)
async def server_rp_bau(ctx):
    """Build a roleplay Discord server structure."""
    roles = [
        ("Administrator", discord.Colour.from_rgb(220, 55, 55), discord.Permissions(administrator=True)),
        ("RP-Leitung", discord.Colour.from_rgb(150, 70, 200), discord.Permissions.none()),
        ("Moderator", discord.Colour.from_rgb(230, 125, 45), discord.Permissions(moderate_members=True, manage_messages=True, kick_members=True, ban_members=True)),
        ("Support", discord.Colour.from_rgb(70, 160, 220), discord.Permissions.none()),
        ("Fraktionsleitung", discord.Colour.from_rgb(190, 155, 70), discord.Permissions.none()),
        ("RP-Spieler", discord.Colour.from_rgb(80, 190, 120), discord.Permissions.none()),
        ("Charakter in Prüfung", discord.Colour.from_rgb(160, 160, 160), discord.Permissions.none()),
        ("Zuschauer", discord.Colour.from_rgb(110, 110, 110), discord.Permissions.none()),
        ("Bot", discord.Colour.from_rgb(130, 100, 200), discord.Permissions.none()),
    ]
    channels = [
        ("📌 INFORMATIONEN", [
            ("text", "willkommen", "Willkommen im RP. Lies zuerst die Informationen."),
            ("text", "regeln", "Allgemeine Discord- und RP-Regeln."),
            ("text", "rp-regeln", "Regeln für Roleplay, Fairplay und Konfliktszenen."),
            ("text", "ankündigungen", "Offizielle Ankündigungen der RP-Leitung."),
            ("text", "server-lore", "Hintergrundgeschichte und Welt des Roleplays."),
            ("text", "fraktionen", "Vorstellung der verfügbaren Fraktionen."),
            ("text", "faq-und-hilfe", "Häufige Fragen und Hilfe für neue Spieler."),
        ]),
        ("🪪 CHARAKTERE", [
            ("text", "charakter-erstellung", "Vorlage und Anleitung für neue Charaktere."),
            ("text", "charakter-einreichung", "Reiche hier deinen Charakter zur Prüfung ein."),
            ("text", "charakter-freigaben", "Status und Freigaben durch das Team."),
            ("text", "gesuche", "Suche nach Kontakten, Gruppen und Geschichten."),
        ]),
        ("🎭 IN CHARACTER", [
            ("text", "rp-hauptchat", "In-Character-Kommunikation im Hauptbereich."),
            ("text", "rp-szenen", "Starte und spiele ausgeschriebene RP-Szenen."),
            ("text", "rp-ereignisse", "Laufende und angekündigte In-Character-Ereignisse."),
            ("text", "rp-anzeigen", "In-Character-Anzeigen, Angebote und Gesuche."),
            ("text", "rp-berichte", "Berichte und Zusammenfassungen aus der Spielwelt."),
        ]),
        ("💬 OUT OF CHARACTER", [
            ("text", "ooc-chat", "Außerhalb des Charakters kommunizieren."),
            ("text", "community-chat", "Allgemeiner Austausch der Community."),
            ("text", "screenshots-und-medien", "Screenshots und Medien aus dem RP."),
            ("text", "feedback-und-ideen", "Feedback und Ideen für das RP."),
            ("voice", "OOC Voice", None),
        ]),
        ("🏴 FRAKTIONEN", [
            ("text", "fraktions-gesuche", "Suche oder gründe eine Fraktion."),
            ("text", "fraktions-planung", "Planung für Fraktionsaktivitäten."),
            ("voice", "Fraktions Voice", None),
        ]),
        ("📅 EVENTS", [
            ("text", "event-kalender", "Kommende RP-Events und Termine."),
            ("text", "event-anmeldungen", "Anmeldung zu RP-Events."),
            ("text", "event-besprechung", "Absprachen zu Events."),
        ]),
        ("🔒 TEAM-BEREICH", [
            ("text", "team-chat", "Interne Teamkommunikation."),
            ("text", "charakter-pruefung", "Interne Prüfung eingereichter Charaktere."),
            ("text", "spieler-meldungen", "Meldungen und Beschwerden zur Bearbeitung."),
            ("text", "team-protokoll", "Interne Notizen und Entscheidungen."),
            ("voice", "Team Besprechung", None),
        ]),
    ]
    await _build_server_template(ctx, roles, channels, "Roleplay-Discord-Server")


@serverbau.error
@server_rp_bau.error
async def server_template_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.reply("❌ Du brauchst die Berechtigung **Server verwalten**, um diesen Befehl zu benutzen.", mention_author=False)
    elif isinstance(error, commands.NoPrivateMessage):
        await ctx.reply("Dieser Befehl funktioniert nur auf einem Discord-Server.", mention_author=False)
    else:
        await ctx.reply(f"❌ Fehler beim Serverbau: {error}", mention_author=False)


@bot.tree.command(name="module-manager", description="Öffnet das IronWolf Control Center zur Modulverwaltung")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
async def module_manager_command(interaction: discord.Interaction):
    dashboard_url = os.getenv("DASHBOARD_PUBLIC_URL", "").strip().rstrip("/")
    if not dashboard_url:
        await interaction.response.send_message(
            "Das Web-Dashboard ist noch nicht vollständig eingerichtet. Bitte setze DASHBOARD_PUBLIC_URL sowie die Discord-OAuth2-Variablen in Railway.",
            ephemeral=True,
        )
        return
    await interaction.response.send_message(
        f"🐺 **IronWolf Control Center**\nÖffne das Dashboard, um Module und Befehle für diesen Server zu verwalten: {dashboard_url}",
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


@bot.tree.command(name="module-status", description="Zeigt den Status der IronWolf-Cog-Module für diesen Server")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
async def module_status_command(interaction: discord.Interaction):
    disabled = set(config.get(str(interaction.guild_id), {}).get("disabled_modules", []))
    names = set()
    for cog_name in bot.cogs:
        names.add(cog_name)
    if not names:
        await interaction.response.send_message("Aktuell sind keine Cog-Module registriert.", ephemeral=True)
        return
    lines = [
        f"{'🔴 AUS' if name in disabled else '🟢 AN'} · **{name}**"
        for name in sorted(names, key=str.lower)
    ]
    embed = discord.Embed(
        title="🐺 IronWolf · Modulstatus",
        description="\n".join(lines)[:4000],
        color=discord.Color.blurple(),
    )
    embed.set_footer(text="Modulverwaltung: /module-manager")
    await interaction.response.send_message(embed=embed, ephemeral=True)



# Seasonal Halloween, Christmas and Advent commands removed.


@bot.event
async def on_ready():
    migrate_all_guild_configs()
    global _automation_worker_task
    global _birthday_worker_task
    record_module_changes()
    if _automation_worker_task is None or _automation_worker_task.done():
        _automation_worker_task = asyncio.create_task(premium_automation_worker(), name="premium-automation-worker")
    if _birthday_worker_task is None or _birthday_worker_task.done():
        _birthday_worker_task = asyncio.create_task(birthday_worker(), name="birthday-worker")
    # Start tracking members already in voice after a bot start/reconnect.
    # Only seed sessions that are not already tracked to avoid resetting time
    # when Discord reconnects the bot.
    now = datetime.now(timezone.utc)
    for guild in bot.guilds:
        try:
            invites = await guild.invites()
            _invite_cache[guild.id] = {
                invite.code: (invite.uses or 0) for invite in invites
            }
        except (discord.Forbidden, discord.HTTPException) as exc:
            print(f"[INVITE TRACKER] Could not initialize invite cache for guild {guild.id}: {type(exc).__name__}: {exc}")
        for member in guild.members:
            if not member.bot and member.voice and member.voice.channel:
                _active_voice_sessions.setdefault((guild.id, member.id), now)

    # Load the personal ID card command once before slash-command sync.
    if not getattr(bot, "_ausweis_loaded", False):
        try:
            await bot.load_extension("ausweis")
            bot._ausweis_loaded = True
            print("Personal ID card extension loaded.")
        except Exception as exc:
            print(f"Could not load personal ID card extension: {type(exc).__name__}: {exc}")

    # Register the applications system defined directly in this file.
    if not getattr(bot, "_applications_loaded", False):
        try:
            await bot.add_cog(Applications(bot))
            bot._applications_loaded = True
            print("IronWolf applications system loaded from bot.py.")
        except Exception as exc:
            print(f"Could not load applications system: {type(exc).__name__}: {exc}")

    # Register IronWolf feature cogs directly from this file before slash-command sync.
    if not getattr(bot, "_ironwolf_features_loaded", False):
        try:
            await bot.add_cog(PlayerFiles(bot))
            await bot.add_cog(TeamTools(bot))
            await bot.add_cog(SecurityTools(bot))
            await bot.add_cog(EventTools(bot))
            await bot.add_cog(ControlTools(bot))
            await bot.add_cog(ServerLogs(bot))
            bot._ironwolf_features_loaded = True
            print("IronWolf feature cogs registered from bot.py.")
        except Exception as exc:
            print(f"Could not register IronWolf feature cogs: {type(exc).__name__}: {exc}")

    # Load the opt-in security suite once before slash-command sync.
    if not getattr(bot, "_security_suite_loaded", False):
        try:
            await bot.load_extension("security_suite")
            bot._security_suite_loaded = True
            print("IronWolf security suite loaded.")
        except Exception as exc:
            print(f"Could not load IronWolf security suite: {type(exc).__name__}: {exc}")

    # Load the economy extension once before slash-command sync.
    if not getattr(bot, "_economy_loaded", False):
        try:
            await bot.load_extension("economy")
            bot._economy_loaded = True
            print("IronWolf economy extension loaded.")
        except Exception as exc:
            print(f"Could not load economy extension: {type(exc).__name__}: {exc}")

    # Resume giveaway timers saved in data.json after a reconnect/restart.
    if not getattr(bot, "_giveaways_resumed", False):
        bot._giveaways_resumed = True
        for message_id, record in data.setdefault("giveaways", {}).items():
            if record.get("ended"):
                continue
            try:
                asyncio.create_task(_finish_giveaway(int(record["guild_id"]), int(record["channel_id"]), int(message_id), int(record.get("winners", 1)), str(record.get("prize", "Giveaway"))))
            except (KeyError, TypeError, ValueError):
                pass

    # Restore role/link buttons created from the dashboard message builder.
    for guild_id, cfg in config.items():
        for message_id, panel in cfg.get("message_panels", {}).items():
            try:
                panel_view = DashboardMessageView(int(guild_id), panel.get("buttons", []))
                bot.add_view(panel_view, message_id=int(message_id))
            except Exception as exc:
                print(f"[DASHBOARD] Could not restore message panel {message_id}: {type(exc).__name__}: {exc}")

    # Register persistent views so old buttons keep working after restart.
    bot.add_view(BirthdayRegisterView())
    bot.add_view(RegistrationView())
    bot.add_view(LanguageView())
    bot.add_view(TicketControlView())
    bot.add_view(VerifyView())

    # Restore persistent ticket panel views saved in config.
    for guild_id, cfg in config.items():
        for panel_key, panel in cfg.get("ticket_panels", {}).items():
            try:
                bot.add_view(TicketPanelView(panel_key, panel.get("labels", [])))
            except Exception:
                pass
    try:
        # Always publish the full command tree globally so commands are available
        # on every server the bot is installed on, not only the optional GUILD_ID.
        global_synced = await bot.tree.sync()
        print(f"Synced {len(global_synced)} global slash commands.")
        # Copy and sync to each connected guild so new commands appear immediately
        # instead of waiting for Discord's global-command propagation.
        for guild_obj in bot.guilds:
            bot.tree.copy_global_to(guild=guild_obj)
            guild_synced = await bot.tree.sync(guild=guild_obj)
            print(f"Synced {len(guild_synced)} slash commands to guild {guild_obj.id}.")
    except Exception as exc:
        print(f"Could not sync slash commands: {type(exc).__name__}: {exc}")
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    # Show the current server count in IronWolf's Discord activity.
    try:
        await bot.change_presence(activity=discord.Game(name=f"auf {len(bot.guilds)} Servern"))
    except discord.HTTPException as exc:
        print(f"Could not update server-count presence: {type(exc).__name__}: {exc}")
    global _social_alert_task_started
    if not _social_alert_task_started:
        _social_alert_task_started = True
        asyncio.create_task(social_alert_loop())
    if os.getenv("DASHBOARD_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"} and not getattr(bot, "_dashboard_started", False):
        try:
            from dashboard import start_dashboard
            start_dashboard(bot, config, save_json, guild_config)
            print(f"Dashboard startup requested (PORT={os.getenv('PORT', '8080')}).", flush=True)
        except Exception as exc:
            print(f"Could not start dashboard: {type(exc).__name__}: {exc}", flush=True)
    elif os.getenv("DASHBOARD_ENABLED", "true").strip().lower() not in {"1", "true", "yes", "on"}:
        print("Dashboard disabled: set DASHBOARD_ENABLED=true in Railway Variables.", flush=True)


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        message = "You need the **Manage Server** permission to use this command."
    else:
        message = f"Command error: `{error}`"
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.HTTPException:
        pass


if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN environment variable is missing.")
    bot.run(TOKEN)
