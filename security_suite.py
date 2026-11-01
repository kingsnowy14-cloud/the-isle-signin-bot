"""Opt-in security suite for IronWolf: lockdown, audit, verification, scam filtering and backups."""
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands
from storage import data_path

STATE_FILE = data_path("security_suite.json")
BACKUP_DIR = data_path("security_backups")
BACKUP_DIR.mkdir(parents=True, exist_ok=True)
URL_RE = re.compile(r"https?://[^\s<>]+|www\.[^\s<>]+", re.IGNORECASE)
INVITE_RE = re.compile(r"(?:discord\.gg/|discord(?:app)?\.com/invite/)[A-Za-z0-9-]+", re.IGNORECASE)
MAX_BACKUPS = 30


STATE_CACHE = None


def read_state():
    global STATE_CACHE
    if STATE_CACHE is not None:
        return STATE_CACHE
    try:
        value = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        STATE_CACHE = value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        STATE_CACHE = {}
    return STATE_CACHE


def write_state(value):
    global STATE_CACHE
    temp = STATE_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(STATE_FILE)
    STATE_CACHE = value


def guild_state(guild_id):
    db = read_state()
    changed = str(guild_id) not in db
    cfg = db.setdefault(str(guild_id), {})
    defaults = {
        "log_channel_id": None, "verify_channel_id": None, "verified_role_id": None,
        "unverified_role_id": None, "alt_min_days": 7, "alt_alerts_enabled": True,
        "scam_enabled": False, "blocked_domains": [], "lockdown": None,
    }
    for key, default in defaults.items():
        if key not in cfg:
            cfg[key] = default
            changed = True
    if changed:
        write_state(db)
    return cfg


def save_guild_state(guild_id, cfg):
    db = read_state()
    db[str(guild_id)] = cfg
    write_state(db)


def is_admin():
    return app_commands.checks.has_permissions(administrator=True)


def bot_member(guild):
    return guild.me or guild.get_member(guild._state.self_id)


async def send_log(guild, message):
    cfg = guild_state(guild.id)
    channel = guild.get_channel(cfg.get("log_channel_id")) if cfg.get("log_channel_id") else None
    if isinstance(channel, discord.TextChannel):
        try:
            await channel.send(message[:1900], allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            pass


def channel_overwrite_record(channel, role):
    overwrite = channel.overwrites_for(role)
    return overwrite.send_messages


class VerifyButton(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Jetzt verifizieren", style=discord.ButtonStyle.success, emoji="✅", custom_id="ironwolf_secure:verify")
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        if guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Please use this button in the server.", ephemeral=True)
            return
        cfg = guild_state(guild.id)
        role_id = cfg.get("verified_role_id")
        role = guild.get_role(role_id) if role_id else None
        if role is None:
            await interaction.response.send_message("Verification is not fully configured yet. Please contact the team.", ephemeral=True)
            return
        me = bot_member(guild)
        if me is None or not me.guild_permissions.manage_roles or role >= me.top_role or role.managed:
            await interaction.response.send_message("The bot cannot safely assign the verification role. Please check its role permissions and hierarchy.", ephemeral=True)
            return
        member = interaction.user
        try:
            if role not in member.roles:
                await member.add_roles(role, reason="IronWolf self-verification")
            unverified_id = cfg.get("unverified_role_id")
            unverified = guild.get_role(unverified_id) if unverified_id else None
            if unverified and unverified in member.roles:
                if unverified >= me.top_role or unverified.managed:
                    await interaction.response.send_message("The Unverified role is above the bot's role; the team must correct the role hierarchy.", ephemeral=True)
                    return
                await member.remove_roles(unverified, reason="IronWolf verification completed")
        except discord.Forbidden:
            await interaction.response.send_message("Discord denied the role change. Please check the bot's permissions and role hierarchy.", ephemeral=True)
            return
        await interaction.response.send_message("✅ Verification completed.", ephemeral=True)
        await send_log(guild, f"✅ {member} ({member.id}) has completed verification.")


class SecuritySuite(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._join_times = {}

    def require_bot_permissions(self, guild, *, manage_roles=False, manage_messages=False):
        me = bot_member(guild)
        if me is None:
            return False, "Could not load the bot member."
        if manage_roles and not me.guild_permissions.manage_roles:
            return False, "The bot is missing **Manage Roles** (required for channel permissions and role changes)."
        if manage_messages and not me.guild_permissions.manage_messages:
            return False, "Dem Bot fehlt **Nachrichten verwalten**."
        return True, ""

    @app_commands.command(name="secure-log-setup", description="Sets the central security log channel")
    @app_commands.guild_only()
    @is_admin()
    async def log_setup(self, interaction: discord.Interaction, channel: discord.TextChannel):
        me = bot_member(interaction.guild)
        perms = kanal.permissions_for(me) if me else None
        if not perms or not perms.view_channel or not perms.send_messages:
            await interaction.response.send_message("The bot needs **View Channel** and **Send Messages** permissions in the log channel.", ephemeral=True)
            return
        cfg = guild_state(interaction.guild_id)
        cfg["log_channel_id"] = kanal.id
        save_guild_state(interaction.guild_id, cfg)
        await interaction.response.send_message(f"🛡️ Security logs will be posted in {kanal.mention} gespeichert.", ephemeral=True)

    @app_commands.command(name="secure-lockdown", description="Sperrt Textkanäle für @everyone und merkt vorherige Rechte")
    @app_commands.guild_only()
    @is_admin()
    async def lockdown(self, interaction: discord.Interaction):
        guild = interaction.guild
        cfg = guild_state(guild.id)
        if cfg.get("lockdown"):
            await interaction.response.send_message("Ein Lockdown ist bereits aktiv. Nutze zuerst /secure-unlock.", ephemeral=True)
            return
        ok, reason = self.require_bot_permissions(guild, manage_roles=True)
        if not ok:
            await interaction.response.send_message(reason, ephemeral=True)
            return
        everyone = guild.default_role
        snapshot = {}
        changed = 0
        failed = []
        await interaction.response.defer(ephemeral=True, thinking=True)
        for channel in guild.text_channels:
            if isinstance(channel, discord.TextChannel) and channel.category_id is not None:
                # Only ordinary text channels are included; forums, voice and categories are untouched.
                pass
            old = channel.overwrites_for(everyone).send_messages
            snapshot[str(channel.id)] = old
            overwrite = channel.overwrites_for(everyone)
            overwrite.send_messages = False
            try:
                await channel.set_permissions(everyone, overwrite=overwrite, reason=f"Security lockdown by {interaction.user} ({interaction.user.id})")
                changed += 1
            except (discord.Forbidden, discord.HTTPException):
                failed.append(channel.name)
        cfg["lockdown"] = {"started_at": datetime.now(timezone.utc).isoformat(), "send_messages": snapshot}
        save_guild_state(guild.id, cfg)
        await send_log(guild, f"🔒 Lockdown gestartet von {interaction.user} ({interaction.user.id}). Geänderte Kanäle: {changed}; fehlgeschlagen: {len(failed)}.")
        details = f"🔒 Lockdown ausgeführt: {changed} Textkanäle gesperrt."
        if failed:
            details += "\nNicht geändert: " + ", ".join(failed[:10])
        details += "\nNutze **/secure-unlock**, um die zuvor gespeicherten @everyone-Schreibrechte wiederherzustellen."
        await interaction.followup.send(details, ephemeral=True)

    @app_commands.command(name="secure-unlock", description="Hebt Lockdown auf und stellt gespeicherte @everyone-Schreibrechte wieder her")
    @app_commands.guild_only()
    @is_admin()
    async def unlock(self, interaction: discord.Interaction):
        guild = interaction.guild
        cfg = guild_state(guild.id)
        lockdown = cfg.get("lockdown")
        if not lockdown:
            await interaction.response.send_message("Es ist kein Lockdown gespeichert.", ephemeral=True)
            return
        ok, reason = self.require_bot_permissions(guild, manage_roles=True)
        if not ok:
            await interaction.response.send_message(reason, ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        restored, failed, skipped = 0, [], 0
        everyone = guild.default_role
        remaining = {}
        for channel_id, old_value in lockdown.get("send_messages", {}).items():
            channel = guild.get_channel(int(channel_id))
            if not isinstance(channel, discord.TextChannel):
                skipped += 1
                continue
            overwrite = channel.overwrites_for(everyone)
            overwrite.send_messages = old_value
            try:
                await channel.set_permissions(everyone, overwrite=overwrite, reason=f"Security lockdown lifted by {interaction.user} ({interaction.user.id})")
                restored += 1
            except (discord.Forbidden, discord.HTTPException):
                failed.append(channel.name)
                remaining[str(channel_id)] = old_value
        cfg["lockdown"] = ({"started_at": lockdown.get("started_at"), "send_messages": remaining} if remaining else None)
        save_guild_state(guild.id, cfg)
        await send_log(guild, f"🔓 Lockdown aufgehoben von {interaction.user} ({interaction.user.id}). Wiederhergestellt: {restored}; fehlgeschlagen: {len(failed)}.")
        message = f"🔓 Rechte für {restored} Kanäle wiederhergestellt; übersprungen (gelöscht/anderer Typ): {skipped}."
        if failed:
            message += "\nFehlgeschlagen: " + ", ".join(failed[:10]) + "\nDer Lockdown-Zustand bleibt zur Nachbearbeitung gespeichert."
        await interaction.followup.send(message, ephemeral=True)

    @app_commands.command(name="secure-audit", description="Prüft gefährliche Rollenrechte und die Bot-Berechtigungen")
    @app_commands.guild_only()
    @is_admin()
    async def audit(self, interaction: discord.Interaction):
        guild = interaction.guild
        me = bot_member(guild)
        findings = []
        for role in sorted(guild.roles, key=lambda r: r.position, reverse=True):
            if role.is_default():
                if role.permissions.administrator or role.permissions.manage_roles or role.permissions.ban_members or role.permissions.kick_members:
                    findings.append(f"@everyone besitzt erhöhte Rechte ({role.id}).")
            elif role.permissions.administrator:
                findings.append(f"Rolle **{discord.utils.escape_markdown(role.name)}** besitzt Administratorrechte.")
            elif role.permissions.manage_roles or role.permissions.manage_channels or role.permissions.ban_members or role.permissions.kick_members or role.permissions.manage_webhooks:
                findings.append(f"Rolle **{discord.utils.escape_markdown(role.name)}** besitzt sensible Moderations-/Verwaltungsrechte.")
        if me:
            if me.guild_permissions.administrator:
                findings.append("Der Bot besitzt Administratorrechte; wenn möglich auf notwendige Einzelrechte reduzieren.")
            if not me.guild_permissions.manage_roles:
                findings.append("Dem Bot fehlt Rollen verwalten; Lockdown und Verifizierung können nicht vollständig arbeiten.")
            if not me.guild_permissions.view_audit_log:
                findings.append("Dem Bot fehlt Audit-Log ansehen; vorhandene Anti-Nuke-Erkennung kann eingeschränkt sein.")
        cfg = guild_state(guild.id)
        if not cfg.get("log_channel_id") or not guild.get_channel(cfg["log_channel_id"]):
            findings.append("No valid security log channel is configured (/secure-log-setup).")
        if not cfg.get("verify_channel_id") or not cfg.get("verified_role_id"):
            findings.append("Verifizierung noch nicht konfiguriert (/secure-verify-setup).")
        if not cfg.get("scam_enabled"):
            findings.append("The link/scam filter is disabled (/secure-scam-setup).")
        embed = discord.Embed(title="🛡️ IronWolf Sicherheitsprüfung", color=discord.Color.orange() if findings else discord.Color.green(), timestamp=datetime.now(timezone.utc))
        embed.description = "\n".join(f"• {x}" for x in findings[:20]) if findings else "Keine der geprüften Fehlkonfigurationen gefunden. Dies ist keine vollständige Sicherheitsgarantie."
        embed.add_field(name="Rollen geprüft", value=str(len(guild.roles)))
        embed.add_field(name="Kanäle geprüft", value=str(len(guild.channels)))
        embed.set_footer(text="Read-only-Prüfung · Es wurden keine Rechte geändert")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="secure-verify-setup", description="Konfiguriert Verifizierung und veröffentlicht einen Button")
    @app_commands.guild_only()
    @is_admin()
    async def verify_setup(self, interaction: discord.Interaction, kanal: discord.TextChannel, verified_role: discord.Role, unverified_role: discord.Role = None):
        guild = interaction.guild
        me = bot_member(guild)
        if not me or not me.guild_permissions.manage_roles:
            await interaction.response.send_message("Dem Bot fehlt **Rollen verwalten**.", ephemeral=True)
            return
        if verified_rolle.is_default() or verified_rolle.managed or verified_rolle >= me.top_role:
            await interaction.response.send_message("Die Verified-Rolle muss unter der höchsten Bot-Rolle liegen und darf keine verwaltete Rolle sein.", ephemeral=True)
            return
        if unverified_rolle and (unverified_rolle.is_default() or unverified_rolle.managed or unverified_rolle >= me.top_role):
            await interaction.response.send_message("Die Unverified-Rolle muss unter der höchsten Bot-Rolle liegen und darf keine verwaltete Rolle sein.", ephemeral=True)
            return
        perms = kanal.permissions_for(me)
        if not perms.view_channel or not perms.send_messages:
            await interaction.response.send_message("Der Bot benötigt im Zielkanal **Kanal ansehen** und **Nachrichten senden**.", ephemeral=True)
            return
        cfg = guild_state(guild.id)
        cfg["verify_channel_id"] = kanal.id
        cfg["verified_role_id"] = verified_rolle.id
        cfg["unverified_role_id"] = unverified_rolle.id if unverified_rolle else None
        save_guild_state(guild.id, cfg)
        embed = discord.Embed(title="🛡️ Member verification", description="Click **Verify now** to receive the verification role. If you have any issues, contact the team.", color=discord.Color.blurple())
        try:
            await kanal.send(embed=embed, view=VerifyButton())
        except discord.HTTPException:
            await interaction.response.send_message("Configuration saved, but the panel could not be sent. Check the channel permissions.", ephemeral=True)
            return
        await interaction.response.send_message(f"Verification configured in {kanal.mention}.", ephemeral=True)

    @app_commands.command(name="secure-scam-setup", description="Enabled Link filter und verwaltet gesperrte Domains")
    @app_commands.guild_only()
    @is_admin()
    async def scam_setup(self, interaction: discord.Interaction, aktiv: bool, domains: str = ""):
        cfg = guild_state(interaction.guild_id)
        parsed = []
        for item in domains.split(","):
            domain = item.strip().lower().removeprefix("https://").removeprefix("http://").split("/")[0].strip(".")
            if domain:
                if not re.fullmatch(r"[a-z0-9.-]{1,253}", domain) or ".." in domain:
                    await interaction.response.send_message(f"Invalid domain: {item.strip()[:80]}", ephemeral=True)
                    return
                parsed.append(domain)
        cfg["blocked_domains"] = sorted(set(parsed))
        cfg["scam_enabled"] = aktiv
        save_guild_state(interaction.guild_id, cfg)
        await interaction.response.send_message(
            f"Link filter {'enabled' if aktiv else 'deenabled'}. Gesperrte Domains: {len(parsed)}. "
            "Discord-Einladungen werden bei aktivem Filter ebenfalls entfernt; Links sind für Administratoren und Mitglieder mit Nachrichten verwalten ausgenommen.",
            ephemeral=True
        )

    @app_commands.command(name="secure-alt-setup", description="Konfiguriert Warnungen für sehr neue Accounts")
    @app_commands.guild_only()
    @is_admin()
    async def alt_setup(self, interaction: discord.Interaction, mindestalter_tage: app_commands.Range[int, 1, 365] = 7, aktiv: bool = True):
        cfg = guild_state(interaction.guild_id)
        cfg["alt_min_days"] = mindestalter_tage
        cfg["alt_alerts_enabled"] = aktiv
        save_guild_state(interaction.guild_id, cfg)
        await interaction.response.send_message(f"Alternate-account warnings {'enabled' if aktiv else 'deenabled'}; Warnschwelle: {mindestalter_tage} days. New accounts are flagged only; they are not automatically kicked or banned.", ephemeral=True)

    @app_commands.command(name="secure-backup", description="Creates a JSON backup of the server structure and permissions")
    @app_commands.guild_only()
    @is_admin()
    async def backup(self, interaction: discord.Interaction):
        guild = interaction.guild
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        record = {
            "schema_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "guild_id": guild.id,
            "guild_name": guild.name,
            "roles": [
                {"id": r.id, "name": r.name, "position": r.position, "permissions": r.permissions.value,
                 "colour": r.colour.value, "hoist": r.hoist, "mentionable": r.mentionable, "managed": r.managed}
                for r in guild.roles
            ],
            "channels": [],
        }
        for channel in guild.channels:
            item = {"id": channel.id, "name": channel.name, "type": str(channel.type), "position": channel.position,
                    "category_id": getattr(channel, "category_id", None), "overwrites": []}
            if isinstance(channel, (discord.TextChannel, discord.VoiceChannel, discord.CategoryChannel, discord.ForumChannel)):
                if isinstance(channel, discord.TextChannel):
                    item["topic"] = channel.topic
                    item["slowmode_delay"] = channel.slowmode_delay
                    item["nsfw"] = channel.nsfw
                for target, overwrite in channel.overwrites.items():
                    item["overwrites"].append({
                        "target_id": target.id, "target_type": "role" if isinstance(target, discord.Role) else "member",
                        "allow": overwrite.pair()[0].value, "deny": overwrite.pair()[1].value
                    })
            record["channels"].append(item)
        path = BACKUP_DIR / f"guild-{guild.id}-{stamp}.json"
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        files = sorted(BACKUP_DIR.glob(f"guild-{guild.id}-*.json"), reverse=True)
        for old in files[MAX_BACKUPS:]:
            try:
                old.unlink()
            except OSError:
                pass
        await interaction.response.send_message(f"✅ Backup created: **{path.name}** · {len(record['roles'])} roles · {len(record['channels'])} channels. Saved in the bot's DATA_DIR; no server changes were made.", ephemeral=True)
        await send_log(guild, f"💾 Backup erstellt von {interaction.user} ({interaction.user.id}): {path.name}")

    @app_commands.command(name="secure-backup-list", description="Listet die letzten Server-Backups")
    @app_commands.guild_only()
    @is_admin()
    async def backup_list(self, interaction: discord.Interaction):
        files = sorted(BACKUP_DIR.glob(f"guild-{interaction.guild_id}-*.json"), reverse=True)[:10]
        if not files:
            await interaction.response.send_message("Noch keine Backups. Nutze /secure-backup.", ephemeral=True)
            return
        lines = []
        for path in files:
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                lines.append(f"• **{path.name}** — {record.get('created_at', 'Zeit unbekannt')[:19]} UTC")
            except (OSError, json.JSONDecodeError):
                lines.append(f"• **{path.name}** — Datei nicht lesbar")
        await interaction.response.send_message("\n".join(lines)[:1900], ephemeral=True)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        if member.bot:
            return
        cfg = guild_state(member.guild.id)
        if cfg.get("alt_alerts_enabled", True):
            age_days = max(0, (datetime.now(timezone.utc) - member.created_at).days)
            if age_days < int(cfg.get("alt_min_days", 7)):
                await send_log(member.guild, f"⚠️ Neuer Account beigetreten: {member} ({member.id}) · Accountalter: {age_days} Tage. Nur Warnung, keine automatische Sanktion.")
        unverified_id = cfg.get("unverified_role_id")
        if unverified_id:
            role = member.guild.get_role(unverified_id)
            me = bot_member(member.guild)
            if role and me and me.guild_permissions.manage_roles and not role.managed and role < me.top_role:
                try:
                    await member.add_roles(role, reason="IronWolf verification gate")
                except (discord.Forbidden, discord.HTTPException):
                    await send_log(member.guild, f"⚠️ Unverified-Rolle konnte für {member.id} nicht vergeben werden; Rechte/Hiearchie prüfen.")

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild or not message.content:
            return
        cfg = guild_state(message.guild.id)
        if not cfg.get("scam_enabled"):
            return
        if message.author.guild_permissions.administrator or message.author.guild_permissions.manage_messages:
            return
        urls = URL_RE.findall(message.content)
        if not urls:
            return
        blocked = set(d.lower().lstrip(".") for d in cfg.get("blocked_domains", []))
        matched = bool(INVITE_RE.search(message.content))
        for raw in urls:
            host = raw.lower().split("://", 1)[-1].split("/", 1)[0].split("?", 1)[0].split("#", 1)[0].split(":", 1)[0].strip(".")
            host = host.removeprefix("www.")
            if any(host == domain or host.endswith("." + domain) for domain in blocked):
                matched = True
                break
        if not matched:
            return
        try:
            await message.delete()
            await send_log(message.guild, f"🚫 Link filter: Nachricht von {message.author} ({message.author.id}) in #{message.channel} entfernt. Inhalt wurde nicht in Logs kopiert.")
            try:
                await message.author.send(f"Your link in **{message.guild.name}** wurde durch den konfigurierten Link filter entfernt.")
            except discord.HTTPException:
                pass
        except discord.Forbidden:
            await send_log(message.guild, f"⚠️ Link filter erkannte einen gesperrten Link von {message.author} ({message.author.id}), konnte ihn aber nicht löschen. Dem Bot fehlt Nachrichten verwalten.")
        except discord.HTTPException:
            pass


async def setup(bot):
    await bot.add_cog(SecuritySuite(bot))
    bot.add_view(VerifyButton())
