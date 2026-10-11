
import os
import io
import json
import html
import asyncio
import logging
from datetime import datetime, timezone

import discord
from discord.ext import commands
from discord import app_commands

try:
    from deep_translator import GoogleTranslator
except ImportError:
    GoogleTranslator = None


# =========================================================
# EINSTELLUNGEN
# =========================================================

logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("DISCORD_TOKEN")
CONFIG_FILE = "config.json"

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

data = {
    "guilds": {},
    "tickets": {}
}

LANGUAGES = [
    app_commands.Choice(name="Deutsch", value="de"),
    app_commands.Choice(name="Englisch", value="en"),
    app_commands.Choice(name="Französisch", value="fr"),
    app_commands.Choice(name="Spanisch", value="es"),
    app_commands.Choice(name="Polnisch", value="pl"),
    app_commands.Choice(name="Türkisch", value="tr"),
]

DEFAULT_CONFIG = {
    "language": "de",
    "support_role_id": None,
    "category_id": None,
    "log_channel_id": None,
    "panel_channel_id": None,
    "welcome_message": (
        "Willkommen! Beschreibe bitte dein Anliegen. "
        "Unser Support hilft dir so bald wie möglich."
    ),
    "button_labels": ["Support"],
}


# =========================================================
# DATEN SPEICHERN UND LADEN
# =========================================================

def save_data():
    temporary_file = CONFIG_FILE + ".tmp"

    with open(temporary_file, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=4, ensure_ascii=False)

    os.replace(temporary_file, CONFIG_FILE)


def load_data():
    global data

    if not os.path.exists(CONFIG_FILE):
        return

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as file:
            loaded = json.load(file)

        if isinstance(loaded, dict):
            data = loaded

        data.setdefault("guilds", {})
        data.setdefault("tickets", {})

    except (OSError, json.JSONDecodeError):
        logging.exception("config.json konnte nicht geladen werden.")


def get_config(guild_id):
    guild_key = str(guild_id)

    if guild_key not in data["guilds"]:
        data["guilds"][guild_key] = {}

    config = data["guilds"][guild_key]

    for key, value in DEFAULT_CONFIG.items():
        if key not in config:
            config[key] = (
                value.copy()
                if isinstance(value, (list, dict))
                else value
            )

    return config


def get_ticket_info(channel_id):
    return data["tickets"].get(str(channel_id))


def save_ticket_info(channel_id, info):
    data["tickets"][str(channel_id)] = info
    save_data()


def remove_ticket_info(channel_id):
    data["tickets"].pop(str(channel_id), None)
    save_data()


# =========================================================
# SPRACHE UND ANTWORTEN
# =========================================================

async def translate(guild_id, text):
    if not guild_id or not text:
        return text

    config = get_config(guild_id)
    language = config.get("language", "de")

    if language == "de" or GoogleTranslator is None:
        return text

    try:
        result = await asyncio.to_thread(
            GoogleTranslator(source="auto", target=language).translate,
            text
        )
        return result or text

    except Exception:
        logging.exception("Übersetzung fehlgeschlagen.")
        return text


async def reply(interaction, text, ephemeral=True):
    text = await translate(interaction.guild_id, text)

    if interaction.response.is_done():
        await interaction.followup.send(
            text,
            ephemeral=ephemeral
        )
    else:
        await interaction.response.send_message(
            text,
            ephemeral=ephemeral
        )


# =========================================================
# BERECHTIGUNGEN UND HILFSFUNKTIONEN
# =========================================================

def is_support(member, config):
    role_id = config.get("support_role_id")

    if not role_id or not isinstance(member, discord.Member):
        return False

    return any(role.id == role_id for role in member.roles)


def safe_channel_name(name):
    cleaned = "".join(
        char.lower() if char.isalnum() else "-"
        for char in name
    )

    cleaned = "-".join(
        part for part in cleaned.split("-") if part
    )

    return cleaned[:80] or "ticket"


# =========================================================
# TICKET-PANEL UND TICKET ERSTELLEN
# =========================================================

class TicketPanelButton(discord.ui.Button):
    def __init__(self, guild_id, index, label):
        self.guild_id = guild_id

        super().__init__(
            label=label[:80],
            style=discord.ButtonStyle.primary,
            custom_id=f"ticket:create:{guild_id}:{index}"
        )

    async def callback(self, interaction: discord.Interaction):
        guild = interaction.guild

        if guild is None:
            await reply(
                interaction,
                "Dieser Button funktioniert nur auf einem Server."
            )
            return

        config = get_config(guild.id)

        support_role_id = config.get("support_role_id")
        category_id = config.get("category_id")

        support_role = (
            guild.get_role(support_role_id)
            if support_role_id else None
        )

        category = (
            guild.get_channel(category_id)
            if category_id else None
        )

        if support_role is None:
            await reply(
                interaction,
                "Die Support-Rolle ist nicht eingerichtet."
            )
            return

        if not isinstance(category, discord.CategoryChannel):
            await reply(
                interaction,
                "Die Ticket-Kategorie ist nicht eingerichtet."
            )
            return

        # Pro Benutzer nur ein offenes Ticket.
        for channel_id, info in data["tickets"].items():
            if (
                info.get("guild_id") == guild.id
                and info.get("creator_id") == interaction.user.id
            ):
                existing = guild.get_channel(int(channel_id))

                if existing is not None:
                    await reply(
                        interaction,
                        f"Du hast bereits ein offenes Ticket: {existing.mention}"
                    )
                    return

        await interaction.response.defer(
            ephemeral=True,
            thinking=True
        )

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
            support_role: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_messages=True
            )
        }

        try:
            channel = await guild.create_text_channel(
                name=safe_channel_name(
                    f"ticket-{interaction.user.display_name}"
                ),
                category=category,
                overwrites=overwrites,
                topic=(
                    f"Ticket von {interaction.user} "
                    f"({interaction.user.id})"
                ),
                reason="Support-Ticket erstellt"
            )

            info = {
                "guild_id": guild.id,
                "creator_id": interaction.user.id,
                "support_role_id": support_role.id,
                "claimed_by": None,
                "created_at": datetime.now(
                    timezone.utc
                ).isoformat()
            }

            save_ticket_info(channel.id, info)

            embed = discord.Embed(
                title="Support-Ticket",
                description=config.get("welcome_message", ""),
                color=discord.Color.blurple(),
                timestamp=datetime.now(timezone.utc)
            )

            embed.add_field(
                name="Erstellt von",
                value=interaction.user.mention,
                inline=True
            )

            embed.add_field(
                name="Support-Team",
                value=support_role.mention,
                inline=True
            )

            await channel.send(
                content=(
                    f"{interaction.user.mention} "
                    f"{support_role.mention}"
                ),
                embed=embed,
                view=TicketControlView(channel.id)
            )

            await interaction.followup.send(
                f"Dein Ticket wurde erstellt: {channel.mention}",
                ephemeral=True
            )

        except discord.Forbidden:
            await interaction.followup.send(
                "Der Bot hat nicht genügend Berechtigungen, "
                "um ein Ticket zu erstellen.",
                ephemeral=True
            )

        except Exception:
            logging.exception("Ticket konnte nicht erstellt werden.")

            await interaction.followup.send(
                "Beim Erstellen des Tickets ist ein Fehler aufgetreten.",
                ephemeral=True
            )


class TicketPanelView(discord.ui.View):
    def __init__(self, guild_id, labels):
        super().__init__(timeout=None)

        for index, label in enumerate(labels[:5]):
            self.add_item(
                TicketPanelButton(
                    guild_id,
                    index,
                    label or f"Ticket {index + 1}"
                )
            )


# =========================================================
# TICKET STEUERUNG
# NUR DIE SUPPORT-ROLLE DARF TICKETS SCHLIESSEN!
# =========================================================

class TicketControlView(discord.ui.View):
    def __init__(self, channel_id):
        super().__init__(timeout=None)
        self.channel_id = channel_id

        claim_button = discord.ui.Button(
            label="Ticket übernehmen",
            style=discord.ButtonStyle.success,
            custom_id=f"ticket:claim:{channel_id}"
        )

        release_button = discord.ui.Button(
            label="Ticket freigeben",
            style=discord.ButtonStyle.secondary,
            custom_id=f"ticket:release:{channel_id}"
        )

        close_button = discord.ui.Button(
            label="Ticket schließen",
            style=discord.ButtonStyle.danger,
            custom_id=f"ticket:close:{channel_id}"
        )

        claim_button.callback = self.claim_ticket
        release_button.callback = self.release_ticket
        close_button.callback = self.close_ticket_button

        self.add_item(claim_button)
        self.add_item(release_button)
        self.add_item(close_button)

    async def claim_ticket(self, interaction):
        await self.support_action(interaction, "claim")

    async def release_ticket(self, interaction):
        await self.support_action(interaction, "release")

    async def support_action(self, interaction, action):
        guild = interaction.guild
        channel = interaction.channel

        if guild is None or channel is None:
            await reply(interaction, "Server oder Ticket nicht gefunden.")
            return

        config = get_config(guild.id)
        info = get_ticket_info(channel.id)

        if not info:
            await reply(interaction, "Ticketdaten nicht gefunden.")
            return

        if not is_support(interaction.user, config):
            await reply(
                interaction,
                "Nur Mitglieder mit der Support-Rolle dürfen diese Aktion ausführen."
            )
            return

        if action == "claim":
            info["claimed_by"] = interaction.user.id
            save_ticket_info(channel.id, info)

            await reply(
                interaction,
                f"{interaction.user.mention} hat das Ticket übernommen.",
                ephemeral=False
            )

        elif action == "release":
            claimed_by = info.get("claimed_by")

            if claimed_by not in (None, interaction.user.id):
                await reply(
                    interaction,
                    "Ein anderes Support-Mitglied hat dieses Ticket übernommen."
                )
                return

            info["claimed_by"] = None
            save_ticket_info(channel.id, info)

            await reply(
                interaction,
                f"{interaction.user.mention} hat das Ticket freigegeben.",
                ephemeral=False
            )

    async def close_ticket_button(self, interaction):
        guild = interaction.guild
        channel = interaction.channel

        if guild is None or channel is None:
            await reply(interaction, "Server oder Ticket nicht gefunden.")
            return

        config = get_config(guild.id)
        info = get_ticket_info(channel.id)

        if not info:
            await reply(interaction, "Ticketdaten nicht gefunden.")
            return

        # Sicherheitsprüfung: Nur die konfigurierte Support-Rolle.
        if not is_support(interaction.user, config):
            await reply(
                interaction,
                "Du darfst dieses Ticket nicht schließen. "
                "Nur Mitglieder mit der Support-Rolle dürfen Tickets schließen.",
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            "Du hast das Schließen des Tickets angefordert.",
            ephemeral=True
        )

        await close_ticket(
            channel,
            guild,
            info,
            config,
            closed_by=interaction.user.id
        )


# =========================================================
# TRANSKRIPT ERSTELLEN UND TICKET SCHLIESSEN
# =========================================================

async def close_ticket(
    channel,
    guild,
    info,
    config,
    closed_by=None
):
    # Zusätzliche Sicherheitsprüfung.
    # Der Aufrufer muss eine echte Support-Berechtigung haben.
    member = guild.get_member(closed_by) if closed_by else None

    if member is None or not is_support(member, config):
        logging.warning(
            "Ticket-Schließung ohne gültige Support-Berechtigung blockiert."
        )
        return

    log_channel_id = config.get("log_channel_id")
    log_channel = (
        guild.get_channel(log_channel_id)
        if log_channel_id else None
    )

    if not isinstance(log_channel, discord.TextChannel):
        try:
            await channel.send(
                "Das Ticket wurde nicht geschlossen, da kein gültiger "
                "Ticket-Log-Kanal eingerichtet ist."
            )
        except discord.HTTPException:
            pass
        return

    try:
        messages_html = []

        async for message in channel.history(
            limit=None,
            oldest_first=True
        ):
            timestamp = message.created_at.strftime(
                "%d.%m.%Y %H:%M:%S UTC"
            )

            author = html.escape(
                f"{message.author} ({message.author.id})"
            )

            content = html.escape(message.content or "")

            attachments_html = ""

            if message.attachments:
                links = []

                for attachment in message.attachments:
                    url = html.escape(attachment.url, quote=True)
                    filename = html.escape(attachment.filename)

                    links.append(
                        f'<a href="{url}">{filename}</a>'
                    )

                attachments_html = (
                    "<p>Anhänge: " + ", ".join(links) + "</p>"
                )

            embed_note = ""

            if message.embeds:
                embed_note = "<p>[Nachricht enthält ein Embed]</p>"

            messages_html.append(
                "<article>"
                f"<small>{timestamp}</small>"
                f"<strong>{author}</strong>"
                f"<p>{content}</p>"
                f"{attachments_html}{embed_note}"
                "</article>"
            )

        creator_id = info.get("creator_id")
        claimer_id = info.get("claimed_by")

        transcript = f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ticket-Transkript</title>
<style>
body {{
    background: #1e1f22;
    color: #f2f3f5;
    font-family: Arial, sans-serif;
    padding: 24px;
}}
article {{
    background: #2b2d31;
    border-radius: 8px;
    padding: 12px;
    margin: 12px 0;
    overflow-wrap: anywhere;
}}
small {{
    display: block;
    color: #b5bac1;
    margin-bottom: 6px;
}}
p {{
    white-space: pre-wrap;
}}
a {{
    color: #00a8fc;
}}
</style>
</head>
<body>
<h1>Ticket-Transkript: {html.escape(channel.name)}</h1>
<p>Server: {html.escape(guild.name)}</p>
<p>Ticket-Ersteller-ID: {creator_id or "Unbekannt"}</p>
<p>Übernommen von: {claimer_id or "Niemand"}</p>
<p>Geschlossen von: {closed_by}</p>
<p>Geschlossen am: {datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M:%S UTC")}</p>
<hr>
{''.join(messages_html)}
</body>
</html>
"""

        filename = safe_channel_name(channel.name) + "-transkript.html"

        transcript_file = discord.File(
            fp=io.BytesIO(transcript.encode("utf-8")),
            filename=filename
        )

        embed = discord.Embed(
            title="Ticket geschlossen",
            color=discord.Color.red(),
            timestamp=datetime.now(timezone.utc)
        )

        embed.add_field(
            name="Ticket",
            value=f"{channel.name} (`{channel.id}`)",
            inline=False
        )

        embed.add_field(
            name="Ticket-Ersteller",
            value=f"<@{creator_id}>" if creator_id else "Unbekannt",
            inline=True
        )

        embed.add_field(
            name="Übernommen von",
            value=f"<@{claimer_id}>" if claimer_id else "Niemand",
            inline=True
        )

        embed.add_field(
            name="Geschlossen von",
            value=f"<@{closed_by}>",
            inline=True
        )

        # Zuerst das Log senden. Erst bei Erfolg den Kanal löschen.
        await log_channel.send(
            embed=embed,
            file=transcript_file
        )

        remove_ticket_info(channel.id)

        await channel.delete(
            reason=f"Ticket durch Support-Mitglied {closed_by} geschlossen"
        )

    except discord.Forbidden:
        logging.exception(
            "Fehlende Rechte beim Erstellen des Ticket-Logs oder Löschen."
        )

        try:
            await channel.send(
                "Das Ticket konnte nicht vollständig geschlossen werden. "
                "Bitte prüfe die Berechtigungen des Bots."
            )
        except discord.HTTPException:
            pass

    except Exception:
        logging.exception("Fehler beim Schließen des Tickets.")

        try:
            await channel.send(
                "Beim Erstellen des Transkripts ist ein Fehler aufgetreten. "
                "Bitte prüfe die Railway-Logs."
            )
        except discord.HTTPException:
            pass


# =========================================================
# SPRACHE EINSTELLEN
# =========================================================

@bot.tree.command(
    name="sprache",
    description="Stellt die Sprache des Bots ein."
)
@app_commands.describe(language="Wähle eine Sprache.")
@app_commands.choices(language=LANGUAGES)
@app_commands.checks.has_permissions(manage_guild=True)
async def sprache(interaction, language: app_commands.Choice[str]):
    config = get_config(interaction.guild_id)
    config["language"] = language.value
    save_data()

    await reply(
        interaction,
        f"Die Sprache wurde auf {language.name} eingestellt."
    )


# =========================================================
# TICKET-PANEL ERSTELLEN
# =========================================================

@bot.tree.command(
    name="ticketcreat",
    description="Erstellt ein Ticket-Panel mit bis zu fünf Buttons."
)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(
    support_role="Die Rolle, die Tickets verwalten darf.",
    category="Die Kategorie für neue Tickets.",
    button_count="Anzahl der Buttons von 1 bis 5.",
    welcome_message="Nachricht im Ticket.",
    button1="Beschriftung des ersten Buttons.",
    button2="Beschriftung des zweiten Buttons.",
    button3="Beschriftung des dritten Buttons.",
    button4="Beschriftung des vierten Buttons.",
    button5="Beschriftung des fünften Buttons."
)
async def ticketcreat(
    interaction: discord.Interaction,
    support_role: discord.Role,
    category: discord.CategoryChannel,
    button_count: app_commands.Range[int, 1, 5],
    welcome_message: str,
    button1: str = "Support",
    button2: str = "Technische Hilfe",
    button3: str = "Frage",
    button4: str = "Meldung",
    button5: str = "Sonstiges"
):
    config = get_config(interaction.guild_id)

    labels = [
        button1,
        button2,
        button3,
        button4,
        button5
    ][:button_count]

    config["support_role_id"] = support_role.id
    config["category_id"] = category.id
    config["welcome_message"] = welcome_message
    config["button_labels"] = labels

    save_data()

    embed = discord.Embed(
        title="Support",
        description=welcome_message,
        color=discord.Color.blurple()
    )

    embed.set_footer(
        text="Wähle unten die passende Ticket-Kategorie."
    )

    await interaction.response.send_message(
        "Das Ticket-Panel wird erstellt.",
        ephemeral=True
    )

    await interaction.channel.send(
        embed=embed,
        view=TicketPanelView(interaction.guild_id, labels)
    )


# =========================================================
# TICKET-EINSTELLUNGEN BEARBEITEN
# =========================================================

@bot.tree.command(
    name="ticketbearbeiten",
    description="Ändert die Ticket-Nachricht und Button-Beschriftungen."
)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(
    welcome_message="Neue Nachricht für Tickets.",
    button1="Neue Beschriftung des ersten Buttons.",
    button2="Neue Beschriftung des zweiten Buttons.",
    button3="Neue Beschriftung des dritten Buttons.",
    button4="Neue Beschriftung des vierten Buttons.",
    button5="Neue Beschriftung des fünften Buttons."
)
async def ticketbearbeiten(
    interaction: discord.Interaction,
    welcome_message: str,
    button1: str = None,
    button2: str = None,
    button3: str = None,
    button4: str = None,
    button5: str = None
):
    config = get_config(interaction.guild_id)
    config["welcome_message"] = welcome_message

    supplied = [button1, button2, button3, button4, button5]
    old_labels = config.get("button_labels", ["Support"])

    new_labels = []

    for index, old_label in enumerate(old_labels[:5]):
        new_labels.append(
            supplied[index]
            if supplied[index] is not None
            else old_label
        )

    config["button_labels"] = new_labels or ["Support"]
    save_data()

    await reply(
        interaction,
        "Die Einstellungen wurden gespeichert. "
        "Führe /ticketcreat erneut aus, um ein neues Panel zu veröffentlichen."
    )


# =========================================================
# LOG-KANAL EINSTELLEN
# =========================================================

@bot.tree.command(
    name="ticketlog-set",
    description="Legt den Kanal für Ticket-Logs fest."
)
@app_commands.checks.has_permissions(manage_guild=True)
async def ticketlog_set(
    interaction: discord.Interaction,
    channel: discord.TextChannel
):
    config = get_config(interaction.guild_id)
    config["log_channel_id"] = channel.id
    save_data()

    await reply(
        interaction,
        f"Der Ticket-Log-Kanal wurde auf {channel.mention} eingestellt."
    )


# =========================================================
# ANMELDEPANEL-KANAL EINSTELLEN
# =========================================================

@bot.tree.command(
    name="kanal-set",
    description="Speichert den Kanal für das Anmeldepanel."
)
@app_commands.checks.has_permissions(manage_guild=True)
async def kanal_set(
    interaction: discord.Interaction,
    channel: discord.TextChannel
):
    config = get_config(interaction.guild_id)
    config["panel_channel_id"] = channel.id
    save_data()

    await reply(
        interaction,
        f"Der Anmeldepanel-Kanal ist jetzt {channel.mention}."
    )


# =========================================================
# ANMELDEPANEL
# =========================================================

class RegistrationView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Anmelden",
        style=discord.ButtonStyle.success,
        custom_id="registration:register"
    )
    async def register(self, interaction, button):
        await reply(
            interaction,
            "Willkommen! Bitte lies die Serverregeln. "
            "Bei Fragen wende dich an das Team."
        )


@bot.command(name="anmeldepannel", aliases=["anmeldepanel"])
@commands.has_guild_permissions(manage_guild=True)
async def anmeldepannel(ctx):
    embed = discord.Embed(
        title="Server-Anmeldung",
        description=(
            "Willkommen auf dem Server!\n\n"
            "Klicke auf **Anmelden**, um die Anmeldehinweise zu erhalten."
        ),
        color=discord.Color.blurple()
    )

    await ctx.send(embed=embed, view=RegistrationView())


# =========================================================
# FEHLERBEHANDLUNG
# =========================================================

async def handle_app_error(interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        message = "Du benötigst die Berechtigung „Server verwalten“."
    else:
        logging.error(
            "Slash-Befehl fehlgeschlagen",
            exc_info=(type(error), error, error.__traceback__)
        )
        message = "Der Befehl ist fehlgeschlagen. Prüfe die Railway-Logs."

    await reply(interaction, message)


@sprache.error
@ticketcreat.error
@ticketbearbeiten.error
@ticketlog_set.error
@kanal_set.error
async def admin_command_error(interaction, error):
    await handle_app_error(interaction, error)


@anmeldepannel.error
async def registration_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.reply(
            "Du benötigst die Berechtigung „Server verwalten“."
        )
    else:
        logging.error(
            "Anmeldepanel fehlgeschlagen",
            exc_info=(type(error), error, error.__traceback__)
        )
        await ctx.reply("Das Anmeldepanel konnte nicht erstellt werden.")


# =========================================================
# BOT STARTEN
# =========================================================

@bot.event
async def on_ready():
    if getattr(bot, "views_registered", False):
        logging.info("Bot ist online als %s", bot.user)
        return

    # Bereits gespeicherte Ticket-Panels wieder aktivieren.
    for guild_key, config in data.get("guilds", {}).items():
        try:
            guild_id = int(guild_key)
            labels = config.get("button_labels", ["Support"])

            bot.add_view(
                TicketPanelView(guild_id, labels)
            )
        except Exception:
            logging.exception(
                "Ticket-Panel konnte nicht registriert werden."
            )

    # Steuerbuttons bestehender Tickets wieder aktivieren.
    for channel_key in data.get("tickets", {}):
        try:
            bot.add_view(
                TicketControlView(int(channel_key))
            )
        except Exception:
            logging.exception(
                "Ticket-Steuerung konnte nicht registriert werden."
            )

    bot.add_view(RegistrationView())

    try:
        synced = await bot.tree.sync()
        logging.info(
            "%s Slash-Befehle synchronisiert.",
            len(synced)
        )
    except Exception:
        logging.exception(
            "Slash-Befehle konnten nicht synchronisiert werden."
        )

    bot.views_registered = True
    logging.info("Bot ist online als %s", bot.user)


if __name__ == "__main__":
    load_data()

    if not TOKEN:
        raise RuntimeError(
            "DISCORD_TOKEN fehlt. Füge deinen Bot-Token bei Railway "
            "unter Variables als DISCORD_TOKEN ein."
        )

    bot.run(TOKEN)
