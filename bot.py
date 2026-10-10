
import os
import json
import re
import asyncio
from pathlib import Path

import discord
from discord.ext import commands
from discord import app_commands


# ==================================================
# KONFIGURATION
# ==================================================

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()

SIGNINS_FILE = Path("signins.json")
LANGUAGES_FILE = Path("languages.json")
CONFIG_FILE = Path("config.json")
TICKETS_FILE = Path("tickets.json")

if not TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN fehlt. Bitte in Railway unter Variables setzen."
    )


# ==================================================
# ÜBERSETZUNGEN FÜR DAS ANMELDEPANEL
# ==================================================

TEXT = {
    "en": {
        "language_saved": "Your language has been set to English.",
        "modal_title": "The Isle - Player Sign-In",
        "dino": "Dinosaur",
        "dino_placeholder": "e.g. Stego",
        "skin": "Skin",
        "skin_placeholder": "Your skin",
        "location": "Location",
        "location_placeholder": "e.g. Swamp",
        "signed_in": "You have successfully signed in!",
        "signed_out": "You have successfully signed out!",
        "not_signed_in": "You are not currently signed in.",
        "no_channel": "The announcement channel has not been configured. Please contact an administrator.",
        "admin_only": "You need administrator permissions.",
        "channel_set": "The announcement channel is now {channel}.",
    },
    "de": {
        "language_saved": "Deine Sprache wurde auf Deutsch eingestellt.",
        "modal_title": "The Isle - Spieleranmeldung",
        "dino": "Dinosaurier",
        "dino_placeholder": "z. B. Stego",
        "skin": "Skin",
        "skin_placeholder": "Dein Skin",
        "location": "Standort",
        "location_placeholder": "z. B. Sumpfteich",
        "signed_in": "Du wurdest erfolgreich angemeldet!",
        "signed_out": "Du wurdest erfolgreich abgemeldet!",
        "not_signed_in": "Du bist aktuell nicht angemeldet.",
        "no_channel": "Der Anmeldekanal wurde noch nicht eingerichtet. Bitte informiere einen Administrator.",
        "admin_only": "Du benötigst Administratorrechte.",
        "channel_set": "Der Anmeldekanal wurde auf {channel} eingestellt.",
    },
    "cs": {
        "language_saved": "Jazyk byl nastaven na češtinu.",
        "modal_title": "The Isle - Registrace hráče",
        "dino": "Dinosaurus",
        "dino_placeholder": "např. Stego",
        "skin": "Vzhled",
        "skin_placeholder": "Tvůj vzhled",
        "location": "Poloha",
        "location_placeholder": "např. bažina",
        "signed_in": "Úspěšně ses přihlásil!",
        "signed_out": "Úspěšně ses odhlásil!",
        "not_signed_in": "Momentálně nejsi přihlášen.",
        "no_channel": "Registrační kanál není nastaven. Kontaktuj správce.",
        "admin_only": "Potřebuješ oprávnění správce.",
        "channel_set": "Registrační kanál byl nastaven na {channel}.",
    },
}


# ==================================================
# JSON-HILFSFUNKTIONEN
# ==================================================

def load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_json(path: Path, data: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary.replace(path)


def get_language(user_id: int) -> str:
    languages = load_json(LANGUAGES_FILE)
    language = languages.get(str(user_id), "en")
    return language if language in TEXT else "en"


def set_language(user_id: int, language: str) -> None:
    if language not in TEXT:
        language = "en"

    languages = load_json(LANGUAGES_FILE)
    languages[str(user_id)] = language
    save_json(LANGUAGES_FILE, languages)


def player_key(guild_id: int, user_id: int) -> str:
    return f"{guild_id}:{user_id}"


# ==================================================
# ANMELDEKANAL PRO SERVER
# ==================================================

def get_announcement_channel_id(guild_id: int):
    config = load_json(CONFIG_FILE)
    channels = config.get("announcement_channels", {})
    channel_id = channels.get(str(guild_id))

    if channel_id is None:
        channel_id = config.get("announcement_channel_id")

    return int(channel_id) if channel_id else None


def set_announcement_channel_id(
    guild_id: int,
    channel_id: int,
) -> None:
    config = load_json(CONFIG_FILE)
    channels = config.get("announcement_channels", {})
    channels[str(guild_id)] = channel_id
    config["announcement_channels"] = channels
    save_json(CONFIG_FILE, config)


# ==================================================
# BOT EINRICHTEN
# ==================================================

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
)


async def get_announcement_channel(guild: discord.Guild):
    channel_id = get_announcement_channel_id(guild.id)

    if channel_id is None:
        return None

    channel = guild.get_channel(channel_id)

    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except (
            discord.NotFound,
            discord.Forbidden,
            discord.HTTPException,
        ):
            return None

    if not isinstance(channel, discord.TextChannel):
        return None

    if channel.guild.id != guild.id:
        return None

    return channel


# ==================================================
# ANMELDEFORMULAR
# ==================================================

class SignInModal(discord.ui.Modal):
    def __init__(self, language: str):
        self.language = language if language in TEXT else "en"
        t = TEXT[self.language]

        super().__init__(title=t["modal_title"])

        self.dinosaur = discord.ui.TextInput(
            label=t["dino"],
            placeholder=t["dino_placeholder"],
            max_length=60,
            required=True,
        )
        self.skin = discord.ui.TextInput(
            label=t["skin"],
            placeholder=t["skin_placeholder"],
            max_length=80,
            required=True,
        )
        self.location = discord.ui.TextInput(
            label=t["location"],
            placeholder=t["location_placeholder"],
            max_length=100,
            required=True,
        )

        self.add_item(self.dinosaur)
        self.add_item(self.skin)
        self.add_item(self.location)

    async def on_submit(self, interaction: discord.Interaction):
        personal_text = TEXT[self.language]

        if interaction.guild is None:
            await interaction.response.send_message(
                "This form can only be used in a server.",
                ephemeral=True,
            )
            return

        channel = await get_announcement_channel(interaction.guild)

        if channel is None:
            await interaction.response.send_message(
                personal_text["no_channel"],
                ephemeral=True,
            )
            return

        key = player_key(interaction.guild.id, interaction.user.id)
        data = load_json(SIGNINS_FILE)
        existing = data.get(key, {})

        if existing.get("status") == "Signed In":
            await interaction.response.send_message(
                "You are already signed in. Please sign out first.",
                ephemeral=True,
            )
            return

        # Öffentliche Anmeldung bleibt immer auf Englisch.
        embed = discord.Embed(
            title="🟢 Player Signed In",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(
            name="Player",
            value=interaction.user.mention,
            inline=False,
        )
        embed.add_field(
            name="Dinosaur",
            value=self.dinosaur.value.strip(),
            inline=True,
        )
        embed.add_field(
            name="Skin",
            value=self.skin.value.strip(),
            inline=True,
        )
        embed.add_field(
            name="Location",
            value=self.location.value.strip(),
            inline=False,
        )
        embed.set_footer(text="The Isle - Player Registration")

        try:
            await channel.send(
                embed=embed,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException as error:
            print(f"Could not publish sign-in: {error}")
            await interaction.response.send_message(
                "Your registration could not be published. "
                "Please contact an administrator.",
                ephemeral=True,
            )
            return

        data[key] = {
            "discord_name": interaction.user.display_name,
            "discord_id": interaction.user.id,
            "dino": self.dinosaur.value.strip(),
            "skin": self.skin.value.strip(),
            "location": self.location.value.strip(),
            "status": "Signed In",
            "language": self.language,
            "guild_id": interaction.guild.id,
            "updated_at": discord.utils.utcnow().isoformat(),
        }
        save_json(SIGNINS_FILE, data)

        await interaction.response.send_message(
            personal_text["signed_in"],
            ephemeral=True,
        )


# ==================================================
# ANMELDEPANEL
# ==================================================

class SignInView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.select(
        placeholder="Choose your language",
        custom_id="isle_language",
        options=[
            discord.SelectOption(
                label="English", value="en", emoji="🇬🇧", default=True
            ),
            discord.SelectOption(
                label="Deutsch", value="de", emoji="🇩🇪"
            ),
            discord.SelectOption(
                label="Čeština", value="cs", emoji="🇨🇿"
            ),
        ],
    )
    async def choose_language(
        self,
        interaction: discord.Interaction,
        select: discord.ui.Select,
    ):
        language = select.values[0]
        set_language(interaction.user.id, language)

        await interaction.response.send_message(
            TEXT[language]["language_saved"],
            ephemeral=True,
        )

    @discord.ui.button(
        label="Sign In",
        style=discord.ButtonStyle.success,
        custom_id="isle_signin",
    )
    async def signin(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await interaction.response.send_modal(
            SignInModal(get_language(interaction.user.id))
        )

    @discord.ui.button(
        label="Sign Out",
        style=discord.ButtonStyle.danger,
        custom_id="isle_signout",
    )
    async def signout(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if interaction.guild is None:
            await interaction.response.send_message(
                "This button can only be used in a server.",
                ephemeral=True,
            )
            return

        language = get_language(interaction.user.id)
        personal_text = TEXT[language]
        key = player_key(interaction.guild.id, interaction.user.id)

        data = load_json(SIGNINS_FILE)
        player = data.get(key)

        if not player or player.get("status") != "Signed In":
            await interaction.response.send_message(
                personal_text["not_signed_in"],
                ephemeral=True,
            )
            return

        channel = await get_announcement_channel(interaction.guild)

        if channel is None:
            await interaction.response.send_message(
                personal_text["no_channel"],
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="🔴 Player Signed Out",
            color=discord.Color.red(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(
            name="Player",
            value=interaction.user.mention,
            inline=False,
        )
        embed.add_field(
            name="Last Location",
            value=player.get("location", "Unknown"),
            inline=False,
        )
        embed.set_footer(text="The Isle - Player Registration")

        try:
            await channel.send(
                embed=embed,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException as error:
            print(f"Could not publish sign-out: {error}")
            await interaction.response.send_message(
                "Your sign-out could not be published. "
                "Please contact an administrator.",
                ephemeral=True,
            )
            return

        player["status"] = "Signed Out"
        player["updated_at"] = discord.utils.utcnow().isoformat()
        save_json(SIGNINS_FILE, data)

        await interaction.response.send_message(
            personal_text["signed_out"],
            ephemeral=True,
        )


# ==================================================
# !anmeldepannel
# ==================================================

@bot.command(name="anmeldepannel", aliases=["anmeldepanel"])
@commands.has_permissions(administrator=True)
async def anmeldepannel(ctx: commands.Context):
    embed = discord.Embed(
        title="🦖 THE ISLE - PLAYER SIGN-IN",
        color=discord.Color.blurple(),
        description=(
            "Welcome to the player sign-in panel!\n"
            "Willkommen beim Spieler-Anmeldepanel!\n"
            "Vítej v registračním panelu hráčů!\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "🇬🇧 **ENGLISH - HOW TO SIGN IN**\n"
            "1. Select your language below.\n"
            "2. Click **Sign In**.\n"
            "3. Enter your dinosaur, skin and current location.\n"
            "4. Submit the form to publish your registration.\n"
            "5. When you finish playing, click **Sign Out**.\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "🇩🇪 **DEUTSCH - SO MELDEST DU DICH AN**\n"
            "1. Wähle unten deine Sprache aus.\n"
            "2. Klicke auf **Sign In**.\n"
            "3. Gib Dinosaurier, Skin und Standort ein.\n"
            "4. Sende das Formular ab.\n"
            "5. Wenn du fertig bist, klicke auf **Sign Out**.\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "🇨🇿 **ČEŠTINA - JAK SE PŘIHLÁSIT**\n"
            "1. Vyber si jazyk.\n"
            "2. Klikni na **Sign In**.\n"
            "3. Vyplň dinosaura, vzhled a polohu.\n"
            "4. Odešli formulář.\n"
            "5. Po skončení hraní klikni na **Sign Out**.\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "🌐 Choose your language / Wähle deine Sprache / Vyber jazyk"
        ),
    )
    embed.set_footer(text="created by KingSnowy")

    await ctx.send(embed=embed, view=SignInView())


@anmeldepannel.error
async def anmeldepannel_error(
    ctx: commands.Context,
    error: commands.CommandError,
):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send(
            "You need administrator permissions. / "
            "Du benötigst Administratorrechte. / "
            "Potřebuješ oprávnění správce."
        )
    else:
        print(f"Error in !anmeldepannel: {error}")


# ==================================================
# /kanal-set
# ==================================================

@bot.tree.command(
    name="kanal-set",
    description="Set the public player announcement channel.",
)
@app_commands.describe(
    kanal="Channel for public sign-in and sign-out announcements",
)
@app_commands.checks.has_permissions(administrator=True)
async def kanal_set(
    interaction: discord.Interaction,
    kanal: discord.TextChannel,
):
    if interaction.guild is None:
        await interaction.response.send_message(
            "This command can only be used in a server.",
            ephemeral=True,
        )
        return

    set_announcement_channel_id(interaction.guild.id, kanal.id)
    language = get_language(interaction.user.id)

    await interaction.response.send_message(
        TEXT[language]["channel_set"].format(channel=kanal.mention),
        ephemeral=True,
    )


@kanal_set.error
async def kanal_set_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError,
):
    language = get_language(interaction.user.id)

    if isinstance(error, app_commands.MissingPermissions):
        message = TEXT[language]["admin_only"]
    else:
        print(f"Error in /kanal-set: {error}")
        message = "An error occurred. Please check the bot logs."

    if not interaction.response.is_done():
        await interaction.response.send_message(message, ephemeral=True)


# ==================================================
# TICKET-DATEN UND HILFSFUNKTIONEN
# ==================================================

def load_ticket_data():
    data = load_json(TICKETS_FILE)
    data.setdefault("panels", {})
    data.setdefault("counters", {})
    return data


def clean_ticket_name(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9-]", "-", value)
    value = re.sub(r"-+", "-", value)
    return value.strip("-")[:35] or "ticket"


def get_ticket_info(channel: discord.TextChannel):
    topic = channel.topic or ""

    owner = re.search(r"ticket-owner:(\d+)", topic)
    role = re.search(r"ticket-role:(\d+)", topic)
    number = re.search(r"ticket-number:(\d+)", topic)
    kind = re.search(r"ticket-type:([a-z0-9-]+)", topic)

    if not owner:
        return None

    return {
        "owner_id": int(owner.group(1)),
        "role_id": int(role.group(1)) if role else 0,
        "number": int(number.group(1)) if number else 0,
        "type": kind.group(1) if kind else "ticket",
    }


def next_ticket_number(guild_id: int, ticket_type: str) -> int:
    data = load_ticket_data()
    key = f"{guild_id}:{ticket_type}"

    number = int(data["counters"].get(key, 0)) + 1
    data["counters"][key] = number
    save_json(TICKETS_FILE, data)

    return number


# ==================================================
# TICKET SCHLIESSEN
# ==================================================

class TicketCloseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Ticket schließen",
        emoji="🔒",
        style=discord.ButtonStyle.danger,
        custom_id="kingsnowy:ticket:close",
    )
    async def close_ticket(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if (
            interaction.guild is None
            or not isinstance(interaction.channel, discord.TextChannel)
        ):
            await interaction.response.send_message(
                "Dieser Button funktioniert nur in einem Ticket.",
                ephemeral=True,
            )
            return

        channel = interaction.channel
        info = get_ticket_info(channel)

        if info is None:
            await interaction.response.send_message(
                "Dieser Channel ist kein erkanntes Ticket.",
                ephemeral=True,
            )
            return

        member = interaction.user
        role = interaction.guild.get_role(info["role_id"])

        is_owner = member.id == info["owner_id"]
        is_admin = member.guild_permissions.manage_channels
        is_support = (
            isinstance(member, discord.Member)
            and role is not None
            and role in member.roles
        )

        if not (is_owner or is_admin or is_support):
            await interaction.response.send_message(
                "Nur der Ticket-Ersteller, das Support-Team oder "
                "Administratoren dürfen dieses Ticket schließen.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            "🔒 Dieses Ticket wird in 5 Sekunden gelöscht."
        )

        await asyncio.sleep(5)

        try:
            await channel.delete(reason=f"Ticket geschlossen von {member}")
        except discord.HTTPException as error:
            print(f"Ticket konnte nicht gelöscht werden: {error}")


# ==================================================
# TICKET-PANEL
# ==================================================

class TicketPanelView(discord.ui.View):
    def __init__(self, ticket_name: str):
        super().__init__(timeout=None)
        self.create_ticket.label = ticket_name[:80]

    @discord.ui.button(
        label="Ticket erstellen",
        emoji="🎫",
        style=discord.ButtonStyle.success,
        custom_id="kingsnowy:ticket:create",
    )
    async def create_ticket(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        guild = interaction.guild

        if guild is None:
            await interaction.response.send_message(
                "Dieser Button funktioniert nur auf einem Server.",
                ephemeral=True,
            )
            return

        if interaction.message is None:
            await interaction.response.send_message(
                "Das Panel wurde nicht gefunden.",
                ephemeral=True,
            )
            return

        data = load_ticket_data()
        panel = data["panels"].get(str(interaction.message.id))

        if not panel or int(panel["guild_id"]) != guild.id:
            await interaction.response.send_message(
                "Die Panel-Einstellungen fehlen. Bitte erstelle ein neues Panel.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        ticket_name = panel["ticket_name"]
        ticket_type = clean_ticket_name(ticket_name)
        role_id = int(panel["role_id"])
        category_id = int(panel.get("category_id", 0))
        support_role = guild.get_role(role_id)

        if support_role is None:
            await interaction.followup.send(
                "Die konfigurierte Ping-Rolle existiert nicht mehr. "
                "Bitte erstelle ein neues Ticket-Panel.",
                ephemeral=True,
            )
            return

        # Pro Nutzer höchstens ein offenes Ticket je Ticket-Typ.
        for channel in guild.text_channels:
            info = get_ticket_info(channel)
            if (
                info
                and info["owner_id"] == interaction.user.id
                and info["type"] == ticket_type
            ):
                await interaction.followup.send(
                    f"Du hast bereits ein offenes Ticket: {channel.mention}",
                    ephemeral=True,
                )
                return

        number = next_ticket_number(guild.id, ticket_type)
        channel_name = f"ticket-{ticket_type}-{number:03d}"

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
            ),
        }

        bot_member = guild.me
        if bot_member:
            overwrites[bot_member] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_channels=True,
            )

        category = guild.get_channel(category_id) if category_id else None
        if not isinstance(category, discord.CategoryChannel):
            category = None

        try:
            ticket_channel = await guild.create_text_channel(
                name=channel_name,
                category=category,
                overwrites=overwrites,
                topic=(
                    f"ticket-owner:{interaction.user.id} "
                    f"ticket-role:{support_role.id} "
                    f"ticket-number:{number} "
                    f"ticket-type:{ticket_type}"
                ),
                reason=f"Ticket von {interaction.user}",
            )

            embed = discord.Embed(
                title=f"🎫 {ticket_name} | Ticket #{number:03d}",
                description=(
                    f"Hallo {interaction.user.mention}!\n\n"
                    f"Willkommen in deinem Ticket für **{ticket_name}**.\n"
                    "Beschreibe hier dein Anliegen. Das Support-Team wird dir helfen.\n\n"
                    "Mit dem Button unten kannst du das Ticket schließen."
                ),
                color=discord.Color.blue(),
            )

            await ticket_channel.send(
                content=f"{interaction.user.mention} {support_role.mention}",
                embed=embed,
                view=TicketCloseView(),
                allowed_mentions=discord.AllowedMentions(
                    users=True,
                    roles=True,
                    everyone=False,
                ),
            )

            await interaction.followup.send(
                f"✅ Dein Ticket wurde erstellt: {ticket_channel.mention}",
                ephemeral=True,
            )

        except discord.Forbidden:
            await interaction.followup.send(
                "Mir fehlen Berechtigungen zum Erstellen des Ticket-Channels. "
                "Prüfe insbesondere **Kanäle verwalten**.",
                ephemeral=True,
            )
        except discord.HTTPException as error:
            print(f"Ticket-Erstellungsfehler: {error}")
            await interaction.followup.send(
                "Das Ticket konnte nicht erstellt werden. Bitte versuche es erneut.",
                ephemeral=True,
            )


# ==================================================
# /ticketcreat
# ==================================================

@bot.tree.command(
    name="ticketcreat",
    description="Erstellt ein Ticket-Panel mit auswählbarer Ping-Rolle.",
)
@app_commands.describe(
    ping_rolle="Die Rolle, die bei jedem Ticket erwähnt wird.",
    ticket_name="Name und Button-Text, z. B. Support oder Bewerbung.",
    kategorie="Optionale Discord-Kategorie für die Ticket-Channels.",
)
@app_commands.checks.has_permissions(manage_guild=True)
async def ticketcreat(
    interaction: discord.Interaction,
    ping_rolle: discord.Role,
    ticket_name: str,
    kategorie: discord.CategoryChannel = None,
):
    if interaction.guild is None:
        await interaction.response.send_message(
            "Dieser Befehl funktioniert nur auf einem Server.",
            ephemeral=True,
        )
        return

    ticket_name = ticket_name.strip()

    if not ticket_name or len(ticket_name) > 80:
        await interaction.response.send_message(
            "Der Ticket-Name muss zwischen 1 und 80 Zeichen lang sein.",
            ephemeral=True,
        )
        return

    if ping_rolle.is_default():
        await interaction.response.send_message(
            "Bitte wähle eine echte Server-Rolle aus.",
            ephemeral=True,
        )
        return

    bot_member = interaction.guild.me
    if bot_member and ping_rolle >= bot_member.top_role:
        await interaction.response.send_message(
            "Die ausgewählte Rolle muss in der Rollenliste unter "
            "der höchsten Bot-Rolle stehen.",
            ephemeral=True,
        )
        return

    embed = discord.Embed(
        title=f"🎫 {ticket_name}",
        description=(
            f"Du benötigst Hilfe bei **{ticket_name}**?\n\n"
            "Klicke auf den Button unten, um ein privates Ticket zu erstellen.\n"
            "Bitte eröffne nur ein Ticket, wenn du Unterstützung benötigst."
        ),
        color=discord.Color.blue(),
    )
    embed.set_footer(text="KingSnowy Support")

    # Zunächst ohne Panel-ID senden, dann Einstellungen speichern.
    await interaction.response.send_message(
        embed=embed,
        view=TicketPanelView(ticket_name),
    )

    panel_message = await interaction.original_response()

    data = load_ticket_data()
    data["panels"][str(panel_message.id)] = {
        "guild_id": interaction.guild.id,
        "role_id": ping_rolle.id,
        "ticket_name": ticket_name,
        "category_id": kategorie.id if kategorie else 0,
    }
    save_json(TICKETS_FILE, data)

    # Registriert genau dieses Panel auch nach einem Neustart.
    bot.add_view(
        TicketPanelView(ticket_name),
        message_id=panel_message.id,
    )


@ticketcreat.error
async def ticketcreat_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError,
):
    if isinstance(error, app_commands.MissingPermissions):
        message = "Du benötigst die Berechtigung **Server verwalten**."
    else:
        print(f"Fehler bei /ticketcreat: {error}")
        message = "Beim Erstellen des Ticket-Panels ist ein Fehler aufgetreten."

    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


# ==================================================
# BOT STARTEN UND PERSISTENTE BUTTONS WIEDERHERSTELLEN
# ==================================================

@bot.event
async def on_ready():
    if not getattr(bot, "persistent_views_registered", False):
        bot.add_view(SignInView())
        bot.add_view(TicketCloseView())

        data = load_ticket_data()

        for message_id, panel in data["panels"].items():
            try:
                bot.add_view(
                    TicketPanelView(panel["ticket_name"]),
                    message_id=int(message_id),
                )
            except (KeyError, ValueError, TypeError) as error:
                print(f"Ticket-Panel {message_id} konnte nicht geladen werden: {error}")

        bot.persistent_views_registered = True

    if not getattr(bot, "slash_commands_synced", False):
        try:
            synced = await bot.tree.sync()
            bot.slash_commands_synced = True
            print(f"Slash-Commands synchronisiert: {len(synced)}")
        except discord.HTTPException as error:
            print(f"Slash-Command-Synchronisierung fehlgeschlagen: {error}")

    print(f"Bot online: {bot.user} (ID: {bot.user.id})")


bot.run(TOKEN)
