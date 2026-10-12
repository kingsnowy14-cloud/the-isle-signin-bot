import os
import discord
from discord import app_commands
from discord.ext import commands


class AusweisModal(discord.ui.Modal, title="Personal-Ausweis beantragen"):
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

    def __init__(self, bot: commands.Bot, guild_id: int, member_id: int, channel_id: int):
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id
        self.member_id = member_id
        self.channel_id = channel_id

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.member_id:
            await interaction.response.send_message(
                "Dieses Formular ist für ein anderes Mitglied bestimmt.", ephemeral=True
            )
            return

        guild = self.bot.get_guild(self.guild_id)
        if guild is None:
            await interaction.response.send_message(
                "Der ursprüngliche Server ist nicht verfügbar.", ephemeral=True
            )
            return

        member = guild.get_member(self.member_id)
        if member is None:
            await interaction.response.send_message(
                "Du bist nicht mehr Mitglied des ursprünglichen Servers.", ephemeral=True
            )
            return

        channel = guild.get_channel(self.channel_id)
        if channel is None:
            try:
                channel = await guild.fetch_channel(self.channel_id)
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
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Name", value=str(self.rp_name.value).strip(), inline=True)
        embed.add_field(name="Geburtsdatum", value=str(self.birth_date.value).strip(), inline=True)
        embed.add_field(name="Dienstgrad / Beruf", value=str(self.role.value).strip(), inline=False)
        embed.add_field(name="Discord-Mitglied", value=member.mention, inline=False)
        embed.set_footer(text=f"IronWolf RP • Ausweisinhaber-ID: {member.id}")

        try:
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except (discord.Forbidden, discord.HTTPException):
            await interaction.response.send_message(
                "Ich konnte den Ausweis nicht veröffentlichen. Bitte prüfe die Bot-Rechte "
                "im Ausweis-Kanal (Nachrichten senden und Links einbetten).",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"✅ Dein Ausweis wurde erstellt und in **{guild.name}** veröffentlicht.",
            ephemeral=True,
        )


class OpenAusweisFormView(discord.ui.View):
    def __init__(self, bot: commands.Bot, guild_id: int, member_id: int, channel_id: int):
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = guild_id
        self.member_id = member_id
        self.channel_id = channel_id

    @discord.ui.button(label="Formular öffnen", emoji="📝", style=discord.ButtonStyle.primary)
    async def open_form(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.member_id:
            await interaction.response.send_message(
                "Dieser Formular-Button gehört zu einer anderen Person.", ephemeral=True
            )
            return
        await interaction.response.send_modal(
            AusweisModal(self.bot, self.guild_id, self.member_id, self.channel_id)
        )


class AusweisPanelView(discord.ui.View):
    def __init__(self, bot: commands.Bot, channel_id: int):
        super().__init__(timeout=None)
        self.bot = bot
        self.channel_id = channel_id

    @discord.ui.button(
        label="Ausweis beantragen",
        emoji="🪪",
        style=discord.ButtonStyle.primary,
        custom_id="ironwolf:ausweis:request",
    )
    async def request_ausweis(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild is None:
            await interaction.response.send_message(
                "Bitte nutze diesen Button auf dem IronWolf-Server.", ephemeral=True
            )
            return
        try:
            dm = await interaction.user.create_dm()
            await dm.send(
                f"Hallo! Du beantragst einen Personal-Ausweis für **{interaction.guild.name}**. "
                "Klicke auf den Button, um das Formular privat auszufüllen.",
                view=OpenAusweisFormView(self.bot, interaction.guild.id, interaction.user.id, self.channel_id),
            )
        except (discord.Forbidden, discord.HTTPException):
            await interaction.response.send_message(
                "Ich kann dir keine DM senden. Aktiviere Direktnachrichten für diesen Server "
                "und drücke den Button erneut.", ephemeral=True
            )
            return

        await interaction.response.send_message(
            "📩 Ich habe dir eine DM mit dem Formular-Button geschickt.", ephemeral=True
        )


class Ausweis(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        # The panel view is attached to each published message.
        pass

    @app_commands.command(
        name="ausweis",
        description="Erstellt ein Ausweis-Panel und wählt den Veröffentlichungskanal aus",
    )
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(kanal="Kanal, in dem fertige Ausweise veröffentlicht werden")
    async def ausweis(self, interaction: discord.Interaction, kanal: discord.TextChannel):
        embed = discord.Embed(
            title="🪪 Personal-Ausweis beantragen",
            description=(
                "Du möchtest einen offiziellen Roleplay-Ausweis? "
                "Klicke auf **Ausweis beantragen**. Der Bot sendet dir eine DM; "
                "dort kannst du das Formular öffnen und ausfüllen."
            ),
            color=discord.Color.dark_blue(),
        )
        await interaction.response.send_message(
            embed=embed, view=AusweisPanelView(self.bot, kanal.id)
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Ausweis(bot))
