"""Persistent application panels and staff review workflow for IronWolf."""
import json
from pathlib import Path
from datetime import datetime, timezone
import discord
from discord import app_commands
from discord.ext import commands

CONFIG_PATH = Path("applications_config.json")
RECORDS_PATH = Path("applications.json")

def read_json(path, default):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, type(default)) else default
    except (OSError, json.JSONDecodeError):
        return default

CONFIG = read_json(CONFIG_PATH, {"guilds": {}})
RECORDS = read_json(RECORDS_PATH, {"applications": {}})

def save_config():
    CONFIG_PATH.write_text(json.dumps(CONFIG, ensure_ascii=False, indent=2), encoding="utf-8")

def save_records():
    RECORDS_PATH.write_text(json.dumps(RECORDS, ensure_ascii=False, indent=2), encoding="utf-8")

def guild_settings(guild_id):
    return CONFIG.setdefault("guilds", {}).setdefault(str(guild_id), {})

def safe_text(value, limit=1000):
    return discord.utils.escape_mentions(str(value or ""))[:limit]

class ApplicationModal(discord.ui.Modal):
    def __init__(self, cog, guild_id, questions):
        super().__init__(title="IronWolf – Bewerbung", timeout=300)
        self.cog, self.guild_id = cog, guild_id
        self.questions = questions[:5]
        for index, question in enumerate(self.questions):
            field = discord.ui.TextInput(
                label=question[:45],
                custom_id="answer_{}".format(index),
                placeholder="Deine Antwort hier …",
                style=discord.TextStyle.paragraph if index >= 2 else discord.TextStyle.short,
                max_length=1000 if index >= 2 else 200,
                required=True,
            )
            self.add_item(field)

    async def on_submit(self, interaction):
        if not interaction.guild or interaction.guild.id != self.guild_id:
            await interaction.response.send_message("Dieses Formular gehört zu einem anderen Server.", ephemeral=True)
            return
        if any(row.get("guild_id") == self.guild_id and row.get("user_id") == interaction.user.id and row.get("status") == "pending" for row in RECORDS.setdefault("applications", {}).values()):
            await interaction.response.send_message("Du hast bereits eine offene Bewerbung. Warte bitte auf die Rückmeldung des Teams.", ephemeral=True)
            return
        settings = guild_settings(self.guild_id)
        channel = interaction.guild.get_channel(settings.get("review_channel_id") or 0)
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("Der Prüfkanal ist nicht eingerichtet. Bitte informiere das Team.", ephemeral=True)
            return
        answers = [{"question": self.questions[i], "answer": str(field.value).strip()} for i, field in enumerate(self.children)]
        app_id = "{}-{}-{}".format(self.guild_id, interaction.user.id, int(datetime.now(timezone.utc).timestamp()))
        row = {
            "id": app_id, "guild_id": self.guild_id, "user_id": interaction.user.id,
            "answers": answers, "status": "pending",
            "created_at": datetime.now(timezone.utc).isoformat(), "reviewed_by": None
        }
        RECORDS["applications"][app_id] = row
        save_records()
        embed = discord.Embed(
            title="📨 Neue Bewerbung",
            description="**Bewerber:** <@{}>\n**Status:** ⏳ Offen".format(interaction.user.id),
            color=discord.Color.blurple(), timestamp=datetime.now(timezone.utc)
        )
        for answer in answers:
            embed.add_field(name=safe_text(answer["question"], 256), value=safe_text(answer["answer"], 1000) or "—", inline=False)
        embed.set_footer(text="Bewerbungs-ID: " + app_id)
        try:
            message = await channel.send(embed=embed, view=ApplicationReviewView(self.cog, app_id), allowed_mentions=discord.AllowedMentions.none())
            row["review_message_id"] = message.id
            save_records()
        except (discord.Forbidden, discord.HTTPException):
            RECORDS["applications"].pop(app_id, None)
            save_records()
            await interaction.response.send_message("Die Bewerbung konnte nicht ans Team gesendet werden. Bitte informiere einen Administrator.", ephemeral=True)
            return
        await interaction.response.send_message("✅ Deine Bewerbung wurde eingereicht. Das Team prüft sie und meldet sich bei dir.", ephemeral=True)

class ApplicationPanelView(discord.ui.View):
    def __init__(self, cog, guild_id):
        super().__init__(timeout=None)
        self.add_item(ApplicationStartButton(cog, guild_id))

class ApplicationStartButton(discord.ui.Button):
    def __init__(self, cog, guild_id):
        super().__init__(label="Jetzt bewerben", emoji="📝", style=discord.ButtonStyle.success, custom_id="ironwolf:application:start:{}".format(guild_id))
        self.cog, self.guild_id = cog, guild_id
    async def callback(self, interaction):
        if not interaction.guild or interaction.guild.id != self.guild_id:
            await interaction.response.send_message("Bitte nutze das Panel auf dem richtigen Server.", ephemeral=True)
            return
        questions = guild_settings(self.guild_id).get("questions") or ["Wie lautet dein RP-Name?", "Wie lautet dein Roblox-Name?", "Wie alt bist du (OOC)?", "Welche Erfahrung bringst du mit?", "Warum möchtest du beitreten?"]
        await interaction.response.send_modal(ApplicationModal(self.cog, self.guild_id, questions))

class ApplicationReviewView(discord.ui.View):
    def __init__(self, cog, app_id):
        super().__init__(timeout=None)
        self.add_item(ApplicationDecisionButton(cog, app_id, "approve"))
        self.add_item(ApplicationDecisionButton(cog, app_id, "reject"))

class ApplicationDecisionButton(discord.ui.Button):
    def __init__(self, cog, app_id, decision):
        self.cog, self.app_id, self.decision = cog, app_id, decision
        super().__init__(label="Annehmen" if decision == "approve" else "Ablehnen", emoji="✅" if decision == "approve" else "❌", style=discord.ButtonStyle.success if decision == "approve" else discord.ButtonStyle.danger, custom_id="ironwolf:application:{}:{}".format(decision, app_id))

    async def callback(self, interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Nur Teammitglieder auf dem Server können Bewerbungen bearbeiten.", ephemeral=True)
            return
        settings = guild_settings(interaction.guild.id)
        role_id = settings.get("review_role_id")
        allowed_role = role_id and any(role.id == role_id for role in interaction.user.roles)
        if not (interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.manage_guild or allowed_role):
            await interaction.response.send_message("Dir fehlt die Berechtigung, Bewerbungen zu bearbeiten.", ephemeral=True)
            return
        row = RECORDS.setdefault("applications", {}).get(self.app_id)
        if not row or row.get("guild_id") != interaction.guild.id:
            await interaction.response.send_message("Bewerbung nicht gefunden.", ephemeral=True)
            return
        if row.get("status") != "pending":
            await interaction.response.send_message("Diese Bewerbung wurde bereits bearbeitet.", ephemeral=True)
            return
        row["status"] = "accepted" if self.decision == "approve" else "rejected"
        row["reviewed_by"] = interaction.user.id
        row["reviewed_at"] = datetime.now(timezone.utc).isoformat()
        save_records()
        embed = interaction.message.embeds[0] if interaction.message and interaction.message.embeds else discord.Embed(title="Bewerbung")
        embed.color = discord.Color.green() if self.decision == "approve" else discord.Color.red()
        embed.description = "Bewerber: <@{}>\nStatus: {}\nBearbeitet von: {}".format(row["user_id"], "✅ Angenommen" if self.decision == "approve" else "❌ Abgelehnt", interaction.user.mention)
        await interaction.response.edit_message(embed=embed, view=None)
        applicant = interaction.guild.get_member(row["user_id"])
        if applicant:
            try:
                await applicant.send("Deine IronWolf-Fraktionsbewerbung auf {} wurde {}.".format(interaction.guild.name, "angenommen" if self.decision == "approve" else "abgelehnt"))
            except (discord.Forbidden, discord.HTTPException):
                pass
        log_channel = interaction.guild.get_channel(settings.get("log_channel_id") or 0)
        if isinstance(log_channel, discord.TextChannel):
            log = discord.Embed(title="📋 Bewerbung bearbeitet", description="Bewerber: <@{}>\nEntscheidung: {}\nBearbeiter: {}\nID: {}".format(row["user_id"], row["status"], interaction.user.mention, self.app_id), color=discord.Color.green() if self.decision == "approve" else discord.Color.red(), timestamp=datetime.now(timezone.utc))
            await log_channel.send(embed=log, allowed_mentions=discord.AllowedMentions.none())
        await interaction.followup.send("Entscheidung gespeichert und protokolliert.", ephemeral=True)

class Applications(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        for guild_id in CONFIG.get("guilds", {}):
            try:
                self.bot.add_view(ApplicationPanelView(self, int(guild_id)))
            except (TypeError, ValueError):
                pass
        for app_id, row in RECORDS.get("applications", {}).items():
            if row.get("status") == "pending":
                self.bot.add_view(ApplicationReviewView(self, app_id))

    @app_commands.command(name="bewerbung-panel", description="Erstellt ein anpassbares Bewerbungs-Panel")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(
        kanal="Kanal für das öffentliche Bewerbungs-Panel",
        review_kanal="Privater Kanal für eingehende Bewerbungen",
        log_kanal="Kanal für Entscheidungsprotokolle",
        titel="Titel des Panels",
        beschreibung="Beschreibung des Panels",
        frage1="Erste Formularfrage (max. 45 Zeichen)",
        frage2="Zweite Formularfrage (optional)",
        frage3="Dritte Formularfrage (optional)",
        frage4="Vierte Formularfrage (optional)",
        frage5="Fünfte Formularfrage (optional)",
        review_rolle="Optionale Rolle, die Bewerbungen prüfen darf",
    )
    async def application_panel(
        self, interaction: discord.Interaction, kanal: discord.TextChannel,
        review_kanal: discord.TextChannel, log_kanal: discord.TextChannel,
        titel: str = "🐺 IronWolf – Fraktionsbewerbungen",
        beschreibung: str = "Du möchtest Teil unseres Teams werden? Klicke auf den Button und fülle das Formular aus.",
        frage1: str = None, frage2: str = None, frage3: str = None,
        frage4: str = None, frage5: str = None, review_rolle: discord.Role = None
    ):
        if not interaction.guild:
            await interaction.response.send_message("Nur auf einem Server verfügbar.", ephemeral=True)
            return
        questions = [q.strip() for q in (frage1, frage2, frage3, frage4, frage5) if q and q.strip()]
        if not questions:
            questions = ["Wie lautet dein RP-Name?", "Wie lautet dein Roblox-Name?", "Wie alt bist du (OOC)?", "Welche Erfahrung bringst du mit?", "Warum möchtest du beitreten?"]
        if any(len(question) > 45 for question in questions):
            await interaction.response.send_message("Jede Frage darf höchstens 45 Zeichen lang sein. Bitte kürze die Fragen.", ephemeral=True)
            return
        gid = interaction.guild.id
        guild_settings(gid).update({
            "panel_channel_id": kanal.id, "review_channel_id": review_kanal.id,
            "log_channel_id": log_kanal.id, "review_role_id": review_rolle.id if review_rolle else None,
            "panel_title": titel[:256], "panel_description": beschreibung[:4000], "questions": questions
        })
        save_config()
        embed = discord.Embed(title=titel[:256], description=beschreibung[:4000], color=discord.Color.dark_blue())
        embed.set_footer(text="IronWolf RP • Bewerbungen")
        self.bot.add_view(ApplicationPanelView(self, gid))
        await kanal.send(embed=embed, view=ApplicationPanelView(self, gid))
        await interaction.response.send_message(
            "✅ Angepasstes Bewerbungs-Panel in {} erstellt. {} Fragen gespeichert. Prüfkanal: {} · Logkanal: {}".format(kanal.mention, len(questions), review_kanal.mention, log_kanal.mention),
            ephemeral=True
        )

async def setup(bot):
    await bot.add_cog(Applications(bot))
