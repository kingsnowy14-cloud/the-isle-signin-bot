import asyncio
import json
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands

from storage import data_path

STATE_PATH = data_path("absence_records.json")


def _read_state():
    try:
        value = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {"guilds": {}}
    except (OSError, json.JSONDecodeError):
        return {"guilds": {}}


STATE = _read_state()


def _save_state():
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(STATE, ensure_ascii=False, indent=2), encoding="utf-8")


def _parse_date(value):
    parsed = datetime.strptime(value.strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return parsed


class AbsenceRequestModal(discord.ui.Modal):
    def __init__(self, cog, guild_id):
        super().__init__(title="Absence request")
        self.cog = cog
        self.guild_id = int(guild_id)
        self.reason = discord.ui.TextInput(label="Reason", placeholder="Enter the reason for your absence", max_length=500, required=True)
        self.start_date = discord.ui.TextInput(label="Start date (YYYY-MM-DD)", placeholder="2026-11-07", max_length=10, required=True)
        self.end_date = discord.ui.TextInput(label="End date (YYYY-MM-DD)", placeholder="2026-11-14", max_length=10, required=True)
        self.add_item(self.reason)
        self.add_item(self.start_date)
        self.add_item(self.end_date)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            start = _parse_date(str(self.start_date.value))
            end = _parse_date(str(self.end_date.value))
            if end <= start or end <= datetime.now(timezone.utc):
                await interaction.response.send_message("The end date must be after the start date and in the future.", ephemeral=True)
                return
        except (ValueError, TypeError):
            await interaction.response.send_message("Enter valid start and end dates in YYYY-MM-DD format.", ephemeral=True)
            return
        await self.cog.submit_absence(interaction, str(self.reason.value).strip(), start, end)

class AbsencePanelView(discord.ui.View):
    def __init__(self, cog, guild_id):
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = int(guild_id)

    @discord.ui.button(
        label="Sign out / Report absence",
        style=discord.ButtonStyle.primary,
        custom_id="ironwolf:absence:request",
        emoji="📅",
    )
    async def request_absence(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild is None or interaction.guild.id != self.guild_id:
            await interaction.response.send_message("This panel belongs to another server.", ephemeral=True)
            return
        await interaction.response.send_modal(AbsenceRequestModal(self.cog, self.guild_id))
    @discord.ui.button(label="I'm back / Sign in", style=discord.ButtonStyle.success, custom_id="ironwolf:absence:sign-in", emoji="✅")
    async def sign_in(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild is None or interaction.guild.id != self.guild_id:
            await interaction.response.send_message("This panel belongs to another server.", ephemeral=True)
            return
        rows = STATE.setdefault("guilds", {}).setdefault(str(self.guild_id), [])
        record = next((item for item in reversed(rows) if str(item.get("user_id")) == str(interaction.user.id) and item.get("status") == "active"), None)
        if record is None:
            await interaction.response.send_message("You do not have an active absence.", ephemeral=True)
            return
        role_id = record.get("role_id")
        role = interaction.guild.get_role(int(role_id)) if str(role_id or "").isdigit() else None
        try:
            if role and role in interaction.user.roles:
                await interaction.user.remove_roles(role, reason="Member signed back in using Absence Management")
            record["status"] = "completed"
            record["completed_at"] = datetime.now(timezone.utc).isoformat()
            record["completed_early"] = True
            _save_state()
            await self.cog._log(interaction.guild, self.cog.settings(interaction.guild.id), "Member signed back in",
                f"Member: {interaction.user.mention} ({interaction.user.id})\nThe member marked themselves as active again and the absence role was removed.", discord.Color.green())
            await interaction.response.send_message("You are marked as active again. Your absence role has been removed.", ephemeral=True)
        except (discord.Forbidden, discord.HTTPException):
            await interaction.response.send_message("I could not remove your absence role. Please contact staff.", ephemeral=True)


class AbsenceExtendSelect(discord.ui.Select):
    def __init__(self, cog, guild_id, record_id):
        options = [discord.SelectOption(label=f"{days} day(s)", value=str(days)) for days in range(1, 21)]
        super().__init__(placeholder="Choose 1–20 additional days", min_values=1, max_values=1, options=options,
                         custom_id=f"ironwolf:absence:extend-days:{int(guild_id)}:{record_id}")
        self.cog = cog
        self.guild_id = int(guild_id)
        self.record_id = str(record_id)

    async def callback(self, interaction: discord.Interaction):
        rows = STATE.setdefault("guilds", {}).setdefault(str(self.guild_id), [])
        record = next((item for item in rows if str(item.get("record_id")) == self.record_id
                       and str(item.get("user_id")) == str(interaction.user.id) and item.get("status") == "active"), None)
        if record is None:
            await interaction.response.send_message("No active absence was found for you.", ephemeral=True)
            return
        try:
            previous_end = datetime.fromisoformat(record["end_at"])
            if previous_end.tzinfo is None:
                previous_end = previous_end.replace(tzinfo=timezone.utc)
            days = int(self.values[0])
            new_end = previous_end + timedelta(days=days)
            record["end_at"] = new_end.isoformat()
            record["duration_days"] = int(record.get("duration_days", 0)) + days
            record["reminder_sent"] = False
            record["extended_at"] = datetime.now(timezone.utc).isoformat()
            _save_state()
            guild = self.cog.bot.get_guild(self.guild_id)
            if guild:
                await self.cog._log(guild, self.cog.settings(guild.id), "Absence extended",
                    f"Member: {interaction.user.mention} ({interaction.user.id})\nAdditional time: {days} day(s)\nNew end date: <t:{int(new_end.timestamp())}:F>")
            await interaction.response.edit_message(content=f"Your absence was extended by {days} day(s), until <t:{int(new_end.timestamp())}:F>.", view=None)
        except (KeyError, TypeError, ValueError):
            await interaction.response.send_message("I could not update your absence. Please contact staff.", ephemeral=True)

class AbsenceExtendView(discord.ui.View):
    def __init__(self, cog, guild_id, record_id):
        super().__init__(timeout=300)
        self.add_item(AbsenceExtendSelect(cog, guild_id, record_id))

class AbsenceExtendButton(discord.ui.Button):
    def __init__(self, cog, guild_id, record_id):
        super().__init__(label="Extend my absence", style=discord.ButtonStyle.primary,
                         custom_id=f"ironwolf:absence:extend:{int(guild_id)}:{record_id}", emoji="🗓️")
        self.cog = cog
        self.guild_id = int(guild_id)
        self.record_id = str(record_id)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_message("Choose how many additional days you need (1–20):",
            view=AbsenceExtendView(self.cog, self.guild_id, self.record_id), ephemeral=True)

class AbsenceExtensionView(discord.ui.View):
    def __init__(self, cog, guild_id, record_id):
        super().__init__(timeout=None)
        self.add_item(AbsenceExtendButton(cog, guild_id, record_id))

class AbsenceManagement(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.worker_task = None

    def settings(self, guild_id):
        return self.bot.config.setdefault(str(guild_id), {}).get("absence_system", {})

    async def publish_panel(self, guild):
        settings = self.settings(guild.id)
        channel_id = settings.get("panel_channel_id")
        channel = guild.get_channel(int(channel_id)) if str(channel_id or "").isdigit() else None
        if channel is None:
            raise ValueError("Select a valid absence panel channel in the dashboard.")
        embed = discord.Embed(
            title=str(settings.get("panel_title") or "Absence Registration")[:256],
            description=str(settings.get("panel_description") or "Use the button below to report your absence.")[:4000],
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="IronWolf · Absence Management")
        message = await channel.send(embed=embed, view=AbsencePanelView(self, guild.id))
        settings["panel_message_id"] = message.id
        settings["panel_channel_id"] = channel.id
        self.bot.save_json(self.bot.CONFIG_FILE, self.bot.config)
        return message

    async def _ensure_absence_role(self, guild, settings):
        role_id = settings.get("absence_role_id")
        role = guild.get_role(int(role_id)) if str(role_id or "").isdigit() else None
        if role is not None:
            return role
        me = guild.me
        if me is None or not me.guild_permissions.manage_roles:
            raise ValueError("IronWolf needs Manage Roles permission to create the absence role.")
        staff_id = settings.get("lowest_staff_role_id")
        staff_role = guild.get_role(int(staff_id)) if str(staff_id or "").isdigit() else None
        if staff_role is None:
            raise ValueError("Select the lowest staff role in the dashboard before using automatic role creation.")
        if staff_role >= me.top_role:
            raise ValueError("IronWolf's highest role must be above the lowest staff role.")
        role = await guild.create_role(
            name="Absent",
            reason="IronWolf Absence Management: create default absence role",
            mentionable=False,
            hoist=True,
        )
        await role.edit(name="Absent", hoist=True, position=max(1, staff_role.position - 1), reason="Place Absent role below the lowest staff role")
        settings["absence_role_id"] = role.id
        self.bot.save_json(self.bot.CONFIG_FILE, self.bot.config)
        return role

    async def _log(self, guild, settings, title, description, color=discord.Color.blurple()):
        channel_id = settings.get("log_channel_id")
        channel = guild.get_channel(int(channel_id)) if str(channel_id or "").isdigit() else None
        if channel is None:
            print(f"[ABSENCE] No valid log channel configured for guild {guild.id}.", flush=True)
            return
        await channel.send(embed=discord.Embed(title=title, description=description[:4000], color=color))

    async def submit_absence(self, interaction, reason, start, end, extension_record_id=None):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("This action is only available inside the server.", ephemeral=True)
            return
        settings = self.settings(guild.id)
        try:
            role = await self._ensure_absence_role(guild, settings)
            if extension_record_id:
                rows = STATE.setdefault("guilds", {}).setdefault(str(guild.id), [])
                record = next((r for r in rows if str(r.get("record_id")) == extension_record_id
                               and str(r.get("user_id")) == str(interaction.user.id)
                               and r.get("status") == "active"), None)
                if record is None:
                    raise ValueError("Your active absence could not be found.")
                previous_end = datetime.fromisoformat(record["end_at"])
                record["end_at"] = end.isoformat()
                record["start_at"] = min(start, previous_end).isoformat()
                record["reason"] = reason
                record["reminder_sent"] = False
                record["extended_at"] = datetime.now(timezone.utc).isoformat()
                record["duration_days"] = max(1, min(20, (end - start).days))
                await self._log(
                    guild, settings, "Absence extended",
                    f"Member: {interaction.user.mention} (`{interaction.user.id}`)\n"
                    f"Reason: {discord.utils.escape_markdown(reason)}\n"
                    f"New end: <t:{int(end.timestamp())}:F>\nDuration: {(end-start).days} day(s)",
                )
                _save_state()
                await interaction.response.send_message("Your absence has been extended and logged.", ephemeral=True)
                return

            existing = next(
                (r for r in reversed(STATE.setdefault("guilds", {}).setdefault(str(guild.id), []))
                 if str(r.get("user_id")) == str(interaction.user.id) and r.get("status") == "active"),
                None,
            )
            if existing:
                raise ValueError("You already have an active absence. Use the extension button in your reminder DM.")
            if role not in interaction.user.roles:
                await interaction.user.add_roles(role, reason="IronWolf Absence Management")
            record_id = f"{guild.id}-{interaction.user.id}-{int(datetime.now(timezone.utc).timestamp())}"
            row = {
                "record_id": record_id,
                "user_id": str(interaction.user.id),
                "reason": reason,
                "start_at": start.isoformat(),
                "end_at": end.isoformat(),
                "duration_days": (end - start).days,
                "status": "active",
                "reminder_sent": False,
                "role_id": str(role.id),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            STATE["guilds"][str(guild.id)].append(row)
            _save_state()
            await self._log(
                guild, settings, "Member signed out",
                f"Member: {interaction.user.mention} (`{interaction.user.id}`)\n"
                f"Reason: {discord.utils.escape_markdown(reason)}\n"
                f"From: <t:{int(start.timestamp())}:F>\nUntil: <t:{int(end.timestamp())}:F>\n"
                f"Duration: {(end-start).days} day(s)\nRole: {role.mention}",
                discord.Color.orange(),
            )
            await interaction.response.send_message(
                f"Your absence has been registered until <t:{int(end.timestamp())}:F>.",
                ephemeral=True,
            )
        except (ValueError, discord.Forbidden, discord.HTTPException) as exc:
            message = str(exc) if isinstance(exc, ValueError) else "I could not assign the absence role. Check my role position and permissions."
            if not interaction.response.is_done():
                await interaction.response.send_message(message, ephemeral=True)
            else:
                await interaction.followup.send(message, ephemeral=True)

    async def expiry_worker(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            now = datetime.now(timezone.utc)
            for guild_id, rows in list(STATE.setdefault("guilds", {}).items()):
                guild = self.bot.get_guild(int(guild_id)) if str(guild_id).isdigit() else None
                if guild is None:
                    continue
                settings = self.settings(guild.id)
                for row in rows:
                    if row.get("status") != "active":
                        continue
                    try:
                        expires = datetime.fromisoformat(row["end_at"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    if expires.tzinfo is None:
                        expires = expires.replace(tzinfo=timezone.utc)
                    user_id = int(row.get("user_id", 0))
                    member = guild.get_member(user_id)
                    if member is None:
                        try:
                            member = await guild.fetch_member(user_id)
                        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                            member = None
                    if not row.get("reminder_sent") and timedelta(0) < expires - now <= timedelta(days=1):
                        if member:
                            try:
                                await member.send(
                                    f"Your absence on **{guild.name}** ends tomorrow. "
                                    "If you need more time, use the button below to choose another 1–20 days.",
                                    view=AbsenceExtensionView(self, guild.id, str(row.get("record_id"))),
                                )
                            except (discord.Forbidden, discord.HTTPException):
                                pass
                        row["reminder_sent"] = True
                        _save_state()
                    if expires <= now:
                        role_id = row.get("role_id")
                        role = guild.get_role(int(role_id)) if str(role_id or "").isdigit() else None
                        if member and role and role in member.roles:
                            try:
                                await member.remove_roles(role, reason="IronWolf Absence Management: absence period ended")
                            except (discord.Forbidden, discord.HTTPException):
                                continue
                        row["status"] = "completed"
                        row["completed_at"] = now.isoformat()
                        _save_state()
                        if member:
                            try:
                                await member.send(f"Your absence on **{guild.name}** has ended. You are active again.")
                            except (discord.Forbidden, discord.HTTPException):
                                pass
                        await self._log(
                            guild, settings, "Member active again",
                            f"Member: <@{user_id}> (`{user_id}`)\nAbsence ended: <t:{int(expires.timestamp())}:F>",
                            discord.Color.green(),
                        )
            await asyncio.sleep(300)

    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds:
            settings = self.settings(guild.id)
            panel_channel_id = settings.get("panel_channel_id")
            message_id = settings.get("panel_message_id")
            if str(panel_channel_id or "").isdigit():
                try:
                    self.bot.add_view(AbsencePanelView(self, guild.id), message_id=int(message_id) if message_id else None)
                    for active_row in STATE.setdefault("guilds", {}).get(str(guild.id), []):
                        if active_row.get("status") == "active":
                            self.bot.add_view(AbsenceExtensionView(self, guild.id, str(active_row.get("record_id"))))
                except Exception as exc:
                    print(f"[ABSENCE] Could not restore persistent views for guild {guild.id}: {type(exc).__name__}: {exc}", flush=True)
        if self.worker_task is None or self.worker_task.done():
            self.worker_task = asyncio.create_task(self.expiry_worker(), name="absence-expiry-worker")


async def setup(bot):
    cog = AbsenceManagement(bot)
    await bot.add_cog(cog)
    for guild in bot.guilds:
        settings = cog.settings(guild.id)
        panel_channel_id = settings.get("panel_channel_id")
        message_id = settings.get("panel_message_id")
        if str(panel_channel_id or "").isdigit():
            try:
                bot.add_view(AbsencePanelView(cog, guild.id), message_id=int(message_id) if message_id else None)
                for active_row in STATE.setdefault("guilds", {}).get(str(guild.id), []):
                    if active_row.get("status") == "active":
                        bot.add_view(AbsenceExtensionView(cog, guild.id, str(active_row.get("record_id"))))
            except Exception as exc:
                print(f"[ABSENCE] Could not restore views for guild {guild.id}: {type(exc).__name__}: {exc}", flush=True)
    cog.worker_task = asyncio.create_task(cog.expiry_worker(), name="absence-expiry-worker")
    print("[ABSENCE] Absence Management loaded.", flush=True)
