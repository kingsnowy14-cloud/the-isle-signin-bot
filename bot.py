import os
import json
import html
import io
import zipfile
import time
import asyncio
import random
import shutil
from pathlib import Path
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
CONFIG_FILE = "config.json"
DATA_FILE = "data.json"

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)


def create_embed(*args, **kwargs):
    """Create an embed with the standard bot attribution footer."""
    embed = discord.Embed(*args, **kwargs)
    embed.set_footer(text="Created by KingSnowy")
    return embed


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


config = load_json(CONFIG_FILE, {})
data = load_json(DATA_FILE, {"languages": {}, "registrations": {}})
# Stats are stored per server and per user in data.json.
data.setdefault("stats", {})
data.setdefault("moderation_warnings", {})
data.setdefault("giveaways", {})
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


def guild_config(guild_id):
    cfg = config.setdefault(str(guild_id), {
        "registration_channel": None,
        "ticket_category": None,
        "ticket_log_channel": None,
        "support_role": None,
        "ticket_panels": {},
        "ticket_counter": 0,
    })
    # Security settings are added safely to existing config files too.
    cfg.setdefault("security_log_channel", None)
    cfg.setdefault("honeypot_channel", None)
    cfg.setdefault("anti_nuke_enabled", True)
    cfg.setdefault("anti_nuke_threshold", 4)
    cfg.setdefault("anti_raid_enabled", True)
    cfg.setdefault("raid_threshold", 8)
    cfg.setdefault("trusted_security_role", None)
    cfg.setdefault("welcome_channel", None)
    cfg.setdefault("welcome_message", "Willkommen {mention} auf **{server}**! Du bist Mitglied Nr. **{count}**.")
    cfg.setdefault("social_links", {})
    return cfg


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
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Use this inside a server.", ephemeral=True)
            return
        cfg = guild_config(guild.id)
        panel = cfg.get("ticket_panels", {}).get(self.panel_key)
        if not panel:
            await interaction.response.send_message("This ticket panel is no longer configured.", ephemeral=True)
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
                channel_name, category=category, overwrites=overwrites,
                topic=f"Ticket owner ID: {interaction.user.id} | Ticket number: {ticket_number}"
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "I don't have permission to create ticket channels. Check Manage Channels permission.",
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

        embed = create_embed(
            title=f"🎫 {self.button_label}",
            description=f"Hello {interaction.user.mention}!\n\n**Issue:**\n{self.reason.value}",
            color=discord.Color.blurple(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Ticket owner", value=interaction.user.mention, inline=True)
        embed.add_field(name="Ticket number", value=str(ticket_number), inline=True)
        await channel.send(
            content=f"{interaction.user.mention}" + (f" {support_role.mention}" if support_role else ""),
            embed=embed,
            view=TicketControlView(),
        )
        await interaction.response.send_message(f"Your ticket has been created: {channel.mention}", ephemeral=True)


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
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.primary, emoji="🙋",
                       custom_id="ticket:claim")
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        meta = data.setdefault("tickets", {}).get(str(interaction.channel_id))
        if not meta:
            await interaction.response.send_message("Ticket data not found.", ephemeral=True)
            return
        if not is_support(interaction.user, meta, interaction.guild):
            await interaction.response.send_message("Only the configured support team can claim tickets.", ephemeral=True)
            return
        meta["claimed_by"] = interaction.user.id
        save_json(DATA_FILE, data)
        await interaction.response.send_message(f"Ticket claimed by {interaction.user.mention}.")
        try:
            await interaction.channel.edit(topic=f"Ticket owner ID: {meta['owner_id']} | Claimed by ID: {interaction.user.id}")
        except discord.HTTPException:
            pass

    @discord.ui.button(label="Close ticket", style=discord.ButtonStyle.danger, emoji="🔒",
                       custom_id="ticket:close")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
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
    data.setdefault("tickets", {}).pop(str(channel.id), None)
    save_json(DATA_FILE, data)
    await channel.delete(reason=f"Ticket closed by {closed_by}")



# ----------------------------- LEVEL SYSTEM -----------------------------

import random


def get_user_level(xp: int) -> int:
    """Calculate the level from total XP."""
    return int((max(0, xp) / 100) ** 0.5)


def xp_for_level(level: int) -> int:
    """Total XP required to reach a level."""
    return 100 * level * level


def add_message_xp(guild_id: int, user_id: int):
    """Award 5–15 XP, at most once every 60 seconds."""
    stats = get_user_stats(guild_id, user_id)
    now = time.time()
    last_xp_at = float(stats.get("last_xp_at", 0))

    if now - last_xp_at < 60:
        return 0, get_user_level(int(stats.get("xp", 0)))

    old_level = get_user_level(int(stats.get("xp", 0)))
    earned_xp = random.randint(5, 15)

    stats["xp"] = int(stats.get("xp", 0)) + earned_xp
    stats["last_xp_at"] = now

    new_level = get_user_level(stats["xp"])
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
    support_rolle: discord.Role = None,
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
    """Zeigt automatisch alle registrierten Slash- und Prefix-Befehle."""
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
                slash_commands.append((f"/{full_name}", description))

    # Read the live command registries on every invocation.
    collect_slash(bot.tree.get_commands())

    # Prefix commands (!...) and their aliases are collected automatically too.
    for command in bot.commands:
        if command.hidden or command.name == "help":
            continue
        names = [command.name, *command.aliases]
        description = (command.help or command.brief or "Keine Beschreibung vorhanden.").strip()
        for command_name in names:
            prefix_commands.append((f"!{command_name}", description))

    slash_commands.sort(key=lambda item: item[0].casefold())
    prefix_commands.sort(key=lambda item: item[0].casefold())

    embed = create_embed(
        title="📖 Hilfe – aktuelle Befehle",
        description=(
            "Diese Übersicht wird bei jedem Aufruf direkt aus den registrierten Befehlen erstellt.\n"
            "**Slash-Befehle** beginnen mit `/`, **Prefix-Befehle** beginnen mit `!`."
        ),
        color=discord.Color.blurple(),
    )

    def make_chunks(items):
        chunks = []
        current = ""
        for name, description in items:
            line = f"**{name}**\n{description}"
            if len(line) > 950:
                line = line[:947] + "..."
            if current and len(current) + len(line) + 2 > 950:
                chunks.append(current)
                current = line
            else:
                current = f"{current}\n\n{line}" if current else line
        if current:
            chunks.append(current)
        return chunks

    fields = []
    slash_chunks = make_chunks(slash_commands)
    prefix_chunks = make_chunks(prefix_commands)
    for index, chunk in enumerate(slash_chunks):
        fields.append((f"🔹 Slash-Befehle {index + 1}", chunk))
    for index, chunk in enumerate(prefix_chunks):
        fields.append((f"🟢 Prefix-Befehle (!) {index + 1}", chunk))

    # Discord permits up to 25 fields per embed; send additional pages as follow-ups.
    pages = [fields[i:i + 25] for i in range(0, max(len(fields), 1), 25)]
    for page_index, page_fields in enumerate(pages):
        page_embed = embed.copy()
        for field_name, field_value in page_fields:
            page_embed.add_field(name=field_name, value=field_value, inline=False)
        page_embed.set_footer(
            text=f"{len(slash_commands)} Slash-Befehle • {len(prefix_commands)} Prefix-Befehle • Seite {page_index + 1}/{len(pages)}"
        )
        if page_index == 0:
            await interaction.response.send_message(embed=page_embed, ephemeral=True)
        else:
            await interaction.followup.send(embed=page_embed, ephemeral=True)


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
    await interaction.response.send_message(f"🔇 {mitglied.mention} erhielt einen Timeout für **{minuten} Minuten**. Grund: {grund[:500]}")
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
    await interaction.response.send_message(f"🔊 Timeout von {mitglied.mention} wurde entfernt.")


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
    await interaction.response.send_message(f"👢 {mitglied} wurde entfernt. Grund: {grund[:500]}")
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
    await interaction.response.send_message(f"🔨 {mitglied} wurde gebannt. Grund: {grund[:500]}")
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
        entrants = []
        for reaction in message.reactions:
            if str(reaction.emoji) == "🎉":
                async for user in reaction.users():
                    if not user.bot:
                        entrants.append(user)
        entrants = list({u.id: u for u in entrants}.values())
        winners = random.sample(entrants, min(winners_count, len(entrants))) if entrants else []
        record["ended"] = True
        record["winners"] = [u.id for u in winners]
        save_json(DATA_FILE, data)
        if winners:
            await channel.send(f"🎉 Giveaway beendet! Preis: **{prize}** — Gewinner: {', '.join(u.mention for u in winners)}")
        else:
            await channel.send(f"Giveaway für **{prize}** beendet. Es gab keine gültigen Teilnehmer.")
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        print(f"Giveaway task error: {type(exc).__name__}: {exc}")


@bot.tree.command(name="giveaway-start", description="Starte ein Giveaway mit Reaktion 🎉")
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(preis="Zu gewinnender Preis", minuten="Laufzeit in Minuten (1–10080)", gewinner="Anzahl der Gewinner (1–10)")
async def giveaway_start(interaction: discord.Interaction, preis: str, minuten: app_commands.Range[int, 1, 10080], gewinner: app_commands.Range[int, 1, 10] = 1):
    await interaction.response.send_message("Giveaway wird erstellt …", ephemeral=True)
    ends_at = time.time() + minuten * 60
    embed = create_embed(title="🎉 GIVEAWAY", description=f"**Preis:** {preis[:200]}\n**Gewinner:** {gewinner}\n**Endet:** <t:{int(ends_at)}:R>\n\nReagiere mit 🎉, um teilzunehmen!", color=discord.Color.gold())
    message = await interaction.channel.send(embed=embed)
    await message.add_reaction("🎉")
    data.setdefault("giveaways", {})[str(message.id)] = {"guild_id": interaction.guild_id, "channel_id": interaction.channel_id, "ends_at": ends_at, "winners": gewinner, "prize": preis[:200], "ended": False}
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


# ----------------------------- SECURITY: HONEYPOT / ANTI-NUKE / ANTI-RAID -----------------------------

# Rolling counters live in memory; they reset when the bot restarts.
_nuke_events = defaultdict(deque)       # (guild_id, actor_id) -> timestamps
_raid_joins = defaultdict(deque)        # guild_id -> (timestamp, member_id)
_raid_lockdown_until = {}               # guild_id -> monotonic timestamp
_nuke_handled = set()                   # avoid handling the same audit entry twice


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
                        return entry.user, entry.id, entry.reason
        except (discord.Forbidden, discord.HTTPException):
            return None, None, None
        if attempt < 3:
            await asyncio.sleep(0.7)
    return None, None, None


async def record_nuke_action(guild: discord.Guild, action_name: str, action: discord.AuditLogAction,
                             target_id=None, target_name="unknown"):
    cfg = guild_config(guild.id)
    if not cfg.get("anti_nuke_enabled", True):
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

        # Count messages and award XP with a cooldown.
        stats = get_user_stats(guild.id, message.author.id)
        stats["messages"] = int(stats.get("messages", 0)) + 1
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
            await message.channel.send(embed=embed)

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
    """Track time spent connected to any voice channel (not just speaking)."""
    if member.bot:
        return

    key = (member.guild.id, member.id)
    now = datetime.now(timezone.utc)

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


@bot.event
async def on_member_join(member: discord.Member):
    if member.bot:
        # Bot additions are also monitored by the anti-nuke system below.
        return
    cfg = guild_config(member.guild.id)
    # Welcome message is independent from whether anti-raid is enabled.
    welcome_channel_id = cfg.get("welcome_channel")
    if welcome_channel_id:
        welcome_channel = member.guild.get_channel(welcome_channel_id)
        if isinstance(welcome_channel, discord.TextChannel):
            template = cfg.get("welcome_message", "Willkommen {mention} auf **{server}**! Du bist Mitglied Nr. **{count}**.")
            welcome_text = (template.replace("{mention}", member.mention)
                            .replace("{user}", discord.utils.escape_markdown(member.display_name))
                            .replace("{server}", discord.utils.escape_markdown(member.guild.name))
                            .replace("{count}", str(member.guild.member_count or len(member.guild.members))))
            try:
                await welcome_channel.send(welcome_text[:1900], allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False))
            except (discord.Forbidden, discord.HTTPException):
                pass
    if not cfg.get("anti_raid_enabled", True):
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


@bot.event
async def on_ready():
    # Start tracking members already in voice after a bot start/reconnect.
    # Only seed sessions that are not already tracked to avoid resetting time
    # when Discord reconnects the bot.
    now = datetime.now(timezone.utc)
    for guild in bot.guilds:
        for member in guild.members:
            if not member.bot and member.voice and member.voice.channel:
                _active_voice_sessions.setdefault((guild.id, member.id), now)

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

    # Register persistent views so old buttons keep working after restart.
    bot.add_view(RegistrationView())
    bot.add_view(LanguageView())
    bot.add_view(TicketControlView())

    # Restore persistent ticket panel views saved in config.
    for guild_id, cfg in config.items():
        for panel_key, panel in cfg.get("ticket_panels", {}).items():
            try:
                bot.add_view(TicketPanelView(panel_key, panel.get("labels", [])))
            except Exception:
                pass
    try:
        if GUILD_ID:
            guild_obj = discord.Object(id=int(GUILD_ID))
            # Copy registered commands to this server and sync them immediately.
            bot.tree.copy_global_to(guild=guild_obj)
            synced = await bot.tree.sync(guild=guild_obj)
            print(f"Synced {len(synced)} slash commands to guild {GUILD_ID}.")
        else:
            synced = await bot.tree.sync()
            print(f"Synced {len(synced)} global slash commands. Global commands may take a while to appear; set GUILD_ID in Railway for faster server-only sync.")
    except Exception as exc:
        print(f"Could not sync slash commands: {type(exc).__name__}: {exc}")
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")


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
    # Optional web dashboard; enable with DASHBOARD_ENABLED=true.
    if os.getenv("DASHBOARD_ENABLED", "false").lower() == "true":
        from dashboard import start_dashboard
        start_dashboard()
    bot.run(TOKEN)
