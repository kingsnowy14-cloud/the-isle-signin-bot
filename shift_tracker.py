"""Persistent shift tracking panels for IronWolf."""
import asyncio
import json
import secrets
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks
from storage import data_path
from premium import guild_has_feature

STATE_FILE = data_path("shift_tracker.json")


def _now():
    return datetime.now(timezone.utc)


def _stamp(value):
    return value.isoformat()


def _read_state():
    try:
        value = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {"guilds": {}}
    except (OSError, json.JSONDecodeError):
        return {"guilds": {}}


STATE = _read_state()
LOCK = asyncio.Lock()


def _save():
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(STATE, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(STATE_FILE)


def _guild_data(guild_id):
    return STATE.setdefault("guilds", {}).setdefault(str(guild_id), {"panels": {}, "shifts": {}})


def _duration(seconds):
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours} Std. {minutes} Min."
    if minutes:
        return f"{minutes} Min. {secs} Sek."
    return f"{secs} Sek."


def _parse_time(raw):
    try:
        value = datetime.fromisoformat(raw)
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return _now()


class PauseModal(discord.ui.Modal):
    def __init__(self, cog, panel_id, user_id, default_minutes):
        super().__init__(title="Start a break")
        self.cog = cog
        self.panel_id = panel_id
        self.user_id = user_id
        self.minutes = discord.ui.TextInput(
            label="Break duration (minutes)",
            placeholder="z. B. 15",
            default=str(default_minutes),
            min_length=1,
            max_length=4,
        )
        self.add_item(self.minutes)

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This break does not belong to you.", ephemeral=True)
            return
        try:
            minutes = int(str(self.minutes.value))
            if not 1 <= minutes <= 480:
                raise ValueError
        except ValueError:
            await interaction.response.send_message("Enter a duration between 1 and 480 minutes.", ephemeral=True)
            return
        await self.cog.start_pause(interaction, self.panel_id, minutes)


class ShiftPanelView(discord.ui.View):
    def __init__(self, cog, panel_id):
        super().__init__(timeout=None)
        self.cog = cog
        self.panel_id = panel_id
        checkin = discord.ui.Button(label="Start shift", style=discord.ButtonStyle.success, emoji="🟢",
                                    custom_id=f"iwshift:in:{panel_id}")
        pause = discord.ui.Button(label="Break", style=discord.ButtonStyle.primary, emoji="☕",
                                  custom_id=f"iwshift:pause:{panel_id}")
        checkout = discord.ui.Button(label="End shift", style=discord.ButtonStyle.danger, emoji="🔴",
                                     custom_id=f"iwshift:out:{panel_id}")
        checkin.callback = self._checkin
        pause.callback = self._pause
        checkout.callback = self._checkout
        self.add_item(checkin)
        self.add_item(pause)
        self.add_item(checkout)

    async def _checkin(self, interaction):
        await self.cog.checkin(interaction, self.panel_id)

    async def _pause(self, interaction):
        await self.cog.open_pause_modal(interaction, self.panel_id)

    async def _checkout(self, interaction):
        await self.cog.checkout(interaction, self.panel_id, "Manuell")


class PausePromptView(discord.ui.View):
    def __init__(self, cog, panel_id, user_id):
        super().__init__(timeout=None)
        self.cog = cog
        self.panel_id = panel_id
        self.user_id = int(user_id)
        yes = discord.ui.Button(label="I'm back at work", style=discord.ButtonStyle.success,
                                custom_id=f"iwshift:yes:{panel_id}:{user_id}")
        yes.callback = self._yes
        self.add_item(yes)

    async def _yes(self, interaction):
        await self.cog.pause_answer(interaction, self.panel_id, self.user_id, True)

    async def _delay(self, interaction):
        await self.cog.set_warning_delay(interaction, self.panel_id, self.user_id, int(interaction.data["values"][0]))


class ShiftTracker(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.pause_worker.start()

    def cog_unload(self):
        self.pause_worker.cancel()

    async def log(self, guild, panel, message):
        channel = guild.get_channel(int(panel.get("log_channel_id", 0)))
        if not isinstance(channel, discord.TextChannel):
            return
        try:
            await channel.send(message, allowed_mentions=discord.AllowedMentions.none())
        except (discord.Forbidden, discord.HTTPException) as exc:
            print(f"[SHIFT] Log failed in guild {guild.id}: {type(exc).__name__}: {exc}", flush=True)

    def get_shift(self, guild_id, panel_id, user_id):
        return _guild_data(guild_id)["shifts"].get(f"{panel_id}:{user_id}")

    async def checkin(self, interaction, panel_id):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("This action is only available in a server.", ephemeral=True)
            return
        async with LOCK:
            data = _guild_data(guild.id)
            panel = data["panels"].get(panel_id)
            if not panel:
                await interaction.response.send_message("This shift panel no longer exists.", ephemeral=True)
                return
            key = f"{panel_id}:{interaction.user.id}"
            if key in data["shifts"] and data["shifts"][key].get("status") != "ended":
                await interaction.response.send_message("You already have an active shift in this panel.", ephemeral=True)
                return
            data["shifts"][key] = {
                "panel_id": panel_id, "user_id": interaction.user.id,
                "started_at": _stamp(_now()), "status": "working",
                "paused_seconds": 0, "pause_started_at": None, "pause_until": None,
                "warnings": 0, "prompt_message_id": None, "warning_due_at": None,
            }
            _save()
        await interaction.response.send_message("✅ Your shift has started.", ephemeral=True)
        await self.log(guild, panel, f"🟢 {interaction.user.mention} hat **{panel['name']}** begonnen.")
    
    async def open_pause_modal(self, interaction, panel_id):
        shift = self.get_shift(interaction.guild_id, panel_id, interaction.user.id)
        if not shift or shift.get("status") != "working":
            await interaction.response.send_message("You must be on shift and not already on a break.", ephemeral=True)
            return
        panel = _guild_data(interaction.guild_id)["panels"].get(panel_id, {})
        await interaction.response.send_modal(PauseModal(self, panel_id, interaction.user.id, panel.get("default_pause_minutes", 15)))

    async def start_pause(self, interaction, panel_id, minutes):
        guild = interaction.guild
        async with LOCK:
            shift = self.get_shift(guild.id, panel_id, interaction.user.id)
            if not shift or shift.get("status") != "working":
                await interaction.response.send_message("Your shift is not active or you are already on a break.", ephemeral=True)
                return
            now = _now()
            shift.update({
                "status": "paused", "pause_started_at": _stamp(now),
                "pause_until": _stamp(now + timedelta(minutes=minutes)),
                "prompt_message_id": None,
            })
            _save()
            panel = _guild_data(guild.id)["panels"].get(panel_id, {})
        await interaction.response.send_message(f"☕ Your {minutes}-minute break has started.", ephemeral=True)
        await self.log(guild, panel, f"☕ {interaction.user.mention} ist für **{minutes} Minuten** in Pause gegangen.")

    async def pause_answer(self, interaction, panel_id, user_id, returned):
        if interaction.user.id != user_id:
            await interaction.response.send_message("This prompt belongs to another member.", ephemeral=True)
            return
        guild = interaction.guild
        async with LOCK:
            data = _guild_data(guild.id)
            shift = data["shifts"].get(f"{panel_id}:{user_id}")
            panel = data["panels"].get(panel_id)
            if not shift or shift.get("status") != "paused" or not shift.get("prompt_message_id"):
                await interaction.response.send_message("This break prompt is no longer active.", ephemeral=True)
                return
            now = _now()
            pause_start = _parse_time(shift.get("pause_started_at"))
            shift["paused_seconds"] = int(shift.get("paused_seconds", 0) + (now - pause_start).total_seconds())
            shift["pause_started_at"] = None
            shift["pause_until"] = None
            shift["prompt_message_id"] = None
            shift["warning_due_at"] = None
            if returned:
                shift["status"] = "working"
                result = "You are back on shift. Your break has ended."
                log_message = f"▶️ {interaction.user.mention} ist aus der Pause zurückgekehrt."
            else:
                shift["status"] = "working"
                result = f"Your break has ended. Warnings: **{shift.get('warnings', 0)}/3**."
                log_message = f"▶️ {interaction.user.mention} acknowledged the return-to-work prompt."
            _save()
        await interaction.response.edit_message(content="Break prompt completed.", view=None)
        await interaction.followup.send(result, ephemeral=True)
        await self.log(guild, panel, log_message)

    async def set_warning_delay(self, interaction, panel_id, user_id, minutes):
        if interaction.user.id != user_id:
            await interaction.response.send_message("This prompt belongs to another member.", ephemeral=True)
            return
        async with LOCK:
            shift = self.get_shift(interaction.guild_id, panel_id, user_id)
            if not shift or shift.get("status") != "paused" or not shift.get("prompt_message_id"):
                await interaction.response.send_message("This break prompt is no longer active.", ephemeral=True)
                return
            due = _now() + timedelta(minutes=minutes)
            shift["warning_due_at"] = _stamp(due)
            _save()
        await interaction.response.edit_message(
            content=f"⏳ Verwarnung erfolgt automatisch, wenn du nicht innerhalb von **{minutes} Minuten** auf „I'm back at work“ klickst.",
            view=PausePromptView(self, panel_id, user_id),
        )

    async def checkout(self, interaction, panel_id, reason):
        guild = interaction.guild
        async with LOCK:
            data = _guild_data(guild.id)
            shift = data["shifts"].get(f"{panel_id}:{interaction.user.id}")
            panel = data["panels"].get(panel_id)
            if not shift or shift.get("status") == "ended":
                await interaction.response.send_message("You do not have an active shift in this panel.", ephemeral=True)
                return
            now = _now()
            if shift.get("status") == "paused" and shift.get("pause_started_at"):
                shift["paused_seconds"] = int(shift.get("paused_seconds", 0) + (now - _parse_time(shift["pause_started_at"])).total_seconds())
            total = max(0, int((now - _parse_time(shift["started_at"])).total_seconds()))
            worked = max(0, total - int(shift.get("paused_seconds", 0)))
            shift.update({"status": "ended", "ended_at": _stamp(now), "pause_started_at": None, "pause_until": None, "warning_due_at": None, "prompt_message_id": None})
            _save()
        await interaction.response.send_message(f"🔴 Shift ended. Worked: **{_duration(worked)}** (Pausen: {_duration(shift.get('paused_seconds', 0))}).", ephemeral=True)
        await self.log(guild, panel, f"🔴 {interaction.user.mention} hat **{panel['name']}** beendet. Gesamtdauer: **{_duration(total)}** · Arbeitszeit: **{_duration(worked)}** · Pausenzeit: **{_duration(shift.get('paused_seconds', 0))}** · Verwarnungen: **{shift.get('warnings', 0)}**.")

    @tasks.loop(seconds=20)
    async def pause_worker(self):
        now = _now()
        for guild_id, data in list(STATE.get("guilds", {}).items()):
            guild = self.bot.get_guild(int(guild_id))
            if guild is None:
                continue
            for shift in list(data.get("shifts", {}).values()):
                if shift.get("status") != "paused" or shift.get("prompt_message_id"):
                    continue
                panel_id = shift.get("panel_id")
                panel = data.get("panels", {}).get(panel_id)
                if not panel or _parse_time(shift.get("pause_until")) > now:
                    continue
                user_id = int(shift.get("user_id", 0))
                channel = guild.get_channel(int(panel.get("panel_channel_id", 0)))
                if not isinstance(channel, discord.TextChannel):
                    continue
                try:
                    async with LOCK:
                        current = self.get_shift(guild.id, panel_id, user_id)
                        if not current or current.get("status") != "paused" or current.get("prompt_message_id") or _parse_time(current.get("pause_until")) > _now():
                            continue
                        pause_start = _parse_time(current.get("pause_started_at"))
                        pause_end = _parse_time(current.get("pause_until"))
                        current["paused_seconds"] = int(current.get("paused_seconds", 0) + max(0, (pause_end - pause_start).total_seconds()))
                        current["pause_started_at"] = None
                        current["pause_until"] = None
                        if panel.get("warnings_enabled", True):
                            current["warnings"] = int(current.get("warnings", 0)) + 1
                        warning_count = int(current.get("warnings", 0))
                        ended = warning_count >= 3
                        if ended:
                            current.update({"status": "ended", "ended_at": _stamp(now), "warning_due_at": None, "prompt_message_id": None})
                        _save()
                    if ended:
                        total = max(0, int((now - _parse_time(current["started_at"])).total_seconds()))
                        paused = int(current.get("paused_seconds", 0))
                        worked = max(0, total - paused)
                        await channel.send(f"⛔ <@{user_id}> reached 3 warnings. The shift has been terminated for misconduct (dismissed for misconduct).", allowed_mentions=discord.AllowedMentions.none())
                        await self.log(guild, panel, f"⛔ <@{user_id}> was dismissed for misconduct after reaching 3 warnings. Total: **{_duration(total)}** · Worked: **{_duration(worked)}** · Breaks: **{_duration(paused)}** · Warnings: **{warning_count}/3**.")
                        continue
                    reminder_text = str(panel.get("pause_reminder_message", "⏰ Your break is over, {mention}. Please confirm that you are back at work using the button below."))[:1500]
                    reminder_text = (reminder_text.replace("{mention}", f"<@{user_id}>").replace("{name}", str(panel.get("name", "Shift"))[:50]).replace("{warning_minutes}", str(max(1, min(60, int(panel.get("warning_delay_minutes", 5) or 5))))))
                    reminder_text += f"\n\n⚠️ Warning recorded immediately: **{warning_count}/3**."
                    message = await channel.send(reminder_text, view=PausePromptView(self, panel_id, user_id), allowed_mentions=discord.AllowedMentions(users=True))
                    async with LOCK:
                        current = self.get_shift(guild.id, panel_id, user_id)
                        if current and current.get("status") == "paused" and not current.get("prompt_message_id"):
                            current["prompt_message_id"] = message.id
                            current["warning_due_at"] = None
                            _save()
                        else:
                            await message.delete()
                    await self.log(guild, panel, f"⚠️ <@{user_id}> received an immediate warning when the break ended ({warning_count}/3).")
                except (discord.Forbidden, discord.HTTPException) as exc:
                    print(f"[SHIFT] Break-end warning failed for {user_id}: {type(exc).__name__}: {exc}", flush=True)

    @pause_worker.before_loop
    async def before_pause_worker(self):
        await self.bot.wait_until_ready()

    @app_commands.guild_only()
    @app_commands.command(name="schichtpanel_erstellen", description="Create a shift panel with check-in, break and check-out.")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(name="Name des Panels, z. B. Polizei oder EMS", logkanal="Kanal für alle Schicht-Protokolle",
                           pausenminuten="Vorgeschlagene Pausenlänge", warnungen="Warnung vergeben, wenn jemand Nein auswählt")
    async def create_panel(self, interaction: discord.Interaction, name: app_commands.Range[str, 2, 50],
                           logkanal: discord.TextChannel, pausenminuten: app_commands.Range[int, 1, 480] = 15,
                           warnungen: bool = True):
        guild = interaction.guild
        from bot import guild_config
        premium = guild_has_feature(guild_config(guild.id), "shift_tracker")
        async with LOCK:
            data = _guild_data(guild.id)
            limit = 5 if premium else 2
            if len(data["panels"]) >= limit:
                await interaction.response.send_message(
                    f"Du hast das Limit von **{limit} Schicht-Panels** erreicht ({'Premium' if premium else 'Free'}).",
                    ephemeral=True,
                )
                return
            panel_id = secrets.token_hex(4)
            panel = {
                "name": name.strip(), "log_channel_id": logkanal.id,
                "panel_channel_id": interaction.channel_id,
                "default_pause_minutes": pausenminuten,
                "warnings_enabled": warnungen,
                "created_at": _stamp(_now()),
            }
            data["panels"][panel_id] = panel
            _save()
        embed = discord.Embed(
            title=f"🐺 IronWolf · {panel['name']}",
            description="Click **Start shift** to begin. You can choose your break duration when starting a break.",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Default break", value=f"{pausenminuten} Minuten", inline=True)
        embed.add_field(name="Warnings", value="Aktiv" if warnungen else "Deaktiviert", inline=True)
        try:
            message = await interaction.channel.send(embed=embed, view=ShiftPanelView(self, panel_id))
        except (discord.Forbidden, discord.HTTPException):
            async with LOCK:
                _guild_data(guild.id)["panels"].pop(panel_id, None)
                _save()
            await interaction.response.send_message("Ich kann in diesem Kanal kein Panel veröffentlichen.", ephemeral=True)
            return
        async with LOCK:
            _guild_data(guild.id)["panels"][panel_id]["panel_message_id"] = message.id
            _save()
        await interaction.response.send_message(f"✅ Schicht-Panel **{name}** erstellt. ID: `{panel_id}` · Limit: {limit}.", ephemeral=True)
        await self.log(guild, panel, f"⚙️ Schicht-Panel **{name}** wurde von {interaction.user.mention} erstellt.")

    @app_commands.guild_only()
    @app_commands.command(name="schichtpanel_liste", description="List shift panels on this server.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def list_panels(self, interaction: discord.Interaction):
        panels = _guild_data(interaction.guild_id)["panels"]
        if not panels:
            await interaction.response.send_message("No shift panels have been created yet.", ephemeral=True)
            return
        lines = [f"• **{p['name']}** · ID `{pid}` · Log: <#{p['log_channel_id']}>" for pid, p in panels.items()]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @app_commands.guild_only()
    @app_commands.command(name="schichtpanel_loeschen", description="Delete a shift panel.")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(panel_id="ID aus /schichtpanel_liste")
    async def delete_panel(self, interaction: discord.Interaction, panel_id: str):
        async with LOCK:
            data = _guild_data(interaction.guild_id)
            if panel_id not in data["panels"]:
                await interaction.response.send_message("Panel ID not found.", ephemeral=True)
                return
            active = [s for s in data["shifts"].values() if s.get("panel_id") == panel_id and s.get("status") != "ended"]
            if active:
                await interaction.response.send_message("This panel still has active shifts. End them before deleting the panel.", ephemeral=True)
                return
            panel = data["panels"].pop(panel_id)
            _save()
        channel = interaction.guild.get_channel(int(panel.get("panel_channel_id", 0)))
        if channel:
            try:
                msg = await channel.fetch_message(int(panel.get("panel_message_id", 0)))
                await msg.edit(content="This shift panel has been deactivated.", embed=None, view=None)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass
        await interaction.response.send_message(f"Panel **{panel['name']}** wurde gelöscht.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(ShiftTracker(bot))
