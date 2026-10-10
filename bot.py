import os
import json
from pathlib import Path

import discord
from discord.ext import commands
from discord import app_commands


# ==================================================
# KONFIGURATION
# ==================================================

TOKEN = os.getenv("DISCORD_TOKEN")

SIGNINS_FILE = Path("signins.json")
LANGUAGES_FILE = Path("languages.json")
CONFIG_FILE = Path("config.json")

if not TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN fehlt. Bitte in Railway unter Variables setzen."
    )


# ==================================================
# ÜBERSETZUNGEN
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
# JSON-DATEIEN
# ==================================================

def load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_json(path: Path, data: dict) -> None:
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


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


# ==================================================
# ANMELDEKANAL PRO SERVER SPEICHERN
# ==================================================

def get_announcement_channel_id(guild_id: int):
    config = load_json(CONFIG_FILE)
    channels = config.get("announcement_channels", {})
    channel_id = channels.get(str(guild_id))

    # Unterstützt auch eine frühere config.json.
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


def player_key(guild_id: int, user_id: int) -> str:
    return f"{guild_id}:{user_id}"


# ==================================================
# BOT EINRICHTEN
# ==================================================

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
)


async def get_announcement_channel(
    guild: discord.Guild,
):
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

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ) -> None:
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

        guild_id = interaction.guild.id
        user_id = interaction.user.id
        key = player_key(guild_id, user_id)

        data = load_json(SIGNINS_FILE)
        existing = data.get(key, {})

        # Keine doppelte aktive Anmeldung.
        if existing.get("status") == "Signed In":
            await interaction.response.send_message(
                "You are already signed in. Please sign out first.",
                ephemeral=True,
            )
            return

        # Öffentliche Anmeldung: immer Englisch.
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
        embed.set_footer(
            text="The Isle - Player Registration"
        )

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
            "discord_id": user_id,
            "dino": self.dinosaur.value.strip(),
            "skin": self.skin.value.strip(),
            "location": self.location.value.strip(),
            "status": "Signed In",
            "language": self.language,
            "guild_id": guild_id,
            "updated_at": discord.utils.utcnow().isoformat(),
        }

        save_json(SIGNINS_FILE, data)

        await interaction.response.send_message(
            personal_text["signed_in"],
            ephemeral=True,
        )


# ==================================================
# PANEL: SPRACHAUSWAHL UND BUTTONS
# ==================================================

class SignInView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.select(
        placeholder="Choose your language",
        custom_id="isle_language",
        options=[
            discord.SelectOption(
                label="English",
                value="en",
                emoji="🇬🇧",
                default=True,
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
    )
    async def choose_language(
        self,
        interaction: discord.Interaction,
        select: discord.ui.Select,
    ) -> None:
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
    ) -> None:
        language = get_language(interaction.user.id)

        await interaction.response.send_modal(
            SignInModal(language)
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
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "This button can only be used in a server.",
                ephemeral=True,
            )
            return

        language = get_language(interaction.user.id)
        personal_text = TEXT[language]

        key = player_key(
            interaction.guild.id,
            interaction.user.id,
        )

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

        # Öffentliche Abmeldung: immer Englisch.
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
        embed.set_footer(
            text="The Isle - Player Registration"
        )

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
# Auch !anmeldepanel funktioniert als Alias.
# ==================================================

@bot.command(
    name="anmeldepannel",
    aliases=["anmeldepanel"],
)
@commands.has_permissions(administrator=True)
async def anmeldepannel(ctx: commands.Context) -> None:
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
            "4. Submit the form. Your registration will be posted "
            "in the designated announcement channel.\n"
            "5. When you finish playing, click **Sign Out**.\n\n"

            "━━━━━━━━━━━━━━━━━━━━\n\n"

            "🇩🇪 **DEUTSCH - SO MELDEST DU DICH AN**\n"
            "1. Wähle unten deine Sprache aus.\n"
            "2. Klicke auf **Sign In**.\n"
            "3. Gib deinen Dinosaurier, Skin und Standort ein.\n"
            "4. Sende das Formular ab. Deine Anmeldung wird im "
            "festgelegten Anmeldekanal veröffentlicht.\n"
            "5. Wenn du fertig bist, klicke auf **Sign Out**.\n\n"

            "━━━━━━━━━━━━━━━━━━━━\n\n"

            "🇨🇿 **ČEŠTINA - JAK SE PŘIHLÁSIT**\n"
            "1. Vyber si jazyk v nabídce níže.\n"
            "2. Klikni na **Sign In**.\n"
            "3. Vyplň dinosaura, vzhled a aktuální polohu.\n"
            "4. Odešli formulář. Registrace bude zveřejněna "
            "v určeném registračním kanálu.\n"
            "5. Po skončení hraní klikni na **Sign Out**.\n\n"

            "━━━━━━━━━━━━━━━━━━━━\n"
            "🌐 Choose your language / Wähle deine Sprache / Vyber jazyk"
        ),
    )

    embed.set_footer(
        text="createt by KingSnowy"
    )

    await ctx.send(
        embed=embed,
        view=SignInView(),
    )


@anmeldepannel.error
async def anmeldepannel_error(
    ctx: commands.Context,
    error: commands.CommandError,
) -> None:
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
) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            "This command can only be used in a server.",
            ephemeral=True,
        )
        return

    set_announcement_channel_id(
        interaction.guild.id,
        kanal.id,
    )

    language = get_language(interaction.user.id)

    await interaction.response.send_message(
        TEXT[language]["channel_set"].format(
            channel=kanal.mention,
        ),
        ephemeral=True,
    )


@kanal_set.error
async def kanal_set_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError,
) -> None:
    language = get_language(interaction.user.id)

    if isinstance(error, app_commands.MissingPermissions):
        message = TEXT[language]["admin_only"]
    else:
        print(f"Error in /kanal-set: {error}")
        message = "An error occurred. Please check the bot logs."

    if not interaction.response.is_done():
        await interaction.response.send_message(
            message,
            ephemeral=True,
        )


# ==================================================
# BOT STARTEN
# ==================================================

@bot.event
async def on_ready():
    if not getattr(bot, "persistent_view_registered", False):
        bot.add_view(SignInView())
        bot.persistent_view_registered = True

    if not getattr(bot, "slash_commands_synced", False):
        try:
            synced = await bot.tree.sync()
            bot.slash_commands_synced = True
            print(f"Slash commands synced: {len(synced)}")
        except discord.HTTPException as error:
            print(f"Could not sync slash commands: {error}")

    print(f"Bot online: {bot.user} (ID: {bot.user.id})")


# ===== TICKET SYSTEM =====

import os
import re
import discord
from discord import app_commands


# Optional: IDs in Railway als Variables hinterlegen.
# Ohne diese Variablen sind Tickets nur für den Ersteller
# und Administratoren sichtbar.
SUPPORT_ROLE_ID = int(os.getenv("SUPPORT_ROLE_ID", "0"))
TICKET_CATEGORY_ID = int(os.getenv("TICKET_CATEGORY_ID", "0"))


class TicketCloseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Close Ticket",
        emoji="🔒",
        style=discord.ButtonStyle.danger,
        custom_id="kingsnowy_ticket_close"
    )
    async def close_ticket(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        if not interaction.guild or not isinstance(
            interaction.channel, discord.TextChannel
        ):
            await interaction.response.send_message(
                "This button can only be used inside a ticket.",
                ephemeral=True
            )
            return

        channel = interaction.channel
        topic = channel.topic or ""

        match = re.search(r"ticket-owner:(\d+)", topic)
        owner_id = int(match.group(1)) if match else None

        support_role = (
            interaction.guild.get_role(SUPPORT_ROLE_ID)
            if SUPPORT_ROLE_ID else None
        )

        is_owner = interaction.user.id == owner_id
        is_admin = interaction.user.guild_permissions.manage_channels
        is_support = (
            support_role is not None
            and support_role in getattr(interaction.user, "roles", [])
        )

        if not (is_owner or is_admin or is_support):
            await interaction.response.send_message(
                "Only the ticket creator or support team can close this ticket.",
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            "🔒 This ticket will be deleted in 5 seconds."
        )

        import asyncio
        await asyncio.sleep(5)

        try:
            await channel.delete(reason="Ticket closed")
        except discord.HTTPException:
            pass


class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Create Ticket",
        emoji="🎫",
        style=discord.ButtonStyle.success,
        custom_id="kingsnowy_ticket_create"
    )
    async def create_ticket(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        guild = interaction.guild

        if guild is None:
            await interaction.response.send_message(
                "This can only be used in a server.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        # Verhindert mehrere gleichzeitig offene Tickets pro Person.
        existing = discord.utils.find(
            lambda ch: isinstance(ch, discord.TextChannel)
            and ch.topic
            and f"ticket-owner:{interaction.user.id}" in ch.topic,
            guild.text_channels
        )

        if existing:
            await interaction.edit_original_response(
                content=f"You already have an open ticket: {existing.mention}"
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
                attach_files=True
            )
        }

        bot_member = guild.get_member(interaction.client.user.id)

        if bot_member:
            overwrites[bot_member] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_channels=True
            )

        support_role = (
            guild.get_role(SUPPORT_ROLE_ID)
            if SUPPORT_ROLE_ID else None
        )

        if support_role:
            overwrites[support_role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True
            )

        category = (
            guild.get_channel(TICKET_CATEGORY_ID)
            if TICKET_CATEGORY_ID else None
        )

        if not isinstance(category, discord.CategoryChannel):
            category = None

        safe_name = re.sub(
            r"[^a-z0-9-]",
            "-",
            interaction.user.name.lower()
        ).strip("-")[:40] or "user"

        try:
            ticket_channel = await guild.create_text_channel(
                name=f"ticket-{safe_name}",
                category=category,
                overwrites=overwrites,
                topic=f"ticket-owner:{interaction.user.id}",
                reason=f"Support ticket opened by {interaction.user}"
            )

            embed = discord.Embed(
                title="🎫 Support Ticket",
                description=(
                    f"Hello {interaction.user.mention}!\n\n"
                    "Please describe your problem here. "
                    "A member of the support team will assist you.\n\n"
                    "Use the button below to close this ticket."
                ),
                color=discord.Color.blue()
            )

            await ticket_channel.send(
                content=(
                    f"{interaction.user.mention}"
                    + (f" {support_role.mention}" if support_role else "")
                ),
                embed=embed,
                view=TicketCloseView()
            )

            await interaction.edit_original_response(
                content=f"Your ticket has been created: {ticket_channel.mention}"
            )

        except discord.Forbidden:
            await interaction.edit_original_response(
                content=(
                    "I don't have permission to create ticket channels. "
                    "Please check the bot's Manage Channels permission."
                )
            )
        except discord.HTTPException:
            await interaction.edit_original_response(
                content="The ticket could not be created. Please try again."
            )


# Creates the slash-command group: /ticket creat
ticket_group = app_commands.Group(
    name="ticket",
    description="KingSnowy support ticket system"
)


@ticket_group.command(
    name="creat",
    description="Create a support ticket panel in this channel"
)
@app_commands.checks.has_permissions(manage_guild=True)
async def ticket_creat(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🎫 KingSnowy Support",
        description=(
            "Need help? Click **Create Ticket** below to open a private "
            "support channel.\n\n"
            "Please create a ticket only when you need assistance."
        ),
        color=discord.Color.blue()
    )

    await interaction.response.send_message(
        embed=embed,
        view=TicketPanelView()
    )


@ticket_creat.error
async def ticket_creat_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError
):
    if isinstance(error, app_commands.MissingPermissions):
        message = "You need the Manage Server permission to create the panel."
    else:
        message = "An error occurred while creating the ticket panel."

    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


# Register the slash-command group before bot.run(TOKEN).
bot.tree.add_command(ticket_group)

bot.run(TOKEN)
