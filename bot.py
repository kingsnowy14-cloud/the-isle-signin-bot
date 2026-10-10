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
        "DISCORD_TOKEN fehlt. Bitte in Railway Variables setzen."
    )


# ==================================================
# SPRACHEN
# ==================================================

TEXT = {
    "de": {
        "language_saved": "Deine Sprache wurde auf Deutsch eingestellt.",
        "modal_title": "The Isle – Spieleranmeldung",
        "dino": "Dinosaurier",
        "dino_placeholder": "z. B. Stego",
        "skin": "Skin",
        "skin_placeholder": "Dein Skin",
        "location": "Standort",
        "location_placeholder": "z. B. Sumpfteich",
        "signed_in": "Du wurdest erfolgreich angemeldet!",
        "signed_out": "Du wurdest erfolgreich abgemeldet.",
        "not_signed_in": "Du bist aktuell nicht angemeldet.",
        "signin_title": "🟢 Spieler angemeldet",
        "signout_title": "🔴 Spieler abgemeldet",
        "player": "Spieler",
        "last_location": "Letzter Standort",
        "footer": "The Isle – Spieleranmeldung",
        "no_channel": "Der Anmeldekanal wurde noch nicht eingerichtet. Bitte informiere einen Administrator.",
        "panel_title": "🦖 THE ISLE – SPIELERANMELDUNG",
        "panel_description": (
            "Wähle zuerst deine Sprache aus.\n\n"
            "🟢 **Sign In:** Melde deinen Charakter an.\n"
            "🔴 **Sign Out:** Melde dich nach dem Spielen ab."
        ),
        "panel_created": "Das Anmeldepanel wurde erstellt.",
        "admin_only": "Du benötigst Administratorrechte.",
        "channel_set": "Der Anmeldekanal wurde auf {channel} eingestellt.",
        "unknown": "Unbekannt",
    },
    "en": {
        "language_saved": "Your language has been set to English.",
        "modal_title": "The Isle – Player Sign-In",
        "dino": "Dinosaur",
        "dino_placeholder": "e.g. Stego",
        "skin": "Skin",
        "skin_placeholder": "Your skin",
        "location": "Location",
        "location_placeholder": "e.g. Swamp",
        "signed_in": "You have successfully signed in!",
        "signed_out": "You have successfully signed out.",
        "not_signed_in": "You are not currently signed in.",
        "signin_title": "🟢 Player Signed In",
        "signout_title": "🔴 Player Signed Out",
        "player": "Player",
        "last_location": "Last Location",
        "footer": "The Isle – Player Sign-In",
        "no_channel": "The sign-in announcement channel has not been configured. Please contact an administrator.",
        "panel_title": "🦖 THE ISLE – PLAYER SIGN-IN",
        "panel_description": (
            "Please choose your language first.\n\n"
            "🟢 **Sign In:** Register your character.\n"
            "🔴 **Sign Out:** Sign out when you finish playing."
        ),
        "panel_created": "The sign-in panel has been created.",
        "admin_only": "You need administrator permissions.",
        "channel_set": "The sign-in channel has been set to {channel}.",
        "unknown": "Unknown",
    },
    "cs": {
        "language_saved": "Jazyk byl nastaven na češtinu.",
        "modal_title": "The Isle – Registrace hráče",
        "dino": "Dinosaurus",
        "dino_placeholder": "např. Stego",
        "skin": "Vzhled",
        "skin_placeholder": "Tvůj vzhled",
        "location": "Poloha",
        "location_placeholder": "např. bažina",
        "signed_in": "Úspěšně ses přihlásil!",
        "signed_out": "Úspěšně ses odhlásil.",
        "not_signed_in": "Momentálně nejsi přihlášen.",
        "signin_title": "🟢 Hráč přihlášen",
        "signout_title": "🔴 Hráč odhlášen",
        "player": "Hráč",
        "last_location": "Poslední poloha",
        "footer": "The Isle – Registrace hráče",
        "no_channel": "Kanál pro oznámení registrací není nastaven. Kontaktuj správce.",
        "panel_title": "🦖 THE ISLE – REGISTRACE HRÁČE",
        "panel_description": (
            "Nejprve si vyber jazyk.\n\n"
            "🟢 **Sign In:** Zaregistruj svou postavu.\n"
            "🔴 **Sign Out:** Odhlas se po skončení hraní."
        ),
        "panel_created": "Registrační panel byl vytvořen.",
        "admin_only": "Potřebuješ oprávnění správce.",
        "channel_set": "Registrační kanál byl nastaven na {channel}.",
        "unknown": "Neznámý",
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
    language = load_json(LANGUAGES_FILE).get(str(user_id), "en")
    return language if language in TEXT else "en"


def set_language(user_id: int, language: str) -> None:
    if language not in TEXT:
        language = "en"

    languages = load_json(LANGUAGES_FILE)
    languages[str(user_id)] = language
    save_json(LANGUAGES_FILE, languages)


def get_announcement_channel_id():
    return load_json(CONFIG_FILE).get("announcement_channel_id")


async def get_announcement_channel(guild: discord.Guild):
    channel_id = get_announcement_channel_id()

    if not channel_id:
        return None

    channel = guild.get_channel(int(channel_id))

    if channel is None:
        try:
            channel = await bot.fetch_channel(int(channel_id))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None

    if not isinstance(channel, discord.TextChannel):
        return None

    return channel


# ==================================================
# BOT
# ==================================================

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
)


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
        t = TEXT[self.language]

        if interaction.guild is None:
            await interaction.response.send_message(
                "This form can only be used in a server.",
                ephemeral=True,
            )
            return

        channel = await get_announcement_channel(interaction.guild)

        if channel is None:
            await interaction.response.send_message(
                t["no_channel"],
                ephemeral=True,
            )
            return

        data = load_json(SIGNINS_FILE)
        user_id = str(interaction.user.id)

        data[user_id] = {
            "discord_name": interaction.user.display_name,
            "discord_id": interaction.user.id,
            "dino": self.dinosaur.value.strip(),
            "skin": self.skin.value.strip(),
            "location": self.location.value.strip(),
            "status": "Angemeldet",
            "language": self.language,
            "guild_id": interaction.guild_id,
            "updated_at": discord.utils.utcnow().isoformat(),
        }

        save_json(SIGNINS_FILE, data)

        embed = discord.Embed(
            title=t["signin_title"],
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        )

        embed.add_field(
            name=t["player"],
            value=interaction.user.mention,
            inline=False,
        )
        embed.add_field(
            name=t["dino"],
            value=self.dinosaur.value.strip(),
            inline=True,
        )
        embed.add_field(
            name=t["skin"],
            value=self.skin.value.strip(),
            inline=True,
        )
        embed.add_field(
            name=t["location"],
            value=self.location.value.strip(),
            inline=False,
        )
        embed.set_footer(text=t["footer"])

        try:
            await channel.send(
                embed=embed,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            await interaction.response.send_message(
                "Die Anmeldung wurde gespeichert, aber die öffentliche Nachricht konnte nicht gesendet werden. Bitte prüfe die Bot-Berechtigungen im Anmeldekanal.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            t["signed_in"],
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
        t = TEXT[language]

        data = load_json(SIGNINS_FILE)
        user_id = str(interaction.user.id)

        if (
            user_id not in data
            or data[user_id].get("status") != "Angemeldet"
        ):
            await interaction.response.send_message(
                t["not_signed_in"],
                ephemeral=True,
            )
            return

        channel = await get_announcement_channel(interaction.guild)

        if channel is None:
            await interaction.response.send_message(
                t["no_channel"],
                ephemeral=True,
            )
            return

        data[user_id]["status"] = "Abgemeldet"
        data[user_id]["updated_at"] = (
            discord.utils.utcnow().isoformat()
        )
        save_json(SIGNINS_FILE, data)

        embed = discord.Embed(
            title=t["signout_title"],
            color=discord.Color.red(),
            timestamp=discord.utils.utcnow(),
        )

        embed.add_field(
            name=t["player"],
            value=interaction.user.mention,
            inline=False,
        )
        embed.add_field(
            name=t["last_location"],
            value=data[user_id].get("location", t["unknown"]),
            inline=False,
        )
        embed.set_footer(text=t["footer"])

        try:
            await channel.send(
                embed=embed,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            await interaction.response.send_message(
                "Der Abmeldestatus wurde gespeichert, aber die öffentliche Nachricht konnte nicht gesendet werden. Bitte prüfe die Bot-Berechtigungen.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            t["signed_out"],
            ephemeral=True,
        )
# ==================================================
# !anmeldepannel
# ==================================================

@bot.command(name="anmeldepannel")
@commands.has_permissions(administrator=True)
async def anmeldepannel(ctx: commands.Context) -> None:
    embed = discord.Embed(
        title="🦖 THE ISLE – PLAYER SIGN-IN | SPIELERANMELDUNG",
        color=discord.Color.blurple(),
        description=(
            "Welcome to the official player sign-in panel!\n"
            "Willkommen beim Spieler-Anmeldepanel!\n"
            "Vítej v registračním panelu hráčů!\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"

            "🇬🇧 **ENGLISH – HOW TO SIGN IN**\n"
            "1. Select your language from the menu below.\n"
            "2. Click **Sign In**.\n"
            "3. Enter your dinosaur, skin and current location.\n"
            "4. Submit the form. Your sign-in will be posted in the designated channel.\n"
            "5. When you finish playing, click **Sign Out**.\n\n"

            "━━━━━━━━━━━━━━━━━━━━\n\n"

            "🇩🇪 **DEUTSCH – SO MELDEST DU DICH AN**\n"
            "1. Wähle unten deine Sprache aus.\n"
            "2. Klicke auf **Anmelden**.\n"
            "3. Gib deinen Dinosaurier, deinen Skin und deinen aktuellen Standort ein.\n"
            "4. Sende das Formular ab. Deine Anmeldung wird im festgelegten Kanal veröffentlicht.\n"
            "5. Wenn du mit dem Spielen fertig bist, klicke auf **Abmelden**.\n\n"

            "━━━━━━━━━━━━━━━━━━━━\n\n"

            "🇨🇿 **ČEŠTINA – JAK SE PŘIHLÁSIT**\n"
            "1. Vyber si jazyk v nabídce níže.\n"
            "2. Klikni na **Přihlásit se**.\n"
            "3. Vyplň svého dinosaura, vzhled a aktuální polohu.\n"
            "4. Odešli formulář. Tvoje registrace bude zveřejněna v určeném kanálu.\n"
            "5. Až skončíš s hraním, klikni na **Odhlásit se**.\n\n"

            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "🟢 Sign In / Anmelden / Přihlásit se\n"
            "🔴 Sign Out / Abmelden / Odhlásit se\n"
            "🌐 Choose your language / Wähle deine Sprache / Vyber si jazyk"
        ),
    )

    embed.set_footer(
        text="The Isle Evrima • Player Registration • Spieleranmeldung"
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
        print(f"Fehler bei !anmeldepannel: {error}")

# ==================================================
# /kanal-set
# ==================================================

@bot.tree.command(
    name="kanal-set",
    description="Set the channel for public player sign-in announcements.",
)
@app_commands.describe(
    kanal="The text channel where sign-ins and sign-outs are posted.",
)
@app_commands.checks.has_permissions(administrator=True)
async def kanal_set(
    interaction: discord.Interaction,
    kanal: discord.TextChannel,
) -> None:
    config = load_json(CONFIG_FILE)
    config["announcement_channel_id"] = kanal.id
    save_json(CONFIG_FILE, config)

    await interaction.response.send_message(
        TEXT[get_language(interaction.user.id)]["channel_set"].format(
            channel=kanal.mention
        ),
        ephemeral=True,
    )


@kanal_set.error
async def kanal_set_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError,
) -> None:
    if isinstance(error, app_commands.MissingPermissions):
        message = TEXT[get_language(interaction.user.id)]["admin_only"]

        if not interaction.response.is_done():
            await interaction.response.send_message(
                message,
                ephemeral=True,
            )
    else:
        print(f"Fehler bei /kanal-set: {error}")

        if not interaction.response.is_done():
            await interaction.response.send_message(
                "An error occurred while setting the channel. Check the bot logs.",
                ephemeral=True,
            )


# ==================================================
# START
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
            print(f"Slash-Befehle synchronisiert: {len(synced)}")
        except discord.HTTPException as error:
            print(f"Slash-Befehle konnten nicht synchronisiert werden: {error}")

    print(f"Bot online: {bot.user} (ID: {bot.user.id})")


bot.run(TOKEN)
