import os
import discord
from discord import app_commands
from discord.ext import commands


class AusweisModal(discord.ui.Modal, title="Personal-Ausweis erstellen"):
    rp_name = discord.ui.TextInput(
        label="Name im Roleplay",
        placeholder="Vorname Nachname",
        min_length=2,
        max_length=60,
    )
    birth_date = discord.ui.TextInput(
        label="Geburtsdatum",
        placeholder="TT.MM.JJJJ",
        min_length=8,
        max_length=10,
    )
    role = discord.ui.TextInput(
        label="Dienstgrad / Beruf",
        placeholder="z. B. Polizei, Feuerwehr, Rettungsdienst",
        min_length=2,
        max_length=60,
    )

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message(
                "Der Ausweis kann nur auf einem Server erstellt werden.",
                ephemeral=True,
            )
            return

        raw_channel_id = os.getenv("AUSWEIS_CHANNEL_ID", "").strip()
        if not raw_channel_id.isdigit():
            await interaction.response.send_message(
                "Der Ausweis-Kanal ist noch nicht eingerichtet. Bitte einen Admin, "
                "die Railway-Variable AUSWEIS_CHANNEL_ID mit der Kanal-ID zu setzen.",
                ephemeral=True,
            )
            return

        channel = interaction.guild.get_channel(int(raw_channel_id))
        if channel is None:
            try:
                channel = await interaction.guild.fetch_channel(int(raw_channel_id))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                channel = None

        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "Der konfigurierte Ausweis-Kanal wurde nicht gefunden oder ist kein Textkanal.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="🪪 Personal-Ausweis",
            description="Offizieller Roleplay-Ausweis",
            color=discord.Color.dark_blue(),
        )
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        embed.add_field(name="Name", value=str(self.rp_name.value).strip(), inline=True)
        embed.add_field(name="Geburtsdatum", value=str(self.birth_date.value).strip(), inline=True)
        embed.add_field(name="Dienstgrad / Beruf", value=str(self.role.value).strip(), inline=False)
        embed.add_field(name="Discord-Mitglied", value=interaction.user.mention, inline=False)
        embed.set_footer(text=f"IronWolf RP • Ausweisinhaber-ID: {interaction.user.id}")

        try:
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except (discord.Forbidden, discord.HTTPException):
            await interaction.response.send_message(
                "Ich konnte den Ausweis nicht senden. Prüfe, ob der Bot im Ausweis-Kanal "
                "Nachrichten senden und Embeds einbetten darf.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"✅ Dein Personal-Ausweis wurde erstellt und in {channel.mention} veröffentlicht.",
            ephemeral=True,
        )


class Ausweis(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="ausweis", description="Erstelle deinen persönlichen Roleplay-Ausweis")
    @app_commands.guild_only()
    async def ausweis(self, interaction: discord.Interaction):
        await interaction.response.send_modal(AusweisModal())


async def setup(bot: commands.Bot):
    await bot.add_cog(Ausweis(bot))
