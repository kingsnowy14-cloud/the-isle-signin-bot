import os
import json
from pathlib import Path

import discord
from discord.ext import commands


# ==================================================
# KONFIGURATION
# ==================================================

TOKEN = os.getenv("DISCORD_TOKEN")

SIGNINS_FILE = Path("signins.json")
LANGUAGES_FILE = Path("languages.json")

if not TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN fehlt. Bitte in Railway unter Variables setzen."
    )


# ==================================================
# ÜBERSETZUNGEN
# ==================================================

TEXT = {
    "de": {
        "panel_title": "🦖 THE ISLE – SPIELERANMELDUNG",
        "panel_description": (
            "Wähle zuerst deine Sprache aus.\n\n"
            "🟢 **Anmelden:** Trage Dinosaurier, Skin und Standort ein.\n"
            "🔴 **Abmelden:** Melde dich ab, wenn du fertig gespielt hast."
        ),
        "language_saved": "Deine Sprache wurde auf Deutsch eingestellt.",
        "language_placeholder": "Sprache auswählen",
        "signin_button": "Anmelden",
        "signout_button": "Abmelden",
        "modal_title": "The Isle – Spieleranmeldung",
        "dino_label": "Dinosaurier",
        "dino_placeholder": "z. B. Stego",
        "skin_label": "Skin",
        "skin_placeholder": "Dein Skin",
        "location_label": "Standort",
        "location_placeholder": "z. B. Sumpfteich",
        "signed_in": "Du wurdest erfolgreich angemeldet!",
        "signed_out": "Du wurdest erfolgreich abgemeldet!",
        "not_signed_in": "Du bist aktuell nicht angemeldet.",
        "notice_title": "🟢 Spieler angemeldet",
        "out_title": "🔴 Spieler abgemeldet",
        "player": "Spieler",
        "dino": "Dinosaurier",
        "skin": "Skin",
        "location": "Standort",
        "last_location": "Letzter Standort",
        "footer": "Anmeldestatus gespeichert.",
        "panel_created": "Das Anmeldepanel wurde erstellt.",
        "admin_only": "Du benötigst Administratorrechte.",
        "unknown": "Unbekannt",
    },
    "en": {
        "panel_title": "🦖 THE ISLE – PLAYER SIGN-IN",
        "panel_description": (
            "Choose your language first.\n\n"
            "🟢 **Sign In:** Enter your dinosaur, skin and location.\n"
            "🔴 **Sign Out:** Sign out when you finish playing."
        ),
        "language_saved": "Your language has been set to English.",
        "language_placeholder": "Choose your language",
        "signin_button": "Sign In",
        "signout_button": "Sign Out",
        "modal_title": "The Isle – Player Sign-In",
        "dino_label": "Dinosaur",
        "dino_placeholder": "e.g. Stego",
        "skin_label": "Skin",
        "skin_placeholder": "Your skin",
        "location_label": "Location",
        "location_placeholder": "e.g. Swamp",
        "signed_in": "You have successfully signed in!",
        "signed_out": "You have successfully signed out!",
        "not_signed_in": "You are not currently signed in.",
        "notice_title": "🟢 Player Signed In",
        "out_title": "🔴 Player Signed Out",
        "player": "Player",
        "dino": "Dinosaur",
        "skin": "Skin",
        "location": "Location",
        "last_location": "Last Location",
        "footer": "Sign-in status saved.",
        "panel_created": "The sign-in panel has been created.",
        "admin_only": "You need administrator permissions.",
        "unknown": "Unknown",
    },
    "cs": {
        "panel_title": "🦖 THE ISLE – REGISTRACE HRÁČE",
        "panel_description": (
            "Nejprve si vyber jazyk.\n\n"
            "🟢 **Přihlásit se:** Vyplň dinosaura, vzhled a polohu.\n"
            "🔴 **Odhlásit se:** Odhlas se, až skončíš s hraním."
        ),
        "language_saved": "Jazyk byl nastaven na češtinu.",
        "language_placeholder": "Vyber jazyk",
        "signin_button": "Přihlásit se",
        "signout_button": "Odhlásit se",
        "modal_title": "The Isle – Registrace hráče",
        "dino_label": "Dinosaurus",
        "dino_placeholder": "např. Stego",
        "skin_label": "Vzhled",
        "skin_placeholder": "Tvůj vzhled",
        "location_label": "Poloha",
        "location_placeholder": "např. bažina",
        "signed_in": "Úspěšně ses přihlásil!",
        "signed_out": "Úspěšně ses odhlásil!",
        "not_signed_in": "Momentálně nejsi přihlášen.",
        "notice_title": "🟢 Hráč přihlášen",
        "out_title": "🔴 Hráč odhlášen",
        "player": "Hráč",
        "dino": "Dinosaurus",
        "skin": "Vzhled",
        "location": "Poloha",
        "last_location": "Poslední poloha",
        "footer": "Stav přihlášení byl uložen.",
        "panel_created": "Registrační panel byl vytvořen.",
        "admin_only": "Potřebuješ oprávnění správce.",
        "unknown": "Neznámý",
    },
}


# ==================================================
# JSON-DATEN SICHER LADEN UND SPEICHERN
# ==================================================

def load_json(path: Path) -> dict:
    try:
        content = path.read_text(encoding="utf-8")
        data = json.loads(content)

        if isinstance(data, dict):
            return data

        return {}

    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_json(path: Path, data: dict) -> None:
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def get_language(user_id: int) -> str:
    languages = load_json(LANGUAGES_FILE)
    language = languages.get(str(user_id), "de")

    if language not in TEXT:
        return "de"

    return language


def set_language(user_id: int, language: str) -> None:
    if language not in TEXT:
        language = "de"

    languages = load_json(LANGUAGES_FILE)
    languages[str(user_id)] = language
    save_json(LANGUAGES_FILE, languages)


# ==================================================
# DISCORD-BOT EINRICHTEN
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
        self.language = language if language in TEXT else "de"
        t = TEXT[self.language]

        super().__init__(title=t["modal_title"])

        self.dinosaur = discord.ui.TextInput(
            label=t["dino_label"],
            placeholder=t["dino_placeholder"],
            max_length=60,
            required=True,
        )

        self.skin = discord.ui.TextInput(
            label=t["skin_label"],
            placeholder=t["skin_placeholder"],
            max_length=80,
            required=True,
        )

        self.location = discord.ui.TextInput(
            label=t["location_label"],
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
        data = load_json(SIGNINS_FILE)

        data[str(interaction.user.id)] = {
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
            title=t["notice_title"],
            color=discord.Color.green(),
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

        await interaction.response.send_message(
            content=t["signed_in"],
            embed=embed,
            ephemeral=True,
        )


# ==================================================
# ANMELDEPANEL MIT SPRACHWAHL UND BUTTONS
# ==================================================

class SignInView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.select(
        placeholder="Sprache auswählen / Choose language",
        custom_id="isle_language",
        options=[
            discord.SelectOption(
                label="Deutsch",
                value="de",
                emoji="🇩🇪",
            ),
            discord.SelectOption(
                label="English",
                value="en",
                emoji="🇬🇧",
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
        label="Anmelden",
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
        label="Abmelden",
        style=discord.ButtonStyle.danger,
        custom_id="isle_signout",
    )
    async def signout(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
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

        data[user_id]["status"] = "Abgemeldet"
        data[user_id]["updated_at"] = (
            discord.utils.utcnow().isoformat()
        )

        save_json(SIGNINS_FILE, data)

        embed = discord.Embed(
            title=t["out_title"],
            color=discord.Color.red(),
        )

        embed.add_field(
            name=t["player"],
            value=interaction.user.mention,
            inline=False,
        )

        embed.add_field(
            name=t["last_location"],
            value=data[user_id].get(
                "location",
                t["unknown"],
            ),
            inline=False,
        )

        embed.set_footer(text=t["footer"])

        await interaction.response.send_message(
            content=t["signed_out"],
            embed=embed,
            ephemeral=True,
        )


# ==================================================
# BEFEHL: !anmeldepanel
# ==================================================

@bot.command(name="anmeldepanel")
@commands.has_permissions(administrator=True)
async def anmeldepanel(ctx: commands.Context) -> None:
    t = TEXT["de"]

    embed = discord.Embed(
        title=t["panel_title"],
        description=t["panel_description"],
        color=discord.Color.green(),
    )

    await ctx.send(
        content=t["panel_created"],
        embed=embed,
        view=SignInView(),
    )


@anmeldepanel.error
async def anmeldepanel_error(
    ctx: commands.Context,
    error: commands.CommandError,
) -> None:
    if isinstance(error, commands.MissingPermissions):
        await ctx.send(TEXT["de"]["admin_only"])
    else:
        print(f"Fehler bei !anmeldepanel: {error}")


# ==================================================
# BOT STARTEN
# ==================================================

@bot.event
async def on_ready() -> None:
    if not getattr(bot, "signin_view_registered", False):
        bot.add_view(SignInView())
        bot.signin_view_registered = True

    print(f"Bot online: {bot.user} (ID: {bot.user.id})")


bot.run(TOKEN)
