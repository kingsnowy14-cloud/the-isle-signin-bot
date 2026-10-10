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
    languages = load_json(LANGUAGES_FILE)
    return languages.get(str(user_id), "de")


def set_language(user_id, language):
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
        "language_placeholder": "Sprache auswählen / Choose language",
        "language_de": "Deutsch",
        "language_en": "English",
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
            value=self.dinosaur.value,
            inline=True,
        )
        embed.add_field(
            name=t["skin"],
            value=self.skin.value,
            inline=True,
        )
        embed.add_field(
            name=t["location"],
            value=self.location.value,
            inline=False,
        )
        embed.set_footer(text=t["footer"])

        await interaction.response.send_message(
            t["signed_in"],
            ephemeral=True,
        )

        if interaction.channel:
            await interaction.channel.send(embed=embed)


# ---------- Sprachmenü und Buttons ----------

class SignInView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

        self.language_select = discord.ui.Select(
            placeholder="Sprache auswählen / Choose language",
            custom_id="isle:language",
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
            ],
        )
        self.language_select.callback = self.select_language
        self.add_item(self.language_select)

        signin = discord.ui.Button(
            label="🟢 Anmelden / Sign In",
            style=discord.ButtonStyle.success,
            custom_id="isle:signin",
        )
        signin.callback = self.signin
        self.add_item(signin)

        signout = discord.ui.Button(
            label="🔴 Abmelden / Sign Out",
            style=discord.ButtonStyle.danger,
            custom_id="isle:signout",
        )
        signout.callback = self.signout
        self.add_item(signout)

    async def select_language(self, interaction: discord.Interaction):
        language = self.language_select.values[0]
        set_language(interaction.user.id, language)

        await interaction.response.send_message(
            TEXT[language]["language_saved"],
            ephemeral=True,
        )

    async def signin(self, interaction: discord.Interaction):
        language = get_language(interaction.user.id)
        await interaction.response.send_modal(SignInModal(language))

    async def signout(self, interaction: discord.Interaction):
        language = get_language(interaction.user.id)
        t = TEXT[language]
        data = load_json(SIGNINS_FILE)
        record = data.get(str(interaction.user.id))

        if not record or record.get("status") != "Angemeldet":
            await interaction.response.send_message(
                t["not_signed_in"],
                ephemeral=True,
            )
            return

        record["status"] = "Abgemeldet"
        record["updated_at"] = discord.utils.utcnow().isoformat()
        data[str(interaction.user.id)] = record
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
            name=t["dino"],
            value=record.get("dino", "-"),
            inline=True,
        )
        embed.add_field(
            name=t["skin"],
            value=record.get("skin", "-"),
            inline=True,
        )
        embed.add_field(
            name=t["last_location"],
            value=record.get("location", "-"),
            inline=False,
        )
        embed.set_footer(text=t["footer"])

        await interaction.response.send_message(
            t["signed_out"],
            ephemeral=True,
        )

        if interaction.channel:
            await interaction.channel.send(embed=embed)


# ---------- Slash-Befehl ----------

@bot.tree.command(
    name="setup-signin",
    description="Erstellt das The Isle-Anmeldepanel / Creates the sign-in panel",
)
@discord.app_commands.default_permissions(manage_guild=True)
@discord.app_commands.guild_only()
async def setup_signin(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🦖 THE ISLE – SIGN-IN / SPIELERANMELDUNG",
        description=(
            "Wähle deine Sprache und nutze die Buttons.\n"
            "Choose your language and use the buttons.\n\n"
            "🟢 Anmelden / Sign In\n"
            "🔴 Abmelden / Sign Out"
        ),
        color=discord.Color.blurple(),
    )

    await interaction.response.send_message(
        "Das Anmeldepanel wurde erstellt. / Sign-in panel created.",
        ephemeral=True,
    )

    if interaction.channel:
        await interaction.channel.send(
            embed=embed,
            view=SignInView(),
        )


# ---------- Start ----------

@bot.event
async def setup_hook():
    bot.add_view(SignInView())
    synced = await bot.tree.sync()
    print(f"{len(synced)} Slash-Befehle synchronisiert.")


@bot.event
async def on_ready():
    print(f"Bot angemeldet als {bot.user}")


bot.run(TOKEN)
