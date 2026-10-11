
import os
import json
import io
import html
import asyncio
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands
from deep_translator import GoogleTranslator

# =========================================================
# KONFIGURATION
# =========================================================

TOKEN = os.getenv("DISCORD_TOKEN")
CONFIG_FILE = "config.json"
DATA_FILE = "data.json"

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

DEFAULT_CONFIG = {
    "guilds": {},
    "sign_in_channels": {},
    "languages": {},
    "tickets": {}
}

DEFAULT_GUILD_CONFIG = {
    "support_role_id": None,
    "ticket_category_id": None,
    "ticket_log_channel_id": None,
    "ticket_title": "Willkommen im Support!",
    "ticket_description": (
        "Beschreibe dein Anliegen bitte möglichst genau. "
        "Ein Teammitglied wird sich um dich kümmern."
    ),
    "ticket_buttons": ["Support", "Spielhilfe"],
    "language": "de"
}

data = {}


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        return default.copy()


def save_json(path, content):
    temporary_path = path + ".tmp"
    with open(temporary_path, "w", encoding="utf-8") as file:
        json.dump(content, file, ensure_ascii=False, indent=4)
    os.replace(temporary_path, path)


config = load_json(CONFIG_FILE, DEFAULT_CONFIG)
data = load_json(DATA_FILE, {})


def save_config():
    save_json(CONFIG_FILE, config)


def save_data():
    save_json(DATA_FILE, data)


def get_guild_config(guild_id):
    guild_key = str(guild_id)

    if "guilds" not in config:
        config["guilds"] = {}

    if guild_key not in config["guilds"]:
        config["guilds"][guild_key] = DEFAULT_GUILD_CONFIG.copy()

    result = config["guilds"][guild_key]

    for key, value in DEFAULT_GUILD_CONFIG.items():
        if key not in result:
            result[key] = value

    return result


def get_language(guild_id):
    return get_guild_config(guild_id).get("language", "de")


# =========================================================
# SPRACHÜBERSETZUNG
# =========================================================

TRANSLATION_CACHE = {}

# Google Translate unterstützt zahlreiche Sprachen.
# Sprachcodes können ebenfalls eingegeben werden, z. B. de, en, tr.
LANGUAGE_ALIASES = {
    "deutsch": "de",
    "german": "de",
    "englisch": "en",
    "english": "en",
    "französisch": "fr",
    "französisch": "fr",
    "french": "fr",
    "spanisch": "es",
    "spanish": "es",
    "italienisch": "it",
    "italian": "it",
    "türkisch": "tr",
    "turkish": "tr",
    "polnisch": "pl",
    "polish": "pl",
    "russisch": "ru",
    "russian": "ru",
    "ukrainisch": "uk",
    "ukrainian": "uk",
    "niederländisch": "nl",
    "dutch": "nl",
    "portugiesisch": "pt",
    "portuguese": "pt",
    "arabisch": "ar",
    "arabic": "ar",
    "chinesisch": "zh-CN",
    "chinese": "zh-CN",
    "japanisch": "ja",
    "japanese": "ja",
    "koreanisch": "ko",
    "korean": "ko",
    "griechisch": "el",
    "greek": "el",
    "hindi": "hi",
    "schwedisch": "sv",
    "swedish": "sv",
    "norwegisch": "no",
    "norwegian": "no",
    "dänisch": "da",
    "danish": "da",
    "finnisch": "fi",
    "finnish": "fi",
    "tschechisch": "cs",
    "czech": "cs",
    "ungarisch": "hu",
    "hungarian": "hu",
    "rumänisch": "ro",
    "romanian": "ro",
    "bulgarisch": "bg",
    "bulgarian": "bg",
    "persisch": "fa",
    "farsi": "fa",
    "hebräisch": "iw",
    "hebrew": "iw",
    "vietnamesisch": "vi",
    "vietnamese": "vi",
    "thai": "th",
    "indonesisch": "id",
    "indonesian": "id",
    "malaiisch": "ms",
    "malay": "ms",
    "katalanisch": "ca",
    "catalan": "ca",
    "kroatisch": "hr",
    "croatian": "hr",
    "serbisch": "sr",
    "serbian": "sr",
    "slowakisch": "sk",
    "slovak": "sk",
    "slowenisch": "sl",
    "slovenian": "sl",
    "estnisch": "et",
    "estonian": "et",
    "lettisch": "lv",
    "latvian": "lv",
    "litauisch": "lt",
    "lithuanian": "lt",
    "afrikaans": "af",
    "albanisch": "sq",
    "albanian": "sq",
    "bengalisch": "bn",
    "bengali": "bn",
    "urdu": "ur",
    "swahili": "sw",
    "esperanto": "eo",
    "irisch": "ga",
    "irish": "ga",
    "walisisch": "cy",
    "welsh": "cy",
    "latein": "la",
    "latin": "la",
}


def normalize_language(language):
    language = language.strip()
    alias = LANGUAGE_ALIASES.get(language.lower())

    if alias:
        return alias

    # Erlaubt auch direkt eingegebene Sprachcodes.
    return language.replace("_", "-")


async def translate_text(text, language):
    if not text or not language:
        return text

    language = normalize_language(language)

    if language.lower() in ("de", "german", "deutsch"):
        return text

    cache_key = (text, language)

    if cache_key in TRANSLATION_CACHE:
        return TRANSLATION_CACHE[cache_key]

    try:
        translated = await asyncio.to_thread(
            GoogleTranslator(source="auto", target=language).translate,
            text
        )

        if translated:
            TRANSLATION_CACHE[cache_key] = translated
            return translated

    except Exception as error:
        print(f"Übersetzungsfehler für {language}: {error}")

    # Falls der Übersetzungsdienst nicht erreichbar ist,
    # wird der ursprüngliche Text zurückgegeben.
    return text


async def tr(guild_id, text):
    return await translate_text(text, get_language(guild_id))


async def reply_private(interaction, text, **kwargs):
    translated = await tr(interaction.guild_id, text)

    if interaction.response.is_done():
        await interaction.followup.send(
            translated, ephemeral=True, **kwargs
        )
    else:
        await interaction.response.send_message(
            translated, ephemeral=True, **kwargs
        )


# =========================================================
# ALLGEMEINE HILFSFUNKTIONEN
# =========================================================

def is_admin(interaction):
    return (
        interaction.guild is not None
        and interaction.user.guild_permissions.manage_guild
    )


async def get_text_channel(guild, channel_id):
    if not channel_id:
        return None

    channel = guild.get_channel(int(channel_id))

    if channel is None:
        try:
            channel = await guild.fetch_channel(int(channel_id))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None

    return channel


def ticket_metadata(channel):
    if not channel.topic:
        return {}

    try:
        return json.loads(channel.topic)
    except (json.JSONDecodeError, TypeError):
        return {}


async def set_ticket_metadata(channel, metadata):
    await channel.edit(
        topic=json.dumps(metadata, ensure_ascii=False)
    )


async def ticket_record(channel):
    metadata = ticket_metadata(channel)
    return metadata if metadata.get("ticket") else None


async def find_existing_ticket(guild, user_id):
    for channel in guild.text_channels:
        metadata = ticket_metadata(channel)

        if (
            metadata.get("ticket")
            and int(metadata.get("owner_id", 0)) == int(user_id)
            and not metadata.get("closed", False)
        ):
            return channel

    return None


def make_ticket_channel_name(user, button_label):
    name = "".join(
        char.lower() if char.isalnum() else "-"
        for char in button_label
    )
    name = "-".join(part for part in name.split("-") if part)
    name = name[:35] or "support"
    return f"ticket-{name}-{user.id}"[:95]


# =========================================================
# SPRACHBEFEHL
# =========================================================

@bot.tree.command(
    name="sprache",
    description="Legt die Sprache des Bots auf diesem Server fest."
)
@app_commands.describe(
    sprache="Sprache oder Sprachcode, zum Beispiel Deutsch, English, tr, ja"
)
async def sprache(interaction: discord.Interaction, sprache: str):
    if not interaction.guild:
        await reply_private(
            interaction,
            "Dieser Befehl funktioniert nur auf einem Server."
        )
        return

    if not is_admin(interaction):
        await reply_private(
            interaction,
            "Du benötigst die Berechtigung „Server verwalten“."
        )
        return

    language = normalize_language(sprache)

    # Testübersetzung überprüft, ob die Sprache angenommen wird.
    try:
        await asyncio.to_thread(
            GoogleTranslator(source="auto", target=language).translate,
            "Language test"
        )
    except Exception:
        await reply_private(
            interaction,
            "Diese Sprache konnte nicht überprüft werden. "
            "Versuche einen Namen wie Deutsch oder English "
            "oder einen gültigen Sprachcode wie de, en, tr oder ja."
        )
        return

    get_guild_config(interaction.guild_id)["language"] = language
    save_config()

    confirmation = await translate_text(
        "The bot language has been set to: " + sprache,
        language
    )

    await interaction.response.send_message(
        confirmation,
        ephemeral=True
    )


# =========================================================
# TICKET-PANEL: BIS ZU FÜNF BUTTONS
# =========================================================

class TicketPanelView(discord.ui.View):
    def __init__(self, guild_id):
        super().__init__(timeout=None)
        self.guild_id = guild_id

        guild_config = get_guild_config(guild_id)
        labels = guild_config.get("ticket_buttons", ["Support"])

        for index, label in enumerate(labels[:5]):
            button = discord.ui.Button(
                label=label[:80],
                style=discord.ButtonStyle.primary,
                custom_id=f"ticket:create:{index}",
                emoji="🎫"
            )

            async def callback(interaction, button_index=index):
                await create_ticket(interaction, button_index)

            button.callback = callback
            self.add_item(button)


async def create_ticket(interaction, button_index):
    guild = interaction.guild

    if guild is None:
        await reply_private(interaction, "Dieser Befehl benötigt einen Server.")
        return

    guild_config = get_guild_config(guild.id)
    labels = guild_config.get("ticket_buttons", ["Support"])

    if button_index >= len(labels):
        await reply_private(
            interaction,
            "Dieser Ticket-Button ist nicht mehr eingerichtet."
        )
        return

    existing = await find_existing_ticket(guild, interaction.user.id)

    if existing:
        await reply_private(
            interaction,
            f"Du hast bereits ein offenes Ticket: {existing.mention}"
        )
        return

    category_id = guild_config.get("ticket_category_id")
    support_role_id = guild_config.get("support_role_id")

    category = guild.get_channel(int(category_id)) if category_id else None
    support_role = guild.get_role(int(support_role_id)) if support_role_id else None

    if category_id and category is None:
        await reply_private(
            interaction,
            "Die konfigurierte Ticket-Kategorie wurde nicht gefunden. "
            "Führe /ticketcreat erneut aus."
        )
        return

    if support_role_id and support_role is None:
        await reply_private(
            interaction,
            "Die konfigurierte Support-Rolle wurde nicht gefunden. "
            "Führe /ticketcreat erneut aus."
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
            embed_links=True
        ),
        guild.me: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            manage_channels=True,
            attach_files=True
        )
    }

    if support_role:
        overwrites[support_role] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            manage_messages=True,
            attach_files=True
        )

    try:
        channel = await guild.create_text_channel(
            name=make_ticket_channel_name(
                interaction.user, labels[button_index]
            ),
            category=category,
            overwrites=overwrites,
            reason=f"Ticket von {interaction.user}"
        )
    except discord.HTTPException as error:
        await reply_private(
            interaction,
            f"Das Ticket konnte nicht erstellt werden: {error}"
        )
        return

    metadata = {
        "ticket": True,
        "owner_id": interaction.user.id,
        "owner_name": str(interaction.user),
        "ticket_type": labels[button_index],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "claimed_id": None,
        "claimed_name": "Niemand",
        "closed": False
    }

    await set_ticket_metadata(channel, metadata)

    title = await tr(
        guild.id,
        guild_config.get("ticket_title", "Willkommen im Support!")
    )
    description = await tr(
        guild.id,
        guild_config.get(
            "ticket_description",
            "Beschreibe dein Anliegen bitte möglichst genau."
        )
    )

    embed = discord.Embed(
        title=title,
        description=description,
        color=discord.Color.blurple(),
        timestamp=datetime.now(timezone.utc)
    )
    embed.add_field(
        name=await tr(guild.id, "Erstellt von"),
        value=interaction.user.mention,
        inline=True
    )
    embed.add_field(
        name=await tr(guild.id, "Ticket-Typ"),
        value=await tr(guild.id, labels[button_index]),
        inline=True
    )

    if support_role:
        await channel.send(support_role.mention)

    await channel.send(
        content=interaction.user.mention,
        embed=embed,
        view=TicketControlsView()
    )

    await reply_private(
        interaction,
        f"Dein Ticket wurde erstellt: {channel.mention}"
    )


# =========================================================
# TICKET-KONTROLLEN
# Kein Button zum Bearbeiten der Ticket-Nachricht!
# =========================================================

class TicketControlsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Übernehmen",
        style=discord.ButtonStyle.success,
        emoji="🙋",
        custom_id="ticket:claim"
    )
    async def claim(self, interaction, button):
        await self._claim(interaction)

    async def _claim(self, interaction):
        metadata = await ticket_record(interaction.channel)

        if not metadata:
            await reply_private(interaction, "Das ist kein Ticket-Kanal.")
            return

        guild_config = get_guild_config(interaction.guild_id)
        role_id = guild_config.get("support_role_id")
        role = interaction.guild.get_role(int(role_id)) if role_id else None

        if not interaction.user.guild_permissions.manage_channels:
            if role is None or role not in interaction.user.roles:
                await reply_private(
                    interaction,
                    "Nur das Support-Team kann Tickets übernehmen."
                )
                return

        if metadata.get("claimed_id"):
            await reply_private(
                interaction,
                f"Dieses Ticket wurde bereits von "
                f"{metadata.get('claimed_name', 'einem Teammitglied')} übernommen."
            )
            return

        metadata["claimed_id"] = interaction.user.id
        metadata["claimed_name"] = str(interaction.user)
        await set_ticket_metadata(interaction.channel, metadata)

        await reply_private(interaction, "Du hast das Ticket übernommen.")

        await interaction.channel.send(
            await tr(
                interaction.guild_id,
                f"🙋 {interaction.user.mention} hat dieses Ticket übernommen."
            )
        )

    @discord.ui.button(
        label="Freigeben",
        style=discord.ButtonStyle.secondary,
        emoji="🔓",
        custom_id="ticket:release"
    )
    async def release(self, interaction, button):
        metadata = await ticket_record(interaction.channel)

        if not metadata:
            await reply_private(interaction, "Das ist kein Ticket-Kanal.")
            return

        guild_config = get_guild_config(interaction.guild_id)
        role_id = guild_config.get("support_role_id")
        role = interaction.guild.get_role(int(role_id)) if role_id else None

        if not interaction.user.guild_permissions.manage_channels:
            if role is None or role not in interaction.user.roles:
                await reply_private(
                    interaction,
                    "Nur das Support-Team kann Tickets freigeben."
                )
                return

        if metadata.get("claimed_id") != interaction.user.id:
            if not interaction.user.guild_permissions.manage_channels:
                await reply_private(
                    interaction,
                    "Du kannst nur Tickets freigeben, die du selbst übernommen hast."
                )
                return

        metadata["claimed_id"] = None
        metadata["claimed_name"] = "Niemand"
        await set_ticket_metadata(interaction.channel, metadata)

        await reply_private(interaction, "Das Ticket wurde freigegeben.")

        await interaction.channel.send(
            await tr(
                interaction.guild_id,
                f"🔓 {interaction.user.mention} hat das Ticket freigegeben."
            )
        )

    @discord.ui.button(
        label="Schließen",
        style=discord.ButtonStyle.danger,
        emoji="🔒",
        custom_id="ticket:close"
    )
    async def close(self, interaction, button):
        metadata = await ticket_record(interaction.channel)

        if not metadata:
            await reply_private(interaction, "Das ist kein Ticket-Kanal.")
            return

        await interaction.response.send_message(
            await tr(
                interaction.guild_id,
                "Möchtest du das Ticket wirklich schließen? "
                "Der Ersteller und ein Support-Mitglied müssen bestätigen."
            ),
            view=CloseVoteView(),
            ephemeral=True
        )


class CloseVoteView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=600)
        self.votes = set()

    @discord.ui.button(
        label="Schließen bestätigen",
        style=discord.ButtonStyle.danger,
        custom_id="ticket:close_confirm"
    )
    async def confirm(self, interaction, button):
        channel = interaction.channel
        metadata = await ticket_record(channel)

        if not metadata:
            await reply_private(interaction, "Das ist kein Ticket.")
            return

        guild_config = get_guild_config(interaction.guild_id)
        support_role_id = guild_config.get("support_role_id")
        support_role = (
            interaction.guild.get_role(int(support_role_id))
            if support_role_id else None
        )

        is_owner = interaction.user.id == int(metadata["owner_id"])
        is_support = (
            interaction.user.guild_permissions.manage_channels
            or (
                support_role is not None
                and support_role in interaction.user.roles
            )
        )

        if not is_owner and not is_support:
            await reply_private(
                interaction,
                "Nur der Ticket-Ersteller oder das Support-Team kann bestätigen."
            )
            return

        if interaction.user.id in self.votes:
            await reply_private(interaction, "Du hast bereits bestätigt.")
            return

        self.votes.add(interaction.user.id)

        owner_voted = int(metadata["owner_id"]) in self.votes
        support_voted = any(
            user_id != int(metadata["owner_id"])
            for user_id in self.votes
        )

        await reply_private(interaction, "Deine Bestätigung wurde gespeichert.")

        if owner_voted and support_voted:
            await close_ticket(channel, interaction.user)


# =========================================================
# TRANSKRIPT: NUR BEIM SCHLIESSEN
# =========================================================

async def create_html_transcript(channel, metadata, closer):
    messages = []

    async for message in channel.history(
        limit=None,
        oldest_first=True
    ):
        timestamp = message.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        author = html.escape(str(message.author))
        content = html.escape(message.content or "")

        if message.attachments:
            attachment_html = "<br>".join(
                f'<a href="{html.escape(attachment.url, quote=True)}">'
                f'{html.escape(attachment.filename)}</a>'
                for attachment in message.attachments
            )
            content += "<br>" + attachment_html

        if message.embeds:
            content += "<br><i>[Embed-Nachricht]</i>"

        messages.append(
            f"""
            <div class="message">
                <div class="meta">{author} · {timestamp}</div>
                <div class="content">{content}</div>
            </div>
            """
        )

    created = html.escape(str(metadata.get("created_at", "Unbekannt")))
    owner = html.escape(str(metadata.get("owner_name", "Unbekannt")))
    claimed = html.escape(str(metadata.get("claimed_name", "Niemand")))
    closer_name = html.escape(str(closer))
    ticket_type = html.escape(str(metadata.get("ticket_type", "Unbekannt")))

    document = f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<title>Ticket-Transkript</title>
<style>
body {{ font-family: Arial, sans-serif; background:#1e1f22; color:#dbdee1;
       margin:0; padding:24px; }}
h1 {{ color:#fff; }}
.info {{ background:#2b2d31; padding:16px; border-radius:8px; margin-bottom:20px; }}
.message {{ background:#2b2d31; padding:12px; border-radius:6px; margin:10px 0; }}
.meta {{ color:#b5bac1; font-size:12px; margin-bottom:8px; }}
.content {{ white-space:pre-wrap; overflow-wrap:anywhere; }}
a {{ color:#00a8fc; }}
</style>
</head>
<body>
<h1>Ticket-Transkript</h1>
<div class="info">
<p><b>Kanal:</b> {html.escape(channel.name)}</p>
<p><b>Ersteller:</b> {owner} (ID: {metadata.get('owner_id', 'Unbekannt')})</p>
<p><b>Ticket-Typ:</b> {ticket_type}</p>
<p><b>Übernommen von:</b> {claimed}
(ID: {metadata.get('claimed_id') or 'Niemand'})</p>
<p><b>Geschlossen von:</b> {closer_name}
(ID: {closer.id})</p>
<p><b>Erstellt am:</b> {created}</p>
<p><b>Geschlossen am:</b> {datetime.now(timezone.utc).isoformat()}</p>
</div>
{''.join(messages)}
</body>
</html>
"""
    return document.encode("utf-8")


async def close_ticket(channel, closer):
    metadata = await ticket_record(channel)

    if not metadata:
        return

    guild = channel.guild
    guild_config = get_guild_config(guild.id)
    log_channel_id = guild_config.get("ticket_log_channel_id")
    log_channel = await get_text_channel(guild, log_channel_id)

    if log_channel is None:
        try:
            await closer.send(
                "Das Ticket wurde nicht geschlossen: "
                "Es ist kein gültiger Log-Kanal eingerichtet. "
                "Bitte führe /ticketlog-set aus."
            )
        except discord.HTTPException:
            pass
        return

    transcript = await create_html_transcript(channel, metadata, closer)

    log_embed = discord.Embed(
        title=await tr(guild.id, "Ticket geschlossen"),
        color=discord.Color.red(),
        timestamp=datetime.now(timezone.utc)
    )
    log_embed.add_field(
        name=await tr(guild.id, "Ticket-Kanal"),
        value=channel.name,
        inline=True
    )
    log_embed.add_field(
        name=await tr(guild.id, "Ersteller"),
        value=f"{metadata.get('owner_name')} (`{metadata.get('owner_id')}`)",
        inline=False
    )
    log_embed.add_field(
        name=await tr(guild.id, "Übernommen von"),
        value=(
            f"{metadata.get('claimed_name', 'Niemand')} "
            f"(`{metadata.get('claimed_id') or 'Niemand'}`)"
        ),
        inline=False
    )
    log_embed.add_field(
        name=await tr(guild.id, "Geschlossen von"),
        value=f"{closer} (`{closer.id}`)",
        inline=False
    )
    log_embed.add_field(
        name=await tr(guild.id, "Ticket-Typ"),
        value=metadata.get("ticket_type", "Unbekannt"),
        inline=True
    )

    transcript_file = discord.File(
        io.BytesIO(transcript),
        filename=f"transkript-{channel.id}.html"
    )

    # Erst wenn das Log erfolgreich gesendet wurde, wird der Kanal gelöscht.
    try:
        await log_channel.send(
            embed=log_embed,
            file=transcript_file
        )
    except discord.HTTPException as error:
        print(f"Ticket-Log konnte nicht gesendet werden: {error}")
        try:
            await closer.send(
                "Das Ticket wurde nicht gelöscht, da das Transkript "
                "nicht im Log-Kanal gespeichert werden konnte."
            )
        except discord.HTTPException:
            pass
        return

    try:
        await channel.delete(reason=f"Ticket geschlossen von {closer}")
    except discord.HTTPException as error:
        print(f"Ticket-Kanal konnte nicht gelöscht werden: {error}")


# =========================================================
# SLASH-BEFEHL: TICKET ERSTELLEN
# =========================================================

@bot.tree.command(
    name="ticketcreat",
    description="Richtet das Ticket-System ein und erstellt ein Ticket-Panel."
)
@app_commands.describe(
    support_rolle="Rolle, die Tickets sehen und übernehmen darf",
    kategorie="Kategorie für neu erstellte Tickets",
    button1="Beschriftung des ersten Ticket-Buttons",
    button2="Optionaler zweiter Button",
    button3="Optionaler dritter Button",
    button4="Optionaler vierter Button",
    button5="Optionaler fünfter Button"
)
@app_commands.default_permissions(manage_guild=True)
async def ticketcreat(
    interaction: discord.Interaction,
    support_rolle: discord.Role,
    kategorie: discord.CategoryChannel,
    button1: str,
    button2: str = None,
    button3: str = None,
    button4: str = None,
    button5: str = None
):
    if not interaction.guild:
        await reply_private(interaction, "Dieser Befehl benötigt einen Server.")
        return

    if not is_admin(interaction):
        await reply_private(
            interaction,
            "Du benötigst die Berechtigung „Server verwalten“."
        )
        return

    labels = [
        value.strip()
        for value in [button1, button2, button3, button4, button5]
        if value and value.strip()
    ]

    if not labels:
        await reply_private(interaction, "Du musst mindestens einen Button angeben.")
        return

    if any(len(label) > 80 for label in labels):
        await reply_private(
            interaction,
            "Jede Button-Beschriftung darf höchstens 80 Zeichen haben."
        )
        return

    guild_config = get_guild_config(interaction.guild_id)
    guild_config["support_role_id"] = support_rolle.id
    guild_config["ticket_category_id"] = kategorie.id
    guild_config["ticket_buttons"] = labels
    save_config()

    embed = discord.Embed(
        title=await tr(interaction.guild_id, "Support-Tickets"),
        description=await tr(
            interaction.guild_id,
            "Wähle einen Button aus, um ein neues Ticket zu erstellen."
        ),
        color=discord.Color.blurple()
    )

    await interaction.channel.send(
        embed=embed,
        view=TicketPanelView(interaction.guild_id)
    )

    await reply_private(
        interaction,
        f"Das Ticket-Panel wurde erstellt. Buttons: {len(labels)}. "
        f"Support-Rolle: {support_rolle.mention}. Kategorie: {kategorie.name}."
    )


# =========================================================
# SLASH-BEFEHL: TICKET-NACHRICHTEN BEARBEITEN
# =========================================================

@bot.tree.command(
    name="ticketbearbeiten",
    description="Bearbeitet die Ticket-Willkommensnachricht und Button-Namen."
)
@app_commands.describe(
    titel="Neuer Titel der Willkommensnachricht",
    nachricht="Neue Beschreibung der Willkommensnachricht",
    button1="Neuer Name des ersten Buttons",
    button2="Neuer Name des zweiten Buttons",
    button3="Neuer Name des dritten Buttons",
    button4="Neuer Name des vierten Buttons",
    button5="Neuer Name des fünften Buttons"
)
@app_commands.default_permissions(manage_guild=True)
async def ticketbearbeiten(
    interaction: discord.Interaction,
    titel: str,
    nachricht: str,
    button1: str = None,
    button2: str = None,
    button3: str = None,
    button4: str = None,
    button5: str = None
):
    if not interaction.guild:
        await reply_private(interaction, "Dieser Befehl benötigt einen Server.")
        return

    if not is_admin(interaction):
        await reply_private(
            interaction,
            "Du benötigst die Berechtigung „Server verwalten“."
        )
        return

    if len(titel) > 256 or len(nachricht) > 4000:
        await reply_private(
            interaction,
            "Der Titel darf maximal 256 und die Nachricht maximal 4000 Zeichen haben."
        )
        return

    guild_config = get_guild_config(interaction.guild_id)
    guild_config["ticket_title"] = titel
    guild_config["ticket_description"] = nachricht

    new_labels = [
        value.strip()
        for value in [button1, button2, button3, button4, button5]
        if value and value.strip()
    ]

    if new_labels:
        if any(len(label) > 80 for label in new_labels):
            await reply_private(
                interaction,
                "Jede Button-Beschriftung darf höchstens 80 Zeichen haben."
            )
            return
        guild_config["ticket_buttons"] = new_labels

    save_config()

    await reply_private(
        interaction,
        "Die Ticket-Willkommensnachricht wurde gespeichert. "
        "Neue Ticket-Panels verwenden die neuen Beschriftungen. "
        "Führe /ticketcreat erneut aus, um ein neues Panel zu posten."
    )


# =========================================================
# SLASH-BEFEHL: LOG-KANAL SETZEN
# =========================================================

@bot.tree.command(
    name="ticketlog-set",
    description="Legt den Kanal für Ticket-Transkripte fest."
)
@app_commands.describe(
    kanal="Textkanal, in dem geschlossene Tickets protokolliert werden"
)
@app_commands.default_permissions(manage_guild=True)
async def ticketlog_set(
    interaction: discord.Interaction,
    kanal: discord.TextChannel
):
    if not interaction.guild:
        await reply_private(interaction, "Dieser Befehl benötigt einen Server.")
        return

    if not is_admin(interaction):
        await reply_private(
            interaction,
            "Du benötigst die Berechtigung „Server verwalten“."
        )
        return

    get_guild_config(interaction.guild_id)["ticket_log_channel_id"] = kanal.id
    save_config()

    await reply_private(
        interaction,
        f"Der Ticket-Log-Kanal ist jetzt {kanal.mention}. "
        "Transkripte werden erst beim Schließen eines Tickets gesendet."
    )


# =========================================================
# ANMELDEPANEL
# =========================================================

class SignInModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="The Isle – Anmeldung")

        self.dinosaur = discord.ui.TextInput(
            label="Dinosaurier",
            placeholder="Welchen Dinosaurier spielst du?",
            max_length=100
        )
        self.skin = discord.ui.TextInput(
            label="Skin",
            placeholder="Welchen Skin verwendest du?",
            max_length=100
        )
        self.location = discord.ui.TextInput(
            label="Standort",
            placeholder="Wo befindest du dich im Spiel?",
            max_length=200
        )

        self.add_item(self.dinosaur)
        self.add_item(self.skin)
        self.add_item(self.location)

    async def on_submit(self, interaction):
        guild_id = interaction.guild_id
        channel_id = config.get("sign_in_channels", {}).get(str(guild_id))
        channel = await get_text_channel(interaction.guild, channel_id)

        if channel is None:
            await reply_private(
                interaction,
                "Der Anmelde-Log-Kanal ist nicht eingerichtet. "
                "Verwende /kanal-set."
            )
            return

        embed = discord.Embed(
            title="The Isle – Player Sign-In",
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(
            name="Player",
            value=f"{interaction.user} ({interaction.user.id})",
            inline=False
        )
        embed.add_field(
            name="Dinosaur",
            value=str(self.dinosaur),
            inline=True
        )
        embed.add_field(
            name="Skin",
            value=str(self.skin),
            inline=True
        )
        embed.add_field(
            name="Location",
            value=str(self.location),
            inline=False
        )

        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            await reply_private(
                interaction,
                "Die Anmeldung konnte nicht in den Log-Kanal gesendet werden."
            )
            return

        await reply_private(
            interaction,
            "Deine Anmeldung wurde erfolgreich übermittelt."
        )


class SignInView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Jetzt anmelden",
        style=discord.ButtonStyle.success,
        emoji="🦖",
        custom_id="signin:open"
    )
    async def open_modal(self, interaction, button):
        await interaction.response.send_modal(SignInModal())


@bot.tree.command(
    name="kanal-set",
    description="Legt den Kanal für The-Isle-Anmeldungen fest."
)
@app_commands.describe(
    kanal="Textkanal für die Anmeldeprotokolle"
)
@app_commands.default_permissions(manage_guild=True)
async def kanal_set(
    interaction: discord.Interaction,
    kanal: discord.TextChannel
):
    if not interaction.guild:
        await reply_private(interaction, "Dieser Befehl benötigt einen Server.")
        return

    if not is_admin(interaction):
        await reply_private(
            interaction,
            "Du benötigst die Berechtigung „Server verwalten“."
        )
        return

    config.setdefault("sign_in_channels", {})[
        str(interaction.guild_id)
    ] = kanal.id
    save_config()

    await reply_private(
        interaction,
        f"Der Anmelde-Log-Kanal wurde auf {kanal.mention} gesetzt."
    )


@bot.command(name="anmeldepannel", aliases=["anmeldepanel"])
@commands.has_permissions(manage_guild=True)
async def anmeldepanel(ctx):
    embed = discord.Embed(
        title="The Isle – Spieler-Anmeldung",
        description=(
            "Klicke auf den Button und gib deinen Dinosaurier, "
            "Skin und Standort an."
        ),
        color=discord.Color.green()
    )

    await ctx.send(embed=embed, view=SignInView())


@anmeldepanel.error
async def anmeldepanel_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send(
            "Du benötigst die Berechtigung „Server verwalten“."
        )
    else:
        print(f"Anmeldepanel-Fehler: {error}")


# =========================================================
# FEHLERBEHANDLUNG UND START
# =========================================================

@bot.tree.error
async def on_app_command_error(interaction, error):
    print(f"Slash-Command-Fehler: {error}")

    try:
        await reply_private(
            interaction,
            "Bei der Ausführung des Befehls ist ein Fehler aufgetreten."
        )
    except discord.HTTPException:
        pass


@bot.event
async def on_ready():
    # Persistente Views, damit Buttons auch nach Neustarts reagieren.
    bot.add_view(SignInView())
    bot.add_view(TicketControlsView())

    # Ticket-Panel-Views für die aktuell gespeicherten Konfigurationen.
    for guild_key in config.get("guilds", {}):
        try:
            bot.add_view(TicketPanelView(int(guild_key)))
        except Exception as error:
            print(f"Ticket-Panel-View konnte nicht geladen werden: {error}")

    try:
        synced = await bot.tree.sync()
        print(f"{len(synced)} Slash-Befehle synchronisiert.")
    except Exception as error:
        print(f"Slash-Befehle konnten nicht synchronisiert werden: {error}")

    print(f"Bot angemeldet als {bot.user}")


if not TOKEN:
    raise RuntimeError(
        "Die Umgebungsvariable DISCORD_TOKEN fehlt. "
        "Lege sie in Railway unter Variables an."
    )

bot.run(TOKEN)
