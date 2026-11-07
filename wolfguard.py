"""WolfGuard: versioned server backups and explainable security monitoring."""
import copy
import hashlib
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path

import discord
from discord import app_commands
from storage import data_path

STORE = data_path("wolfguard.json")
MAX_VERSIONS = 25
MAX_SIGNALS = 500
JOIN_WINDOW = {}
WEBHOOK_BASELINE = {}


def now():
    return datetime.now(timezone.utc)


def read_store():
    try:
        value = json.loads(STORE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def write_store(value):
    STORE.parent.mkdir(parents=True, exist_ok=True)
    temp = STORE.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(STORE)


def guild_store(store, guild_id):
    return store.setdefault("guilds", {}).setdefault(str(guild_id), {
        "versions": [], "signals": [], "webhooks": {}
    })


def enabled(cfg, feature):
    return bool(cfg.get("wolfguard", {}).get(feature, False))


def add_signal(guild_id, kind, severity, title, detail, actor=None, subject=None):
    store = read_store()
    target = guild_store(store, guild_id)
    item = {
        "id": secrets.token_hex(5).upper(),
        "at": now().isoformat(),
        "kind": kind,
        "severity": severity,
        "title": title[:120],
        "detail": detail[:900],
        "actor": str(actor) if actor else None,
        "subject": str(subject) if subject else None,
    }
    target["signals"] = (target.get("signals", []) + [item])[-MAX_SIGNALS:]
    write_store(store)
    return item


async def alert(guild, cfg, item):
    channel_id = cfg.get("wolfguard", {}).get("log_channel_id")
    if not str(channel_id or "").isdigit():
        return
    channel = guild.get_channel(int(channel_id))
    if not isinstance(channel, discord.TextChannel):
        return
    colors = {
        "low": discord.Color.blurple(),
        "medium": discord.Color.gold(),
        "high": discord.Color.orange(),
        "critical": discord.Color.red(),
    }
    embed = discord.Embed(
        title="IronWolf · " + item["title"],
        description=item["detail"],
        color=colors.get(item["severity"], discord.Color.gold()),
        timestamp=now(),
    )
    embed.add_field(name="Schweregrad", value=item["severity"].upper(), inline=True)
    embed.add_field(name="Referenz", value=item["id"], inline=True)
    if item.get("actor"):
        embed.add_field(name="Akteur", value=item["actor"], inline=True)
    try:
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    except (discord.Forbidden, discord.HTTPException) as exc:
        print("[WOLFGUARD] Alert failed: " + type(exc).__name__, flush=True)


def snapshot(guild_id, cfg, data):
    # Keep only data buckets explicitly keyed by this guild; never copy global secrets.
    scoped = {}
    for key, value in data.items():
        if isinstance(value, dict) and str(guild_id) in value:
            scoped[key] = {str(guild_id): copy.deepcopy(value[str(guild_id)])}
    payload = {
        "schema": 1,
        "guild_id": str(guild_id),
        "created_at": now().isoformat(),
        "config": copy.deepcopy(cfg),
        "data": scoped,
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    version_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12].upper()
    return {"id": version_id, **payload}


def create_version(guild_id, cfg, data):
    version = snapshot(guild_id, cfg, data)
    store = read_store()
    target = guild_store(store, guild_id)
    versions = target.get("versions", [])
    if not any(v.get("id") == version["id"] for v in versions):
        target["versions"] = ([version] + versions)[:MAX_VERSIONS]
        write_store(store)
    return version


def setup_wolfguard(bot, guild_config, config, data, save_json, config_file, data_file):
    async def on_member_join(member):
        if member.bot:
            return
        cfg = guild_config(member.guild.id)
        if not enabled(cfg, "alt_network"):
            return
        stamp = now().timestamp()
        joins = JOIN_WINDOW.setdefault(member.guild.id, [])
        joins[:] = [x for x in joins if stamp - x[0] < 900]
        age_days = max(0, (now() - member.created_at).days)
        joins.append((stamp, member.id, age_days))
        young = [x for x in joins if x[2] <= 7]
        if age_days <= 7 and len(young) >= 3:
            ids = [str(x[1]) for x in young[-10:]]
            item = add_signal(
                member.guild.id, "young_account_cluster", "high",
                "Möglicher Alt-Account-Cluster",
                str(len(young)) + " sehr junge Accounts sind innerhalb von 15 Minuten beigetreten. "
                "Das ist ein Prüfsignal, kein Beweis gemeinsamer Inhaberschaft. IDs: " + ", ".join(ids),
                subject=member.id,
            )
            await alert(member.guild, cfg, item)

    async def on_webhooks_update(channel):
        guild = channel.guild
        cfg = guild_config(guild.id)
        if not enabled(cfg, "webhook_monitor"):
            return
        try:
            hooks = await channel.webhooks()
        except (discord.Forbidden, discord.HTTPException):
            return
        current = {
            h.id: {"name": h.name, "creator": getattr(h.user, "id", None), "channel": channel.id}
            for h in hooks
        }
        baseline = WEBHOOK_BASELINE.get(guild.id)
        if baseline is None:
            WEBHOOK_BASELINE[guild.id] = current
            return
        changes = []
        for hook_id, info in current.items():
            if hook_id not in baseline:
                changes.append(("erstellt", hook_id, info, "high"))
            elif baseline[hook_id] != info:
                changes.append(("geändert", hook_id, info, "medium"))
        for hook_id, info in baseline.items():
            if hook_id not in current:
                changes.append(("gelöscht", hook_id, info, "medium"))
        WEBHOOK_BASELINE[guild.id] = current
        for action, hook_id, info, severity in changes:
            item = add_signal(
                guild.id, "webhook_" + action, severity,
                "Webhook " + action,
                "Webhook " + info.get("name", "unbekannt") + " in #" + channel.name
                + " (" + str(hook_id) + "). Ersteller und Zweck bitte prüfen.",
                actor=info.get("creator"),
                subject=hook_id,
            )
            await alert(guild, cfg, item)

    async def seed_webhooks():
        for guild in bot.guilds:
            if not enabled(guild_config(guild.id), "webhook_monitor"):
                continue
            inventory = {}
            for channel in guild.text_channels:
                try:
                    for hook in await channel.webhooks():
                        inventory[hook.id] = {
                            "name": hook.name,
                            "creator": getattr(hook.user, "id", None),
                            "channel": channel.id,
                        }
                except (discord.Forbidden, discord.HTTPException):
                    continue
            WEBHOOK_BASELINE[guild.id] = inventory

    async def on_guild_channel_update(before, after):
        cfg = guild_config(after.guild.id)
        if not enabled(cfg, "insider_detection") or before.overwrites == after.overwrites:
            return
        actor = None
        try:
            async for entry in after.guild.audit_logs(limit=8, action=discord.AuditLogAction.overwrite_update):
                if entry.target and entry.target.id == after.id:
                    actor = entry.user.id if entry.user else None
                    break
        except (discord.Forbidden, discord.HTTPException):
            pass
        item = add_signal(
            after.guild.id, "permission_overwrite", "high",
            "Kanalberechtigungen geändert",
            "Overwrites in #" + after.name + " wurden geändert. Prüfe Umfang und Anlass.",
            actor=actor, subject=after.id,
        )
        await alert(after.guild, cfg, item)

    async def on_guild_role_update(before, after):
        cfg = guild_config(after.guild.id)
        if not enabled(cfg, "insider_detection"):
            return
        sensitive = discord.Permissions(
            administrator=True, manage_guild=True, manage_roles=True,
            manage_channels=True, manage_webhooks=True, ban_members=True,
            kick_members=True, manage_messages=True,
        )
        if (before.permissions.value & sensitive.value) == (after.permissions.value & sensitive.value):
            return
        actor = None
        try:
            async for entry in after.guild.audit_logs(limit=8, action=discord.AuditLogAction.role_update):
                if entry.target and entry.target.id == after.id:
                    actor = entry.user.id if entry.user else None
                    break
        except (discord.Forbidden, discord.HTTPException):
            pass
        item = add_signal(
            after.guild.id, "privileged_role_change", "critical",
            "Privilegierte Rollenrechte geändert",
            "Sicherheitsrelevante Rechte der Rolle " + after.name + " wurden geändert.",
            actor=actor, subject=after.id,
        )
        await alert(after.guild, cfg, item)

    bot.add_listener(on_member_join, "on_member_join")
    bot.add_listener(on_webhooks_update, "on_webhooks_update")
    bot.add_listener(on_guild_channel_update, "on_guild_channel_update")
    bot.add_listener(on_guild_role_update, "on_guild_role_update")
    bot.add_listener(seed_webhooks, "on_ready")

    @bot.tree.command(name="wolfguard-setup", description="Konfiguriert IronWolf Backup und Sicherheitsüberwachung")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(administrator=True)
    async def wolfguard_setup(interaction: discord.Interaction, log_kanal: discord.TextChannel,
                              alt_account_netzwerk: bool = True,
                              insider_detection: bool = True,
                              webhook_monitor: bool = True,
                              backup_versionen: bool = True):
        cfg = guild_config(interaction.guild_id)
        cfg["wolfguard"] = {
            "log_channel_id": log_kanal.id,
            "alt_network": alt_account_netzwerk,
            "insider_detection": insider_detection,
            "webhook_monitor": webhook_monitor,
            "backup_versions": backup_versionen,
        }
        save_json(config_file, config)
        await interaction.response.send_message(
            "WolfGuard konfiguriert. Meldungen gehen an " + log_kanal.mention
            + ". Automatische Sanktionen sind deaktiviert.",
            ephemeral=True,
        )

    @bot.tree.command(name="server-backup-version", description="Erstellt einen versionierten Server-Snapshot")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(administrator=True)
    async def server_backup_version(interaction: discord.Interaction):
        cfg = guild_config(interaction.guild_id)
        if not enabled(cfg, "backup_versions"):
            await interaction.response.send_message("Bitte zuerst /wolfguard-setup aktivieren.", ephemeral=True)
            return
        version = create_version(interaction.guild_id, cfg, data)
        await interaction.response.send_message(
            "Backup-Version " + version["id"] + " gespeichert. Enthält die IronWolf-Serverkonfiguration "
            "und serverbezogene Datensätze, keine Bot-Tokens oder globalen Geheimnisse.",
            ephemeral=True,
        )

    @bot.tree.command(name="server-backup-versionen", description="Listet die gespeicherten Backup-Versionen")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(administrator=True)
    async def server_backup_versionen(interaction: discord.Interaction):
        versions = guild_store(read_store(), interaction.guild_id).get("versions", [])
        if not versions:
            await interaction.response.send_message("Noch keine Backups vorhanden.", ephemeral=True)
            return
        lines = [v["id"] + " · " + v["created_at"] for v in versions[:15]]
        await interaction.response.send_message("**Backup-Versionen**\n" + "\n".join(lines), ephemeral=True)

    class RestoreView(discord.ui.View):
        def __init__(self, guild_id, version_id, requester_id):
            super().__init__(timeout=60)
            self.guild_id = guild_id
            self.version_id = version_id
            self.requester_id = requester_id

        @discord.ui.button(label="Wiederherstellung bestätigen", style=discord.ButtonStyle.danger)
        async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
            if interaction.guild_id != self.guild_id or interaction.user.id != self.requester_id:
                await interaction.response.send_message("Nur der anfragende Administrator darf bestätigen.", ephemeral=True)
                return
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message("Administratorrechte erforderlich.", ephemeral=True)
                return
            versions = guild_store(read_store(), self.guild_id).get("versions", [])
            version = next((v for v in versions if v.get("id") == self.version_id), None)
            if version is None:
                await interaction.response.send_message("Backup nicht gefunden.", ephemeral=True)
                return
            config[str(self.guild_id)] = copy.deepcopy(version["config"])
            for key, value in version.get("data", {}).items():
                if isinstance(value, dict) and str(self.guild_id) in value:
                    data.setdefault(key, {})[str(self.guild_id)] = copy.deepcopy(value[str(self.guild_id)])
            save_json(config_file, config)
            save_json(data_file, data)
            button.disabled = True
            await interaction.response.edit_message(
                content="Backup " + self.version_id + " wiederhergestellt. Bitte Servereinstellungen kontrollieren.",
                view=self,
            )

    @bot.tree.command(name="server-backup-restore", description="Stellt eine Backup-Version nach Bestätigung wieder her")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(administrator=True)
    async def server_backup_restore(interaction: discord.Interaction, version_id: str):
        version_id = version_id.strip().upper()
        versions = guild_store(read_store(), interaction.guild_id).get("versions", [])
        if not any(v.get("id") == version_id for v in versions):
            await interaction.response.send_message("Backup-Version nicht gefunden.", ephemeral=True)
            return
        await interaction.response.send_message(
            "Achtung: Die gespeicherte IronWolf-Serverkonfiguration überschreibt die aktuelle. "
            "Die Discord-Kanäle und Rollen selbst werden nicht verändert. Fortfahren?",
            view=RestoreView(interaction.guild_id, version_id, interaction.user.id),
            ephemeral=True,
        )

    @bot.tree.command(name="wolfguard-signale", description="Zeigt die letzten Sicherheitsmeldungen")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    async def wolfguard_signale(interaction: discord.Interaction):
        signals = guild_store(read_store(), interaction.guild_id).get("signals", [])[-10:]
        if not signals:
            await interaction.response.send_message("Keine Sicherheitsmeldungen gespeichert.", ephemeral=True)
            return
        lines = [
            x["severity"].upper() + " · " + x["id"] + " · " + x["title"] + "\n" + x["detail"][:160]
            for x in reversed(signals)
        ]
        await interaction.response.send_message("\n\n".join(lines)[:1900], ephemeral=True)
