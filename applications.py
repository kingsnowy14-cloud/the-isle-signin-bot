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
    rp_name = discord.ui.TextInput(label="RP Name", placeholder="Vorname Nachname", max_length=80)
    roblox_name = discord.ui.TextInput(label="Roblox Username", placeholder="Your Roblox username", max_length=80)
    age = discord.ui.TextInput(label="Age (OOC)", placeholder="z. B. 16", max_length=3)
    experience = discord.ui.TextInput(label="RP / faction experience", placeholder="What experience can you bring?", style=discord.TextStyle.paragraph, max_length=700)
    motivation = discord.ui.TextInput(label="Why would you like to join?", placeholder="Your motivation and strengths", style=discord.TextStyle.paragraph, max_length=1000)

    def __init__(self, cog, guild_id):
        super().__init__(title="IronWolf – Application", timeout=300)
        self.cog, self.guild_id = cog, guild_id
        if guild_id == 1561027077984227371:
            self.rp_name.label, self.rp_name.placeholder = "RP Name", "First and last name"
            self.roblox_name.label, self.roblox_name.placeholder = "Roblox Username", "Your Roblox username"
            self.age.label, self.age.placeholder = "Age (OOC)", "e.g. 16"
            self.experience.label, self.experience.placeholder = "RP / Faction Experience", "What experience do you have?"
            self.motivation.label, self.motivation.placeholder = "Why do you want to join?", "Your motivation and strengths"

    async def on_submit(self, interaction):
        if not interaction.guild or interaction.guild.id != self.guild_id:
            await interaction.response.send_message("This form belongs to a different server.", ephemeral=True)
            return
        if any(row.get("guild_id") == self.guild_id and row.get("user_id") == interaction.user.id and row.get("status") == "pending" for row in RECORDS.setdefault("applications", {}).values()):
            await interaction.response.send_message("You already have an open application. Please wait for the team to review it.", ephemeral=True)
            return
        settings = guild_settings(self.guild_id)
        channel = interaction.guild.get_channel(settings.get("review_channel_id") or 0)
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("The application log is not configured. Please notify the team.", ephemeral=True)
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
        english = True
        embed = discord.Embed(title="📨 New Application" if english else "📨 Neue Application", description=("Applicant: <@{}>\nStatus: ⏳ Pending" if english else "Applicant: <@{}>\nStatus: ⏳ Pending").format(interaction.user.id), color=discord.Color.blurple(), timestamp=datetime.now(timezone.utc))
        embed.add_field(name="RP Name" if english else "RP Name", value=safe_text(row["rp_name"], 200), inline=True)
        embed.add_field(name="Roblox Username" if english else "Roblox Username", value=safe_text(row["roblox_name"], 200), inline=True)
        embed.add_field(name="Age (OOC)" if english else "Age (OOC)", value=safe_text(row["age"], 30), inline=True)
        embed.add_field(name="Experience" if english else "Experience", value=safe_text(row["experience"]), inline=False)
        embed.add_field(name="Motivation", value=safe_text(row["motivation"]), inline=False)
        embed.set_footer(text=("Application ID: " if english else "Application ID: ") + app_id)
        try:
            message = await channel.send(embed=embed, view=ApplicationReviewView(self.cog, app_id), allowed_mentions=discord.AllowedMentions.none())
            row["review_message_id"] = message.id
            save_records()
        except (discord.Forbidden, discord.HTTPException):
            RECORDS["applications"].pop(app_id, None)
            save_records()
            await interaction.response.send_message("Your application could not be sent to the team. Please contact an administrator.", ephemeral=True)
            return
        await interaction.response.send_message("✅ Your application has been submitted. The team will review it and get back to you.", ephemeral=True)

class DashboardApplicationModal(discord.ui.Modal):
    def __init__(self, cog, guild_id, panel_id, panel):
        super().__init__(title=str(panel.get("title") or "Application")[:45], timeout=300)
        self.cog, self.guild_id, self.panel_id, self.panel = cog, guild_id, str(panel_id), panel
        self.fields = []
        for index, question in enumerate(panel.get("questions", [])[:10]):
            field = discord.ui.TextInput(label=str(question)[:45], style=discord.TextStyle.paragraph, max_length=1000, required=True)
            self.fields.append((str(question)[:200], field))
            self.add_item(field)

    async def on_submit(self, interaction):
        if not interaction.guild or interaction.guild.id != self.guild_id:
            await interaction.response.send_message("Please use this panel on the correct server.", ephemeral=True)
            return
        if any(row.get("guild_id") == self.guild_id and row.get("user_id") == interaction.user.id and row.get("status") == "pending" and row.get("panel_id") == self.panel_id for row in RECORDS.setdefault("applications", {}).values()):
            await interaction.response.send_message("You already have a pending application for this panel.", ephemeral=True)
            return
        channel = interaction.guild.get_channel(int(self.panel.get("review_channel_id") or 0))
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("The application review channel is not configured.", ephemeral=True)
            return
        app_id = "{}-{}-{}".format(self.guild_id, interaction.user.id, int(datetime.now(timezone.utc).timestamp()))
        answers = [{"question": question, "answer": str(field.value).strip()} for question, field in self.fields]
        row = {
            "id": app_id, "guild_id": self.guild_id, "panel_id": self.panel_id, "panel_name": self.panel.get("panel_name", "Bewerbung"),
            "user_id": interaction.user.id, "rp_name": "—", "roblox_name": "—", "age": "—",
            "experience": "—", "motivation": "—", "answers": answers, "status": "pending",
            "created_at": datetime.now(timezone.utc).isoformat(), "reviewed_by": None
        }
        RECORDS["applications"][app_id] = row
        save_records()
        embed = discord.Embed(title="📨 New Application · " + str(row["panel_name"])[:200], description="Applicant: <@{}>\nStatus: ⏳ Pending".format(interaction.user.id), color=discord.Color.blurple(), timestamp=datetime.now(timezone.utc))
        for question, answer in answers:
            embed.add_field(name=safe_text(question, 250), value=safe_text(answer, 1000) or "—", inline=False)
        embed.set_footer(text="Application ID: " + app_id)
        try:
            message = await channel.send(embed=embed, view=ApplicationReviewView(self.cog, app_id), allowed_mentions=discord.AllowedMentions.none())
            row["review_message_id"] = message.id
            save_records()
        except (discord.Forbidden, discord.HTTPException):
            RECORDS["applications"].pop(app_id, None)
            save_records()
            await interaction.response.send_message("Your application could not be sent to the team.", ephemeral=True)
            return
        await interaction.response.send_message("✅ Your application has been submitted.", ephemeral=True)


class ApplicationPanelView(discord.ui.View):
    def __init__(self, cog, guild_id, panel_id=None, button_label=None):
        super().__init__(timeout=None)
        settings = guild_settings(guild_id)
        if panel_id and isinstance(settings.get("panels"), dict):
            panel = settings["panels"].get(str(panel_id), {})
            saved_label = button_label or panel.get("button_label")
        else:
            saved_label = button_label or settings.get("button_label")
        self.add_item(ApplicationStartButton(cog, guild_id, saved_label, panel_id=panel_id))

class ApplicationStartButton(discord.ui.Button):
    def __init__(self, cog, guild_id, button_label=None, panel_id=None):
        default_label = "Start application"
        label = str(button_label or default_label).strip()[:80] or default_label
        super().__init__(label=label, emoji="📝", style=discord.ButtonStyle.success, custom_id="ironwolf:application:start:{}:{}".format(guild_id, panel_id or "default"))
        self.cog, self.guild_id, self.panel_id = cog, guild_id, panel_id
    async def callback(self, interaction):
        if not interaction.guild or interaction.guild.id != self.guild_id:
            await interaction.response.send_message("Please use this panel on the correct server." if True else "Bitte nutze das Panel auf dem richtigen Server.", ephemeral=True)
            return
        if self.panel_id:
            panel = guild_settings(self.guild_id).get("panels", {}).get(str(self.panel_id), {})
            await interaction.response.send_modal(DashboardApplicationModal(self.cog, self.guild_id, self.panel_id, panel))
        else:
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
        english = True
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Only server team members can review applications." if english else "Only server team members can review applications.", ephemeral=True)
            return
        settings = guild_settings(interaction.guild.id)
        role_id = settings.get("review_role_id")
        allowed_role = role_id and any(role.id == role_id for role in interaction.user.roles)
        if not (interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.manage_guild or allowed_role):
            await interaction.response.send_message("You do not have permission to review applications." if english else "You do not have permission to review applications.", ephemeral=True)
            return
        row = RECORDS.setdefault("applications", {}).get(self.app_id)
        if not row or row.get("guild_id") != interaction.guild.id:
            await interaction.response.send_message("Application not found." if english else "Application not found.", ephemeral=True)
            return
        if row.get("status") != "pending":
            await interaction.response.send_message("This application has already been reviewed." if english else "This application has already been reviewed.", ephemeral=True)
            return
        row["status"] = "accepted" if self.decision == "approve" else "rejected"
        row["reviewed_by"] = interaction.user.id
        row["reviewed_at"] = datetime.now(timezone.utc).isoformat()
        save_records()
        embed = interaction.message.embeds[0] if interaction.message and interaction.message.embeds else discord.Embed(title="Application" if english else "Bewerbung")
        embed.color = discord.Color.green() if self.decision == "approve" else discord.Color.red()
        embed.description = ("Applicant: <@{}>\nStatus: {}\nReviewed by: {}" if english else "Applicant: <@{}>\nStatus: {}\nBearbeitet von: {}").format(row["user_id"], ("✅ Accepted" if self.decision == "approve" else "❌ Rejected") if english else ("✅ Angenommen" if self.decision == "approve" else "❌ Abgelehnt"), interaction.user.mention)
        await interaction.response.edit_message(embed=embed, view=None)
        applicant = interaction.guild.get_member(row["user_id"])
        if applicant:
            try:
                await applicant.send(("Your IronWolf application on {} was {}.".format(interaction.guild.name, "accepted" if self.decision == "approve" else "rejected")) if english else ("Deine IronWolf-Application auf {} wurde {}.".format(interaction.guild.name, "angenommen" if self.decision == "approve" else "abgelehnt")) )
            except (discord.Forbidden, discord.HTTPException):
                pass
        log_channel = interaction.guild.get_channel(settings.get("log_channel_id") or 0)
        if isinstance(log_channel, discord.TextChannel):
            log = discord.Embed(title="📋 Application Reviewed" if english else "📋 Application reviewed", description=("Applicant: <@{}>\nDecision: {}\nReviewer: {}\nID: {}" if english else "Applicant: <@{}>\nEntscheidung: {}\nBearbeiter: {}\nID: {}").format(row["user_id"], row["status"], interaction.user.mention, self.app_id), color=discord.Color.green() if self.decision == "approve" else discord.Color.red(), timestamp=datetime.now(timezone.utc))
            await log_channel.send(embed=log, allowed_mentions=discord.AllowedMentions.none())
        await interaction.followup.send("Decision saved and logged." if english else "Decision saved and logged.", ephemeral=True)

class Applications(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def dashboard_create_panel(self, guild_id, panel_channel_id, review_channel_id, log_channel_id, title, description, questions, review_role_id=None, ping_role_id=None, panel_name="Application", button_label=None):
        """Create and persist a dashboard-managed application panel.

        Keep panel_name and button_label optional so older slash-command callers
        remain compatible while the dashboard can customize each panel.
        """
        settings = guild_settings(guild_id)
        panels = settings.setdefault("panels", {})
        panel_id = str(int(datetime.now(timezone.utc).timestamp() * 1000))
        default_button_label = "Start application"
        label = str(button_label or default_button_label).strip()[:80] or default_button_label
        panel = {
            "panel_id": panel_id, "panel_name": str(panel_name)[:80], "title": str(title)[:256],
            "description": str(description)[:4000], "questions": [str(q)[:200] for q in questions[:10]],
            "panel_channel_id": int(panel_channel_id), "review_channel_id": int(review_channel_id),
            "log_channel_id": int(log_channel_id), "review_role_id": review_role_id,
            "ping_role_id": ping_role_id, "button_label": label
        }
        panels[panel_id] = panel
        save_config()
        guild = self.bot.get_guild(int(guild_id))
        channel = guild.get_channel(int(panel_channel_id)) if guild else None
        if not isinstance(channel, discord.TextChannel):
            panels.pop(panel_id, None)
            save_config()
            raise ValueError("The selected application panel channel is unavailable.")
        embed = discord.Embed(title=panel["title"], description=panel["description"], color=discord.Color.dark_blue())
        embed.set_footer(text="IronWolf RP • Applications")
        view = ApplicationPanelView(self, guild_id, panel_id=panel_id, button_label=label)
        message = await channel.send(embed=embed, view=view, allowed_mentions=discord.AllowedMentions.none())
        panel["message_id"] = message.id
        save_config()
        return "Panel veröffentlicht"

    async def cog_load(self):
        for guild_id, settings in CONFIG.get("guilds", {}).items():
            try:
                self.bot.add_view(ApplicationPanelView(self, int(guild_id)))
                for panel_id, panel in settings.get("panels", {}).items():
                    self.bot.add_view(ApplicationPanelView(self, int(guild_id), panel_id=panel_id, button_label=panel.get("button_label")))
            except (TypeError, ValueError):
                pass
        for app_id, row in RECORDS.get("applications", {}).items():
            if row.get("status") == "pending":
                self.bot.add_view(ApplicationReviewView(self, app_id))

    @app_commands.command(name="application-panel", description="Creates a persistent application panel")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(kanal="Channel for the application panel", review_kanal="Channel for incoming applications", log_kanal="Channel for decision logs", review_rolle="Optional role for application reviewers", button_name="Custom name for the application button (max. 80 characters)")
    async def application_panel(self, interaction: discord.Interaction, kanal: discord.TextChannel, review_kanal: discord.TextChannel, log_kanal: discord.TextChannel, review_rolle: discord.Role = None, button_name: str = None):
        if not interaction.guild:
            await interaction.response.send_message("Nur auf einem Server verfügbar.", ephemeral=True)
            return
        gid = interaction.guild.id
        guild_settings(gid).update({"panel_channel_id": kanal.id, "review_channel_id": review_kanal.id, "log_channel_id": log_kanal.id, "review_role_id": review_rolle.id if review_rolle else None, "button_label": (str(button_name).strip()[:80] if button_name and str(button_name).strip() else None)})
        save_config()
        english = gid == 1561027077984227371
        embed = discord.Embed(title="🐺 IronWolf – Applications" if english else "🐺 IronWolf – Applicationen", description=("Would you like to join our team?\n\nClick **Apply Now** and complete the form. Your answers will be sent confidentially to the team. You can only have one pending application at a time." if english else "Du möchtest Teil unseres Teams werden?\n\nKlicke auf **Apply now** und fülle das Formular vollständig aus. Deine Angaben werden vertraulich an das zuständige Team übermittelt. Du kannst immer nur eine offene Bewerbung gleichzeitig haben."), color=discord.Color.dark_blue())
        embed.set_footer(text="IronWolf RP • Applications" if english else "IronWolf RP • Applications")
        self.bot.add_view(ApplicationPanelView(self, gid))
        await kanal.send(embed=embed, view=ApplicationPanelView(self, gid))
        await interaction.response.send_message(("✅ Application panel created in {}. Button: **{}**. Review channel: {} · Log channel: {}".format(kanal.mention, guild_settings(gid).get("button_label") or ("Apply Now" if english else "Apply now"), review_kanal.mention, log_kanal.mention)) if english else ("✅ Bewerbungs-Panel in {} erstellt. Button: **{}**. Prüfkanal: {} · Logkanal: {}".format(kanal.mention, guild_settings(gid).get("button_label") or "Apply now", review_kanal.mention, log_kanal.mention)), ephemeral=True)

async def setup(bot):
    await bot.add_cog(Applications(bot))
