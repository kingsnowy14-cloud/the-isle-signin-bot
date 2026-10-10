import os
import json
from pathlib import Path

import discord
from discord.ext import commands

TOKEN = os.getenv("DISCORD_TOKEN")

SIGNINS_FILE = Path("signins.json")
LANGUAGES_FILE = Path("languages.json")

if not TOKEN:
    raise RuntimeError("Bitte setze DISCORD_TOKEN in Railway Variables.")


# ---------- Daten speichern und laden ----------

def load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_json(path, data):
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def get_language(user_id):
    language = load_json(LANGUAGES_FILE).get(str(user_id), "de")
    return language if language in TEXT else "de"


def set_language(user_id, language):
    if language not in TEXT:
        language = "de"

    languages = load_json(LANGUAGES_FILE)
    languages[str(user_id)] = language
    save_json(LANGUAGES_FILE, languages)


# ---------- Übersetzungen ----------

TEXT = {
    "de": {
        "panel_title": "🦖 THE ISLE – SPIELERANMELDUNG",
        "panel_description": (
            "Wähle zuerst deine Sprache im Menü.\n\n"
            "🟢 **Anmelden:** Trage Dinosaurier, Skin und Standort ein.\n"
            "🔴 **Abmelden:** Melde dich ab, wenn du fertig gespielt hast."
        ),
        "language_saved": "Deine Sprache wurde auf Deutsch eingestellt.",
        "signin_button": "Anmelden",
        "signout_button": "Abmelden",
        "language_placeholder": "Sprache auswählen / Choose language",
        "language_de": "Deutsch",
        "language_en": "English",
        "language_cs": "Čeština",
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
    },
    "en": {
        "panel_title": "🦖 THE ISLE – PLAYER SIGN-IN",
        "panel_description": (
            "Choose your language from the menu first.\n\n"
            "🟢 **Sign In:** Enter your dinosaur, skin and location.\n"
            "🔴 **Sign Out:** Sign out when you finish playing."
        ),
        "language_saved": "Your language has been set to English.",
        "signin_button": "Sign In",
        "signout_button": "Sign Out",
        "language_placeholder": "Choose your language",
        "language_de": "Deutsch",
        "language_en": "English",
        "language_cs": "Čeština",
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
    },
    "cs": {
        "panel_title": "🦖 THE ISLE – REGISTRACE HRÁČE",
        "panel_description": (
            "Nejprve si vyber jazyk v nabídce.\n\n"
            "🟢 **Přihlásit se:** Vyplň dinosaura, vzhled a polohu.\n"
            "🔴 **Odhlásit se:** Odhlas se, až skončíš s hraním."
        ),
        "language_saved": "Jazyk byl nastaven na češtinu.",
        "signin_button": "Přihlásit se",
        "signout_button": "Odhlásit se",
        "language_placeholder": "Vyber jazyk",
        "language_de": "Deutsch",
        "language_en": "English",
        "language_cs": "Čeština",
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
    },
}


# ---------- Bot einrichten ----------

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


# ---------- Anmeldeformular ----------

class SignInModal(discord.ui.Modal):
    def __init__(self, language):
        self.language = language
        t = TEXT[language]

        super().__init__(title=t["modal_title"])

        self.dinosaur = discord.ui.TextInput(
            label=t["dino_label"],
            placeholder=t["dino_placeholder"],
            max_length=60,
        )
        self.skin = discord.ui.TextInput(
            label=t["skin_label"],
            placeholder=t["skin_placeholder"],
            max_length=80,
        )
        self.location = discord.ui.TextInput(
            label=t["location_label"],
            placeholder=t["location_placeholder"],
            max_length=100,
        )

        self.add_item(self.dinosaur)
        self.add_item(self.skin)
        self.add_item(self.location)


    async def on_submit(self, interaction: discord.Interaction):
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
            ephemeral=True,
        )

@bot.event
async def on_ready():
    print(f"Bot online: {bot.user} (ID: {bot.user.id})")


@bot.event
async def on_command_error(ctx, error):
    print(f"Command-Fehler: {error}")


bot.run(TOKEN)

