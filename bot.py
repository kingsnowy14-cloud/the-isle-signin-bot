import os
import json
import io
import html
import re
from datetime import datetime, timezone

import discord
from discord.ext import commands
from discord import app_commands


# =========================================================
# GRUNDEINSTELLUNGEN
# =========================================================

TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN fehlt in den Railway-Variablen!")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

CONFIG_FILE = "config.json"
SIGNINS_FILE = "signins.json"
LANGUAGES_FILE = "languages.json"
TICKETS_FILE = "tickets.json"


def load_json(filename, default):
    try:
        with open(filename, "r", encoding="utf-8") as file:
            return json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(filename, data):
    temp = filename + ".tmp"
    with open(temp, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=4, ensure_ascii=False)
    os.replace(temp, filename)


config = load_json(CONFIG_FILE, {})
signins = load_json(SIGNINS_FILE, {})
languages = load_json(LANGUAGES_FILE, {})
tickets = load_json(TICKETS_FILE, {})

config.setdefault("announcement_channel_id", None)
config.setdefault("ticket_log_channel_id", None)
config.setdefault("support_role_id", None)
config.setdefault("ticket_category_id", None)
config.setdefault("ticket_button_labels", ["Ticket erstellen"])
config.setdefault("ticket_welcome_title", "🎫 KingSnowy Support")
config.setdefault(
    "ticket_welcome_description",
    "Hallo! Beschreibe dein Problem möglichst genau. "
    "Unser Support-Team wird dir helfen."
)

views_registered = False


# =========================================================
# HILFSFUNKTIONEN
# =========================================================

async def get_text_channel(channel_id):
    if not channel_id:
        return None

    channel = bot.get_channel(int(channel_id))
    if channel is None:
        try:
            channel = await bot.fetch_channel(int(channel_id))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None

    return channel if isinstance(channel, discord.TextChannel) else None


def is_support(member):
    if not isinstance(member, discord.Member):
        return False

    if (
        member.guild_permissions.administrator
        or member.guild_permissions.manage_channels
        or member.guild_permissions.manage_messages
    ):
        return True

    role_id = config.get("support_role_id")
    return bool(
        role_id and any(role.id == int(role_id) for role in member.roles)
    )


async def private_reply(interaction, content, **kwargs):
    if interaction.response.is_done():
        await interaction.followup.send(
            content, ephemeral=True, **kwargs
        )
    else:
        await interaction.response.send_message(
            content, ephemeral=True, **kwargs
        )


async def send_announcement(embed):
    channel = await get_text_channel(config.get("announcement_channel_id"))
    if channel:
        try:
            await channel.send(embed=embed)
        except discord.HTTPException as error:
            print(f"Anmeldungs-Log-Fehler: {error}")


async def send_ticket_log_file(content, filename, file_bytes):
    """Ticket-Log wird ausschließlich beim Schließen verwendet."""
    channel = await get_text_channel(config.get("ticket_log_channel_id"))

    if channel is None:
        print("Ticket-Log fehlt. Transkript konnte nicht protokolliert werden.")
        return False

    try:
        await channel.send(
            content=content,
            file=discord.File(io.BytesIO(file_bytes), filename=filename),
        )
        return True
    except discord.HTTPException as error:
        print(f"Ticket-Transkript konnte nicht gesendet werden: {error}")
        return False


# =========================================================
# TICKET-METADATEN IM KANAL-THEMA
# =========================================================

def make_topic(owner, claimed=0, created="", owner_vote=0,
               support_vote=0, ticket_type="Ticket"):
    # Discord erlaubt maximal 1024 Zeichen im Kanalthema.
    values = [
        "ks_ticket",
        f"owner:{owner}",
        f"claimed:{claimed}",
        f"created:{created}",
        f"owner_vote:{owner_vote}",
        f"support_vote:{support_vote}",
        f"type:{ticket_type[:50]}",
    ]
    return "|".join(values)[:1024]


def parse_topic(topic):
    if not topic or not topic.startswith("ks_ticket|"):
        return None

    data = {}
    for part in topic.split("|")[1:]:
        if ":" not in part:
            continue
        key, value = part.split(":", 1)
        if key in ("owner", "claimed", "owner_vote", "support_vote"):
            try:
                data[key] = int(value)
            except ValueError:
                pass
        else:
            data[key] = value

    return data


async def update_topic(channel, **updates):
    data = parse_topic(channel.topic)
    if not data:
        return None

    data.update(updates)
    new_topic = make_topic(
        owner=data.get("owner", 0),
        claimed=data.get("claimed", 0),
        created=data.get("created", ""),
        owner_vote=data.get("owner_vote", 0),
        support_vote=data.get("support_vote", 0),
        ticket_type=data.get("type", "Ticket"),
    )
    await channel.edit(topic=new_topic)
    return data


# =========================================================
# ANMELDE-SYSTEM
# =========================================================

class SignInModal(discord.ui.Modal, title="The Isle - Sign In"):
    dinosaur = discord.ui.TextInput(
        label="Dinosaur",
        placeholder="For example: Deinosuchus",
        max_length=100,
    )
    skin = discord.ui.TextInput(
        label="Skin",
        placeholder="Enter your skin",
        max_length=100,
    )
    location = discord.ui.TextInput(
        label="Current location",
        placeholder="Where are you currently located?",
        max_length=200,
    )

    async def on_submit(self, interaction):
        user_id = str(interaction.user.id)
        signins[user_id] = {
            "dino": self.dinosaur.value,
            "skin": self.skin.value,
            "location": self.location.value,
            "username": interaction.user.name,
            "signed_in_at": datetime.now(timezone.utc).isoformat(),
        }
        save_json(SIGNINS_FILE, signins)

        embed = discord.Embed(
            title="🟢 Player signed in",
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Player", value=interaction.user.mention,
                        inline=False)
        embed.add_field(name="Dinosaur", value=self.dinosaur.value,
                        inline=True)
        embed.add_field(name="Skin", value=self.skin.value, inline=True)
        embed.add_field(name="Current location", value=self.location.value,
                        inline=False)

        await send_announcement(embed)
        await interaction.response.send_message(
            "✅ Du bist angemeldet!", ephemeral=True
        )


class LanguageSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(
            placeholder="Choose your language / Sprache auswählen",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label="English", value="en", emoji="🇬🇧"),
                discord.SelectOption(label="Deutsch", value="de", emoji="🇩🇪"),
                discord.SelectOption(label="Čeština", value="cs", emoji="🇨🇿"),
            ],
            custom_id="kingsnowy:language",
        )

    async def callback(self, interaction):
        languages[str(interaction.user.id)] = self.values[0]
        save_json(LANGUAGES_FILE, languages)

        messages = {
            "en": "Language set to English.",
            "de": "Sprache auf Deutsch eingestellt.",
            "cs": "Jazyk wurde eingestellt.",
        }
        await interaction.response.send_message(
            messages[self.values[0]], ephemeral=True
        )


class SignInView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(LanguageSelect())

    @discord.ui.button(
        label="Sign In",
        emoji="🟢",
        style=discord.ButtonStyle.success,
        custom_id="kingsnowy:signin",
    )
    async def signin(self, interaction, button):
        if str(interaction.user.id) in signins:
            await private_reply(
                interaction, "Du bist bereits angemeldet. Melde dich zuerst ab."
            )
            return
        await interaction.response.send_modal(SignInModal())

    @discord.ui.button(
        label="Sign Out",
        emoji="🔴",
        style=discord.ButtonStyle.danger,
        custom_id="kingsnowy:signout",
    )
    async def signout(self, interaction, button):
        user_id = str(interaction.user.id)
        if user_id not in signins:
            await private_reply(interaction, "Du bist nicht angemeldet.")
            return

        saved = signins.pop(user_id)
        save_json(SIGNINS_FILE, signins)

        embed = discord.Embed(
            title="🔴 Player signed out",
            color=discord.Color.red(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Player", value=interaction.user.mention,
                        inline=False)
        embed.add_field(name="Dinosaur", value=saved.get("dino", "Not specified"),
                        inline=True)
        embed.add_field(name="Skin", value=saved.get("skin", "Not specified"),
                        inline=True)

        await send_announcement(embed)
        await interaction.response.send_message(
            "✅ Du wurdest abgemeldet.", ephemeral=True
        )


@bot.command(name="anmeldepannel", aliases=["anmeldepanel"])
@commands.has_permissions(manage_guild=True)
async def anmeldepanel(ctx):
    embed = discord.Embed(
        title="❄️ The Isle – Player Sign-In",
        description=(
            "Use the buttons below to sign in or sign out.\n"
            "Wähle zuerst deine Sprache aus."
        ),
        color=discord.Color.blue(),
    )
    await ctx.send(embed=embed, view=SignInView())


@bot.tree.command(
    name="kanal-set",
    description="Set the player sign-in log channel",
)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(channel="Kanal für Anmeldungen und Abmeldungen")
async def kanal_set(interaction, channel: discord.TextChannel):
    config["announcement_channel_id"] = channel.id
    save_json(CONFIG_FILE, config)
    await interaction.response.send_message(
        f"✅ Anmeldungs-Log: {channel.mention}", ephemeral=True
    )


# =========================================================
# TICKET-ERSTELLUNG
# =========================================================

async def create_ticket(interaction, button_index):
    guild = interaction.guild
    if guild is None:
        await private_reply(interaction, "Nur auf einem Server möglich.")
        return

    labels = config.get("ticket_button_labels", ["Ticket erstellen"])
    if button_index >= len(labels):
        await private_reply(
            interaction, "Dieser Ticket-Button ist nicht mehr konfiguriert."
        )
        return

    ticket_type = labels[button_index]
    for channel in guild.text_channels:
        data = parse_topic(channel.topic)
        if data and data.get("owner") == interaction.user.id:
            await private_reply(
                interaction,
                f"Du hast bereits ein offenes Ticket: {channel.mention}",
            )
            return

    bot_member = guild.me
    if bot_member is None:
        await private_reply(interaction, "Bot-Mitglied nicht gefunden.")
        return

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        interaction.user: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            attach_files=True,
            embed_links=True,
        ),
        bot_member: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            manage_channels=True,
            attach_files=True,
            embed_links=True,
        ),
    }

    role_id = config.get("support_role_id")
    if role_id:
        role = guild.get_role(int(role_id))
        if role:
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
            )

    category = None
    category_id = config.get("ticket_category_id")
    if category_id:
        candidate = guild.get_channel(int(category_id))
        if isinstance(candidate, discord.CategoryChannel):
            category = candidate

    safe_name = re.sub(
        r"[^a-z0-9_-]", "",
        interaction.user.name.lower().replace(" ", "-")
    )[:20] or "user"

    channel_name = f"ticket-{safe_name}"[:90]
    created_at = datetime.now(timezone.utc).isoformat()

    try:
        channel = await guild.create_text_channel(
            name=channel_name,
            category=category,
            overwrites=overwrites,
            topic=make_topic(
                interaction.user.id,
                created=created_at,
                ticket_type=ticket_type,
            ),
            reason=f"Ticket created by {interaction.user}",
        )
    except (discord.Forbidden, discord.HTTPException) as error:
        await private_reply(
            interaction, f"Ticket konnte nicht erstellt werden: {error}"
        )
        return

    tickets[str(channel.id)] = {
        "owner_id": interaction.user.id,
        "owner_name": str(interaction.user),
        "claimed_id": None,
        "claimed_name": None,
        "ticket_type": ticket_type,
        "created_at": created_at,
        "guild_id": guild.id,
    }
    save_json(TICKETS_FILE, tickets)

    title = config.get("ticket_welcome_title", "🎫 KingSnowy Support")
    description = config.get(
        "ticket_welcome_description",
        "Beschreibe dein Problem möglichst genau."
    )

    embed = discord.Embed(
        title=title,
        description=(
            f"Hallo {interaction.user.mention}!\n\n"
            f"{description}\n\n"
            f"**Ticket-Typ:** {ticket_type}"
        ),
        color=discord.Color.blue(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(
        name="Erstellt von",
        value=interaction.user.mention,
        inline=True,
    )

    await channel.send(embed=embed, view=TicketControlsView())
    # Absichtlich keine Ticket-Log-Nachricht bei der Erstellung.

    await interaction.response.send_message(
        f"✅ Dein Ticket wurde erstellt: {channel.mention}",
        ephemeral=True,
    )


class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

        labels = config.get("ticket_button_labels", ["Ticket erstellen"])[:5]
        if not labels:
            labels = ["Ticket erstellen"]

        for index, label in enumerate(labels):
            button = discord.ui.Button(
                label=label[:80],
                emoji="🎫",
                style=discord.ButtonStyle.primary,
                custom_id=f"kingsnowy:ticket-create:{index}",
            )

            async def callback(interaction, index=index):
                await create_ticket(interaction, index)

            button.callback = callback
            self.add_item(button)


# =========================================================
# TICKET-NACHRICHTEN BEARBEITEN
# =========================================================

class EditTicketModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Ticket-Nachricht bearbeiten")

        self.title_input = discord.ui.TextInput(
            label="Willkommens-Titel",
            default=config.get("ticket_welcome_title", "🎫 KingSnowy Support"),
            max_length=256,
            required=True,
        )
        self.description_input = discord.ui.TextInput(
            label="Willkommens-Beschreibung",
            default=config.get(
                "ticket_welcome_description",
                "Beschreibe dein Problem möglichst genau."
            )[:4000],
            style=discord.TextStyle.paragraph,
            max_length=4000,
            required=True,
        )
        self.add_item(self.title_input)
        self.add_item(self.description_input)

    async def on_submit(self, interaction):
        config["ticket_welcome_title"] = self.title_input.value
        config["ticket_welcome_description"] = self.description_input.value
        save_json(CONFIG_FILE, config)

        preview = discord.Embed(
            title=self.title_input.value,
            description=self.description_input.value,
            color=discord.Color.blue(),
        )
        await interaction.response.send_message(
            "✅ Die Willkommensnachricht für **neue Tickets** wurde gespeichert. "
            "Bereits bestehende Tickets werden nicht automatisch geändert.",
            embed=preview,
            ephemeral=True,
        )


# =========================================================
# TICKET SCHLIESSEN UND TRANSKRIPT
# =========================================================

class CloseVoteView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Als Ersteller bestätigen",
        emoji="👤",
        style=discord.ButtonStyle.primary,
        custom_id="kingsnowy:close-owner",
    )
    async def owner_vote(self, interaction, button):
        channel = interaction.channel
        data = parse_topic(channel.topic) if isinstance(
            channel, discord.TextChannel
        ) else None

        if not data:
            await private_reply(interaction, "Ticket nicht gefunden.")
            return
        if interaction.user.id != data.get("owner"):
            await private_reply(
                interaction, "Nur der Ticket-Ersteller darf bestätigen."
            )
            return

        await update_topic(channel, owner_vote=1)
        await self.check_close(interaction, channel)

    @discord.ui.button(
        label="Als Support bestätigen",
        emoji="🛡️",
        style=discord.ButtonStyle.success,
        custom_id="kingsnowy:close-support",
    )
    async def support_vote(self, interaction, button):
        channel = interaction.channel
        data = parse_topic(channel.topic) if isinstance(
            channel, discord.TextChannel
        ) else None

        if not data:
            await private_reply(interaction, "Ticket nicht gefunden.")
            return
        if not is_support(interaction.user):
            await private_reply(interaction, "Nur Support darf bestätigen.")
            return

        await update_topic(channel, support_vote=1)
        await self.check_close(interaction, channel)

    async def check_close(self, interaction, channel):
        data = parse_topic(channel.topic)
        if data and data.get("owner_vote") and data.get("support_vote"):
            await interaction.response.send_message(
                "🔒 Beide Bestätigungen liegen vor. Ticket wird geschlossen.",
                ephemeral=True,
            )
            await close_ticket(channel, interaction.user)
        else:
            await interaction.response.send_message(
                "✅ Bestätigung gespeichert. Es fehlt noch die zweite Bestätigung.",
                ephemeral=True,
            )


async def build_transcript(channel, closer):
    data = parse_topic(channel.topic) or {}
    saved = tickets.get(str(channel.id), {})

    owner_id = data.get("owner") or saved.get("owner_id")
    claimed_id = data.get("claimed") or saved.get("claimed_id")

    owner_name = saved.get("owner_name", "Unbekannt")
    claimed_name = saved.get("claimed_name", "Niemand")

    owner_member = channel.guild.get_member(int(owner_id)) if owner_id else None
    claimed_member = (
        channel.guild.get_member(int(claimed_id)) if claimed_id else None
    )

    if owner_member:
        owner_name = str(owner_member)
    if claimed_member:
        claimed_name = str(claimed_member)

    created_at = saved.get("created_at", data.get("created", "Unbekannt"))
    ticket_type = saved.get("ticket_type", data.get("type", "Ticket"))

    lines = [
        "<!DOCTYPE html>",
        "<html><head><meta charset='utf-8'>",
        f"<title>Transkript - {html.escape(channel.name)}</title>",
        """
        <style>
        body { font-family: Arial, sans-serif; margin: 24px; }
        .meta { background: #f0f0f0; padding: 16px; border-radius: 8px; }
        .message { border-bottom: 1px solid #ddd; padding: 12px 0; }
        .author { font-weight: bold; }
        .time { color: #666; font-size: 12px; }
        .content { white-space: pre-wrap; overflow-wrap: anywhere; }
        </style></head><body>
        """,
        f"<h1>Ticket-Transkript: {html.escape(channel.name)}</h1>",
        "<div class='meta'>",
        f"<p><b>Ticket erstellt von:</b> {html.escape(owner_name)} "
        f"(ID: {owner_id or 'Unbekannt'})</p>",
        f"<p><b>Übernommen von:</b> {html.escape(claimed_name)} "
        f"(ID: {claimed_id or 'Niemand'})</p>",
        f"<p><b>Geschlossen von:</b> {html.escape(str(closer))} "
        f"(ID: {closer.id})</p>",
        f"<p><b>Ticket-Typ:</b> {html.escape(str(ticket_type))}</p>",
        f"<p><b>Erstellt am:</b> {html.escape(str(created_at))}</p>",
        f"<p><b>Geschlossen am:</b> "
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}</p>",
        "</div><h2>Nachrichtenverlauf</h2>",
    ]

    async for message in channel.history(limit=None, oldest_first=True):
        timestamp = message.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        lines.extend([
            "<div class='message'>",
            f"<div class='author'>{html.escape(str(message.author))} "
            f"(ID: {message.author.id})</div>",
            f"<div class='time'>{html.escape(timestamp)}</div>",
            f"<div class='content'>{html.escape(message.content or '')}</div>",
        ])

        for embed in message.embeds:
            if embed.title:
                lines.append(f"<p><b>{html.escape(embed.title)}</b></p>")
            if embed.description:
                lines.append(
                    f"<div class='content'>{html.escape(embed.description)}</div>"
                )

        for attachment in message.attachments:
            lines.append(
                f"<p>Anhang: {html.escape(attachment.filename)} "
                f"(Datei-Link: {html.escape(attachment.url)})</p>"
            )

        lines.append("</div>")

    lines.append("</body></html>")
    return "\n".join(lines).encode("utf-8")


async def close_ticket(channel, closer):
    data = parse_topic(channel.topic)
    if not data:
        return

    transcript = await build_transcript(channel, closer)
    filename = f"{channel.name}-transcript.html"

    saved = tickets.get(str(channel.id), {})
    owner_id = data.get("owner") or saved.get("owner_id")
    claimed_id = data.get("claimed") or saved.get("claimed_id")

    owner_name = saved.get("owner_name", "Unbekannt")
    claimed_name = saved.get("claimed_name", "Niemand")

    owner_member = channel.guild.get_member(int(owner_id)) if owner_id else None
    claimed_member = (
        channel.guild.get_member(int(claimed_id)) if claimed_id else None
    )

    if owner_member:
        owner_name = str(owner_member)
    if claimed_member:
        claimed_name = str(claimed_member)

    log_message = (
        f"🔒 **Ticket geschlossen: {channel.name}**\n"
        f"**Erstellt von:** {owner_name} (`{owner_id or 'Unbekannt'}`)\n"
        f"**Übernommen von:** {claimed_name} (`{claimed_id or 'Niemand'}`)\n"
        f"**Geschlossen von:** {closer} (`{closer.id}`)\n"
        f"**Ticket-Typ:** {saved.get('ticket_type', data.get('type', 'Ticket'))}\n"
        "Das HTML-Transkript ist angehängt."
    )

    sent = await send_ticket_log_file(
        log_message, filename, transcript
    )

    if not sent:
        # Sicherheit: Nicht löschen, wenn das Transkript nicht
        # im konfigurierten Log-Kanal gesichert werden konnte.
        try:
            await channel.send(
                "⚠️ Das Transkript konnte nicht im Ticket-Log gesichert werden. "
                "Bitte prüfe den Log-Kanal und die Bot-Berechtigungen. "
                "Das Ticket wurde deshalb nicht gelöscht."
            )
        except discord.HTTPException:
            pass
        return

    tickets.pop(str(channel.id), None)
    save_json(TICKETS_FILE, tickets)

    try:
        await channel.delete(reason=f"Ticket closed by {closer}")
    except discord.HTTPException as error:
        print(f"Ticket-Kanal konnte nicht gelöscht werden: {error}")


# =========================================================
# TICKET-STEUERUNG
# =========================================================

class TicketControlsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Ticket übernehmen",
        emoji="🛡️",
        style=discord.ButtonStyle.success,
        custom_id="kingsnowy:ticket-claim",
    )
    async def claim(self, interaction, button):
        channel = interaction.channel
        data = parse_topic(channel.topic) if isinstance(
            channel, discord.TextChannel
        ) else None

        if not data:
            await private_reply(interaction, "Ticket nicht gefunden.")
            return
        if not is_support(interaction.user):
            await private_reply(interaction, "Nur Support darf Tickets übernehmen.")
            return
        if data.get("claimed"):
            await private_reply(
                interaction, f"Bereits übernommen von <@{data['claimed']}>."
            )
            return

        await update_topic(channel, claimed=interaction.user.id)
        saved = tickets.get(str(channel.id), {})
        saved["claimed_id"] = interaction.user.id
        saved["claimed_name"] = str(interaction.user)
        tickets[str(channel.id)] = saved
        save_json(TICKETS_FILE, tickets)

        await interaction.response.send_message(
            f"🛡️ {interaction.user.mention} hat das Ticket übernommen."
        )

    @discord.ui.button(
        label="Übernahme freigeben",
        emoji="↩️",
        style=discord.ButtonStyle.secondary,
        custom_id="kingsnowy:ticket-release",
    )
    async def release(self, interaction, button):
        channel = interaction.channel
        data = parse_topic(channel.topic) if isinstance(
            channel, discord.TextChannel
        ) else None

        if not data:
            await private_reply(interaction, "Ticket nicht gefunden.")
            return
        if not is_support(interaction.user):
            await private_reply(interaction, "Nur Support darf das freigeben.")
            return
        if (
            data.get("claimed")
            and data.get("claimed") != interaction.user.id
            and not interaction.user.guild_permissions.administrator
        ):
            await private_reply(
                interaction, "Nur der zuständige Support oder ein Admin darf das."
            )
            return

        await update_topic(channel, claimed=0)
        saved = tickets.get(str(channel.id), {})
        saved["claimed_id"] = None
        saved["claimed_name"] = "Niemand"
        tickets[str(channel.id)] = saved
        save_json(TICKETS_FILE, tickets)

        await interaction.response.send_message(
            f"↩️ {interaction.user.mention} hat die Übernahme freigegeben."
        )

    @discord.ui.button(
        label="Schließen anfordern",
        emoji="🔒",
        style=discord.ButtonStyle.danger,
        custom_id="kingsnowy:ticket-close-request",
    )
    async def request_close(self, interaction, button):
        channel = interaction.channel
        data = parse_topic(channel.topic) if isinstance(
            channel, discord.TextChannel
        ) else None

        if not data:
            await private_reply(interaction, "Ticket nicht gefunden.")
            return
        if (
            interaction.user.id != data.get("owner")
            and not is_support(interaction.user)
        ):
            await private_reply(
                interaction, "Nur Ticket-Ersteller oder Support dürfen das."
            )
            return

        await update_topic(channel, owner_vote=0, support_vote=0)

        embed = discord.Embed(
            title="🔒 Ticket-Schließung",
            description=(
                "Zum Schließen werden zwei Bestätigungen benötigt:\n"
                "• Ticket-Ersteller\n"
                "• Support-Team\n\n"
                "Das Transkript wird erst beim erfolgreichen Schließen "
                "im Ticket-Log veröffentlicht."
            ),
            color=discord.Color.orange(),
        )
        await interaction.response.send_message(
            embed=embed, view=CloseVoteView()
        )


# =========================================================
# SLASH-BEFEHL: /ticketcreat
# =========================================================

@bot.tree.command(
    name="ticketcreat",
    description="Ticket-Panel einrichten und veröffentlichen",
)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(
    support_rolle="Rolle, die Tickets sehen und bearbeiten darf",
    kategorie="Kategorie, in der neue Tickets erstellt werden",
    button1="Name des ersten Ticket-Buttons",
    button2="Optionaler zweiter Ticket-Button",
    button3="Optionaler dritter Ticket-Button",
    button4="Optionaler vierter Ticket-Button",
    button5="Optionaler fünfter Ticket-Button",
)
async def ticketcreat(
    interaction: discord.Interaction,
    support_rolle: discord.Role,
    kategorie: discord.CategoryChannel,
    button1: str,
    button2: str = None,
    button3: str = None,
    button4: str = None,
    button5: str = None,
):
    labels = [
        label.strip()
        for label in [button1, button2, button3, button4, button5]
        if label and label.strip()
    ]

    if not labels:
        await private_reply(interaction, "Du musst mindestens einen Button angeben.")
        return

    if any(len(label) > 80 for label in labels):
        await private_reply(
            interaction, "Jeder Button-Name darf höchstens 80 Zeichen haben."
        )
        return

    config["support_role_id"] = support_rolle.id
    config["ticket_category_id"] = kategorie.id
    config["ticket_button_labels"] = labels
    save_json(CONFIG_FILE, config)

    embed = discord.Embed(
        title="🎫 KingSnowy Support",
        description=(
            "Wähle einen Button, um ein privates Ticket zu erstellen.\n"
            "Unser Support-Team wird dir so schnell wie möglich helfen."
        ),
        color=discord.Color.blue(),
    )
    embed.add_field(name="Support", value=support_rolle.mention, inline=True)
    embed.add_field(name="Kategorie", value=kategorie.name, inline=True)

    await interaction.response.send_message(
        "✅ Ticket-Panel wurde erstellt. Die Einstellungen wurden gespeichert.",
        embed=embed,
        view=TicketPanelView(),
    )


# =========================================================
# SLASH-BEFEHL: /ticketbearbeiten
# =========================================================

@bot.tree.command(
    name="ticketbearbeiten",
    description="Willkommensnachricht und Ticket-Buttons bearbeiten",
)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(
    titel="Neuer Titel der Willkommensnachricht",
    beschreibung="Neue Beschreibung der Willkommensnachricht",
    button1="Neuer Name des ersten Buttons (optional)",
    button2="Neuer Name des zweiten Buttons (optional)",
    button3="Neuer Name des dritten Buttons (optional)",
    button4="Neuer Name des vierten Buttons (optional)",
    button5="Neuer Name des fünften Buttons (optional)",
)
async def ticketbearbeiten(
    interaction: discord.Interaction,
    titel: str,
    beschreibung: str,
    button1: str = None,
    button2: str = None,
    button3: str = None,
    button4: str = None,
    button5: str = None,
):
    if len(titel) > 256:
        await private_reply(interaction, "Der Titel darf maximal 256 Zeichen haben.")
        return
    if len(beschreibung) > 4000:
        await private_reply(
            interaction, "Die Beschreibung darf maximal 4000 Zeichen haben."
        )
        return

    supplied_buttons = [button1, button2, button3, button4, button5]
    if any(label is not None and len(label.strip()) > 80
           for label in supplied_buttons):
        await private_reply(
            interaction, "Jeder Button-Name darf maximal 80 Zeichen haben."
        )
        return

    config["ticket_welcome_title"] = titel
    config["ticket_welcome_description"] = beschreibung

    # Wenn Button-Namen angegeben wurden, werden die Panel-Buttons
    # durch diese Liste ersetzt. Ohne Button-Angaben bleiben sie unverändert.
    if any(label is not None for label in supplied_buttons):
        labels = [
            label.strip()
            for label in supplied_buttons
            if label and label.strip()
        ]
        if not labels:
            await private_reply(
                interaction, "Mindestens ein Button-Name muss ausgefüllt sein."
            )
            return
        config["ticket_button_labels"] = labels

    save_json(CONFIG_FILE, config)

    embed = discord.Embed(
        title=titel,
        description=beschreibung,
        color=discord.Color.blue(),
    )

    await interaction.response.send_message(
        "✅ Willkommensnachricht gespeichert. Sie gilt für neue Tickets. "
        "Die neuen Button-Namen werden bei einem neu erstellten Ticket-Panel "
        "verwendet.",
        embed=embed,
        ephemeral=True,
    )


# =========================================================
# TICKET-LOG-KANAL
# =========================================================

@bot.tree.command(
    name="ticketlog-set",
    description="Kanal für Ticket-Transkripte festlegen",
)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(channel="Kanal für Transkripte geschlossener Tickets")
async def ticketlog_set(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
):
    config["ticket_log_channel_id"] = channel.id
    save_json(CONFIG_FILE, config)
    await interaction.response.send_message(
        f"✅ Ticket-Log-Kanal: {channel.mention}\n"
        "Dort erscheinen die Transkripte erst nach dem Schließen.",
        ephemeral=True,
    )


# =========================================================
# FEHLERBEHANDLUNG
# =========================================================

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ Du benötigst die Berechtigung „Server verwalten“.")
    elif isinstance(error, commands.CommandNotFound):
        return
    else:
        print(f"Prefix-Fehler: {error}")
        await ctx.send("❌ Es ist ein Fehler aufgetreten.")


@bot.tree.error
async def slash_error(interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        message = "❌ Du benötigst die Berechtigung „Server verwalten“."
    else:
        print(f"Slash-Fehler: {error}")
        message = "❌ Ein Fehler ist aufgetreten. Prüfe die Railway-Logs."

    await private_reply(interaction, message)


# =========================================================
# START
# =========================================================

@bot.event
async def on_ready():
    global views_registered

    if not views_registered:
        bot.add_view(SignInView())
        bot.add_view(TicketControlsView())
        bot.add_view(CloseVoteView())
        bot.add_view(TicketPanelView())
        views_registered = True

    try:
        synced = await bot.tree.sync()
        print(f"{len(synced)} Slash-Befehle synchronisiert.")
    except Exception as error:
        print(f"Slash-Befehle konnten nicht synchronisiert werden: {error}")

    print(f"KingSnowy Bot online: {bot.user}")


bot.run(TOKEN)
