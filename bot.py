
import os
import io
import json
import html
from datetime import datetime, timezone

import discord
from discord.ext import commands
from discord import app_commands


# ============================================================
# GRUNDEINSTELLUNGEN
# ============================================================

TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN fehlt in den Railway-Variablen!")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)


def load_json(filename, default):
    try:
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(filename, data):
    temporary = filename + ".tmp"
    with open(temporary, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
    os.replace(temporary, filename)


signins = load_json("signins.json", {})
languages = load_json("languages.json", {})
config = load_json("config.json", {})
tickets = load_json("tickets.json", {})

config.setdefault("announcement_channel_id", None)
config.setdefault("ticket_log_channel_id", None)
config.setdefault("support_role_id", None)
config.setdefault("ticket_category_id", None)
config.setdefault("ticket_button_labels", ["Support"])
config.setdefault("ticket_welcome_title", "🎫 KingSnowy Support")
config.setdefault(
    "ticket_welcome_message",
    "Hallo! Beschreibe dein Problem möglichst genau. "
    "Unser Support-Team hilft dir so schnell wie möglich."
)
config.setdefault("ticket_panels", [])

views_registered = False


def save_signins():
    save_json("signins.json", signins)


def save_languages():
    save_json("languages.json", languages)


def save_config():
    save_json("config.json", config)


def save_tickets():
    save_json("tickets.json", tickets)


# ============================================================
# HILFSFUNKTIONEN
# ============================================================

async def get_text_channel(channel_id):
    if not channel_id:
        return None

    channel = bot.get_channel(int(channel_id))

    if channel is None:
        try:
            channel = await bot.fetch_channel(int(channel_id))
        except (
            discord.NotFound,
            discord.Forbidden,
            discord.HTTPException,
        ):
            return None

    if isinstance(channel, discord.TextChannel):
        return channel

    return None


async def send_announcement(embed):
    channel = await get_text_channel(
        config.get("announcement_channel_id")
    )

    if channel is None:
        print("Anmeldungs-Log ist nicht eingestellt.")
        return

    try:
        await channel.send(embed=embed)
    except discord.HTTPException as error:
        print(f"Anmeldungs-Log-Fehler: {error}")


async def send_ticket_log(message, file=None):
    """Ticket-Logs werden nur beim endgültigen Schließen aufgerufen."""
    channel = await get_text_channel(
        config.get("ticket_log_channel_id")
    )

    if channel is None:
        print("Ticket-Log-Kanal ist nicht eingestellt.")
        return

    try:
        await channel.send(content=message, file=file)
    except discord.HTTPException as error:
        print(f"Ticket-Log-Fehler: {error}")


def is_support_member(member):
    if not isinstance(member, discord.Member):
        return False

    if (
        member.guild_permissions.administrator
        or member.guild_permissions.manage_channels
    ):
        return True

    role_id = config.get("support_role_id")

    if role_id:
        return any(
            role.id == int(role_id)
            for role in member.roles
        )

    return member.guild_permissions.manage_messages


async def private_reply(interaction, message):
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


# ============================================================
# TICKET-KANALDATEN
# ============================================================

def make_topic(
    owner_id,
    claimed_id=0,
    owner_vote=0,
    support_vote=0,
):
    return (
        f"ks_ticket|owner:{owner_id}"
        f"|claimed:{claimed_id}"
        f"|owner_vote:{owner_vote}"
        f"|support_vote:{support_vote}"
    )


def parse_topic(topic):
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
            pass

    return result


async def update_topic(channel, **updates):
    data = parse_topic(channel.topic)

    if data is None:
        return None

    data.update(updates)

    await channel.edit(
        topic=make_topic(
            data.get("owner", 0),
            data.get("claimed", 0),
            data.get("owner_vote", 0),
            data.get("support_vote", 0),
        )
    )

    return data


async def get_member_name(guild, user_id):
    if not user_id:
        return "Niemand"

    member = guild.get_member(int(user_id))

    if member is None:
        try:
            member = await guild.fetch_member(int(user_id))
        except (
            discord.NotFound,
            discord.Forbidden,
            discord.HTTPException,
        ):
            return f"Unbekannt (ID: {user_id})"

    return f"{member} (ID: {member.id})"


# ============================================================
# ANMELDESYSTEM
# ============================================================

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
            "signed_in_at": datetime.now(timezone.utc).isoformat(),
        }

        save_signins()

        # Der Anmeldungs-Log ist immer auf Englisch.
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
            "✅ Du bist angemeldet!",
            ephemeral=True,
        )


class LanguageSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(
            placeholder="Choose your language / Sprache auswählen",
            min_values=1,
            max_values=1,
            options=[
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
            ],
            custom_id="kingsnowy:language",
        )

    async def callback(self, interaction: discord.Interaction):
        languages[str(interaction.user.id)] = self.values[0]
        save_languages()

        messages = {
            "en": "Language set to English.",
            "de": "Sprache auf Deutsch eingestellt.",
            "cs": "Jazyk byl eingestellt.",
        }

        await interaction.response.send_message(
            messages[self.values[0]],
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
        custom_id="kingsnowy:signin",
    )
    async def signin(self, interaction, button):
        user_id = str(interaction.user.id)

        if user_id in signins:
            await private_reply(
                interaction,
                "Du bist bereits angemeldet. Melde dich zuerst ab.",
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
            await private_reply(
                interaction,
                "Du bist aktuell nicht angemeldet.",
            )
            return

        saved = signins.pop(user_id)
        save_signins()

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
@app_commands.describe(
    channel="Kanal für Anmeldungen und Abmeldungen"
)
async def kanal_set(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
):
    config["announcement_channel_id"] = channel.id
    save_config()

    await interaction.response.send_message(
        f"✅ Anmeldungs-Log eingestellt: {channel.mention}",
        ephemeral=True,
    )


# ============================================================
# TICKET-WILLKOMMENSTEXT
# ============================================================

def make_welcome_embed(user):
    title = config.get(
        "ticket_welcome_title",
        "🎫 KingSnowy Support",
    )
    description = config.get(
        "ticket_welcome_message",
        "Beschreibe dein Problem möglichst genau.",
    )

    embed = discord.Embed(
        title=title,
        description=f"{user.mention}\n\n{description}",
        color=discord.Color.blue(),
        timestamp=datetime.now(timezone.utc),
    )

    embed.set_footer(text="KingSnowy Support")
    return embed


class EditTicketMessageModal(discord.ui.Modal):
    def __init__(self, message_id, current_embed):
        super().__init__(title="Ticket-Nachricht bearbeiten")
        self.message_id = message_id

        current_title = current_embed.title if current_embed else ""
        current_description = (
            current_embed.description if current_embed else ""
        )

        self.title_input = discord.ui.TextInput(
            label="Titel",
            default=current_title[:256] if current_title else "",
            max_length=256,
            required=True,
        )

        self.description_input = discord.ui.TextInput(
            label="Beschreibung",
            default=(
                current_description[:4000]
                if current_description else ""
            ),
            style=discord.TextStyle.paragraph,
            max_length=4000,
            required=True,
        )

        self.add_item(self.title_input)
        self.add_item(self.description_input)

    async def on_submit(self, interaction: discord.Interaction):
        if not is_support_member(interaction.user):
            await private_reply(
                interaction,
                "❌ Nur das Support-Team darf diese Nachricht bearbeiten.",
            )
            return

        if not isinstance(interaction.channel, discord.TextChannel):
            await private_reply(interaction, "Ungültiger Kanal.")
            return

        try:
            message = await interaction.channel.fetch_message(
                self.message_id
            )
        except (
            discord.NotFound,
            discord.Forbidden,
            discord.HTTPException,
        ):
            await private_reply(interaction, "Nachricht nicht gefunden.")
            return

        if message.author.id != bot.user.id:
            await private_reply(
                interaction,
                "Ich kann nur meine eigenen Bot-Nachrichten bearbeiten.",
            )
            return

        embed = discord.Embed(
            title=self.title_input.value,
            description=self.description_input.value,
            color=discord.Color.blue(),
        )

        try:
            await message.edit(embed=embed)
        except discord.HTTPException as error:
            await private_reply(
                interaction,
                f"Bearbeiten fehlgeschlagen: {error}",
            )
            return

        await interaction.response.send_message(
            "✅ Die Nachricht wurde bearbeitet.",
            ephemeral=True,
        )


# ============================================================
# TICKET-STEUERUNGSBUTTONS
# ============================================================

class TicketControlsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Ticket übernehmen",
        emoji="🛡️",
        style=discord.ButtonStyle.success,
        custom_id="kingsnowy:ticket:claim",
    )
    async def claim(self, interaction, button):
        channel = interaction.channel

        if not isinstance(channel, discord.TextChannel):
            await private_reply(interaction, "Ungültiger Kanal.")
            return

        data = parse_topic(channel.topic)

        if not data:
            await private_reply(interaction, "Ticket nicht gefunden.")
            return

        if not is_support_member(interaction.user):
            await private_reply(
                interaction,
                "Nur das Support-Team darf Tickets übernehmen.",
            )
            return

        if data.get("claimed"):
            await private_reply(
                interaction,
                f"Dieses Ticket wurde bereits von <@{data['claimed']}> "
                "übernommen.",
            )
            return

        await update_topic(channel, claimed=interaction.user.id)

        await interaction.response.send_message(
            f"🛡️ {interaction.user.mention} hat das Ticket übernommen."
        )

    @discord.ui.button(
        label="Übernahme freigeben",
        emoji="↩️",
        style=discord.ButtonStyle.secondary,
        custom_id="kingsnowy:ticket:release",
    )
    async def release(self, interaction, button):
        channel = interaction.channel

        if not isinstance(channel, discord.TextChannel):
            await private_reply(interaction, "Ungültiger Kanal.")
            return

        data = parse_topic(channel.topic)

        if not data:
            await private_reply(interaction, "Ticket nicht gefunden.")
            return

        if not is_support_member(interaction.user):
            await private_reply(
                interaction,
                "Nur das Support-Team darf die Übernahme freigeben.",
            )
            return

        claimed_id = data.get("claimed", 0)

        if (
            claimed_id
            and claimed_id != interaction.user.id
            and not interaction.user.guild_permissions.administrator
        ):
            await private_reply(
                interaction,
                "Nur der zuständige Support oder ein Administrator darf "
                "die Übernahme freigeben.",
            )
            return

        await update_topic(channel, claimed=0)

        await interaction.response.send_message(
            f"↩️ {interaction.user.mention} hat die Übernahme freigegeben."
        )

    @discord.ui.button(
        label="Nachricht bearbeiten",
        emoji="✏️",
        style=discord.ButtonStyle.secondary,
        custom_id="kingsnowy:ticket:edit",
    )
    async def edit_message(self, interaction, button):
        if not is_support_member(interaction.user):
            await private_reply(
                interaction,
                "Nur das Support-Team darf Nachrichten bearbeiten.",
            )
            return

        embed = (
            interaction.message.embeds[0]
            if interaction.message.embeds else None
        )

        await interaction.response.send_modal(
            EditTicketMessageModal(
                interaction.message.id,
                embed,
            )
        )

    @discord.ui.button(
        label="Schließen anfordern",
        emoji="🔒",
        style=discord.ButtonStyle.danger,
        custom_id="kingsnowy:ticket:close-request",
    )
    async def request_close(self, interaction, button):
        channel = interaction.channel

        if not isinstance(channel, discord.TextChannel):
            await private_reply(interaction, "Ungültiger Kanal.")
            return

        data = parse_topic(channel.topic)

        if not data:
            await private_reply(interaction, "Ticket nicht gefunden.")
            return

        is_owner = interaction.user.id == data.get("owner")
        is_support = is_support_member(interaction.user)

        if not is_owner and not is_support:
            await private_reply(
                interaction,
                "Nur der Ticket-Ersteller oder Support kann das anfordern.",
            )
            return

        await update_topic(
            channel,
            owner_vote=0,
            support_vote=0,
        )

        embed = discord.Embed(
            title="🔒 Ticket-Schließung",
            description=(
                "Zum endgültigen Schließen sind zwei Bestätigungen nötig:\n\n"
                "• Ticket-Ersteller\n"
                "• Support-Team"
            ),
            color=discord.Color.orange(),
        )

        await interaction.response.send_message(
            embed=embed,
            view=CloseVoteView(),
        )


# ============================================================
# SCHLIESSEN MIT ZWEI BESTÄTIGUNGEN
# ============================================================

class CloseVoteView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Als Ersteller bestätigen",
        emoji="👤",
        style=discord.ButtonStyle.primary,
        custom_id="kingsnowy:close:owner",
    )
    async def owner_vote(self, interaction, button):
        channel = interaction.channel

        if not isinstance(channel, discord.TextChannel):
            await private_reply(interaction, "Ungültiger Kanal.")
            return

        data = parse_topic(channel.topic)

        if not data:
            await private_reply(interaction, "Ticket nicht gefunden.")
            return

        if interaction.user.id != data.get("owner"):
            await private_reply(
                interaction,
                "Nur der Ticket-Ersteller darf hier bestätigen.",
            )
            return

        await update_topic(channel, owner_vote=1)
        await self.check_close(interaction, channel)

    @discord.ui.button(
        label="Als Support bestätigen",
        emoji="🛡️",
        style=discord.ButtonStyle.success,
        custom_id="kingsnowy:close:support",
    )
    async def support_vote(self, interaction, button):
        channel = interaction.channel

        if not isinstance(channel, discord.TextChannel):
            await private_reply(interaction, "Ungültiger Kanal.")
            return

        data = parse_topic(channel.topic)

        if not data:
            await private_reply(interaction, "Ticket nicht gefunden.")
            return

        if not is_support_member(interaction.user):
            await private_reply(
                interaction,
                "Nur das Support-Team darf hier bestätigen.",
            )
            return

        await update_topic(channel, support_vote=1)
        await self.check_close(interaction, channel)

    async def check_close(self, interaction, channel):
        data = parse_topic(channel.topic)

        if (
            data
            and data.get("owner_vote")
            and data.get("support_vote")
        ):
            await interaction.response.send_message(
                "✅ Beide Bestätigungen liegen vor. Das Ticket wird geschlossen.",
                ephemeral=True,
            )
            await close_ticket(channel, interaction.user)
        else:
            await interaction.response.send_message(
                "✅ Bestätigung gespeichert. Die zweite Bestätigung fehlt noch.",
                ephemeral=True,
            )


# ============================================================
# HTML-TRANSKRIPT MIT TICKET-INFORMATIONEN
# ============================================================

async def create_ticket_transcript(channel, data, closed_by):
    owner_id = data.get("owner", 0)
    claimed_id = data.get("claimed", 0)

    owner_name = await get_member_name(channel.guild, owner_id)
    claimed_name = await get_member_name(channel.guild, claimed_id)
    closed_name = await get_member_name(
        channel.guild,
        closed_by.id,
    )

    created_at = channel.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
    closed_at = datetime.now(timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )

    lines = [
        "<!DOCTYPE html>",
        "<html><head><meta charset='utf-8'>",
        f"<title>Transcript - {html.escape(channel.name)}</title>",
        """
        <style>
        body { background: #f3f4f6; font-family: Arial, sans-serif;
               padding: 24px; color: #222; }
        h1, h2 { color: #26354a; }
        .info, .message { background: white; padding: 14px;
               border-radius: 8px; margin: 12px 0; }
        .meta { color: #666; font-size: 12px; }
        .content { white-space: pre-wrap; overflow-wrap: anywhere; }
        a { overflow-wrap: anywhere; }
        </style></head><body>",
        "<h1>KingSnowy - Ticket Transcript</h1>",
        "<div class='info'>",
        f"<p><b>Ticket:</b> {html.escape(channel.name)}</p>",
        f"<p><b>Ticket-Ersteller:</b> {html.escape(owner_name)}</p>",
        f"<p><b>Übernommen von:</b> {html.escape(claimed_name)}</p>",
        f"<p><b>Geschlossen von:</b> {html.escape(closed_name)}</p>",
        f"<p><b>Erstellt am:</b> {html.escape(created_at)}</p>",
        f"<p><b>Geschlossen am:</b> {html.escape(closed_at)}</p>",
        f"<p><b>Ticket-Kanal-ID:</b> {channel.id}</p>",
        "</div>",
        "<h2>Nachrichtenverlauf</h2>",
    ]

    async for message in channel.history(
        limit=None,
        oldest_first=True,
    ):
        author = html.escape(str(message.author))
        timestamp = message.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        content = html.escape(message.content or "")

        lines.append("<div class='message'>")
        lines.append(f"<b>{author}</b>")
        lines.append(
            f"<div class='meta'>{html.escape(timestamp)} "
            f"| User-ID: {message.author.id}</div>"
        )

        if content:
            lines.append(f"<div class='content'>{content}</div>")

        for embed in message.embeds:
            if embed.title:
                lines.append(
                    f"<p><b>{html.escape(embed.title)}</b></p>"
                )
            if embed.description:
                lines.append(
                    f"<div class='content'>"
                    f"{html.escape(embed.description)}</div>"
                )

        for attachment in message.attachments:
            lines.append(
                "<p>Anhang: <a href='"
                + html.escape(attachment.url, quote=True)
                + "'>"
                + html.escape(attachment.filename)
                + "</a></p>"
            )

        lines.append("</div>")

    lines.append("</body></html>")
    return "\n".join(lines).encode("utf-8")


async def close_ticket(channel, closed_by):
    data = parse_topic(channel.topic)

    if not data:
        return

    try:
        transcript = await create_ticket_transcript(
            channel,
            data,
            closed_by,
        )

        owner_name = await get_member_name(
            channel.guild,
            data.get("owner", 0),
        )
        claimed_name = await get_member_name(
            channel.guild,
            data.get("claimed", 0),
        )
        closed_name = await get_member_name(
            channel.guild,
            closed_by.id,
        )

        # ERST JETZT wird eine Nachricht an den Ticket-Log-Kanal gesendet.
        # Sie enthält das Transkript als HTML-Datei.
        log_message = (
            f"🔒 **Ticket geschlossen: {channel.name}**\n"
            f"**Ersteller:** {owner_name}\n"
            f"**Übernommen von:** {claimed_name}\n"
            f"**Geschlossen von:** {closed_name}\n"
            f"**Erstellt:** {channel.created_at.strftime('%Y-%m-%d %H:%M UTC')}"
        )

        await send_ticket_log(
            log_message,
            file=discord.File(
                io.BytesIO(transcript),
                filename=f"{channel.name}-transcript.html",
            ),
        )

        tickets.pop(str(channel.id), None)
        save_tickets()

        await channel.delete(
            reason=f"Ticket closed by {closed_by}"
        )

    except discord.HTTPException as error:
        print(f"Fehler beim Schließen des Tickets: {error}")


# ============================================================
# TICKET-PANEL: BIS ZU FÜNF BUTTONS
# ============================================================

class TicketPanelButton(discord.ui.Button):
    def __init__(self, index, label):
        super().__init__(
            label=label[:80],
            style=discord.ButtonStyle.primary,
            emoji="🎫",
            custom_id=f"kingsnowy:panel:{index}",
            row=(index - 1) // 5,
        )
        self.index = index

    async def callback(self, interaction: discord.Interaction):
        await create_ticket_from_button(
            interaction,
            self.index,
        )


class TicketPanelView(discord.ui.View):
    def __init__(self, labels=None):
        super().__init__(timeout=None)

        if labels is None:
            labels = config.get("ticket_button_labels", ["Support"])

        for index, label in enumerate(labels[:5], start=1):
            if label and label.strip():
                self.add_item(
                    TicketPanelButton(index, label.strip())
                )


async def create_ticket_from_button(interaction, button_index):
    guild = interaction.guild

    if guild is None:
        await private_reply(
            interaction,
            "Tickets können nur auf einem Server erstellt werden.",
        )
        return

    await interaction.response.defer(ephemeral=True)

    # Pro Nutzer nur ein offenes Ticket.
    for channel in guild.text_channels:
        data = parse_topic(channel.topic)
        if data and data.get("owner") == interaction.user.id:
            await interaction.followup.send(
                f"Du hast bereits ein offenes Ticket: {channel.mention}",
                ephemeral=True,
            )
            return

    role_id = config.get("support_role_id")
    category_id = config.get("ticket_category_id")

    support_role = guild.get_role(int(role_id)) if role_id else None
    category = (
        guild.get_channel(int(category_id))
        if category_id else None
    )

    if support_role is None:
        await interaction.followup.send(
            "❌ Die Support-Rolle ist nicht konfiguriert. "
            "Führe `/ticketcreat` erneut aus.",
            ephemeral=True,
        )
        return

    if not isinstance(category, discord.CategoryChannel):
        await interaction.followup.send(
            "❌ Die Ticket-Kategorie ist nicht konfiguriert. "
            "Führe `/ticketcreat` erneut aus.",
            ephemeral=True,
        )
        return

    bot_member = guild.me

    if bot_member is None:
        await interaction.followup.send(
            "Der Bot konnte nicht als Servermitglied gefunden werden.",
            ephemeral=True,
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
        support_role: discord.PermissionOverwrite(
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

    safe_name = (
        interaction.user.name.lower()
        .replace(" ", "-")
        .replace(".", "-")
    )
    safe_name = "".join(
        char for char in safe_name
        if char.isalnum() or char == "-"
    )[:25] or "user"

    try:
        channel = await guild.create_text_channel(
            name=f"ticket-{safe_name}",
            category=category,
            overwrites=overwrites,
            topic=make_topic(interaction.user.id),
            reason=f"Ticket created by {interaction.user}",
        )
    except (discord.Forbidden, discord.HTTPException) as error:
        await interaction.followup.send(
            f"❌ Ticket konnte nicht erstellt werden: {error}",
            ephemeral=True,
        )
        return

    labels = config.get("ticket_button_labels", ["Support"])
    button_label = (
        labels[button_index - 1]
        if len(labels) >= button_index
        else "Support"
    )

    tickets[str(channel.id)] = {
        "owner_id": interaction.user.id,
        "guild_id": guild.id,
        "button_index": button_index,
        "button_label": button_label,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "welcome_message_id": None,
    }
    save_tickets()

    welcome_embed = make_welcome_embed(interaction.user)

    try:
        welcome_message = await channel.send(
            content=f"{interaction.user.mention} · **{button_label}**",
            embed=welcome_embed,
            view=TicketControlsView(),
        )

        tickets[str(channel.id)]["welcome_message_id"] = welcome_message.id
        save_tickets()

    except discord.HTTPException as error:
        await interaction.followup.send(
            f"Ticket wurde erstellt, aber die Willkommensnachricht "
            f"konnte nicht gesendet werden: {error}",
            ephemeral=True,
        )
        return

    # Hier wird absichtlich KEIN Ticket-Log gesendet.
    await interaction.followup.send(
        f"✅ Dein Ticket wurde erstellt: {channel.mention}",
        ephemeral=True,
    )


# ============================================================
# SLASH /ticketcreat
# WÄHLT SUPPORT-ROLLE, KATEGORIE UND BIS ZU 5 BUTTONS
# ============================================================

@bot.tree.command(
    name="ticketcreat",
    description="Ticket-Panel konfigurieren und erstellen",
)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(
    support_rolle="Rolle, die alle Tickets sehen und bearbeiten darf",
    kategorie="Kategorie, in der neue Tickets erstellt werden",
    button1="Beschriftung des ersten Ticket-Buttons",
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
        value.strip()
        for value in [
            button1, button2, button3, button4, button5
        ]
        if value and value.strip()
    ]

    if not labels:
        await private_reply(
            interaction,
            "Du musst mindestens einen Button angeben.",
        )
        return

    if any(len(label) > 80 for label in labels):
        await private_reply(
            interaction,
            "Jede Button-Beschriftung darf höchstens 80 Zeichen lang sein.",
        )
        return

    config["support_role_id"] = support_rolle.id
    config["ticket_category_id"] = kategorie.id
    config["ticket_button_labels"] = labels
    save_config()

    embed = discord.Embed(
        title="🎫 KingSnowy Support",
        description=(
            "Wähle unten den passenden Button, um ein privates Ticket "
            "zu erstellen.\n\n"
            "Bitte erstelle nur ein Ticket pro Anliegen."
        ),
        color=discord.Color.blue(),
    )

    view = TicketPanelView(labels)

    await interaction.response.send_message(
        embed=embed,
        view=view,
    )

    panel_message = await interaction.original_response()

    panels = config.get("ticket_panels", [])
    panels.append({
        "guild_id": interaction.guild_id,
        "channel_id": interaction.channel_id,
        "message_id": panel_message.id,
    })
    config["ticket_panels"] = panels
    save_config()


# ============================================================
# SLASH /ticketbearbeiten
# WILLKOMMENSTEXT UND PANEL-BUTTONS BEARBEITEN
# ============================================================

@bot.tree.command(
    name="ticketbearbeiten",
    description="Willkommensnachricht und Ticket-Buttons bearbeiten",
)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(
    titel="Neuer Titel der Willkommensnachricht",
    nachricht="Neuer Text der Willkommensnachricht",
    button1="Neue Beschriftung des ersten Buttons",
    button2="Neue Beschriftung des zweiten Buttons",
    button3="Neue Beschriftung des dritten Buttons",
    button4="Neue Beschriftung des vierten Buttons",
    button5="Neue Beschriftung des fünften Buttons",
)
async def ticketbearbeiten(
    interaction: discord.Interaction,
    titel: str = None,
    nachricht: str = None,
    button1: str = None,
    button2: str = None,
    button3: str = None,
    button4: str = None,
    button5: str = None,
):
    if titel is not None:
        if not titel.strip() or len(titel) > 256:
            await private_reply(
                interaction,
                "Der Titel muss 1 bis 256 Zeichen lang sein.",
            )
            return
        config["ticket_welcome_title"] = titel.strip()

    if nachricht is not None:
        if not nachricht.strip() or len(nachricht) > 4000:
            await private_reply(
                interaction,
                "Die Nachricht muss 1 bis 4000 Zeichen lang sein.",
            )
            return
        config["ticket_welcome_message"] = nachricht.strip()

    labels = list(config.get("ticket_button_labels", ["Support"]))
    labels = labels[:5]

    while len(labels) < 5:
        labels.append("")

    new_labels = [button1, button2, button3, button4, button5]

    for index, label in enumerate(new_labels):
        if label is None:
            continue

        label = label.strip()

        if not label or len(label) > 80:
            await private_reply(
                interaction,
                f"Button {index + 1}: Die Beschriftung muss "
                "1 bis 80 Zeichen lang sein.",
            )
            return

        labels[index] = label

    # Leere Buttons am Ende entfernen.
    while labels and not labels[-1]:
        labels.pop()

    if not labels:
        labels = ["Support"]

    config["ticket_button_labels"] = labels
    save_config()

    await interaction.response.defer(ephemeral=True)

    # Vorhandene Ticket-Panels aktualisieren.
    updated_panels = []

    for panel in config.get("ticket_panels", []):
        if panel.get("guild_id") != interaction.guild_id:
            updated_panels.append(panel)
            continue

        channel = await get_text_channel(panel.get("channel_id"))

        if channel is None:
            continue

        try:
            message = await channel.fetch_message(
                int(panel["message_id"])
            )

            panel_embed = discord.Embed(
                title="🎫 KingSnowy Support",
                description=(
                    "Wähle unten den passenden Button, um ein privates "
                    "Ticket zu erstellen.\n\n"
                    "Bitte erstelle nur ein Ticket pro Anliegen."
                ),
                color=discord.Color.blue(),
            )

            await message.edit(
                embed=panel_embed,
                view=TicketPanelView(labels),
            )
            updated_panels.append(panel)

        except (
            discord.NotFound,
            discord.Forbidden,
            discord.HTTPException,
        ):
            pass

    config["ticket_panels"] = updated_panels
    save_config()

    # Auch offene Ticket-Willkommensnachrichten aktualisieren.
    updated_tickets = 0

    for channel_id, ticket_data in list(tickets.items()):
        if ticket_data.get("guild_id") != interaction.guild_id:
            continue

        channel = interaction.guild.get_channel(int(channel_id))

        if not isinstance(channel, discord.TextChannel):
            continue

        welcome_message_id = ticket_data.get("welcome_message_id")

        if not welcome_message_id:
            continue

        try:
            message = await channel.fetch_message(
                int(welcome_message_id)
            )

            owner_id = ticket_data.get("owner_id")
            member = interaction.guild.get_member(int(owner_id))

            if member is None:
                try:
                    member = await interaction.guild.fetch_member(
                        int(owner_id)
                    )
                except (
                    discord.NotFound,
                    discord.Forbidden,
                    discord.HTTPException,
                ):
                    continue

            button_label = ticket_data.get("button_label", "Support")

            await message.edit(
                content=f"{member.mention} · **{button_label}**",
                embed=make_welcome_embed(member),
                view=TicketControlsView(),
            )
            updated_tickets += 1

        except (
            discord.NotFound,
            discord.Forbidden,
            discord.HTTPException,
        ):
            continue

    save_tickets()

    preview = discord.Embed(
        title=config["ticket_welcome_title"],
        description=config["ticket_welcome_message"],
        color=discord.Color.blue(),
    )

    await interaction.followup.send(
        f"✅ Ticket-Einstellungen gespeichert.\n"
        f"Buttons: {len(labels)}\n"
        f"Aktualisierte offene Tickets: {updated_tickets}",
        embed=preview,
        ephemeral=True,
    )


# ============================================================
# TICKET-LOG UND SUPPORT-ROLLE
# ============================================================

@bot.tree.command(
    name="ticketlog-set",
    description="Kanal für Ticket-Logs und Transkripte festlegen",
)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(channel="Ticket-Log-Kanal")
async def ticketlog_set(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
):
    config["ticket_log_channel_id"] = channel.id
    save_config()

    await interaction.response.send_message(
        f"✅ Ticket-Log-Kanal: {channel.mention}\n"
        "Dort erscheint der Log erst beim Schließen eines Tickets.",
        ephemeral=True,
    )


@bot.tree.command(
    name="ticket-support-set",
    description="Support-Rolle festlegen",
)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(role="Support-Rolle")
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


# ============================================================
# FEHLERBEHANDLUNG
# ============================================================

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send(
            "❌ Du benötigst die Berechtigung „Server verwalten“."
        )
    elif isinstance(error, commands.CommandNotFound):
        return
    else:
        print(f"Prefix-Command-Fehler: {error}")
        await ctx.send(
            "❌ Fehler beim Ausführen des Befehls. "
            "Prüfe die Railway-Logs."
        )


@bot.tree.error
async def slash_error(interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        message = "❌ Du benötigst die Berechtigung „Server verwalten“."
    else:
        print(f"Slash-Command-Fehler: {error}")
        message = (
            "❌ Fehler beim Ausführen des Slash-Befehls. "
            "Prüfe die Railway-Logs."
        )

    await private_reply(interaction, message)


# ============================================================
# BOT START
# ============================================================

@bot.event
async def on_ready():
    global views_registered

    if not views_registered:
        bot.add_view(SignInView())
        bot.add_view(TicketControlsView())
        bot.add_view(CloseVoteView())

        labels = config.get("ticket_button_labels", ["Support"])
        bot.add_view(TicketPanelView(labels))

        views_registered = True

    try:
        synced = await bot.tree.sync()
        print(f"{len(synced)} Slash-Befehle synchronisiert.")
    except Exception as error:
        print(f"Slash-Befehle konnten nicht synchronisiert werden: {error}")

    print(f"KingSnowy Bot online: {bot.user}")


bot.run(TOKEN)
