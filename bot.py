import os
import json
from pathlib import Path

import discord
from discord.ext import commands

TOKEN = os.getenv("DISCORD_TOKEN")
DATA_FILE = Path("signins.json")

if not TOKEN:
    raise RuntimeError(
        "Bitte setze DISCORD_TOKEN in den Umgebungsvariablen."
    )


def daten_laden():
    try:
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def daten_speichern(daten):
    DATA_FILE.write_text(
        json.dumps(daten, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


class AnmeldeFenster(discord.ui.Modal, title="The Isle – Spieleranmeldung"):
    dinosaurier = discord.ui.TextInput(
        label="Dinosaurier",
        placeholder="z. B. Stego",
        max_length=60,
    )

    skin = discord.ui.TextInput(
        label="Skin",
        placeholder="z. B. Inferno",
        max_length=80,
    )

    standort = discord.ui.TextInput(
        label="Standort",
        placeholder="z. B. Sumpfteich",
        max_length=100,
    )

    async def on_submit(self, interaction: discord.Interaction):
        daten = daten_laden()

        daten[str(interaction.user.id)] = {
            "discord_name": interaction.user.display_name,
            "discord_id": interaction.user.id,
            "dino": self.dinosaurier.value.strip(),
            "skin": self.skin.value.strip(),
            "location": self.standort.value.strip(),
            "status": "Angemeldet",
            "aktualisiert_am": discord.utils.utcnow().isoformat(),
        }

        daten_speichern(daten)

        embed = discord.Embed(
            title="🟢 Spieler angemeldet",
            color=discord.Color.green(),
        )

        embed.add_field(
            name="Spieler",
            value=interaction.user.mention,
            inline=False,
        )
        embed.add_field(
            name="Dinosaurier",
            value=self.dinosaurier.value,
            inline=True,
        )
        embed.add_field(
            name="Skin",
            value=self.skin.value,
            inline=True,
        )
        embed.add_field(
            name="Standort",
            value=self.standort.value,
            inline=False,
        )
        embed.set_footer(
            text="Der aktuelle Anmeldestatus wurde gespeichert."
        )

        await interaction.response.send_message(
            "Du wurdest erfolgreich angemeldet!",
            ephemeral=True,
        )

        if interaction.channel:
            await interaction.channel.send(embed=embed)


class AnmeldeAnsicht(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Anmelden",
        style=discord.ButtonStyle.success,
        emoji="🟢",
        custom_id="isle:signin",
    )
    async def anmelden(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await interaction.response.send_modal(AnmeldeFenster())

    @discord.ui.button(
        label="Abmelden",
        style=discord.ButtonStyle.danger,
        emoji="🔴",
        custom_id="isle:signout",
    )
    async def abmelden(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        daten = daten_laden()
        eintrag = daten.get(str(interaction.user.id))

        if not eintrag or eintrag.get("status") != "Angemeldet":
            await interaction.response.send_message(
                "Du bist aktuell nicht angemeldet.",
                ephemeral=True,
            )
            return

        eintrag["status"] = "Abgemeldet"
        eintrag["aktualisiert_am"] = (
            discord.utils.utcnow().isoformat()
        )

        daten[str(interaction.user.id)] = eintrag
        daten_speichern(daten)

        embed = discord.Embed(
            title="🔴 Spieler abgemeldet",
            color=discord.Color.red(),
        )

        embed.add_field(
            name="Spieler",
            value=interaction.user.mention,
            inline=False,
        )
        embed.add_field(
            name="Dinosaurier",
            value=eintrag["dino"],
            inline=True,
        )
        embed.add_field(
            name="Skin",
            value=eintrag["skin"],
            inline=True,
        )
        embed.add_field(
            name="Letzter Standort",
            value=eintrag["location"],
            inline=False,
        )

        await interaction.response.send_message(
            "Du wurdest erfolgreich abgemeldet!",
            ephemeral=True,
        )

        if interaction.channel:
            await interaction.channel.send(embed=embed)


@bot.event
async def setup_hook():
    bot.add_view(AnmeldeAnsicht())
    synchronisiert = await bot.tree.sync()
    print(
        f"{len(synchronisiert)} Slash-Befehle synchronisiert."
    )


@bot.event
async def on_ready():
    print(f"Bot angemeldet als {bot.user}")


@bot.tree.command(
    name="setup-signin",
    description="Erstellt das Anmeldepanel für The Isle",
)
@discord.app_commands.default_permissions(manage_guild=True)
@discord.app_commands.guild_only()
async def setup_signin(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🦖 THE ISLE – SPIELERANMELDUNG",
        description=(
            "Klicke auf **🟢 Anmelden**, um deinen Dinosaurier, "
            "deinen Skin und deinen Standort einzutragen.\n"
            "Klicke auf **🔴 Abmelden**, wenn du fertig gespielt hast.\n\n"
            "Dein Discord-Anzeigename wird automatisch erfasst."
        ),
        color=discord.Color.blurple(),
    )

    await interaction.response.send_message(
        "Das Anmeldepanel wurde erstellt.",
        ephemeral=True,
    )

    if interaction.channel:
        await interaction.channel.send(
            embed=embed,
            view=AnmeldeAnsicht(),
        )


bot.run(TOKEN)
