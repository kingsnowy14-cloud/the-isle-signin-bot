
import os
import json
import io
import html
import asyncio
from datetime import datetime, timezone

import discord
from discord.ext import commands
from discord import app_commands


# =========================================================
# GRUNDEINSTELLUNGEN
# =========================================================

TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    raise RuntimeError(
        "Der Bot-Token fehlt! Setze DISCORD_TOKEN in deinen Railway-Variablen."
    )

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

DATA_DIR = "."
SIGNINS_FILE = os.path.join(DATA_DIR, "signins.json")
LANGUAGES_FILE = os.path.join(DATA_DIR, "languages.json")
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
TICKETS_FILE = os.path.join(DATA_DIR, "tickets.json")

DEFAULT_CONFIG = {
    "announcement_channel_id": None,
    "ticket_log_channel_id": None,
    "support_role_id": None,
}

# Verhindert, dass persistente Views bei jedem Reconnect
# mehrfach registriert werden.
views_registered = False


# =========================================================
# JSON-DATEIEN
# =========================================================

def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, data):
    temp_path = path + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=4, ensure_ascii=False)
    os.replace(temp_path, path)


signins = load_json(SIGNINS_FILE, {})
languages = load_json(LANGUAGES_FILE, {})
config = load_json(CONFIG_FILE, DEFAULT_CONFIG.copy())
tickets = load_json(TICKETS_FILE, {})

for key, value in DEFAULT_CONFIG.items():
    config.setdefault(key, value)


def save_signins():
    save_json(SIGNINS_FILE, signins)


def save_languages():
    save_json(LANGUAGES_FILE, languages)


def save_config():
    save_json(CONFIG_FILE, config)


def save_tickets():
    save_json(TICKETS_FILE, tickets)


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

    if isinstance(channel, discord.TextChannel):
        return channel

    return None


async def send_announcement(embed):
    """Sendet Anmeldungen immer in den konfigurierten Log-Kanal."""
    channel = await get_text_channel(
        config.get("announcement_channel_id")
    )

    if channel is None:
        print("Anmeldungs-Log nicht eingerichtet oder nicht erreichbar.")
        return

    try:
        await channel.send(embed=embed)
    except discord.HTTPException as error:
        print(f"Anmeldungs-Log konnte nicht gesendet werden: {error}")


async def send_ticket_log(channel, content=None, embed=None, file=None):
    """Sendet Nachrichten in den konfigurierten Ticket-Log-Kanal."""
    log_channel = await get_text_channel(
        config.get("ticket_log_channel_id")
    )

    if log_channel is None:
        return

    try:
        await log_channel.send(
            content=content,
            embed=embed,
            file=file,
        )
    except discord.HTTPException as error:
        print(f"Ticket-Log konnte nicht gesendet werden: {error}")


def is_support_member(member, channel=None):
    """Erlaubt Support-Rollen oder Mitglieder mit Verwaltungsrechten."""
    if not isinstance(member, discord.Member):
        return False

    if member.guild_permissions.administrator:
        return True

    if member.guild_permissions.manage_channels:
        return True

    role_id = config.get("support_role_id")

    if role_id:
        if any(role.id == int(role_id) for role in member.roles):
            return True

    # Alternativ kann die Berechtigung zum Verwalten von Nachrichten
    # als Support-Berechtigung verwendet werden.
    if member.guild_permissions.manage_messages:
        return True

    return False


async def ticket_ephemeral(interaction, message):
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


# =========================================================
# TICKET-KANALDATEN
# =========================================================

def make_ticket_topic(owner_id, claimed_id=None,
                      close_owner=0, close_support=0):
    return (
        f"ks_ticket|owner:{owner_id}"
        f"|claimed:{claimed_id or 0}"
        f"|close_owner:{close_owner}"
        f"|close_support:{close_support}"
    )


def parse_ticket_topic(topic):
    if not topic or not topic.startswith("ks_ticket|"):
        return None

    result = {}

    for part in topic.split("|")[1:]:
        if ":" not in part:
            continue

        key, value = part.split(":", 1)

        try:
            result[key] = int(value)
        except ValueError:
            continue

    return result


async def update_ticket_topic(channel, **updates):
    data = parse_ticket_topic(channel.topic)

    if data is None:
        return None

    data.update(updates)

    new_topic = make_ticket_topic(
        owner_id=data.get("owner", 0),
        claimed_id=data.get("claimed", 0),
        close_owner=data.get("close_owner", 0),
        close_support=data.get("close_support", 0),
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
        required=True,
    )

    skin = discord.ui.TextInput(
        label="Skin",
        placeholder="Enter your skin",
        max_length=100,
        required=True,
    )

    location = discord.ui.TextInput(
        label="Current location",
        placeholder="Where are you currently located?",
        max_length=200,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)

        signins[user_id] = {
            "dino": self.dinosaur.value,
            "skin": self.skin.value,
            "location": self.location.value,
            "username": interaction.user.name,
            "guild_id": interaction.guild_id,
            "signed_in_at": datetime.now(timezone.utc).isoformat(),
        }

        save_signins()

        # Der öffentliche Anmeldungs-Log ist IMMER auf Englisch.
        embed = discord.Embed(
            title="🟢 Player signed in",
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc),
        )

        embed.add_field(
            name="Player",
            value=interaction.user.mention,
            inline=False,
        )
        embed.add_field(
            name="Dinosaur",
            value=self.dinosaur.value,
            inline=True,
        )
        embed.add_field(
            name="Skin",
            value=self.skin.value,
            inline=True,
        )
        embed.add_field(
            name="Current location",
            value=self.location.value,
            inline=False,
        )

        await send_announcement(embed)

        await interaction.response.send_message(
            "✅ Du bist angemeldet! Deine Anmeldung wurde gespeichert.",
            ephemeral=True,
        )


class LanguageSelect(discord.ui.Select):

    def __init__(self):
        options = [
            discord.SelectOption(
                label="English",
                value="en",
                emoji="🇬🇧",
            ),
            discord.SelectOption(
                label="Deutsch",
                value="de",
                emoji="🇩🇪",
            ),
            discord.SelectOption(
                label="Čeština",
                value="cs",
                emoji="🇨🇿",
            ),
        ]

        super().__init__(
            placeholder="Choose your language / Sprache auswählen",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="kingsnowy:signin:language",
        )

    async def callback(self, interaction: discord.Interaction):
        languages[str(interaction.user.id)] = self.values[0]
        save_languages()

        labels = {
            "en": "Language set to English.",
            "de": "Sprache auf Deutsch eingestellt.",
            "cs": "Jazyk byl nastaven na češtinu.",
        }

        await interaction.response.send_message(
            labels.get(self.values[0], "Language updated."),
            ephemeral=True,
        )


class SignInView(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(LanguageSelect())

    @discord.ui.button(
        label="Sign In",
        emoji="🟢",
        style=discord.ButtonStyle.success,
        custom_id="kingsnowy:signin:open",
    )
    async def sign_in(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        user_id = str(interaction.user.id)

        if user_id in signins:
            await interaction.response.send_message(
                "Du bist bereits angemeldet. Melde dich zuerst ab.",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(SignInModal())

    @discord.ui.button(
        label="Sign Out",
        emoji="🔴",
        style=discord.ButtonStyle.danger,
        custom_id="kingsnowy:signin:close",
    )
    async def sign_out(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        user_id = str(interaction.user.id)

        if user_id not in signins:
            await interaction.response.send_message(
                "Du bist aktuell nicht angemeldet.",
                ephemeral=True,
            )
            return

        saved = signins.pop(user_id)
        save_signins()

        # Auch Abmeldungen werden immer auf Englisch geloggt.
        embed = discord.Embed(
            title="🔴 Player signed out",
            color=discord.Color.red(),
            timestamp=datetime.now(timezone.utc),
        )

        embed.add_field(
            name="Player",
            value=interaction.user.mention,
            inline=False,
        )
        embed.add_field(
            name="Dinosaur",
            value=saved.get("dino", "Not specified"),
            inline=True,
        )
        embed.add_field(
            name="Skin",
            value=saved.get("skin", "Not specified"),
            inline=True,
        )

        await send_announcement(embed)

        await interaction.response.send_message(
            "✅ Du wurdest abgemeldet.",
            ephemeral=True,
        )


@bot.command(name="anmeldepannel", aliases=["anmeldepanel"])
@commands.has_permissions(manage_guild=True)
async def anmeldepanel(ctx):
    embed = discord.Embed(
        title="❄️ The Isle – Player Sign-In",
        description=(
            "Use the buttons below to sign in or sign out.\n\n"
            "Wähle zuerst deine Sprache aus und nutze danach "
            "die Anmelde-Buttons."
        ),
        color=discord.Color.blue(),
    )

    await ctx.send(embed=embed, view=SignInView())


@bot.tree.command(
    name="kanal-set",
    description="Set the channel for player sign-in logs",
)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(channel="The channel for sign-in and sign-out logs")
async def kanal_set(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
):
    config["announcement_channel_id"] = channel.id
    save_config()

    await interaction.response.send_message(
        f"✅ Anmeldungs-Log eingestellt: {channel.mention}\n"
        "Die fertigen Anmeldungen und Abmeldungen werden dort "
        "immer auf Englisch veröffentlicht.",
        ephemeral=True,
    )


# =========================================================
# TICKET-NACHRICHT BEARBEITEN
# =========================================================

class EditTicketMessageModal(discord.ui.Modal):

    def __init__(self, message_id, current_embed):
        super().__init__(title="Ticket-Nachricht bearbeiten")

        self.message_id = message_id

        current_title = ""
        current_description = ""

        if current_embed is not None:
            current_title = current_embed.title or ""
            current_description = current_embed.description or ""

        self.new_title = discord.ui.TextInput(
            label="Neuer Titel",
            placeholder="Leer lassen, um den Titel beizubehalten",
            default=current_title[:256] if current_title else None,
            max_length=256,
            required=False,
        )

        self.new_description = discord.ui.TextInput(
            label="Neue Beschreibung",
            placeholder="Leer lassen, um die Beschreibung beizubehalten",
            default=(
                current_description[:4000]
                if current_description
                else None
            ),
            style=discord.TextStyle.paragraph,
            max_length=4000,
            required=False,
        )

        self.add_item(self.new_title)
        self.add_item(self.new_description)

    async def on_submit(self, interaction: discord.Interaction):
        if not is_support_member(interaction.user, interaction.channel):
            await interaction.response.send_message(
                "❌ Nur das Support-Team darf diese Nachricht bearbeiten.",
                ephemeral=True,
            )
            return

        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message(
                "Diese Nachricht kann hier nicht bearbeitet werden.",
                ephemeral=True,
            )
            return

        try:
            message = await interaction.channel.fetch_message(
                self.message_id
            )
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            await interaction.response.send_message(
                "❌ Die Nachricht wurde nicht gefunden oder kann nicht "
                "bearbeitet werden.",
                ephemeral=True,
            )
            return

        if message.author.id != bot.user.id:
            await interaction.response.send_message(
                "❌ Ich kann nur meine eigenen Bot-Nachrichten bearbeiten.",
                ephemeral=True,
            )
            return

        if message.embeds:
            embed = discord.Embed.from_dict(
                message.embeds[0].to_dict()
            )
        else:
            embed = discord.Embed(
                color=discord.Color.blue()
            )

        if self.new_title.value.strip():
            embed.title = self.new_title.value.strip()

        if self.new_description.value.strip():
            embed.description = self.new_description.value.strip()

        try:
            await message.edit(embed=embed)
        except discord.HTTPException as error:
            await interaction.response.send_message(
                f"❌ Die Nachricht konnte nicht bearbeitet werden: {error}",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            "✅ Die Ticket-Bot-Nachricht wurde bearbeitet.",
            ephemeral=True,
        )


# =========================================================
# TICKET-TRANSKRIPT
# =========================================================

async def create_ticket_transcript(channel):
    lines = [
        "<!DOCTYPE html>",
        "<html><head><meta charset='utf-8'>",
        "<title>Ticket Transcript</title>",
        """
        <style>
        body { font-family: Arial, sans-serif; margin: 25px; }
        .message { padding: 10px; margin: 10px 0;
                   border-bottom: 1px solid #ddd; }
        .author { font-weight: bold; }
        .time { color: #777; font-size: 12px; }
        .content { margin-top: 5px; white-space: pre-wrap; }
        </style>
        """,
        "</head><body>",
        f"<h1>Ticket Transcript: {html.escape(channel.name)}</h1>",
    ]

    async for message in channel.history(
        limit=None,
        oldest_first=True,
    ):
        timestamp = message.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        author = html.escape(str(message.author))
        content = html.escape(message.content or "")

        lines.append("<div class='message'>")
        lines.append(
            f"<div class='author'>{author}</div>"
        )
        lines.append(
            f"<div class='time'>{html.escape(timestamp)}</div>"
        )
        lines.append(
            f"<div class='content'>{content}</div>"
        )

        for embed in message.embeds:
            if embed.title:
                lines.append(
                    f"<div><b>{html.escape(embed.title)}</b></div>"
                )
            if embed.description:
                lines.append(
                    f"<div class='content'>"
                    f"{html.escape(embed.description)}</div>"
                )

        if message.attachments:
            for attachment in message.attachments:
                lines.append(
                    "<div>Attachment: "
                    + html.escape(attachment.filename)
                    + "</div>"
                )

        lines.append("</div>")

    lines.append("</body></html>")

    content = "\n".join(lines).encode("utf-8")
    return content


# =========================================================
# TICKET SCHLIESSEN
# =========================================================

class TicketCloseVoteView(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Bestätigen als Ticket-Ersteller",
        emoji="👤",
        style=discord.ButtonStyle.primary,
        custom_id="kingsnowy:ticket:close-owner",
    )
    async def owner_vote(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        channel = interaction.channel

        if not isinstance(channel, discord.TextChannel):
            await ticket_ephemeral(interaction, "Ungültiger Ticket-Kanal.")
            return

        data = parse_ticket_topic(channel.topic)

        if data is None:
            await ticket_ephemeral(interaction, "Dieses Ticket ist ungültig.")
            return

        if interaction.user.id != data.get("owner"):
            await ticket_ephemeral(
                interaction,
                "Nur der Ticket-Ersteller kann diese Bestätigung abgeben.",
            )
            return

        await update_ticket_topic(channel, close_owner=1)
        await self.finish_if_approved(interaction, channel)

    @discord.ui.button(
        label="Bestätigen als Support",
        emoji="🛡️",
        style=discord.ButtonStyle.success,
        custom_id="kingsnowy:ticket:close-support",
    )
    async def support_vote(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        channel = interaction.channel

        if not isinstance(channel, discord.TextChannel):
            await ticket_ephemeral(interaction, "Ungültiger Ticket-Kanal.")
            return

        data = parse_ticket_topic(channel.topic)

        if data is None:
            await ticket_ephemeral(interaction, "Dieses Ticket ist ungültig.")
            return

        if not is_support_member(interaction.user, channel):
            await ticket_ephemeral(
                interaction,
                "Nur das Support-Team kann diese Bestätigung abgeben.",
            )
            return

        await update_ticket_topic(channel, close_support=1)
        await self.finish_if_approved(interaction, channel)

    async def finish_if_approved(self, interaction, channel):
        data = parse_ticket_topic(channel.topic)

        if data is None:
            await ticket_ephemeral(interaction, "Ticket-Daten fehlen.")
            return

        if data.get("close_owner") and data.get("close_support"):
            await interaction.response.send_message(
                "✅ Beide Bestätigungen liegen vor. Das Ticket wird geschlossen.",
                ephemeral=True,
            )
            await close_ticket(channel, interaction.user)
            return

        await interaction.response.send_message(
            "✅ Bestätigung gespeichert. Zum Schließen werden beide "
            "Bestätigungen benötigt.",
            ephemeral=True,
        )


async def close_ticket(channel, closed_by):
    data = parse_ticket_topic(channel.topic)

    if data is None:
        return

    transcript_bytes = await create_ticket_transcript(channel)
    filename = f"{channel.name}-transcript.html"

    owner = channel.guild.get_member(data.get("owner", 0))

    # Jede Sendung bekommt eine eigene Datei, damit der Datei-Stream
    # beim Senden an mehrere Empfänger korrekt verwendet wird.
    if owner is not None:
        try:
            await owner.send(
                "Hier ist das Transkript deines geschlossenen Tickets.",
                file=discord.File(
                    io.BytesIO(transcript_bytes),
                    filename=filename,
                ),
            )
        except (discord.Forbidden, discord.HTTPException):
            pass

    log_channel = await get_text_channel(
        config.get("ticket_log_channel_id")
    )

    if log_channel is not None:
        try:
            await log_channel.send(
                f"🔒 Ticket **{channel.name}** wurde von "
                f"{closed_by.mention} geschlossen.",
                file=discord.File(
                    io.BytesIO(transcript_bytes),
                    filename=filename,
                ),
            )
        except discord.HTTPException:
            pass

    tickets.pop(str(channel.id), None)
    save_tickets()

    await channel.delete(
        reason=f"Ticket closed by {closed_by}"
    )


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
        custom_id="kingsnowy:ticket:claim",
    )
    async def claim_ticket(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        channel = interaction.channel

        if not isinstance(channel, discord.TextChannel):
            await ticket_ephemeral(interaction, "Ungültiger Kanal.")
            return

        if not is_support_member(interaction.user, channel):
            await ticket_ephemeral(
                interaction,
                "Nur das Support-Team darf Tickets übernehmen.",
            )
            return

        data = parse_ticket_topic(channel.topic)

        if data is None:
            await ticket_ephemeral(interaction, "Ticket nicht gefunden.")
            return

        if data.get("claimed"):
            await ticket_ephemeral(
                interaction,
                f"Dieses Ticket wurde bereits von <@{data['claimed']}> "
                "übernommen.",
            )
            return

        await update_ticket_topic(channel, claimed=interaction.user.id)

        await interaction.response.send_message(
            f"🛡️ {interaction.user.mention} hat das Ticket übernommen."
        )

    @discord.ui.button(
        label="Übernahme freigeben",
        emoji="↩️",
        style=discord.ButtonStyle.secondary,
        custom_id="kingsnowy:ticket:release",
    )
    async def release_ticket(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        channel = interaction.channel

        if not isinstance(channel, discord.TextChannel):
            await ticket_ephemeral(interaction, "Ungültiger Kanal.")
            return

        if not is_support_member(interaction.user, channel):
            await ticket_ephemeral(
                interaction,
                "Nur das Support-Team darf die Übernahme freigeben.",
            )
            return

        data = parse_ticket_topic(channel.topic)

        if data is None:
            await ticket_ephemeral(interaction, "Ticket nicht gefunden.")
            return

        if data.get("claimed") not in (0, interaction.user.id):
            await ticket_ephemeral(
                interaction,
                "Nur der zuständige Support-Mitarbeiter oder ein "
                "Administrator kann die Übernahme freigeben.",
            )
            if not interaction.user.guild_permissions.administrator:
                return

        await update_ticket_topic(channel, claimed=0)

        await interaction.response.send_message(
            f"↩️ {interaction.user.mention} hat die Ticket-Übernahme "
            "freigegeben."
        )

    @discord.ui.button(
        label="Ticket-Nachricht bearbeiten",
        emoji="✏️",
        style=discord.ButtonStyle.secondary,
        custom_id="kingsnowy:ticket:edit-message",
    )
    async def edit_message(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if not is_support_member(interaction.user, interaction.channel):
            await ticket_ephemeral(
                interaction,
                "Nur das Support-Team darf Ticket-Bot-Nachrichten bearbeiten.",
            )
            return

        current_embed = (
            interaction.message.embeds[0]
            if interaction.message.embeds
            else None
        )

        await interaction.response.send_modal(
            EditTicketMessageModal(
                interaction.message.id,
                current_embed,
            )
        )

    @discord.ui.button(
        label="Schließen anfordern",
        emoji="🔒",
        style=discord.ButtonStyle.danger,
        custom_id="kingsnowy:ticket:request-close",
    )
    async def request_close(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        channel = interaction.channel

        if not isinstance(channel, discord.TextChannel):
            await ticket_ephemeral(interaction, "Ungültiger Kanal.")
            return

        data = parse_ticket_topic(channel.topic)

        if data is None:
            await ticket_ephemeral(interaction, "Ticket nicht gefunden.")
            return

        is_owner = interaction.user.id == data.get("owner")
        is_support = is_support_member(interaction.user, channel)

        if not is_owner and not is_support:
            await ticket_ephemeral(
                interaction,
                "Nur der Ticket-Ersteller oder das Support-Team "
                "kann das Schließen anfordern.",
            )
            return

        # Eine neue Schließanfrage beginnt ohne alte Bestätigungen.
        await update_ticket_topic(
            channel,
            close_owner=0,
            close_support=0,
        )

        embed = discord.Embed(
            title="🔒 Ticket-Schließung",
            description=(
                "Das Ticket soll geschlossen werden.\n\n"
                "Für die endgültige Schließung werden zwei Bestätigungen "
                "benötigt:\n"
                "• vom Ticket-Ersteller\n"
                "• vom Support-Team"
            ),
            color=discord.Color.orange(),
        )

        await interaction.response.send_message(
            embed=embed,
            view=TicketCloseVoteView(),
        )


class TicketControlsViewWithEdit(TicketControlsView):
    """Persistente Ticket-Steuerung inklusive Bearbeiten-Button."""
    pass


# =========================================================
# TICKET ERSTELLEN
# =========================================================

class TicketPanelView(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Ticket erstellen",
        emoji="🎫",
        style=discord.ButtonStyle.primary,
        custom_id="kingsnowy:ticket:create",
    )
    async def create_ticket(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        guild = interaction.guild

        if guild is None:
            await ticket_ephemeral(
                interaction,
                "Tickets können nur auf einem Server erstellt werden.",
            )
            return

        # Prüfen, ob der Nutzer bereits ein Ticket hat.
        for channel in guild.text_channels:
            data = parse_ticket_topic(channel.topic)

            if data and data.get("owner") == interaction.user.id:
                await ticket_ephemeral(
                    interaction,
                    f"Du hast bereits ein offenes Ticket: {channel.mention}",
                )
                return

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=False
            ),
            interaction.user: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_channels=True,
                attach_files=True,
                embed_links=True,
            ),
        }

        support_role_id = config.get("support_role_id")

        if support_role_id:
            role = guild.get_role(int(support_role_id))
            if role is not None:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    attach_files=True,
                    embed_links=True,
                )

        username = "".join(
            char.lower()
            for char in interaction.user.name
            if char.isalnum() or char in "-_"
        )[:30] or "user"

        try:
            channel = await guild.create_text_channel(
                name=f"ticket-{username}",
                overwrites=overwrites,
                topic=make_ticket_topic(interaction.user.id),
                reason=f"Ticket created by {interaction.user}",
            )
        except discord.Forbidden:
            await ticket_ephemeral(
                interaction,
                "❌ Der Bot darf keine Ticket-Kanäle erstellen. "
                "Gib ihm die Berechtigung „Kanäle verwalten“.",
            )
            return
        except discord.HTTPException as error:
            await ticket_ephemeral(
                interaction,
                f"❌ Ticket konnte nicht erstellt werden: {error}",
            )
            return

        tickets[str(channel.id)] = {
            "owner_id": interaction.user.id,
            "guild_id": guild.id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        save_tickets()

        embed = discord.Embed(
            title="🎫 Support-Ticket",
            description=(
                f"Hallo {interaction.user.mention}!\n\n"
                "Beschreibe hier dein Problem möglichst genau.\n"
                "Das Support-Team wird dir so schnell wie möglich helfen.\n\n"
                "Verwende die Buttons unten, um das Ticket zu verwalten."
            ),
            color=discord.Color.blue(),
            timestamp=datetime.now(timezone.utc),
        )

        await channel.send(
            content=interaction.user.mention,
            embed=embed,
            view=TicketControlsViewWithEdit(),
        )

        await send_ticket_log(
            channel,
            content=f"🎫 Neues Ticket: {channel.mention} "
                    f"von {interaction.user.mention}",
        )

        await interaction.response.send_message(
            f"✅ Dein Ticket wurde erstellt: {channel.mention}",
            ephemeral=True,
        )


@bot.hybrid_command(
    name="ticketcreat",
    description="Create the support ticket panel",
)
@commands.has_permissions(manage_guild=True)
async def ticketcreat(ctx):
    embed = discord.Embed(
        title="🎫 KingSnowy Support",
        description=(
            "Du brauchst Hilfe? Erstelle über den Button ein privates "
            "Support-Ticket.\n\n"
            "Bitte beschreibe dein Anliegen nach der Erstellung so genau "
            "wie möglich."
        ),
        color=discord.Color.blue(),
    )

    await ctx.send(embed=embed, view=TicketPanelView())


@bot.tree.command(
    name="ticketlog-set",
    description="Set the channel for ticket logs",
)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(channel="The channel where ticket logs are sent")
async def ticketlog_set(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
):
    config["ticket_log_channel_id"] = channel.id
    save_config()

    await interaction.response.send_message(
        f"✅ Ticket-Log-Kanal eingestellt: {channel.mention}",
        ephemeral=True,
    )


@bot.tree.command(
    name="ticket-support-set",
    description="Set the role allowed to manage support tickets",
)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(role="The support team role")
async def ticket_support_set(
    interaction: discord.Interaction,
    role: discord.Role,
):
    config["support_role_id"] = role.id
    save_config()

    await interaction.response.send_message(
        f"✅ Support-Rolle eingestellt: {role.mention}",
        ephemeral=True,
    )


# =========================================================
# FEHLERMELDUNGEN
# =========================================================

@anmeldepanel.error
async def anmeldepanel_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send(
            "❌ Du benötigst die Berechtigung „Server verwalten“."
        )
    else:
        print(f"Fehler bei anmeldepanel: {error}")
        await ctx.send("❌ Beim Erstellen des Anmelde-Panels ist ein Fehler aufgetreten.")


@ticketcreat.error
async def ticketcreat_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send(
            "❌ Du benötigst die Berechtigung „Server verwalten“."
        )
    else:
        print(f"Fehler bei ticketcreat: {error}")
        await ctx.send("❌ Das Ticket-Panel konnte nicht erstellt werden.")


@bot.tree.error
async def app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError,
):
    if isinstance(error, app_commands.MissingPermissions):
        message = "❌ Du hast nicht die nötigen Berechtigungen."
    else:
        print(f"Slash-Command-Fehler: {error}")
        message = "❌ Beim Ausführen des Befehls ist ein Fehler aufgetreten."

    await ticket_ephemeral(interaction, message)


# =========================================================
# BOT STARTEN
# =========================================================

@bot.event
async def on_ready():
    global views_registered

    if not views_registered:
        bot.add_view(SignInView())
        bot.add_view(TicketPanelView())
        bot.add_view(TicketControlsViewWithEdit())
        bot.add_view(TicketCloseVoteView())
        views_registered = True

    try:
        synced = await bot.tree.sync()
        print(f"{len(synced)} Slash-Commands synchronisiert.")
    except Exception as error:
        print(f"Slash-Commands konnten nicht synchronisiert werden: {error}")

    print(f"Eingeloggt als {bot.user} (ID: {bot.user.id})")
    print("KingSnowy Bot ist online!")


bot.run(TOKEN)
