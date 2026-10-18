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

class ApplicationModal(discord.ui.Modal, title="IronWolf – Application"):
    rp_name = discord.ui.TextInput(label="RP-Name", placeholder="Vorname Nachname", max_length=80)
    roblox_name = discord.ui.TextInput(label="Roblox-Name", placeholder="Dein Roblox-Benutzername", max_length=80)
    age = discord.ui.TextInput(label="Alter (OOC)", placeholder="z. B. 16", max_length=3)
    experience = discord.ui.TextInput(label="RP-/Fraktionserfahrung", placeholder="Welche Erfahrungen bringst du mit?", style=discord.TextStyle.paragraph, max_length=700)
    motivation = discord.ui.TextInput(label="Warum möchtest du beitreten?", placeholder="Motivation und Stärken", style=discord.TextStyle.paragraph, max_length=1000)

    def __init__(self, cog, guild_id):
        super().__init__(title="IronWolf – Application" if guild_id == 1561027077984227371 else "IronWolf – Fraktionsbewerbung", timeout=300)
        self.cog, self.guild_id = cog, guild_id
        if guild_id == 1561027077984227371:
            self.rp_name.label, self.rp_name.placeholder = "RP Name", "First and last name"
            self.roblox_name.label, self.roblox_name.placeholder = "Roblox Username", "Your Roblox username"
            self.age.label, self.age.placeholder = "Age (OOC)", "e.g. 16"
            self.experience.label, self.experience.placeholder = "RP / Faction Experience", "What experience do you have?"
            self.motivation.label, self.motivation.placeholder = "Why do you want to join?", "Your motivation and strengths"

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
            await interaction.response.send_message("Das Bewerbungs-Log ist nicht eingerichtet. Bitte informiere das Team.", ephemeral=True)
            return
        app_id = "{}-{}-{}".format(self.guild_id, interaction.user.id, int(datetime.now(timezone.utc).timestamp()))
        row = {
            "id": app_id, "guild_id": self.guild_id, "user_id": interaction.user.id,
            "rp_name": str(self.rp_name.value).strip(), "roblox_name": str(self.roblox_name.value).strip(),
            "age": str(self.age.value).strip(), "experience": str(self.experience.value).strip(),
            "motivation": str(self.motivation.value).strip(), "status": "pending",
            "created_at": datetime.now(timezone.utc).isoformat(), "reviewed_by": None
        }
        RECORDS["applications"][app_id] = row
        save_records()
        english = self.guild_id == 1561027077984227371
        embed = discord.Embed(title="📨 New Application" if english else "📨 Neue Fraktionsbewerbung", description=("Applicant: <@{}>\nStatus: ⏳ Pending" if english else "Bewerber: <@{}>\nStatus: ⏳ Offen").format(interaction.user.id), color=discord.Color.blurple(), timestamp=datetime.now(timezone.utc))
        embed.add_field(name="RP Name" if english else "RP-Name", value=safe_text(row["rp_name"], 200), inline=True)
        embed.add_field(name="Roblox Username" if english else "Roblox-Name", value=safe_text(row["roblox_name"], 200), inline=True)
        embed.add_field(name="Age (OOC)" if english else "Alter (OOC)", value=safe_text(row["age"], 30), inline=True)
        embed.add_field(name="Experience" if english else "Erfahrung", value=safe_text(row["experience"]), inline=False)
        embed.add_field(name="Motivation", value=safe_text(row["motivation"]), inline=False)
        embed.set_footer(text=("Application ID: " if english else "Bewerbungs-ID: ") + app_id)
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
        saved_label = guild_settings(guild_id).get("button_label")
        self.add_item(ApplicationStartButton(cog, guild_id, saved_label))

class ApplicationStartButton(discord.ui.Button):
    def __init__(self, cog, guild_id, button_label=None):
        default_label = "Apply Now" if guild_id == 1561027077984227371 else "Jetzt bewerben"
        label = str(button_label or default_label).strip()[:80] or default_label
        super().__init__(label=label, emoji="📝", style=discord.ButtonStyle.success, custom_id="ironwolf:application:start:{}".format(guild_id))
        self.cog, self.guild_id = cog, guild_id
    async def callback(self, interaction):
        if not interaction.guild or interaction.guild.id != self.guild_id:
            await interaction.response.send_message("Please use this panel on the correct server." if self.guild_id == 1561027077984227371 else "Bitte nutze das Panel auf dem richtigen Server.", ephemeral=True)
            return
        await interaction.response.send_modal(ApplicationModal(self.cog, self.guild_id))

class ApplicationReviewView(discord.ui.View):
    def __init__(self, cog, app_id):
        super().__init__(timeout=None)
        self.add_item(ApplicationDecisionButton(cog, app_id, "approve"))
        self.add_item(ApplicationDecisionButton(cog, app_id, "reject"))

class ApplicationDecisionButton(discord.ui.Button):
    def __init__(self, cog, app_id, decision):
        self.cog, self.app_id, self.decision = cog, app_id, decision
        guild_id = int(str(app_id).split("-", 1)[0]) if str(app_id).split("-", 1)[0].isdigit() else None
        english = guild_id == 1561027077984227371
        super().__init__(label=("Accept" if decision == "approve" else "Reject") if english else ("Annehmen" if decision == "approve" else "Ablehnen"), emoji="✅" if decision == "approve" else "❌", style=discord.ButtonStyle.success if decision == "approve" else discord.ButtonStyle.danger, custom_id="ironwolf:application:{}:{}".format(decision, app_id))

    async def callback(self, interaction):
        english = interaction.guild_id == 1561027077984227371
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Only server team members can review applications." if english else "Nur Teammitglieder auf dem Server können Bewerbungen bearbeiten.", ephemeral=True)
            return
        settings = guild_settings(interaction.guild.id)
        role_id = settings.get("review_role_id")
        allowed_role = role_id and any(role.id == role_id for role in interaction.user.roles)
        if not (interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.manage_guild or allowed_role):
            await interaction.response.send_message("You do not have permission to review applications." if english else "Dir fehlt die Berechtigung, Bewerbungen zu bearbeiten.", ephemeral=True)
            return
        row = RECORDS.setdefault("applications", {}).get(self.app_id)
        if not row or row.get("guild_id") != interaction.guild.id:
            await interaction.response.send_message("Application not found." if english else "Bewerbung nicht gefunden.", ephemeral=True)
            return
        if row.get("status") != "pending":
            await interaction.response.send_message("This application has already been reviewed." if english else "Diese Bewerbung wurde bereits bearbeitet.", ephemeral=True)
            return
        row["status"] = "accepted" if self.decision == "approve" else "rejected"
        row["reviewed_by"] = interaction.user.id
        row["reviewed_at"] = datetime.now(timezone.utc).isoformat()
        save_records()
        embed = interaction.message.embeds[0] if interaction.message and interaction.message.embeds else discord.Embed(title="Application" if english else "Bewerbung")
        embed.color = discord.Color.green() if self.decision == "approve" else discord.Color.red()
        embed.description = ("Applicant: <@{}>\nStatus: {}\nReviewed by: {}" if english else "Bewerber: <@{}>\nStatus: {}\nBearbeitet von: {}").format(row["user_id"], ("✅ Accepted" if self.decision == "approve" else "❌ Rejected") if english else ("✅ Angenommen" if self.decision == "approve" else "❌ Abgelehnt"), interaction.user.mention)
        await interaction.response.edit_message(embed=embed, view=None)
        applicant = interaction.guild.get_member(row["user_id"])
        if applicant:
            try:
                await applicant.send(("Your IronWolf application on {} was {}.".format(interaction.guild.name, "accepted" if self.decision == "approve" else "rejected")) if english else ("Deine IronWolf-Fraktionsbewerbung auf {} wurde {}.".format(interaction.guild.name, "angenommen" if self.decision == "approve" else "abgelehnt")) )
            except (discord.Forbidden, discord.HTTPException):
                pass
        log_channel = interaction.guild.get_channel(settings.get("log_channel_id") or 0)
        if isinstance(log_channel, discord.TextChannel):
            log = discord.Embed(title="📋 Application Reviewed" if english else "📋 Bewerbung bearbeitet", description=("Applicant: <@{}>\nDecision: {}\nReviewer: {}\nID: {}" if english else "Bewerber: <@{}>\nEntscheidung: {}\nBearbeiter: {}\nID: {}").format(row["user_id"], row["status"], interaction.user.mention, self.app_id), color=discord.Color.green() if self.decision == "approve" else discord.Color.red(), timestamp=datetime.now(timezone.utc))
            await log_channel.send(embed=log, allowed_mentions=discord.AllowedMentions.none())
        await interaction.followup.send("Decision saved and logged." if english else "Entscheidung gespeichert und protokolliert.", ephemeral=True)

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

    @app_commands.command(name="bewerbung-panel", description="Erstellt ein dauerhaftes Bewerbungs-Panel")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(kanal="Kanal für das Bewerbungs-Panel", review_kanal="Kanal für eingehende Bewerbungen", log_kanal="Kanal für Entscheidungsprotokolle", review_rolle="Optionale Rolle für Bewerbungsprüfer", button_name="Custom name for the application button (max. 80 characters)")
    async def application_panel(self, interaction: discord.Interaction, kanal: discord.TextChannel, review_kanal: discord.TextChannel, log_kanal: discord.TextChannel, review_rolle: discord.Role = None, button_name: str = None):
        if not interaction.guild:
            await interaction.response.send_message("Nur auf einem Server verfügbar.", ephemeral=True)
            return
        gid = interaction.guild.id
        guild_settings(gid).update({"panel_channel_id": kanal.id, "review_channel_id": review_kanal.id, "log_channel_id": log_kanal.id, "review_role_id": review_rolle.id if review_rolle else None, "button_label": (str(button_name).strip()[:80] if button_name and str(button_name).strip() else None)})
        save_config()
        english = gid == 1561027077984227371
        embed = discord.Embed(title="🐺 IronWolf – Applications" if english else "🐺 IronWolf – Fraktionsbewerbungen", description=("Would you like to join our team?\n\nClick **Apply Now** and complete the form. Your answers will be sent confidentially to the team. You can only have one pending application at a time." if english else "Du möchtest Teil unseres Teams werden?\n\nKlicke auf **Jetzt bewerben** und fülle das Formular vollständig aus. Deine Angaben werden vertraulich an das zuständige Team übermittelt. Du kannst immer nur eine offene Bewerbung gleichzeitig haben."), color=discord.Color.dark_blue())
        embed.set_footer(text="IronWolf RP • Applications" if english else "IronWolf RP • Bewerbungen")
        self.bot.add_view(ApplicationPanelView(self, gid))
        await kanal.send(embed=embed, view=ApplicationPanelView(self, gid))
        await interaction.response.send_message(("✅ Application panel created in {}. Button: **{}**. Review channel: {} · Log channel: {}".format(kanal.mention, guild_settings(gid).get("button_label") or ("Apply Now" if english else "Jetzt bewerben"), review_kanal.mention, log_kanal.mention)) if english else ("✅ Bewerbungs-Panel in {} erstellt. Button: **{}**. Prüfkanal: {} · Logkanal: {}".format(kanal.mention, guild_settings(gid).get("button_label") or "Jetzt bewerben", review_kanal.mention, log_kanal.mention)), ephemeral=True)

async def setup(bot):
    await bot.add_cog(Applications(bot))
