import os
import re
import json
import html
import io
import discord

from pathlib import Path
from datetime import datetime, timezone
from discord.ext import commands
from discord import app_commands


# ============================================================
# GRUNDEINSTELLUNGEN
# ============================================================

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
PREFIX = "!"

SIGNINS_FILE = Path("signins.json")
LANGUAGES_FILE = Path("languages.json")
CONFIG_FILE = Path("config.json")
TICKETS_FILE = Path("tickets.json")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix=PREFIX, intents=intents)


# ============================================================
# JSON-HILFSFUNKTIONEN
# ============================================================

def load_json(path, default=None):
    if default is None:
        default = {}

    if not path.exists():
        return default.copy()

    try:
        with path.open("r", encoding="utf-8") as file:
            result = json.load(file)
            return result if isinstance(result, type(default)) else default.copy()
    except (json.JSONDecodeError, OSError):
        return default.copy()


def save_json(path, data):
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=4)


signins = load_json(SIGNINS_FILE)
languages = load_json(LANGUAGES_FILE)
config = load_json(CONFIG_FILE)
tickets = load_json(TICKETS_FILE)


# ============================================================
# SPRACHEN
# ============================================================

TEXTS = {
    "en": {
        "signed_in": "Player signed in",
        "signed_out": "Player signed out",
        "player": "Player",
        "dino": "Dinosaur",
        "skin": "Skin",
        "location": "Current location",
        "unknown": "Not specified",
        "not_signed_in": "You are not currently signed in.",
        "signout_done": "You have successfully signed out.",
        "signin_done": "You have successfully signed in.",
        "modal_title": "The Isle - Player Sign In",
        "player_name": "Player name",
        "dino_label": "Dinosaur",
        "skin_label": "Skin",
        "location_label": "Current location",
    },
    "de": {
        "signed_in": "Spieler angemeldet",
        "signed_out": "Spieler abgemeldet",
        "player": "Spieler",
        "dino": "Dinosaurier",
        "skin": "Skin",
        "location": "Aktueller Standort",
        "unknown": "Nicht angegeben",
        "not_signed_in": "Du bist derzeit nicht angemeldet.",
        "signout_done": "Du wurdest erfolgreich abgemeldet.",
        "signin_done": "Du wurdest erfolgreich angemeldet.",
        "modal_title": "The Isle - Spieler anmelden",
        "player_name": "Spielername",
        "dino_label": "Dinosaurier",
        "skin_label": "Skin",
        "location_label": "Aktueller Standort",
    },
    "cs": {
        "signed_in": "Hráč přihlášen",
        "signed_out": "Hráč odhlášen",
        "player": "Hráč",
        "dino": "Dinosaurus",
        "skin": "Vzhled",
        "location": "Aktuální poloha",
        "unknown": "Neuvedeno",
        "not_signed_in": "Momentálně nejsi přihlášen.",
        "signout_done": "Úspěšně ses odhlásil.",
        "signin_done": "Úspěšně ses přihlásil.",
        "modal_title": "The Isle - Přihlášení hráče",
        "player_name": "Jméno hráče",
        "dino_label": "Dinosaurus",
        "skin_label": "Vzhled",
        "location_label": "Aktuální poloha",
    },
}


def get_language(user_id):
    return languages.get(str(user_id), "en")


# ============================================================
# ANMELDEPANEL - BESCHREIBUNG
# ============================================================

SIGNIN_DESCRIPTION = (
    "🦖 **THE ISLE - PLAYER SIGN-IN**\n\n"
    "Welcome to the player sign-in panel! "
    "Willkommen beim Spieler-Anmeldepanel! "
    "Vítej v registračním panelu hráčů!\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "🇬🇧 **ENGLISH - HOW TO SIGN IN**\n\n"
    "1. Select your language below.\n"
    "2. Click **Sign In**.\n"
    "3. Enter your dinosaur, skin and current location.\n"
    "4. Submit the form. Your registration will be posted "
    "in the designated announcement channel.\n"
    "5. When you finish playing, click **Sign Out**.\n\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "🇩🇪 **DEUTSCH - SO MELDEST DU DICH AN**\n\n"
    "1. Wähle unten deine Sprache aus.\n"
    "2. Klicke auf **Sign In**.\n"
    "3. Gib deinen Dinosaurier, Skin und Standort ein.\n"
    "4. Sende das Formular ab. Deine Anmeldung wird im "
    "festgelegten Anmeldekanal veröffentlicht.\n"
    "5. Wenn du fertig bist, klicke auf **Sign Out**.\n\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "🇨🇿 **ČEŠTINA - JAK SE PŘIHLÁSIT**\n\n"
    "1. Vyber si jazyk v nabídce níže.\n"
    "2. Klikni na **Sign In**.\n"
    "3. Vyplň dinosaura, vzhled a aktuální polohu.\n"
    "4. Odešli formulář. Registrace bude zveřejněna "
    "v určeném registračním kanálu.\n"
    "5. Po skončení hraní klikni na **Sign Out**.\n\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "🌐 Choose your language / Wähle deine Sprache / Vyber jazyk\n\n"
    "**Created by KingSnowy**"
)


# ============================================================
# ANMELDEFORMULAR
# ============================================================

class SignInModal(discord.ui.Modal):

    def __init__(self):
        super().__init__(title="The Isle - Player Sign In")

        self.dino = discord.ui.TextInput(
            label="Dinosaur",
            placeholder="e.g. Deinosuchus",
            max_length=80,
        )
        self.skin = discord.ui.TextInput(
            label="Skin",
            placeholder="Enter your dinosaur skin",
            max_length=100,
        )
        self.location = discord.ui.TextInput(
            label="Current location",
            placeholder="Where are you currently?",
            max_length=150,
        )

        self.add_item(self.dino)
        self.add_item(self.skin)
        self.add_item(self.location)

    async def on_submit(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        language = get_language(user_id)
        text = TEXTS.get(language, TEXTS["en"])

        signins[user_id] = {
            "discord_name": str(interaction.user),
            "discord_id": interaction.user.id,
            "dino": str(self.dino.value),
            "skin": str(self.skin.value),
            "location": str(self.location.value),
            "status": "online",
            "aktualisiert_am": datetime.now(timezone.utc).isoformat(),
        }
        save_json(SIGNINS_FILE, signins)

        embed = discord.Embed(
            title=f"🟢 {text['signed_in']}",
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(
            name=text["player"],
            value=interaction.user.mention,
            inline=False,
        )
        embed.add_field(name=text["dino"], value=self.dino.value, inline=True)
        embed.add_field(name=text["skin"], value=self.skin.value, inline=True)
        embed.add_field(
            name=text["location"],
            value=self.location.value,
            inline=False,
        )

        channel_id = config.get("announcement_channel_id")
        channel = bot.get_channel(int(channel_id)) if channel_id else None

        if channel:
            await channel.send(embed=embed)

        await interaction.response.send_message(
            text["signin_done"],
            ephemeral=True,
        )


# ============================================================
# SPRACHAUSWAHL
# ============================================================

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
            placeholder="🌐 Choose your language / Sprache auswählen",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="isle:language",
        )

    async def callback(self, interaction: discord.Interaction):
        languages[str(interaction.user.id)] = self.values[0]
        save_json(LANGUAGES_FILE, languages)

        labels = {
            "en": "Your language is now English.",
            "de": "Deine Sprache ist jetzt Deutsch.",
            "cs": "Tvým jazykem je nyní čeština.",
        }

        await interaction.response.send_message(
            labels[self.values[0]],
            ephemeral=True,
        )


# ============================================================
# ANMELDE- UND ABMELDEBUTTONS
# ============================================================

class SignInView(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Sign In",
        emoji="🟢",
        style=discord.ButtonStyle.success,
        custom_id="isle:signin",
    )
    async def sign_in(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await interaction.response.send_modal(SignInModal())

    @discord.ui.button(
        label="Sign Out",
        emoji="🔴",
        style=discord.ButtonStyle.danger,
        custom_id="isle:signout",
    )
    async def sign_out(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        user_id = str(interaction.user.id)
        language = get_language(user_id)
        text = TEXTS.get(language, TEXTS["en"])

        if user_id not in signins or signins[user_id].get("status") != "online":
            await interaction.response.send_message(
                text["not_signed_in"],
                ephemeral=True,
            )
            return

        signins[user_id]["status"] = "offline"
        signins[user_id]["aktualisiert_am"] = (
            datetime.now(timezone.utc).isoformat()
        )
        save_json(SIGNINS_FILE, signins)

        embed = discord.Embed(
            title=f"🔴 {text['signed_out']}",
            color=discord.Color.red(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(
            name=text["player"],
            value=interaction.user.mention,
            inline=False,
        )
        embed.add_field(
            name=text["dino"],
            value=signins[user_id].get("dino", text["unknown"]),
            inline=True,
        )

        channel_id = config.get("announcement_channel_id")
        channel = bot.get_channel(int(channel_id)) if channel_id else None

        if channel:
            await channel.send(embed=embed)

        await interaction.response.send_message(
            text["signout_done"],
            ephemeral=True,
        )

    @discord.ui.select(
        cls=LanguageSelect,
        custom_id="isle:language_select",
    )
    async def language_select(
        self,
        interaction: discord.Interaction,
        select: LanguageSelect,
    ):
        # Die Auswahl wird durch LanguageSelect.callback verarbeitet.
        pass


# ============================================================
# ANMELDEPANEL ERSTELLEN
# ============================================================

@bot.command(name="anmeldepannel", aliases=["anmeldepanel"])
@commands.has_permissions(manage_guild=True)
async def anmeldepannel(ctx):
    embed = discord.Embed(
        description=SIGNIN_DESCRIPTION,
        color=discord.Color.from_rgb(70, 170, 220),
    )
    embed.set_footer(text="The Isle • Player Sign-In • KingSnowy")

    await ctx.send(embed=embed, view=SignInView())


@bot.tree.command(
    name="kanal-set",
    description="Set the channel for player sign-in announcements",
)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(kanal="Channel for sign-in and sign-out announcements")
async def kanal_set(
    interaction: discord.Interaction,
    kanal: discord.TextChannel,
):
    config["announcement_channel_id"] = kanal.id
    save_json(CONFIG_FILE, config)

    await interaction.response.send_message(
        f"Anmelde- und Abmeldeankündigungen werden jetzt in {kanal.mention} "
        "veröffentlicht.",
        ephemeral=True,
    )


# ============================================================
# TICKET-HILFSFUNKTIONEN
# ============================================================

def topic_value(channel, key, default="0"):
    topic = channel.topic or ""
    match = re.search(
        rf"(?:^|\s){re.escape(key)}:([^\s]+)",
        topic,
    )
    return match.group(1) if match else default


def update_topic(channel, **values):
    data = {}

    for item in (channel.topic or "").split():
        if ":" in item:
            key, value = item.split(":", 1)
            data[key] = value

    data.update({key: str(value) for key, value in values.items()})
    return " ".join(f"{key}:{value}" for key, value in data.items())


def get_support_role_id(channel):
    value = topic_value(channel, "role")
    return int(value) if value.isdigit() else None


def get_ticket_owner_id(channel):
    value = topic_value(channel, "owner")
    return int(value) if value.isdigit() else None


def is_support_member(member, channel):
    role_id = get_support_role_id(channel)
    return bool(
        role_id
        and isinstance(member, discord.Member)
        and any(role.id == role_id for role in member.roles)
    )


async def ticket_ephemeral(interaction, message):
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


# ============================================================
# TICKET-TRANSKRIPT
# ============================================================

async def create_ticket_transcript(channel):
    lines = [
        "<!DOCTYPE html>",
        '<html lang="en"><head><meta charset="utf-8">',
        f"<title>Transcript - {html.escape(channel.name)}</title>",
        "<style>",
        "body{font-family:Arial,sans-serif;background:#202225;color:#eee;"
        "padding:24px}",
        ".message{padding:12px;margin:10px 0;background:#2f3136;"
        "border-radius:8px}",
        ".meta{color:#aaa;font-size:12px;margin-bottom:6px}",
        "a{color:#00aff4}",
        "</style></head><body>",
        f"<h1>Ticket Transcript: {html.escape(channel.name)}</h1>",
        f"<p>Server: {html.escape(channel.guild.name)}</p>",
    ]

    async for message in channel.history(limit=None, oldest_first=True):
        timestamp = message.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        author = html.escape(str(message.author))
        content = html.escape(message.content or "").replace("\n", "<br>")

        lines.append(
            f'<div class="message"><div class="meta">'
            f"{author} • {timestamp}</div><div>{content}</div>"
        )

        for attachment in message.attachments:
            url = html.escape(attachment.url, quote=True)
            name = html.escape(attachment.filename)
            lines.append(f'<p><a href="{url}">{name}</a></p>')

        for embed in message.embeds:
            if embed.title:
                lines.append(f"<p><b>{html.escape(embed.title)}</b></p>")
            if embed.description:
                lines.append(f"<p>{html.escape(embed.description)}</p>")

        lines.append("</div>")

    lines.append("</body></html>")
    content = "\n".join(lines)

    return discord.File(
        io.BytesIO(content.encode("utf-8")),
        filename=f"transkript-{channel.id}.html",
    )


async def close_ticket(channel, reason, closed_by):
    owner_id = get_ticket_owner_id(channel)
    guild = channel.guild

    transcript = await create_ticket_transcript(channel)

    if owner_id:
        try:
            owner = await bot.fetch_user(owner_id)
            await owner.send(
                f"Dein Ticket **{channel.name}** wurde geschlossen.\n"
                f"Grund: {reason}\n"
                "Hier ist dein HTML-Transkript:",
                file=transcript,
            )
        except (discord.Forbidden, discord.HTTPException):
            pass

    log_channel_id = config.get("ticket_log_channel_id")
    log_channel = (
        bot.get_channel(int(log_channel_id))
        if log_channel_id
        else None
    )

    if log_channel:
        log_embed = discord.Embed(
            title="Ticket geschlossen",
            description=(
                f"**Ticket:** {channel.name}\n"
                f"**Geschlossen von:** {closed_by.mention}\n"
                f"**Grund:** {reason}"
            ),
            color=discord.Color.red(),
            timestamp=datetime.now(timezone.utc),
        )
        await log_channel.send(embed=log_embed, file=transcript)

    try:
        await channel.delete(reason=reason)
    except discord.HTTPException:
        pass


# ============================================================
# TICKET-NACHRICHT BEARBEITEN
# ============================================================

class EditTicketMessageModal(discord.ui.Modal, title="Bot-Nachricht bearbeiten"):

    message_id = discord.ui.TextInput(
        label="Nachrichten-ID",
        placeholder="ID der Bot-Nachricht",
        max_length=25,
    )

    new_text = discord.ui.TextInput(
        label="Neuer Nachrichtentext",
        style=discord.TextStyle.paragraph,
        max_length=2000,
    )

    async def on_submit(self, interaction):
        channel = interaction.channel

        if not is_support_member(interaction.user, channel):
            await interaction.response.send_message(
                "Nur das Support-Team darf Bot-Nachrichten bearbeiten.",
                ephemeral=True,
            )
            return

        try:
            message = await channel.fetch_message(int(self.message_id.value))
        except (ValueError, discord.NotFound, discord.Forbidden):
            await interaction.response.send_message(
                "Nachricht nicht gefunden oder kein Zugriff.",
                ephemeral=True,
            )
            return

        if message.author.id != bot.user.id:
            await interaction.response.send_message(
                "Ich kann nur Nachrichten bearbeiten, die der Bot selbst "
                "geschrieben hat.",
                ephemeral=True,
            )
            return

        await message.edit(content=self.new_text.value)
        await interaction.response.send_message(
            "Bot-Nachricht bearbeitet.",
            ephemeral=True,
        )


# ============================================================
# TICKET-SCHLIESSUNGSABSTIMMUNG
# ============================================================

class TicketCloseVoteView(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Ja, schließen",
        style=discord.ButtonStyle.success,
        custom_id="kingsnowy:ticket:vote-yes",
    )
    async def vote_yes(self, interaction, button):
        channel = interaction.channel
        owner_id = get_ticket_owner_id(channel)
        support = is_support_member(interaction.user, channel)

        if interaction.user.id != owner_id and not support:
            await ticket_ephemeral(
                interaction,
                "Nur der Ticket-Ersteller oder das Support-Team darf abstimmen.",
            )
            return

        if interaction.user.id == owner_id:
            channel_topic = update_topic(channel, **{"owner-yes": 1})
        else:
            channel_topic = update_topic(channel, **{"support-yes": 1})

        await channel.edit(topic=channel_topic)

        owner_yes = topic_value(channel, "owner-yes") == "1"
        support_yes = topic_value(channel, "support-yes") == "1"

        if owner_yes and support_yes:
            await interaction.response.send_message(
                "Beide Seiten haben zugestimmt. Das Ticket wird geschlossen."
            )
            await close_ticket(channel, "Beide Seiten haben zugestimmt", interaction.user)
            return

        await interaction.response.send_message(
            "Deine Zustimmung wurde gespeichert. Die andere Seite muss ebenfalls "
            "zustimmen.",
            ephemeral=True,
        )

    @discord.ui.button(
        label="Nein, abbrechen",
        style=discord.ButtonStyle.danger,
        custom_id="kingsnowy:ticket:vote-no",
    )
    async def vote_no(self, interaction, button):
        channel = interaction.channel
        owner_id = get_ticket_owner_id(channel)
        support = is_support_member(interaction.user, channel)

        if interaction.user.id != owner_id and not support:
            await ticket_ephemeral(
                interaction,
                "Nur der Ticket-Ersteller oder das Support-Team darf abstimmen.",
            )
            return

        topic = update_topic(
            channel,
            **{
                "owner-yes": 0,
                "support-yes": 0,
                "close-request": 0,
            },
        )
        await channel.edit(topic=topic)

        await interaction.response.send_message(
            f"Der Schließungsantrag wurde von {interaction.user.mention} "
            "abgelehnt. Das Ticket bleibt offen."
        )


# ============================================================
# TICKET-STEUERUNG
# ============================================================

class TicketControlsView(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Ticket übernehmen",
        emoji="🙋",
        style=discord.ButtonStyle.primary,
        custom_id="kingsnowy:ticket:claim",
    )
    async def claim(self, interaction, button):
        channel = interaction.channel

        if not is_support_member(interaction.user, channel):
            await ticket_ephemeral(interaction, "Nur Support darf Tickets übernehmen.")
            return

        if topic_value(channel, "claimed") != "0":
            await ticket_ephemeral(interaction, "Dieses Ticket wurde bereits übernommen.")
            return

        topic = update_topic(
            channel,
            claimed=interaction.user.id,
        )
        await channel.edit(topic=topic)

        await interaction.response.send_message(
            f"🙋 {interaction.user.mention} hat dieses Ticket übernommen."
        )

    @discord.ui.button(
        label="Ticket freigeben",
        emoji="↩️",
        style=discord.ButtonStyle.secondary,
        custom_id="kingsnowy:ticket:release",
    )
    async def release(self, interaction, button):
        channel = interaction.channel
        claimer = topic_value(channel, "claimed")

        if claimer == "0":
            await ticket_ephemeral(interaction, "Dieses Ticket ist nicht übernommen.")
            return

        if str(interaction.user.id) != claimer:
            await ticket_ephemeral(
                interaction,
                "Nur die Person, die das Ticket übernommen hat, darf es freigeben.",
            )
            return

        await channel.edit(topic=update_topic(channel, claimed=0))
        await interaction.response.send_message(
            f"↩️ {interaction.user.mention} hat das Ticket freigegeben."
        )

    @discord.ui.button(
        label="Ticket direkt schließen",
        emoji="🔒",
        style=discord.ButtonStyle.danger,
        custom_id="kingsnowy:ticket:close",
    )
    async def close(self, interaction, button):
        if not is_support_member(interaction.user, interaction.channel):
            await ticket_ephemeral(
                interaction,
                "Nur das Support-Team darf ein Ticket direkt schließen.",
            )
            return

        await interaction.response.send_message(
            "Das Ticket wird geschlossen."
        )
        await close_ticket(
            interaction.channel,
            "Vom Support direkt geschlossen",
            interaction.user,
        )

    @discord.ui.button(
        label="Schließung beantragen",
        emoji="🗳️",
        style=discord.ButtonStyle.secondary,
        custom_id="kingsnowy:ticket:request-close",
    )
    async def request_close(self, interaction, button):
        channel = interaction.channel

        if topic_value(channel, "close-request") == "1":
            await ticket_ephemeral(
                interaction,
                "Es gibt bereits einen offenen Schließungsantrag.",
            )
            return

        topic = update_topic(
            channel,
            **{
                "close-request": 1,
                "owner-yes": 0,
                "support-yes": 0,
            },
        )
        await channel.edit(topic=topic)

        embed = discord.Embed(
            title="Ticket schließen?",
            description=(
                "Das Ticket wird nur geschlossen, wenn sowohl der Ersteller "
                "als auch das Support-Team zustimmen.\n"
                "Ein Nein bricht den Antrag ab."
            ),
            color=discord.Color.orange(),
        )

        await interaction.response.send_message(
            embed=embed,
            view=TicketCloseVoteView(),
        )

    @discord.ui.button(
        label="Bot-Nachricht bearbeiten",
        emoji="✏️",
        style=discord.ButtonStyle.secondary,
        custom_id="kingsnowy:ticket:edit-message",
    )
    async def edit_message(self, interaction, button):
        if not is_support_member(interaction.user, interaction.channel):
            await ticket_ephemeral(
                interaction,
                "Nur das Support-Team darf Bot-Nachrichten bearbeiten.",
            )
            return

        await interaction.response.send_modal(EditTicketMessageModal())


# ============================================================
# TICKET-PANEL UND TICKET-ERSTELLUNG
# ============================================================

class TicketPanelView(discord.ui.View):

    def __init__(self, ticket_name="Support-Ticket"):
        super().__init__(timeout=None)
        self.ticket_name = ticket_name

    @discord.ui.button(
        label="Ticket erstellen",
        emoji="🎫",
        style=discord.ButtonStyle.success,
        custom_id="kingsnowy:ticket:create",
    )
    async def create_ticket(self, interaction, button):
        panel_key = str(interaction.message.id)
        settings = tickets.get(panel_key)

        if not settings:
            await ticket_ephemeral(
                interaction,
                "Diese Ticket-Konfiguration wurde nicht gefunden. "
                "Bitte erstelle das Panel erneut.",
            )
            return

        guild = interaction.guild
        owner = interaction.user
        role_id = int(settings["role_id"])
        support_role = guild.get_role(role_id)

        if support_role is None:
            await ticket_ephemeral(interaction, "Die konfigurierte Support-Rolle fehlt.")
            return

        category_id = settings.get("category_id")
        category = guild.get_channel(int(category_id)) if category_id else None

        number_key = str(guild.id)
        number = int(settings.get("next_number", 1))
        settings["next_number"] = number + 1
        tickets[panel_key] = settings
        save_json(TICKETS_FILE, tickets)

        slug = re.sub(
            r"[^a-z0-9-]+",
            "-",
            settings.get("ticket_name", "ticket").lower(),
        ).strip("-")[:35] or "ticket"

        channel_name = f"{slug}-{number:04d}"

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=False,
            ),
            owner: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
            ),
            support_role: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_messages=True,
                attach_files=True,
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_channels=True,
                attach_files=True,
            ),
        }

        try:
            channel = await guild.create_text_channel(
                name=channel_name,
                category=category,
                overwrites=overwrites,
                topic=(
                    f"owner:{owner.id} role:{support_role.id} "
                    "claimed:0 close-request:0 owner-yes:0 support-yes:0"
                ),
                reason=f"Ticket erstellt von {owner}",
            )
        except discord.HTTPException:
            await ticket_ephemeral(
                interaction,
                "Das Ticket konnte nicht erstellt werden. Prüfe die Bot-Rechte.",
            )
            return

        embed = discord.Embed(
            title=f"🎫 {settings.get('ticket_name', 'Support-Ticket')} #{number:04d}",
            description=(
                f"Hallo {owner.mention}!\n\n"
                f"Beschreibe hier dein Anliegen. {support_role.mention} "
                "wird dir helfen.\n\n"
                "Nutze die Buttons unten, um das Ticket zu verwalten."
            ),
            color=discord.Color.blurple(),
            timestamp=datetime.now(timezone.utc),
        )

        await channel.send(
            content=f"{owner.mention} {support_role.mention}",
            embed=embed,
            view=TicketControlsView(),
        )

        await interaction.response.send_message(
            f"Dein Ticket wurde erstellt: {channel.mention}",
            ephemeral=True,
        )


@bot.tree.command(
    name="ticketcreat",
    description="Create a ticket panel",
)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(
    ping_rolle="Support-Rolle, die bei neuen Tickets benachrichtigt wird",
    ticket_name="Name des Tickets und des Panels",
    kategorie="Optionale Kategorie für neue Tickets",
)
async def ticketcreat(
    interaction: discord.Interaction,
    ping_rolle: discord.Role,
    ticket_name: app_commands.Range[str, 1, 80],
    kategorie: discord.CategoryChannel = None,
):
    if ping_rolle >= interaction.guild.me.top_role:
        await interaction.response.send_message(
            "Die Support-Rolle muss in der Rollenliste unter der höchsten "
            "Bot-Rolle stehen.",
            ephemeral=True,
        )
        return

    embed = discord.Embed(
        title=f"🎫 {ticket_name}",
        description=(
            "Benötigst du Hilfe? Erstelle mit dem Button unten ein privates "
            "Ticket.\n\n"
            f"Support-Rolle: {ping_rolle.mention}"
        ),
        color=discord.Color.blurple(),
    )

    view = TicketPanelView(ticket_name)
    await interaction.response.send_message(embed=embed, view=view)
    message = await interaction.original_response()

    tickets[str(message.id)] = {
        "guild_id": interaction.guild.id,
        "role_id": ping_rolle.id,
        "category_id": kategorie.id if kategorie else None,
        "ticket_name": ticket_name,
        "next_number": 1,
    }
    save_json(TICKETS_FILE, tickets)


@bot.tree.command(
    name="ticketlog-set",
    description="Set the channel for ticket logs and transcripts",
)
@app_commands.checks.has_permissions(manage_guild=True)
async def ticketlog_set(
    interaction: discord.Interaction,
    kanal: discord.TextChannel,
):
    config["ticket_log_channel_id"] = kanal.id
    save_json(CONFIG_FILE, config)

    await interaction.response.send_message(
        f"Ticket-Protokolle werden jetzt in {kanal.mention} gespeichert.",
        ephemeral=True,
    )


# ============================================================
# FEHLERBEHANDLUNG
# ============================================================

@bot.tree.error
async def on_app_command_error(interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        message = "Du hast nicht die erforderlichen Berechtigungen."
    else:
        print(f"Slash-Command-Fehler: {repr(error)}")
        message = "Der Befehl ist fehlgeschlagen. Prüfe die Railway-Logs."

    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.HTTPException:
        pass


@bot.event
async def on_ready():
    bot.add_view(SignInView())
    bot.add_view(TicketControlsView())
    bot.add_view(TicketCloseVoteView())

    for panel_id, settings in tickets.items():
        try:
            bot.add_view(
                TicketPanelView(settings.get("ticket_name", "Support-Ticket")),
                message_id=int(panel_id),
            )
        except (ValueError, TypeError):
            continue

    try:
        synced = await bot.tree.sync()
        print(f"Slash-Commands synchronisiert: {len(synced)}")
        print("Commands:", ", ".join(command.name for command in synced))
    except Exception as error:
        print(f"Fehler beim Synchronisieren der Slash-Commands: {error}")

    print(f"Bot online: {bot.user}")


# ============================================================
# BOT STARTEN
# ============================================================

if not TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN fehlt. Trage deinen Bot-Token als "
        "Environment Variable in Railway ein."
    )

bot.run(TOKEN)
