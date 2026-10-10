
import os
import json
import re
from datetime import datetime, timezone

import discord
from discord.ext import commands
from discord import app_commands


# =====================================================
# EINSTELLUNGEN
# =====================================================

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
    temp = filename + ".tmp"
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
    os.replace(temp, filename)


signins = load_json("signins.json", {})
languages = load_json("languages.json", {})
config = load_json("config.json", {})
tickets = load_json("tickets.json", {})

config.setdefault("announcement_channel_id", None)
config.setdefault("ticket_log_channel_id", None)
config.setdefault("support_role_id", None)
config.setdefault("ticket_message_title", "🎫 KingSnowy Support")
config.setdefault(
    "ticket_message_description",
    "Hallo! Beschreibe dein Problem möglichst genau. "
    "Unser Support-Team hilft dir so schnell wie möglich."
)

views_registered = False


# =====================================================
# HILFSFUNKTIONEN
# =====================================================

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


async def send_announcement(embed):
    channel = await get_text_channel(
        config.get("announcement_channel_id")
    )
    if channel:
        try:
            await channel.send(embed=embed)
        except discord.HTTPException as error:
            print(f"Anmeldungs-Log-Fehler: {error}")


async def send_ticket_log(message):
    channel = await get_text_channel(
        config.get("ticket_log_channel_id")
    )
    if channel:
        try:
            await channel.send(message)
        except discord.HTTPException as error:
            print(f"Ticket-Log-Fehler: {error}")


def is_support_member(member):
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
        role_id
        and any(role.id == int(role_id) for role in member.roles)
    )


async def private_reply(interaction, message):
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


# =====================================================
# TICKET-KANALDATEN
# =====================================================

def make_topic(owner, claimed=0, owner_vote=0, support_vote=0):
    return (
        f"ks_ticket|owner:{owner}|claimed:{claimed}"
        f"|owner_vote:{owner_vote}|support_vote:{support_vote}"
    )


def parse_topic(topic):
    if not topic or not topic.startswith("ks_ticket|"):
        return None

    result = {}
    for part in topic.split("|")[1:]:
        if ":" in part:
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


# =====================================================
# ANMELDUNG
# =====================================================

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

    async def on_submit(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)

        signins[user_id] = {
            "dino": self.dinosaur.value,
            "skin": self.skin.value,
            "location": self.location.value,
            "username": interaction.user.name,
            "signed_in_at": datetime.now(timezone.utc).isoformat(),
        }
        save_json("signins.json", signins)

        # Anmeldungs-Log immer auf Englisch
        embed = discord.Embed(
            title="🟢 Player signed in",
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(
            name="Player", value=interaction.user.mention, inline=False
        )
        embed.add_field(
            name="Dinosaur", value=self.dinosaur.value, inline=True
        )
        embed.add_field(
            name="Skin", value=self.skin.value, inline=True
        )
        embed.add_field(
            name="Current location", value=self.location.value, inline=False
        )

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

    async def callback(self, interaction: discord.Interaction):
        languages[str(interaction.user.id)] = self.values[0]
        save_json("languages.json", languages)

        labels = {
            "en": "Language set to English.",
            "de": "Sprache auf Deutsch eingestellt.",
            "cs": "Jazyk byl nastaven.",
        }
        await interaction.response.send_message(
            labels[self.values[0]], ephemeral=True
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
        save_json("signins.json", signins)

        # Abmeldungs-Log immer auf Englisch
        embed = discord.Embed(
            title="🔴 Player signed out",
            color=discord.Color.red(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(
            name="Player", value=interaction.user.mention, inline=False
        )
        embed.add_field(
            name="Dinosaur", value=saved.get("dino", "Not specified"), inline=True
        )
        embed.add_field(
            name="Skin", value=saved.get("skin", "Not specified"), inline=True
        )

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
async def kanal_set(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
):
    config["announcement_channel_id"] = channel.id
    save_json("config.json", config)
    await interaction.response.send_message(
        f"✅ Anmeldungs-Log: {channel.mention}", ephemeral=True
    )


# =====================================================
# TICKET-NACHRICHT BEARBEITEN
# =====================================================

class EditTicketMessageModal(discord.ui.Modal):
    def __init__(self, message_id, embed):
        super().__init__(title="Ticket-Nachricht bearbeiten")
        self.message_id = message_id

        self.title_input = discord.ui.TextInput(
            label="Titel",
            default=(embed.title[:256] if embed and embed.title else ""),
            max_length=256,
            required=True,
        )
        self.description_input = discord.ui.TextInput(
            label="Beschreibung",
            default=(
                embed.description[:4000]
                if embed and embed.description else ""
            ),
            style=discord.TextStyle.paragraph,
            max_length=4000,
            required=True,
        )
        self.add_item(self.title_input)
        self.add_item(self.description_input)

    async def on_submit(self, interaction: discord.Interaction):
        if not is_support_member(interaction.user):
            await private_reply(interaction, "Nur Support darf das bearbeiten.")
            return

        if not isinstance(interaction.channel, discord.TextChannel):
            await private_reply(interaction, "Ungültiger Kanal.")
            return

        try:
            message = await interaction.channel.fetch_message(self.message_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            await private_reply(interaction, "Nachricht nicht gefunden.")
            return

        if message.author.id != bot.user.id:
            await private_reply(
                interaction, "Ich kann nur meine eigenen Bot-Nachrichten ändern."
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
                interaction, f"Bearbeiten fehlgeschlagen: {error}"
            )
            return

        await interaction.response.send_message(
            "✅ Ticket-Nachricht wurde bearbeitet.", ephemeral=True
        )


# =====================================================
# TICKET SCHLIESSEN – OHNE TRANSKRIPT
# =====================================================

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
                interaction, "Nur der Ticket-Ersteller darf das bestätigen."
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

        if not is_support_member(interaction.user):
            await private_reply(
                interaction, "Nur das Support-Team darf das bestätigen."
            )
            return

        await update_topic(channel, support_vote=1)
        await self.check_close(interaction, channel)

    async def check_close(self, interaction, channel):
        data = parse_topic(channel.topic)

        if data and data.get("owner_vote") and data.get("support_vote"):
            await interaction.response.send_message(
                "🔒 Beide Bestätigungen sind da. Ticket wird geschlossen.",
                ephemeral=True,
            )
            await close_ticket(channel, interaction.user)
        else:
            await interaction.response.send_message(
                "✅ Bestätigung gespeichert. Die zweite Bestätigung fehlt noch.",
                ephemeral=True,
            )


async def close_ticket(channel, closed_by):
    data = parse_topic(channel.topic)
    if not data:
        return

    tickets.pop(str(channel.id), None)
    save_json("tickets.json", tickets)

    await send_ticket_log(
        f"🔒 Ticket **{channel.name}** wurde von {closed_by.mention} geschlossen."
    )

    # Kein Transkript wird erstellt oder verschickt.
    await channel.delete(reason=f"Ticket closed by {closed_by}")


# =====================================================
# TICKET-STEUERUNG
# =====================================================

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
        if not is_support_member(interaction.user):
            await private_reply(interaction, "Nur Support darf Tickets übernehmen.")
            return
        if data.get("claimed"):
            await private_reply(
                interaction, f"Ticket bereits übernommen von <@{data['claimed']}>."
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
        if not is_support_member(interaction.user):
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
        await interaction.response.send_message(
            f"↩️ {interaction.user.mention} hat die Übernahme freigegeben."
        )

    @discord.ui.button(
        label="Ticket-Nachricht bearbeiten",
        emoji="✏️",
        style=discord.ButtonStyle.secondary,
        custom_id="kingsnowy:ticket-edit",
    )
    async def edit_message(self, interaction, button):
        if not is_support_member(interaction.user):
            await private_reply(interaction, "Nur Support darf das bearbeiten.")
            return

        embed = (
            interaction.message.embeds[0]
            if interaction.message.embeds else None
        )
        await interaction.response.send_modal(
            EditTicketMessageModal(interaction.message.id, embed)
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
            and not is_support_member(interaction.user)
        ):
            await private_reply(
                interaction, "Nur Ticket-Ersteller oder Support dürfen das."
            )
            return

        await update_topic(channel, owner_vote=0, support_vote=0)

        embed = discord.Embed(
            title="🔒 Ticket-Schließung",
            description=(
                "Zum endgültigen Schließen werden zwei Bestätigungen benötigt:\n"
                "• Ticket-Ersteller\n"
                "• Support-Team"
            ),
            color=discord.Color.orange(),
        )

        await interaction.response.send_message(
            embed=embed, view=CloseVoteView()
        )


# =====================================================
# TICKET ERSTELLEN
# =====================================================

class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Ticket erstellen",
        emoji="🎫",
        style=discord.ButtonStyle.primary,
        custom_id="kingsnowy:ticket-create",
    )
    async def create_ticket(self, interaction, button):
        guild = interaction.guild

        if guild is None:
            await private_reply(interaction, "Nur auf einem Server möglich.")
            return

        # Pro Nutzer nur ein offenes Ticket
        for channel in guild.text_channels:
            data = parse_topic(channel.topic)
            if data and data.get("owner") == interaction.user.id:
                await private_reply(
                    interaction, f"Du hast bereits ein Ticket: {channel.mention}"
                )
                return

        bot_member = guild.me
        if bot_member is None:
            await private_reply(interaction, "Bot-Mitglied konnte nicht gefunden werden.")
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

        safe_name = re.sub(
            r"[^a-z0-9_-]", "",
            interaction.user.name.lower().replace(" ", "-")
        )[:25] or "user"

        try:
            channel = await guild.create_text_channel(
                name=f"ticket-{safe_name}",
                overwrites=overwrites,
                topic=make_topic(interaction.user.id),
                reason=f"Ticket created by {interaction.user}",
            )
        except (discord.Forbidden, discord.HTTPException) as error:
            await private_reply(
                interaction,
                f"Ticket konnte nicht erstellt werden: {error}",
            )
            return

        tickets[str(channel.id)] = {
            "owner_id": interaction.user.id,
            "guild_id": guild.id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        save_json("tickets.json", tickets)

        # Nachricht aus /ticketset verwenden
        title = config.get("ticket_message_title", "🎫 KingSnowy Support")
        description = config.get(
            "ticket_message_description",
            "Beschreibe dein Problem möglichst genau."
        )

        embed = discord.Embed(
            title=title,
            description=(
                f"{interaction.user.mention}\n\n"
                f"{description}\n\n"
                "Verwende die Buttons unten, um das Ticket zu verwalten."
            ),
            color=discord.Color.blue(),
            timestamp=datetime.now(timezone.utc),
        )

        await channel.send(
            embed=embed,
            view=TicketControlsView(),
        )

        await send_ticket_log(
            f"🎫 Neues Ticket: {channel.mention} von {interaction.user.mention}"
        )

        await interaction.response.send_message(
            f"✅ Dein Ticket wurde erstellt: {channel.mention}",
            ephemeral=True,
        )


# =====================================================
# BEFEHLE
# =====================================================

# Prefix-Befehl: !ticketcreat
@bot.command(name="ticketcreat")
@commands.has_permissions(manage_guild=True)
async def ticketcreat(ctx):
    embed = discord.Embed(
        title="🎫 KingSnowy Support",
        description=(
            "Benötigst du Hilfe? Klicke auf den Button, um ein privates "
            "Support-Ticket zu erstellen."
        ),
        color=discord.Color.blue(),
    )
    await ctx.send(embed=embed, view=TicketPanelView())


# Slash-Befehl: /ticketset
@bot.tree.command(
    name="ticketset",
    description="Set the message shown inside newly created tickets",
)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(
    titel="Titel der Ticket-Nachricht",
    beschreibung="Text der Ticket-Nachricht",
)
async def ticketset(
    interaction: discord.Interaction,
    titel: str,
    beschreibung: str,
):
    if len(titel) > 256:
        await private_reply(interaction, "Der Titel darf höchstens 256 Zeichen haben.")
        return

    if len(beschreibung) > 4000:
        await private_reply(
            interaction, "Die Beschreibung darf höchstens 4000 Zeichen haben."
        )
        return

    config["ticket_message_title"] = titel
    config["ticket_message_description"] = beschreibung
    save_json("config.json", config)

    preview = discord.Embed(
        title=titel,
        description=beschreibung,
        color=discord.Color.blue(),
    )

    await interaction.response.send_message(
        "✅ Die Nachricht für **neu erstellte Tickets** wurde gespeichert. "
        "Bereits vorhandene Tickets werden nicht automatisch geändert.",
        embed=preview,
        ephemeral=True,
    )


@bot.tree.command(
    name="ticketlog-set",
    description="Set the ticket log channel",
)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(channel="Kanal für Ticket-Logs")
async def ticketlog_set(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
):
    config["ticket_log_channel_id"] = channel.id
    save_json("config.json", config)
    await interaction.response.send_message(
        f"✅ Ticket-Log eingestellt: {channel.mention}", ephemeral=True
    )


@bot.tree.command(
    name="ticket-support-set",
    description="Set the support team role",
)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(role="Die Support-Rolle")
async def ticket_support_set(
    interaction: discord.Interaction,
    role: discord.Role,
):
    config["support_role_id"] = role.id
    save_json("config.json", config)
    await interaction.response.send_message(
        f"✅ Support-Rolle eingestellt: {role.mention}", ephemeral=True
    )


# =====================================================
# FEHLER
# =====================================================

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ Du benötigst die Berechtigung „Server verwalten“.")
    elif isinstance(error, commands.CommandNotFound):
        return
    else:
        print(f"Prefix-Command-Fehler: {error}")
        await ctx.send("❌ Beim Ausführen des Befehls ist ein Fehler aufgetreten.")


@bot.tree.error
async def slash_error(interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        message = "❌ Du benötigst die Berechtigung „Server verwalten“."
    else:
        print(f"Slash-Command-Fehler: {error}")
        message = "❌ Beim Ausführen des Befehls ist ein Fehler aufgetreten."

    await private_reply(interaction, message)


# =====================================================
# START
# =====================================================

@bot.event
async def on_ready():
    global views_registered

    if not views_registered:
        bot.add_view(SignInView())
        bot.add_view(TicketPanelView())
        bot.add_view(TicketControlsView())
        bot.add_view(CloseVoteView())
        views_registered = True

    try:
        synced = await bot.tree.sync()
        print(f"{len(synced)} Slash-Befehle synchronisiert.")
    except Exception as error:
        print(f"Slash-Befehle konnten nicht synchronisiert werden: {error}")

    print(f"KingSnowy Bot online: {bot.user}")


bot.run(TOKEN)
