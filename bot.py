
import os
import re
import json
import asyncio
from pathlib import Path
from datetime import datetime, timezone

import discord
from discord.ext import commands
from discord import app_commands


# ============================================================
# KONFIGURATION
# ============================================================

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
PREFIX = "!"

SIGNINS_FILE = Path("signins.json")
LANGUAGES_FILE = Path("languages.json")
CONFIG_FILE = Path("config.json")
TICKETS_FILE = Path("tickets.json")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix=PREFIX, intents=intents)

views_registered = False


# ============================================================
# JSON-HILFSFUNKTIONEN
# ============================================================

def load_json(path, default=None):
    if default is None:
        default = {}

    if not path.exists():
        return default.copy() if isinstance(default, dict) else default

    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)

        return data if isinstance(data, type(default)) else default.copy()

    except (json.JSONDecodeError, OSError):
        print(f"Warnung: {path} konnte nicht gelesen werden.")
        return default.copy() if isinstance(default, dict) else default


def save_json(path, data):
    temporary_path = path.with_suffix(path.suffix + ".tmp")

    with temporary_path.open("w", encoding="utf-8") as file:
        json.dump(data, file, indent=4, ensure_ascii=False)

    temporary_path.replace(path)


def load_signins():
    return load_json(SIGNINS_FILE, {})


def load_languages():
    return load_json(LANGUAGES_FILE, {})


def load_config():
    return load_json(CONFIG_FILE, {})


def load_ticket_data():
    return load_json(
        TICKETS_FILE,
        {"panels": {}, "counters": {}}
    )


def get_language(user_id):
    languages = load_languages()
    language = languages.get(str(user_id), "de")

    return language if language in ("de", "en", "cs") else "de"


def set_language(user_id, language):
    languages = load_languages()
    languages[str(user_id)] = language
    save_json(LANGUAGES_FILE, languages)


# ============================================================
# ÜBERSETZUNGEN FÜR DAS ANMELDESYSTEM
# ============================================================

TEXT = {
    "de": {
        "panel_title": "The Isle – Anmeldung",
        "panel_description": (
            "Melde dich mit dem Anmeldeknopf an oder melde dich ab, "
            "wenn du den Server verlässt.\n\n"
            "Du kannst deine Sprache über das Auswahlmenü ändern."
        ),
        "signin": "Anmelden",
        "signout": "Abmelden",
        "name": "Spielername",
        "steam": "Steam-ID",
        "dinosaur": "Dinosaurier",
        "success": "Du wurdest erfolgreich angemeldet.",
        "already": "Du bist bereits angemeldet.",
        "not_signed": "Du bist aktuell nicht angemeldet.",
        "signed_out": "Du wurdest erfolgreich abgemeldet.",
        "language": "Sprache",
        "language_set": "Deine Sprache wurde geändert.",
        "modal_title": "The Isle – Anmeldung",
    },
    "en": {
        "panel_title": "The Isle – Sign In",
        "panel_description": (
            "Use the sign-in button to join the server roster, "
            "or sign out when you leave.\n\n"
            "You can change your language using the selection menu."
        ),
        "signin": "Sign In",
        "signout": "Sign Out",
        "name": "Player Name",
        "steam": "Steam ID",
        "dinosaur": "Dinosaur",
        "success": "You have successfully signed in.",
        "already": "You are already signed in.",
        "not_signed": "You are not currently signed in.",
        "signed_out": "You have successfully signed out.",
        "language": "Language",
        "language_set": "Your language has been changed.",
        "modal_title": "The Isle – Sign In",
    },
    "cs": {
        "panel_title": "The Isle – Přihlášení",
        "panel_description": (
            "Pomocí tlačítka se přihlas nebo se odhlaš, "
            "když opouštíš server.\n\n"
            "Jazyk můžeš změnit v nabídce výběru."
        ),
        "signin": "Přihlásit se",
        "signout": "Odhlásit se",
        "name": "Jméno hráče",
        "steam": "Steam ID",
        "dinosaur": "Dinosaurus",
        "success": "Úspěšně ses přihlásil.",
        "already": "Už jsi přihlášen.",
        "not_signed": "Momentálně nejsi přihlášen.",
        "signed_out": "Úspěšně ses odhlásil.",
        "language": "Jazyk",
        "language_set": "Jazyk byl změněn.",
        "modal_title": "The Isle – Přihlášení",
    },
}


# ============================================================
# ANMELDE-MODAL
# ============================================================

class SignInModal(discord.ui.Modal):

    def __init__(self, language):
        self.language = language
        texts = TEXT[language]

        super().__init__(title=texts["modal_title"])

        self.player_name = discord.ui.TextInput(
            label=texts["name"],
            placeholder="KingSnowy",
            max_length=50,
            required=True
        )

        self.steam_id = discord.ui.TextInput(
            label=texts["steam"],
            placeholder="Deine Steam-ID",
            max_length=50,
            required=True
        )

        self.dinosaur = discord.ui.TextInput(
            label=texts["dinosaur"],
            placeholder="Dein Dinosaurier",
            max_length=50,
            required=True
        )

        self.add_item(self.player_name)
        self.add_item(self.steam_id)
        self.add_item(self.dinosaur)

    async def on_submit(self, interaction: discord.Interaction):
        signins = load_signins()
        user_id = str(interaction.user.id)
        texts = TEXT[self.language]

        if user_id in signins:
            return await interaction.response.send_message(
                texts["already"],
                ephemeral=True
            )

        signins[user_id] = {
            "username": self.player_name.value.strip(),
            "steam_id": self.steam_id.value.strip(),
            "dinosaur": self.dinosaur.value.strip(),
            "discord_name": str(interaction.user),
            "signed_in_at": datetime.now(timezone.utc).isoformat()
        }

        save_json(SIGNINS_FILE, signins)

        # Öffentliche Ankündigungen bleiben immer auf Englisch.
        config = load_config()
        channel_id = config.get("announcement_channel_id")

        if channel_id:
            channel = interaction.guild.get_channel(
                int(channel_id)
            ) if interaction.guild else None

            if channel:
                embed = discord.Embed(
                    title="Player Signed In",
                    color=discord.Color.green(),
                    timestamp=datetime.now(timezone.utc)
                )

                embed.add_field(
                    name="Player",
                    value=self.player_name.value.strip(),
                    inline=True
                )
                embed.add_field(
                    name="Steam ID",
                    value=self.steam_id.value.strip(),
                    inline=True
                )
                embed.add_field(
                    name="Dinosaur",
                    value=self.dinosaur.value.strip(),
                    inline=True
                )
                embed.add_field(
                    name="Discord",
                    value=interaction.user.mention,
                    inline=False
                )

                try:
                    await channel.send(embed=embed)
                except discord.HTTPException:
                    pass

        await interaction.response.send_message(
            texts["success"],
            ephemeral=True
        )


# ============================================================
# ANMELDE-ANSICHT UND SPRACHWAHL
# ============================================================

class SignInView(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Anmelden",
        emoji="🟢",
        style=discord.ButtonStyle.success,
        custom_id="kingsnowy:signin"
    )
    async def signin_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        signins = load_signins()
        user_id = str(interaction.user.id)
        language = get_language(interaction.user.id)

        if user_id in signins:
            return await interaction.response.send_message(
                TEXT[language]["already"],
                ephemeral=True
            )

        await interaction.response.send_modal(
            SignInModal(language)
        )

    @discord.ui.button(
        label="Abmelden",
        emoji="🔴",
        style=discord.ButtonStyle.danger,
        custom_id="kingsnowy:signout"
    )
    async def signout_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        signins = load_signins()
        user_id = str(interaction.user.id)
        language = get_language(interaction.user.id)

        if user_id not in signins:
            return await interaction.response.send_message(
                TEXT[language]["not_signed"],
                ephemeral=True
            )

        player = signins.pop(user_id)
        save_json(SIGNINS_FILE, signins)

        config = load_config()
        channel_id = config.get("announcement_channel_id")

        if channel_id and interaction.guild:
            channel = interaction.guild.get_channel(
                int(channel_id)
            )

            if channel:
                embed = discord.Embed(
                    title="Player Signed Out",
                    color=discord.Color.red(),
                    timestamp=datetime.now(timezone.utc)
                )

                embed.add_field(
                    name="Player",
                    value=player.get("username", "Unknown"),
                    inline=True
                )
                embed.add_field(
                    name="Discord",
                    value=interaction.user.mention,
                    inline=True
                )

                try:
                    await channel.send(embed=embed)
                except discord.HTTPException:
                    pass

        await interaction.response.send_message(
            TEXT[language]["signed_out"],
            ephemeral=True
        )

    @discord.ui.select(
        placeholder="Sprache / Language / Jazyk",
        custom_id="kingsnowy:language",
        options=[
            discord.SelectOption(
                label="Deutsch",
                value="de",
                emoji="🇩🇪"
            ),
            discord.SelectOption(
                label="English",
                value="en",
                emoji="🇬🇧"
            ),
            discord.SelectOption(
                label="Čeština",
                value="cs",
                emoji="🇨🇿"
            ),
        ]
    )
    async def language_select(
        self,
        interaction: discord.Interaction,
        select: discord.ui.Select
    ):
        language = select.values[0]
        set_language(interaction.user.id, language)

        await interaction.response.send_message(
            TEXT[language]["language_set"],
            ephemeral=True
        )


# ============================================================
# ANMELDE-PANEL-BEFEHLE
# ============================================================

@bot.command(
    name="anmeldepannel",
    aliases=["anmeldepanel"]
)
@commands.has_permissions(manage_guild=True)
async def anmeldepannel(ctx):
    embed = discord.Embed(
        title="The Isle – Sign In",
        description=(
            "Sign in before playing and sign out when you leave.\n\n"
            "Use the buttons below to manage your sign-in status.\n"
            "Select your preferred language from the menu."
        ),
        color=discord.Color.blue()
    )

    await ctx.send(embed=embed, view=SignInView())


@anmeldepannel.error
async def anmeldepannel_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send(
            "Du brauchst die Berechtigung **Server verwalten**."
        )
    else:
        print("Fehler bei !anmeldepannel:", repr(error))
        await ctx.send(
            "Beim Erstellen des Panels ist ein Fehler aufgetreten."
        )


@bot.tree.command(
    name="kanal-set",
    description="Legt den Kanal für öffentliche An- und Abmeldungen fest."
)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(
    kanal="Textkanal für die An- und Abmelde-Ankündigungen"
)
async def kanal_set(
    interaction: discord.Interaction,
    kanal: discord.TextChannel
):
    config = load_config()
    config["announcement_channel_id"] = kanal.id
    save_json(CONFIG_FILE, config)

    await interaction.response.send_message(
        f"Der Ankündigungskanal wurde auf {kanal.mention} gesetzt.",
        ephemeral=True
    )


# ============================================================
# TICKET-HILFSFUNKTIONEN
# ============================================================

def get_topic_value(channel, key, default="0"):
    topic = channel.topic or ""

    match = re.search(
        rf"(?:^|\s){re.escape(key)}:([^\s]+)",
        topic
    )

    return match.group(1) if match else default


async def set_topic_values(channel, **values):
    topic = channel.topic or ""

    for key, value in values.items():
        topic = re.sub(
            rf"(?:^|\s){re.escape(key)}:[^\s]+",
            "",
            topic
        ).strip()

        topic = f"{topic} {key}:{value}".strip()

    await channel.edit(topic=topic[:1024])


def get_ticket_owner_id(channel):
    return int(get_topic_value(channel, "ticket-owner", "0"))


def get_ticket_role_id(channel):
    return int(get_topic_value(channel, "ticket-role", "0"))


def get_ticket_claimed_id(channel):
    return int(get_topic_value(channel, "ticket-claimed", "0"))


def user_has_support_role(user, channel):
    role_id = get_ticket_role_id(channel)

    return (
        isinstance(user, discord.Member)
        and any(role.id == role_id for role in user.roles)
    )


def make_ticket_slug(name):
    slug = re.sub(
        r"[^a-z0-9-]+",
        "-",
        name.lower()
    ).strip("-")

    return slug[:30] or "support"


# ============================================================
# TICKET-KONTROLLKNÖPFE
# ============================================================

class TicketControlsView(discord.ui.View):

    def __init__(self, claimed_by=0):
        super().__init__(timeout=None)

        claim_button = discord.ui.Button(
            label="Claim",
            emoji="🙋",
            style=discord.ButtonStyle.primary,
            custom_id="kingsnowy:ticket:claim",
            disabled=bool(claimed_by)
        )
        claim_button.callback = self.claim_callback
        self.add_item(claim_button)

        release_button = discord.ui.Button(
            label="Freigeben",
            emoji="🔓",
            style=discord.ButtonStyle.secondary,
            custom_id="kingsnowy:ticket:release",
            disabled=not bool(claimed_by)
        )
        release_button.callback = self.release_callback
        self.add_item(release_button)

        close_button = discord.ui.Button(
            label="Ticket schließen",
            emoji="🔒",
            style=discord.ButtonStyle.danger,
            custom_id="kingsnowy:ticket:close",
        )
        close_button.callback = self.close_callback
        self.add_item(close_button)

        request_button = discord.ui.Button(
            label="Schließungsantrag",
            emoji="📝",
            style=discord.ButtonStyle.success,
            custom_id="kingsnowy:ticket:request-close",
        )
        request_button.callback = self.request_close_callback
        self.add_item(request_button)

    async def claim_callback(self, interaction: discord.Interaction):
        channel = interaction.channel

        if not isinstance(channel, discord.TextChannel):
            return await interaction.response.send_message(
                "Dieser Knopf funktioniert nur in einem Ticket.",
                ephemeral=True
            )

        if not user_has_support_role(interaction.user, channel):
            return await interaction.response.send_message(
                "Nur die Support-Rolle darf Tickets übernehmen.",
                ephemeral=True
            )

        if get_ticket_claimed_id(channel):
            return await interaction.response.send_message(
                "Dieses Ticket wurde bereits übernommen.",
                ephemeral=True
            )

        await set_topic_values(
            channel,
            **{"ticket-claimed": interaction.user.id}
        )

        await interaction.response.edit_message(
            view=TicketControlsView(claimed_by=interaction.user.id)
        )

        await channel.send(
            f"🙋 {interaction.user.mention} hat dieses Ticket übernommen."
        )

    async def release_callback(self, interaction: discord.Interaction):
        channel = interaction.channel

        if not isinstance(channel, discord.TextChannel):
            return await interaction.response.send_message(
                "Dieser Knopf funktioniert nur in einem Ticket.",
                ephemeral=True
            )

        claimed_by = get_ticket_claimed_id(channel)

        if not claimed_by or claimed_by != interaction.user.id:
            return await interaction.response.send_message(
                "Nur der Supporter, der das Ticket übernommen hat, "
                "darf es wieder freigeben.",
                ephemeral=True
            )

        await set_topic_values(
            channel,
            **{"ticket-claimed": 0}
        )

        await interaction.response.edit_message(
            view=TicketControlsView()
        )

        await channel.send(
            f"🔓 {interaction.user.mention} hat das Ticket wieder freigegeben."
        )

    async def close_callback(self, interaction: discord.Interaction):
        channel = interaction.channel

        if not isinstance(channel, discord.TextChannel):
            return await interaction.response.send_message(
                "Dieser Knopf funktioniert nur in einem Ticket.",
                ephemeral=True
            )

        if not user_has_support_role(interaction.user, channel):
            return await interaction.response.send_message(
                "Nur die Support-Rolle darf Tickets direkt schließen.",
                ephemeral=True
            )

        await interaction.response.send_message(
            "🔒 Das Ticket wird in 5 Sekunden geschlossen."
        )

        await asyncio.sleep(5)

        try:
            await channel.delete(
                reason=f"Ticket geschlossen von {interaction.user}"
            )
        except discord.NotFound:
            pass

    async def request_close_callback(
        self,
        interaction: discord.Interaction
    ):
        channel = interaction.channel

        if not isinstance(channel, discord.TextChannel):
            return await interaction.response.send_message(
                "Dieser Knopf funktioniert nur in einem Ticket.",
                ephemeral=True
            )

        if get_topic_value(
            channel, "ticket-close-request", "0"
        ) == "1":
            return await interaction.response.send_message(
                "Für dieses Ticket läuft bereits ein Schließungsantrag.",
                ephemeral=True
            )

        await set_topic_values(
            channel,
            **{
                "ticket-close-request": 1,
                "ticket-close-owner-yes": 0,
                "ticket-close-support-yes": 0
            }
        )

        await interaction.response.send_message(
            f"📝 {interaction.user.mention} hat einen Schließungsantrag "
            "gestellt.\n\n"
            "Das Ticket wird nur geschlossen, wenn sowohl der "
            "Ticket-Ersteller als auch ein Supporter mit Ja zustimmen.",
            view=TicketCloseVoteView()
        )


# ============================================================
# SCHLIESSUNGSANTRAG: BEIDE SEITEN MÜSSEN JA SAGEN
# ============================================================

class TicketCloseVoteView(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Ja, schließen",
        emoji="✅",
        style=discord.ButtonStyle.success,
        custom_id="kingsnowy:ticket:vote-yes"
    )
    async def vote_yes(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        channel = interaction.channel

        if not isinstance(channel, discord.TextChannel):
            return await interaction.response.send_message(
                "Dieser Knopf funktioniert nur in einem Ticket.",
                ephemeral=True
            )

        if get_topic_value(
            channel, "ticket-close-request", "0"
        ) != "1":
            return await interaction.response.send_message(
                "Es gibt aktuell keinen offenen Schließungsantrag.",
                ephemeral=True
            )

        owner_id = get_ticket_owner_id(channel)
        is_owner = interaction.user.id == owner_id
        is_support = user_has_support_role(interaction.user, channel)

        # Der Ersteller und Support müssen zwei getrennte Seiten sein.
        if is_owner:
            side = "ticket-close-owner-yes"
        elif is_support:
            side = "ticket-close-support-yes"
        else:
            return await interaction.response.send_message(
                "Nur der Ticket-Ersteller oder die Support-Rolle "
                "darf abstimmen.",
                ephemeral=True
            )

        if get_topic_value(channel, side, "0") == "1":
            return await interaction.response.send_message(
                "Deine Seite hat bereits zugestimmt.",
                ephemeral=True
            )

        await set_topic_values(channel, **{side: 1})

        owner_yes = (
            get_topic_value(
                channel, "ticket-close-owner-yes", "0"
            ) == "1"
        )

        support_yes = (
            get_topic_value(
                channel, "ticket-close-support-yes", "0"
            ) == "1"
        )

        if owner_yes and support_yes:
            await interaction.response.edit_message(
                content=(
                    "✅ Beide Seiten haben zugestimmt. "
                    "Das Ticket wird in 5 Sekunden geschlossen."
                ),
                view=None
            )

            await asyncio.sleep(5)

            try:
                await channel.delete(
                    reason="Beide Seiten haben dem Schließungsantrag zugestimmt"
                )
            except discord.NotFound:
                pass

            return

        if owner_yes:
            status = (
                "Ticket-Ersteller: ✅ Ja\n"
                "Support: ⏳ Noch ausstehend"
            )
        else:
            status = (
                "Ticket-Ersteller: ⏳ Noch ausstehend\n"
                "Support: ✅ Ja"
            )

        await interaction.response.edit_message(
            content=(
                "📝 **Schließungsantrag**\n"
                f"{status}\n\n"
                "Das Ticket wird erst geschlossen, wenn beide Seiten "
                "zugestimmt haben."
            ),
            view=self
        )

    @discord.ui.button(
        label="Nein, abbrechen",
        emoji="❌",
        style=discord.ButtonStyle.danger,
        custom_id="kingsnowy:ticket:vote-no"
    )
    async def vote_no(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        channel = interaction.channel

        if not isinstance(channel, discord.TextChannel):
            return await interaction.response.send_message(
                "Dieser Knopf funktioniert nur in einem Ticket.",
                ephemeral=True
            )

        is_owner = (
            interaction.user.id == get_ticket_owner_id(channel)
        )

        is_support = user_has_support_role(
            interaction.user, channel
        )

        if not (is_owner or is_support):
            return await interaction.response.send_message(
                "Nur der Ticket-Ersteller oder die Support-Rolle "
                "darf den Antrag ablehnen.",
                ephemeral=True
            )

        if get_topic_value(
            channel, "ticket-close-request", "0"
        ) != "1":
            return await interaction.response.send_message(
                "Es gibt aktuell keinen offenen Schließungsantrag.",
                ephemeral=True
            )

        await set_topic_values(
            channel,
            **{
                "ticket-close-request": 0,
                "ticket-close-owner-yes": 0,
                "ticket-close-support-yes": 0
            }
        )

        await interaction.response.edit_message(
            content=(
                f"❌ {interaction.user.mention} hat den "
                "Schließungsantrag abgelehnt. Das Ticket bleibt offen."
            ),
            view=None
        )


# ============================================================
# TICKET-PANEL
# ============================================================

class TicketPanelView(discord.ui.View):

    def __init__(self, ticket_name="Support"):
        super().__init__(timeout=None)

        self.ticket_name = ticket_name

        self.create_ticket.label = ticket_name[:80]

    @discord.ui.button(
        label="Support",
        emoji="🎫",
        style=discord.ButtonStyle.primary,
        custom_id="kingsnowy:ticket:create"
    )
    async def create_ticket(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        if interaction.guild is None:
            return await interaction.response.send_message(
                "Tickets können nur auf einem Server erstellt werden.",
                ephemeral=True
            )

        data = load_ticket_data()
        panel = data.get("panels", {}).get(
            str(interaction.message.id)
        )

        if not panel:
            return await interaction.response.send_message(
                "Die Ticket-Konfiguration wurde nicht gefunden. "
                "Bitte erstelle das Panel erneut.",
                ephemeral=True
            )

        guild = interaction.guild
        role_id = int(panel["ping_role_id"])
        support_role = guild.get_role(role_id)

        if support_role is None:
            return await interaction.response.send_message(
                "Die konfigurierte Support-Rolle existiert nicht mehr.",
                ephemeral=True
            )

        category = None
        category_id = panel.get("category_id")

        if category_id:
            category = guild.get_channel(int(category_id))

            if not isinstance(category, discord.CategoryChannel):
                category = None

        slug = panel["ticket_slug"]
        counter_key = f"{guild.id}:{slug}"

        data.setdefault("counters", {})
        ticket_number = int(data["counters"].get(counter_key, 0)) + 1
        data["counters"][counter_key] = ticket_number
        save_json(TICKETS_FILE, data)

        bot_member = guild.get_member(bot.user.id)

        if bot_member is None:
            return await interaction.response.send_message(
                "Der Bot konnte seine Servermitgliedschaft nicht finden.",
                ephemeral=True
            )

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=False
            ),
            interaction.user: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True
            ),
            support_role: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True
            ),
            bot_member: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_channels=True,
                manage_messages=True
            )
        }

        channel_name = f"ticket-{slug}-{ticket_number:03d}"[:100]

        topic = (
            f"ticket-owner:{interaction.user.id} "
            f"ticket-role:{support_role.id} "
            f"ticket-number:{ticket_number} "
            f"ticket-type:{slug} "
            f"ticket-claimed:0 "
            f"ticket-close-request:0 "
            f"ticket-close-owner-yes:0 "
            f"ticket-close-support-yes:0"
        )

        try:
            channel = await guild.create_text_channel(
                name=channel_name,
                category=category,
                overwrites=overwrites,
                topic=topic,
                reason=f"Ticket erstellt von {interaction.user}"
            )
        except discord.Forbidden:
            return await interaction.response.send_message(
                "Der Bot hat keine Berechtigung, Ticket-Kanäle zu erstellen.",
                ephemeral=True
            )
        except discord.HTTPException as error:
            print("Fehler beim Erstellen des Ticket-Kanals:", repr(error))

            return await interaction.response.send_message(
                "Der Ticket-Kanal konnte nicht erstellt werden.",
                ephemeral=True
            )

        embed = discord.Embed(
            title=f"{panel['ticket_name']} #{ticket_number:03d}",
            description=(
                f"Hallo {interaction.user.mention}!\n\n"
                "Beschreibe hier dein Anliegen. Ein Supporter kann "
                "das Ticket mit **Claim** übernehmen.\n\n"
                "Du kannst einen **Schließungsantrag** stellen. "
                "Dieser wird nur umgesetzt, wenn der Ticket-Ersteller "
                "und ein Supporter jeweils mit Ja zustimmen."
            ),
            color=discord.Color.blue(),
            timestamp=datetime.now(timezone.utc)
        )

        embed.add_field(
            name="Ticket-Ersteller",
            value=interaction.user.mention,
            inline=True
        )

        embed.add_field(
            name="Ticket-Nummer",
            value=f"{ticket_number:03d}",
            inline=True
        )

        try:
            await channel.send(
                content=(
                    f"{support_role.mention} Neues Ticket von "
                    f"{interaction.user.mention}"
                ),
                embed=embed,
                view=TicketControlsView(),
                allowed_mentions=discord.AllowedMentions(
                    roles=True,
                    users=True
                )
            )

        except discord.HTTPException as error:
            print("Fehler beim Senden der Ticket-Nachricht:", repr(error))

            try:
                await channel.delete(
                    reason="Ticket-Nachricht konnte nicht gesendet werden"
                )
            except discord.HTTPException:
                pass

            return await interaction.response.send_message(
                "Der Ticket-Kanal wurde erstellt, aber die Nachricht "
                "konnte nicht gesendet werden. Prüfe die Bot-Rechte.",
                ephemeral=True
            )

        await interaction.response.send_message(
            f"Dein Ticket wurde erstellt: {channel.mention}",
            ephemeral=True
        )


# ============================================================
# TICKET-ERSTELLUNGSBEFEHL
# ============================================================

@bot.tree.command(
    name="ticketcreat",
    description="Erstellt ein Ticket-Panel mit konfigurierbarer Support-Rolle."
)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(
    ping_rolle="Die Rolle, die bei neuen Tickets erwähnt wird",
    ticket_name="Name des Ticket-Buttons und der Ticket-Art",
    kategorie="Optionale Kategorie für die Ticket-Kanäle"
)
async def ticketcreat(
    interaction: discord.Interaction,
    ping_rolle: discord.Role,
    ticket_name: app_commands.Range[str, 1, 80],
    kategorie: discord.CategoryChannel = None
):
    if interaction.guild is None:
        return await interaction.response.send_message(
            "Dieser Befehl funktioniert nur auf einem Server.",
            ephemeral=True
        )

    if ping_rolle.is_default():
        return await interaction.response.send_message(
            "Bitte wähle eine echte Support-Rolle und nicht @everyone.",
            ephemeral=True
        )

    bot_member = interaction.guild.get_member(bot.user.id)

    if bot_member is None:
        return await interaction.response.send_message(
            "Der Bot konnte seine Servermitgliedschaft nicht finden.",
            ephemeral=True
        )

    if ping_rolle >= bot_member.top_role:
        return await interaction.response.send_message(
            "Die Support-Rolle muss in der Rollenliste unterhalb "
            "der höchsten Bot-Rolle stehen.",
            ephemeral=True
        )

    slug = make_ticket_slug(ticket_name)

    embed = discord.Embed(
        title=f"{ticket_name} – Tickets",
        description=(
            "Klicke auf den Knopf unten, um ein privates Ticket zu erstellen.\n\n"
            "Ein Supporter wird benachrichtigt und kann das Ticket übernehmen."
        ),
        color=discord.Color.blue()
    )

    await interaction.response.send_message(
        embed=embed,
        view=TicketPanelView(ticket_name)
    )

    panel_message = await interaction.original_response()

    data = load_ticket_data()
    data.setdefault("panels", {})
    data.setdefault("counters", {})

    data["panels"][str(panel_message.id)] = {
        "guild_id": interaction.guild.id,
        "ping_role_id": ping_rolle.id,
        "ticket_name": ticket_name,
        "ticket_slug": slug,
        "category_id": kategorie.id if kategorie else None
    }

    save_json(TICKETS_FILE, data)


# ============================================================
# FEHLERBEHANDLUNG FÜR SLASH-COMMANDS
# ============================================================

@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError
):
    if isinstance(error, app_commands.MissingPermissions):
        message = "Du brauchst die Berechtigung **Server verwalten**."
    else:
        print("Slash-Command-Fehler:", repr(error))
        message = (
            "Beim Ausführen des Befehls ist ein Fehler aufgetreten. "
            "Prüfe die Railway-Logs."
        )

    try:
        if interaction.response.is_done():
            await interaction.followup.send(
                message,
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                message,
                ephemeral=True
            )
    except discord.HTTPException:
        pass


# ============================================================
# BOT-START
# ============================================================

@bot.event
async def on_ready():
    global views_registered

    print(f"Bot online: {bot.user} (ID: {bot.user.id})")

    if not views_registered:
        # Anmelde-Buttons und Sprachauswahl
        bot.add_view(SignInView())

        # Ticket-Steuerung und Abstimmung
        bot.add_view(TicketControlsView())
        bot.add_view(TicketCloseVoteView())

        # Bereits erstellte Ticket-Panels nach Neustart registrieren
        data = load_ticket_data()

        for message_id, panel in data.get("panels", {}).items():
            try:
                bot.add_view(
                    TicketPanelView(panel.get("ticket_name", "Support")),
                    message_id=int(message_id)
                )
            except (ValueError, TypeError) as error:
                print(
                    f"Ticket-Panel {message_id} konnte nicht "
                    f"registriert werden: {error}"
                )

        views_registered = True

    try:
        synced = await bot.tree.sync()
        print(
            f"Slash-Commands synchronisiert: "
            f"{len(synced)}"
        )
        print(
            "Registrierte Slash-Commands:",
            [command.name for command in bot.tree.get_commands()]
        )
    except Exception as error:
        print("Fehler beim Synchronisieren der Slash-Commands:", repr(error))


if not TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN fehlt. Setze die Variable in Railway unter Variables."
    )

bot.run(TOKEN)
