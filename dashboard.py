"""IronWolf Control Center: Discord-OAuth protected web dashboard.

The dashboard only edits allow-listed server settings and command toggles.
New module files are generated as downloadable templates and are never executed
automatically by the running bot.
"""
import base64
import hmac
import inspect
import re
import asyncio
import html
import json
import os
import secrets
import sqlite3
import threading
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode, urlparse

import discord
from discord import app_commands
import requests
from flask import Flask, abort, redirect, request, session, url_for, make_response, send_file
from storage import data_path
from premium import PREMIUM_FEATURES, PLAN_FEATURES, account_plan, guild_has_feature, guild_trial_active
from wolfmirror import simulate_scenario, SCENARIOS
from wolfchronicle import INCIDENT_TYPES, SEVERITIES, STATUSES, create_incident, list_incidents, update_incident

DISCORD_API = "https://discord.com/api/v10"
MANAGE_GUILD = 0x20
ADMINISTRATOR = 0x8
MODULE_OUTPUT_DIR = data_path("created_modules")
MODULE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

app = Flask("ironwolf_dashboard")
app.secret_key = os.getenv("DASHBOARD_SECRET_KEY") or secrets.token_hex(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=True,
    MAX_CONTENT_LENGTH=8 * 1024 * 1024,
)

_bot = None
_config = None
_save_json = None
_guild_config = None
_oauth_state = None
_guild_cache = {}
_guild_cache_lock = threading.Lock()
_GUILD_CACHE_TTL = 45
_GUILD_CACHE_STALE_TTL = 300

TEXT_SETTINGS = {
    "welcome_message": {
        "label": "Welcome message",
        "help": "Placeholders: {mention}, {server}, {count}",
        "max_length": 1000,
    },
    "leave_message": {
        "label": "Goodbye message",
        "help": "Placeholders: {user}, {server}, {count}",
        "max_length": 1000,
    },

}
BOOL_SETTINGS = {
    "welcome_bots_enabled": "Send welcome messages to bot accounts",
    "welcome_dm_enabled": "Send a private welcome DM to new members",
    "invite_tracker_enabled": "Invite-Tracker aktivieren",
    "automod_enabled": "AutoMod",
    "xp_enabled": "Level- und XP-System aktivieren",
    "anti_nuke_enabled": "Anti-Nuke-Schutz",
    "anti_raid_enabled": "Anti-Raid-Schutz",
}
CHANNEL_SETTINGS = {
    "welcome_channel": "Public welcome channel",
    "goodbye_channel": "Goodbye channel",
    "birthday_channel": "Geburtstagskanal",
    "levelup_channel": "Level-up channel",
    "invite_log_channel": "Invite-Tracker-Logkanal",
    "security_log_channel": "Security log channel",
    "verify_channel": "Verifizierungskanal",
    "registration_channel": "Anmeldekanal",
    "ticket_log_channel": "Ticket-Logkanal",
    "activity_log_channel": "Enableditäts-Logkanal",
    "suggestion_channel": "Vorschlagskanal",
    "ticket_category": "Ticket-Kategorie",
    "log_channel_id": "Moderations-Logkanal",
}
ROLE_SETTINGS = {
    "support_role": "Support role",
    "verified_role": "Verified role",
    "unverified_role": "Unverified role",
    "trusted_security_role": "Vertrauenswürdige Sicherheitsrolle",
}

NUMBER_SETTINGS = {
    "anti_nuke_threshold": {"label": "Anti-Nuke-Schwelle", "help": "Verdächtige Aktionen pro 60 Sekunden (2–10).", "min": 2, "max": 10, "default": 4},
    "raid_threshold": {"label": "Anti-Raid-Schwelle", "help": "Beitritte innerhalb des Schutzzeitfensters (3–30).", "min": 3, "max": 30, "default": 8},
}

VOICE_CHANNEL_SETTINGS = {
    "tempvoice_trigger": "Temporärer Voice-Trigger",
}

CATEGORY_SETTINGS = {
    "tempvoice_category": "Kategorie für temporäre Voice-Kanäle",
}



_STEAM_OPENID_ENDPOINT = "https://steamcommunity.com/openid/login"
_STEAM_OPENID_NS = "http://specs.openid.net/auth/2.0"
_STEAM_LINK_STORE = data_path("steam_account_links.json")
_STEAM_LINK_LOCK = threading.Lock()
_STEAM_LINK_TTL_SECONDS = 600


def _steam_callback_config():
    """Return the explicitly configured HTTPS Steam OpenID callback and origin."""
    callback_uri = (os.getenv("STEAM_OPENID_RETURN_URI") or "").strip()
    parsed = urlparse(callback_uri)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path != "/gaming/steam/callback"
    ):
        raise ValueError(
            "Steam-Verknüpfung ist noch nicht konfiguriert. Setze in Railway "
            "STEAM_OPENID_RETURN_URI auf die öffentliche HTTPS-Adresse "
            "mit dem Pfad /gaming/steam/callback."
        )
    origin = f"https://{parsed.netloc}"
    return callback_uri, origin


def _steam_read_store_locked():
    try:
        value = json.loads(_STEAM_LINK_STORE.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            value.setdefault("pending", {})
            value.setdefault("accounts", {})
            if isinstance(value["pending"], dict) and isinstance(value["accounts"], dict):
                return value
    except (OSError, json.JSONDecodeError):
        pass
    return {"pending": {}, "accounts": {}}


def _steam_write_store_locked(value):
    temp = _STEAM_LINK_STORE.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, _STEAM_LINK_STORE)


def _create_steam_link_url(guild_id, discord_user_id):
    if not guild_id:
        raise ValueError("Steam kann nur über einen Verifizierungsbutton innerhalb eines Discord Serverss verknüpft werden.")
    callback_uri, origin = _steam_callback_config()
    token = secrets.token_urlsafe(32)
    now = time.time()
    with _STEAM_LINK_LOCK:
        store = _steam_read_store_locked()
        store["pending"] = {
            key: row for key, row in store["pending"].items()
            if isinstance(row, dict) and float(row.get("expires_at", 0) or 0) > now - 86400
        }
        store["pending"][token] = {
            "discord_id": str(discord_user_id),
            "guild_id": str(guild_id),
            "created_at": now,
            "expires_at": now + _STEAM_LINK_TTL_SECONDS,
            "status": "pending",
        }
        _steam_write_store_locked(store)
    return origin + "/gaming/steam/start?" + urlencode({"token": token})


def _steam_get_pending(token):
    now = time.time()
    with _STEAM_LINK_LOCK:
        store = _steam_read_store_locked()
        row = store["pending"].get(token)
        if not isinstance(row, dict) or float(row.get("expires_at", 0) or 0) <= now:
            return None
        return dict(row)


def _steam_claim_pending(token):
    now = time.time()
    with _STEAM_LINK_LOCK:
        store = _steam_read_store_locked()
        row = store["pending"].get(token)
        if (
            not isinstance(row, dict)
            or row.get("status") != "pending"
            or float(row.get("expires_at", 0) or 0) <= now
        ):
            return None
        row["status"] = "processing"
        store["pending"][token] = row
        _steam_write_store_locked(store)
        return dict(row)


def _steam_set_pending_status(token, status):
    with _STEAM_LINK_LOCK:
        store = _steam_read_store_locked()
        row = store["pending"].get(token)
        if isinstance(row, dict):
            row["status"] = status
            store["pending"][token] = row
            _steam_write_store_locked(store)


def _steam_profile_name(steam_id):
    """Best-effort lookup of a public Steam profile name; SteamID remains the verified identity."""
    try:
        response = requests.get(
            "https://steamcommunity.com/profiles/" + steam_id,
            params={"xml": "1"},
            timeout=8,
            headers={"User-Agent": "IronWolf account verification"},
        )
        response.raise_for_status()
        match = re.search(r"<steamID><!\[CDATA\[(.*?)\]\]></steamID>", response.text, re.DOTALL)
        if match:
            name = html.unescape(match.group(1)).strip()
            if name:
                return name[:32]
    except requests.RequestException as exc:
        print(f"[STEAM LINK] Public profile name lookup failed: {type(exc).__name__}", flush=True)
    return ""


def _steam_save_verified_account(token, steam_id, username=""):
    """Persist a verified SteamID once; never allow it to be claimed by another Discord user."""
    with _STEAM_LINK_LOCK:
        store = _steam_read_store_locked()
        pending = store["pending"].get(token)
        if not isinstance(pending, dict) or pending.get("status") != "processing":
            return False, "Der Verknüpfungslink wurde bereits verwendet oder ist abgelaufen."
        discord_id = str(pending.get("discord_id", ""))
        guild_id = str(pending.get("guild_id", ""))
        for owner_id, account in store["accounts"].items():
            if isinstance(account, dict) and str(account.get("steam_id")) == steam_id and str(owner_id) != discord_id:
                pending["status"] = "rejected"
                store["pending"][token] = pending
                _steam_write_store_locked(store)
                return False, "Dieses Steam-Konto ist bereits mit einem anderen Discord-Konto verknüpft."
        existing = store["accounts"].get(discord_id)
        if isinstance(existing, dict) and str(existing.get("steam_id")) != steam_id:
            pending["status"] = "rejected"
            store["pending"][token] = pending
            _steam_write_store_locked(store)
            return False, "Dein Discord-Konto ist bereits mit einem anderen Steam-Konto verknüpft. Bitte wende dich an die Serveradministration."
        account = existing if isinstance(existing, dict) else {
            "discord_id": discord_id,
            "steam_id": steam_id,
            "linked_at": datetime.now(timezone.utc).isoformat(),
            "guild_ids": [],
        }
        account["steam_id"] = steam_id
        if username:
            account["username"] = str(username)[:100]
        guild_ids = account.get("guild_ids", [])
        if not isinstance(guild_ids, list):
            guild_ids = []
        if guild_id not in [str(value) for value in guild_ids]:
            guild_ids.append(guild_id)
        account["guild_ids"] = guild_ids
        account["last_verified_at"] = datetime.now(timezone.utc).isoformat()
        store["accounts"][discord_id] = account
        pending["status"] = "linked"
        pending["steam_id"] = steam_id
        store["pending"][token] = pending
        _steam_write_store_locked(store)
        return True, guild_id


async def _steam_grant_guild_role(guild_id, discord_id, nickname=None, provider="Gaming"):
    if not _bot or not _guild_config:
        return "unavailable"
    guild = _bot.get_guild(int(guild_id))
    if guild is None:
        return "unavailable"
    cfg = _guild_config(int(guild_id))
    member = guild.get_member(int(discord_id))
    if member is None:
        try:
            member = await guild.fetch_member(int(discord_id))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return "member_missing"

    nickname_status = "nickname_unchanged"
    nickname_enabled = cfg.get("verified_accounts_nickname_enabled", True)
    safe_nickname = str(nickname or "").strip()[:32]
    if not nickname_enabled:
        nickname_status = "nickname_disabled"
    elif not safe_nickname:
        nickname_status = "nickname_missing"
        print(f"[GAMING LINK] No username available for {provider} nickname in guild {guild_id}; Discord ID {discord_id}", flush=True)
    elif safe_nickname != (member.nick or ""):
        try:
            await member.edit(nick=safe_nickname, reason=f"{provider}-Konto erfolgreich verifiziert")
            nickname_status = "nickname_set"
            print(f"[GAMING LINK] Set {provider} nickname in guild {guild_id} for member {discord_id}", flush=True)
        except (discord.Forbidden, discord.HTTPException) as exc:
            status_code = getattr(getattr(exc, "response", None), "status", None)
            print(
                f"[GAMING LINK] Could not set nickname in guild {guild_id} for member {discord_id}: "
                f"{type(exc).__name__}, HTTP {status_code or 'unknown'}; check Manage Nicknames and role hierarchy",
                flush=True,
            )
            nickname_status = "nickname_failed"

    # Grant every configured gaming-verification role (supports old single-role settings too).
    configured_roles = cfg.get("verified_accounts_role_ids")
    if configured_roles is None:
        legacy_role = cfg.get("verified_accounts_role_id")
        configured_roles = [legacy_role] if legacy_role else []
    elif not isinstance(configured_roles, (list, tuple, set)):
        configured_roles = [configured_roles]
    if not configured_roles:
        role_result = "not_configured"
    else:
        roles_to_add = []
        invalid_roles = []
        already_had_roles = []
        for raw_role_id in configured_roles:
            try:
                role = guild.get_role(int(raw_role_id))
            except (TypeError, ValueError):
                role = None
            if role is None or role.is_default() or role.managed:
                invalid_roles.append(raw_role_id)
            elif role in member.roles:
                already_had_roles.append(role.id)
            else:
                roles_to_add.append(role)
        if roles_to_add:
            try:
                await member.add_roles(*roles_to_add, reason=f"{provider}-Konto erfolgreich über den offiziellen Anbieter verifiziert")
                role_result = "granted"
            except (discord.Forbidden, discord.HTTPException) as exc:
                print(f"[GAMING LINK] Could not grant verification roles in guild {guild_id}: {type(exc).__name__}: {exc}", flush=True)
                role_result = "role_failed"
        elif invalid_roles:
            role_result = "invalid_role"
        elif already_had_roles:
            role_result = "already_has_role"
        else:
            role_result = "not_configured"

    # Make nickname failures visible instead of masking them with a successful role result.
    if nickname_status in {"nickname_failed", "nickname_missing"}:
        return nickname_status
    if nickname_status == "nickname_set":
        return "nickname_set"
    return role_result


@app.route("/gaming/steam/start")
def steam_link_start():
    token = request.args.get("token", "")
    if not token or len(token) > 128:
        abort(400, "Ungültiger oder fehlender Verknüpfungslink.")
    pending = _steam_get_pending(token)
    if not pending or pending.get("status") != "pending":
        return _render("Steam-Verknüpfung", '<section class="card"><h1>Link abgelaufen</h1><p>Dieser Steam-Link ist ungültig, abgelaufen oder wurde bereits verwendet. Starte die Verknüpfung erneut über Discord.</p></section>', 400)
    try:
        callback_uri, origin = _steam_callback_config()
    except ValueError as exc:
        return _render("Steam-Verknüpfung nicht eingerichtet", '<section class="card"><h1>Steam-Verknüpfung nicht eingerichtet</h1><p>' + html.escape(str(exc)) + '</p></section>', 503)
    return_to = callback_uri + "?" + urlencode({"token": token})
    params = {
        "openid.ns": _STEAM_OPENID_NS,
        "openid.mode": "checkid_setup",
        "openid.return_to": return_to,
        "openid.realm": origin + "/",
        "openid.identity": "http://specs.openid.net/auth/2.0/identifier_select",
        "openid.claimed_id": "http://specs.openid.net/auth/2.0/identifier_select",
    }
    return redirect(_STEAM_OPENID_ENDPOINT + "?" + urlencode(params), code=302)


@app.route("/gaming/steam/callback")
def steam_link_callback():
    token = request.args.get("token", "")
    if not token or len(token) > 128:
        abort(400, "Ungültiger oder fehlender Verknüpfungstoken.")
    pending = _steam_get_pending(token)
    if not pending or pending.get("status") != "pending":
        return _render("Steam-Verknüpfung", '<section class="card"><h1>Verknüpfung nicht möglich</h1><p>Der Link ist abgelaufen oder wurde bereits verwendet. Starte den Vorgang erneut über Discord.</p></section>', 400)
    try:
        callback_uri, origin = _steam_callback_config()
    except ValueError as exc:
        return _render("Steam-Verknüpfung nicht eingerichtet", '<section class="card"><h1>Steam-Verknüpfung nicht eingerichtet</h1><p>' + html.escape(str(exc)) + '</p></section>', 503)

    fields = {key: value for key, value in request.args.items() if key.startswith("openid.")}
    claimed_id = fields.get("openid.claimed_id", "")
    identity = fields.get("openid.identity", "")
    endpoint = urlparse(fields.get("openid.op_endpoint", ""))
    return_to = callback_uri + "?" + urlencode({"token": token})
    match = re.fullmatch(r"https?://steamcommunity\.com/openid/id/(\d{15,20})", claimed_id)
    signed_fields = set(fields.get("openid.signed", "").split(","))
    required_signed_fields = {"op_endpoint", "claimed_id", "identity", "return_to", "response_nonce", "assoc_handle"}
    if (
        fields.get("openid.ns") != _STEAM_OPENID_NS
        or fields.get("openid.mode") != "id_res"
        or fields.get("openid.return_to") != return_to
        or identity != claimed_id
        or not match
        or not required_signed_fields.issubset(signed_fields)
        or not fields.get("openid.sig")
        or not fields.get("openid.assoc_handle")
        or not fields.get("openid.response_nonce")
        or endpoint.scheme != "https"
        or endpoint.hostname != "steamcommunity.com"
        or endpoint.path.rstrip("/") not in {"/openid", "/openid/login"}
    ):
        return _render("Steam-Verknüpfung fehlgeschlagen", '<section class="card"><h1>Steam-Anmeldung konnte nicht bestätigt werden</h1><p>Die Rückgabe von Steam war ungültig. Bitte starte den Vorgang erneut über Discord.</p></section>', 400)

    claimed = _steam_claim_pending(token)
    if not claimed:
        return _render("Steam-Verknüpfung", '<section class="card"><h1>Link bereits verwendet</h1><p>Dieser Link wurde bereits verwendet oder ist abgelaufen. Starte den Vorgang erneut über Discord.</p></section>', 400)
    verification_fields = dict(fields)
    verification_fields["openid.mode"] = "check_authentication"
    try:
        response = requests.post(_STEAM_OPENID_ENDPOINT, data=verification_fields, timeout=12)
        response.raise_for_status()
        verified = bool(re.search(r"(?m)^is_valid:true\s*$", response.text.strip()))
    except requests.RequestException as exc:
        print(f"[STEAM LINK] OpenID verification request failed: {type(exc).__name__}", flush=True)
        _steam_set_pending_status(token, "failed")
        return _render("Steam-Verknüpfung fehlgeschlagen", '<section class="card"><h1>Steam ist gerade nicht erreichbar</h1><p>Die Bestätigung konnte nicht geprüft werden. Bitte starte den Vorgang erneut über Discord.</p></section>', 502)
    if not verified:
        _steam_set_pending_status(token, "failed")
        return _render("Steam-Verknüpfung fehlgeschlagen", '<section class="card"><h1>Steam-Anmeldung ungültig</h1><p>Steam konnte die Anmeldung nicht bestätigen. Bitte starte den Vorgang erneut über Discord.</p></section>', 400)

    steam_id = match.group(1)
    steam_username = _steam_profile_name(steam_id)
    saved, result = _steam_save_verified_account(token, steam_id, steam_username)
    if not saved:
        return _render("Steam-Verknüpfung fehlgeschlagen", '<section class="card"><h1>Verknüpfung nicht möglich</h1><p>' + html.escape(result) + '</p></section>', 409)
    guild_id = result
    role_status = "unavailable"
    if _bot and getattr(_bot, "loop", None) and _bot.loop.is_running():
        try:
            future = asyncio.run_coroutine_threadsafe(
                _steam_grant_guild_role(guild_id, pending["discord_id"], nickname=steam_username, provider="Steam"), _bot.loop
            )
            role_status = future.result(timeout=18)
        except Exception as exc:
            print(f"[STEAM LINK] Role assignment failed in guild {guild_id}: {type(exc).__name__}: {exc}", flush=True)
            role_status = "role_failed"
    if role_status == "nickname_failed":
        role_text = "Steam wurde verknüpft, aber Discord hat die Nickname-Änderung abgelehnt. Prüfe „Spitznamen verwalten“ und die Rollen-Hierarchie des Bots; Details stehen in den Railway-Logs."
    elif role_status == "nickname_missing":
        role_text = "Steam wurde verknüpft, aber IronWolf konnte keinen öffentlichen Steam-Profilnamen abrufen. Stelle dein Steam-Profil auf öffentlich und versuche es erneut."
    elif role_status == "nickname_set":
        role_text = "Dein Discord-Nickname wurde auf deinen Steam-Namen gesetzt."
    elif role_status in {"granted", "already_has_role"}:
        role_text = "Die Verifizierungsrolle ist korrekt. Der Nickname war bereits passend oder die Nickname-Automatik ist deaktiviert."
    elif role_status == "not_configured":
        role_text = "Das Steam-Konto ist gespeichert. Im Dashboard ist für diesen Server noch keine Verifizierungsrolle ausgewählt."
    else:
        role_text = "Das Steam-Konto wurde gespeichert, aber die Discord-Aktion konnte nicht vollständig ausgeführt werden. Prüfe Bot-Berechtigungen und Rollen-Hierarchie sowie die Railway-Logs."
    return _render(
        "Steam erfolgreich verknüpft",
        '<section class="card"><h1>✅ Steam erfolgreich verknüpft</h1><p>Deine Steam-ID <code>' +
        html.escape(steam_id) + '</code> wurde sicher deinem Discord-Konto zugeordnet.</p><p>' +
        html.escape(role_text) + '</p><p>Du kannst dieses Fenster jetzt schließen.</p></section>',
    )



_ROBLOX_AUTHORIZE_ENDPOINT = "https://apis.roblox.com/oauth/v1/authorize"
_ROBLOX_TOKEN_ENDPOINT = "https://apis.roblox.com/oauth/v1/token"
_ROBLOX_USERINFO_ENDPOINT = "https://apis.roblox.com/oauth/v1/userinfo"
_ROBLOX_LINK_STORE = data_path("roblox_account_links.json")
_ROBLOX_LINK_LOCK = threading.Lock()
_ROBLOX_LINK_TTL_SECONDS = 600


def _roblox_callback_config():
    client_id = _env("ROBLOX_CLIENT_ID")
    client_secret = _env("ROBLOX_CLIENT_SECRET")
    callback_uri = _env("ROBLOX_REDIRECT_URI")
    parsed = urlparse(callback_uri)
    if not client_id or not client_secret or (
        parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
        or parsed.query or parsed.fragment or parsed.path != "/gaming/roblox/callback"
    ):
        raise ValueError(
            "Roblox ist noch nicht konfiguriert. Prüfe ROBLOX_CLIENT_ID, ROBLOX_CLIENT_SECRET "
            "und ROBLOX_REDIRECT_URI in Railway. Die Redirect-URI muss exakt "
            "https://ironwolf-bot.up.railway.app/gaming/roblox/callback sein."
        )
    return client_id, client_secret, callback_uri


def _roblox_read_store_locked():
    try:
        value = json.loads(_ROBLOX_LINK_STORE.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            value.setdefault("pending", {})
            value.setdefault("accounts", {})
            if isinstance(value["pending"], dict) and isinstance(value["accounts"], dict):
                return value
    except (OSError, json.JSONDecodeError):
        pass
    return {"pending": {}, "accounts": {}}


def _roblox_write_store_locked(value):
    temp = _ROBLOX_LINK_STORE.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, _ROBLOX_LINK_STORE)


def _create_roblox_link_url(guild_id, discord_user_id):
    if not guild_id:
        raise ValueError("Roblox kann nur über einen Verifizierungsbutton innerhalb eines Discord Serverss verknüpft werden.")
    client_id, client_secret, callback_uri = _roblox_callback_config()
    token = secrets.token_urlsafe(32)
    now = time.time()
    with _ROBLOX_LINK_LOCK:
        store = _roblox_read_store_locked()
        store["pending"] = {
            key: row for key, row in store["pending"].items()
            if isinstance(row, dict) and float(row.get("expires_at", 0) or 0) > now - 86400
        }
        store["pending"][token] = {
            "discord_id": str(discord_user_id), "guild_id": str(guild_id),
            "created_at": now, "expires_at": now + _ROBLOX_LINK_TTL_SECONDS, "status": "pending",
        }
        _roblox_write_store_locked(store)
    origin = f"https://{urlparse(callback_uri).netloc}"
    return origin + "/gaming/roblox/start?" + urlencode({"token": token})


def _roblox_claim_pending(token):
    now = time.time()
    with _ROBLOX_LINK_LOCK:
        store = _roblox_read_store_locked()
        row = store["pending"].get(token)
        if not isinstance(row, dict) or row.get("status") != "pending" or float(row.get("expires_at", 0) or 0) <= now:
            return None
        row["status"] = "processing"
        store["pending"][token] = row
        _roblox_write_store_locked(store)
        return dict(row)


def _roblox_set_pending_status(token, status):
    with _ROBLOX_LINK_LOCK:
        store = _roblox_read_store_locked()
        row = store["pending"].get(token)
        if isinstance(row, dict):
            row["status"] = status
            store["pending"][token] = row
            _roblox_write_store_locked(store)


def _roblox_save_verified_account(token, roblox_id, username):
    with _ROBLOX_LINK_LOCK:
        store = _roblox_read_store_locked()
        pending = store["pending"].get(token)
        if not isinstance(pending, dict) or pending.get("status") != "processing":
            return False, "Der Verknüpfungslink wurde bereits verwendet oder ist abgelaufen."
        discord_id = str(pending.get("discord_id", ""))
        guild_id = str(pending.get("guild_id", ""))
        for owner_id, account in store["accounts"].items():
            if isinstance(account, dict) and str(account.get("roblox_id")) == roblox_id and str(owner_id) != discord_id:
                pending["status"] = "rejected"
                store["pending"][token] = pending
                _roblox_write_store_locked(store)
                return False, "Dieses Roblox-Konto ist bereits mit einem anderen Discord-Konto verknüpft."
        existing = store["accounts"].get(discord_id)
        if isinstance(existing, dict) and str(existing.get("roblox_id")) != roblox_id:
            pending["status"] = "rejected"
            store["pending"][token] = pending
            _roblox_write_store_locked(store)
            return False, "Dein Discord-Konto ist bereits mit einem anderen Roblox-Konto verknüpft. Bitte wende dich an die Serveradministration."
        account = existing if isinstance(existing, dict) else {
            "discord_id": discord_id, "roblox_id": roblox_id,
            "linked_at": datetime.now(timezone.utc).isoformat(), "guild_ids": [],
        }
        account["roblox_id"] = roblox_id
        account["username"] = str(username)[:100]
        guild_ids = account.get("guild_ids", [])
        if not isinstance(guild_ids, list):
            guild_ids = []
        if guild_id not in [str(value) for value in guild_ids]:
            guild_ids.append(guild_id)
        account["guild_ids"] = guild_ids
        account["last_verified_at"] = datetime.now(timezone.utc).isoformat()
        store["accounts"][discord_id] = account
        pending["status"] = "linked"
        pending["roblox_id"] = roblox_id
        store["pending"][token] = pending
        _roblox_write_store_locked(store)
        return True, guild_id


@app.route("/gaming/roblox/start")
def roblox_link_start():
    token = request.args.get("token", "")
    if not token or len(token) > 128:
        abort(400, "Ungültiger oder fehlender Verknüpfungstoken.")
    with _ROBLOX_LINK_LOCK:
        row = _roblox_read_store_locked()["pending"].get(token)
        pending = dict(row) if isinstance(row, dict) else None
    if not pending or pending.get("status") != "pending" or float(pending.get("expires_at", 0) or 0) <= time.time():
        return _render("Roblox-Verknüpfung", '<section class="card"><h1>Link abgelaufen</h1><p>Starte die Verknüpfung erneut über Discord.</p></section>', 400)
    try:
        client_id, client_secret, callback_uri = _roblox_callback_config()
    except ValueError as exc:
        return _render("Roblox nicht eingerichtet", '<section class="card"><h1>Roblox nicht eingerichtet</h1><p>' + html.escape(str(exc)) + '</p></section>', 503)
    # The state token is opaque, one-time, and bound to the Discord user and guild.
    authorize_url = _ROBLOX_AUTHORIZE_ENDPOINT + "?" + urlencode({
        "client_id": client_id, "redirect_uri": callback_uri,
        "response_type": "code", "scope": "openid profile", "state": token,
    })
    return redirect(authorize_url, code=302)


@app.route("/gaming/roblox/callback")
def roblox_link_callback():
    token = request.args.get("state", "")
    if not token or len(token) > 128:
        abort(400, "Ungültiger oder fehlender Verknüpfungstoken.")
    if request.args.get("error"):
        return _render("Roblox-Anmeldung abgebrochen", '<section class="card"><h1>Anmeldung abgebrochen</h1><p>Du hast die Roblox-Verknüpfung abgebrochen. Starte sie erneut über Discord.</p></section>', 400)
    code = request.args.get("code", "")
    if not code or len(code) > 4096:
        abort(400, "Roblox hat keinen gültigen Autorisierungscode zurückgegeben.")
    try:
        client_id, client_secret, callback_uri = _roblox_callback_config()
    except ValueError as exc:
        return _render("Roblox nicht eingerichtet", '<section class="card"><h1>Roblox nicht eingerichtet</h1><p>' + html.escape(str(exc)) + '</p></section>', 503)
    pending = _roblox_claim_pending(token)
    if not pending:
        return _render("Roblox-Verknüpfung", '<section class="card"><h1>Link ungültig oder abgelaufen</h1><p>Starte die Verknüpfung erneut über Discord.</p></section>', 400)
    try:
        token_response = requests.post(
            _ROBLOX_TOKEN_ENDPOINT,
            data={"grant_type": "authorization_code", "code": code, "client_id": client_id,
                  "client_secret": client_secret, "redirect_uri": callback_uri},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        token_response.raise_for_status()
        access_token = token_response.json().get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise ValueError("Roblox token response had no access token")
        profile_response = requests.get(
            _ROBLOX_USERINFO_ENDPOINT,
            headers={"Authorization": "Bearer " + access_token},
            timeout=15,
        )
        profile_response.raise_for_status()
        profile = profile_response.json()
        subject = str(profile.get("sub", ""))
        username = profile.get("preferred_username") or profile.get("nickname") or profile.get("name") or ""
        if not re.fullmatch(r"\d{1,30}", subject):
            raise ValueError("Roblox userinfo returned an invalid subject")
    except (requests.RequestException, ValueError, json.JSONDecodeError) as exc:
        print(f"[ROBLOX LINK] OAuth verification failed: {type(exc).__name__}", flush=True)
        _roblox_set_pending_status(token, "failed")
        return _render("Roblox-Verknüpfung fehlgeschlagen", '<section class="card"><h1>Roblox-Anmeldung konnte nicht bestätigt werden</h1><p>Prüfe die OAuth-App und die exakt eingetragene Redirect-URI. Bitte starte den Vorgang erneut.</p></section>', 502)
    saved, result = _roblox_save_verified_account(token, subject, username)
    if not saved:
        return _render("Roblox-Verknüpfung fehlgeschlagen", '<section class="card"><h1>Verknüpfung nicht möglich</h1><p>' + html.escape(result) + '</p></section>', 409)
    guild_id = result
    role_status = "unavailable"
    if _bot and getattr(_bot, "loop", None) and _bot.loop.is_running():
        try:
            future = asyncio.run_coroutine_threadsafe(
                _steam_grant_guild_role(guild_id, pending["discord_id"], nickname=username, provider="Roblox"), _bot.loop
            )
            role_status = future.result(timeout=18)
        except Exception as exc:
            print(f"[ROBLOX LINK] Role assignment failed in guild {guild_id}: {type(exc).__name__}", flush=True)
            role_status = "role_failed"
    if role_status == "nickname_failed":
        role_text = "Roblox wurde verknüpft, aber Discord hat die Nickname-Änderung abgelehnt. Prüfe „Spitznamen verwalten“ und die Rollen-Hierarchie des Bots; Details stehen in den Railway-Logs."
    elif role_status == "nickname_missing":
        role_text = "Roblox wurde verknüpft, aber IronWolf hat keinen Roblox-Nutzernamen für die Nickname-Änderung erhalten. Prüfe die Roblox-Profilantwort und die Railway-Logs."
    elif role_status == "nickname_set":
        role_text = "Dein Discord-Nickname wurde auf deinen Roblox-Namen gesetzt."
    elif role_status in {"granted", "already_has_role"}:
        role_text = "Die Verifizierungsrolle ist korrekt. Der Nickname war bereits passend oder die Nickname-Automatik ist deaktiviert."
    elif role_status == "not_configured":
        role_text = "Das Roblox-Konto wurde gespeichert. Wähle im Dashboard eine Verifizierungsrolle aus."
    else:
        role_text = "Das Roblox-Konto wurde gespeichert, aber die Discord-Aktion konnte nicht vollständig ausgeführt werden. Prüfe Bot-Berechtigungen und Rollen-Hierarchie sowie die Railway-Logs."
    return _render(
        "Roblox erfolgreich verknüpft",
        '<section class="card"><h1>✅ Roblox erfolgreich verknüpft</h1><p>Dein Roblox-Konto <strong>' +
        html.escape(str(username) or subject) + '</strong> (ID <code>' + html.escape(subject) +
        '</code>) wurde deinem Discord-Konto zugeordnet.</p><p>' + html.escape(role_text) +
        '</p><p>Du kannst dieses Fenster jetzt schließen.</p></section>',
    )



class _GamingAccountVerifyView(discord.ui.View):
    """Gaming provider buttons for official provider-owned account verification flows."""
    def __init__(self, enabled_platforms):
        super().__init__(timeout=None)
        details = {
            "steam": ("🚂 Steam", discord.ButtonStyle.secondary,
                "Steam-Verknüpfung ist noch nicht aktiviert. Dafür muss Steam OpenID mit einer sicheren Rückleitungs-URL eingerichtet werden. Gib dein Steam-Passwort niemals im Discord ein."),
            "roblox": ("🟥 Roblox", discord.ButtonStyle.danger,
                "Roblox sicher verknüpfen: Melde dich auf der offiziellen Roblox-Seite an. Teile niemals dein Passwort oder einen Login-Code."),
        }
        for key, (label, style, explanation) in details.items():
            if key not in set(enabled_platforms):
                continue
            button = discord.ui.Button(label=label, style=style, custom_id="gaming_verify:" + key)
            async def callback(interaction, message=explanation, provider=key):
                # Acknowledge immediately so Discord's 3-second interaction deadline is met.
                await interaction.response.defer(ephemeral=True, thinking=True)
                if provider == "roblox":
                    try:
                        link_url = _create_roblox_link_url(interaction.guild_id, interaction.user.id)
                    except ValueError as exc:
                        await interaction.followup.send(str(exc), ephemeral=True)
                        return
                    except Exception as exc:
                        print(f"[ROBLOX LINK] Could not create link: {type(exc).__name__}: {exc}", flush=True)
                        await interaction.followup.send(
                            "Der Roblox-Link konnte gerade nicht erstellt werden. Bitte prüfe die Railway-Variablen und versuche es später erneut.",
                            ephemeral=True,
                        )
                        return
                    await interaction.followup.send(
                        "🔐 **Roblox sicher verknüpfen**\nÖffne den Link und melde dich direkt bei Roblox an. IronWolf fragt nur die Roblox-Profilidentität ab, niemals dein Passwort. Der Link ist 10 Minuten gültig und einmalig.\n"
                        + link_url,
                        ephemeral=True,
                    )
                    return
                if provider == "steam":
                    try:
                        link_url = _create_steam_link_url(interaction.guild_id, interaction.user.id)
                    except ValueError as exc:
                        await interaction.followup.send(str(exc), ephemeral=True)
                        return
                    except Exception as exc:
                        print(f"[STEAM LINK] Could not create link: {type(exc).__name__}: {exc}", flush=True)
                        await interaction.followup.send(
                            "Der Steam-Link konnte gerade nicht erstellt werden. Bitte versuche es später erneut.",
                            ephemeral=True,
                        )
                        return
                    await interaction.followup.send(
                        "🔐 **Steam sicher verknüpfen**\nÖffne diesen Link und melde dich direkt bei Steam an. Dein Steam-Passwort wird niemals an IronWolf übermittelt. "
                        "Der Link ist 10 Minuten gültig und kann nur einmal verwendet werden.\n" + link_url,
                        ephemeral=True,
                    )
                    return
                await interaction.followup.send(message, ephemeral=True)
            button.callback = callback
            self.add_item(button)


def _env(name):
    return (os.getenv(name) or "").strip()


def _oauth_ready():
    return all(_env(key) for key in ("DISCORD_CLIENT_ID", "DISCORD_CLIENT_SECRET", "DASHBOARD_REDIRECT_URI"))


def _csrf_ok():
    supplied = request.form.get("csrf_token", "")
    expected = session.get("csrf_token", "")
    return bool(supplied and expected and hmac.compare_digest(supplied, expected))


def _csrf_field():
    token = session.setdefault("csrf_token", secrets.token_urlsafe(32))
    return f'<input type="hidden" name="csrf_token" value="{html.escape(token, quote=True)}">'


def _page(title, body):
    logged_in = bool(session.get("access_token"))
    is_premium_admin = str(session.get("user_id", "")) == "1493110334280564807"
    custom_theme_css = ""
    match = re.match(r"^/guild/(\d+)", request.path)
    if match and _guild_config:
        try:
            theme_cfg = _guild_config(int(match.group(1)))
            raw_color = str(theme_cfg.get("dashboard_brand_color", "8B9DFF")).strip().lstrip("#")
            if re.fullmatch(r"[0-9a-fA-F]{6}", raw_color):
                rgb = [int(raw_color[i:i + 2], 16) for i in (0, 2, 4)]
                custom_theme_css = (
                    ":root{--accent:#" + raw_color + ";--accent2:#" + raw_color + ";}"
                    ".card::before{background:linear-gradient(90deg,#" + raw_color + ",transparent)}"
                    ".pill{background:rgba(" + ",".join(map(str, rgb)) + ",.12);"
                    "border-color:rgba(" + ",".join(map(str, rgb)) + ",.35)}"
                )
        except Exception:
            custom_theme_css = ""
    nav = (
        '<a class="skip-link" href="#main-content">Skip to content</a><nav aria-label="Main navigation"><a href="/" class="brand-mark">🐺 IRONWOLF <span class="muted">/ CONTROL CENTER</span></a>'
        + ('<a class="nav-server" href="/">🎮 Switch server</a>' if logged_in else '')
        + ('<a class="nav-server" href="/guild/' + match.group(1) + '?section=general">⚙️ Server Settings</a>' if logged_in and match else '')
        + ('<a href="/guild/' + match.group(1) + '/wolfpulse">🐺 WolfPulse</a>' if logged_in and match else '')
        + ('<a href="/guild/' + match.group(1) + '/wolfshield">🛡️ WolfShield</a>' if logged_in and match else '')
        + ('<a href="/guild/' + match.group(1) + '/wolfmirror">🧪 WolfMirror</a>' if logged_in and match else '')
        + ('<a href="/guild/' + match.group(1) + '/wolfchronicle">📚 WolfChronicle</a>' if logged_in and match else '')
        + '<a href="/feedback">💬 Feedback</a>'
        + ('<a href="/admin/premium-servers">👑 Premium Servers</a>' if logged_in and is_premium_admin else '')
        + '<a href="https://discord.gg/T3AYmFmbrS" target="_blank" rel="noopener noreferrer">Discord ↗</a>'
        + '<a href="https://king-snowy-at.base44.app/" target="_blank" rel="noopener noreferrer">🌐 Website ↗</a>'
        + '<a class="nav-download" href="https://github.com/kingsnowy14-cloud/the-isle-signin-bot/releases/download/desktop-latest/IronWolf-Setup.exe">⬇️ Desktop App</a>'
        + ('<form class="nav-logout" method="post" action="/logout">' + _csrf_field() + '<button type="submit" class="nav-logout-button">Log out</button></form>' if logged_in else '<a href="/login">Log in with Discord</a>')
        + '</nav>'
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)} · IronWolf</title>
<style>
:root{{--bg:#080b12;--panel:#101725;--panel2:#151f30;--text:#f2f5fb;--muted:#98a7bd;--accent:#72c7ff;--accent2:#b7e5ff;--border:rgba(154,177,211,.17);--shadow:0 22px 70px rgba(0,0,0,.34);--glow:rgba(91,141,255,.14);}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth;scroll-padding-top:100px}}
body{{margin:0;background:radial-gradient(ellipse at 5% 0%,rgba(91,141,255,.16),transparent 34%),radial-gradient(ellipse at 94% 4%,rgba(114,199,255,.13),transparent 28%),linear-gradient(180deg,#0b1020 0%,var(--bg) 58%,#070910 100%);color:var(--text);font:15px/1.7 Inter,ui-sans-serif,system-ui,-apple-system,sans-serif;min-height:100vh}}
nav{{position:sticky;top:0;z-index:10;display:flex;gap:18px;align-items:center;padding:14px max(20px,calc((100vw - 1240px)/2));border-bottom:1px solid rgba(154,177,211,.15);background:rgba(8,11,18,.82);backdrop-filter:blur(22px);box-shadow:0 8px 35px rgba(0,0,0,.16)}}
nav a:first-child{{font-weight:1000;font-size:19px;letter-spacing:1.4px;text-decoration:none;color:var(--text);text-shadow:0 0 22px rgba(91,141,255,.35)}}nav a:first-child::first-letter{{color:var(--accent)}}nav span{{flex:1}}nav a:not(:first-child){{padding:8px 13px;border:1px solid var(--border);border-radius:11px;text-decoration:none;font-weight:800;background:linear-gradient(145deg,rgba(255,255,255,.055),rgba(255,255,255,.015));transition:transform .18s,border-color .18s,box-shadow .18s}}nav a:not(:first-child):hover{{transform:translateY(-2px);border-color:rgba(114,199,255,.7);box-shadow:0 0 20px rgba(91,141,255,.12);color:var(--accent)}}nav a.nav-server{{background:linear-gradient(120deg,rgba(91,141,255,.2),rgba(114,199,255,.12));border-color:rgba(125,161,231,.42);color:#e9f0ff}}a{{color:var(--accent);text-underline-offset:4px}}
main{{max-width:1240px;margin:42px auto;padding:0 24px 54px}}main>p:last-child{{border-top:1px solid var(--border);padding-top:18px;margin-top:38px}}h1,h2,h3{{line-height:1.22;letter-spacing:-.4px}}h1{{font-size:clamp(30px,4vw,46px);margin:0 0 12px;font-weight:950;letter-spacing:-1px}}h2{{font-size:21px;margin:0 0 13px;font-weight:850}}h3{{font-size:14px;margin:22px 0 9px;color:var(--accent);text-transform:uppercase;letter-spacing:1px}}main>p.muted{{margin-top:0;margin-bottom:26px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,340px),1fr));gap:22px;align-items:start}}.grid>.card,.grid>div>.card{{margin:0 0 20px}}
.dashboard-metrics{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,180px),1fr));gap:12px;margin:18px 0 26px}}.dashboard-metric{{border:1px solid var(--border);border-radius:14px;padding:16px;background:rgba(13,20,34,.82);min-width:0}}.dashboard-metric strong{{display:block;font-size:25px;line-height:1.25;color:var(--accent2);overflow-wrap:anywhere}}.dashboard-metric span{{display:block;color:var(--muted);font-size:12px;font-weight:750;margin-top:5px}}.dashboard-group-title{{margin:28px 0 10px;font-size:17px}}.dashboard-card{{display:block;text-decoration:none;min-height:145px}}.dashboard-card h2{{font-size:18px;margin-top:4px}}.dashboard-card p{{margin-bottom:12px}}.dashboard-card .card-link{{font-weight:850;font-size:13px}}
.card{{background:linear-gradient(145deg,rgba(19,28,44,.94),rgba(12,17,29,.97));border:1px solid var(--border);border-radius:20px;padding:25px;margin:20px 0;box-shadow:var(--shadow);min-width:0;position:relative;overflow:hidden;backdrop-filter:blur(14px)}}.card::before{{content:"";position:absolute;inset:0 0 auto;height:2px;background:linear-gradient(90deg,var(--accent),#7e9ee9 42%,transparent);opacity:.9}}.card::after{{content:"";position:absolute;right:-90px;top:-115px;width:190px;height:190px;border-radius:50%;background:var(--glow);filter:blur(18px);pointer-events:none}}.card h1{{margin-bottom:14px}}.card p{{color:var(--muted)}}.card ul{{padding-left:22px;color:var(--muted)}}.card li{{padding:3px 0}}
.grid>a.card{{transition:transform .2s ease,border-color .2s ease,box-shadow .2s ease}}.grid>a.card:hover{{transform:translateY(-4px);border-color:rgba(114,199,255,.45);box-shadow:0 28px 75px rgba(0,0,0,.4)}}
label{{display:block;font-weight:750;margin:15px 0 7px;color:#e1e8f3}}input[type=text],input[type=password],select,textarea{{width:100%;padding:12px 14px;border:1px solid rgba(154,177,211,.23);border-radius:12px;background:rgba(5,9,17,.78);color:var(--text);font:inherit;outline:none;transition:border-color .18s,box-shadow .18s,background .18s}}input:focus,select:focus,textarea:focus{{border-color:rgba(114,199,255,.75);background:#0b1120;box-shadow:0 0 0 4px rgba(114,199,255,.1)}}textarea{{min-height:110px;resize:vertical}}input::placeholder,textarea::placeholder{{color:#718099}}
button,.button{{display:inline-flex;align-items:center;justify-content:center;gap:8px;border:1px solid rgba(255,255,255,.12);border-radius:12px;padding:11px 17px;background:linear-gradient(125deg,#9cddff,#438fd0);color:#07111e;font:inherit;font-weight:900;cursor:pointer;text-decoration:none;margin-top:15px;box-shadow:0 8px 24px rgba(201,148,62,.13);transition:transform .16s,filter .16s,box-shadow .16s}}button:hover,.button:hover{{transform:translateY(-2px);filter:brightness(1.07);box-shadow:0 12px 28px rgba(201,148,62,.2)}}button.secondary,.button.secondary{{background:rgba(30,43,63,.8);border-color:var(--border);color:var(--text);box-shadow:none}}button.secondary:hover,.button.secondary:hover{{border-color:rgba(114,199,255,.5);color:var(--accent)}}button:focus-visible,.button:focus-visible,a:focus-visible{{outline:2px solid var(--accent);outline-offset:4px}}
.muted,small{{color:var(--muted)}}.pill{{display:inline-flex;align-items:center;gap:7px;padding:5px 11px;border-radius:999px;background:rgba(114,199,255,.09);border:1px solid rgba(114,199,255,.25);color:#b7e5ff;font-size:10px;font-weight:900;letter-spacing:1.25px;text-transform:uppercase}}
.row{{display:flex;gap:14px;align-items:center;justify-content:space-between;border-bottom:1px solid rgba(154,177,211,.12);padding:13px 0}}.row:last-child{{border:0}}.row strong{{overflow-wrap:anywhere}}.check{{display:flex;gap:9px;align-items:center;flex-shrink:0}}.check label{{margin:0;font-weight:650;color:var(--muted)}}.check input{{width:18px;height:18px;accent-color:var(--accent);cursor:pointer}}
.notice{{padding:16px 18px;border-radius:14px;background:linear-gradient(110deg,rgba(91,141,255,.08),rgba(114,199,255,.055));border:1px solid rgba(154,177,211,.2);margin:19px 0;color:#dce6f5}}code{{overflow-wrap:anywhere;background:#080d17;border:1px solid var(--border);border-radius:7px;padding:3px 6px;font-size:.92em}}form{{margin-top:12px}}form button{{width:auto}}.card form>button{{margin-top:20px}}select{{cursor:pointer;appearance:none;-webkit-appearance:none;padding-right:42px;background-color:#0c1322;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='%23aebaff' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E");background-repeat:no-repeat;background-position:right 14px center;background-size:16px}}select option{{background:#111827;color:#f3f6fc}}select optgroup{{background:#111827;color:#aebaff}}select[multiple]{{appearance:auto;-webkit-appearance:auto;padding-right:12px;background-image:none}}
.section-nav{{display:flex;flex-wrap:wrap;gap:9px;margin:20px 0 25px;padding:12px;background:rgba(12,18,30,.7);border:1px solid var(--border);border-radius:16px}}.section-nav a{{display:inline-flex;padding:8px 12px;border-radius:10px;text-decoration:none;font-size:13px;font-weight:800;background:rgba(255,255,255,.025);border:1px solid transparent;transition:background .15s,border-color .15s}}.section-nav a:hover{{border-color:rgba(114,199,255,.4);background:rgba(114,199,255,.07)}}section[id]{{scroll-margin-top:100px}}
.command-groups{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,280px),1fr));gap:14px;align-items:start}}.command-group{{border:1px solid var(--border);background:rgba(7,12,21,.5);border-radius:15px;padding:14px;min-width:0}}.command-group h3{{margin:2px 0 10px;padding-bottom:10px;border-bottom:1px solid var(--border)}}.command-group .row{{gap:8px;padding:10px 0}}.command-group .row strong{{font-size:13px;overflow-wrap:anywhere}}.command-group .check label{{font-size:12px}}
@media(max-width:650px){{nav{{padding:12px 15px;gap:10px}}nav a:first-child{{font-size:15px}}main{{margin:25px auto;padding:0 14px 34px}}.card{{padding:19px;border-radius:16px}}.row{{align-items:flex-start}}.section-nav{{gap:6px;padding:9px}}.section-nav a{{font-size:12px;padding:7px 9px}}button,.button{{max-width:100%;white-space:normal}}}}
\n/* Gaming HUD look */\nbody{{background-image:radial-gradient(ellipse at 12% 0%,rgba(47,112,255,.22),transparent 35%),radial-gradient(ellipse at 90% 12%,rgba(0,255,190,.075),transparent 28%),linear-gradient(180deg,#080d19 0%,#070a12 62%,#04060b 100%)}}\n.card{{border-radius:16px;border-color:rgba(103,143,208,.22);background:linear-gradient(145deg,rgba(16,27,48,.96),rgba(8,14,26,.98));box-shadow:0 16px 45px rgba(0,0,0,.38),inset 0 1px 0 rgba(255,255,255,.025)}}\n.card::before{{height:3px;background:linear-gradient(90deg,#53a0ff,#438fd0,#72c7ff,transparent)}}\n.pill{{background:rgba(62,133,255,.12);border-color:rgba(83,160,255,.32);color:#a9d0ff;letter-spacing:1.4px}}\nh1{{text-shadow:0 0 34px rgba(65,129,255,.16)}}\n.button{{background:linear-gradient(120deg,#73b7ff,#9b8cff 55%,#64efd0);color:#07111e;border:0;box-shadow:0 8px 26px rgba(72,136,255,.18)}}\n.button:hover,button:hover{{box-shadow:0 10px 30px rgba(72,136,255,.27)}}\ninput[type=file]{{width:100%;padding:12px;border:1px dashed rgba(103,164,255,.45);border-radius:12px;background:rgba(32,66,112,.12);color:var(--text)}}\n@media(max-width:760px){{nav{{flex-wrap:wrap}}nav span{{display:none}}nav a:first-child{{width:100%}}}}
/* Dashboard layout: persistent module sidebar with a focused content area */
@media(min-width:900px){{
 body{{padding-left:246px}}
 nav:not(.module-nav){{position:sticky;top:0;inset:auto;width:100%;height:auto;overflow:visible;display:flex;flex-direction:row;flex-wrap:wrap;align-items:center;gap:8px;padding:11px 22px;background:rgba(11,16,32,.96);border-right:0;border-bottom:1px solid var(--border);box-shadow:0 4px 18px rgba(0,0,0,.12)}}
 nav:not(.module-nav) a:first-child{{width:auto;padding:7px 10px;margin:0 auto 0 0;border:0;font-size:16px}}
 nav:not(.module-nav) a:not(:first-child){{display:inline-flex;width:auto;min-height:34px;padding:7px 10px;border:1px solid transparent;border-radius:9px;background:transparent;font-size:12px;box-shadow:none;transform:none}}
 nav:not(.module-nav) .nav-logout{{margin:0;padding:0;border:0}}
 .module-nav{{position:fixed;z-index:9;inset:64px auto 0 0;width:246px;height:calc(100vh - 64px);overflow-y:auto;overflow-x:hidden;display:flex;flex-direction:column;align-items:stretch;gap:4px;flex-wrap:nowrap;margin:0;padding:16px 12px;border:0;border-right:1px solid var(--border);border-radius:0;background:rgba(10,15,29,.98)}}
 .module-nav-title{{padding:7px 10px 10px}}
 .module-nav-link{{width:100%;min-height:36px;white-space:normal}}
 main{{max-width:1440px;margin:30px auto;padding:0 34px 52px}}
}}
@media(max-width:899px){{
 body{{padding-left:0}}
 nav:not(.module-nav){{position:sticky;inset:auto;width:auto;height:auto;overflow:visible;flex-direction:row;flex-wrap:wrap;align-items:center;padding:11px 14px}}
 nav:not(.module-nav) a:first-child{{width:100%;padding-bottom:8px;margin-bottom:4px}}
 nav:not(.module-nav) .nav-logout{{margin:0}}
 .module-nav{{position:relative;width:100%;height:auto;max-height:220px;overflow-x:auto;overflow-y:hidden;flex-direction:row;align-items:center;flex-wrap:nowrap;margin:0 0 18px;padding:10px;border:1px solid var(--border);border-radius:13px}}
 .module-nav-link{{width:auto;white-space:nowrap}}
}}
\n@media(prefers-reduced-motion:reduce){{*,*::before,*::after{{scroll-behavior:auto!important;transition:none!important;animation:none!important}}}}
/* GalaxyBot-inspired clean module dashboard */
:root{{--bg:#0b1020;--panel:#111827;--panel2:#182235;--text:#f3f6fc;--muted:#9caec5;--accent:#8b9dff;--accent2:#c7d0ff;--border:rgba(163,177,211,.15);--shadow:0 12px 34px rgba(0,0,0,.22);--glow:rgba(139,157,255,.10)}}
body{{background:radial-gradient(ellipse at 50% -12%,rgba(105,123,255,.12),transparent 48%),#0b1020;font-size:14px;line-height:1.65}}
nav{{gap:10px;padding-top:11px;padding-bottom:11px;background:rgba(11,16,32,.92);border-bottom:1px solid var(--border);box-shadow:0 4px 18px rgba(0,0,0,.12)}}
nav a:first-child{{font-size:16px;letter-spacing:.8px;text-shadow:none}}
nav a:not(:first-child){{padding:7px 11px;border-radius:9px;background:transparent;border-color:transparent;font-size:13px;box-shadow:none}}
nav a:not(:first-child):hover{{background:rgba(139,157,255,.09);border-color:rgba(139,157,255,.22);box-shadow:none;color:#d7ddff}}
main{{max-width:1180px;margin:30px auto;padding:0 22px 48px}}
h1{{font-size:clamp(28px,3.3vw,38px);letter-spacing:-.8px;text-shadow:none}}
h2{{font-size:19px;letter-spacing:-.2px}}
h3{{font-size:12px;letter-spacing:.8px;color:#aebaff}}
.card{{border-radius:14px;padding:22px;margin:16px 0;background:#111827;border-color:var(--border);box-shadow:0 8px 24px rgba(0,0,0,.16);backdrop-filter:none}}
.card::before{{height:1px;background:linear-gradient(90deg,rgba(139,157,255,.6),rgba(139,157,255,0));opacity:.65}}
.card::after{{display:none}}
.card p{{color:#aab7ca}}
.grid{{gap:15px}}
.grid>.card,.grid>div>.card{{margin:0 0 15px}}
.grid>a.card{{min-height:132px;transition:transform .16s,border-color .16s,background .16s}}
.grid>a.card:hover{{transform:translateY(-2px);border-color:rgba(139,157,255,.42);background:#151e30;box-shadow:0 12px 28px rgba(0,0,0,.22)}}
.dashboard-metric{{background:#111827;border-color:var(--border);border-radius:12px}}
.dashboard-metric strong{{color:#d0d6ff;font-size:23px}}
.dashboard-group-title{{margin:26px 0 11px;font-size:16px;font-weight:850}}
.pill{{background:rgba(139,157,255,.09);border-color:rgba(139,157,255,.23);color:#c7ceff;letter-spacing:.9px}}
button,.button{{border-radius:9px;padding:10px 15px;background:#818cf8;color:#0b1020;box-shadow:none;font-size:13px}}
.button:hover,button:hover{{background:#a5b4fc;box-shadow:none;filter:none}}
button.secondary,.button.secondary{{background:#1b2639;border-color:var(--border);color:#e4e9f5}}
button.secondary:hover,.button.secondary:hover{{background:#24314a;border-color:rgba(139,157,255,.4);color:#fff}}
input[type=text],input[type=password],select,textarea{{border-radius:9px;background:#0c1322;border-color:rgba(163,177,211,.2);padding:11px 12px}}
input:focus,select:focus,textarea:focus{{border-color:#818cf8;box-shadow:0 0 0 3px rgba(129,140,248,.12)}}
label{{font-size:13px;margin:13px 0 6px}}
.row{{border-color:rgba(163,177,211,.11)}}
.notice{{border-radius:10px}}
table{{border-collapse:collapse;width:100%;overflow:hidden;border-radius:10px}}
th,td{{border-color:var(--border);padding:10px 12px}}
@media(max-width:760px){{main{{margin:20px auto;padding:0 14px 34px}}.card{{padding:17px;border-radius:12px}}nav{{gap:6px}}nav a:not(:first-child){{font-size:12px;padding:6px 8px}}}}
.select-search-wrap{{margin:8px 0 7px;position:relative}}
.select-search-wrap input{{width:100%;padding:9px 12px 9px 36px;border:1px solid var(--border);border-radius:9px;background:#0c1322;color:var(--text);font:inherit;font-size:13px}}
.select-search-wrap:before{{content:"⌕";position:absolute;left:12px;top:3px;color:var(--muted);font-size:21px;pointer-events:none}}
.select-search-count{{display:block;color:var(--muted);font-size:11px;margin:3px 2px 7px}}
.role-picker{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,230px),1fr));gap:8px;margin-top:10px}}
.role-option{{display:flex;align-items:center;gap:10px;padding:10px 12px;margin:0;border:1px solid var(--border);border-radius:10px;background:rgba(255,255,255,.02);cursor:pointer;font-size:13px;min-width:0;overflow-wrap:anywhere}}
.role-option:hover{{border-color:rgba(139,157,255,.42);background:rgba(139,157,255,.06)}}
.role-option input{{width:17px;height:17px;flex-shrink:0;accent-color:#818cf8}}
.role-option[hidden]{{display:none}}
.role-picker-tools{{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:8px 0}}
.role-picker-tools button{{margin:0;padding:7px 10px}}
.selection-summary{{font-size:12px;color:var(--muted);margin-left:auto}}
/* Professional navigation, accessibility and responsive layouts */
nav{{flex-wrap:wrap;row-gap:8px;column-gap:8px;min-height:62px}}
nav .brand-mark{{margin-right:auto;white-space:nowrap}}
nav .nav-logout{{display:inline-flex;margin:0}}
nav .nav-logout input{{display:none}}
nav .nav-logout-button{{margin:0;padding:7px 11px;border-radius:9px;background:transparent;border:1px solid transparent;color:var(--text);font:inherit;font-size:12px;font-weight:800;box-shadow:none}}
nav .nav-logout-button:hover{{background:rgba(139,157,255,.09);border-color:rgba(139,157,255,.22);color:#fff;transform:none;box-shadow:none}}
nav a:not(:first-child){{font-size:12px;line-height:1.35;white-space:nowrap}}
nav a.nav-download{{background:rgba(129,140,248,.12);border-color:rgba(129,140,248,.28);color:#d8dcff}}
nav a:focus-visible,.skip-link:focus{{outline:2px solid var(--accent);outline-offset:3px}}
.skip-link{{position:fixed;z-index:100;top:8px;left:8px;padding:10px 14px;border-radius:9px;background:#fff;color:#111827;font-weight:800;transform:translateY(-150%)}}
.skip-link:focus{{transform:translateY(0)}}
main{{min-width:0}}
input[type=email],input[type=number],input[type=date],input[type=time],input[type=search],input[type=url],input[type=tel]{{width:100%;max-width:100%;padding:11px 12px;border:1px solid rgba(163,177,211,.2);border-radius:9px;background:#0c1322;color:var(--text);font:inherit}}
input[type=checkbox],input[type=radio]{{accent-color:#818cf8}}
button:disabled,button[disabled],.button[aria-disabled=true]{{opacity:.55;cursor:not-allowed;transform:none;box-shadow:none}}
button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible{{outline:2px solid var(--accent);outline-offset:2px}}
.card,.grid,.command-group,.dashboard-metric{{min-width:0}}
table{{display:block;max-width:100%;overflow-x:auto;-webkit-overflow-scrolling:touch}}
th{{text-align:left;font-size:12px;color:#c7ceff;white-space:nowrap}}
td{{vertical-align:top;overflow-wrap:anywhere}}
@media(max-width:760px){{nav{{position:sticky;gap:6px;padding:10px 12px}}nav .brand-mark{{width:100%;margin:0 0 2px}}nav a:not(:first-child){{font-size:11px;padding:6px 8px}}}}
@media(max-width:420px){{nav a:not(:first-child){{white-space:normal;flex:1 1 auto;text-align:center}}main{{padding-left:12px;padding-right:12px}}}}
/* IronWolf signature look: editorial hero, clean metrics and grouped module cards */
.iw-hero{{padding:34px 38px;margin:8px 0 20px;border:1px solid rgba(139,157,255,.24);border-radius:22px;background:radial-gradient(ellipse at 85% 20%,rgba(99,102,241,.25),transparent 38%),linear-gradient(125deg,#111b32,#15182b 60%,#20182d);box-shadow:0 18px 55px rgba(0,0,0,.2)}}
.iw-hero h1{{font-size:clamp(28px,4vw,42px);margin:10px 0 6px;letter-spacing:-.04em}}.iw-hero p{{color:#c4cbe0;margin:0 0 12px}}.iw-hero small,.iw-kicker{{font-size:10px;letter-spacing:.16em;color:#aeb9ff;font-weight:900}}
.iw-metrics{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:11px;margin:0 0 32px}}.iw-metrics>div{{padding:15px 17px;border:1px solid var(--border);border-radius:14px;background:rgba(255,255,255,.025)}}.iw-metrics small,.iw-metrics strong{{display:block}}.iw-metrics small{{color:var(--muted);font-size:11px}}.iw-metrics strong{{font-size:25px;margin-top:5px}}
.iw-section{{margin:0 0 34px}}.iw-heading{{display:flex;justify-content:space-between;align-items:flex-end;gap:14px;margin:0 0 14px}}.iw-heading h2{{font-size:21px;margin:5px 0}}.iw-heading p,.iw-category-subtitle{{color:var(--muted);font-size:12px;margin:0}}.iw-heading>a{{font-size:12px;color:#b9c2ff;font-weight:800;white-space:nowrap}}
.iw-card-grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}}.iw-module-card,.iw-popular-card{{display:flex;align-items:center;gap:11px;min-width:0;min-height:88px;padding:13px;border:1px solid var(--border);border-radius:13px;background:rgba(9,14,27,.34);color:var(--text);text-decoration:none;transition:transform .16s,border-color .16s,background .16s}}.iw-popular-card{{border-color:rgba(139,157,255,.22);background:linear-gradient(135deg,rgba(99,102,241,.11),rgba(255,255,255,.025))}}.iw-module-card:hover,.iw-popular-card:hover{{transform:translateY(-2px);border-color:rgba(139,157,255,.5);background:rgba(99,102,241,.09)}}.iw-icon{{display:grid;place-items:center;flex:0 0 38px;height:38px;border-radius:11px;background:rgba(139,157,255,.12);font-size:19px}}.iw-module-card>span:nth-child(2),.iw-popular-card>span:nth-child(2){{flex:1;min-width:0}}.iw-module-card strong,.iw-module-card small,.iw-popular-card strong,.iw-popular-card small{{display:block}}.iw-module-card strong,.iw-popular-card strong{{font-size:12px}}.iw-module-card small,.iw-popular-card small{{margin-top:4px;color:var(--muted);font-size:10px;line-height:1.45}}.iw-arrow{{color:#aeb9ff}}
.iw-category{{margin:0 0 22px;padding:20px;border:1px solid var(--border);border-radius:17px;background:linear-gradient(145deg,rgba(255,255,255,.025),rgba(255,255,255,.008))}}.iw-category>h2{{font-size:18px;margin:0 0 4px}}.iw-category-subtitle{{margin-bottom:15px}}
.module-nav{{display:flex;align-items:center;gap:7px;flex-wrap:nowrap;overflow-x:auto;margin:0 0 18px;padding:10px;border:1px solid var(--border);border-radius:13px;background:rgba(9,14,27,.72);scrollbar-width:thin}}
.module-nav-title{{flex:0 0 auto;padding:0 7px;color:var(--muted);font-size:10px;font-weight:900;letter-spacing:.12em}}
.module-nav-link{{flex:0 0 auto;display:inline-flex;align-items:center;min-height:34px;padding:7px 10px;border:1px solid transparent;border-radius:8px;color:var(--muted);font-size:12px;font-weight:700;text-decoration:none;white-space:nowrap}}
.module-nav-link:hover{{color:var(--text);background:rgba(139,157,255,.08);border-color:var(--border)}}
.module-nav-link.is-active{{color:#fff;background:rgba(129,140,248,.18);border-color:rgba(139,157,255,.4)}}
@media(max-width:1000px){{.iw-card-grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}@media(max-width:650px){{.iw-hero{{padding:24px 19px}}.iw-metrics{{grid-template-columns:repeat(2,minmax(0,1fr))}}.iw-card-grid{{grid-template-columns:1fr}}.iw-category{{padding:15px}}.iw-heading{{align-items:flex-start}}}}

{custom_theme_css}
</style></head><body>{nav}<main id="main-content" tabindex="-1">{body}<p class="muted"><small>IronWolf · Created by KingSnowy</small></p></main>{_selection_menu_script()}</body></html>"""



def _selection_menu_script():
    """Add lightweight search to long select menus across every dashboard module."""
    return r"""<script>
(function () {
  document.querySelectorAll('select:not([multiple]):not([data-no-search])').forEach(function (select) {
    if (select.options.length < 7 || select.dataset.searchReady === '1') return;
    select.dataset.searchReady = '1';
    var wrap = document.createElement('div');
    wrap.className = 'select-search-wrap';
    var input = document.createElement('input');
    input.type = 'search';
    input.placeholder = 'Optionen suchen …';
    input.setAttribute('aria-label', 'Auswahloptionen durchsuchen');
    input.autocomplete = 'off';
    var count = document.createElement('small');
    count.className = 'select-search-count';
    wrap.appendChild(input);
    wrap.appendChild(count);
    select.parentNode.insertBefore(wrap, select);
    function filterOptions() {
      var query = input.value.trim().toLocaleLowerCase();
      var visible = 0;
      Array.prototype.forEach.call(select.options, function (option) {
        var match = !query || option.text.toLocaleLowerCase().indexOf(query) !== -1;
        option.hidden = !match;
        if (match) visible++;
      });
      count.textContent = query ? visible + ' passende Optionen' : select.options.length + ' Optionen';
    }
    input.addEventListener('input', filterOptions);
    filterOptions();
  });

  document.querySelectorAll('[data-role-picker]').forEach(function (picker) {
    var input = picker.querySelector('[data-role-search]');
    var options = Array.prototype.slice.call(picker.querySelectorAll('.role-option'));
    var count = picker.querySelector('[data-role-count]');
    var summary = picker.querySelector('[data-role-summary]');
    var clear = picker.querySelector('[data-role-clear]');
    var selectAll = picker.querySelector('[data-role-select-visible]');
    function update() {
      var query = input.value.trim().toLocaleLowerCase();
      var visible = 0;
      options.forEach(function (label) {
        var match = !query || label.dataset.search.indexOf(query) !== -1;
        label.hidden = !match;
        if (match) visible++;
      });
      var selected = options.filter(function (label) {
        return label.querySelector('input').checked;
      }).length;
      count.textContent = visible + ' Rollen angezeigt';
      summary.textContent = selected + ' ausgewählt';
    }
    input.addEventListener('input', update);
    options.forEach(function (label) {
      label.querySelector('input').addEventListener('change', update);
    });
    clear.addEventListener('click', function () {
      options.forEach(function (label) { label.querySelector('input').checked = false; });
      update();
    });
    selectAll.addEventListener('click', function () {
      options.forEach(function (label) {
        if (!label.hidden) label.querySelector('input').checked = true;
      });
      update();
    });
    update();
  });
})();
</script>"""

def _module_command_footer():
    """Render the commands relevant to the current dashboard module at its bottom."""
    if request.method != "GET" or not _bot or not request.path.startswith("/guild/"):
        return ""
    parts = request.path.strip("/").split("/")
    if len(parts) != 3 or parts[2] in {"commands", "stats", "audit-log", "premium"}:
        return ""
    guild_id, slug = parts[1], parts[2]
    # Match dashboard modules to the command/cog names exposed by discord.py.
    terms_by_slug = {
        "automation": ("automation", "autom", "scheduled", "plan"),
        "birthdays": ("birthday", "geburtstag"),
        "economy": ("economy", "econom", "shop", "money", "currency", "wirtschaft"),
        "verification": ("verif", "verify", "registration", "anmeld", "ausweis"),
        "tickets": ("ticket", "support"),
        "messages": ("message", "nachricht", "panel", "button"),
        "actions": ("moderation", "moder", "security", "serverlogs", "controltools"),
    }
    terms = terms_by_slug.get(slug)
    if not terms:
        return ""
    try:
        records = _command_records(guild_id)
    except Exception:
        return ""
    matched = [
        item for item in records
        if any(term in (str(item.get("cog", "")) + " " + str(item.get("name", ""))).casefold() for term in terms)
    ]
    rows = []
    for item in matched:
        status = "🟢 Enabled" if item.get("enabled") else "🔴 Disabled"
        rows.append(
            '<div class="row"><div><strong><code>' + html.escape(item["name"]) +
            '</code></strong><br><small>' + html.escape(item.get("kind", "Befehl")) +
            ' · ' + html.escape(item.get("cog", "Allgemein")) +
            '</small></div><small>' + status + '</small></div>'
        )
    if not rows:
        content = '<p class="muted">No Discord commands could be matched to this module. View the full list under <a href="/guild/' + html.escape(guild_id) + '/commands">Commands &amp; Slash Commands</a>.</p>'
    else:
        content = '<p class="muted">Discord commands related to this module. Disabled commands are marked; manage them in the command overview.</p>' + ''.join(rows)
    return '<section class="card module-command-footer"><h2>⌘ Related Discord Commands</h2>' + content + '<p><a class="button secondary" href="/guild/' + html.escape(guild_id) + '/commands">Manage all commands →</a></p></section>'


def _module_navigation():
    """Render the English module sidebar on every server dashboard page."""
    if not request.path.startswith("/guild/"):
        return ""
    parts = request.path.strip("/").split("/")
    if len(parts) not in (2, 3) or not parts[1].isdigit():
        return ""
    gid = parts[1]
    active = parts[2] if len(parts) == 3 else "starter"
    modules = (
        ("starter", "🏠 Starter Module", "?section=overview"),
        ("welcome", "👋 Welcome & Goodbye", "/welcome"),
        ("tickets", "🎟️ Support Tickets", "/tickets"),
        ("verification", "🔐 Verification", "/verification"),
        ("reaction-roles", "🎭 Reaction Roles", "/reaction-roles"),
        ("feedback", "💬 Anonymous Feedback", "/feedback"),
        ("birthdays", "🎂 Birthdays", "/birthdays"),
        ("teamlist", "👥 Team List", "/teamlist"),
        ("general", "📈 Leveling & XP", "?section=general#settings-panel"),
        ("temporary-voice", "🔊 Temporary Voice", "/temporary-voice"),
        ("applications", "📝 Applications", "/applications"),
        ("messages", "✉️ Message Builder", "/messages"),
        ("automation", "📣 Automation", "/automation"),
        ("security-lab", "🛡️ Security Center", "/security-lab"),
        ("wolfshield", "🧱 Protection Settings", "/wolfshield"),
        ("honeypot", "🍯 Honeypot", "/honeypot"),
        ("ping-protection", "📡 Ping Protection", "/ping-protection"),
        ("actions", "⚔️ Action Center", "/actions"),
        ("verified-accounts", "🎮 Gaming Verification", "/verified-accounts"),
        ("branding", "🎨 Server Branding", "/branding"),
        ("custom-bot", "🤖 Custom Bot Profile", "/custom-bot"),
        ("roles", "🎭 Roles & Rewards", "?section=roles#settings-panel"),
        ("channels", "📡 Channels & Logs", "?section=channels#settings-panel"),
        ("server-statistics", "📊 Server Statistics", "/server-statistics"),
        ("economy", "💰 Economy", "/economy"),
        ("server-boost", "🚀 Server Boosts", "/server-boost"),
        ("stats", "📈 Live Statistics", "/stats"),
        ("commands", "⌘ Commands", "/commands"),
        ("audit-log", "📜 Audit Log", "/audit-log"),
        ("health", "🩺 System Health", "/health"),
        ("backup", "🗄️ Backup & Restore", "/backup"),
        ("premium", "✨ Premium", "/premium"),
        ("module-manager", "🧩 Module Manager", "?section=modules"),
        ("module-template", "🛠️ Module Template", "?section=template"),
    )
    links = []
    try:
        nav_cfg = _guild_config(int(gid))
        if not guild_has_feature(nav_cfg, "custom_bot"):
            modules = tuple(item for item in modules if item[0] != "custom-bot")
    except Exception:
        modules = tuple(item for item in modules if item[0] != "custom-bot")
    for key, label, target in modules:
        href = "/guild/" + gid + (target if target.startswith("?") else target)
        current = (active == "starter" and key == "starter") or active == key
        links.append(
            '<a class="module-nav-link' + (' is-active' if current else '') +
            '" href="' + href + '"' +
            (' aria-current="page"' if current else '') + '>' +
            html.escape(label) + '</a>'
        )
    return (
        '<aside class="module-nav" aria-label="Server modules">'
        '<div class="module-nav-title">SERVER MODULES</div>' +
        ''.join(links) + '</aside>'
    )

DASHBOARD_TRANSLATIONS = {
    "en": {
        "Dein Server. Deine Regeln.": "Your server. Your rules.",
        "Verwalte deine IronWolf-Server an einem Ort: Einstellungen, Befehle und Modulvorlagen.": "Manage your IronWolf servers in one place: settings, commands, and module templates.",
        "Erfasste Mitglieder": "Tracked members",
        "Befehle": "Commands",
        "IronWolf einladen": "Invite IronWolf",
        "Hinweis: Die Werte aktualisieren sich beim Neuladen der Seite.": "Note: Values update when you reload the page.",
        "Was du hier machen kannst": "What you can do here",
        "Serverbezogene Einstellungen mit Formularen ändern": "Change server-specific settings using forms",
        "Neue Python-Modulvorlagen erstellen und herunterladen": "Create and download new Python module templates",
        "Deine Server": "Your servers",
        "Wähle einen Server, um seine Einstellungen zu öffnen.": "Choose a server to open its settings.",
        "Keine passenden Server gefunden": "No matching servers found",
        "Prüfe deine Discord-Rechte und ob IronWolf auf dem Server ist.": "Check your Discord permissions and make sure IronWolf is on the server.",
        "Mit Discord anmelden": "Sign in with Discord",
        "Einstellungen, Befehle und Module verwalten": "Manage settings, commands, and modules",
        "Server öffnen": "Open server",
        "Allgemein": "General",
        "Anonymes Feedback": "Anonymous feedback",
        "Befehle im Dashboard öffnen": "Open commands in dashboard",
        "Erneut anmelden": "Sign in again"
    },
    "de": {
        "Your Control Center 🐺": "Dein Kontrollzentrum 🐺",
        "Manage your connected Discord servers from one convenient dashboard.": "Verwalte deine verbundenen Discord-Server bequem über ein Dashboard.",
        "Your servers": "Deine Server",
        "Choose a server to open its settings.": "Wähle einen Server, um seine Einstellungen zu öffnen.",
        "No matching servers found.": "Keine passenden Server gefunden.",
        "Manage settings, commands, and modules": "Einstellungen, Befehle und Module verwalten",
        "Open server": "Server öffnen",
        "General settings": "Allgemeine Einstellungen",
        "Channels & logs": "Kanäle & Protokolle",
        "Roles": "Rollen",
        "Temporary voice channels": "Temporäre Sprachkanäle",
        "Protection & moderation": "Schutz & Moderation",
        "Bot language": "Bot-Sprache",
        "English": "Englisch",
        "Save settings": "Einstellungen speichern",
        "Back to settings": "Zurück zu den Einstellungen",
        "Skip to content": "Zum Inhalt springen",
        "Switch server": "Server wechseln",
        "Server Settings": "Servereinstellungen",
        "Main navigation": "Hauptnavigation",
        "Server modules": "Servermodule",
        "CONTROL CENTER": "KONTROLLZENTRUM",
        "WELCOME TO IRONWOLF": "WILLKOMMEN BEI IRONWOLF",
        "Premium costs €5 per year.": "Premium kostet 5 € pro Jahr.",
        "Join our Discord": "Tritt unserem Discord bei",
        "Manage Premium": "Premium verwalten",
        "Tracked members": "Erfasste Mitglieder",
        "Commands": "Befehle",
        "Invite IronWolf": "IronWolf einladen",
        "Support open": "Support öffnen",
        "Status is retrieved directly from the running bot process.": "Der Status wird direkt aus dem laufenden Bot-Prozess ermittelt.",
        "No matching servers found.": "Keine passenden Server gefunden.",
        "Sign in with Discord": "Mit Discord anmelden",
        "Sign in again": "Erneut anmelden",
        "Open server": "Server öffnen",
        "General settings": "Allgemeine Einstellungen",
        "Channels & logs": "Kanäle & Protokolle",
        "Temporary voice channels": "Temporäre Sprachkanäle",
        "Protection & moderation": "Schutz & Moderation"
    }
}

def _translate_dashboard(value, language):
    translations = DASHBOARD_TRANSLATIONS.get(language, {})
    for source in sorted(translations, key=len, reverse=True):
        value = value.replace(source, translations[source])
    return value

def _render(title, body, status=200):
    language = session.get("dashboard_language", "en")
    if language not in {"en", "de"}:
        language = "en"
    body = _translate_dashboard(_module_navigation() + body + _module_command_footer(), language)
    title = _translate_dashboard(title, language)
    selected_en = " selected" if language == "en" else ""
    selected_de = " selected" if language == "de" else ""
    language_control = (
        '<form method="post" action="/language" class="language-switcher">' + _csrf_field() +
        '<label for="dashboard_language">🌐 Language / Sprache</label>' +
        '<select id="dashboard_language" name="language" onchange="this.form.submit()">' +
        '<option value="en"' + selected_en + '>🇬🇧 English</option>' +
        '<option value="de"' + selected_de + '>🇩🇪 Deutsch</option>' +
        '</select></form>'
    )
    body = language_control + body
    rendered_page = _page(title, body)
    # Translate the complete rendered document, including the global navigation
    # and page chrome, not only the page-specific content.
    rendered_page = _translate_dashboard(rendered_page, language)
    rendered_page = rendered_page.replace('<html lang="en">', f'<html lang="{language}">', 1)
    response = make_response(rendered_page, status)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@app.route("/privacy")
def public_privacy_policy():
    """Public privacy notice for Roblox OAuth review."""
    body = """
    <section class="card">
      <h1>IronWolf – Datenschutzerklärung</h1>
      <p><strong>Stand:</strong> 17. Oktober 2026</p>
      <p>IronWolf ist ein Discord-Bot mit einer Weboberfläche zur Kontoverifizierung. Diese Erklärung beschreibt die Verarbeitung bei der freiwilligen Verknüpfung eines Roblox-Kontos mit einem Discord-Konto.</p>
      <h2>Welche Daten verarbeitet werden</h2>
      <ul>
        <li>Discord-Nutzer-ID und gegebenenfalls die Discord Servers-ID, um die Verknüpfung dem richtigen Konto und Server zuzuordnen.</li>
        <li>Roblox-Benutzer-ID und Roblox-Benutzername, die Roblox nach der OAuth-Autorisierung bereitstellt.</li>
        <li>Verknüpfungs- und Verifizierungsstatus sowie Zeitstempel, die zur Vermeidung doppelter oder missbräuchlicher Verknüpfungen benötigt werden.</li>
        <li>OAuth-Statusdaten, die vorübergehend für den sicheren Abschluss des Autorisierungsablaufs benötigt werden.</li>
      </ul>
      <h2>Wofür die Daten verwendet werden</h2>
      <p>Die Daten werden ausschließlich zur Kontoverknüpfung und Verifizierung in Discord verwendet. Abhängig von der Serverkonfiguration kann IronWolf nach erfolgreicher Verifizierung eine Discord-Rolle vergeben und den Discord-Nickname auf den verifizierten Roblox-Benutzernamen setzen.</p>
      <h2>Roblox OAuth und Passwörter</h2>
      <p>Die Anmeldung und Zustimmung erfolgen auf Roblox. IronWolf fordert weder dein Roblox-Passwort noch deinen Roblox-Anmeldecode an. Die App verwendet die von Roblox im OAuth-Ablauf bereitgestellten Informationen für die oben genannten Funktionen.</p>
      <h2>Speicherung und Weitergabe</h2>
      <p>Verknüpfungsdaten werden in der vom Betreiber gehosteten Anwendungsumgebung gespeichert und sind für die Verifizierungsfunktion erforderlich. Sie werden nicht verkauft. Daten können durch die für den Betrieb notwendige Hosting-Infrastruktur verarbeitet werden. Eine Weitergabe erfolgt nur, soweit sie für die Funktion erforderlich ist, gesetzlich vorgeschrieben wird oder du sie selbst veranlasst.</p>
      <h2>Aufbewahrung und Löschung</h2>
      <p>Die Daten werden so lange gespeichert, wie die Kontoverknüpfung für die Verifizierung benötigt wird. Wenn du die Löschung oder Aufhebung einer Verknüpfung anfragen möchtest, kontaktiere den Betreiber über das <a href="https://github.com/kingsnowy14-cloud/the-isle-signin-bot/issues">GitHub-Repository von IronWolf</a>. Bitte veröffentliche dort keine privaten Kontodaten; beschreibe nur, dass du eine Datenschutzanfrage stellen möchtest, und nutze anschließend einen privaten Kontaktweg.</p>
      <h2>Deine Wahl</h2>
      <p>Die Verknüpfung ist freiwillig. Du kannst die Autorisierung auf Roblox abbrechen, bevor du zustimmst. Ohne Zustimmung kann die Roblox-Verifizierung nicht abgeschlossen werden.</p>
      <h2>Kontakt</h2>
      <p>Für Datenschutzanfragen nutze zunächst das <a href="https://github.com/kingsnowy14-cloud/the-isle-signin-bot">IronWolf-Projekt auf GitHub</a>. Diese Seite benennt derzeit keine postalische Anschrift oder direkte E-Mail-Adresse des Betreibers; diese Kontaktdaten sollten ergänzt werden, bevor die App öffentlich eingereicht wird.</p>
    </section>
    """
    return make_response(_page("Datenschutzerklärung – IronWolf", body), 200)


@app.route("/terms")
def public_terms_of_service():
    """Public terms of service for Roblox OAuth review."""
    body = """
    <section class="card">
      <h1>IronWolf – Nutzungsbedingungen</h1>
      <p><strong>Stand:</strong> 17. Oktober 2026</p>
      <h2>1. Zweck des Dienstes</h2>
      <p>IronWolf bietet Funktionen für Discord-Communitys, darunter die freiwillige Verknüpfung eines Roblox-Kontos zur Identitätsbestätigung innerhalb eines Discord Serverss.</p>
      <h2>2. Nutzung und Autorisierung</h2>
      <p>Du musst zur Nutzung berechtigt sein, die angegebenen Konten zu verwenden. Die Roblox-Verknüpfung wird über den offiziellen OAuth-Ablauf von Roblox autorisiert. Teile IronWolf niemals dein Roblox-Passwort mit.</p>
      <h2>3. Verifizierungsfunktionen</h2>
      <p>Nach erfolgreicher Autorisierung kann IronWolf Roblox-Kontoinformationen speichern und – abhängig von der Konfiguration und den Berechtigungen des Discord-Bots – eine Discord-Rolle vergeben oder den Discord-Nickname ändern. Das Ergebnis kann von den Einstellungen und Berechtigungen des jeweiligen Discord Serverss abhängen.</p>
      <h2>4. Missbrauch</h2>
      <p>Du darfst den Dienst nicht zur Täuschung, zum Umgehen von Sicherheitsmaßnahmen, zur unbefugten Kontonutzung oder für rechtswidrige Zwecke verwenden. Versuche nicht, den OAuth-Ablauf zu manipulieren oder fremde Konten zu verknüpfen.</p>
      <h2>5. Verfügbarkeit und Änderungen</h2>
      <p>Der Dienst wird ohne Garantie einer ununterbrochenen Verfügbarkeit bereitgestellt. Funktionen können aus Sicherheits-, Wartungs- oder technischen Gründen geändert oder eingestellt werden.</p>
      <h2>6. Externe Dienste</h2>
      <p>Roblox und Discord sind eigenständige Dienste und unterliegen ihren jeweiligen Nutzungsbedingungen und Datenschutzbestimmungen. IronWolf wird nicht von Roblox oder Discord betrieben oder offiziell unterstützt, sofern nicht ausdrücklich anders angegeben.</p>
      <h2>7. Datenschutz und Anfragen</h2>
      <p>Informationen zur Verarbeitung von Kontodaten findest du in der <a href="/privacy">Datenschutzerklärung</a>. Für Support- oder Datenschutzanfragen besuche das <a href="https://github.com/kingsnowy14-cloud/the-isle-signin-bot">IronWolf-Projekt auf GitHub</a>. Veröffentliche keine Passwörter, Tokens oder anderen vertraulichen Daten.</p>
    </section>
    """
    return make_response(_page("Nutzungsbedingungen – IronWolf", body), 200)


@app.errorhandler(401)
def dashboard_auth_expired(error):
    """Keep expired Discord sessions actionable instead of showing a broken page."""
    session.clear()
    body = (
        '<section class="card"><h1>Discord Session Expired</h1>'
        '<p>Your session is no longer valid. Melde dich erneut über Discord an, '
        'um zum Dashboard zurückzukehren.</p>'
        '<a class="button" href="/login">Sign in with Discord again</a></section>'
    )
    return make_response(_page("Erneut anmelden", body), 401)


def _require_login():
    if not session.get("access_token"):
        return redirect(url_for("login"))
    return None


def _api_get(path):
    response = requests.get(
        DISCORD_API + path,
        headers={"Authorization": "Bearer " + session["access_token"]},
        timeout=10,
    )
    if response.status_code == 401:
        session.clear()
        return None
    response.raise_for_status()
    return response.json()


def _user_guilds():
    # Cache Discord's guild list briefly to avoid rate-limiting on redirects and page navigation.
    user_key = str(session.get("user_id") or "")
    now = time.monotonic()
    with _guild_cache_lock:
        cached = _guild_cache.get(user_key)
        if cached and now - cached["time"] <= _GUILD_CACHE_TTL:
            return cached["guilds"]

    try:
        guilds = _api_get("/users/@me/guilds")
    except requests.HTTPError as exc:
        response = exc.response
        if response is not None and response.status_code == 429:
            with _guild_cache_lock:
                cached = _guild_cache.get(user_key)
            if cached and now - cached["time"] <= _GUILD_CACHE_STALE_TTL:
                print("[DASHBOARD] Discord rate limit; using briefly cached guild list.", flush=True)
                return cached["guilds"]
            print("[DASHBOARD] Discord rate limit while loading guild list; no usable cache.", flush=True)
            abort(503, "Discord begrenzt gerade Anfragen. Bitte warte kurz und lade die Seite erneut.")
        raise
    except requests.RequestException as exc:
        print(f"[DASHBOARD] Discord API request failed while loading guild list: {type(exc).__name__}", flush=True)
        abort(502, "Discord ist momentan nicht erreichbar. Bitte versuche es später erneut.")

    if guilds is None:
        # _api_get clears an expired OAuth session on HTTP 401. Return a
        # deliberate login response instead of treating it as a bad API payload.
        abort(401, "Your Discord session has expired. Please sign in again.")
    if not isinstance(guilds, list):
        abort(502, "Discord hat eine unerwartete Antwort auf die Serverliste geliefert.")
    with _guild_cache_lock:
        _guild_cache[user_key] = {"guilds": guilds, "time": now}
    return guilds


def _authorized_guild(guild_id):
    login = _require_login()
    if login:
        return None, login
    try:
        target = int(guild_id)
    except (TypeError, ValueError):
        abort(404)
    matches = [
        item for item in _user_guilds()
        if str(item.get("id")) == str(target)
        and (int(item.get("permissions", "0")) & (MANAGE_GUILD | ADMINISTRATOR))
    ]
    guild = _bot.get_guild(target) if _bot else None
    if not matches or guild is None:
        print(
            f"[DASHBOARD] Guild authorization failed: guild={target}, "
            f"oauth_match={bool(matches)}, bot_in_guild={guild is not None}.",
            flush=True,
        )
        abort(403, "Server unavailable: Check that the bot is still in the server and that your Discord account has Manage Server permission.")
    return guild, None


def _module_records(guild_id):
    cfg = _config.get(str(guild_id), {})
    disabled = set(cfg.get("disabled_modules", []))
    modules = {}
    for command in _bot.commands:
        if command.hidden or command.name == "help":
            continue
        cog = command.cog
        if cog is not None:
            modules[cog.__class__.__name__] = cog.__class__.__name__
    for command in _bot.tree.walk_commands():
        if isinstance(command, app_commands.Group):
            continue
        cog = getattr(command, "binding", None)
        if cog is not None:
            modules[cog.__class__.__name__] = cog.__class__.__name__
    return [
        {"name": name, "enabled": name not in disabled}
        for name in sorted(modules, key=str.lower)
    ]


def _command_records(guild_id):
    cfg = _config.get(str(guild_id), {})
    disabled = set(cfg.get("disabled_commands", []))
    disabled_modules = set(cfg.get("disabled_modules", []))
    records = []
    seen = set()
    for command in _bot.walk_commands():
        if command.hidden or command.name == "help":
            continue
        qualified_name = command.qualified_name
        key = f"prefix:!{qualified_name}"
        if key in seen:
            continue
        seen.add(key)
        cog = command.cog
        module_name = cog.__class__.__name__ if cog is not None else None
        enabled = key not in disabled and f"prefix:.{qualified_name}" not in disabled and module_name not in disabled_modules
        records.append({"key": key, "name": "! / ." + qualified_name, "kind": "Prefix", "enabled": enabled, "cog": command.cog_name or "Allgemein"})
    for command in _bot.tree.walk_commands():
        if isinstance(command, app_commands.Group):
            continue
        key = f"slash:/{command.qualified_name}"
        if key in seen:
            continue
        seen.add(key)
        cog = getattr(command, "binding", None)
        module_name = cog.__class__.__name__ if cog is not None else None
        records.append({"key": key, "name": "/" + command.qualified_name, "kind": "Slash", "enabled": key not in disabled and module_name not in disabled_modules, "cog": module_name or "Allgemein"})
    return sorted(records, key=lambda item: (item["cog"].lower(), item["name"].lower()))


def _audit_dashboard_change(guild_id, action, actor_id, details):
    """Store a bounded, server-local audit trail of dashboard configuration changes."""
    cfg = _guild_config(guild_id)
    records = cfg.setdefault("dashboard_audit_log", [])
    records.append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "actor_id": str(actor_id or "unknown"),
        "action": str(action)[:100],
        "details": str(details)[:500],
    })
    del records[:-200]
    _save_json(data_path("config.json"), _config)


def _guild_channels(guild):
    return [(channel.id, channel.name) for channel in guild.text_channels]


def _guild_roles(guild):
    return [(role.id, role.name) for role in guild.roles if not role.is_default() and not role.managed]


@app.before_request
def log_dashboard_navigation():
    """Log module-page requests to diagnose unexpected dashboard redirects."""
    if request.path.startswith("/guild/"):
        print(
            f"[DASHBOARD NAV] {request.method} {request.path} "
            f"query={request.query_string.decode('utf-8', errors='replace')[:200]} "
            f"referer={request.referrer or '-'}",
            flush=True,
        )


@app.after_request
def security_headers(response):
    if request.path.startswith("/guild/"):
        print(
            f"[DASHBOARD NAV] response={response.status_code} "
            f"path={request.path} location={response.headers.get('Location', '-')}",
            flush=True,
        )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'"
    return response


def _public_status_panel():
    """Render live bot state and basic public statistics from the running process."""
    ready = bool(_bot and getattr(_bot, "is_ready", lambda: False)())
    guilds = list(getattr(_bot, "guilds", []) or []) if _bot else []
    guild_count = len(guilds) if ready else 0
    member_count = sum(int(getattr(guild, "member_count", 0) or 0) for guild in guilds) if ready else 0
    prefix_count = sum(1 for command in getattr(_bot, "commands", []) if not getattr(command, "hidden", False)) if ready else 0
    slash_count = sum(1 for command in _bot.tree.walk_commands() if not isinstance(command, app_commands.Group)) if ready else 0
    status_label = "ONLINE" if ready else "OFFLINE"
    status_color = "#62e6b4" if ready else "#ff8190"
    invite_url = "https://discord.com/oauth2/authorize?client_id=1558547445187743804&scope=bot%20applications.commands&permissions=0"
    return (
        '<section class="card" id="ironwolf-status">'
        '<span class="pill">IRONWOLF LIVE STATUS</span>'
        '<h2 style="margin-top:14px"><span style="color:' + status_color + '">● ' + status_label + '</span></h2>'
        '<p class="muted">Status wird direkt aus dem laufenden Bot-Prozess ermittelt.</p>'
        '<div class="grid">'
        '<div class="notice"><small>Discord Servers</small><h2>' + str(guild_count) + '</h2></div>'
        '<div class="notice"><small>Erfasste Mitglieder</small><h2>' + str(member_count) + '</h2></div>'
        '<div class="notice"><small>Befehle</small><h2>' + str(prefix_count + slash_count) + '</h2><small>' + str(prefix_count) + ' Prefix · ' + str(slash_count) + ' Slash</small></div>'
        '</div>'
        '<p><a class="button" href="' + invite_url + '" target="_blank" rel="noopener noreferrer">🐺 IronWolf einladen</a> '
        '<a class="button secondary" href="https://discord.gg/T3AYmFmbrS" target="_blank" rel="noopener noreferrer">💬 Support öffnen</a></p>'
        '<p class="muted"><small>Hinweis: Die Werte aktualisieren sich beim Neuladen der Seite.</small></p>'
        '</section>'
    )


@app.route("/language", methods=["POST"])
def set_dashboard_language():
    if not _csrf_ok():
        abort(400, "Security check failed. Please reload the page.")
    language = request.form.get("language", "en")
    if language not in {"en", "de"}:
        abort(400, "Please select English or German.")
    session["dashboard_language"] = language

    # When the language is changed from a server page, keep that server's bot
    # language in sync so dashboard and bot responses use the same selection.
    referrer_path = urlparse(request.referrer or "").path
    guild_match = re.match(r"^/guild/(\\d+)(?:/|$)", referrer_path)
    if guild_match and _guild_config and _config is not None and _save_json:
        guild_id = int(guild_match.group(1))
        _authorized_guild(guild_id)
        guild_cfg = _guild_config(guild_id)
        guild_cfg["bot_language"] = language
        _save_json(data_path("config.json"), _config)

    return redirect(request.referrer or url_for("index"))


@app.route("/")
def index():
    login = _require_login()
    if login:
        body = """<section class="card hero"><span class="pill">CONTROL CENTER</span><h1 style="margin-top:16px">Dein Server. Deine Regeln.</h1>
<p>Verwalte deine IronWolf-Server an einem Ort: Einstellungen, Befehle und Modulvorlagen.</p>
<div class="notice">🔐 Die Anmeldung erfolgt über Discord. Du kannst nur Server verwalten, auf denen du „Server verwalten“ oder Administrator-Rechte besitzt und auf denen der Bot online ist.</div>
<a class="button" href="/login">Sign in with Discord</a> <a class="button secondary" href="https://github.com/kingsnowy14-cloud/the-isle-signin-bot/releases/download/desktop-latest/IronWolf-Setup.exe">⬇️ IronWolf für Windows herunterladen</a>
<div class="notice">Windows-App: Nach der Installation kann IronWolf beim Windows-Anmelden starten. Die Discord-Anmeldung bleibt weiterhin erforderlich.</div>
<div class="notice">✨ <strong>Premium costs €5 per year.</strong> Fragen oder Nachrichten? <a href="https://discord.gg/T3AYmFmbrS" target="_blank" rel="noopener noreferrer">Join our Discord</a>.</div>
<h2>Was du hier machen kannst</h2><ul><li>Serverbezogene Einstellungen mit Formularen ändern</li><li>Prefix- und Slash-Befehle einzeln aktivieren/deaktivieren</li><li>Neue Python-Modulvorlagen erstellen und herunterladen</li></ul></section>"""
        if not _oauth_ready():
            body += '<div class="card"><h2>Einmalige Einrichtung nötig</h2><p>Setze in Railway die Variablen <code>DISCORD_CLIENT_ID</code>, <code>DISCORD_CLIENT_SECRET</code>, <code>DASHBOARD_REDIRECT_URI</code> und <code>DASHBOARD_SECRET_KEY</code>. Als Redirect URI muss exakt die öffentliche URL plus <code>/callback</code> in der Discord Developer Portal OAuth2-Einstellung stehen.</p></div>'
        body += _public_status_panel()
        body += '<section class="card"><h2>📜 Commands & Features</h2><p>After signing in with Discord, you can view and manage the available prefix and slash commands. Die Liste wird aus den tatsächlich geladenen Bot-Befehlen erstellt.</p><a class="button secondary" href="/login">Befehle im Dashboard öffnen →</a></section>'
        return _render("Control Center", body)
    try:
        guilds = _user_guilds()
    except (requests.RequestException, ValueError, KeyError):
        session.clear()
        return _render("Discord-Anmeldung", '<div class="card"><h1>Session Expired</h1><p>Please sign in again.</p><a class="button" href="/login">Erneut anmelden</a></div>', 401)
    cards = []
    for item in guilds:
        if not (int(item.get("permissions", "0")) & (MANAGE_GUILD | ADMINISTRATOR)):
            continue
        gid = str(item.get("id"))
        bot_guild = _bot.get_guild(int(gid)) if _bot else None
        if not bot_guild:
            continue
        cards.append(f'<a class="card server-card" style="display:block;text-decoration:none" href="/guild/{gid}"><span class="pill">● BOT VERBUNDEN</span><h2 style="margin-top:14px">{html.escape(item.get("name","Server"))}</h2><p class="muted">Einstellungen, Befehle und Module verwalten</p><strong>Server öffnen ↗</strong></a>')
    body = '<section class="card hero"><span class="pill">WELCOME TO IRONWOLF</span><h1 style="margin-top:14px">Your Control Center 🐺</h1><p>Hier steuerst du deine verbundenen Discord Servers bequem über eine Oberfläche.</p><div class="notice">✨ <strong>Premium costs €5 per year.</strong> <a href="https://discord.gg/T3AYmFmbrS" target="_blank" rel="noopener noreferrer">Nachrichten auf Discord ↗</a></div><div class="grid"><div><h2>So funktioniert es</h2><ol><li>Wähle unten den Server aus, den du verwalten möchtest.</li><li>Unter <strong>Einstellungen</strong> legst du Kanäle, Rollen und Serveroptionen fest.</li><li>Unter <strong>Module</strong> aktivierst oder deaktivierst du Funktionen für diesen Server.</li><li>Öffne <strong>Befehle</strong>, um Prefix- und Slash-Befehle gruppiert zu verwalten.</li><li>Nutze die <strong>Aktionszentrale</strong> für unterstützte Moderationsaktionen.</li></ol></div><div><h2>Wichtige Hinweise</h2><ul><li>Du brauchst Administrator- oder Server-verwalten-Rechte.</li><li>Der Bot muss auf dem Server online sein.</li><li>Änderungen an Modulen und Befehlen gelten nur für den ausgewählten Server.</li><li>Gefährliche Aktionen benötigen zusätzliche Bestätigung und passende Rechte.</li></ul></div></div></section><h2>Deine Server</h2><p class="muted">Wähle einen Server, um seine Einstellungen zu öffnen.</p>'
    body += _public_status_panel()
    body += '<p><a class="button" href="/premium">✨ Manage Premium →</a></p>'
    body += '<div class="grid">' + ("".join(cards) or '<div class="card">No matching servers found. Prüfe deine Discord-Rechte und ob IronWolf auf dem Server ist.</div>') + '</div>'
    return _render("Deine Server", body)


@app.route("/feedback", methods=["GET", "POST"])
def anonymous_feedback():
    feedback_file = data_path("website_feedback.json")
    try:
        entries = json.loads(feedback_file.read_text(encoding="utf-8"))
        if not isinstance(entries, list):
            entries = []
    except (OSError, json.JSONDecodeError):
        entries = []
    if request.method == "POST":
        if not _csrf_ok():
            abort(400, "Security check failed. Please reload the page.")
        rating = request.form.get("rating", "")
        category = request.form.get("category", "Allgemein")
        allowed_categories = ("Allgemein", "Dashboard", "Ticketsystem", "Verifizierung", "Module")
        if rating not in ("like", "dislike"):
            abort(400, "Please choose 👍 or 👎.")
        if category not in allowed_categories:
            category = "Allgemein"
        # Intentionally do not store account IDs, IP addresses, user agents, or session data.
        entries.append({"rating": rating, "category": category, "comment": request.form.get("comment", "").strip()[:500], "date": datetime.now(timezone.utc).date().isoformat()})
        temp = feedback_file.with_suffix(".tmp")
        temp.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, feedback_file)
        return redirect(url_for("anonymous_feedback", sent="1"))
    total = len(entries)
    likes = sum(1 for x in entries if x.get("rating") == "like")
    dislikes = sum(1 for x in entries if x.get("rating") == "dislike")
    cats = ("Allgemein", "Dashboard", "Ticketsystem", "Verifizierung", "Module")
    rows = ''.join('<div class="row"><strong>' + html.escape(cat) + '</strong><span>👍 ' + str(sum(1 for x in entries if x.get("category") == cat and x.get("rating") == "like")) + ' · 👎 ' + str(sum(1 for x in entries if x.get("category") == cat and x.get("rating") == "dislike")) + '</span></div>' for cat in cats)
    notice = '<div class="notice">Thank you! Your feedback has been saved anonymously.</div>' if request.args.get("sent") == "1" else ""
    body = '<section class="card"><span class="pill">ANONYMOUS FEEDBACK</span><h1>How do you like IronWolf?</h1><p>Es werden keine Discord-ID, kein Name und keine IP-Adresse im Feedback-Datensatz gespeichert. Die Statistik ist zusammengefasst.</p>' + notice + '<form method="post">' + _csrf_field() + '<label>Bereich</label><select name="category"><option>Allgemein</option><option>Dashboard</option><option>Ticketsystem</option><option>Verifizierung</option><option>Module</option></select><label>Bewertung</label><p><label><input type="radio" name="rating" value="like" required> 👍 Gefällt mir</label><label><input type="radio" name="rating" value="dislike" required> 👎 Verbesserungsbedarf</label></p><label>Kommentar (optional; bitte keine persönlichen Daten eintragen)</label><textarea name="comment" maxlength="500" placeholder="Was können wir verbessern?"></textarea><button type="submit">Anonym absenden</button></form></section><section class="card"><h2>Feedback-Statistik</h2><div class="grid"><div class="notice">Alle Bewertungen<h2>' + str(total) + '</h2></div><div class="notice">👍 Gefällt<h2>' + str(likes) + '</h2></div><div class="notice">👎 Verbesserungsbedarf<h2>' + str(dislikes) + '</h2></div></div><h3>Nach Bereich</h3>' + rows + '</section>'
    return _render("Anonymes Feedback", body)


@app.route("/login")
def login():
    if not _oauth_ready():
        return _render("OAuth-Einrichtung", '<div class="card"><h1>Discord OAuth2 fehlt</h1><p>Bitte konfiguriere <code>DISCORD_CLIENT_ID</code>, <code>DISCORD_CLIENT_SECRET</code>, <code>DASHBOARD_REDIRECT_URI</code> und <code>DASHBOARD_SECRET_KEY</code> in Railway. Der Redirect muss exakt auf <code>/callback</code> zeigen.</p></div>', 503)
    state = secrets.token_urlsafe(32)
    session["oauth_state"] = state
    query = urlencode({
        "client_id": _env("DISCORD_CLIENT_ID"),
        "redirect_uri": _env("DASHBOARD_REDIRECT_URI"),
        "response_type": "code",
        "scope": "identify guilds",
        "state": state,
        "prompt": "consent",
    })
    return redirect("https://discord.com/oauth2/authorize?" + query)


@app.route("/callback")
def callback():
    code = request.args.get("code", "")
    state = request.args.get("state", "")
    expected = session.pop("oauth_state", "")
    if not code or not state or not expected or not hmac.compare_digest(state, expected):
        return _render("Anmeldung fehlgeschlagen", '<div class="card"><h1>OAuth-Prüfung fehlgeschlagen</h1><p>Bitte versuche die Anmeldung erneut.</p><a class="button" href="/login">Erneut anmelden</a></div>', 400)
    if not _oauth_ready():
        abort(503)
    try:
        response = requests.post(
            DISCORD_API + "/oauth2/token",
            data={
                "client_id": _env("DISCORD_CLIENT_ID"),
                "client_secret": _env("DISCORD_CLIENT_SECRET"),
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": _env("DASHBOARD_REDIRECT_URI"),
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=10,
        )
        response.raise_for_status()
        token = response.json()["access_token"]
        profile = requests.get(DISCORD_API + "/users/@me", headers={"Authorization": "Bearer " + token}, timeout=10)
        profile.raise_for_status()
    except (requests.RequestException, ValueError, KeyError):
        return _render("Anmeldung fehlgeschlagen", '<div class="card"><h1>Discord konnte nicht erreicht werden</h1><p>Bitte versuche es später erneut.</p></div>', 502)
    session.clear()
    session["access_token"] = token
    session["user_id"] = str(profile.json().get("id", ""))
    session["csrf_token"] = secrets.token_urlsafe(32)
    return redirect(url_for("index"))


@app.route("/logout", methods=["POST"])
def logout():
    if not _csrf_ok():
        abort(400, "CSRF-Prüfung fehlgeschlagen. Bitte Seite neu laden.")
    session.clear()
    return redirect(url_for("index"))


@app.route("/premium")
def premium_page():
    login = _require_login()
    if login:
        return login
    user_id = str(session.get("user_id", ""))
    plan = account_plan(user_id)
    labels = {"free": "Free", "premium": "Premium", "premium_plus": "Premium+"}
    included_features = set(PLAN_FEATURES.get(plan, frozenset()))
    rows = []
    for feature, label in PREMIUM_FEATURES.items():
        status = "Enthalten" if feature in included_features else "Nicht enthalten"
        rows.append('<div class="card"><h2>' + html.escape(label) + '</h2><p>' + html.escape(status) + '</p></div>')
    body = (
        '<section class="card hero"><span class="pill">IRONWOLF PREMIUM</span><h1 style="margin-top:14px">Dein Premium-Konto</h1>'
        '<p>Dein Zugriff wird an dein angemeldetes Discord-Konto gebunden.</p>'
        '<div class="notice">Aktueller Plan: <strong>' + html.escape(labels[plan]) + '</strong></div>'
        '<p>Die Zahlungsintegration ist noch nicht aktiviert. Bis ein Zahlungsanbieter festgelegt und sicher angebunden ist, werden Pläne ausschließlich manuell über Railway-Variablen freigeschaltet.</p>'
        '</section><div class="grid">' + "".join(rows) + '</div>'
        '<section class="card"><h2>Premium Plans</h2><div class="grid">'
        '<div class="card"><h3>Premium</h3><p><strong>All core Premium features</strong></p><ul><li>Ticket System Pro</li><li>Custom Branding</li><li>Custom Bot Profile (server-specific banner, avatar and description)</li><li>Invite Tracker Pro</li></ul></div>'
        '<div class="card"><h3>Premium+</h3><p><strong>Everything in Premium, plus</strong></p><ul><li>Security Pro</li><li>Automation Pro</li><li>Advanced protection tools</li></ul></div></div>'
        '<p class="muted">No online checkout is connected yet. Plans are granted manually by an administrator. A feature is enabled only while the linked server entitlement or its trial is active.</p>'
        '<p class="muted">Open a server Premium management page to link your eligible account to that server.</p>'
        '<a class="button secondary" href="/">Zur Serverübersicht</a></section>'
    )
    return _render("Premium · IronWolf", body)

@app.route("/guild/<guild_id>/premium", methods=["GET", "POST"])
def guild_premium(guild_id):
    guild, response = _authorized_guild(guild_id)
    if response:
        return response
    gid = str(guild.id)
    cfg = _guild_config(guild.id)
    user_id = str(session.get("user_id", ""))
    account_level = account_plan(user_id)
    server_override = cfg.get("premium_server_override")
    plan = server_override if server_override in PLAN_FEATURES and server_override != "free" else account_level
    actor = guild.get_member(int(user_id)) if user_id.isdigit() else None
    if request.method == "POST":
        if not _csrf_ok():
            abort(400, "CSRF-Prüfung fehlgeschlagen. Bitte Seite neu laden.")
        if actor is None or not (actor.guild_permissions.administrator or actor.guild_permissions.manage_guild):
            abort(403, "Du brauchst Administrator- oder Server-verwalten-Rechte.")
        action = request.form.get("action", "link")
        if action == "start_trial":
            if cfg.get("premium_trial_used"):
                abort(403, "Die kostenlose 15-Tage-Testphase wurde auf diesem Server bereits verwendet.")
            now = datetime.now(timezone.utc)
            cfg["premium_trial_used"] = True
            cfg["premium_trial_started_at"] = now.isoformat()
            cfg["premium_trial_expires_at"] = (now + timedelta(days=15)).isoformat()
            _save_json(data_path("config.json"), _config)
            return redirect(url_for("guild_premium", guild_id=gid))
        if account_level != "free":
            current_owner = str(cfg.get("premium_owner_id") or "")
            if current_owner and current_owner != user_id:
                abort(409, "Dieser Server ist bereits mit einem anderen Premium-Konto verknüpft.")
            cfg["premium_owner_id"] = user_id
            _save_json(data_path("config.json"), _config)
            return redirect(url_for("guild_premium", guild_id=gid))
        abort(403, "Premium ist für dieses Discord-Konto nicht freigeschaltet. Bitte wende dich an den Support.")

    # Automatically attach a manually granted account plan when its owner opens Premium
    # management, unless the server is already linked to another account.
    owner_id = str(cfg.get("premium_owner_id") or "")
    if account_level != "free" and not owner_id:
        cfg["premium_owner_id"] = user_id
        _save_json(data_path("config.json"), _config)
        owner_id = user_id
    owner_matches = owner_id == user_id and bool(owner_id)
    trial_active = guild_trial_active(cfg)
    trial_used = bool(cfg.get("premium_trial_used"))
    labels = {"free": "Free", "premium": "Premium", "premium_plus": "Premium+"}
    rows = []
    for feature, label in PREMIUM_FEATURES.items():
        if feature == "branding":
            continue
        manual_server_premium = server_override in PLAN_FEATURES and server_override != "free"
        entitled = trial_active or (feature in PLAN_FEATURES.get(plan, frozenset()) and (owner_matches or manual_server_premium))
        if trial_active:
            status = "Enabled · 15-Tage-Testphase"
        else:
            status = "Enabled für diesen Server" if entitled else ("Konto berechtigt, Server noch nicht verknüpft" if feature in PLAN_FEATURES.get(plan, frozenset()) and not owner_matches else "Nicht im Kontoplan enthalten")
        feature_links = {
            "ticket": ("/guild/" + gid + "/tickets", "Ticket-System verwalten"),
            "branding": ("/guild/" + gid + "/tickets", "Ticket-Branding einstellen"),
            "custom_bot": ("/guild/" + gid + "/custom-bot", "Customize server bot profile"),
            "security": ("/guild/" + gid + "/commands", "Security-Befehle verwalten"),
            "automation": ("/guild/" + gid + "/automation", "Automation einstellen"),
            "invite_tracker": ("/guild/" + gid + "?section=general", "Invite Tracker einstellen"),
        }
        link_url, link_label = feature_links.get(feature, ("/guild/" + gid, "Serververwaltung öffnen"))
        rows.append('<div class="card"><h2>' + html.escape(label) + '</h2><p>' + html.escape(status) + '</p><p><a class="button" href="' + html.escape(link_url) + '">' + html.escape(link_label) + ' →</a></p></div>')

    if trial_active:
        plan_display = "Premium-Testphase"
        expiry_display = str(cfg.get("premium_trial_expires_at", ""))[:10]
    else:
        plan_display = labels[plan]
        expiry_display = ""

    body = (
        '<section class="card hero"><span class="pill">SERVER PREMIUM</span><h1 style="margin-top:14px">Premium für ' + html.escape(guild.name) + '</h1>'
        '<p>Premium wird aktiviert, wenn dein Konto Premium hat, dein Server manuell freigeschaltet wurde oder du die kostenlose 15-Tage-Testphase hier startest.</p>'
        '<div class="notice">Aktueller Plan: <strong>' + html.escape(plan_display) + '</strong></div>'
    )
    if trial_active:
        body += '<p>Premium ist auf diesem Server bis einschließlich <strong>' + html.escape(expiry_display) + '</strong> aktiv. Danach endet die Testphase automatisch.</p>'
    if owner_matches:
        body += '<p>Dieser Server ist mit deinem Premium-Konto verknüpft. Dein Kontoplan wird automatisch angezeigt.</p>'
    elif trial_active:
        body += '<p>Die kostenlose Testphase ist für diesen Server aktiv. Es ist keine weitere Aktion nötig.</p>'
    elif owner_id:
        body += '<p class="notice">Dieser Server ist bereits mit einem anderen Discord-Konto verknüpft. Die Verknüpfung wird nicht automatisch überschrieben.</p>'
    elif plan != "free" and account_level == "free":
        body += '<p>Dieser Server wurde manuell für Premium freigeschaltet. Alle Premium-Module sind verfügbar.</p>'
    elif plan != "free":
        body += '<form method="post">' + _csrf_field() + '<button type="submit">Mein Premium-Konto mit diesem Server verknüpfen</button></form>'
    elif not trial_used:
        body += '<p>Alle Premium-Module sind gesperrt, bis du Premium verknüpfst oder die kostenlose Testphase startest. Die 15 Tage beginnen erst nach deinem Klick.</p>'
        body += '<form method="post">' + _csrf_field() + '<input type="hidden" name="action" value="start_trial"><button type="submit" >🎁 15 Tage Premium kostenlos starten</button></form>'
    else:
        body += '<p>Die kostenlose 15-Tage-Testphase wurde bereits verwendet und ist abgelaufen. Alle Premium-Module sind gesperrt. Wende dich an den Support für Premium.</p>'
    body += '</section><div class="grid">' + "".join(rows) + '</div><p><a class="button secondary" href="/guild/' + gid + '">Zur Serververwaltung</a></p>'
    return _render(guild.name + " · Premium", body)


@app.route("/guild/<guild_id>/automation", methods=["GET", "POST"])
def guild_automation(guild_id):
    guild, response = _authorized_guild(guild_id)
    if response:
        return response
    gid = str(guild.id)
    cfg = _guild_config(guild.id)
    user_id = str(session.get("user_id", ""))
    actor = guild.get_member(int(user_id)) if user_id.isdigit() else None
    job = cfg.setdefault("automation_announcement", {"enabled": False, "channel_id": None, "message": "", "interval_minutes": 1440, "next_run_at": None})
    if request.method == "POST":
        if not _csrf_ok():
            abort(400, "CSRF-Prüfung fehlgeschlagen. Bitte Seite neu laden.")
        if actor is None or not (actor.guild_permissions.administrator or actor.guild_permissions.manage_guild):
            abort(403, "Du brauchst Administrator- oder Server-verwalten-Rechte.")
        if not guild_has_feature(cfg, "automation"):
            abort(403, "Automation Pro ist nur mit einem verknüpften Premium-Konto verfügbar.")
        action = request.form.get("automation_action", "save")
        templates = cfg.setdefault("automation_templates", [])
        if not isinstance(templates, list):
            templates = []
            cfg["automation_templates"] = templates
        if action in {"delete_template", "duplicate_template", "use_template"}:
            template_id = request.form.get("template_id", "")
            template = next((item for item in templates if isinstance(item, dict) and str(item.get("id")) == template_id), None)
            if template is None:
                abort(404, "Vorlage nicht gefunden.")
            if action == "delete_template":
                templates.remove(template)
            elif action == "duplicate_template":
                clone = dict(template)
                clone["id"] = secrets.token_hex(4)
                clone["title"] = (str(clone.get("title", "Vorlage")) + " (Kopie)")[:80]
                templates.append(clone)
                templates[:] = templates[-25:]
            else:
                job.update({"enabled": False, "channel_id": template.get("channel_id"), "message": template.get("message", ""), "interval_minutes": int(template.get("interval_minutes", 1440) or 1440), "next_run_at": None})
            _save_json(data_path("config.json"), _config)
            _audit_dashboard_change(guild.id, "Nachrichtenplaner", session.get("user_id"), "Änderung gespeichert")
            return redirect(url_for("guild_automation", guild_id=gid))
        channel_raw = request.form.get("channel_id", "").strip()
        message = request.form.get("message", "").strip()
        enabled = request.form.get("enabled") == "on"
        try:
            channel_id = int(channel_raw)
            interval = int(request.form.get("interval_minutes", "1440"))
        except (TypeError, ValueError):
            abort(400, "Bitte einen gültigen Kanal und ein gültiges Intervall auswählen.")
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            abort(400, "Bitte einen gültigen Textkanal dieses Servers auswählen.")
        if not message or len(message) > 1800:
            abort(400, "Die Nachricht muss zwischen 1 und 1800 Zeichen lang sein.")
        if interval < 60 or interval > 10080:
            abort(400, "Das Intervall muss zwischen 60 Minuten und 7 Tagen liegen.")
        if action == "save_template":
            templates.append({"id": secrets.token_hex(4), "title": request.form.get("template_title", "").strip()[:80] or "Nachrichtenvorlage", "channel_id": channel.id, "message": message, "interval_minutes": interval})
            templates[:] = templates[-25:]
        else:
            job.update({"enabled": enabled, "channel_id": channel.id, "message": message, "interval_minutes": interval})
            job["next_run_at"] = (datetime.now(timezone.utc) + timedelta(minutes=interval)).isoformat() if enabled else None
        _save_json(data_path("config.json"), _config)
        _audit_dashboard_change(guild.id, "Nachrichtenplaner", session.get("user_id"), "Änderung gespeichert")
        return redirect(url_for("guild_automation", guild_id=gid))
    if not guild_has_feature(cfg, "automation"):
        body = '<section class="card"><h1>⚙️ Automation Pro</h1><p>Geplante Nachrichten sind eine Premium-Funktion. Verknüpfe zuerst dein berechtigtes Konto auf der Premium-Seite.</p><a class="button" href="/guild/' + gid + '/premium">Manage Premium →</a></section>'
    else:
        channel_options = '<option value="">— Textkanal auswählen —</option>'
        for channel in guild.text_channels:
            selected = " selected" if str(job.get("channel_id") or "") == str(channel.id) else ""
            channel_options += '<option value="' + str(channel.id) + '"' + selected + '>' + html.escape(channel.name) + '</option>'
        checked = " checked" if job.get("enabled") else ""
        last_run = html.escape(str(job.get("last_run_at", "Noch kein Lauf")).replace("T", " ")[:19])
        next_run = html.escape(str(job.get("next_run_at", "Nicht geplant")).replace("T", " ")[:19])
        run_status = str(job.get("last_status", "pending"))
        status_label = {"success": "Letzter Lauf erfolgreich", "error": "Letzter Lauf mit Fehler", "pending": "Noch kein Lauf protokolliert"}.get(run_status, "Status unbekannt")
        status_color = "#3fbf91" if run_status == "success" else ("#e6a23c" if run_status == "error" else "#8ca0b3")
        last_error = html.escape(str(job.get("last_error", ""))[:300])
        run_summary = (
            '<div class="notice"><strong>Automationsstatus</strong><p>Letzter Lauf: ' + last_run
            + '<br>Nächster Lauf: ' + next_run + '<br><span style="color:' + status_color + '">' + status_label + '</span>'
            + (('<br>Fehler: ' + last_error) if last_error else '')
            + '</p></div>'
        )
        body = (
            '<section class="card hero"><span class="pill">PREMIUM+</span><h1 style="margin-top:14px">⚙️ Automation Pro</h1>'
            '<p>Veröffentliche automatisch wiederkehrende Server-Ankündigungen. Die erste Nachricht wird erst nach dem gewählten Intervall gesendet.</p>'
            + run_summary +
            '<div class="notice">Mindestens 60 Minuten zwischen Nachrichten; maximale Nachrichtenlänge 1800 Zeichen. Erwähnungen werden standardmäßig deaktiviert.</div>'
            '<form method="post">' + _csrf_field() +
            '<label for="channel_id">Zielkanal</label><select id="channel_id" name="channel_id" required>' + channel_options + '</select>'
            '<label for="message">Nachricht</label><textarea id="message" name="message" maxlength="1800" required>' + html.escape(str(job.get("message", ""))) + '</textarea>'
            '<label for="interval_minutes">Intervall in Minuten (60–10080)</label><input id="interval_minutes" name="interval_minutes" type="number" min="60" max="10080" step="1" value="' + str(max(60, min(10080, int(job.get("interval_minutes", 1440) or 1440)))) + '" required>'
            '<div class="check"><input type="checkbox" id="enabled" name="enabled"' + checked + '><label for="enabled">Automation aktivieren</label></div>'
            '<label for="template_title">Vorlagenname (optional)</label><input id="template_title" name="template_title" maxlength="80" placeholder="z. B. Wöchentliche Ankündigung">'
            '<button type="submit" name="automation_action" value="save">Automation speichern</button> <button class="secondary" type="submit" name="automation_action" value="save_template">Als Vorlage speichern</button></form></section>'
        )
        templates = cfg.get("automation_templates", [])
        if isinstance(templates, list) and templates:
            body += '<section class="card"><h2>📚 Gespeicherte Nachrichtenvorlagen</h2><p class="muted">Vorlagen können in die Automation geladen oder dupliziert werden. Beim Laden bleibt die Automation zunächst deaktiviert.</p>'
            for template in templates:
                if not isinstance(template, dict):
                    continue
                tid = html.escape(str(template.get("id", "")), quote=True)
                body += '<div class="row"><div><strong>' + html.escape(str(template.get("title", "Vorlage"))) + '</strong><p class="muted">' + html.escape(str(template.get("message", ""))[:100]) + '</p></div><div class="grid">'
                for action_name, action_label in (("use_template", "Vorlage laden"), ("duplicate_template", "Duplizieren"), ("delete_template", "Löschen")):
                    body += '<form method="post">' + _csrf_field() + '<input type="hidden" name="automation_action" value="' + action_name + '"><input type="hidden" name="template_id" value="' + tid + '"><button class="secondary" type="submit">' + action_label + '</button></form>'
                body += '</div></div>'
            body += '</section>'
    body += '<p><a class="button secondary" href="/guild/' + gid + '">Zur Serververwaltung</a></p>'
    return _render(guild.name + " · Automation Pro", body)



@app.route("/guild/<guild_id>/ausweis", methods=["GET", "POST"])
def guild_ausweis(guild_id):
    """Configure the member ID application and card."""
    guild, response = _authorized_guild(guild_id)
    if response:
        return response
    gid = str(guild.id)
    cfg = _config.setdefault(gid, {})
    defaults = {
        "id_card_enabled": False, "id_card_title": "🪪 Mitgliedsausweis",
        "id_card_description": "Beantrage hier deinen digitalen Mitgliedsausweis für {server}.",
        "id_card_color": "5865F2", "id_card_show_id": True, "id_card_show_joined": True,
        "id_card_show_created": False, "id_card_show_roles": True, "id_card_role_id": None,
        "id_card_auto_approve": False, "id_card_review_channel_id": None, "id_card_review_role_id": None, "id_card_output_channel_id": None,
        "id_card_questions": ["Wie lautet dein gewünschter Ausweisname?", "Warum benötigst du einen Ausweis?"],
    }
    for key, value in defaults.items():
        cfg.setdefault(key, value.copy() if isinstance(value, list) else value)
    if request.method == "POST":
        if not _csrf_ok():
            abort(400, "CSRF-Prüfung fehlgeschlagen. Bitte Seite neu laden.")
        actor = guild.get_member(int(session.get("user_id", "0")))
        if actor is None or not (actor.guild_permissions.administrator or actor.guild_permissions.manage_guild):
            abort(403, "Du brauchst Administrator- oder Server-verwalten-Rechte.")
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        color = request.form.get("color", "5865F2").strip().lstrip("#")
        if not title or len(title) > 256 or len(description) > 3500:
            abort(400, "Titel oder Description sind zu lang oder leer.")
        if not re.fullmatch(r"[0-9a-fA-F]{6}", color):
            abort(400, "Bitte eine gültige Ausweisfarbe auswählen.")
        questions = [line.strip() for line in request.form.get("questions", "").splitlines() if line.strip()]
        if len(questions) > 10:
            abort(400, "Es sind maximal 10 Ausweisfragen erlaubt.")
        if any(len(question) > 45 for question in questions):
            abort(400, "Jede Frage darf höchstens 45 Zeichen lang sein.")
        if not questions:
            questions = defaults["id_card_questions"]
        role_value = request.form.get("id_card_role_id", "").strip()
        review_role_value = request.form.get("id_card_review_role_id", "").strip()
        channel_value = request.form.get("id_card_review_channel_id", "").strip()
        output_channel_value = request.form.get("id_card_output_channel_id", "").strip()
        valid_roles = {str(role.id) for role in guild.roles if not role.is_default() and not role.managed}
        valid_channels = {str(channel.id) for channel in guild.text_channels}
        if role_value and role_value not in valid_roles:
            abort(400, "Ungültige Ausweisrolle.")
        if review_role_value and review_role_value not in valid_roles:
            abort(400, "Ungültige Teamrolle.")
        if channel_value and channel_value not in valid_channels:
            abort(400, "Ungültiger Prüfkanal.")
        if output_channel_value and output_channel_value not in valid_channels:
            abort(400, "Ungültiger Ausweis-Ausgabekanal.")
        auto = request.form.get("id_card_auto_approve") == "on"
        if not auto and (not channel_value or not review_role_value):
            abort(400, "Für manuelle Prüfung bitte einen Prüfkanal und eine Teamrolle auswählen oder automatische Ausstellung aktivieren.")
        cfg.update({
            "id_card_enabled": request.form.get("id_card_enabled") == "on",
            "id_card_title": title, "id_card_description": description, "id_card_color": color.upper(),
            "id_card_show_id": request.form.get("show_id") == "on",
            "id_card_show_joined": request.form.get("show_joined") == "on",
            "id_card_show_created": request.form.get("show_created") == "on",
            "id_card_show_roles": request.form.get("show_roles") == "on",
            "id_card_role_id": int(role_value) if role_value else None,
            "id_card_auto_approve": auto,
            "id_card_review_channel_id": int(channel_value) if channel_value else None,
            "id_card_review_role_id": int(review_role_value) if review_role_value else None,
            "id_card_output_channel_id": int(output_channel_value) if output_channel_value else None,
            "id_card_questions": questions,
        })
        _save_json(data_path("config.json"), _config)
        _audit_dashboard_change(guild.id, "Ausweissystem", session.get("user_id"),
            ("aktiviert" if cfg["id_card_enabled"] else "deaktiviert") + f"; Fragen: {len(questions)}; Modus: {'automatisch' if auto else 'Teamprüfung'}")
        return redirect(url_for("guild_ausweis", guild_id=gid, saved="1"))
    roles = [(role.id, role.name) for role in sorted(guild.roles, key=lambda item: item.position, reverse=True) if not role.is_default() and not role.managed]
    role_options = '<option value="">Keine Rolle vergeben</option>' + ''.join('<option value="' + str(rid) + '"' + (' selected' if str(cfg.get("id_card_role_id") or "") == str(rid) else '') + '>' + html.escape(name) + '</option>' for rid, name in roles)
    review_role_options = '<option value="">Teamrolle auswählen</option>' + ''.join('<option value="' + str(rid) + '"' + (' selected' if str(cfg.get("id_card_review_role_id") or "") == str(rid) else '') + '>' + html.escape(name) + '</option>' for rid, name in roles)
    channel_options = '<option value="">Prüfkanal auswählen</option>' + ''.join('<option value="' + str(ch.id) + '"' + (' selected' if str(cfg.get("id_card_review_channel_id") or "") == str(ch.id) else '') + '>' + html.escape(ch.name) + '</option>' for ch in guild.text_channels)
    output_channel_options = '<option value="">Kein zusätzlicher Kanal</option>' + ''.join('<option value="' + str(ch.id) + '"' + (' selected' if str(cfg.get("id_card_output_channel_id") or "") == str(ch.id) else '') + '>' + html.escape(ch.name) + '</option>' for ch in guild.text_channels)
    def checked(key):
        return " checked" if cfg.get(key, defaults.get(key, False)) else ""
    success = '<div class="notice">✅ Ausweis-Einstellungen gespeichert.</div>' if request.args.get("saved") == "1" else ""
    body = (
        '<section class="card"><span class="pill">IRONWOLF · ID CENTER</span><h1>🪪 Ausweissystem</h1>'
        '<p class="muted">/ausweis veröffentlicht das Antrags-Panel. Mitglieder klicken auf „Ausweis beantragen“ und beantworten bis zu 10 Fragen privat per DM. Die Antworten werden auf einem gemeinsamen Ausweisbild zusammengefasst. Standardmäßig ist das System aus.</p>' + success +
        '<form method="post">' + _csrf_field() +
        '<div class="check"><input type="checkbox" id="id_card_enabled" name="id_card_enabled"' + checked("id_card_enabled") + '><label for="id_card_enabled">Ausweissystem aktivieren</label></div>'
        '<label for="title">Ausweis-Titel</label><input id="title" name="title" maxlength="256" required value="' + html.escape(str(cfg.get("id_card_title", defaults["id_card_title"])), quote=True) + '">'
        '<label for="description">Panel-Description</label><textarea id="description" name="description" maxlength="3500">' + html.escape(str(cfg.get("id_card_description", defaults["id_card_description"]))) + '</textarea>'
        '<label for="color">Ausweisfarbe</label><input id="color" name="color" type="color" value="#' + html.escape(str(cfg.get("id_card_color", "5865F2")).lstrip("#"), quote=True) + '">'
        '<h3>Ausstellung</h3><div class="check"><input type="checkbox" id="id_card_auto_approve" name="id_card_auto_approve"' + checked("id_card_auto_approve") + '><label for="id_card_auto_approve">Anträge automatisch genehmigen und Ausweis direkt erstellen</label></div>'
        '<label for="id_card_review_role_id">Teamrolle für manuelle Prüfung</label><select id="id_card_review_role_id" name="id_card_review_role_id">' + review_role_options + '</select>'
        '<label for="id_card_review_channel_id">Kanal für Ausweisanträge / Teamprüfung</label><select id="id_card_review_channel_id" name="id_card_review_channel_id">' + channel_options + '</select>'
        '<label for="id_card_output_channel_id">Optionaler Kanal für fertige Ausweise</label><select id="id_card_output_channel_id" name="id_card_output_channel_id">' + output_channel_options + '</select>'
        '<small class="muted">Der Ausgabekanal ist optional und erhält den fertigen Ausweis inklusive Antworten. Bei manueller Prüfung müssen Teamrolle und Prüfkanal gesetzt sein. Admins und Server-Verwalter können ebenfalls prüfen.</small>'
        '<label for="id_card_role_id">Optionale Ausweisrolle nach Genehmigung</label><select id="id_card_role_id" name="id_card_role_id">' + role_options + '</select>'
        '<h3>Formularfragen (maximal 10)</h3><label for="questions">Eine Frage pro Zeile, maximal 45 Zeichen je Frage</label><textarea id="questions" name="questions" rows="10" maxlength="500">' + html.escape("\n".join(cfg.get("id_card_questions", defaults["id_card_questions"]))) + '</textarea>'
        '<h3>Informationen auf dem Ausweis</h3>'
        '<div class="check"><input type="checkbox" id="show_id" name="show_id"' + checked("id_card_show_id") + '><label for="show_id">Discord-ID anzeigen</label></div>'
        '<div class="check"><input type="checkbox" id="show_joined" name="show_joined"' + checked("id_card_show_joined") + '><label for="show_joined">Server-Beitrittsdatum anzeigen</label></div>'
        '<div class="check"><input type="checkbox" id="show_created" name="show_created"' + checked("id_card_show_created") + '><label for="show_created">Discord-Konto-Erstellungsdatum anzeigen</label></div>'
        '<div class="check"><input type="checkbox" id="show_roles" name="show_roles"' + checked("id_card_show_roles") + '><label for="show_roles">Rollen anzeigen</label></div>'
        '<button type="submit">💾 Ausweissystem speichern</button></form>'
        '<p class="notice">Der Ausweis ist ein digitaler Server-Ausweis, kein amtlicher Identitätsnachweis. Der Bot benötigt passende Rollen- und Kanalrechte.</p>'
        + '<p><a class="button secondary" href="/guild/' + gid + '">← Serverübersicht</a></p></section>'
    )
    return _render(guild.name + " · Ausweissystem", body)

@app.route("/guild/<guild_id>/birthdays", methods=["GET", "POST"])
def birthday_dashboard(guild_id):
    """Birthday settings, registration panel publishing and saved birthday overview."""
    guild, response = _authorized_guild(guild_id)
    if response:
        return response
    gid = str(guild.id)
    cfg = _guild_config(guild.id)
    birthdays = cfg.setdefault("birthdays", {})
    if request.method == "POST":
        if not _csrf_ok():
            abort(400, "CSRF-Prüfung fehlgeschlagen. Bitte Seite neu laden.")
        action = request.form.get("action", "save")
        if action == "delete_birthday":
            user_id = request.form.get("user_id", "").strip()
            if user_id in birthdays:
                birthdays.pop(user_id, None)
                cfg.setdefault("birthday_greeted_year", {}).pop(user_id, None)
                _save_json(data_path("config.json"), _config)
                if _bot and getattr(_bot, "loop", None) and _bot.loop.is_running() and hasattr(_bot, "refresh_birthday_calendar"):
                    try:
                        asyncio.run_coroutine_threadsafe(_bot.refresh_birthday_calendar(guild, cfg), _bot.loop).result(timeout=15)
                    except Exception as exc:
                        print(f"[DASHBOARD] Birthday calendar refresh after delete failed: {type(exc).__name__}: {exc}", flush=True)
                _audit_dashboard_change(guild.id, "Geburtstage", session.get("user_id"), "Geburtstag entfernt: " + user_id)
            return redirect(url_for("birthday_dashboard", guild_id=gid))
        channel_raw = request.form.get("birthday_channel", "").strip()
        signup_channel_raw = request.form.get("birthday_signup_channel", "").strip()
        role_raw = request.form.get("birthday_role_id", "").strip()
        valid_channels = {str(channel.id) for channel in guild.text_channels}
        valid_roles = {str(role.id) for role in guild.roles}
        if channel_raw and channel_raw not in valid_channels:
            abort(400, "Bitte einen gültigen Textkanal für Geburtstagsgrüße auswählen.")
        if signup_channel_raw and signup_channel_raw not in valid_channels:
            abort(400, "Bitte einen gültigen Textkanal für den Eintragen-Button auswählen.")
        if role_raw and role_raw not in valid_roles:
            abort(400, "Bitte eine gültige Geburtstagsrolle auswählen.")
        cfg["birthday_channel"] = int(channel_raw) if channel_raw else None
        cfg["birthday_signup_channel"] = int(signup_channel_raw) if signup_channel_raw else None
        cfg["birthday_role_id"] = int(role_raw) if role_raw else None
        cfg["birthday_message"] = request.form.get("birthday_message", "").strip()[:1000]
        _save_json(data_path("config.json"), _config)
        _audit_dashboard_change(guild.id, "Geburtstage", session.get("user_id"), "Geburtstags-Einstellungen gespeichert")
        # Saving settings also publishes/refreshes the signup panel when a signup channel is selected.
        if signup_channel_raw:
            if not _bot or not getattr(_bot, "loop", None) or not _bot.loop.is_running():
                abort(503, "Der Bot ist gerade nicht bereit. Einstellungen wurden gespeichert; der Button konnte noch nicht gesendet werden.")
            panel_channel = guild.get_channel(int(signup_channel_raw))
            if not isinstance(panel_channel, discord.TextChannel):
                abort(400, "Der ausgewählte Kanal ist kein Textkanal.")
            embed = discord.Embed(
                title="🎂 Geburtstag eintragen",
                description="Trage deinen Geburtstag über den Button ein. Es werden nur Tag und Monat gespeichert. An deinem Geburtstag erhältst du eine Glückwunschnachricht und – falls eingerichtet – die Geburtstagsrolle.",
                color=discord.Color.from_rgb(83, 160, 255),
            )
            embed.set_footer(text="IronWolf · Geburtstag eintragen")
            async def _send_birthday_panel():
                panel_message = await panel_channel.send(embed=embed, view=_bot.BirthdayRegisterView(), allowed_mentions=discord.AllowedMentions.none())
                cfg["birthday_signup_message_id"] = panel_message.id
                calendar_message = await panel_channel.send(
                    embed=discord.Embed(title="🎂 Geburtstagskalender", description="Die Liste wird automatisch aktualisiert, sobald Mitglieder ihren Geburtstag eintragen.", color=discord.Color.from_rgb(83, 160, 255)),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                cfg["birthday_calendar_message_id"] = calendar_message.id
                if hasattr(_bot, "refresh_birthday_calendar"):
                    await _bot.refresh_birthday_calendar(guild, cfg)
                _save_json(data_path("config.json"), _config)
                return panel_message
            try:
                asyncio.run_coroutine_threadsafe(_send_birthday_panel(), _bot.loop).result(timeout=20)
            except Exception as exc:
                print(f"[DASHBOARD] Birthday panel send failed in guild {gid}: {type(exc).__name__}: {exc}", flush=True)
                abort(502, "Einstellungen wurden gespeichert, aber der Geburtstags-Button konnte nicht gesendet werden. Prüfe Bot-Rechte und Railway-Logs.")
            _audit_dashboard_change(guild.id, "Geburtstage", session.get("user_id"), "Geburtstags-Einstellungen gespeichert und Eintragen-Button gesendet")
        return redirect(url_for("birthday_dashboard", guild_id=gid))
    selected_channel = str(cfg.get("birthday_channel") or "")
    selected_signup_channel = str(cfg.get("birthday_signup_channel") or "")
    selected_role = str(cfg.get("birthday_role_id") or "")
    channel_options = ['<option value="">— Kein Geburtstagskanal —</option>']
    signup_channel_options = ['<option value="">— Kanal auswählen —</option>']
    for channel in guild.text_channels:
        selected = " selected" if str(channel.id) == selected_channel else ""
        channel_options.append(f'<option value="{channel.id}"{selected}>{html.escape(channel.name)}</option>')
        signup_selected = " selected" if str(channel.id) == selected_signup_channel else ""
        signup_channel_options.append(f'<option value="{channel.id}"{signup_selected}>{html.escape(channel.name)}</option>')
    role_options = ['<option value="">— Keine Geburtstagsrolle —</option>']
    bot_member = guild.get_member(_bot.user.id) if _bot and _bot.user else None
    can_manage_roles = bool(bot_member and bot_member.guild_permissions.manage_roles)
    for role in reversed(guild.roles):
        if role.is_default() or role.managed:
            continue
        selected = " selected" if str(role.id) == selected_role else ""
        can_assign = bool(can_manage_roles and bot_member and role < bot_member.top_role)
        # Allow admins to select the intended role even before fixing bot permissions
        # or hierarchy. Show a warning instead of disabling every role in the dropdown.
        suffix = "" if can_assign else " (Bot-Rechte/Rollenposition prüfen)"
        role_options.append(f'<option value="{role.id}"{selected}>{html.escape(role.name + suffix)}</option>')
    month_names = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"]
    month_entries = [[] for _ in range(12)]
    for user_id, birthday in birthdays.items():
        if not isinstance(birthday, dict):
            continue
        try:
            month = int(birthday.get("month", 0))
            day = int(birthday.get("day", 0))
            if not 1 <= month <= 12 or not 1 <= day <= 31:
                continue
            member = guild.get_member(int(user_id))
            display_name = member.display_name if member else "Unbekanntes Mitglied"
            month_entries[month - 1].append((day, display_name, str(user_id)))
        except (TypeError, ValueError):
            continue
    month_cards = []
    for month_index, month_name in enumerate(month_names):
        entries = sorted(month_entries[month_index], key=lambda entry: (entry[0], entry[1].casefold()))
        if entries:
            items = ''.join('<li><strong>' + f'{day:02d}.' + '</strong> ' + html.escape(name) + '<form method="post" style="display:inline;margin-left:8px">' + _csrf_field() + '<input type="hidden" name="action" value="delete_birthday"><input type="hidden" name="user_id" value="' + html.escape(user_id) + '"><button class="secondary" style="margin:0;padding:3px 8px;font-size:12px" type="submit">Entfernen</button></form></li>' for day, name, user_id in entries)
        else:
            items = '<li class="muted">Noch keine Geburtstage</li>'
        month_cards.append('<section class="card" style="margin:0"><h2>' + month_name + '</h2><ul style="margin:0;padding-left:22px">' + items + '</ul></section>')
    rows = []
    for month_index, entries in enumerate(month_entries):
        for day, display_name, user_id in entries:
            rows.append('<tr><td>' + html.escape(display_name) + '</td><td>' + f'{day:02d}.{month_index + 1:02d}.' + '</td><td><form method="post" style="margin:0">' + _csrf_field() + '<input type="hidden" name="action" value="delete_birthday"><input type="hidden" name="user_id" value="' + html.escape(user_id) + '"><button class="secondary" type="submit">Entfernen</button></form></td></tr>')
    if not rows:
        rows.append('<tr><td colspan="3" class="muted">Noch keine Geburtstage gespeichert. Mitglieder können den Button unten verwenden oder /geburtstag nutzen.</td></tr>')
    body = '<section class="card hero"><span class="pill">🎂 COMMUNITY-MODUL</span><h1>Geburtstagskalender</h1><p>Stelle einen Eintragen-Button bereit, verwalte Geburtstagsgrüße und optional eine Tagesrolle.</p><p>Es wird nur Tag und Monat gespeichert – kein Geburtsjahr.</p></section>'
    body += '<section class="card"><h2>⚙️ Geburtstags-Einstellungen</h2><form method="post">' + _csrf_field() + '<input type="hidden" name="action" value="save"><label for="birthday_channel">Kanal für automatische Geburtstagsgrüße</label><select id="birthday_channel" name="birthday_channel">' + ''.join(channel_options) + '</select><label for="birthday_signup_channel">Kanal für den Geburtstag-eintragen-Button</label><select id="birthday_signup_channel" name="birthday_signup_channel">' + ''.join(signup_channel_options) + '</select><label for="birthday_role_id">Optionale Geburtstagsrolle (wird am Geburtstag vergeben und danach entfernt)</label><select id="birthday_role_id" name="birthday_role_id">' + ''.join(role_options) + '</select><label for="birthday_message">Nachricht</label><small>Platzhalter: {mention}, {user}, {server}</small><textarea id="birthday_message" name="birthday_message" maxlength="1000">' + html.escape(str(cfg.get("birthday_message") or "🎂 Alles Gute zum Geburtstag, {mention}! Wir wünschen dir einen tollen Tag! 🎉")) + '</textarea><button type="submit">Einstellungen speichern</button></form><p class="muted">Der Bot braucht im Zielkanal die Berechtigung, Nachrichten und Embeds zu senden. Für die Rolle muss seine höchste Rolle über der Geburtstagsrolle liegen und „Rollen verwalten“ erlaubt sein.</p></section>'
    body += '<section class="card"><h2>🗓️ Geburtstage nach Monat (' + str(len(birthdays)) + ')</h2><p class="muted">Sobald ein Mitglied seinen Geburtstag einträgt, erscheint sein Name automatisch unter dem passenden Monat.</p><div class="grid">' + ''.join(month_cards) + '</div></section>'
    body += '<section class="card"><h2>🧭 Schnellzugriff</h2><a class="button secondary" href="/guild/' + gid + '/commands">Befehle & Slash-Commands ansehen →</a> <a class="button secondary" href="/guild/' + gid + '">Zur Serverübersicht →</a></section>'
    return _render("Geburtstagskalender", body)


@app.route("/guild/<guild_id>/teamlist", methods=["GET", "POST"])
def teamlist_dashboard(guild_id):
    """Configure team roles and publish matching members to a selected Discord channel."""
    guild, response = _authorized_guild(guild_id)
    if response:
        return response
    gid = str(guild.id)
    cfg = _guild_config(guild.id)
    valid_roles = {str(role.id): role for role in guild.roles if not role.is_default() and not role.managed}
    valid_channels = {str(channel.id): channel for channel in guild.text_channels}

    if request.method == "POST":
        if not _csrf_ok():
            abort(400, "CSRF-Prüfung fehlgeschlagen. Bitte Seite neu laden.")
        selected = request.form.getlist("teamlist_role_ids")
        if any(value not in valid_roles for value in selected):
            abort(400, "Ungültige Rolle ausgewählt.")
        channel_raw = request.form.get("teamlist_channel_id", "").strip()
        if channel_raw and channel_raw not in valid_channels:
            abort(400, "Bitte einen gültigen Textkanal für die Teamliste auswählen.")
        cfg["teamlist_role_ids"] = sorted(set(selected), key=lambda value: int(value))
        cfg["teamlist_channel_id"] = int(channel_raw) if channel_raw else None
        _save_json(data_path("config.json"), _config)
        _audit_dashboard_change(guild.id, "Teamliste", session.get("user_id"),
            f"{len(cfg['teamlist_role_ids'])} Teamrollen gespeichert; Zielkanal {channel_raw or 'nicht festgelegt'}")

        if request.form.get("teamlist_action") == "publish":
            if not channel_raw:
                abort(400, "Wähle zuerst einen Zielkanal aus und speichere die Einstellungen.")
            if not selected:
                abort(400, "Wähle mindestens eine Rolle aus, um die Teamliste zu senden.")
            try:
                if not _bot or not getattr(_bot, "loop", None) or not _bot.loop.is_running():
                    raise RuntimeError("Der Bot ist nicht online.")
                target_channel = valid_channels[channel_raw]
                async def _publish_teamlist():
                    members = [member async for member in guild.fetch_members(limit=None)]
                    matching = []
                    selected_set = set(selected)
                    for member in members:
                        matching_roles = [role for role in member.roles if str(role.id) in selected_set]
                        if matching_roles:
                            matching.append((member, matching_roles))
                    matching.sort(key=lambda item: (item[0].display_name or item[0].name).casefold())
                    # Discord embeds are limited to 25 fields each; split large team lists
                    # into several embeds so the list remains readable on desktop and mobile.
                    page_size = 25
                    pages = [matching[index:index + page_size] for index in range(0, len(matching), page_size)]
                    if not pages:
                        pages = [[]]
                    for page_index, page_members in enumerate(pages, start=1):
                        embed = discord.Embed(
                            title=f"👥 Teamliste · {guild.name}"[:256],
                            description=f"**Mitglieder:** {len(matching)}",
                            color=discord.Color.blurple(),
                        )
                        if page_members:
                            for member, member_roles in page_members:
                                display_name = (member.display_name or member.name)[:256]
                                role_names = ", ".join(role.name for role in member_roles)[:1024]
                                embed.add_field(
                                    name=f"👤 {display_name}",
                                    value=role_names or "Keine passende Rolle",
                                    inline=False,
                                )
                        else:
                            embed.description += "\\n\\nAktuell wurden keine Mitglieder mit den ausgewählten Rollen gefunden."
                        embed.set_footer(text=f"IronWolf Teamliste · Seite {page_index}/{len(pages)}")
                        await target_channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
                    return len(matching)
                count = asyncio.run_coroutine_threadsafe(_publish_teamlist(), _bot.loop).result(timeout=90)
                _audit_dashboard_change(guild.id, "Teamliste gesendet", session.get("user_id"),
                    f"{count} Mitglieder nach #{target_channel.name} gesendet")
                return redirect(url_for("teamlist_dashboard", guild_id=gid, sent="1", count=str(count)))
            except Exception as exc:
                print(f"[DASHBOARD] Teamliste konnte für Guild {gid} nicht gesendet werden: {type(exc).__name__}: {exc}", flush=True)
                return _render("Teamliste konnte nicht gesendet werden",
                    '<section class="card"><h1>Teamliste konnte nicht gesendet werden</h1><p>Prüfe, ob der Bot im ausgewählten Kanal Nachrichten senden darf und der Server Members Intent aktiviert ist.</p><p class="muted">Technischer Fehler: ' + html.escape(type(exc).__name__) + '</p><a class="button secondary" href="/guild/' + gid + '/teamlist">Zurück zur Teamliste</a></section>', 500)
        return redirect(url_for("teamlist_dashboard", guild_id=gid, saved="1"))

    selected_roles = {str(value) for value in cfg.get("teamlist_role_ids", [])}
    selected_channel = str(cfg.get("teamlist_channel_id") or "")
    form = '<section class="card"><span class="pill">STAFF LIST</span><h2 style="margin-top:10px">👥 Teamliste erstellen</h2><p class="muted">Wähle einen Zielkanal und markiere die Rollen, deren Mitglieder in der Teamliste erscheinen sollen. Du kannst Rollen direkt suchen, ohne Strg oder Cmd gedrückt zu halten.</p><form method="post">' + _csrf_field()
    channel_options = ['<option value="">— Zielkanal auswählen —</option>']
    for channel in sorted(guild.text_channels, key=lambda item: item.position):
        chosen = " selected" if str(channel.id) == selected_channel else ""
        channel_options.append(f'<option value="{channel.id}"{chosen}>#{html.escape(channel.name)}</option>')
    form += '<label for="teamlist_channel_id">Kanal für die Teamliste</label><select id="teamlist_channel_id" name="teamlist_channel_id">' + "".join(channel_options) + '</select>'
    form += '<label for="teamlist_role_search">Teamrollen auswählen</label><div data-role-picker><div class="select-search-wrap"><input id="teamlist_role_search" data-role-search type="search" placeholder="Rolle suchen …" autocomplete="off" aria-label="Teamrollen durchsuchen"></div><div class="role-picker-tools"><button type="button" class="secondary" data-role-select-visible>Alle sichtbaren auswählen</button><button type="button" class="secondary" data-role-clear>Auswahl leeren</button><span class="selection-summary" data-role-summary>0 ausgewählt</span></div><small data-role-count class="select-search-count"></small><div class="role-picker">'
    for role_id, role in sorted(valid_roles.items(), key=lambda item: item[1].position, reverse=True):
        chosen = " checked" if role_id in selected_roles else ""
        role_label = html.escape(role.name)
        role_search = html.escape(role.name.casefold(), quote=True)
        form += f'<label class="role-option" data-search="{role_search}"><input type="checkbox" name="teamlist_role_ids" value="{role_id}"{chosen}><span>{role_label}</span></label>'
    form += '</div></div><div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:16px"><button type="submit" name="teamlist_action" value="save">Einstellungen speichern</button><button type="submit" name="teamlist_action" value="publish">💬 Speichern & Teamliste in Discord senden</button></div></form></section>'

    members_html = '<section class="card"><h2>👥 Vorschau der Teammitglieder</h2>'
    if request.args.get("saved") == "1":
        members_html += '<div class="notice">✅ Teamliste-Einstellungen gespeichert.</div>'
    if request.args.get("sent") == "1":
        members_html += '<div class="notice">✅ Teamliste wurde in Discord gesendet (' + html.escape(request.args.get("count", "0")) + ' Mitglieder).</div>'
    if not selected_roles:
        members_html += '<p class="muted">Wähle oben mindestens eine Rolle aus und speichere die Auswahl, um die Vorschau zu sehen.</p>'
    else:
        try:
            if not _bot or not getattr(_bot, "loop", None) or not _bot.loop.is_running():
                raise RuntimeError("Der Bot ist nicht bereit, Mitglieder abzurufen.")
            async def _fetch_team_members():
                return [member async for member in guild.fetch_members(limit=None)]
            members = asyncio.run_coroutine_threadsafe(_fetch_team_members(), _bot.loop).result(timeout=45)
            matching = []
            for member in members:
                member_roles = [role for role in member.roles if str(role.id) in selected_roles]
                if member_roles:
                    matching.append((member, member_roles))
            matching.sort(key=lambda item: (item[0].display_name or item[0].name).casefold())
            members_html += f'<p class="muted">{len(matching)} Mitglied(er) mit ausgewählten Rollen gefunden.</p>'
            if matching:
                members_html += '<div class="table-wrap"><table><thead><tr><th>Mitglied</th><th>Discord-ID</th><th>Ausgewählte Rolle(n)</th></tr></thead><tbody>'
                for member, member_roles in matching[:1000]:
                    display = html.escape(member.display_name or member.name)
                    username = html.escape(str(member))
                    role_names = ", ".join(html.escape(role.name) for role in member_roles)
                    members_html += f'<tr><td>{display}<br><small class="muted">@{username}</small></td><td><code>{member.id}</code></td><td>{role_names}</td></tr>'
                members_html += '</tbody></table></div>'
                if len(matching) > 1000:
                    members_html += '<p class="muted">Aus Gründen der Ladezeit werden maximal 1.000 Einträge angezeigt.</p>'
            else:
                members_html += '<p class="muted">Kein Mitglied besitzt aktuell eine der ausgewählten Rollen.</p>'
        except Exception as exc:
            print(f"[DASHBOARD] Teamliste konnte Mitglieder für Guild {gid} nicht abrufen: {type(exc).__name__}: {exc}", flush=True)
            members_html += '<div class="notice">Die Mitgliederliste konnte nicht geladen werden. Prüfe, ob der Bot online ist und der privilegierte „Server Members Intent“ im Discord Developer Portal aktiviert ist.</div>'
    members_html += '</section>'
    body = f'<span class="pill">IRONWOLF · TEAMLISTE</span><h1 style="margin-top:12px">👥 Teamliste · {html.escape(guild.name)}</h1><p class="muted">Die Teamliste nutzt die aktuellen Discord-Rollen und kann in einen Kanal gesendet werden.</p><div class="section-nav"><a href="/guild/{gid}">🏠 Übersicht</a><a href="/guild/{gid}/teamlist">👥 Teamliste</a><a href="/guild/{gid}/isle-roster">🦖 The Isle Spieler</a></div>' + form + members_html
    return _render("Teamliste", body)


@app.route("/guild/<guild_id>/verified-accounts", methods=["GET", "POST"])
def guild_verified_accounts(guild_id):
    """Configure gaming account verification choices and publish a panel message."""
    guild, response = _authorized_guild(guild_id)
    if response:
        return response
    gid = str(guild.id)
    cfg = _guild_config(guild.id)
    platforms = {
        "steam": ("🚂", "Steam"),
        "roblox": ("🟥", "Roblox"),
    }
    if request.method == "POST":
        if not _csrf_ok():
            abort(400, "CSRF-Prüfung fehlgeschlagen. Bitte Seite neu laden.")
        actor = guild.get_member(int(session.get("user_id", "0")))
        if actor is None or not (actor.guild_permissions.administrator or actor.guild_permissions.manage_guild):
            abort(403, "Du brauchst Administrator- oder Server-verwalten-Rechte.")
        action = request.form.get("action", "save")
        selected = [key for key in platforms if request.form.get("platform_" + key) == "on"]
        channel_ids = {str(channel.id) for channel in guild.text_channels}
        channel_raw = request.form.get("verified_accounts_channel_id", "").strip()
        if channel_raw and channel_raw not in channel_ids:
            abort(400, "Bitte wähle einen gültigen Textkanal.")
        role_ids = {str(role.id) for role in guild.roles}
        role_raws = request.form.getlist("verified_accounts_role_ids")
        role_raws = [raw.strip() for raw in role_raws if raw.strip()]
        if any(raw not in role_ids for raw in role_raws):
            abort(400, "Bitte wähle nur gültige Rollen aus.")
        message_text = request.form.get("verified_accounts_message", "").strip()
        if len(message_text) > 1800:
            abort(400, "Die Nachricht darf höchstens 1800 Zeichen lang sein.")
        cfg["verified_accounts_enabled_platforms"] = selected
        cfg["verified_accounts_channel_id"] = int(channel_raw) if channel_raw else None
        cfg["verified_accounts_role_ids"] = [int(raw) for raw in role_raws]
        cfg["verified_accounts_role_id"] = int(role_raws[0]) if role_raws else None
        cfg["verified_accounts_nickname_enabled"] = request.form.get("nickname_enabled") == "on"
        cfg["verified_accounts_message"] = message_text
        _save_json(data_path("config.json"), _config)

        if action == "send_panel":
            if not channel_raw:
                abort(400, "Wähle zuerst einen Kanal für das Verifizierungs-Panel.")
            if not selected:
                abort(400, "Enablediere mindestens einen Anbieter-Button.")
            if not _bot or not getattr(_bot, "loop", None) or not _bot.loop.is_running():
                abort(503, "Der Discord-Bot ist gerade nicht verbunden. Bitte versuche es später erneut.")
            channel = guild.get_channel(int(channel_raw))
            if channel is None or not hasattr(channel, "send"):
                abort(400, "Der ausgewählte Kanal ist nicht verfügbar.")
            labels = "\n".join(platforms[key][0] + " **" + platforms[key][1] + "**" for key in selected)
            content = message_text or "Wähle den Gaming-Anbieter aus, dessen Konto du verknüpfen möchtest:"
            embed = discord.Embed(
                title="🎮 Gaming-Account-Verifizierung",
                description=content + "\n\n**Verfügbare Anbieter**\n" + labels,
                color=discord.Color.blurple(),
            )
            embed.set_footer(text="Kontoprüfung muss über sichere, offizielle Kontonachweise erfolgen.")
            try:
                asyncio.run_coroutine_threadsafe(
                    channel.send(embed=embed, view=_GamingAccountVerifyView(selected), allowed_mentions=discord.AllowedMentions.none()),
                    _bot.loop,
                ).result(timeout=20)
            except Exception as exc:
                print(f"[DASHBOARD] Gaming verification panel send failed: {type(exc).__name__}: {exc}", flush=True)
                abort(502, "Die Nachricht konnte nicht gesendet werden. Prüfe die Bot-Berechtigung „Nachrichten senden“ und „Links einbetten“.")
            _audit_dashboard_change(guild.id, "Gaming-Account-Verifizierung", session.get("user_id"),
                "Verifizierungs-Nachricht gesendet; Anbieter: " + ", ".join(selected))
            return redirect(url_for("guild_verified_accounts", guild_id=gid, sent="1"))

        _audit_dashboard_change(guild.id, "Gaming-Account-Verifizierung", session.get("user_id"),
            "Anbieter: " + (", ".join(selected) if selected else "keiner") +
            "; Nickname-Automatik: " + ("an" if cfg["verified_accounts_nickname_enabled"] else "aus"))
        return redirect(url_for("guild_verified_accounts", guild_id=gid, saved="1"))

    selected = set(cfg.get("verified_accounts_enabled_platforms", ["steam", "roblox"]))
    channel_selected = str(cfg.get("verified_accounts_channel_id") or "")
    role_selected_values = cfg.get("verified_accounts_role_ids")
    if role_selected_values is None:
        legacy_selected = cfg.get("verified_accounts_role_id")
        role_selected_values = [legacy_selected] if legacy_selected else []
    role_selected = {str(value) for value in role_selected_values}
    platform_fields = ""
    for key, (emoji, label) in platforms.items():
        checked = " checked" if key in selected else ""
        platform_fields += ('<div class="row"><strong>' + emoji + ' ' + html.escape(label) +
            '</strong><div class="check"><input type="checkbox" id="platform_' + key +
            '" name="platform_' + key + '"' + checked + '><label for="platform_' + key +
            '">Anbieter anzeigen</label></div></div>')
    channel_options = '<option value="">— Kanal auswählen —</option>' + "".join(
        '<option value="' + str(channel.id) + '"' + (' selected' if str(channel.id) == channel_selected else '') +
        '>' + html.escape("#" + channel.name) + '</option>' for channel in guild.text_channels)
    role_options = "".join(
        '<label class="check" style="display:flex;align-items:center;gap:10px;margin:8px 0;padding:10px 12px;border:1px solid var(--border, #444);border-radius:8px">'
        '<input type="checkbox" name="verified_accounts_role_ids" value="' + str(role.id) + '"' +
        (' checked' if str(role.id) in role_selected else '') + '>'
        '<span>' + html.escape(role.name) + '</span></label>'
        for role in guild.roles if not role.is_default() and not role.managed)
    nickname_checked = ' checked' if cfg.get("verified_accounts_nickname_enabled", True) else ''
    notice = ''
    if request.args.get("saved") == "1":
        notice = '<div class="notice">✅ Verifizierungseinstellungen gespeichert.</div>'
    elif request.args.get("sent") == "1":
        notice = '<div class="notice">✅ Die Verifizierungs-Nachricht wurde in den ausgewählten Kanal gesendet.</div>'
    message_value = html.escape(str(cfg.get("verified_accounts_message", "")), quote=False)
    body = (
        '<section class="card"><span class="pill">EIGENE VERIFIZIERUNGS-KATEGORIE</span>'
        '<h1 style="margin-top:12px">🎮 Gaming-Account-Verifizierung</h1>'
        '<p class="muted">Wähle die Anbieter, den Zielkanal und schreibe die Nachricht direkt hier im Dashboard. Mit „Nachricht jetzt senden“ wird sie sofort in Discord veröffentlicht.</p>' + notice +
        '<form method="post">' + _csrf_field() + '<h2>1. Welche Anbieter sollen erscheinen?</h2>' + platform_fields +
        '<h2 style="margin-top:24px">2. Discord-Einstellungen</h2>'
        '<label for="verified_accounts_channel_id">Zielkanal für die Verifizierungs-Nachricht</label>'
        '<select id="verified_accounts_channel_id" name="verified_accounts_channel_id">' + channel_options + '</select>'
        '<label>Rollen nach erfolgreicher Verifizierung <small>Einfach die gewünschten Rollen anklicken – mehrere sind möglich.</small></label>'
        '<div style="max-height:260px;overflow-y:auto;margin:8px 0 18px">' + (role_options or '<p class="muted">Keine auswählbaren Rollen vorhanden.</p>') + '</div>' +
        '<div class="check"><input type="checkbox" id="nickname_enabled" name="nickname_enabled"' + nickname_checked +
        '><label for="nickname_enabled">Discord-Nickname nach erfolgreicher Prüfung auf den Gaming-Namen setzen</label></div>'
        '<h2 style="margin-top:24px">3. Nachrichtentext</h2>'
        '<label for="verified_accounts_message">Text der Discord-Nachricht <small>Maximal 1800 Zeichen; leer lassen für den Standardtext.</small></label>'
        '<textarea id="verified_accounts_message" name="verified_accounts_message" rows="5" maxlength="1800" placeholder="Wähle den Gaming-Anbieter aus, dessen Konto du verknüpfen möchtest:">' + message_value + '</textarea>'
        '<p class="notice">🔐 Niemals Passwörter oder Login-Codes im Discord-Chat abfragen. Die echten Konto-Anmelde- und Verifizierungsabläufe müssen separat eingerichtet sein.</p>'
        '<p class="muted">Steam nutzt Steam OpenID und Roblox benötigt eine konfigurierte OAuth-Anwendung mit exakt passender Redirect-URI in Railway und im Entwicklerportal.</p>'
        '<button type="submit" name="action" value="save">💾 Einstellungen speichern</button> '
        '<button type="submit" name="action" value="send_panel">📨 Nachricht jetzt senden</button> '
        '<a class="button secondary" href="/guild/' + gid + '">Zur Serverübersicht</a></form></section>')
    return _render(guild.name + " · Gaming-Verifizierung", body)


@app.route("/guild/<guild_id>/welcome", methods=["GET", "POST"])
def guild_welcome_settings(guild_id):
    """Dedicated welcome/goodbye settings page with its own validated save action."""
    guild, response = _authorized_guild(guild_id)
    if response:
        return response
    gid = str(guild.id)
    cfg = _guild_config(guild.id)
    result_html = ""
    if request.method == "POST":
        if not _csrf_ok():
            abort(400, "CSRF-Prüfung fehlgeschlagen. Bitte Seite neu laden.")
        actor = guild.get_member(int(session.get("user_id", "0")))
        if actor is None or not (actor.guild_permissions.administrator or actor.guild_permissions.manage_guild):
            abort(403, "Du brauchst Administrator- oder Server-verwalten-Rechte.")
        text_channel_ids = {str(channel.id) for channel in guild.text_channels}
        for key, label in (("welcome_channel", "Welcome-Kanal"), ("goodbye_channel", "Goodbye-Kanal")):
            raw = request.form.get(key, "").strip()
            if raw and raw not in text_channel_ids:
                abort(400, f"{label}: Bitte einen gültigen Textkanal auswählen.")
            cfg[key] = int(raw) if raw else None
        for key, limit in (("welcome_message", 1000), ("leave_message", 1000)):
            value = request.form.get(key, "").strip()
            if len(value) > limit:
                abort(400, f"{key}: maximal {limit} Zeichen.")
            cfg[key] = value
        for key in ("welcome_bots_enabled", "welcome_dm_enabled", "welcome_embed", "leave_enabled", "leave_embed"):
            cfg[key] = request.form.get(key) == "on"
        _save_json(data_path("config.json"), _config)
        _audit_dashboard_change(guild.id, "Welcome & Goodbye", session.get("user_id"), "Willkommens- und Abschiedseinstellungen gespeichert")
        return redirect(url_for("guild_welcome_settings", guild_id=gid, saved="1"))
    channels = _guild_channels(guild)
    def welcome_options(current):
        values = ['<option value="">— Nicht festgelegt —</option>']
        for channel_id, name in channels:
            selected = " selected" if str(channel_id) == str(current or "") else ""
            values.append(f'<option value="{channel_id}"{selected}>{html.escape(name)}</option>')
        return "".join(values)
    def welcome_check(key, label, help_text=""):
        checked = " checked" if cfg.get(key, False) else ""
        return '<div class="check"><input type="checkbox" id="' + key + '" name="' + key + '"' + checked + '><label for="' + key + '">' + html.escape(label) + '</label></div>' + ('<small class="muted">' + html.escape(help_text) + '</small>' if help_text else '')
    body = '<section class="card"><span class="pill">COMMUNITY · WELCOME</span><h1>👋 Welcome & Goodbye</h1><p>Configure public join messages, optional private welcomes and leave announcements.</p>'
    if request.args.get("saved") == "1":
        body += '<div class="notice">✅ Einstellungen gespeichert.</div>'
    body += '<form method="post">' + _csrf_field()
    body += '<label for="welcome_channel">Öffentlicher Willkommenskanal</label><select id="welcome_channel" name="welcome_channel">' + welcome_options(cfg.get("welcome_channel")) + '</select>'
    body += '<label for="welcome_message">Willkommensnachricht</label><textarea id="welcome_message" name="welcome_message" maxlength="1000" rows="4">' + html.escape(str(cfg.get("welcome_message", ""))) + '</textarea><small class="muted">Platzhalter: {mention}, {user}, {server}, {count}</small>'
    body += welcome_check("welcome_embed", "Willkommensnachricht als Embed senden")
    body += welcome_check("welcome_bots_enabled", "Auch Bot-Konten begrüßen")
    body += welcome_check("welcome_dm_enabled", "Willkommensnachricht zusätzlich privat senden")
    body += '<hr><h2>Goodbye</h2><label for="goodbye_channel">Abschiedskanal</label><select id="goodbye_channel" name="goodbye_channel">' + welcome_options(cfg.get("goodbye_channel")) + '</select>'
    body += '<label for="leave_message">Abschiedsnachricht</label><textarea id="leave_message" name="leave_message" maxlength="1000" rows="4">' + html.escape(str(cfg.get("leave_message", ""))) + '</textarea><small class="muted">Platzhalter: {user}, {server}, {count}</small>'
    body += welcome_check("leave_enabled", "Abschiedsnachrichten aktivieren")
    body += welcome_check("leave_embed", "Abschiedsnachricht als Embed senden")
    body += '<button type="submit">💾 Welcome-Einstellungen speichern</button></form></section>'
    body += '<p class="muted">Hinweis: Der Bot benötigt im gewählten Kanal die Berechtigung „Kanal ansehen“ und „Nachrichten senden“.</p>'
    return _render(guild.name + " · Welcome & Goodbye", body)


@app.route("/guild/<guild_id>/temporary-voice", methods=["GET", "POST"])
def guild_temporary_voice_settings(guild_id):
    """Dedicated temporary voice settings page with a validated save action."""
    guild, response = _authorized_guild(guild_id)
    if response:
        return response
    gid = str(guild.id)
    cfg = _guild_config(guild.id)
    if request.method == "POST":
        if not _csrf_ok():
            abort(400, "CSRF-Prüfung fehlgeschlagen. Bitte Seite neu laden.")
        actor = guild.get_member(int(session.get("user_id", "0")))
        if actor is None or not (actor.guild_permissions.administrator or actor.guild_permissions.manage_guild):
            abort(403, "Du brauchst Administrator- oder Server-verwalten-Rechte.")
        voice_ids = {str(channel.id) for channel in guild.voice_channels}
        category_ids = {str(category.id) for category in guild.categories}
        raw_trigger = request.form.get("tempvoice_trigger", "").strip()
        raw_category = request.form.get("tempvoice_category", "").strip()
        if raw_trigger and raw_trigger not in voice_ids:
            abort(400, "Bitte einen gültigen Voice-Trigger auswählen.")
        if raw_category and raw_category not in category_ids:
            abort(400, "Bitte eine gültige Zielkategorie auswählen.")
        cfg["tempvoice_trigger"] = int(raw_trigger) if raw_trigger else None
        cfg["tempvoice_category"] = int(raw_category) if raw_category else None
        _save_json(data_path("config.json"), _config)
        _audit_dashboard_change(guild.id, "Temporary Voice", session.get("user_id"), "Voice-Trigger und Zielkategorie gespeichert")
        return redirect(url_for("guild_temporary_voice_settings", guild_id=gid, saved="1"))
    voice_options = ['<option value="">— Nicht festgelegt —</option>']
    for channel in guild.voice_channels:
        selected = " selected" if str(channel.id) == str(cfg.get("tempvoice_trigger") or "") else ""
        voice_options.append(f'<option value="{channel.id}"{selected}>{html.escape(channel.name)}</option>')
    category_options = ['<option value="">— Kategorie des Trigger-Kanals verwenden —</option>']
    for category in guild.categories:
        selected = " selected" if str(category.id) == str(cfg.get("tempvoice_category") or "") else ""
        category_options.append(f'<option value="{category.id}"{selected}>{html.escape(category.name)}</option>')
    body = '<section class="card"><span class="pill">VOICE · AUTOMATION</span><h1>🔊 Temporary Voice</h1><p>Members join the trigger channel to receive their own temporary voice room. Empty rooms are automatically deleted.</p>'
    if request.args.get("saved") == "1":
        body += '<div class="notice">✅ Einstellungen gespeichert.</div>'
    body += '<form method="post">' + _csrf_field()
    body += '<label for="tempvoice_trigger">Trigger-Voice-Kanal</label><select id="tempvoice_trigger" name="tempvoice_trigger">' + "".join(voice_options) + '</select>'
    body += '<label for="tempvoice_category">Zielkategorie für neue Räume</label><select id="tempvoice_category" name="tempvoice_category">' + "".join(category_options) + '</select>'
    body += '<p class="muted">Wenn keine Kategorie gewählt wird, übernimmt der neue Raum die Kategorie des Trigger-Kanals. Der Bot benötigt „Kanäle verwalten“, „Kanäle ansehen“, „Verbinden“ und „Mitglieder verschieben“.</p>'
    body += '<button type="submit">💾 Temporary-Voice-Einstellungen speichern</button></form></section>'
    return _render(guild.name + " · Temporary Voice", body)


@app.route("/guild/<guild_id>", methods=["GET", "POST"])
def guild_page(guild_id):
    guild, response = _authorized_guild(guild_id)
    if response:
        return response
    gid = str(guild.id)
    cfg = _guild_config(guild.id)
    if request.method == "POST":
        if not _csrf_ok():
            abort(400, "CSRF-Prüfung fehlgeschlagen. Bitte Seite neu laden.")
        action = request.form.get("action", "")
        if action == "modules":
            disabled = set(cfg.get("disabled_modules", []))
            valid_modules = {item["name"] for item in _module_records(guild.id)}
            for name in valid_modules:
                if request.form.get("module:" + name) == "on":
                    disabled.discard(name)
                else:
                    disabled.add(name)
            cfg["disabled_modules"] = sorted(disabled)
            _save_json(data_path("config.json"), _config)
            _audit_dashboard_change(guild.id, "Modul-Manager", session.get("user_id"), "Enablede Module: " + ", ".join(sorted(valid_modules - disabled)))
            return redirect(url_for("guild_page", guild_id=gid, section="modules"))
        if action == "settings":
            # Keep the in-memory configuration unchanged if persistence fails.
            import copy
            original_cfg = copy.deepcopy(cfg)
            try:
                settings_section = request.form.get("settings_section", "general")
                if settings_section == "general":
                    # Missing flag is treated as enabled for legacy servers; new servers explicitly start disabled.
                    cfg["bot_enabled"] = request.form.get("bot_enabled") == "on"
                    selected_language = request.form.get("bot_language", "en")
                    if selected_language not in {"de", "en"}:
                        abort(400, "Please select English or German.")
                    cfg["bot_language"] = selected_language
                    # Welcome and goodbye destinations are configured in the Welcome module.
                    text_channel_ids = {str(item.id) for item in guild.text_channels}
                    for key, label in (("welcome_channel", "Welcome channel"), ("goodbye_channel", "Goodbye channel")):
                        raw_channel_id = request.form.get(key, "").strip()
                        if raw_channel_id and raw_channel_id not in text_channel_ids:
                            abort(400, f"{label}: Please select a valid text channel.")
                        cfg[key] = int(raw_channel_id) if raw_channel_id else None
                    for key, meta in TEXT_SETTINGS.items():
                        if key in request.form:
                            value = request.form.get(key, "").strip()
                            if len(value) > meta["max_length"]:
                                abort(400, f"{meta['label']} is too long.")
                            cfg[key] = value
                    for key in ("welcome_bots_enabled", "welcome_dm_enabled", "welcome_embed", "leave_enabled", "leave_embed", "automod_enabled", "xp_enabled"):
                        cfg[key] = request.form.get(key) == "on"
                    numeric_settings = {
                        "automod_threshold": (3, 20, 6),
                        "automod_window_seconds": (3, 30, 8),
                        "automod_timeout_minutes": (1, 60, 2),
                        "xp_cooldown_seconds": (10, 600, 60),
                        "xp_min": (1, 100, 5),
                        "xp_max": (1, 100, 15),
                    }
                    for key, (minimum, maximum, default) in numeric_settings.items():
                        try:
                            value = int(request.form.get(key, str(cfg.get(key, default))).strip())
                        except (TypeError, ValueError):
                            abort(400, f"{key}: Bitte eine ganze Zahl eingeben.")
                        if not minimum <= value <= maximum:
                            abort(400, f"{key} muss zwischen {minimum} und {maximum} liegen.")
                        cfg[key] = value
                    if cfg["xp_min"] > cfg["xp_max"]:
                        abort(400, "Die minimale XP-Zahl darf nicht größer als die maximale XP-Zahl sein.")
                    requested_invite_tracker = request.form.get("invite_tracker_enabled") == "on"
                    if requested_invite_tracker and not guild_has_feature(cfg, "invite_tracker"):
                        abort(403, "Der Invite-Tracker benötigt ein aktives Premium-Modul.")
                    cfg["invite_tracker_enabled"] = requested_invite_tracker
                    invite_channel_raw = request.form.get("invite_log_channel", "").strip()
                    valid_text_channel_ids = {str(item.id) for item in guild.text_channels}
                    if invite_channel_raw and invite_channel_raw not in valid_text_channel_ids:
                        abort(400, "Bitte einen gültigen Invite-Tracker-Logkanal auswählen.")
                    cfg["invite_log_channel"] = int(invite_channel_raw) if invite_channel_raw else None
                channel_ids = {str(item.id) for item in guild.text_channels}
                category_ids = {str(item.id) for item in guild.categories}
                role_ids = {str(item.id) for item in guild.roles}
                if settings_section == "channels":
                    for key in CHANNEL_SETTINGS:
                        if key not in request.form:
                            continue
                        raw = request.form.get(key, "").strip()
                        allowed_ids = category_ids if key == "ticket_category" else channel_ids
                        if raw and raw not in allowed_ids:
                            abort(400, "Ungültiger Kanal oder ungültige Kategorie ausgewählt.")
                        cfg[key] = int(raw) if raw else None
                if settings_section == "security":
                    for key in ("anti_nuke_enabled", "anti_raid_enabled"):
                        cfg[key] = request.form.get(key) == "on"
                    for key, meta in NUMBER_SETTINGS.items():
                        raw = request.form.get(key, str(meta["default"])).strip()
                        try:
                            value = int(raw)
                        except (TypeError, ValueError):
                            abort(400, f"{meta['label']}: Bitte eine ganze Zahl eingeben.")
                        if not meta["min"] <= value <= meta["max"]:
                            abort(400, f"{meta['label']} muss zwischen {meta['min']} und {meta['max']} liegen.")
                        cfg[key] = value
                    from security_suite import guild_state as security_guild_state, save_guild_state as save_security_guild_state
                    secure_cfg = security_guild_state(guild.id)
                    secure_cfg["alt_alerts_enabled"] = request.form.get("secure_alt_alerts_enabled") == "on"
                    try:
                        alt_days = int(request.form.get("secure_alt_min_days", str(secure_cfg.get("alt_min_days", 7))).strip())
                    except (TypeError, ValueError):
                        abort(400, "Mindestalter für neue Accounts muss eine ganze Zahl sein.")
                    if not 1 <= alt_days <= 365:
                        abort(400, "Mindestalter muss zwischen 1 und 365 Tagen liegen.")
                    secure_cfg["alt_min_days"] = alt_days
                    secure_cfg["scam_enabled"] = request.form.get("secure_scam_enabled") == "on"
                    secure_cfg["log_channel_id"] = int(request.form["secure_log_channel"]) if request.form.get("secure_log_channel") else None
                    secure_cfg["verify_channel_id"] = int(request.form["secure_verify_channel"]) if request.form.get("secure_verify_channel") else None
                    secure_cfg["verified_role_id"] = int(request.form["secure_verified_role"]) if request.form.get("secure_verified_role") else None
                    secure_cfg["unverified_role_id"] = int(request.form["secure_unverified_role"]) if request.form.get("secure_unverified_role") else None
                    raw_domains = request.form.get("secure_blocked_domains", "")
                    domains = []
                    for domain in raw_domains.splitlines():
                        domain = domain.strip().lower().removeprefix("https://").removeprefix("http://").split("/")[0]
                        if domain and re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", domain):
                            if domain not in domains:
                                domains.append(domain)
                    if len(domains) > 100:
                        abort(400, "Es können maximal 100 gesperrte Domains gespeichert werden.")
                    secure_cfg["blocked_domains"] = domains
                    save_security_guild_state(guild.id, secure_cfg)
                if settings_section == "voice":
                    voice_ids = {str(item.id) for item in guild.voice_channels}
                    raw = request.form.get("tempvoice_trigger", "").strip()
                    if raw and raw not in voice_ids:
                        abort(400, "Ungültigen Sprachkanal ausgewählt.")
                    cfg["tempvoice_trigger"] = int(raw) if raw else None
                    raw = request.form.get("tempvoice_category", "").strip()
                    if raw and raw not in category_ids:
                        abort(400, "Ungültige Kategorie ausgewählt.")
                    cfg["tempvoice_category"] = int(raw) if raw else None
                if settings_section == "roles":
                    for key in ROLE_SETTINGS:
                        raw = request.form.get(key, "").strip()
                        if raw and raw not in role_ids:
                            abort(400, "Ungültige Rolle ausgewählt.")
                        cfg[key] = int(raw) if raw else None
                    # Level rewards are editable per server; blank means no reward at that level.
                    level_roles = {}
                    for level in range(1, 51):
                        raw = request.form.get(f"level_role_{level}", "").strip()
                        if raw:
                            if raw not in role_ids:
                                abort(400, f"Ungültige Level-Rolle für Level {level}.")
                            level_roles[str(level)] = int(raw)
                    cfg["level_roles"] = level_roles
                _save_json(data_path("config.json"), _config)
                changed_keys = sorted(key for key in set(original_cfg) | set(cfg) if original_cfg.get(key) != cfg.get(key))
                _audit_dashboard_change(guild.id, "Einstellungen: " + settings_section, session.get("user_id"), "Geänderte Felder: " + ", ".join(changed_keys))
            except Exception as exc:
                from werkzeug.exceptions import HTTPException
                if isinstance(exc, HTTPException):
                    raise
                cfg.clear()
                cfg.update(original_cfg)
                import traceback
                print(f"[DASHBOARD] Failed to save settings for guild {gid}: {type(exc).__name__}: {exc}", flush=True)
                traceback.print_exc()
                return _render("Speichern fehlgeschlagen", '<div class="card"><h1>Änderungen konnten nicht gespeichert werden</h1><p>Die Einstellungen wurden zurückgesetzt. Prüfe die Railway-Logs nach <code>[DASHBOARD] Failed to save settings</code>, um den genauen technischen Fehler zu sehen.</p><a class="button secondary" href="/guild/' + gid + '">Back to settings</a></div>', 500)
        else:
            abort(400, "Unbekannte Aktion.")
        return redirect(url_for("guild_page", guild_id=gid, section=settings_section if action == "settings" else "overview", saved="1"))
    channels = _guild_channels(guild)
    roles = _guild_roles(guild)
    def options(items, current):
        out = ['<option value="">— Nicht festgelegt —</option>']
        for item_id, name in items:
            selected = " selected" if str(item_id) == str(current or "") else ""
            out.append(f'<option value="{item_id}"{selected}>{html.escape(name)}</option>')
        return "".join(out)
    section = request.args.get("section", "overview")
    settings_html = ""
    if section in ("general", "channels", "roles", "voice", "security"):
        settings_html = '<section class="card" id="settings-panel"><h2>⚙️ ' + {"general":"General settings","channels":"Channels & logs","roles":"Roles","voice":"Temporary voice channels","security":"Protection & moderation"}[section] + '</h2><form method="post">' + _csrf_field() + '<input type="hidden" name="action" value="settings"><input type="hidden" name="settings_section" value="' + section + '">'
        if section == "general":
            bot_enabled_checked = " checked" if cfg.get("bot_enabled", True) else ""
            settings_html += '<div class="check"><input type="checkbox" id="bot_enabled" name="bot_enabled"' + bot_enabled_checked + '><label for="bot_enabled">🐺 Enable IronWolf on this server</label></div><p class="muted">When disabled, the bot will not respond on this server. New servers start disabled; existing servers keep their current behavior.</p>'
            settings_html += '<label for="bot_language">🌐 Bot language</label><small>Choose the language used for bot messages and supported command responses on this server. Default: English.</small><select id="bot_language" name="bot_language"><option value="en"' + (' selected' if cfg.get("bot_language", "en") == "en" else '') + '>🇬🇧 English</option><option value="de"' + (' selected' if cfg.get("bot_language") == "de" else '') + '>🇩🇪 Deutsch</option></select>'
            settings_html += '<h3>👋 Welcome module · Channels</h3><p class="muted">Choose where IronWolf welcomes new members and where goodbye messages are posted.</p>'
            settings_html += '<label for="welcome_channel">Public welcome channel</label><select id="welcome_channel" name="welcome_channel">' + options(channels, cfg.get("welcome_channel")) + '</select>'
            settings_html += '<label for="goodbye_channel">Goodbye channel</label><select id="goodbye_channel" name="goodbye_channel">' + options(channels, cfg.get("goodbye_channel")) + '</select>'
            settings_html += '<p class="muted">👑 The Invite Tracker is a premium feature. Enable it only if this server has an active premium subscription.</p>'
            for key, label in TEXT_SETTINGS.items():
                settings_html += f'<label for="{key}">{label["label"]}</label><small>{label["help"]}</small><textarea id="{key}" name="{key}" maxlength="{label["max_length"]}">{html.escape(str(cfg.get(key, "")))}</textarea>'
            for key in ("welcome_bots_enabled", "welcome_dm_enabled", "welcome_embed", "leave_enabled", "leave_embed", "automod_enabled", "xp_enabled"):
                label = {"welcome_embed": "Send welcome message as an embed", "leave_enabled": "Enable goodbye messages", "leave_embed": "Send goodbye message as an embed"}.get(key, BOOL_SETTINGS.get(key, key))
                checked = " checked" if cfg.get(key, False) else ""
                settings_html += f'<div class="check"><input type="checkbox" id="{key}" name="{key}"{checked}><label for="{key}">{html.escape(label)}</label></div>'
            settings_html += "<h3>Configure AutoMod & XP</h3>"
            for key, label, minimum, maximum, default in [("automod_threshold", "Messages before spam detection", 3, 20, 6), ("automod_window_seconds", "Spam detection window (seconds)", 3, 30, 8), ("automod_timeout_minutes", "Timeout duration (minutes)", 1, 60, 2), ("xp_cooldown_seconds", "XP-Cooldown in Sekunden", 10, 600, 60), ("xp_min", "Minimum XP per message", 1, 100, 5), ("xp_max", "Maximum XP per message", 1, 100, 15)]:
                value = max(minimum, min(maximum, int(cfg.get(key, default) or default)))
                settings_html += f'<label for="{key}">{label}</label><input id="{key}" name="{key}" type="number" min="{minimum}" max="{maximum}" value="{value}" required>'
            if guild_has_feature(cfg, "invite_tracker"):
                checked = " checked" if cfg.get("invite_tracker_enabled", False) else ""
                settings_html += f'<div class="check"><input type="checkbox" id="invite_tracker_enabled" name="invite_tracker_enabled"{checked}><label for="invite_tracker_enabled">{html.escape(BOOL_SETTINGS["invite_tracker_enabled"])}</label></div>'
                settings_html += '<label for="invite_log_channel">Invite Tracker: Select log channel</label><small>Detected invites and invite uses will be logged here.</small><select id="invite_log_channel" name="invite_log_channel">' + options(channels, cfg.get("invite_log_channel")) + '</select>'
            else:
                settings_html += '<div class="check"><input type="checkbox" id="invite_tracker_locked" disabled><label for="invite_tracker_locked">🔒 Invite-Tracker aktivieren (Premium erforderlich)</label></div><p class="muted">The Invite Tracker remains locked until this server has an active premium subscription. <a href="/guild/' + gid + '/premium">Manage Premium →</a></p>'
        if section == "channels":
            for key, label in CHANNEL_SETTINGS.items():
                if key in {"ticket_category", "welcome_channel", "goodbye_channel"}:
                    continue
                settings_html += f'<label for="{key}">{html.escape(label)}</label><select id="{key}" name="{key}">{options(channels, cfg.get(key))}</select>'
        if section == "roles":
            for key, label in ROLE_SETTINGS.items():
                settings_html += f'<label for="{key}">{html.escape(label)}</label><select id="{key}" name="{key}">{options(roles, cfg.get(key))}</select>'
            settings_html += '<h3>🏆 Level-Rollen</h3><p class="muted">Wähle optional eine Rolle für Level 1 bis 50. Beim Erreichen des Levels versucht IronWolf, die passende Rolle zu vergeben. Die Bot-Rolle muss darüber liegen.</p>'
            configured_level_roles = cfg.get("level_roles", {}) if isinstance(cfg.get("level_roles", {}), dict) else {}
            for level in range(1, 51):
                current_role = configured_level_roles.get(str(level))
                settings_html += f'<label for="level_role_{level}">Belohnungsrolle für Level {level}</label><select id="level_role_{level}" name="level_role_{level}">{options(roles, current_role)}</select>'
            if request.args.get("role_created") == "1":
                settings_html += '<div class="notice">✅ Discord-Rolle erstellt. Du kannst sie jetzt im Nachrichten-Editor für einen Rollen-Button auswählen.</div>'
        if section == "voice":
            voice_options = options([(channel.id, channel.name) for channel in guild.voice_channels], cfg.get("tempvoice_trigger"))
            category_options = options([(category.id, category.name) for category in guild.categories], cfg.get("tempvoice_category"))
            settings_html += '<p class="muted">Wähle den Sprachkanal, über den Nutzer temporäre Räume erstellen, und die Zielkategorie.</p>'
            settings_html += f'<label for="tempvoice_trigger">{VOICE_CHANNEL_SETTINGS["tempvoice_trigger"]}</label><select id="tempvoice_trigger" name="tempvoice_trigger">{voice_options}</select>'
            settings_html += f'<label for="tempvoice_category">{CATEGORY_SETTINGS["tempvoice_category"]}</label><select id="tempvoice_category" name="tempvoice_category">{category_options}</select>'
        if section == "security":
            for key in ("anti_nuke_enabled", "anti_raid_enabled"):
                label = BOOL_SETTINGS[key]
                checked = " checked" if cfg.get(key, False) else ""
                settings_html += f'<div class="check"><input type="checkbox" id="{key}" name="{key}"{checked}><label for="{key}">{html.escape(label)}</label></div>'
            for key, meta in NUMBER_SETTINGS.items():
                value = int(cfg.get(key, meta["default"]) or meta["default"])
                value = max(meta["min"], min(meta["max"], value))
                settings_html += f'<label for="{key}">{html.escape(meta["label"])}</label><small>{html.escape(meta["help"])}</small><input id="{key}" name="{key}" type="number" min="{meta["min"]}" max="{meta["max"]}" step="1" value="{value}" required>'
            from security_suite import guild_state as security_guild_state
            secure_cfg = security_guild_state(guild.id)
            settings_html += '<h3>🔐 Advanced security features</h3><p class="muted">These options are saved per server. The link and scam filter only works when enabled.</p>'
            for key, label in [("secure_alt_alerts_enabled", "Enable alerts for very new accounts"), ("secure_scam_enabled", "Enable link and scam filter")]:
                source_key = "alt_alerts_enabled" if key == "secure_alt_alerts_enabled" else "scam_enabled"
                checked = " checked" if secure_cfg.get(source_key, False) else ""
                settings_html += f'<div class="check"><input type="checkbox" id="{key}" name="{key}"{checked}><label for="{key}">{label}</label></div>'
            alt_days = max(1, min(365, int(secure_cfg.get("alt_min_days", 7) or 7)))
            settings_html += f'<label for="secure_alt_min_days">Minimum account age for alerts (days)</label><input id="secure_alt_min_days" name="secure_alt_min_days" type="number" min="1" max="365" value="{alt_days}" required>'
            secure_channels = options(channels, secure_cfg.get("log_channel_id"))
            settings_html += '<label for="secure_log_channel">Security log channel</label><select id="secure_log_channel" name="secure_log_channel">' + secure_channels + '</select>'
            settings_html += '<label for="secure_verify_channel">Verification channel (Secure Verify)</label><select id="secure_verify_channel" name="secure_verify_channel">' + options(channels, secure_cfg.get("verify_channel_id")) + '</select>'
            settings_html += '<label for="secure_verified_role">Verified role</label><select id="secure_verified_role" name="secure_verified_role">' + options(roles, secure_cfg.get("verified_role_id")) + '</select>'
            settings_html += '<label for="secure_unverified_role">Unverified role</label><select id="secure_unverified_role" name="secure_unverified_role">' + options(roles, secure_cfg.get("unverified_role_id")) + '</select>'
            blocked_domains = secure_cfg.get("blocked_domains", [])
            settings_html += '<label for="secure_blocked_domains">Blocked domains (one per line)</label><small>Hostnames only, e.g. example.invalid. Maximum 100 entries.</small><textarea id="secure_blocked_domains" name="secure_blocked_domains" maxlength="10000">' + html.escape("\n".join(blocked_domains)) + '</textarea>'
        if section == "channels":
            settings_html += f'<label for="ticket_category">{CHANNEL_SETTINGS["ticket_category"]}</label><select id="ticket_category" name="ticket_category">{options([(category.id, category.name) for category in guild.categories], cfg.get("ticket_category"))}</select>'
        if request.args.get("saved") == "1":
            settings_html += '<div class="notice">✅ Changes saved successfully.</div>'
        settings_html += '<div style="display:flex;justify-content:flex-end;margin-top:18px"><button type="submit">💾 Save changes</button></div>'
        settings_html += '</form></section>'
        if section == "roles":
            settings_html += '<section class="card"><h3>＋ Create Discord role</h3><p class="muted">Creates a Discord role on your server. You can then select it as a role button in the Message Builder.</p><form method="post" action="/guild/' + gid + '/roles/create">' + _csrf_field() + '<label for="role_name">Role name</label><input id="role_name" name="role_name" maxlength="100" required placeholder="z. B. Gaming, Ankündigungen oder Verifiziert"><label for="role_color">Role color</label><input id="role_color" name="role_color" type="color" value="#5865F2" style="height:48px;padding:5px"><button type="submit">🎭 Create Discord role</button></form></section>'
    module_records = _module_records(guild.id)
    modules_html = '<section class="card" id="modules"><h2>🧩 Enable/disable modules</h2><p class="muted">Applies only to this server. A disabled module blocks its commands; other servers remain unchanged.</p><form method="post">' + _csrf_field() + '<input type="hidden" name="action" value="modules">'
    for item in module_records:
        checked = " checked" if item["enabled"] else ""
        modules_html += f'<div class="row"><strong>{html.escape(item["name"])}</strong><div class="check"><input type="checkbox" id="module-{html.escape(item["name"])}" name="module:{html.escape(item["name"])}"{checked}><label for="module-{html.escape(item["name"])}">Enabled</label></div></div>'
    if not module_records:
        modules_html += '<p class="muted">No modules are currently registered.</p>'
    modules_html += '<button type="submit">Save modules</button></form></section>'
    module_form = '<section class="card" id="module-template"><h2>🛠️ Create module template</h2><p class="muted">Creates a downloadable, safe discord.py cog template. The file is not loaded automatically.</p><form method="post" action="/create-module">' + _csrf_field() + f'<input type="hidden" name="guild_id" value="{gid}"><label for="module_name">Module name</label><input id="module_name" name="module_name" type="text" required maxlength="40" pattern="[A-Za-z][A-Za-z0-9_]*" placeholder="mein_modul"><label for="description">Description</label><input id="description" name="description" type="text" maxlength="120" placeholder="What does this module do?"><label for="command_name">Example command</label><input id="command_name" name="command_name" type="text" maxlength="32" pattern="[a-z][a-z0-9-]*" value="mein-modul" required><button type="submit">Create template</button></form></section>'
    section_links = [
        ("general", "⚙️ Welcome, XP & General", "XP aktivieren, Cooldown und XP-Belohnung einstellen"),
        ("channels", "📡 Channels & Logs", "Log-, Level-Up-, Ticket-, Vorschlags- und Verifizierungskanäle"),
        ("roles", "🎭 Roles & Level Rewards", "Support-, Sicherheits- und Level-Rollen konfigurieren"),
        ("voice", "🔊 Temporary Voice Channels", "Trigger und Zielkategorie für temporäre Räume"),
    ]
    settings_cards = ''.join('<a class="card dashboard-card" href="/guild/' + gid + '?section=' + key + '"><h2>' + title + '</h2><p>' + desc + '</p><span class="card-link">Open settings →</span></a>' for key, title, desc in section_links)
    if section != "overview" and section not in {"general", "channels", "roles", "voice", "security", "modules", "template"}:
        section = "overview"
    popular = [
        ("👋", "Welcome & Goodbye", "Welcome messages and private DMs", "/welcome"),
        ("🎟️", "Support Tickets", "Ticket panels and support teams", "/tickets"),
        ("🔐", "Verification", "Member verification and roles", "/verification"),
        ("✉️", "Messages & Buttons", "Create embeds and interactive buttons", "/messages"),
        ("🎭", "Reaction Roles", "Self-service member roles", "/reaction-roles"),
        ("🛡️", "Security Center", "Anti-raid and server protection", "?section=security#settings-panel"),
        ("🧪", "Security Lab", "Safely simulate raid, nuke and malicious-link attacks with no real server changes", "/security-lab"),
    ]
    categories = [
        ("👥 Community & Engagement", [("👋", "Welcome & Goodbye", "Public welcomes, goodbye messages and DMs", "/welcome"), ("🎭", "Reaction Roles", "Self-service roles through reactions", "/reaction-roles"), ("💬", "Anonymous Feedback", "Collect suggestions and feedback", "/feedback"), ("🎂", "Birthdays", "Birthday announcements and saved dates", "/birthdays"), ("📈", "Leveling & XP", "XP, cooldowns and level rewards", "?section=general#settings-panel"), ("👥", "Team List", "Publish your server team", "/teamlist")]),
        ("🛡️ Security & Moderation", [("🧪", "Security Lab", "Run /security-test to simulate attacks safely without changing server content", "/security-lab"), ("🔐", "Verification", "Verification panels, channels and roles", "/verification"), ("🎮", "Gaming Verification", "Connect supported gaming accounts", "/verified-accounts"), ("🍯", "Honeypot", "Hidden trap channel and moderation response", "/honeypot"), ("🛡️", "Ping Protection", "Protect selected members from unwanted mentions", "/ping-protection"), ("⚙️", "Protection Settings", "Anti-raid and anti-nuke controls", "?section=security#settings-panel"), ("🎮", "Action Center", "Moderation and server actions", "/actions")]),
        ("🎟️ Support & Communication", [("🎟️", "Ticket System", "Ticket panels, teams and categories", "/tickets"), ("🔊", "Temporary Voice", "Create temporary voice rooms from a trigger channel", "/temporary-voice"), ("✉️", "Message Builder", "Create messages, embeds and buttons", "/messages"), ("📣", "Automation", "Recurring announcements and templates", "/automation"), ("📝", "Applications", "Manage application forms and submissions", "/applications")]),
        ("⚙️ Server Tools & Customization", [("🎨", "Server Branding", "Customize server presentation", "/branding"), ("🎭", "Roles & Rewards", "Bot roles and level rewards", "?section=roles#settings-panel"), ("📡", "Channels & Logs", "Configure logs and destination channels", "?section=channels#settings-panel"), ("🔊", "Temporary Voice", "Voice triggers and room categories", "/temporary-voice"), ("📊", "Server Statistics", "Live counters in voice channels", "/server-statistics"), ("✨", "Premium", "Premium status and unlocked features", "/premium"), ("🚀", "Server Boosts", "Boost announcements and rewards", "/server-boost"), ("💰", "Economy", "Economy and shop features", "/economy"), ("🪪", "Member ID Cards", "Member identity cards", "/ausweis")]),
        ("📊 Insights & Administration", [("📈", "Live Statistics", "Activity and command usage", "/stats"), ("⌘", "Commands", "Enable or disable commands", "/commands"), ("⌘", "Command Log", "Configure command activity logging", "/command-log"), ("📜", "Audit Log", "Review dashboard changes", "/audit-log"), ("🩺", "System Health", "Bot connection and permissions", "/health"), ("🗄️", "Backup & Restore", "Export and restore server settings", "/backup"), ("🧩", "Module Manager", "Enable or disable bot modules", "?section=modules"), ("🛠️", "Module Template", "Create a downloadable module starter", "?section=template")]),
    ]
    def module_cards(items, popular_style=False):
        css_class = "iw-popular-card" if popular_style else "iw-module-card"
        return '<div class="iw-card-grid">' + "".join(
            '<a class="' + css_class + '" href="/guild/' + gid + suffix + '"><span class="iw-icon">' + icon + '</span><span><strong>' + html.escape(title) + '</strong><small>' + html.escape(description) + '</small></span><span class="iw-arrow">↗</span></a>'
            for icon, title, description, suffix in items
        ) + '</div>'
    module_records = _module_records(guild.id)
    command_records = _command_records(guild.id)
    metrics = (("Modules", len(module_records)), ("Active modules", sum(1 for item in module_records if item["enabled"])), ("Active commands", sum(1 for item in command_records if item["enabled"])), ("Disabled commands", sum(1 for item in command_records if not item["enabled"])))
    body = '<section class="iw-hero"><span class="iw-kicker">IRONWOLF · CONTROL PANEL</span><h1>🐺 ' + html.escape(guild.name) + '</h1><p>Your server, organized. Manage community, security and automation from one place.</p><small>SERVER ID · ' + gid + '</small></section>'
    body += '<div class="iw-metrics">' + "".join('<div><small>' + label + '</small><strong>' + str(value) + '</strong></div>' for label, value in metrics) + '</div>'
    # Render the selected section immediately after the server summary. Previously
    # settings_html was built above but never appended to the response body, so
    # settings links loaded the page without displaying their target panel.
    if settings_html:
        body += settings_html
    if section == "modules":
        body += '<section class="iw-category"><h2>🧩 Module availability</h2>' + modules_html + '</section>'
    elif section == "template":
        body += '<section class="iw-category"><h2>🛠️ Create a module template</h2>' + module_form + '</section>'
    body += '<section class="iw-section"><div class="iw-heading"><div><span class="iw-kicker">START HERE</span><h2>⭐ Starter Module</h2><p>Your starting point for setting up IronWolf on this server.</p></div><a href="/guild/' + gid + '?section=modules">Manage modules →</a></div>' + module_cards(popular, True) + '</section>'
    body += '<div class="iw-heading"><div><span class="iw-kicker">EXPLORE IRONWOLF</span><h2>All Modules</h2><p>Browse every feature, grouped by purpose.</p></div></div>'
    for title, items in categories:
        body += '<section class="iw-category"><h2>' + title + '</h2><p class="iw-category-subtitle">' + str(len(items)) + ' tools in this category</p>' + module_cards(items) + '</section>'
    body += '<section class="iw-category"><h2>⚙️ General configuration</h2><p class="iw-category-subtitle">Core server settings, channels, roles and voice options.</p>' + settings_cards + '</section>'
    return _render(guild.name + " · Control Center", body)



@app.route("/guild/<guild_id>/security-lab", methods=["GET", "POST"])
def guild_security_lab(guild_id):
    """Run real, non-destructive Discord security checks and publish their report."""
    guild, response = _authorized_guild(guild_id)
    if response:
        return response
    gid = str(guild.id)
    cfg = _guild_config(guild.id)
    result_html = ""
    if request.method == "POST":
        if not _csrf_ok():
            abort(400, "CSRF validation failed. Please reload the page.")
        actor = guild.get_member(int(session.get("user_id", "0")))
        if actor is None or not (actor.guild_permissions.administrator or actor.guild_permissions.manage_guild):
            abort(403, "Administrator or Manage Server permission required.")
        scenario = request.form.get("scenario", "all").strip().lower()
        cases = {
            "raid": ("Anti-Raid", bool(cfg.get("anti_raid_enabled")), "The real Anti-Raid setting is enabled."),
            "nuke": ("Anti-Nuke", bool(cfg.get("anti_nuke_enabled")), "The real Anti-Nuke setting is enabled."),
            "links": ("AutoMod", bool(cfg.get("automod_enabled")), "The real AutoMod setting is enabled."),
        }
        if scenario not in {"all", *cases.keys()}:
            abort(400, "Invalid security test scenario.")
        selected = list(cases.items()) if scenario == "all" else [(scenario, cases[scenario])]

        async def _run_live_checks():
            live_guild = _bot.get_guild(guild.id)
            if live_guild is None:
                raise RuntimeError("The bot is not connected to this Discord server.")
            me = live_guild.me
            if me is None:
                raise RuntimeError("The bot's server member could not be loaded.")
            findings = []
            for key, (label, enabled, detail) in selected:
                findings.append((label, enabled, detail))
            permission_checks = [
                ("View channels", me.guild_permissions.view_channel),
                ("View audit log", me.guild_permissions.view_audit_log),
                ("Manage messages (AutoMod)", me.guild_permissions.manage_messages),
                ("Moderate members (timeouts)", me.guild_permissions.moderate_members),
                ("Manage roles (role-based protections)", me.guild_permissions.manage_roles),
            ]
            security_channel_id = cfg.get("security_log_channel")
            log_channel = live_guild.get_channel(int(security_channel_id)) if security_channel_id else None
            log_ready = isinstance(log_channel, discord.TextChannel)
            if log_ready:
                perms = log_channel.permissions_for(me)
                log_ready = perms.view_channel and perms.send_messages and perms.embed_links
            return findings, permission_checks, log_channel if log_ready else None

        try:
            future = asyncio.run_coroutine_threadsafe(_run_live_checks(), _bot.loop)
            findings, permission_checks, log_channel = future.result(timeout=25)
        except Exception as exc:
            abort(400, "Live Discord check failed: " + (str(exc) or type(exc).__name__)[:250])

        passed = sum(1 for _, enabled, _ in findings if enabled)
        permission_passed = sum(1 for _, ok in permission_checks if ok)
        status_lines = []
        for label, enabled, detail in findings:
            status_lines.append(("PASS" if enabled else "WARN", label, detail if enabled else "This protection is currently disabled in the server settings."))
        for label, ok in permission_checks:
            status_lines.append(("PASS" if ok else "WARN", label, "Discord permission is granted." if ok else "The bot is missing this Discord permission."))

        published = False
        if log_channel is not None:
            async def _publish_report():
                embed = discord.Embed(
                    title="🧪 IronWolf Security Lab · Live Check",
                    description=f"Real permission/configuration checks run by {actor} ({actor.id}). No members, roles, channels or permissions were changed.",
                    color=discord.Color.green() if passed == len(findings) and permission_passed == len(permission_checks) else discord.Color.orange(),
                    timestamp=datetime.now(timezone.utc),
                )
                for status, label, detail in status_lines:
                    embed.add_field(name=f"{'✅' if status == 'PASS' else '⚠️'} {label}", value=detail[:900], inline=False)
                embed.set_footer(text="Live Discord checks · Non-destructive")
                await log_channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            try:
                asyncio.run_coroutine_threadsafe(_publish_report(), _bot.loop).result(timeout=20)
                published = True
            except Exception:
                published = False

        result_html = '<section class="card"><h2>🧪 Live Discord results</h2><p class="muted">These checks ran against the connected Discord server and its actual bot permissions. No destructive action was performed.</p>'
        for status, label, detail in status_lines:
            result_html += '<div class="notice"><strong>' + ('✅ PASS' if status == "PASS" else '⚠️ CHECK') + ' · ' + html.escape(label) + '</strong><br>' + html.escape(detail) + '</div>'
        result_html += '<p><strong>Enabled protections:</strong> ' + str(passed) + '/' + str(len(findings)) + '<br><strong>Granted bot permissions:</strong> ' + str(permission_passed) + '/' + str(len(permission_checks)) + '<br><strong>Security log report:</strong> ' + ('Posted in #' + html.escape(log_channel.name) if published else 'Not posted — configure a security log channel and grant Send Messages + Embed Links.') + '</p><p class="muted">This validates configuration and current Discord permissions. It does not generate fake raids, delete channels, or test real-time blocking against an attack.</p></section>'

    body = (
        '<section class="card"><span class="pill">IRONWOLF · SECURITY LAB</span>'
        '<h1 style="margin-top:12px">🧪 Security Lab</h1>'
        '<p class="muted">Run live, non-destructive checks against this Discord server. The bot checks real protection settings and Discord permissions, then posts a report to the configured security log channel.</p>'
        + result_html +
        '<section class="card"><h2>Run live security checks</h2><form method="post">' + _csrf_field() +
        '<label for="scenario">Check</label><select id="scenario" name="scenario">'
        '<option value="all">Full security check</option>'
        '<option value="raid">Anti-Raid</option>'
        '<option value="nuke">Anti-Nuke</option>'
        '<option value="links">AutoMod</option>'
        '</select><button type="submit">🧪 Run live check</button></form>'
        '<p class="muted">Requires Administrator or Manage Server permission. The test performs real read-only checks and writes a report to Discord; it does not launch attacks or change server settings.</p>'
        '<p><a class="button" href="/guild/' + gid + '?section=security#settings-panel">🛡️ Open Security Settings</a> '
        '<a class="button secondary" href="/guild/' + gid + '">← Back to server overview</a></p></section>'
    )
    return _render(guild.name + " · Security Lab", body)


@app.route("/guild/<guild_id>/honeypot", methods=["GET", "POST"])
def guild_honeypot(guild_id):
    guild, response = _authorized_guild(guild_id)
    if response:
        return response
    gid = str(guild.id)
    cfg = _guild_config(guild.id)
    if request.method == "POST":
        if not _csrf_ok():
            abort(400, "CSRF-Prüfung fehlgeschlagen. Bitte Seite neu laden.")
        actor = guild.get_member(int(session.get("user_id", "0")))
        if actor is None or not (actor.guild_permissions.administrator or actor.guild_permissions.manage_guild):
            abort(403, "Du brauchst Administrator- oder Server-verwalten-Rechte.")
        channel_raw = request.form.get("channel_id", "").strip()
        action = request.form.get("honeypot_action", "kick").strip().lower()
        message = request.form.get("honeypot_message", "").strip()
        enabled = request.form.get("honeypot_enabled") == "on"
        valid_channels = {str(ch.id): ch for ch in guild.text_channels}
        if channel_raw not in valid_channels:
            abort(400, "Bitte wähle einen gültigen Honeypot-Textkanal.")
        if action not in {"ban", "kick", "timeout"}:
            abort(400, "Ungültige Schutzaktion.")
        if not message or len(message) > 1900:
            abort(400, "Die Honeypot-Nachricht muss zwischen 1 und 1900 Zeichen lang sein.")
        channel = valid_channels[channel_raw]
        me = guild.me
        if enabled and (me is None or not channel.permissions_for(me).view_channel or not channel.permissions_for(me).read_message_history or not me.guild_permissions.ban_members and action == "ban" or not me.guild_permissions.kick_members and action == "kick" or not me.guild_permissions.moderate_members and action == "timeout"):
            abort(400, "Dem Bot fehlen erforderliche Kanal- oder Moderationsrechte für die gewählte Aktion.")
        cfg["honeypot_channel"] = int(channel_raw)
        cfg["honeypot_action"] = action
        cfg["honeypot_message"] = message
        cfg["honeypot_enabled"] = enabled
        _save_json(data_path("config.json"), _config)
        _audit_dashboard_change(guild.id, "Honeypot-Modul", session.get("user_id"), f"Honeypot {'aktiviert' if enabled else 'deaktiviert'}; Kanal #{channel.name}; Aktion {action}")
        if request.form.get("action") == "publish":
            async def _publish_honeypot():
                embed = discord.Embed(
                    title="🍯 IronWolf · Honeypot",
                    description=message,
                    color=discord.Color.orange(),
                    timestamp=datetime.now(timezone.utc),
                )
                embed.add_field(name="Ausgewählte Aktion", value=str(cfg.get("honeypot_action", "kick")).upper(), inline=False)
                stats = _config.setdefault("honeypot_stats", {}).setdefault(str(guild.id), {"ban": 0, "kick": 0, "timeout": 0})
                embed.add_field(name="🍯 Zähler · Ban", value=str(stats.get("ban", 0)), inline=True)
                embed.add_field(name="👢 Zähler · Kick", value=str(stats.get("kick", 0)), inline=True)
                embed.add_field(name="⏳ Zähler · Timeout", value=str(stats.get("timeout", 0)), inline=True)
                embed.set_footer(text="Sicherheits-System · IronWolf")
                posted = await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
                cfg["honeypot_message_id"] = posted.id
                _save_json(data_path("config.json"), _config)
                return posted
            try:
                asyncio.run_coroutine_threadsafe(_publish_honeypot(), _bot.loop).result(timeout=20)
            except Exception as exc:
                abort(400, "Honeypot-Nachricht konnte nicht veröffentlicht werden: " + (str(exc) or type(exc).__name__)[:200])
            return redirect(url_for("guild_honeypot", guild_id=gid, saved="published"))
        return redirect(url_for("guild_honeypot", guild_id=gid, saved="1"))
    channels = _guild_channels(guild)
    selected = str(cfg.get("honeypot_channel") or "")
    channel_options = '<option value="">— Honeypot-Kanal auswählen —</option>' + ''.join(
        '<option value="' + str(cid) + '"' + (' selected' if str(cid) == selected else '') + '>' + html.escape(name) + '</option>'
        for cid, name in channels
    )
    action = str(cfg.get("honeypot_action", "kick")).lower()
    action_options = ''.join('<option value="' + value + '"' + (' selected' if action == value else '') + '>' + label + '</option>' for value, label in [("kick", "Kick"), ("ban", "Ban"), ("timeout", "Timeout")])
    enabled = " checked" if cfg.get("honeypot_enabled", False) else ""
    message = html.escape(str(cfg.get("honeypot_message") or "🍯 **Honeypot aktiv**\\nDieser Kanal dient der Sicherheitsüberwachung. Bitte hier nicht schreiben."))
    success = {"1": "Einstellungen gespeichert.", "published": "Honeypot-Nachricht veröffentlicht."}.get(request.args.get("saved", ""), "")
    body = '<span class="pill">IRONWOLF · SECURITY</span><h1 style="margin-top:12px">🍯 Honeypot-Schutz</h1><p class="muted">Wie bei Honeypot-Bots dient dieser Lockkanal als Falle: Normale Mitglieder sollen dort nicht schreiben. Wenn jemand es trotzdem tut, führt IronWolf die gewählte Aktion aus. Stelle sicher, dass der Kanal für normale Mitglieder verborgen ist und nur der Bot ihn lesen kann.</p>'
    if success:
        body += '<div class="notice">✅ ' + html.escape(success) + '</div>'
    body += '<section class="card"><h2>Honeypot konfigurieren</h2><form method="post">' + _csrf_field()
    body += '<label for="channel_id">Versteckter Honeypot-Textkanal</label><select id="channel_id" name="channel_id" required>' + channel_options + '</select>'
    body += '<label for="honeypot_action">Aktion bei Nachricht</label><select id="honeypot_action" name="honeypot_action">' + action_options + '</select>'
    body += '<label for="honeypot_message">Nachricht für den Honeypot-Kanal</label><textarea id="honeypot_message" name="honeypot_message" maxlength="1900" rows="5" required>' + message + '</textarea>'
    body += '<div class="check"><input type="checkbox" id="honeypot_enabled" name="honeypot_enabled"' + enabled + '><label for="honeypot_enabled">Honeypot-Schutz aktivieren</label></div>'
    body += '<p class="muted">Hinweis: Die Schutzaktion wird nur auf normale Mitglieder angewendet; Serverinhaber und Administratoren werden vom vorhandenen Bot-Handler ausgenommen.</p>'
    body += '<button type="submit" name="action" value="save">💾 Einstellungen speichern</button> <button type="submit" name="action" value="publish" class="secondary">📨 Nachricht veröffentlichen</button></form></section>'
    body += '<p><a class="button secondary" href="/guild/' + gid + '">← Zur Serververwaltung</a></p>'
    return _render(guild.name + " · Honeypot", body)


@app.route("/guild/<guild_id>/reaction-roles", methods=["GET", "POST"])
def guild_reaction_roles(guild_id):
    guild, response = _authorized_guild(guild_id)
    if response:
        return response
    gid = str(guild.id)
    cfg = _guild_config(guild.id)
    if request.method == "POST":
        if not _csrf_ok():
            abort(400, "CSRF-Prüfung fehlgeschlagen. Bitte Seite neu laden.")
        actor = guild.get_member(int(session.get("user_id", "0")))
        if actor is None or not (actor.guild_permissions.administrator or actor.guild_permissions.manage_roles):
            abort(403, "Du brauchst Administrator- oder Rollen-verwalten-Rechte.")
        action = request.form.get("action", "add").strip()
        records = cfg.setdefault("reaction_roles", [])
        if not isinstance(records, list):
            records = []
            cfg["reaction_roles"] = records
        if action == "delete":
            record_id = request.form.get("record_id", "").strip()
            cfg["reaction_roles"] = [item for item in records if str(item.get("id")) != record_id]
            _save_json(data_path("config.json"), _config)
            _audit_dashboard_change(guild.id, "Reaktionsrollen", session.get("user_id"), "Reaktionsrolle gelöscht: " + record_id[:80])
            return redirect(url_for("guild_reaction_roles", guild_id=gid, saved="deleted"))
        if action != "add":
            abort(400, "Unbekannte Aktion.")
        channel_raw = request.form.get("channel_id", "").strip()
        message_text = request.form.get("message_text", "").strip()
        role_raw = request.form.get("role_id", "").strip()
        emoji = (request.form.get("emoji", "").strip() or request.form.get("emoji_manual", "").strip())
        valid_channels = {str(item.id): item for item in guild.text_channels}
        valid_roles = {str(item.id): item for item in guild.roles}
        if channel_raw not in valid_channels:
            abort(400, "Please select a valid text channel.")
        if role_raw not in valid_roles or not role_raw:
            abort(400, "Bitte eine gültige Rolle auswählen.")
        if not message_text or len(message_text) > 2000:
            abort(400, "Bitte gib eine Nachricht mit 1 bis 2000 Zeichen ein.")
        if len(emoji) > 100:
            abort(400, "Emoji must be 100 characters or fewer.")
        role = valid_roles[role_raw]
        me = guild.me
        if me is None or not me.guild_permissions.manage_roles or role >= me.top_role:
            abort(400, "Der Bot benötigt „Rollen verwalten“ und seine höchste Rolle muss über der ausgewählten Rolle liegen.")
        channel = valid_channels[channel_raw]
        async def _configure_reaction_role():
            message = await channel.send(message_text, allowed_mentions=discord.AllowedMentions.none())
            if emoji:
                await message.add_reaction(emoji)
            return message.id
        try:
            message_id = asyncio.run_coroutine_threadsafe(_configure_reaction_role(), _bot.loop).result(timeout=20)
        except discord.Forbidden:
            abort(403, "Der Bot darf in diesem Kanal keine Nachricht senden oder darauf reagieren.")
        except (discord.HTTPException, ValueError) as exc:
            abort(400, "Nachricht oder Emoji konnte nicht verarbeitet werden: " + str(exc)[:200])
        message_raw = str(message_id)
        records.append({"id": secrets.token_hex(8), "channel_id": int(channel_raw), "message_id": message_id, "emoji": emoji, "role_id": int(role_raw)})
        _save_json(data_path("config.json"), _config)
        emoji_label = emoji or "Any emoji"
        _audit_dashboard_change(guild.id, "Reaction Roles", session.get("user_id"), f"Reaction role created: {emoji_label} -> {role.name} on message {message_raw}")
        return redirect(url_for("guild_reaction_roles", guild_id=gid, saved="added"))
    records = cfg.get("reaction_roles", [])
    if not isinstance(records, list):
        records = []
    channels = _guild_channels(guild)
    roles = _guild_roles(guild)
    channel_options = '<option value="">— Textkanal auswählen —</option>' + ''.join(f'<option value="{cid}">{html.escape(name)}</option>' for cid, name in channels)
    role_options = '<option value="">— Rolle auswählen —</option>' + ''.join(f'<option value="{rid}">{html.escape(name)}</option>' for rid, name in roles)
    server_emojis = sorted(guild.emojis, key=lambda item: item.name.lower())
    emoji_options = '<option value="">— Server-Emoji auswählen —</option>' + ''.join(
        f'<option value="{html.escape(str(item), quote=True)}">{html.escape(item.name)} {html.escape(str(item))}</option>'
        for item in server_emojis
    )
    body = '<span class="pill">IRONWOLF · SERVER CONTROL</span><h1 style="margin-top:12px">🎭 Reaction Roles</h1><p class="muted">Write your message, choose a channel and role, and optionally select an emoji. If you leave the emoji empty, reactions with any emoji will grant the role. Removing a reaction removes the role.</p>'
    if request.args.get("saved") == "added":
        body += '<div class="notice">✅ Reaction role saved and message sent.</div>'
    elif request.args.get("saved") == "deleted":
        body += '<div class="notice">✅ Reaktionsrolle gelöscht.</div>'
    body += '<section class="card"><h2>＋ Create reaction role & send message</h2><form method="post">' + _csrf_field() + '<input type="hidden" name="action" value="add">'
    body += '<label for="channel_id">Textkanal</label><select id="channel_id" name="channel_id" required>' + channel_options + '</select>'
    body += '<label for="message_text">Deine Nachricht</label><textarea id="message_text" name="message_text" required maxlength="2000" rows="5" placeholder="Schreibe hier die Nachricht, die im ausgewählten Kanal erscheinen soll …"></textarea><small>Maximal 2000 Zeichen. Die Nachricht wird vom Bot gesendet.</small>'
    body += '<label for="emoji">Server emoji (optional)</label><select id="emoji" name="emoji">' + emoji_options + '</select>'
    if not server_emojis:
        body += '<p class="muted">Auf diesem Server sind keine benutzerdefinierten Emojis verfügbar. Du kannst stattdessen ein normales Emoji eingeben.</p>'
    body += '<label for="emoji_manual">Or enter an emoji manually (optional)</label><input id="emoji_manual" name="emoji_manual" type="text" maxlength="100" placeholder="e.g. ✅ (overrides the selection)"><small>Leave both emoji fields empty to accept every emoji reaction.</small>'
    body += '<label for="role_id">Role to assign</label><select id="role_id" name="role_id" required>' + role_options + '</select><button type="submit">📨 Nachricht senden & Reaktionsrolle erstellen</button></form></section>'
    body += '<section class="card"><h2>📋 Configured reaction roles</h2>'
    if not records:
        body += '<p class="muted">Noch keine Reaktionsrollen eingerichtet.</p>'
    else:
        body += '<div style="overflow-x:auto"><table><thead><tr><th>Emoji</th><th>Rolle</th><th>Kanal</th><th>Nachrichten-ID</th><th>Aktion</th></tr></thead><tbody>'
        for item in records:
            role = guild.get_role(int(item.get("role_id", 0))) if str(item.get("role_id", "")).isdigit() else None
            channel = guild.get_channel(int(item.get("channel_id", 0))) if str(item.get("channel_id", "")).isdigit() else None
            body += '<tr><td>' + html.escape(str(item.get("emoji", "")) or "Any emoji") + '</td><td>' + html.escape(role.name if role else "Gelöschte Rolle") + '</td><td>' + html.escape("#" + channel.name if channel else "Gelöschter Kanal") + '</td><td><code>' + html.escape(str(item.get("message_id", ""))) + '</code></td><td><form method="post" style="margin:0" onsubmit="return confirm(\'Diese Reaktionsrolle löschen?\')">' + _csrf_field() + '<input type="hidden" name="action" value="delete"><input type="hidden" name="record_id" value="' + html.escape(str(item.get("id", "")), quote=True) + '"><button type="submit" class="secondary">Löschen</button></form></td></tr>'
        body += '</tbody></table></div>'
    body += '</section><p><a class="button secondary" href="/guild/' + gid + '">← Zur Serververwaltung</a></p>'
    return _render(guild.name + " · Reaktionsrollen", body)


@app.route("/guild/<guild_id>/feedback", methods=["GET", "POST"])
def guild_feedback(guild_id):
    guild, response = _authorized_guild(guild_id)
    if response:
        return response
    gid = str(guild.id)
    cfg = _guild_config(guild.id)
    if request.method == "POST":
        if not _csrf_ok():
            abort(400, "CSRF-Prüfung fehlgeschlagen. Bitte Seite neu laden.")
        actor = guild.get_member(int(session.get("user_id", "0")))
        if actor is None or not (actor.guild_permissions.administrator or actor.guild_permissions.manage_guild):
            abort(403, "Du brauchst Administrator- oder Server-verwalten-Rechte.")
        action = request.form.get("action", "save").strip()
        title = request.form.get("title", "Feedback").strip()[:45] or "Feedback"
        description = request.form.get("description", "").strip()[:4000]
        channel_raw = request.form.get("channel_id", "").strip()
        panel_channel_raw = request.form.get("panel_channel_id", "").strip()
        enabled = request.form.get("enabled") == "on"
        valid_channels = {str(item.id): item for item in guild.text_channels}
        if channel_raw not in valid_channels:
            abort(400, "Bitte wähle einen gültigen Kanal für eingehendes Feedback.")
        if panel_channel_raw not in valid_channels:
            panel_channel_raw = str(cfg.get("feedback_panel_channel_id") or channel_raw)
        if panel_channel_raw not in valid_channels:
            abort(400, "Bitte wähle einen gültigen Kanal für das Feedback-Panel.")
        channel = valid_channels[channel_raw]
        panel_channel = valid_channels[panel_channel_raw]
        me = guild.me
        for target in (channel, panel_channel):
            if me is None or not target.permissions_for(me).send_messages or not target.permissions_for(me).embed_links:
                abort(400, "Der Bot benötigt in beiden Feedback-Kanälen die Rechte „Nachrichten senden“ und „Links einbetten“.")
        cfg["feedback_title"] = title
        cfg["feedback_description"] = description
        cfg["feedback_channel_id"] = int(channel_raw)
        cfg["feedback_submission_channel_id"] = int(channel_raw)
        cfg["feedback_panel_channel_id"] = int(panel_channel_raw)
        cfg["feedback_enabled"] = enabled
        _save_json(data_path("config.json"), _config)
        _audit_dashboard_change(guild.id, "Feedback-Modul", session.get("user_id"), f"Feedback {'aktiviert' if enabled else 'deaktiviert'}; Zielkanal #{channel.name}")
        if action == "publish":
            if not enabled:
                abort(400, "Enablediere das Modul, bevor du das Feedback-Panel veröffentlichst.")
            embed = discord.Embed(
                title=title,
                description=description or "Klicke auf den Button, um anonym dein Feedback abzugeben.",
                color=discord.Color.blurple(),
            )
            embed.set_footer(text="IronWolf · Feedback")
            async def _publish_feedback_panel():
                return await panel_channel.send(embed=embed, view=_bot.AnonymousFeedbackView(), allowed_mentions=discord.AllowedMentions.none())
            try:
                asyncio.run_coroutine_threadsafe(_publish_feedback_panel(), _bot.loop).result(timeout=20)
            except Exception as exc:
                abort(400, "Feedback-Panel konnte nicht gesendet werden: " + (str(exc) or type(exc).__name__)[:250])
            _audit_dashboard_change(guild.id, "Feedback-Modul", session.get("user_id"), f"Feedback-Panel in #{panel_channel.name} veröffentlicht; Einsendungen nach #{channel.name}")
            return redirect(url_for("guild_feedback", guild_id=gid, saved="published"))
        return redirect(url_for("guild_feedback", guild_id=gid, saved="saved"))
    channels = _guild_channels(guild)
    selected_channel = str(cfg.get("feedback_submission_channel_id") or cfg.get("feedback_channel_id") or "")
    selected_panel_channel = str(cfg.get("feedback_panel_channel_id") or selected_channel)
    channel_options = '<option value="">— Kanal auswählen —</option>' + ''.join(
        '<option value="' + str(cid) + '"' + (' selected' if str(cid) == selected_channel else '') + '>' + html.escape(name) + '</option>'
        for cid, name in channels
    )
    panel_channel_options = '<option value="">— Panel-Kanal auswählen —</option>' + ''.join(
        '<option value="' + str(cid) + '"' + (' selected' if str(cid) == selected_panel_channel else '') + '>' + html.escape(name) + '</option>'
        for cid, name in channels
    )
    title_value = html.escape(str(cfg.get("feedback_title") or "💬 Feedback"), quote=True)
    description_value = html.escape(str(cfg.get("feedback_description") or "Deine Meinung ist uns wichtig. Klicke auf den Button, um anonym Feedback abzugeben."), quote=True)
    checked = " checked" if cfg.get("feedback_enabled", False) else ""
    body = '<span class="pill">IRONWOLF · SERVER CONTROL</span><h1 style="margin-top:12px">💬 Anonymes Feedback</h1>'
    body += '<p class="muted">Titel, Description und Zielkanal konfigurieren. Mitglieder öffnen über den Button ein Formular; die Einsendung wird ohne Discord-Namen oder Nutzer-ID im gewählten Kanal veröffentlicht.</p>'
    if request.args.get("saved") == "saved":
        body += '<div class="notice">✅ Feedback-Einstellungen gespeichert.</div>'
    elif request.args.get("saved") == "published":
        body += '<div class="notice">✅ Feedback-Panel wurde im ausgewählten Kanal veröffentlicht.</div>'
    body += '<section class="card"><h2>⚙️ Feedback-Modul einstellen</h2><form method="post">' + _csrf_field()
    body += '<input type="hidden" name="action" value="save"><label for="title">Titel der Nachricht</label><input id="title" name="title" maxlength="45" required value="' + title_value + '">'
    body += '<label for="description">Description</label><textarea id="description" name="description" maxlength="4000" rows="5">' + description_value + '</textarea>'
    body += '<label for="channel_id">Kanal für abgesendete Feedback-Nachrichten</label><select id="channel_id" name="channel_id" required>' + channel_options + '</select>'
    body += '<label for="panel_channel_id">Kanal, in dem das Feedback-Panel veröffentlicht wird</label><select id="panel_channel_id" name="panel_channel_id" required>' + panel_channel_options + '</select>'
    body += '<div class="check"><input id="enabled" type="checkbox" name="enabled"' + checked + '><label for="enabled">Feedback-Modul aktivieren</label></div>'
    body += '<button type="submit">💾 Einstellungen speichern</button></form>'
    body += '<form method="post" style="margin-top:14px">' + _csrf_field() + '<input type="hidden" name="action" value="publish">'
    body += '<input type="hidden" name="title" value="' + title_value + '"><input type="hidden" name="description" value="' + description_value + '"><input type="hidden" name="channel_id" value="' + html.escape(selected_channel, quote=True) + '"><input type="hidden" name="panel_channel_id" value="' + html.escape(selected_panel_channel, quote=True) + '">'
    body += '<input type="hidden" name="enabled" value="' + ('on' if cfg.get("feedback_enabled", False) else '') + '">'
    body += '<button type="submit" class="secondary">📨 Feedback-Panel im ausgewählten Kanal veröffentlichen</button></form></section>'
    body += '<section class="card"><h2>🔒 Anonymität</h2><p>Im Feedback-Kanal werden nur der Feedback-Text, der konfigurierte Titel und der Zeitpunkt angezeigt. Der Bot hängt keine Absender-ID und keinen Benutzernamen an. Beachte: Discord selbst und Server-Administratoren mit Zugriff auf Logs oder externe Protokolle können außerhalb dieser Nachricht eventuell technische Informationen sehen.</p></section>'
    body += '<p><a class="button secondary" href="/guild/' + gid + '">← Zur Serververwaltung</a></p>'
    return _render(guild.name + " · Feedback-Modul", body)


@app.route("/guild/<guild_id>/roles/create", methods=["POST"])
def guild_create_role(guild_id):
    """Create a Discord role from the dashboard so it can be used in role buttons."""
    guild, response = _authorized_guild(guild_id)
    if response:
        return response
    if not _csrf_ok():
        abort(400, "CSRF-Prüfung fehlgeschlagen. Bitte Seite neu laden.")
    actor = guild.get_member(int(session.get("user_id", "0")))
    if actor is None or not (actor.guild_permissions.administrator or actor.guild_permissions.manage_roles):
        abort(403, "Du brauchst Administrator- oder Rollen-verwalten-Rechte.")
    bot_member = guild.me
    if bot_member is None or not bot_member.guild_permissions.manage_roles:
        abort(403, "Der Bot benötigt die Discord-Berechtigung „Rollen verwalten“.")
    name = request.form.get("role_name", "").strip()[:100]
    if not name:
        abort(400, "Bitte gib einen Namen für die neue Rolle ein.")
    color_raw = request.form.get("role_color", "#5865F2").strip().lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", color_raw):
        abort(400, "Bitte wähle eine gültige Role color.")
    async def _create():
        return await guild.create_role(name=name, colour=discord.Colour(int(color_raw, 16)), reason=f"Dashboard-Rolle erstellt von {actor}")
    try:
        created = asyncio.run_coroutine_threadsafe(_create(), _bot.loop).result(timeout=20)
    except Exception as exc:
        print(f"[DASHBOARD] Role creation failed in guild {guild_id}: {type(exc).__name__}: {exc}", flush=True)
        abort(502, "Die Rolle konnte nicht erstellt werden. Prüfe Bot-Rechte und die Rollenposition in Discord.")
    _audit_dashboard_change(guild.id, "Discord-Rolle erstellt", session.get("user_id"), created.name)
    return redirect(url_for("guild_page", guild_id=str(guild.id), section="roles", role_created="1"))


@app.route("/guild/<guild_id>/ping-protection", methods=["GET", "POST"])
def guild_ping_protection(guild_id):
    """Configure protected role/member mentions for this guild."""
    guild, response = _authorized_guild(guild_id)
    if response:
        return response
    gid = str(guild.id)
    cfg = _config.setdefault(gid, {})
    if request.method == "POST":
        if not _csrf_ok():
            abort(400, "CSRF-Prüfung fehlgeschlagen. Bitte Seite neu laden.")
        actor = guild.get_member(int(session.get("user_id", "0")))
        if actor is None or not (actor.guild_permissions.administrator or actor.guild_permissions.manage_guild):
            abort(403, "Du brauchst Administrator- oder Server-verwalten-Rechte.")

        # The X next to a protected role removes it immediately from the protection list.
        remove_role_id = request.form.get("remove_protected_role_id", "").strip()
        if remove_role_id:
            if not remove_role_id.isdigit():
                abort(400, "Ungültige Rolle.")
            current_roles = {str(value) for value in cfg.get("ping_protected_role_ids", [])}
            if remove_role_id not in current_roles:
                abort(400, "Diese Rolle ist nicht als geschützt eingetragen.")
            cfg["ping_protected_role_ids"] = sorted(
                int(value) for value in current_roles if value != remove_role_id
            )
            _save_json(data_path("config.json"), _config)
            _audit_dashboard_change(guild.id, "Ping-Schutz", session.get("user_id"), f"Geschützte Rolle {remove_role_id} entfernt")
            return redirect(url_for("guild_ping_protection", guild_id=gid, saved="1"))

        valid_role_ids = {str(role.id) for role in guild.roles if not role.is_default() and not role.managed}
        valid_member_ids = {str(member.id) for member in guild.members if not member.bot}
        selected_roles = request.form.getlist("protected_role_ids")
        selected_members = request.form.getlist("protected_member_ids")
        if any(value not in valid_role_ids for value in selected_roles):
            abort(400, "Ungültige geschützte Rolle ausgewählt.")
        if any(value not in valid_member_ids for value in selected_members):
            abort(400, "Ungültige geschützte Person ausgewählt. Falls sie fehlt, muss der Bot die Mitgliederliste laden können.")
        if len(set(selected_roles)) > 10:
            abort(400, "Du kannst maximal 10 Rollen gleichzeitig vor Pings schützen.")
        if len(set(selected_members)) > 10:
            abort(400, "Du kannst maximal 10 Personen gleichzeitig vor Pings schützen.")

        # Protection starts fully off and with no protected roles or members.
        cfg["ping_protection_enabled"] = request.form.get("ping_protection_enabled") == "on"
        cfg["ping_protected_role_ids"] = sorted({int(value) for value in selected_roles})
        cfg["ping_protected_member_ids"] = sorted({int(value) for value in selected_members})
        _save_json(data_path("config.json"), _config)
        _audit_dashboard_change(guild.id, "Ping-Schutz", session.get("user_id"),
            ("aktiviert" if cfg["ping_protection_enabled"] else "deaktiviert") +
            f"; {len(selected_roles)} Rollen, {len(selected_members)} Personen geschützt")
        return redirect(url_for("guild_ping_protection", guild_id=gid, saved="1"))

    protected_roles = {str(value) for value in cfg.get("ping_protected_role_ids", [])}
    protected_members = {str(value) for value in cfg.get("ping_protected_member_ids", [])}
    role_options = ''.join(
        '<label class="check ping-choice" data-search="' + html.escape(role.name.casefold(), quote=True) + '" style="display:flex;align-items:center;gap:10px;padding:8px 10px;border:1px solid var(--border,#ddd);border-radius:8px;margin:5px 0">'
        '<input type="checkbox" name="protected_role_ids" value="' + str(role.id) + '"' + (' checked' if str(role.id) in protected_roles else '') + '>'
        '<span>' + html.escape(role.name) + '</span></label>'
        for role in sorted(guild.roles, key=lambda item: item.position, reverse=True)
        if not role.is_default() and not role.managed
    )
    member_options = ''.join(
        '<label class="check ping-choice" data-search="' + html.escape((member.display_name + ' ' + str(member.id)).casefold(), quote=True) + '" style="display:flex;align-items:center;gap:10px;padding:8px 10px;border:1px solid var(--border,#ddd);border-radius:8px;margin:5px 0">'
        '<input type="checkbox" name="protected_member_ids" value="' + str(member.id) + '"' + (' checked' if str(member.id) in protected_members else '') + '>'
        '<span>' + html.escape(member.display_name) + ' <small class="muted">(' + str(member.id) + ')</small></span></label>'
        for member in sorted(guild.members, key=lambda item: item.display_name.casefold())
        if not member.bot
    )
    protected_role_rows = ''.join(
        '<div class="check" style="display:flex;align-items:center;justify-content:space-between;gap:12px">'
        '<span>' + html.escape(guild.get_role(int(role_id)).name if role_id.isdigit() and guild.get_role(int(role_id)) else "Unbekannte Rolle") + '</span>'
        '<button type="submit" name="remove_protected_role_id" value="' + html.escape(role_id) + '" class="button secondary" formnovalidate aria-label="Rolle aus Ping-Schutz entfernen" title="Nicht mehr schützen">✕</button></div>'
        for role_id in sorted(protected_roles)
    )
    if not protected_role_rows:
        protected_role_rows = '<p class="muted">Noch keine geschützten Rollen hinzugefügt.</p>'
    protected_member_names = [
        html.escape(member.display_name) for member in guild.members
        if not member.bot and str(member.id) in protected_members
    ]
    if not protected_member_names:
        protected_member_summary = '<p class="muted">Noch keine geschützten Personen hinzugefügt.</p>'
    else:
        protected_member_summary = '<p class="muted">Geschützte Personen: ' + ', '.join(protected_member_names) + '</p>'
    enabled = " checked" if cfg.get("ping_protection_enabled", False) else ""
    success = '<div class="notice">✅ Ping-Schutz-Einstellungen gespeichert.</div>' if request.args.get("saved") == "1" else ""
    body = (
        '<section class="card"><span class="pill">IRONWOLF · SECURITY</span><h1>🛡️ Ping-Schutz</h1>'
        '<p class="muted">Standardmäßig ist der Ping-Schutz ausgeschaltet und es sind keine Rollen oder Personen geschützt. Schalte ihn manuell ein und wähle die gewünschten Rollen oder Personen aus.</p>' + success +
        '<form method="post">' + _csrf_field() +
        '<div class="check"><input type="checkbox" id="ping_protection_enabled" name="ping_protection_enabled"' + enabled + '><label for="ping_protection_enabled">Ping-Schutz aktivieren</label></div>'
        '<h3>Geschützte Rollen <small class="muted">(max. 10)</small></h3>'
        '<input type="search" id="role_search" placeholder="🔎 Rolle suchen…" style="width:100%;margin:6px 0 10px" oninput="filterPingChoices(\'role_choices\',this.value)">'
        '<div id="role_choices" style="max-height:260px;overflow:auto;padding:4px">' + (role_options or '<p class="muted">Keine Rollen verfügbar.</p>') + '</div>'
        '<p class="muted"><span id="role_count">0</span>/10 Rollen ausgewählt. Einfach anklicken, kein Strg/Cmd nötig.</p>'
        '<h3>Aktuell geschützte Rollen</h3>' + ''.join(protected_role_rows) +
        '<h3>Geschützte Personen <small class="muted">(max. 10)</small></h3>'
        '<input type="search" id="member_search" placeholder="🔎 Person oder ID suchen…" style="width:100%;margin:6px 0 10px" oninput="filterPingChoices(\'member_choices\',this.value)">'
        '<div id="member_choices" style="max-height:300px;overflow:auto;padding:4px">' + (member_options or '<p class="muted">Keine Personen verfügbar.</p>') + '</div>'
        '<p class="muted"><span id="member_count">0</span>/10 Personen ausgewählt. Einfach anklicken, kein Strg/Cmd nötig.</p>' + protected_member_summary +
        '<p class="notice">Hinweis: Mitglieder mit „Nachrichten verwalten“ sind ausgenommen. IronWolf braucht im betroffenen Kanal „Nachrichten verwalten“, um Pings zu löschen. DMs können durch Privatsphäre-Einstellungen blockiert sein.</p>'
        '<button type="submit">💾 Ping-Schutz speichern</button></form><script>'
        'function filterPingChoices(boxId,query){const q=query.toLocaleLowerCase();document.querySelectorAll("#"+boxId+" .ping-choice").forEach(el=>{el.style.display=el.dataset.search.includes(q)?"flex":"none";});}'
        'function updatePingCount(boxId,countId){const box=document.getElementById(boxId);const checks=box.querySelectorAll("input[type=checkbox]");const selected=[...checks].filter(c=>c.checked);document.getElementById(countId).textContent=selected.length;checks.forEach(c=>{c.disabled=!c.checked&&selected.length>=10;});}'
        'function initPingChoices(boxId,countId){const box=document.getElementById(boxId);box.addEventListener("change",()=>updatePingCount(boxId,countId));updatePingCount(boxId,countId);}'
        'initPingChoices("role_choices","role_count");initPingChoices("member_choices","member_count");'
        '</script><p><a class="button secondary" href="/guild/' + gid + '">← Serverübersicht</a></p></section>'
    )
    return _render(guild.name + " · Ping-Schutz", body)

@app.route("/guild/<guild_id>/custom-bot/assets/<asset>")
def guild_custom_bot_asset(guild_id, asset):
    """Serve only this guild's uploaded Custom Bot images."""
    guild, response = _authorized_guild(guild_id)
    if response:
        return response
    cfg = _guild_config(guild.id)
    if not guild_has_feature(cfg, "custom_bot") or asset not in {"avatar", "banner"}:
        abort(404)
    profile = cfg.get("custom_bot_profile", {})
    stored = profile.get(asset + "_file") if isinstance(profile, dict) else None
    if not stored:
        abort(404)
    path = Path(stored).resolve()
    root = Path(data_path("custom_bot_assets")).resolve()
    if root not in path.parents or not path.is_file():
        abort(404)
    return send_file(path, mimetype=profile.get(asset + "_mime", "application/octet-stream"), conditional=True, max_age=300)


async def _apply_custom_bot_profile(guild, profile):
    """Apply the current bot's per-server profile in one Discord API request."""
    results = []
    custom_name = str(profile.get("bot_name", "")).strip()[:32]
    description = str(profile.get("description", "")).strip()[:190]
    token = getattr(getattr(_bot, "http", None), "token", None) or os.getenv("DISCORD_TOKEN")
    premium_nickname = guild_has_feature(_guild_config(guild.id), "custom_bot")

    def image_data(asset):
        stored = profile.get(asset + "_file")
        image_url = str(profile.get(asset + "_url", "")).strip()
        if stored and Path(stored).is_file():
            raw = Path(stored).read_bytes()
            mime = profile.get(asset + "_mime", "application/octet-stream")
        elif image_url:
            with requests.get(image_url, timeout=15, stream=True, allow_redirects=True) as response:
                response.raise_for_status()
                if urlparse(response.url).scheme != "https":
                    raise ValueError(asset + " URL redirected to a non-HTTPS address")
                mime = response.headers.get("Content-Type", "").split(";")[0].lower()
                chunks = []
                total = 0
                for chunk in response.iter_content(65536):
                    total += len(chunk)
                    if total > 5 * 1024 * 1024:
                        raise ValueError(asset + " image exceeds the 5 MB limit")
                    chunks.append(chunk)
                raw = b"".join(chunks)
        else:
            return None
        if not raw or len(raw) > 5 * 1024 * 1024:
            raise ValueError(asset + " image is empty or exceeds the 5 MB limit")
        if mime not in {"image/png", "image/jpeg", "image/gif", "image/webp"}:
            raise ValueError(asset + " must be PNG, JPG, GIF or WEBP")
        return "data:{};base64,{}".format(mime, base64.b64encode(raw).decode("ascii"))

    if not token:
        results.append("Discord profile could not be updated: bot token is unavailable")
    else:
        # Send each field separately: Discord can reject one image field (especially
        # banners, whose availability varies) without preventing the other updates.
        auth_token = str(token).removeprefix("Bot ").strip()
        headers = {"Authorization": "Bot " + auth_token, "Content-Type": "application/json"}

        def patch_profile(fields):
            return requests.patch(
                DISCORD_API + "/guilds/{}/members/@me".format(guild.id),
                headers=headers,
                json=fields,
                timeout=20,
            )

        async def send_update(label, fields, confirmation):
            try:
                response = await asyncio.to_thread(patch_profile, fields)
                if response.status_code == 429:
                    try:
                        retry_after = float(response.json().get("retry_after", 0))
                    except (ValueError, TypeError, AttributeError):
                        retry_after = 0
                    results.append(
                        "{} not updated: Discord rate limit (HTTP 429); wait approximately {} seconds.".format(
                            label, max(1, int(retry_after + 0.999))
                        )
                    )
                    return
                if response.status_code >= 400:
                    detail = " ".join(response.text.split())[:350]
                    results.append("{} update failed (HTTP {}): {}".format(label, response.status_code, detail))
                    return
                try:
                    updated = response.json()
                except ValueError:
                    updated = {}
                if confirmation(updated):
                    results.append("{} updated successfully.".format(label))
                else:
                    results.append("Discord accepted the {} request but did not confirm the change.".format(label))
            except (ValueError, requests.RequestException, OSError) as exc:
                results.append("{} update failed: {}".format(label, str(exc)[:250]))

        # Nickname is only sent when the server has the custom_bot Premium feature.
        base_payload = {"bio": description or None}
        me = getattr(guild, "me", None)
        permissions = getattr(me, "guild_permissions", None)
        if premium_nickname:
            if permissions is not None and not permissions.change_nickname:
                results.append("Nickname not changed: give the bot the Change Nickname permission.")
            else:
                base_payload["nick"] = custom_name or None
        else:
            results.append("Nickname unchanged: an active Premium plan is required.")

        await send_update(
            "Server bio",
            {"bio": base_payload["bio"]},
            lambda updated: "bio" in updated,
        )
        if "nick" in base_payload:
            await send_update(
                "Nickname",
                {"nick": base_payload["nick"]},
                lambda updated: updated.get("nick") == base_payload["nick"],
            )

        for asset in ("avatar", "banner"):
            try:
                if profile.get("remove_" + asset):
                    # null clears a guild-specific image so Discord falls back to
                    # the bot's default image.
                    image_payload = {asset: None}
                else:
                    data_uri = await asyncio.to_thread(image_data, asset)
                    if data_uri is None:
                        continue
                    image_payload = {asset: data_uri}
                await send_update(
                    asset.title(),
                    image_payload,
                    lambda updated, key=asset, value=image_payload[asset]: (
                        updated.get(key) is None if value is None else bool(updated.get(key))
                    ),
                )
            except (ValueError, requests.RequestException, OSError) as exc:
                results.append("{} update failed: {}".format(asset.title(), str(exc)[:250]))

    # Keep the optional channel card as a supplemental preview, not as the actual profile.
    channel_id = str(profile.get("profile_channel_id", "")).strip()
    if channel_id:
        try:
            channel = guild.get_channel(int(channel_id))
        except (TypeError, ValueError):
            channel = None
        if channel is None or not hasattr(channel, "send"):
            results.append("profile channel not found")
        else:
            try:
                embed = discord.Embed(
                    title=custom_name or (guild.me.display_name if guild.me else "IronWolf"),
                    description=description or "Server bot profile",
                    color=discord.Color.blurple(),
                )
                embed.set_footer(text=guild.name + " · IronWolf")
                files = []
                for asset, setter in (("avatar", embed.set_thumbnail), ("banner", embed.set_image)):
                    asset_path = profile.get(asset + "_file")
                    asset_url = profile.get(asset + "_url")
                    if asset_path and Path(asset_path).is_file():
                        asset_file = discord.File(asset_path, filename=Path(asset_path).name)
                        files.append(asset_file)
                        setter(url="attachment://" + asset_file.filename)
                    elif asset_url:
                        setter(url=asset_url)
                sent = await channel.send(embed=embed, files=files)
                old_message_id = profile.get("profile_message_id")
                if old_message_id:
                    try:
                        old_message = await channel.fetch_message(int(old_message_id))
                        await old_message.delete()
                    except (discord.NotFound, discord.Forbidden, discord.HTTPException, ValueError):
                        pass
                profile["profile_message_id"] = str(sent.id)
                results.append("supplemental profile card published in #" + getattr(channel, "name", "channel"))
            except discord.Forbidden:
                results.append("profile card could not be published: check View Channel, Send Messages, Embed Links and Attach Files permissions")
            except discord.HTTPException as exc:
                results.append("profile card publishing failed: " + str(exc)[:180])
    return results

def _apply_custom_bot_profile_sync(guild, profile):
    """Run Discord API updates from the dashboard's Flask worker thread."""
    if _bot is None or not getattr(_bot, "loop", None) or not _bot.loop.is_running():
        return ["bot event loop is not running; saved settings were not applied to Discord"]
    future = asyncio.run_coroutine_threadsafe(_apply_custom_bot_profile(guild, profile), _bot.loop)
    try:
        return future.result(timeout=25)
    except Exception as exc:
        future.cancel()
        return ["Discord update failed: " + type(exc).__name__ + ": " + str(exc)[:180]]


@app.route("/guild/<guild_id>/custom-bot", methods=["GET", "POST"])
def guild_custom_bot(guild_id):
    """Configure a Premium-only, per-guild bot presentation (not the global Discord bot account)."""
    guild, response = _authorized_guild(guild_id)
    if response:
        return response
    gid = str(guild.id)
    cfg = _guild_config(guild.id)
    if not guild_has_feature(cfg, "custom_bot"):
        abort(403, "Custom Bot Profile is a Premium feature. Link an active Premium plan to this server.")
    actor = guild.get_member(int(session.get("user_id", "0")))
    if actor is None or not (actor.guild_permissions.administrator or actor.guild_permissions.manage_guild):
        abort(403, "Administrator or Manage Server permission required.")
    profile = cfg.setdefault("custom_bot_profile", {})
    if not isinstance(profile, dict):
        profile = {}
        cfg["custom_bot_profile"] = profile
    if request.method == "POST":
        if not _csrf_ok():
            abort(400, "CSRF validation failed. Please reload the page.")
        avatar_url = request.form.get("avatar_url", "").strip()
        banner_url = request.form.get("banner_url", "").strip()
        description = request.form.get("description", "").strip()
        bot_name = request.form.get("bot_name", "").strip()
        profile_channel_id = request.form.get("profile_channel_id", "").strip()
        if len(bot_name) > 32:
            abort(400, "Bot nickname must be 32 characters or fewer.")
        if profile_channel_id:
            selected_channel = guild.get_channel(int(profile_channel_id)) if profile_channel_id.isdigit() else None
            if selected_channel is None or not isinstance(selected_channel, discord.TextChannel):
                abort(400, "Please select a valid text channel from this server.")
        for field, value in (("avatar", avatar_url), ("banner", banner_url)):
            if value:
                parsed = urlparse(value)
                if parsed.scheme != "https" or not parsed.hostname or len(value) > 500:
                    abort(400, field.title() + " URL must be a valid HTTPS URL (maximum 500 characters).")
        if len(description) > 190:
            abort(400, "Discord server bio must be 190 characters or fewer.")
        profile_data = dict(profile)
        profile_data.update({
            "avatar_url": avatar_url,
            "banner_url": banner_url,
            "description": description,
            "bot_name": bot_name,
            "profile_channel_id": profile_channel_id,
        })
        asset_dir = Path(data_path("custom_bot_assets")) / str(guild.id)
        asset_dir.mkdir(parents=True, exist_ok=True)
        allowed_images = {
            ".png": ("image/png", lambda raw: raw.startswith(bytes.fromhex("89504e470d0a1a0a"))),
            ".jpg": ("image/jpeg", lambda raw: raw.startswith(bytes.fromhex("ffd8ff"))),
            ".jpeg": ("image/jpeg", lambda raw: raw.startswith(bytes.fromhex("ffd8ff"))),
            ".gif": ("image/gif", lambda raw: raw.startswith((b"GIF87a", b"GIF89a"))),
            ".webp": ("image/webp", lambda raw: len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP"),
        }
        for asset in ("avatar", "banner"):
            uploaded = request.files.get(asset + "_file")
            if uploaded is not None and uploaded.filename:
                extension = Path(uploaded.filename).suffix.lower()
                if extension not in allowed_images:
                    abort(400, "Upload a PNG, JPG, GIF or WEBP image.")
                raw = uploaded.stream.read(5 * 1024 * 1024 + 1)
                mime, signature_ok = allowed_images[extension]
                if not raw or len(raw) > 5 * 1024 * 1024 or not signature_ok(raw):
                    abort(400, asset.title() + " image is invalid or exceeds the 5 MB limit.")
                destination = asset_dir / (asset + extension)
                for previous in asset_dir.glob(asset + ".*"):
                    if previous != destination:
                        previous.unlink(missing_ok=True)
                destination.write_bytes(raw)
                profile_data[asset + "_file"] = str(destination.resolve())
                profile_data[asset + "_mime"] = mime
                profile_data[asset + "_url"] = ""
            elif profile_data.get(asset + "_url"):
                # Switching from an uploaded image to a URL must remove the old
                # upload, otherwise the preview and Discord card keep using it.
                for previous in asset_dir.glob(asset + ".*"):
                    previous.unlink(missing_ok=True)
                profile_data.pop(asset + "_file", None)
                profile_data.pop(asset + "_mime", None)
            if request.form.get("remove_" + asset) == "on":
                for previous in asset_dir.glob(asset + ".*"):
                    previous.unlink(missing_ok=True)
                profile_data.pop(asset + "_file", None)
                profile_data.pop(asset + "_mime", None)
                profile_data[asset + "_url"] = ""
        profile_data["remove_avatar"] = request.form.get("remove_avatar") == "on"
        profile_data["remove_banner"] = request.form.get("remove_banner") == "on"
        profile_data["updated_at"] = datetime.now(timezone.utc).isoformat()
        cfg["custom_bot_profile"] = profile_data
        _save_json(data_path("config.json"), _config)
        discord_results = _apply_custom_bot_profile_sync(guild, profile_data)
        profile_data["last_discord_update"] = discord_results
        _save_json(data_path("config.json"), _config)
        _audit_dashboard_change(guild.id, "Custom Bot Profile", session.get("user_id"), "Updated server-specific bot presentation")
        return redirect(url_for("guild_custom_bot", guild_id=gid, saved="1"))
    avatar_url = str(profile.get("avatar_url", ""))
    banner_url = str(profile.get("banner_url", ""))
    avatar_file = profile.get("avatar_file") and Path(profile["avatar_file"]).is_file()
    banner_file = profile.get("banner_file") and Path(profile["banner_file"]).is_file()
    avatar_src = url_for("guild_custom_bot_asset", guild_id=gid, asset="avatar") if avatar_file else avatar_url
    banner_src = url_for("guild_custom_bot_asset", guild_id=gid, asset="banner") if banner_file else banner_url
    description = str(profile.get("description", ""))
    bot_name = str(profile.get("bot_name", ""))
    profile_channel_id = str(profile.get("profile_channel_id", ""))
    channel_options = '<option value="">Do not publish to Discord</option>' + ''.join(
        '<option value="' + str(channel.id) + '"' + (' selected' if str(channel.id) == profile_channel_id else '') + '>#' + html.escape(channel.name) + '</option>'
        for channel in guild.text_channels
    )
    success = ""
    if request.args.get("saved") == "1":
        update_notes = profile.get("last_discord_update", [])
        retry_seconds = 0
        for note in update_notes:
            match = re.search(r"wait approximately (\\d+) seconds", str(note), re.IGNORECASE)
            if match:
                retry_seconds = max(retry_seconds, int(match.group(1)))
        countdown_html = ""
        if retry_seconds:
            countdown_html = (
                '<p style="margin:10px 0 0"><strong>Discord rate-limit countdown:</strong> '
                '<span id="cb-rate-countdown" data-seconds="' + str(retry_seconds) + '">'
                + str(retry_seconds) + ' seconds</span>. You can try saving again when it reaches zero.</p>'
                '<script>(function(){var el=document.getElementById("cb-rate-countdown");if(!el)return;'
                'var left=parseInt(el.getAttribute("data-seconds"),10)||0;'
                'function tick(){if(left<=0){el.textContent="0 seconds — you can try again";return;}'
                'var m=Math.floor(left/60),s=left%60;el.textContent=(m?m+" min ":"")+s+" sec";left--;'
                'window.setTimeout(tick,1000);}tick();})();</script>'
            )
        success = '<div class="notice"><strong>Profile saved.</strong><br>' + '<br>'.join(html.escape(str(note)) for note in update_notes) + countdown_html + '</div>'
    body = (
        '<style>'
        '.cb-layout{display:grid;grid-template-columns:minmax(0,1.35fr) minmax(280px,.85fr);gap:18px;align-items:start}'
        '.cb-stack{display:grid;gap:18px}.cb-section{padding:20px}'
        '.cb-section h2{margin:0 0 6px}.cb-help{margin:0 0 16px;color:var(--muted,#9ca3af);font-size:.92rem}'
        '.cb-field{margin:0 0 16px}.cb-field:last-child{margin-bottom:0}'
        '.cb-field label{display:block;font-weight:600;margin-bottom:7px}'
        '.cb-field input[type=text],.cb-field input[type=url],.cb-field input[type=file],.cb-field select,.cb-field textarea{width:100%;box-sizing:border-box}'
        '.cb-two{display:grid;grid-template-columns:1fr 1fr;gap:14px}'
        '.cb-preview-banner{width:100%;height:150px;object-fit:cover;border-radius:12px;background:var(--surface-2,#252a34)}'
        '.cb-preview-user{display:flex;gap:14px;align-items:center;margin-top:14px}'
        '.cb-preview-avatar{width:68px;height:68px;border-radius:50%;object-fit:cover;background:#5865F2;flex:none}'
        '.cb-preview-name{font-weight:700;font-size:1.05rem}.cb-preview-bio{margin:4px 0 0;overflow-wrap:anywhere}'
        '.cb-actions{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:18px}'
        '.cb-status{line-height:1.55}.cb-status p{margin:5px 0}'
        '@media(max-width:760px){.cb-layout{grid-template-columns:1fr}.cb-two{grid-template-columns:1fr}}'
        '</style>'
        '<section class="card cb-section">'
        '<div style="display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap">'
        '<div><span class="pill">PREMIUM FEATURE</span><h1 style="margin:10px 0 4px">🤖 Custom Bot Profile</h1>'
        '<p class="muted" style="margin:0">Configure how IronWolf appears in ' + html.escape(guild.name) + '.</p></div>'
        '<a class="button secondary" href="/guild/' + gid + '">← Server dashboard</a></div>'
        '<div class="notice" style="margin-top:18px">These settings customize IronWolf’s server-specific Discord profile. The global bot username is not changed. The optional channel card is separate from the actual profile.</div>'
        + success +
        '</section>'
        '<form method="post" enctype="multipart/form-data">' + _csrf_field() +
        '<div class="cb-layout" style="margin-top:18px"><div class="cb-stack">'
        '<section class="card cb-section"><h2>1. Server identity</h2>'
        '<p class="cb-help">Set the nickname and the short description shown on the server profile.</p>'
        '<div class="cb-field"><label for="cb-name">Bot nickname</label><input id="cb-name" type="text" name="bot_name" maxlength="32" value="' + html.escape(bot_name, quote=True) + '" placeholder="IronWolf">'
        '<p class="cb-help" style="margin:6px 0 0">Maximum 32 characters. Leave empty to reset the nickname.</p></div>'
        '<div class="cb-field"><label for="cb-bio">Server profile bio</label><textarea id="cb-bio" name="description" maxlength="190" rows="4" placeholder="Describe this server’s IronWolf setup...">' + html.escape(description) + '</textarea>'
        '<p class="cb-help" style="margin:6px 0 0">Maximum 190 characters. Leave empty to clear the bio.</p></div>'
        '</section>'
        '<section class="card cb-section"><h2>2. Profile images</h2>'
        '<p class="cb-help">Upload an image or provide an HTTPS image URL. Supported formats: PNG, JPG, GIF and WEBP. Maximum 5 MB per image.</p>'
        '<div class="cb-two"><div class="cb-field"><label for="cb-avatar">Avatar image</label>'
        '<input id="cb-avatar" type="file" name="avatar_file" accept="image/png,image/jpeg,image/gif,image/webp">'
        '<label for="cb-avatar-url" style="margin-top:12px">Or avatar URL</label><input id="cb-avatar-url" type="url" name="avatar_url" maxlength="500" value="' + html.escape(avatar_url, quote=True) + '" placeholder="https://example.com/avatar.png">'
        + ('<label style="font-weight:400;margin-top:10px"><input type="checkbox" name="remove_avatar"> Originales Ironwolf-Profilbild wiederherstellen</label>' if avatar_file or avatar_url else '') +
        '</div><div class="cb-field"><label for="cb-banner">Banner image</label>'
        '<input id="cb-banner" type="file" name="banner_file" accept="image/png,image/jpeg,image/gif,image/webp">'
        '<label for="cb-banner-url" style="margin-top:12px">Or banner URL</label><input id="cb-banner-url" type="url" name="banner_url" maxlength="500" value="' + html.escape(banner_url, quote=True) + '" placeholder="https://example.com/banner.png">'
        + ('<label style="font-weight:400;margin-top:10px"><input type="checkbox" name="remove_banner"> Reset banner to default</label>' if banner_file or banner_url else '') +
        '</div></div></section>'
        '<section class="card cb-section"><h2>3. Optional profile card</h2>'
        '<p class="cb-help">Publish a preview embed in a channel. This does not control the actual Discord profile.</p>'
        '<div class="cb-field"><label for="cb-channel">Discord text channel</label><select id="cb-channel" name="profile_channel_id">' + channel_options + '</select></div>'
        '</section>'
        '<div class="cb-actions"><button type="submit">Save profile changes</button><a class="button secondary" href="/guild/' + gid + '/custom-bot">Reload</a></div>'
        '</div><aside class="cb-stack">'
        '<section class="card cb-section"><h2>Profile preview</h2><p class="cb-help">Preview of the currently saved profile settings.</p>'
        + ('<img class="cb-preview-banner" src="' + html.escape(banner_src, quote=True) + '" alt="Custom banner">' if banner_src else '<div class="cb-preview-banner" style="display:flex;align-items:center;justify-content:center;color:var(--muted,#9ca3af)">No custom banner</div>') +
        '<div class="cb-preview-user">'
        + ('<img class="cb-preview-avatar" src="' + html.escape(avatar_src, quote=True) + '" alt="Custom bot avatar">' if avatar_src else '<div class="cb-preview-avatar"></div>') +
        '<div style="min-width:0"><div class="cb-preview-name">' + html.escape(bot_name or (guild.me.display_name if guild.me else "IronWolf")) + '</div>'
        '<p class="cb-preview-bio">' + html.escape(description or "No custom description set.") + '</p></div></div>'
        '</section>'
        '<section class="card cb-section"><h2>Before saving</h2><div class="cb-status">'
        '<p>• Nickname changes require the bot’s <strong>Change Nickname</strong> permission.</p>'
        '<p>• Banner availability and image restrictions are controlled by Discord.</p>'
        '<p>• Reset checkboxes remove the corresponding custom image.</p>'
        '<p>• Changes are applied separately, so one failed field should not block the others.</p>'
        '</div></section>'
        '</aside></div></form>'
    )
    return _render(guild.name + " · Custom Bot Profile", body)


@app.route("/guild/<guild_id>/branding", methods=["GET", "POST"])
def guild_branding(guild_id):
    """Configure dashboard-only accent styling for this Discord server."""
    guild, response = _authorized_guild(guild_id)
    if response:
        return response
    gid = str(guild.id)
    cfg = _guild_config(guild.id)
    if request.method == "POST":
        if not _csrf_ok():
            abort(400, "CSRF-Prüfung fehlgeschlagen. Bitte Seite neu laden.")
        actor = guild.get_member(int(session.get("user_id", "0")))
        if actor is None or not (actor.guild_permissions.administrator or actor.guild_permissions.manage_guild):
            abort(403, "Du brauchst Administrator- oder Server-verwalten-Rechte.")
        dashboard_color = request.form.get("dashboard_brand_color", "8B9DFF").strip().lstrip("#")
        if not re.fullmatch(r"[0-9a-fA-F]{6}", dashboard_color):
            abort(400, "Bitte eine gültige Dashboard-Farbe auswählen.")
        cfg["dashboard_brand_color"] = dashboard_color.upper()
        _save_json(data_path("config.json"), _config)
        _audit_dashboard_change(guild.id, "Dashboard-Design", session.get("user_id"), "Dashboard-Akzentfarbe #" + dashboard_color.upper())
        return redirect(url_for("guild_branding", guild_id=gid, saved="1"))
    dashboard_color = str(cfg.get("dashboard_brand_color", "8B9DFF")).strip().lstrip("#")

    success = '<div class="notice">✅ Dashboard-Design gespeichert. Die Änderung betrifft ausschließlich die Darstellung im Dashboard und verändert keine Discord-Nachrichten.</div>' if request.args.get("saved") == "1" else ""
    body = (
        '<section class="card"><span class="pill">IRONWOLF · SERVER DESIGN</span><h1>🎨 Server-Design</h1>'
        '<p class="muted">Passe ausschließlich die Darstellung des Dashboards für diesen Server an.</p>'
        + success +
        '<form method="post">' + _csrf_field() +
        '<div class="grid"><section class="card"><h2>🖥️ Dashboard-Design</h2>'
        '<label for="dashboard_brand_color">Akzentfarbe</label><input id="dashboard_brand_color" name="dashboard_brand_color" type="color" value="#' + html.escape(dashboard_color) + '" style="height:54px;padding:5px">'
        '<p class="muted">Ändert ausschließlich Akzentlinien, Links und Markierungen im IronWolf-Dashboard dieses Servers. Discord-Rollen, Embeds und Nachrichten bleiben unverändert.</p>'
        '<button type="submit">💾 Dashboard-Design speichern</button></form></section>'
        '<section class="card"><h2>👀 Vorschau</h2><div style="border-radius:12px;padding:18px;background:#111827;color:#f3f6fc;border-left:5px solid #' + html.escape(dashboard_color) + '">'
        '<strong>IronWolf Control Center</strong><p>So sieht die Akzentfarbe im Dashboard aus.</p><small>IronWolf · Created by KingSnowy</small></div>'
        '<p><a class="button secondary" href="/guild/' + gid + '/messages">✉️ Nachricht mit Vorschau erstellen →</a> <a class="button secondary" href="/guild/' + gid + '">Zur Serverübersicht</a></p></section>'
    )
    return _render(guild.name + " · Server-Design", body)


@app.route("/guild/<guild_id>/messages", methods=["GET", "POST"])
def guild_message_builder(guild_id):
    """Compose and immediately send a bot message with link and role buttons."""
    guild, response = _authorized_guild(guild_id)
    if response:
        return response
    gid = str(guild.id)
    cfg = _guild_config(guild.id)
    error_html = ""
    if request.method == "POST":
        if not _csrf_ok():
            abort(400, "CSRF-Prüfung fehlgeschlagen. Bitte Seite neu laden.")
        try:
            channel_id = int(request.form.get("channel_id", "0"))
        except (TypeError, ValueError):
            abort(400, "Bitte einen gültigen Kanal auswählen.")
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            abort(400, "Bitte einen Textkanal auswählen.")
        actor = guild.get_member(int(session.get("user_id", "0")))
        if actor is None or not (actor.guild_permissions.administrator or actor.guild_permissions.manage_guild):
            abort(403, "Du brauchst Administrator- oder Server-verwalten-Rechte.")
        bot_member = guild.me
        if bot_member is None or not channel.permissions_for(bot_member).send_messages:
            abort(403, "Der Bot darf in diesem Kanal keine Nachrichten senden.")
        content = request.form.get("message", "").strip()
        title = request.form.get("embed_title", "").strip()[:256]
        description = request.form.get("embed_description", "").strip()[:4000]
        if len(content) > 2000:
            abort(400, "Die Nachricht darf höchstens 2000 Zeichen haben.")
        if not content and not title and not description:
            abort(400, "Bitte gib Nachrichtentext oder Embed-Inhalt ein.")
        button_records = []
        labels = request.form.getlist("button_label")
        types = request.form.getlist("button_type")
        urls = request.form.getlist("button_url")
        role_ids = request.form.getlist("button_role")
        if len(types) > 5:
            abort(400, "Pro Nachricht sind maximal 5 Buttons erlaubt.")
        for idx, kind in enumerate(types):
            label = labels[idx].strip()[:80] if idx < len(labels) else ""
            url = urls[idx].strip() if idx < len(urls) else ""
            raw_role = role_ids[idx].strip() if idx < len(role_ids) else ""
            # A row is optional unless the user has entered any of its fields.
            if not label and not url and not raw_role:
                continue
            # If only the type selector has its default value, ignore the untouched row.
            if kind == "link" and not label and not url:
                continue
            if kind == "role" and not label and not raw_role:
                continue
            if kind not in {"link", "role"}:
                abort(400, "Unbekannter Button-Typ.")
            if not label:
                if guild.id == 1561027077984227371:
                    label = "Get Role" if kind == "role" else "Open Link"
                else:
                    label = "Rolle erhalten" if kind == "role" else "Link öffnen"
            if kind == "link":
                from urllib.parse import urlparse
                parsed = urlparse(url)
                if parsed.scheme != "https" or not parsed.netloc:
                    abort(400, "Link-Buttons benötigen eine gültige HTTPS-URL.")
                button_records.append({"type": "link", "label": label, "url": url})
            elif kind == "role":
                # Eine Rollen-Zeile ohne ausgewählte Rolle ist optional:
                # Nachricht senden muss auch ganz ohne Rollen-Buttons funktionieren.
                if not raw_role:
                    continue
                role = guild.get_role(int(raw_role)) if raw_role.isdigit() else None
                if role is None or role.is_default() or role.managed:
                    abort(400, "Bitte wähle eine gültige, normale Discord-Rolle aus.")
                if bot_member is None or role >= bot_member.top_role:
                    abort(400, "Diese Rolle liegt über der höchsten Bot-Rolle. Verschiebe die Bot-Rolle in den Servereinstellungen höher.")
                if not bot_member.guild_permissions.manage_roles:
                    abort(400, "The bot needs the “Manage Roles” permission, damit Rollen-Buttons funktionieren.")
                button_records.append({"type": "role", "label": label, "role_id": role.id})
        embed = None
        if title or description:
            embed = discord.Embed(title=title or None, description=description or None, color=discord.Color.blurple())
            embed.set_footer(text="IronWolf · Created by KingSnowy")
        view = _bot.build_dashboard_message_view(guild.id, button_records) if _bot and hasattr(_bot, "build_dashboard_message_view") else None
        async def _send():
            return await channel.send(content=content or None, embed=embed, view=view, allowed_mentions=discord.AllowedMentions.none())
        try:
            future = asyncio.run_coroutine_threadsafe(_send(), _bot.loop)
            sent = future.result(timeout=20)
        except Exception as exc:
            print(f"[DASHBOARD] Message builder send failed in guild {gid}: {type(exc).__name__}: {exc}", flush=True)
            abort(502, "Die Bot-Nachricht konnte nicht gesendet werden. Prüfe Bot-Rechte und Railway-Logs.")
        cfg.setdefault("message_panels", {})[str(sent.id)] = {
            "channel_id": channel.id,
            "buttons": button_records,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        _save_json(data_path("config.json"), _config)
        _audit_dashboard_change(guild.id, "Bot-Nachricht gesendet", session.get("user_id"), f"Kanal {channel.name}; {len(button_records)} Buttons")
        return redirect(url_for("guild_message_builder", guild_id=gid, sent="1"))
    channels = ''.join(f'<option value="{ch.id}">{html.escape(ch.name)}</option>' for ch in guild.text_channels)
    roles = '<option value="">— Keine Rolle (optional) —</option>' + ''.join(f'<option value="{role.id}">{html.escape(role.name)}</option>' for role in reversed(guild.roles) if not role.is_default() and not role.managed)
    button_fields = '<div id="button-fields" class="scnx-button-list">'
    for index in range(1, 6):
        default_type = "role" if index == 1 else "link"
        hidden = "" if index == 1 else ' hidden'
        button_fields += f'<div class="scnx-button-item manual-button-row" data-button-row="{index}"{hidden}><div class="scnx-button-heading"><strong><span class="scnx-button-number">{index}</span> Button {index}</strong><button type="button" class="secondary scnx-remove-button" onclick="removeManualButton({index})">Entfernen</button></div><label>Button-Typ</label><select name="button_type" class="scnx-button-type" onchange="updateButtonType(this)"><option value="role" {"selected" if default_type == "role" else ""}>🎭 Rolle vergeben</option><option value="link" {"selected" if default_type == "link" else ""}>🔗 Link öffnen</option></select><label>Button-Text</label><input name="button_label" maxlength="80" placeholder="z. B. Jetzt verifizieren"><div class="scnx-link-fields"><label>HTTPS-Link</label><input name="button_url" type="url" placeholder="https://deine-seite.de" disabled></div><div class="scnx-role-fields"><label>Rolle auswählen (optional)</label><select name="button_role" disabled>{roles}</select></div></div>'
    button_fields += '</div><button type="button" id="add-button-row" class="secondary" onclick="addManualButton()">＋ Weiteren Button hinzufügen</button><p class="muted">Bis zu 5 Buttons. Rollen-Buttons sind optional und benötigen „Rollen verwalten“ für den Bot. Du kannst die Nachricht auch ganz ohne Buttons senden. Link-Buttons öffnen eine HTTPS-Seite.</p>'
    button_fields += """<style>
.scnx-builder{display:grid;grid-template-columns:minmax(0,1.25fr) minmax(260px,.75fr);gap:18px;align-items:start}
.scnx-panel{border:1px solid var(--border-color,rgba(148,163,184,.25));border-radius:14px;padding:18px;margin:14px 0;background:var(--card-bg,rgba(127,127,127,.035))}
.scnx-panel h2,.scnx-panel h3{margin-top:0}.scnx-field-help{display:block;color:var(--muted,#94a3b8);font-size:.86rem;margin:-5px 0 12px}
.scnx-button-item{border:1px solid var(--border-color,rgba(148,163,184,.25));border-radius:12px;padding:14px;margin:12px 0}
.scnx-button-heading{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:10px}
.scnx-button-number{display:inline-grid;place-items:center;width:27px;height:27px;border-radius:8px;background:rgba(124,58,237,.16);margin-right:6px}
.scnx-remove-button{padding:5px 10px!important;font-size:.85rem}
.scnx-preview{position:sticky;top:16px}.scnx-discord-preview{background:#313338;color:#dbdee1;border-radius:12px;padding:16px;font-family:Arial,sans-serif;overflow-wrap:anywhere}
.scnx-preview-label{font-size:.75rem;color:#949ba4;text-transform:uppercase;letter-spacing:.08em;margin-bottom:10px}
.scnx-preview-message{white-space:pre-wrap;margin-bottom:12px}.scnx-preview-embed{border-left:4px solid #5865f2;background:#2b2d31;border-radius:4px;padding:12px;margin:10px 0;min-height:54px}
.scnx-preview-title{font-weight:700;color:#fff;margin-bottom:8px}.scnx-preview-desc{white-space:pre-wrap}.scnx-preview-buttons{display:flex;gap:7px;flex-wrap:wrap;margin-top:10px}
.scnx-preview-button{background:#4e5058;color:white;border:0;border-radius:5px;padding:8px 12px;font-size:.85rem}.scnx-preview-button.link{background:#5865f2}
.scnx-send{width:100%;margin-top:12px}
@media(max-width:850px){.scnx-builder{grid-template-columns:1fr}.scnx-preview{position:static;grid-row:1}}
</style>
<script>
function updateButtonType(select){
 const row=select.closest(".manual-button-row"), isLink=select.value==="link";
 const link=row.querySelector(".scnx-link-fields"), role=row.querySelector(".scnx-role-fields");
 link.hidden=!isLink; role.hidden=isLink;
 const url=link.querySelector("input"), roleSelect=role.querySelector("select");
 url.disabled=!isLink; roleSelect.disabled=isLink;
 updateScnxPreview();
}
function addManualButton(){
 const row=[...document.querySelectorAll(".manual-button-row")].find(r=>r.hidden);
 if(!row){alert("Du kannst maximal fünf Buttons hinzufügen.");return;}
 row.hidden=false;updateButtonType(row.querySelector(".scnx-button-type"));row.scrollIntoView({behavior:"smooth",block:"center"});
}
function removeManualButton(n){
 const row=document.querySelector('[data-button-row="'+n+'"]');if(!row)return;
 row.querySelectorAll("input").forEach(i=>i.value="");row.querySelectorAll("select").forEach(s=>s.selectedIndex=0);
 row.hidden=true;updateButtonType(row.querySelector(".scnx-button-type"));updateScnxPreview();
}
function updateScnxPreview(){
 const val=id=>document.getElementById(id)?.value||"";
 document.getElementById("scnx-preview-message").textContent=val("message")||"Deine Nachricht erscheint hier …";
 const title=val("embed_title"),desc=val("embed_description");
 document.getElementById("scnx-preview-embed").hidden=!(title||desc);
 document.getElementById("scnx-preview-title").textContent=title;
 document.getElementById("scnx-preview-desc").textContent=desc;
 const buttons=document.getElementById("scnx-preview-buttons");buttons.replaceChildren();
 document.querySelectorAll(".manual-button-row:not([hidden])").forEach(row=>{
  const label=row.querySelector('[name="button_label"]').value.trim();
  const type=row.querySelector('[name="button_type"]').value;
  const role=row.querySelector('[name="button_role"]');
  const text=label||(type==="role"?(role.selectedOptions[0]?.textContent&&role.value?role.selectedOptions[0].textContent:"Rolle erhalten"):"Link öffnen");
  const button=document.createElement("span");button.className="scnx-preview-button"+(type==="link"?" link":"");button.textContent=(type==="link"?"↗ ":"")+text;buttons.appendChild(button);
 });
}
document.addEventListener("DOMContentLoaded",()=>{
 document.querySelectorAll(".manual-button-row").forEach(row=>updateButtonType(row.querySelector(".scnx-button-type")));
 document.querySelectorAll("#message,#embed_title,#embed_description,.manual-button-row input,.manual-button-row select").forEach(el=>el.addEventListener("input",updateScnxPreview));
 document.querySelector(".scnx-message-builder form")?.addEventListener("submit",()=>document.querySelectorAll(".manual-button-row[hidden]").forEach(row=>row.querySelectorAll("input,select").forEach(el=>el.disabled=true)));
 updateScnxPreview();
});
</script>"""
    success = '<div class="notice">✅ Nachricht wurde vom Bot gesendet.</div>' if request.args.get("sent") == "1" else ''
    body = '<section class="card scnx-message-builder"><span class="pill">MESSAGE BUILDER</span><h1>✉️ Nachricht erstellen</h1><p class="muted">Erstelle eine Discord-Nachricht wie in einem visuellen Editor: Inhalt links bearbeiten, Vorschau rechts ansehen und am Ende direkt senden.</p>' + success + error_html + '<form method="post">' + _csrf_field() + '<div class="scnx-builder"><div><section class="scnx-panel"><h2>1. Wo soll die Nachricht hin?</h2><label for="channel_id">Discord-Kanal</label><select id="channel_id" name="channel_id" required>' + channels + '</select><small class="scnx-field-help">Wähle den Kanal, in dem der Bot die Nachricht veröffentlichen soll.</small></section><section class="scnx-panel"><h2>2. Nachricht gestalten</h2><label for="message">Nachrichtentext</label><textarea id="message" name="message" maxlength="2000" rows="4" placeholder="Schreibe hier deine Nachricht …"></textarea><small class="scnx-field-help">Normale Discord-Nachricht, maximal 2000 Zeichen.</small><h3>Embed (optional)</h3><label for="embed_title">Überschrift</label><input id="embed_title" name="embed_title" maxlength="256" placeholder="z. B. Willkommen auf dem Server!"><label for="embed_description">Description</label><textarea id="embed_description" name="embed_description" maxlength="4000" rows="5" placeholder="Erkläre hier, was Mitglieder tun sollen …"></textarea><small class="scnx-field-help">Du kannst nur normalen Text, nur einen Embed oder beides zusammen senden.</small></section><section class="scnx-panel"><h2>3. Buttons hinzufügen</h2><p class="muted">Füge nur die Buttons hinzu, die du brauchst. Wähle einen Button-Typ; unnötige Felder werden automatisch ausgeblendet.</p>' + button_fields + '</section><button class="scnx-send" type="submit">🚀 Nachricht jetzt senden</button></div><aside class="scnx-panel scnx-preview"><div class="scnx-preview-label">Live-Vorschau · Discord</div><div class="scnx-discord-preview"><div class="scnx-preview-message" id="scnx-preview-message">Deine Nachricht erscheint hier …</div><div class="scnx-preview-embed" id="scnx-preview-embed" hidden><div class="scnx-preview-title" id="scnx-preview-title"></div><div class="scnx-preview-desc" id="scnx-preview-desc"></div></div><div class="scnx-preview-buttons" id="scnx-preview-buttons"></div></div><p class="muted" style="font-size:.85rem;margin-top:12px">Die Vorschau ist eine ungefähre Darstellung. Das tatsächliche Aussehen richtet sich nach Discord.</p></aside></div></form><p><a class="button secondary" href="/guild/' + gid + '">← Serverübersicht</a></p></section>'
    return _render(guild.name + " · Bot-Nachricht", body)


@app.route("/guild/<guild_id>/command-log", methods=["GET", "POST"])
def guild_command_log(guild_id):
    """Configure command logging for selected source channels."""
    guild, response = _authorized_guild(guild_id)
    if response:
        return response
    gid = str(guild.id)
    cfg = _guild_config(guild.id)
    channels = _guild_channels(guild)
    channel_ids = {str(cid) for cid, _ in channels}
    if request.method == "POST":
        if not _csrf_ok():
            abort(400, "CSRF-Prüfung fehlgeschlagen. Bitte Seite neu laden.")
        actor = guild.get_member(int(session.get("user_id", "0")))
        if actor is None or not (actor.guild_permissions.administrator or actor.guild_permissions.manage_guild):
            abort(403, "Du brauchst Administrator- oder Server-verwalten-Rechte.")
        output_channel = request.form.get("output_channel_id", "").strip()
        source_channels = [value for value in request.form.getlist("source_channel_ids") if value in channel_ids]
        if output_channel and output_channel not in channel_ids:
            abort(400, "Bitte wähle einen gültigen Log-Kanal.")
        if len(source_channels) > 50:
            abort(400, "Du kannst höchstens 50 überwachte Kanäle auswählen.")
        cfg["command_log_enabled"] = request.form.get("enabled") == "on"
        cfg["command_log_output_channel_id"] = int(output_channel) if output_channel.isdigit() else None
        cfg["command_log_source_channel_ids"] = [int(value) for value in source_channels]
        _save_json(data_path("config.json"), _config)
        _audit_dashboard_change(guild.id, "Command Log", session.get("user_id"),
                                f"Protokollierung {'aktiviert' if cfg['command_log_enabled'] else 'deaktiviert'}; "
                                f"{len(source_channels)} überwachte Kanäle")
        return redirect(url_for("guild_command_log", guild_id=gid, saved="1"))
    selected = {str(value) for value in cfg.get("command_log_source_channel_ids", [])}
    output_selected = str(cfg.get("command_log_output_channel_id") or "")
    channel_options = "".join(
        '<option value="' + str(cid) + '"' + (' selected' if str(cid) == output_selected else '') + '>' +
        html.escape(name) + '</option>' for cid, name in channels
    )
    source_options = "".join(
        '<option value="' + str(cid) + '"' + (' selected' if str(cid) in selected else '') + '>' +
        html.escape(name) + '</option>' for cid, name in channels
    )
    enabled = ' checked' if cfg.get("command_log_enabled", False) else ''
    notice = '<div class="notice">Einstellungen gespeichert.</div>' if request.args.get("saved") == "1" else ''
    body = (
        '<section class="card"><h1>⌘ Command Log</h1>'
        '<p class="muted">Protokolliert IronWolf-Befehle, die in den ausgewählten Textkanälen ausgeführt werden. '
        'Der Log-Eintrag enthält Benutzer, Befehl, Ursprungskanal und Zeit – keine Befehlsargumente.</p>'
        + notice +
        '<form method="post">' + _csrf_field() +
        '<div class="row"><strong>Command Log aktivieren</strong><div class="check">'
        '<input type="checkbox" id="command-log-enabled" name="enabled"' + enabled + '>'
        '<label for="command-log-enabled">Enabled</label></div></div>'
        '<label for="command-log-output">Log-Kanal</label>'
        '<select id="command-log-output" name="output_channel_id"><option value="">— Log-Kanal auswählen —</option>'
        + channel_options + '</select>'
        '<label for="command-log-sources">Überwachte Kanäle (Mehrfachauswahl mit Strg/Cmd)</label>'
        '<select id="command-log-sources" name="source_channel_ids" multiple size="12" style="min-height:220px">'
        + source_options + '</select>'
        '<p class="muted">Wähle einen oder mehrere Kanäle. Befehle außerhalb dieser Kanäle werden nicht protokolliert. '
        'Der Log-Kanal sollte nicht selbst als Quellkanal ausgewählt werden.</p>'
        '<button type="submit">Command Log speichern</button> '
        '<a class="button secondary" href="/guild/' + gid + '">Zur Serverübersicht</a></form></section>'
    )
    return _render(guild.name + " · Command Log", body)


@app.route("/guild/<guild_id>/audit-log", methods=["GET"])
def guild_audit_log(guild_id):
    guild, response = _authorized_guild(guild_id)
    if response:
        return response
    cfg = _guild_config(guild.id)
    entries = list(reversed(cfg.get("dashboard_audit_log", [])))
    body = '<section class="card"><h1>📜 Dashboard-Audit-Log</h1><p class="muted">Die letzten 200 gespeicherten Dashboard-Änderungen.</p><a class="button secondary" href="/guild/' + str(guild.id) + '">← Serverübersicht</a>'
    if not entries:
        body += '<p>Noch keine protokollierten Änderungen. Änderungen ab diesem Update werden gespeichert.</p>'
    for item in entries[:100]:
        timestamp = html.escape(str(item.get("timestamp", ""))[:19].replace("T", " "))
        actor = html.escape(str(item.get("actor_id", "unknown")))
        action = html.escape(str(item.get("action", "Änderung")))
        details = html.escape(str(item.get("details", "")))
        body += '<div class="row"><div><strong>' + action + '</strong><br><small>' + timestamp + ' UTC · Discord-ID ' + actor + '</small><br><small>' + details + '</small></div></div>'
    body += '</section>'
    return _render(guild.name + " · Audit-Log", body)


@app.route("/guild/<guild_id>/health", methods=["GET"])
def guild_health_center(guild_id):
    """Show actionable runtime and permission diagnostics for a server admin."""
    guild, response = _authorized_guild(guild_id)
    if response:
        return response

    ready = bool(_bot and _bot.is_ready())
    latency = getattr(_bot, "latency", None)
    latency_ms = round(latency * 1000) if isinstance(latency, (int, float)) and latency >= 0 else None
    me = getattr(guild, "me", None)
    perms = getattr(me, "guild_permissions", None)
    checks = [
        ("Bot-Verbindung", ready, "Der Bot ist bereit und mit Discord verbunden." if ready else "Der Bot ist noch nicht bereit oder offline."),
        ("Kanäle verwalten", bool(getattr(perms, "manage_channels", False)), "Für automatische Kanal-Setups erforderlich."),
        ("Rollen verwalten", bool(getattr(perms, "manage_roles", False)), "Für Rollen-Erstellung und Rollen-Zuweisung erforderlich."),
        ("Nachrichten senden", bool(getattr(perms, "send_messages", False)), "Für Meldungen und Panel-Nachrichten erforderlich."),
        ("Nachrichten einbetten", bool(getattr(perms, "embed_links", False)), "Für formatierte Panels und Ausweis-Einbettungen hilfreich."),
        ("Mitglieder anzeigen", bool(getattr(perms, "view_guild_insights", False) or getattr(perms, "view_audit_log", False)), "Audit-Log-Rechte sind separat; dieser Check zeigt nur zusätzliche Verwaltungsrechte an."),
    ]
    rows = ""
    for label, ok, detail in checks:
        status = "OK" if ok else "PRÜFEN"
        color = "#3fbf91" if ok else "#e6a23c"
        rows += (
            '<div class="row"><div><strong>' + html.escape(label) + '</strong><br><small>'
            + html.escape(detail) + '</small></div><span class="pill" style="color:' + color
            + ';border-color:' + color + '">' + status + '</span></div>'
        )
    ping = (str(latency_ms) + " ms") if latency_ms is not None else "Nicht verfügbar"
    guild_count = len(getattr(_bot, "guilds", [])) if _bot else 0
    command_count = len(_command_records(guild.id))
    module_count = len(_module_records(guild.id))
    body = (
        '<section class="card"><span class="pill">SYSTEMDIAGNOSE</span><h1>🩺 Systemzustand</h1>'
        '<p class="muted">Prüft den aktuellen Bot-Prozess und die wichtigsten Berechtigungen auf diesem Server. '
        'Die Diagnose führt keine Änderungen durch.</p><div class="grid">'
        '<div class="notice"><small>Discord-Latenz</small><h2>' + html.escape(ping) + '</h2></div>'
        '<div class="notice"><small>Server des Bots</small><h2>' + str(guild_count) + '</h2></div>'
        '<div class="notice"><small>Befehle erkannt</small><h2>' + str(command_count) + '</h2></div>'
        '<div class="notice"><small>Module erkannt</small><h2>' + str(module_count) + '</h2></div>'
        '</div><h2>Verbindungs- und Berechtigungschecks</h2>' + rows
        + '<p class="muted">Hinweis: Discord-Berechtigungen können durch Kanal-Overrides eingeschränkt sein. '
        'Diese Übersicht ersetzt keinen Test in jedem einzelnen Kanal.</p>'
        '<p><a class="button secondary" href="/guild/' + str(guild.id) + '/health">Diagnose aktualisieren</a> '
        '<a class="button secondary" href="/guild/' + str(guild.id) + '/stats">Statistiken →</a></p></section>'
    )
    return _render(guild.name + " · Systemzustand", body)


@app.route("/guild/<guild_id>/backup", methods=["GET", "POST"])
def guild_config_backup(guild_id):
    """Export or explicitly restore a single server's dashboard configuration."""
    guild, response = _authorized_guild(guild_id)
    if response:
        return response
    cfg = _guild_config(guild.id)
    if request.method == "POST":
        if not _csrf_ok():
            abort(400, "CSRF-Prüfung fehlgeschlagen. Bitte Seite neu laden.")
        actor = guild.get_member(int(session.get("user_id", "0")))
        if actor is None or not (actor.guild_permissions.administrator or actor.guild_permissions.manage_guild):
            abort(403, "Zum Wiederherstellen brauchst du Administrator- oder Server-verwalten-Rechte.")
        if request.form.get("confirm_restore", "").strip() != "WIEDERHERSTELLEN":
            abort(400, "Gib WIEDERHERSTELLEN ein, um den Import ausdrücklich zu bestätigen.")
        uploaded = request.files.get("backup_file")
        if uploaded is None or not uploaded.filename:
            abort(400, "Bitte wähle eine zuvor exportierte JSON-Sicherungsdatei.")
        if not uploaded.filename.lower().endswith(".json"):
            abort(400, "Es sind nur JSON-Sicherungsdateien erlaubt.")
        raw = uploaded.stream.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            abort(413, "Die Sicherungsdatei darf höchstens 2 MB groß sein.")
        try:
            backup = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            abort(400, "Die Datei enthält kein gültiges UTF-8-JSON.")
        if not isinstance(backup, dict):
            abort(400, "Die Sicherung muss ein JSON-Objekt für einen einzelnen Server enthalten.")
        # Preserve the server ID boundary and avoid replacing the whole global config.
        forbidden = {"dashboard_audit_log"}
        backup = {str(k): v for k, v in backup.items() if isinstance(k, str) and k not in forbidden}
        if not backup:
            abort(400, "Die Sicherungsdatei enthält keine wiederherstellbaren Einstellungen.")
        # Create a recovery snapshot before replacing anything, so a bad import
        # can be reversed without relying on an older manual download.
        recovery_dir = data_path("backups")
        recovery_dir.mkdir(parents=True, exist_ok=True)
        recovery_path = recovery_dir / ("guild-" + str(guild.id) + "-before-restore-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".json")
        try:
            recovery_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            print(f"[DASHBOARD] Could not create pre-restore backup for guild {guild.id}: {type(exc).__name__}: {exc}", flush=True)
            abort(500, "Sicherheits-Backup fehlgeschlagen. Die bestehende Konfiguration wurde nicht verändert.")
        _config[str(guild.id)] = backup
        try:
            _save_json(data_path("config.json"), _config)
        except Exception:
            # Restore the in-memory value if persistence failed.
            _config[str(guild.id)] = cfg
            raise
        _audit_dashboard_change(guild.id, "Konfiguration wiederhergestellt", session.get("user_id"), "JSON-Import; vorherige Konfiguration automatisch gesichert als " + recovery_path.name)
        return redirect(url_for("guild_config_backup", guild_id=guild.id))

    if request.args.get("download") == "1":
        payload = json.dumps(cfg, ensure_ascii=False, indent=2).encode("utf-8")
        response = make_response(payload)
        response.headers["Content-Type"] = "application/json; charset=utf-8"
        response.headers["Content-Disposition"] = 'attachment; filename="ironwolf-server-' + str(guild.id) + '-backup.json"'
        response.headers["Cache-Control"] = "no-store"
        return response

    body = (
        '<section class="card"><span class="pill">VERWALTUNG & SICHERUNG</span><h1>🗄️ Konfigurations-Backup</h1>'
        '<p>Exportiere die Einstellungen dieses Servers oder stelle sie aus einer JSON-Sicherung wieder her. '
        'Die Wiederherstellung ersetzt die aktuelle Konfiguration dieses Servers; lade vorher bei Bedarf eine Sicherung herunter.</p>'
        '<p><a class="button" href="/guild/' + str(guild.id) + '/backup?download=1">⬇️ Backup herunterladen</a></p>'
        '<h2>Backup wiederherstellen</h2><form method="post" enctype="multipart/form-data">' + _csrf_field()
        + '<label for="backup_file">JSON-Sicherungsdatei</label><input id="backup_file" name="backup_file" type="file" accept=".json,application/json" required>'
        + '<label for="confirm_restore">Bestätigung (WIEDERHERSTELLEN)</label><input id="confirm_restore" name="confirm_restore" required maxlength="30" autocomplete="off">'
        + '<p class="notice">Achtung: Die aktuelle Serverkonfiguration wird ersetzt. Das globale Konfigurations- und Audit-Log anderer Server bleibt erhalten.</p>'
        + '<button type="submit" class="danger">Sicherung wiederherstellen</button></form>'
        '<p><a class="button secondary" href="/guild/' + str(guild.id) + '/audit-log">Audit-Log öffnen →</a> '
        '<a class="button secondary" href="/guild/' + str(guild.id) + '">Zur Serververwaltung</a></p></section>'
    )
    return _render(guild.name + " · Konfigurations-Backup", body)


@app.route("/guild/<guild_id>/server-statistics", methods=["GET", "POST"])
def guild_server_statistics(guild_id):
    """Configure separate voice channels for live server statistics."""
    guild, response = _authorized_guild(guild_id)
    if response:
        return response
    gid = str(guild.id)
    cfg = _guild_config(guild.id)
    stats_types = [
        ("members", "👥 Mitglieder", "👥・Mitglieder: {count}", lambda: int(guild.member_count or len(guild.members))),
        ("bots", "🤖 Bots", "🤖・Bots: {count}", lambda: sum(1 for member in guild.members if member.bot)),
        ("boosters", "🚀 Booster", "🚀・Booster: {count}", lambda: int(guild.premium_subscription_count or 0)),
        ("roles", "🎭 Rollen", "🎭・Rollen: {count}", lambda: len(guild.roles)),
        ("text_channels", "💬 Textkanäle", "💬・Textkanäle: {count}", lambda: len(guild.text_channels)),
        ("voice_channels", "🔊 Sprachkanäle", "🔊・Sprachkanäle: {count}", lambda: len(guild.voice_channels)),
    ]
    if request.method == "POST":
        if not _csrf_ok():
            abort(400, "CSRF-Prüfung fehlgeschlagen. Bitte Seite neu laden.")
        selected = {}
        valid_ids = {str(channel.id) for channel in guild.voice_channels}
        used_ids = []
        for key, _label, _template, _counter in stats_types:
            raw = request.form.get("stat_" + key, "").strip()
            if raw and raw not in valid_ids:
                abort(400, "Bitte für jede Statistik einen gültigen Sprachkanal auswählen.")
            if raw:
                used_ids.append(raw)
            selected[key] = int(raw) if raw else None
        if len(used_ids) != len(set(used_ids)):
            abort(400, "Jede Statistik benötigt einen eigenen Sprachkanal. Bitte doppelte Auswahl korrigieren.")
        cfg["server_statistics_channels"] = selected
        # Clear the legacy single-channel setting so the old worker cannot overwrite a channel.
        cfg.pop("server_statistics_channel", None)
        _save_json(data_path("config.json"), _config)
        _audit_dashboard_change(guild.id, "Server-Statistik", session.get("user_id"),
                                "Statistikkanäle gespeichert: " + ", ".join(key for key, value in selected.items() if value))
        return redirect(url_for("guild_server_statistics", guild_id=gid, saved="1"))
    saved = cfg.get("server_statistics_channels", {})
    if not isinstance(saved, dict):
        saved = {}
    # Migrate a previous single statistics channel to the member counter by default.
    legacy = cfg.get("server_statistics_channel")
    if legacy and not saved.get("members"):
        saved = dict(saved)
        saved["members"] = legacy
    options_by_type = {}
    for key, _label, _template, _counter in stats_types:
        options = ['<option value="">— Nicht verwenden —</option>']
        current = str(saved.get(key) or "")
        for channel in sorted(guild.voice_channels, key=lambda item: item.position):
            mark = " selected" if str(channel.id) == current else ""
            options.append(f'<option value="{channel.id}"{mark}>{html.escape(channel.name)}</option>')
        options_by_type[key] = ''.join(options)
    rows = []
    for key, label, template, counter in stats_types:
        channel_id = saved.get(key) or (legacy if key == "members" else None)
        channel = guild.get_channel(int(channel_id)) if str(channel_id or "").isdigit() else None
        current_preview = template.format(count=counter()) if channel else "Nicht aktiviert"
        rows.append(
            '<div class="stat-setting" style="padding:12px 0;border-bottom:1px solid var(--border)">'
            + '<label>' + html.escape(label) + '</label>'
            + '<select name="stat_' + key + '">' + options_by_type[key] + '</select>'
            + '<small class="muted">Vorschau: ' + html.escape(current_preview) + '</small></div>'
        )
    body = (
        '<section class="card"><h1>📊 Server-Statistik</h1>'
        '<p class="muted">Wie bei GalaxyBot bekommt jede Statistik ihren eigenen Sprachkanal. Wähle für jede Zahl einen anderen Kanal oder „Nicht verwenden“. Die Namen werden automatisch etwa alle fünf Minuten aktualisiert.</p>'
        + ('<p class="pill">Einstellungen gespeichert</p>' if request.args.get("saved") == "1" else '')
        + '<form method="post">' + _csrf_field() + ''.join(rows)
        + '<button type="submit">Statistikkanäle speichern</button></form>'
        + '<h2>Beispiele</h2><p><code>👥・Mitglieder: 1250</code></p>'
        + '<p><code>🤖・Bots: 18</code></p><p><code>🚀・Booster: 24</code></p>'
        + '<p class="muted">Der Bot benötigt „Kanäle verwalten“. Die Mitglieder- und Bot-Zahlen richten sich nach den Mitgliederdaten, die Discord dem Bot bereitstellt.</p>'
        + '<p><a class="button secondary" href="/guild/' + gid + '">← Zur Serverübersicht</a></p></section>'
    )
    return _render(guild.name + " · Server-Statistik", body)


@app.route("/guild/<guild_id>/stats", methods=["GET"])
def guild_live_stats(guild_id):
    guild, response = _authorized_guild(guild_id)
    if response:
        return response
    commands = _command_records(guild.id)
    modules = _module_records(guild.id)
    stats_root = getattr(_bot, "dashboard_data", {}).get("stats", {}).get(str(guild.id), {})
    message_total = sum(max(0, int(item.get("messages", 0) or 0)) for item in stats_root.values() if isinstance(item, dict))
    voice_seconds = sum(max(0, int(item.get("voice_seconds", 0) or 0)) for item in stats_root.values() if isinstance(item, dict))
    active_users = sum(1 for item in stats_root.values() if isinstance(item, dict) and item.get("last_activity_at"))
    body = (
        '<section class="card"><h1>📊 Live-Statistiken</h1><p class="muted">Werte aus dem aktuellen Bot-Prozess; Aktualisierung beim Neuladen.</p>'
        '<div class="grid"><div class="notice"><small>Mitglieder (Discord-Cache)</small><h2>' + str(guild.member_count or len(guild.members)) + '</h2></div>'
        '<div class="notice"><small>Textkanäle</small><h2>' + str(len(guild.text_channels)) + '</h2></div>'
        '<div class="notice"><small>Rollen</small><h2>' + str(len(guild.roles)) + '</h2></div>'
        '<div class="notice"><small>Enablede Befehle</small><h2>' + str(sum(1 for item in commands if item["enabled"])) + '</h2></div>'
        '<div class="notice"><small>Erkannte Module</small><h2>' + str(len(modules)) + '</h2></div>'
        '<div class="notice"><small>Erfasste Nachrichten</small><h2>' + str(message_total) + '</h2></div>'
        '<div class="notice"><small>Mitglieder mit Enableditätsdaten</small><h2>' + str(active_users) + '</h2></div>'
        '<div class="notice"><small>Erfasste Voice-Zeit</small><h2>' + str(voice_seconds // 3600) + ' Std.</h2></div></div>'
        '<p class="muted">Enableditätswerte stammen aus gespeicherten Bot-Statistiken und sind keine Echtzeit-Zeitreihe.</p><a class="button secondary" href="/guild/' + str(guild.id) + '">← Serverübersicht</a></section>'
    )
    return _render(guild.name + " · Live-Statistiken", body)

@app.route("/guild/<guild_id>/isle-roster", methods=["GET"])
def guild_isle_roster(guild_id):
    """Show the selected guild's currently signed-in The Isle players."""
    guild, response = _authorized_guild(guild_id)
    if response:
        return response
    dashboard_data = getattr(_bot, "dashboard_data", {})
    registrations_root = dashboard_data.get("registrations", {}) if isinstance(dashboard_data, dict) else {}
    records = registrations_root.get(str(guild.id), {}) if isinstance(registrations_root, dict) else {}
    rows = []
    if isinstance(records, dict):
        for user_id, record in records.items():
            if not isinstance(record, dict):
                continue
            dinosaur = record.get("dinosaur")
            location = record.get("location")
            skin = record.get("skin", "")
            pack = record.get("pack", "")
            if not isinstance(dinosaur, str) or not dinosaur.strip() or not isinstance(location, str) or not location.strip():
                continue
            member = guild.get_member(int(user_id)) if str(user_id).isdigit() else None
            player_name = member.display_name if member else ("Discord-ID " + str(user_id))
            signed_at = str(record.get("signed_in_at", ""))
            try:
                parsed = datetime.fromisoformat(signed_at.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                signed_label = parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            except (TypeError, ValueError):
                signed_label = "Unbekannt"
            rows.append(
                "<tr><td>" + html.escape(player_name) + "</td><td>" + html.escape(dinosaur.strip()) +
                "</td><td>" + html.escape(skin.strip() if isinstance(skin, str) else "") +
                "</td><td>" + html.escape(pack.strip() if isinstance(pack, str) and pack.strip() else "Solo") +
                "</td><td>" + html.escape(location.strip()) + "</td><td>" + html.escape(signed_label) + "</td></tr>"
            )
    rows.sort(key=lambda row: row.casefold())
    table = (
        '<div style="overflow-x:auto"><table><thead><tr><th>Spieler</th><th>Dinosaurier</th><th>Skin</th><th>Pack / Rudel</th><th>Standort</th><th>Angemeldet seit</th></tr></thead><tbody>'
        + ("".join(rows) if rows else '<tr><td colspan="6">Aktuell sind keine gültigen Spieler-Anmeldungen gespeichert.</td></tr>')
        + '</tbody></table></div>'
    )
    body = (
        '<section class="card"><span class="pill">THE ISLE · LIVE ROSTER</span><h1>🦖 Spieler-Anmeldungen</h1>'
        '<p class="muted">Aktuell gespeicherte Anmeldungen dieses Servers. Die Liste wird beim Neuladen aktualisiert.</p>'
        '<div class="notice"><strong>' + str(len(rows)) + '</strong> aktive Anmeldung(en)</div>'
        + table + '<p class="muted">Die Übersicht zeigt nur gültige Datensätze. Für aktuelle Namen muss der Spieler im Discord-Cache verfügbar sein.</p>'
        + '<a class="button secondary" href="/guild/' + str(guild.id) + '">← Serverübersicht</a></section>'
    )
    return _render(guild.name + " · The Isle Spieler", body)


@app.route("/admin/premium-servers", methods=["GET", "POST"])
def dashboard_premium_servers_admin():
    login = _require_login()
    if login:
        return login
    user_id = str(session.get("user_id", ""))
    # Hard allowlist: only this Discord account may manage manual server Premium grants.
    if user_id != "1493110334280564807":
        abort(403, "Nur der autorisierte Dashboard-Besitzer darf Premium-Server freischalten.")
    premium_file = data_path("premium_guilds.json")
    try:
        stored = json.loads(premium_file.read_text(encoding="utf-8"))
        selected = {str(x) for x in stored if str(x).isdigit()} if isinstance(stored, list) else set()
    except (OSError, json.JSONDecodeError):
        selected = set()
    if request.method == "POST":
        if not _csrf_ok():
            abort(400, "CSRF-Prüfung fehlgeschlagen.")
        submitted = {x for x in request.form.getlist("guild_ids") if x.isdigit() and _bot.get_guild(int(x))}
        temp = premium_file.with_suffix(".tmp")
        temp.write_text(json.dumps(sorted(submitted), indent=2), encoding="utf-8")
        os.replace(temp, premium_file)
        for server in _bot.guilds:
            cfg = _guild_config(server.id)
            if str(server.id) in submitted:
                cfg["premium_server_override"] = "premium"
            else:
                cfg.pop("premium_server_override", None)
        _save_json(data_path("config.json"), _config)
        return redirect(url_for("dashboard_premium_servers_admin"))
    rows = []
    for guild in sorted(_bot.guilds, key=lambda g: g.name.lower()):
        checked = " checked" if str(guild.id) in selected else ""
        rows.append(f'<label style="display:flex;gap:12px;align-items:center;padding:12px 0;border-bottom:1px solid var(--border)"><input type="checkbox" name="guild_ids" value="{guild.id}"{checked}><span><strong>{html.escape(guild.name)}</strong><br><small>Server-ID: {guild.id}</small></span></label>')
    body = '<section class="card"><span class="pill">OWNER ONLY</span><h1>Premium-Server freischalten</h1><p class="muted">Nur konfigurierte Dashboard-Administratoren sehen diese Seite. Markiere die Server, die manuell Premium erhalten sollen.</p><form method="post">' + _csrf_field() + ''.join(rows) + '<button type="submit">Premium-Server speichern</button></form><p><a href="/">Zur Übersicht</a></p></section>'
    return _render("Owner · Premium-Server", body)


@app.route("/guild/<guild_id>/economy", methods=["GET", "POST"])
def guild_economy(guild_id):
    """Configure the same SQLite settings consumed by the economy commands."""
    guild, response = _authorized_guild(guild_id)
    if response:
        return response
    gid = str(guild.id)
    db_path = data_path("ironwolf_economy.sqlite3")
    defaults = {
        "currency_name": "Hamburg-Dollar", "currency_emoji": "💶",
        "starting_balance": "500", "work_min": "100", "work_max": "300",
        "work_cooldown": "3600", "crime_min": "150", "crime_max": "600",
        "crime_cooldown": "1800", "crime_success_percent": "55",
        "police_catch_percent": "35", "crime_fine_percent": "20",
    }
    def load_economy_settings():
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(db_path), timeout=15) as con:
            con.execute("""CREATE TABLE IF NOT EXISTS settings (
                guild_id INTEGER NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
                PRIMARY KEY (guild_id, key))""")
            rows = con.execute("SELECT key, value FROM settings WHERE guild_id=?", (guild.id,)).fetchall()
        values = dict(defaults)
        values.update({str(key): str(value) for key, value in rows if key in defaults})
        return values
    values = load_economy_settings()
    result_html = ""
    if request.method == "POST":
        if not _csrf_ok():
            abort(400, "CSRF-Prüfung fehlgeschlagen. Bitte Seite neu laden.")
        actor = guild.get_member(int(session.get("user_id", "0")))
        if actor is None or not (actor.guild_permissions.administrator or actor.guild_permissions.manage_guild):
            abort(403, "Du brauchst Administrator- oder Server-verwalten-Rechte.")
        currency_name = request.form.get("currency_name", "").strip()
        currency_emoji = request.form.get("currency_emoji", "").strip()
        if not currency_name or len(currency_name) > 32 or not currency_emoji or len(currency_emoji) > 12:
            abort(400, "Währungsname (1–32 Zeichen) und Emoji (1–12 Zeichen) sind erforderlich.")
        numeric_rules = {
            "starting_balance": (0, 1_000_000_000),
            "work_min": (1, 1_000_000), "work_max": (1, 1_000_000),
            "crime_min": (1, 1_000_000), "crime_max": (1, 1_000_000),
            "work_cooldown": (60, 604800), "crime_cooldown": (60, 604800),
            "crime_success_percent": (0, 100), "police_catch_percent": (0, 100),
            "crime_fine_percent": (0, 100),
        }
        new_values = {"currency_name": currency_name, "currency_emoji": currency_emoji}
        for key, (minimum, maximum) in numeric_rules.items():
            raw = request.form.get(key, "").strip()
            try:
                number = int(raw)
            except (TypeError, ValueError):
                abort(400, "Ungültiger Zahlenwert für " + key + ".")
            if not minimum <= number <= maximum:
                abort(400, "Wert außerhalb des erlaubten Bereichs für " + key + ".")
            new_values[key] = str(number)
        if int(new_values["work_min"]) > int(new_values["work_max"]) or int(new_values["crime_min"]) > int(new_values["crime_max"]):
            abort(400, "Ein Mindestwert darf nicht größer als der zugehörige Höchstwert sein.")
        with sqlite3.connect(str(db_path), timeout=15) as con:
            con.execute("""CREATE TABLE IF NOT EXISTS settings (
                guild_id INTEGER NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
                PRIMARY KEY (guild_id, key))""")
            con.executemany("""INSERT INTO settings(guild_id,key,value) VALUES(?,?,?)
                ON CONFLICT(guild_id,key) DO UPDATE SET value=excluded.value""",
                [(guild.id, key, value) for key, value in new_values.items()])
            con.commit()
        _audit_dashboard_change(guild.id, "Wirtschaftssystem", session.get("user_id"), "Economy-Einstellungen über das Dashboard gespeichert.")
        return redirect(url_for("guild_economy", guild_id=gid))
    values = load_economy_settings()
    fields = [
        ("currency_name", "Währungsname", "text", 32),
        ("currency_emoji", "Währungs-Emoji", "text", 12),
        ("starting_balance", "Startguthaben", "number", None),
        ("work_min", "Arbeit: Mindestverdienst", "number", None),
        ("work_max", "Arbeit: Höchstverdienst", "number", None),
        ("work_cooldown", "Arbeit: Cooldown in Sekunden", "number", None),
        ("crime_min", "Crime: Mindestbeute", "number", None),
        ("crime_max", "Crime: Höchstbeute", "number", None),
        ("crime_cooldown", "Crime: Cooldown in Sekunden", "number", None),
        ("crime_success_percent", "Crime: Erfolgswahrscheinlichkeit (%)", "number", None),
        ("police_catch_percent", "Crime: Polizei-Erwischungswahrscheinlichkeit (%)", "number", None),
        ("crime_fine_percent", "Crime: Geldstrafe (%)", "number", None),
    ]
    form_fields = ''.join(
        '<label for="' + key + '">' + html.escape(label) + '</label>'
        '<input id="' + key + '" name="' + key + '" type="' + kind + '"'
        + (' maxlength="' + str(limit) + '"' if limit else ' min="0"')
        + ' value="' + html.escape(str(values[key]), quote=True) + '" required>'
        for key, label, kind, limit in fields
    )
    body = (
        '<section class="card"><span class="pill">ECONOMY CONTROL</span><h1>💰 Wirtschaftssystem</h1>'
        '<p>Diese Werte werden in derselben SQLite-Tabelle gespeichert, die die Economy-Befehle lesen. Änderungen wirken auf die Economy-Funktionen, sobald das Economy-Modul geladen ist.</p>'
        '<form method="post">' + _csrf_field() + form_fields
        + '<button type="submit">Wirtschaftseinstellungen speichern</button></form>'
        '<p class="muted">Cooldowns werden in Sekunden angegeben. Bereits vorhandene Kontostände werden nicht zurückgesetzt; das Startguthaben gilt für neu angelegte Konten.</p>'
        '</section><p><a class="button secondary" href="/guild/' + gid + '">← Zur Serververwaltung</a></p>'
    )
    return _render(guild.name + " · Wirtschaftssystem", body)


@app.route("/guild/<guild_id>/verification", methods=["GET", "POST"])
def guild_verification(guild_id):
    guild, response = _authorized_guild(guild_id)
    if response:
        return response
    gid = str(guild.id)
    cfg = _guild_config(guild.id)
    result_html = ""
    if request.method == "POST":
        if not _csrf_ok():
            abort(400, "CSRF-Prüfung fehlgeschlagen. Bitte Seite neu laden.")
        actor = guild.get_member(int(session.get("user_id", "0")))
        if actor is None or not (actor.guild_permissions.administrator or actor.guild_permissions.manage_guild):
            abort(403, "Du brauchst Administrator- oder Server-verwalten-Rechte.")
        if request.form.get("action") == "delete_verification":
            old_channel = guild.get_channel(int(cfg.get("verify_channel") or 0))
            old_message_id = cfg.get("verify_message_id")
            async def delete_verification_message():
                if not old_message_id or not isinstance(old_channel, discord.TextChannel):
                    return
                try:
                    message = await old_channel.fetch_message(int(old_message_id))
                    await message.delete()
                except discord.NotFound:
                    pass
            try:
                asyncio.run_coroutine_threadsafe(delete_verification_message(), _bot.loop).result(timeout=20)
                cfg.pop("verify_message_id", None)
                _save_json(data_path("config.json"), _config)
                _audit_dashboard_change(guild.id, "Verifizierung", session.get("user_id"), "Änderung gespeichert")
                return redirect(url_for("guild_verification", guild_id=gid))
            except Exception as exc:
                result_html = '<div class="notice">⚠️ Löschen fehlgeschlagen: ' + html.escape(str(exc)[:300]) + '</div>'
        try:
            channel_id = int(request.form.get("channel_id", ""))
            verified_role_id = int(request.form.get("verified_role_id", ""))
            unverified_raw = request.form.get("unverified_role_id", "").strip()
            unverified_role_id = int(unverified_raw) if unverified_raw else None
        except ValueError:
            abort(400, "Bitte einen gültigen Kanal und eine Verifizierungsrolle auswählen.")
        channel = guild.get_channel(channel_id)
        verified_role = guild.get_role(verified_role_id)
        unverified_role = guild.get_role(unverified_role_id) if unverified_role_id else None
        bot_member = guild.me
        if not isinstance(channel, discord.TextChannel) or verified_role is None:
            abort(400, "Ungültiger Verifizierungskanal oder ungültige Rolle.")
        if bot_member is None or verified_role.is_default() or verified_role >= bot_member.top_role:
            abort(400, "Die Bot-Rolle muss über der Verifizierungsrolle stehen.")
        if unverified_role and (unverified_role.is_default() or unverified_role >= bot_member.top_role):
            abort(400, "Die Bot-Rolle muss über der zu entfernenden Rolle stehen.")
        if unverified_role and unverified_role.id == verified_role.id:
            abort(400, "Die zu vergebende und die zu entfernende Rolle müssen unterschiedlich sein.")
        mode = request.form.get("mode", "click")
        if mode not in ("click", "code"):
            abort(400, "Ungültiger Verifizierungsmodus.")
        title = request.form.get("title", "").strip()[:256] or "🔐 Server-Verifizierung"
        description = request.form.get("description", "").strip()[:4000] or "Lies die Serverregeln und klicke auf den Button, um dich zu verifizieren."
        button_label = request.form.get("button_label", "").strip()[:80] or "Verifizieren"
        emoji = request.form.get("emoji", "").strip()[:100] or "✅"
        old_verify_channel_id = cfg.get("verify_channel")
        old_verify_message_id = cfg.get("verify_message_id")
        cfg.update({
            "verify_channel": channel.id, "verified_role": verified_role.id,
            "unverified_role": unverified_role.id if unverified_role else None,
            "verify_mode": mode, "verify_button_label": button_label, "verify_button_emoji": emoji,
            "verify_embed_title": title, "verify_embed_description": description,
        })
        async def publish_verification():
            if bot_member is None or not channel.permissions_for(bot_member).send_messages or not channel.permissions_for(bot_member).embed_links:
                raise PermissionError("Der Bot benötigt in diesem Kanal die Rechte „Nachrichten senden“ und „Links einbetten“.")
            view_cls = getattr(sys.modules.get("__main__"), "VerifyView", None) or getattr(sys.modules.get("bot"), "VerifyView", None)
            if view_cls is None:
                raise RuntimeError("VerifyView wurde nicht gefunden.")
            raw_embed_color = str(cfg.get("default_embed_color", "5865F2")).strip().lstrip("#")
            if not re.fullmatch(r"[0-9a-fA-F]{6}", raw_embed_color):
                raw_embed_color = "5865F2"
            embed = discord.Embed(title=title, description=description, color=discord.Color(int(raw_embed_color, 16)))
            embed.set_footer(text="IronWolf · Created by KingSnowy")
            if old_verify_message_id and str(old_verify_channel_id) == str(channel.id):
                try:
                    old_message = await channel.fetch_message(int(old_verify_message_id))
                    await old_message.edit(embed=embed, view=view_cls(button_label, emoji))
                    return old_message.id, "Verifizierungs-Panel wurde aktualisiert."
                except discord.NotFound:
                    pass
            message = await channel.send(embed=embed, view=view_cls(button_label, emoji), allowed_mentions=discord.AllowedMentions.none())
            return message.id, "Verifizierungs-Panel wurde erstellt."
        try:
            message_id, message = asyncio.run_coroutine_threadsafe(publish_verification(), _bot.loop).result(timeout=25)
            cfg["verify_message_id"] = message_id
            _save_json(data_path("config.json"), _config)
            _audit_dashboard_change(guild.id, "Verifizierung", session.get("user_id"), "Änderung gespeichert")
            result_html = '<div class="notice">✅ ' + html.escape(message) + ' in #' + html.escape(channel.name) + '.</div>'
        except Exception as exc:
            _save_json(data_path("config.json"), _config)
            _audit_dashboard_change(guild.id, "Verifizierung", session.get("user_id"), "Änderung gespeichert")
            result_html = '<div class="notice">⚠️ ' + html.escape(str(exc)[:400] or type(exc).__name__) + '</div>'
    channels = _guild_channels(guild)
    roles = _guild_roles(guild)
    def options(items, current, empty_label="— Nicht festgelegt —"):
        out = [f'<option value="">{empty_label}</option>']
        for item_id, name in items:
            selected = " selected" if str(item_id) == str(current or "") else ""
            out.append(f'<option value="{item_id}"{selected}>{html.escape(name)}</option>')
        return "".join(out)
    mode = cfg.get("verify_mode", "click")
    body = (
        f'<span class="pill">IRONWOLF · VERIFICATION CENTER</span><h1 style="margin-top:12px">🔐 Verifizierung · {html.escape(guild.name)}</h1>'
        '<p class="muted">Erstelle ein neues Verifizierungs-Panel oder bearbeite das gespeicherte Panel. Beim Speichern wird die vorhandene Nachricht nach Möglichkeit aktualisiert.</p>'
        + result_html +
        '<section class="card"><form method="post">' + _csrf_field() +
        '<label>Verifizierungskanal</label><select name="channel_id" required>' + options(channels, cfg.get("verify_channel"), "— Textkanal auswählen —") + '</select>'
        + '<label>Rolle nach Verifizierung vergeben</label><select name="verified_role_id" required>' + options(roles, cfg.get("verified_role"), "— Rolle auswählen —") + '</select>'
        + '<label>Rolle nach Verifizierung entfernen (optional)</label><select name="unverified_role_id">' + options(roles, cfg.get("unverified_role")) + '</select>'
        + '<label>Verifizierungsmethode</label><select name="mode"><option value="click"' + (' selected' if mode == "click" else '') + '>Button klicken</option><option value="code"' + (' selected' if mode == "code" else '') + '>Bild-Captcha</option></select>'
        + '<label>Embed-Titel</label><input name="title" maxlength="256" value="' + html.escape(str(cfg.get("verify_embed_title", "🔐 Server-Verifizierung")), quote=True) + '">'
        + '<label>Description</label><textarea name="description" maxlength="4000">' + html.escape(str(cfg.get("verify_embed_description", "Lies die Serverregeln und klicke auf den Button, um dich zu verifizieren."))) + '</textarea>'
        + '<label>Button-Text</label><input name="button_label" maxlength="80" value="' + html.escape(str(cfg.get("verify_button_label", "Verifizieren")), quote=True) + '">'
        + '<label>Button-Emoji</label><input name="emoji" maxlength="100" value="' + html.escape(str(cfg.get("verify_button_emoji", "✅")), quote=True) + '">'
        + '<button type="submit">Verifizierung speichern und veröffentlichen</button></form><form method="post" onsubmit="return confirm(\'Verifizierungsnachricht wirklich löschen?\')">' + _csrf_field() + '<input type="hidden" name="action" value="delete_verification"><button type="submit" class="secondary">🗑️ Verifizierungsnachricht löschen</button></form></section>'
        + f'<p><a class="button secondary" href="/guild/{gid}">← Zur Serververwaltung</a></p>'
    )
    return _render(guild.name + " · Verifizierung", body)


@app.route("/guild/<guild_id>/server-booster", methods=["GET", "POST"])
@app.route("/guild/<guild_id>/serverboost", methods=["GET", "POST"])
@app.route("/guild/<guild_id>/server-boost", methods=["GET", "POST"])
def guild_server_boost(guild_id):
    """Configure boost announcements and the optional booster role."""
    print(f"[DASHBOARD] Server-Booster route opened for guild {guild_id}.", flush=True)
    guild, response = _authorized_guild(guild_id)
    if response:
        return response
    gid = str(guild.id)
    cfg = _guild_config(guild.id)
    if request.method == "POST":
        if not _csrf_ok():
            abort(400, "CSRF-Prüfung fehlgeschlagen. Bitte Seite neu laden.")
        actor = guild.get_member(int(session.get("user_id", "0")))
        if actor is None or not (actor.guild_permissions.administrator or actor.guild_permissions.manage_guild):
            abort(403, "Du brauchst Administrator- oder Server-verwalten-Rechte.")
        channel_id = request.form.get("channel_id", "").strip()
        role_id = request.form.get("role_id", "").strip()
        message = request.form.get("message", "").strip()[:1800]
        if channel_id and not any(str(cid) == channel_id for cid, _ in _guild_channels(guild)):
            abort(400, "Bitte wähle einen gültigen Textkanal.")
        if role_id and not any(str(rid) == role_id for rid, _ in _guild_roles(guild)):
            abort(400, "Bitte wähle eine gültige Rolle.")
        cfg["server_boost_enabled"] = request.form.get("enabled") == "on"
        cfg["server_boost_channel_id"] = int(channel_id) if channel_id.isdigit() else None
        cfg["server_boost_role_id"] = int(role_id) if role_id.isdigit() else None
        cfg["server_boost_message"] = message or "🚀 Danke {mention} für den Server-Boost von **{server}**!"
        _save_json(data_path("config.json"), _config)
        _audit_dashboard_change(guild.id, "Server-Boost", session.get("user_id"), "Boost-Einstellungen gespeichert")
        return redirect(url_for("guild_server_boost", guild_id=gid, saved="1"))
    channels = _guild_channels(guild)
    roles = _guild_roles(guild)
    selected_channel = str(cfg.get("server_boost_channel_id") or "")
    selected_role = str(cfg.get("server_boost_role_id") or "")
    channel_options = '<option value="">— Kanal auswählen —</option>' + "".join(
        '<option value="' + str(cid) + '"' + (' selected' if str(cid) == selected_channel else '') + '>' + html.escape(name) + '</option>'
        for cid, name in channels
    )
    role_options = '<option value="">— Keine Booster-Rolle —</option>' + "".join(
        '<option value="' + str(rid) + '"' + (' selected' if str(rid) == selected_role else '') + '>' + html.escape(name) + '</option>'
        for rid, name in roles
    )
    saved = '<div class="notice">Einstellungen gespeichert.</div>' if request.args.get("saved") else ""
    enabled = " checked" if cfg.get("server_boost_enabled", False) else ""
    message = html.escape(str(cfg.get("server_boost_message") or "🚀 Danke {mention} für den Server-Boost von **{server}**!"), quote=True)
    body = (
        '<span class="pill">SERVER-BOOST</span><h1 style="margin-top:12px">Server-Booster</h1>'
        '<p class="muted">Konfiguriere die Nachricht bei einem neuen Boost und optional eine Booster-Rolle.</p>'
        + saved + '<section class="card"><form method="post">' + _csrf_field()
        + '<div class="row"><strong>Boost-System aktivieren</strong><label class="check"><input type="checkbox" name="enabled"' + enabled + '> Enabled</label></div>'
        + '<label for="channel_id">Ankündigungskanal</label><select id="channel_id" name="channel_id">' + channel_options + '</select>'
        + '<label for="role_id">Booster-Rolle (optional)</label><select id="role_id" name="role_id">' + role_options + '</select>'
        + '<label for="message">Boost-Nachricht</label><textarea id="message" name="message" maxlength="1800" required>' + message + '</textarea>'
        + '<p class="muted">Platzhalter: <code>{mention}</code> für das Mitglied und <code>{server}</code> für den Servernamen.</p>'
        + '<button type="submit">Einstellungen speichern</button></form></section>'
        + '<p><a class="button secondary" href="/guild/' + gid + '">Zur Serververwaltung</a></p>'
    )
    return _render(guild.name + " · Server-Booster", body)


@app.route("/guild/<guild_id>/tickets", methods=["GET", "POST"])
def guild_tickets(guild_id):
    guild, response = _authorized_guild(guild_id)
    if response:
        return response
    gid = str(guild.id)
    cfg = _guild_config(guild.id)
    panels = cfg.setdefault("ticket_panels", {})
    result_html = ""
    if request.method == "POST":
        if not _csrf_ok():
            abort(400, "CSRF-Prüfung fehlgeschlagen. Bitte Seite neu laden.")
        actor = guild.get_member(int(session.get("user_id", "0")))
        if actor is None or not (actor.guild_permissions.administrator or actor.guild_permissions.manage_guild):
            abort(403, "Du brauchst Administrator- oder Server-verwalten-Rechte.")
        panel_key = request.form.get("panel_id", "").strip()
        if request.form.get("action") == "delete_panel":
            panel_to_delete = panels.get(panel_key)
            if not panel_to_delete:
                abort(404, "Ticket-Panel nicht gefunden.")
            old_channel = guild.get_channel(int(panel_to_delete.get("channel_id") or 0))
            old_message_id = panel_to_delete.get("message_id")
            async def delete_ticket_message():
                if not old_message_id or not isinstance(old_channel, discord.TextChannel):
                    return
                try:
                    message = await old_channel.fetch_message(int(old_message_id))
                    await message.delete()
                except discord.NotFound:
                    pass
            try:
                asyncio.run_coroutine_threadsafe(delete_ticket_message(), _bot.loop).result(timeout=20)
                del panels[panel_key]
                _save_json(data_path("config.json"), _config)
                _audit_dashboard_change(guild.id, "Tickets", session.get("user_id"), "Änderung gespeichert")
                return redirect(url_for("guild_tickets", guild_id=gid))
            except Exception as exc:
                result_html = '<div class="notice">⚠️ Löschen fehlgeschlagen: ' + html.escape(str(exc)[:300]) + '</div>'
        submitted_labels = [request.form.get(f"button{i}", "").strip() for i in range(1, 11)]
        if not any(submitted_labels):
            abort(400, "Mindestens ein Button-Label ist erforderlich.")
        if not guild_has_feature(cfg, "ticket") and sum(bool(label) for label in submitted_labels) > 1:
            abort(403, "Mehrere Ticket-Buttons sind Premium. Im Free-Plan ist ein Button pro Panel verfügbar.")
        if panel_key == "__new__" and not guild_has_feature(cfg, "ticket") and panels:
            abort(403, "Mehrere Ticket-Panels sind Premium. Im Free-Plan kannst du ein Panel mit einem Button verwenden.")
        if panel_key == "__new__":
            panel_key = secrets.token_hex(4)
            while panel_key in panels:
                panel_key = secrets.token_hex(4)
            panel = {"title": "Support-Ticket", "description": "", "labels": ["Allgemeiner Support"], "channel_id": None, "category_id": None, "support_role_id": None}
            panels[panel_key] = panel
        else:
            panel = panels.get(panel_key)
        if not panel:
            abort(404, "Ticket-Panel nicht gefunden.")
        title = request.form.get("title", "").strip()[:256]
        description = request.form.get("description", "").strip()[:4000]
        submitted_labels = [request.form.get(f"button{i}", "").strip()[:80] for i in range(1, 11)]
        submitted_descriptions = [request.form.get(f"button_description{i}", "").strip()[:100] for i in range(1, 11)]
        option_pairs = [
            (label, submitted_descriptions[index])
            for index, label in enumerate(submitted_labels)
            if label
        ]
        labels = [label for label, _ in option_pairs]
        option_descriptions = [description for _, description in option_pairs]
        channel_raw = request.form.get("channel_id", "").strip()
        category_raw = request.form.get("category_id", "").strip()
        role_raw = request.form.get("support_role_id", "").strip()
        try:
            channel_id = int(channel_raw) if channel_raw else None
            category_id = int(category_raw) if category_raw else None
            support_role_id = int(role_raw) if role_raw else None
        except ValueError:
            abort(400, "Ungültige Kanal- oder Rollen-ID.")
        channel = guild.get_channel(channel_id) if channel_id else None
        category = guild.get_channel(category_id) if category_id else None
        support_role = guild.get_role(support_role_id) if support_role_id else None
        if not title:
            abort(400, "Der Titel darf nicht leer sein.")
        if not labels:
            abort(400, "Mindestens ein Button-Label ist erforderlich.")
        if not guild_has_feature(cfg, "ticket") and len(labels) > 1:
            abort(403, "Mehrere Ticket-Optionen sind Premium. Im Free-Plan ist eine Option pro Panel verfügbar.")
        display_mode = request.form.get("display_mode", "buttons").strip().lower()
        if display_mode not in {"buttons", "select"}:
            abort(400, "Ungültige Darstellung für das Ticket-Panel.")
        if display_mode == "select" and not guild_has_feature(cfg, "ticket"):
            abort(403, "Das Ticket-Dropdown ist eine Premium-Funktion.")
        if not isinstance(channel, discord.TextChannel):
            abort(400, "Bitte wähle einen gültigen Textkanal.")
        if category_id and not isinstance(category, discord.CategoryChannel):
            abort(400, "Bitte wähle eine gültige Ticket-Kategorie.")
        if support_role_id and support_role is None:
            abort(400, "Bitte wähle eine gültige Support role.")
        panel.update({
            "title": title,
            "description": description,
            "labels": labels,
            "option_descriptions": option_descriptions,
            "display_mode": display_mode,
            "channel_id": channel.id,
            "category_id": category.id if category else None,
            "support_role_id": support_role.id if support_role else None,
        })
        if guild_has_feature(cfg, "branding"):
            import re
            color_raw = request.form.get("brand_color", "5865F2").strip().lstrip("#")
            logo_url = request.form.get("brand_logo_url", "").strip()
            if not re.fullmatch(r"[0-9a-fA-F]{6}", color_raw):
                abort(400, "Die Branding-Farbe muss ein 6-stelliger Hex-Farbcode sein.")
            if logo_url and (not logo_url.startswith("https://") or len(logo_url) > 500):
                abort(400, "Das Logo muss eine HTTPS-URL mit höchstens 500 Zeichen sein.")
            panel["brand_color"] = color_raw.upper()
            panel["brand_logo_url"] = logo_url
            uploaded_logo = request.files.get("brand_logo_file")
            if uploaded_logo and uploaded_logo.filename:
                extension = Path(uploaded_logo.filename).suffix.lower()
                if extension not in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
                    abort(400, "Bitte ein Bild im PNG-, JPG-, GIF- oder WEBP-Format hochladen.")
                raw_logo = uploaded_logo.read()
                if not raw_logo or len(raw_logo) > 5 * 1024 * 1024:
                    abort(400, "Das Logo darf nicht leer sein und höchstens 5 MB groß sein.")
                logo_dir = Path(data_path("config.json")).parent / "ticket_logos"
                logo_dir.mkdir(parents=True, exist_ok=True)
                logo_path_saved = logo_dir / f"{guild.id}_{panel_key}{extension}"
                logo_path_saved.write_bytes(raw_logo)
                panel["brand_logo_file"] = str(logo_path_saved)
                panel["brand_logo_filename"] = logo_path_saved.name
            if request.form.get("remove_brand_logo") == "on":
                old_logo = panel.pop("brand_logo_file", None)
                panel.pop("brand_logo_filename", None)
                if old_logo:
                    try:
                        Path(old_logo).unlink(missing_ok=True)
                    except OSError:
                        pass
        async def save_panel_message():
            bot_member = guild.me
            if bot_member is None or not channel.permissions_for(bot_member).send_messages or not channel.permissions_for(bot_member).embed_links:
                raise PermissionError("Der Bot benötigt in diesem Kanal die Rechte „Nachrichten senden“ und „Links einbetten“.")
            view_cls = getattr(sys.modules.get("__main__"), "TicketPanelView", None)
            if view_cls is None:
                view_cls = getattr(sys.modules.get("bot"), "TicketPanelView", None)
            if view_cls is None:
                raise RuntimeError("Die vorhandene TicketPanelView-Klasse konnte nicht gefunden werden; die Panel-Konfiguration wurde noch nicht veröffentlicht.")
            panel_color = panel.get("brand_color", "5865F2") if guild_has_feature(cfg, "branding") else "5865F2"
            try:
                embed_color = discord.Color(int(str(panel_color).lstrip("#"), 16))
            except (TypeError, ValueError):
                embed_color = discord.Color.blurple()
            embed = discord.Embed(title=title, description=description or None, color=embed_color)
            logo_url = panel.get("brand_logo_url", "") if guild_has_feature(cfg, "branding") else ""
            logo_path = Path(panel.get("brand_logo_file", "")) if guild_has_feature(cfg, "branding") and panel.get("brand_logo_file") else None
            logo_path = logo_path if logo_path and logo_path.is_file() else None
            if logo_path:
                embed.set_thumbnail(url=f"attachment://{logo_path.name}")
            elif isinstance(logo_url, str) and logo_url.startswith("https://") and len(logo_url) <= 500:
                embed.set_thumbnail(url=logo_url)
            embed.set_footer(text=f"IronWolf · Created by KingSnowy · Ticket-Panel ID {panel_key}")
            view = view_cls(panel_key, labels, display_mode, option_descriptions)
            old_channel_id = panel.get("channel_id")
            old_message_id = panel.get("message_id")
            # Existing panels with a saved message ID are edited in place when possible.
            if old_message_id and old_channel_id == channel.id:
                try:
                    old_message = await channel.fetch_message(int(old_message_id))
                    if logo_path:
                        await old_message.edit(embed=embed, view=view, attachments=[discord.File(str(logo_path), filename=logo_path.name)])
                    else:
                        await old_message.edit(embed=embed, view=view, attachments=[])
                    return f"Ticket-Panel {panel_key} wurde in #{channel.name} aktualisiert."
                except discord.NotFound:
                    pass
            if logo_path:
                message = await channel.send(embed=embed, view=view, file=discord.File(str(logo_path), filename=logo_path.name), allowed_mentions=discord.AllowedMentions.none())
            else:
                message = await channel.send(embed=embed, view=view, allowed_mentions=discord.AllowedMentions.none())
            panel["message_id"] = message.id
            return f"Ticket-Panel gespeichert und in #{channel.name} veröffentlicht. Nachrichten-ID: {message.id}"
        try:
            result = asyncio.run_coroutine_threadsafe(save_panel_message(), _bot.loop).result(timeout=25)
            _save_json(data_path("config.json"), _config)
            _audit_dashboard_change(guild.id, "Tickets", session.get("user_id"), "Änderung gespeichert")
            result_html = '<div class="notice">✅ ' + html.escape(result) + '</div>'
        except Exception as exc:
            # Keep configuration only if the panel was successfully published/edited.
            detail = str(exc) or type(exc).__name__
            result_html = '<div class="notice">⚠️ ' + html.escape(detail[:500]) + '</div>'
        _save_json(data_path("config.json"), _config)
        _audit_dashboard_change(guild.id, "Tickets", session.get("user_id"), "Änderung gespeichert")
    channels = _guild_channels(guild)
    categories = [(item.id, item.name) for item in guild.categories]
    roles = _guild_roles(guild)
    def options(items, current, empty_label):
        out = [f'<option value="">{empty_label}</option>']
        for item_id, name in items:
            selected = " selected" if str(item_id) == str(current or "") else ""
            out.append(f'<option value="{item_id}"{selected}>{html.escape(name)}</option>')
        return "".join(out)
    ticket_data = {}
    try:
        with open(data_path("data.json"), "r", encoding="utf-8") as ticket_file:
            ticket_data = json.load(ticket_file)
        if not isinstance(ticket_data, dict):
            ticket_data = {}
    except (OSError, ValueError, TypeError):
        ticket_data = {}
    open_tickets = [
        (str(channel_id), meta) for channel_id, meta in ticket_data.get("tickets", {}).items()
        if isinstance(meta, dict) and str(meta.get("guild_id", guild.id)) == str(guild.id)
    ]
    # Older ticket records do not store guild_id; confirm them against actual channels.
    open_tickets = [(cid, meta) for cid, meta in open_tickets if guild.get_channel(int(cid)) is not None]
    claimed_tickets = sum(1 for _, meta in open_tickets if meta.get("claimed_by"))
    priority_tickets = sum(1 for _, meta in open_tickets if meta.get("priority"))
    closed_history = [
        item for item in ticket_data.get("ticket_history", [])
        if isinstance(item, dict) and str(item.get("guild_id")) == str(guild.id)
    ]
    durations = [item.get("resolution_minutes") for item in closed_history if isinstance(item.get("resolution_minutes"), (int, float))]
    avg_resolution = (sum(durations) / len(durations)) if durations else None
    avg_label = (f"{avg_resolution / 60:.1f} Std." if avg_resolution >= 60 else f"{avg_resolution:.0f} Min.") if avg_resolution is not None else "Noch keine Daten"
    ticket_metrics_html = (
        '<section class="card"><span class="pill">TICKET-ANALYTICS</span><h2>📊 Ticket-Übersicht</h2><div class="grid">'
        '<div class="notice"><small>Offene Tickets</small><h2>' + str(len(open_tickets)) + '</h2></div>'
        '<div class="notice"><small>Übernommen</small><h2>' + str(claimed_tickets) + '</h2></div>'
        '<div class="notice"><small>Hohe Priorität</small><h2>' + str(priority_tickets) + '</h2></div>'
        '<div class="notice"><small>Ø Bearbeitungszeit</small><h2>' + html.escape(avg_label) + '</h2></div>'
        '<div class="notice"><small>Geschlossene Tickets (gespeicherte Historie)</small><h2>' + str(len(closed_history)) + '</h2></div>'
        '</div><p class="muted">Die Historie beginnt mit den neuen Bot-Versionen; ältere Schließungen werden nicht rückwirkend ergänzt.</p></section>'
    )
    body = ticket_metrics_html + (
        f'<span class="pill">IRONWOLF · TICKET CENTER</span><h1 style="margin-top:12px">🎟️ Ticket-System · {html.escape(guild.name)}</h1>'
        '<p class="muted">Bearbeite Titel, Description, Anfragearten, Ticket-Kategorie und Support role. Im Ticket können Teammitglieder private Notizen speichern; beim Schließen werden Transcript und Bearbeitungszeit protokolliert.</p>'
        '<div class="notice"><strong>Free vs. Premium Ticket-System</strong><br>🆓 <b>Normal:</b> Tickets eröffnen, private Ticket-Kanäle, Support role, Claim/Freigeben und Schließen. Ein Transcript wird in den Logkanal hochgeladen, wenn er eingerichtet ist.<br>✨ <b>Premium Ticket-System:</b> mehrere konfigurierbare Ticket-Panels, bis zu zehn Anfrage-Buttons oder ein Dropdown-Menü mit bis zu zehn Anfragearten pro Panel, eigenes Panel-Branding (Farbe und Logo), Prioritätsmarkierung für wichtige Tickets und Transcript zusätzlich per DM an den Ticket-Ersteller. Premium-Funktionen benötigen einen verknüpften Premium-Plan oder eine aktive Testphase.</div>'
        + result_html
    )
    body += '<section class="card"><h2>＋ Neues Ticket-Panel erstellen</h2><p class="muted">Erstellt und veröffentlicht ein neues Ticket-Panel direkt aus dem Dashboard.</p><form method="post" enctype="multipart/form-data">' + _csrf_field() + '<input type="hidden" name="panel_id" value="__new__"><label>Titel</label><input name="title" maxlength="256" required value="Support-Ticket"><label>Description</label><textarea name="description" maxlength="4000">Wähle unten die passende Anfrage aus.</textarea><label>Button 1</label><input name="button1" maxlength="80" required value="Allgemeiner Support">' + "".join(f'<label>Button {i} (optional)</label><input name="button{i}" maxlength="80">' for i in range(2, 11)) + "".join(f'<div class="ticket-option-description" hidden><label>Dropdown-Description für Anfrage {i} (optional)</label><input name="button_description{i}" maxlength="100" placeholder="Kurze Erklärung für diese Anfrageart"></div>' for i in range(1, 11)) + '<label>Darstellung (Premium)</label><select name="display_mode" class="ticket-display-mode"><option value="buttons">Buttons (bis zu 10)</option><option value="select">Dropdown-Menü (bis zu 10 Optionen)</option></select><p class="muted">Im Free-Plan ist eine Anfrageart als Button verfügbar. Premium kann bis zu zehn Anfragearten als Buttons oder über ein Dropdown anbieten.</p><script>(function(){const form=document.currentScript.closest("form");if(!form)return;const mode=form.querySelector(".ticket-display-mode");const fields=form.querySelectorAll(".ticket-option-description");function sync(){fields.forEach(el=>{el.hidden=!mode||mode.value!=="select";});}if(mode){mode.addEventListener("change",sync);sync();}})();</script><label>Panel-Kanal</label><select name="channel_id" required>' + options(channels, None, "— Textkanal auswählen —") + '</select><label>Ticket-Kategorie (optional)</label><select name="category_id">' + options(categories, None, "— Standard-Kategorie verwenden —") + '</select><label>Support role (optional)</label><select name="support_role_id">' + options(roles, None, "— Standard-Support role verwenden —") + '</select>' + ('<h3>✨ Custom Branding</h3><label>Embed-Farbe</label><div style="display:flex;gap:12px;align-items:center"><input type="color" name="brand_color_picker" value="#5865F2" oninput="this.form.brand_color.value=this.value.slice(1)" style="width:64px;height:48px;padding:3px"><input name="brand_color" maxlength="6" pattern="[0-9a-fA-F]{6}" value="5865F2" oninput="this.form.brand_color_picker.value=\'#\'+this.value"></div><label>Logo vom Computer hochladen (PNG, JPG, GIF, WEBP; max. 5 MB)</label><input type="file" name="brand_logo_file" accept="image/png,image/jpeg,image/gif,image/webp"><label>Alternativ Logo-URL (HTTPS, optional)</label><input name="brand_logo_url" type="url" maxlength="500"></div>' if guild_has_feature(cfg, "branding") else '') + '<button type="submit">Ticket-Panel erstellen und veröffentlichen</button></form></section>'
    if not panels:
        body += '<div class="notice">Für diesen Server sind noch keine Ticket-Panels gespeichert. Nutze das Formular „Neues Ticket-Panel erstellen“ oben, um eines direkt hier anzulegen.</div>'
    for panel_key, panel in sorted(panels.items(), key=lambda pair: pair[0]):
        labels = list(panel.get("labels", []))[:10]
        option_descriptions = panel.get("option_descriptions", [])
        if not isinstance(option_descriptions, list):
            option_descriptions = []
        button_fields = ""
        for i in range(1, 11):
            value = labels[i-1] if len(labels) >= i else ""
            option_description = option_descriptions[i-1] if len(option_descriptions) >= i else ""
            button_fields += f'<label>Button {i} ' + ('(erforderlich)' if i == 1 else '(optional)') + f'</label><input type="text" name="button{i}" maxlength="80" value="{html.escape(str(value), quote=True)}" placeholder="z. B. Allgemeiner Support">'
            button_fields += f'<div class="ticket-option-description" hidden><label>Dropdown-Description für Anfrage {i} (optional)</label><input type="text" name="button_description{i}" maxlength="100" value="{html.escape(str(option_description), quote=True)}" placeholder="Kurze Erklärung für diese Anfrageart"></div>'
        body += (
            '<section class="card"><span class="pill">PANEL-ID ' + html.escape(str(panel_key)) + '</span>'
            '<form method="post" enctype="multipart/form-data">' + _csrf_field() + '<input type="hidden" name="panel_id" value="' + html.escape(str(panel_key), quote=True) + '">'
            '<label>Titel</label><input type="text" name="title" maxlength="256" required value="' + html.escape(str(panel.get("title", "Support-Ticket")), quote=True) + '">'
            '<label>Description</label><textarea name="description" maxlength="4000">' + html.escape(str(panel.get("description", ""))) + '</textarea>'
            + button_fields
            + '<label>Darstellung (Premium)</label><select name="display_mode" class="ticket-display-mode"><option value="buttons"' + (' selected' if panel.get("display_mode", "buttons") == "buttons" else '') + '>Buttons (bis zu 10)</option><option value="select"' + (' selected' if panel.get("display_mode") == "select" else '') + '>Dropdown-Menü (bis zu 10 Optionen)</option></select>'
            + '<label>Panel-Kanal</label><select name="channel_id" required>' + options(channels, panel.get("channel_id"), "— Textkanal auswählen —") + '</select>'
            + '<label>Ticket-Kategorie (optional)</label><select name="category_id">' + options(categories, panel.get("category_id"), "— Standard-Kategorie verwenden —") + '</select>'
            + '<label>Support role (optional)</label><select name="support_role_id">' + options(roles, panel.get("support_role_id"), "— Standard-Support role verwenden —") + '</select>'
            + '<script>(function(){const form=document.currentScript.closest("form");if(!form)return;const mode=form.querySelector(".ticket-display-mode");const fields=form.querySelectorAll(".ticket-option-description");function sync(){fields.forEach(el=>{el.hidden=!mode||mode.value!=="select";});}if(mode){mode.addEventListener("change",sync);sync();}})();</script>'
            + ('<h3>✨ Custom Branding</h3><label>Embed-Farbe</label><div style="display:flex;gap:12px;align-items:center"><input type="color" name="brand_color_picker" value="#' + html.escape(str(panel.get("brand_color", "5865F2")).lstrip("#"), quote=True) + '" oninput="this.form.brand_color.value=this.value.slice(1)" style="width:64px;height:48px;padding:3px"><input name="brand_color" maxlength="6" pattern="[0-9a-fA-F]{6}" value="' + html.escape(str(panel.get("brand_color", "5865F2")).lstrip("#"), quote=True) + '" oninput="this.form.brand_color_picker.value=\'#\'+this.value" placeholder="5865F2"></div><label>Logo vom Computer hochladen (PNG, JPG, GIF, WEBP; max. 5 MB)</label><input type="file" name="brand_logo_file" accept="image/png,image/jpeg,image/gif,image/webp"><small>' + ('Aktuelles Logo: ' + html.escape(str(panel.get("brand_logo_filename", "hochgeladen")), quote=True) if panel.get("brand_logo_file") else 'Noch kein hochgeladenes Logo.') + '</small><label>Alternativ Logo-URL (HTTPS, optional)</label><input name="brand_logo_url" type="url" maxlength="500" value="' + html.escape(str(panel.get("brand_logo_url", "")), quote=True) + '"><div class="check"><input type="checkbox" name="remove_brand_logo" id="remove-logo-' + html.escape(str(panel_key), quote=True) + '"><label for="remove-logo-' + html.escape(str(panel_key), quote=True) + '">Hochgeladenes Logo entfernen</label></div>' if guild_has_feature(cfg, "branding") else '<h3>🔒 Premium-Funktion</h3><p class="muted">Custom Branding ist gesperrt. Verknüpfe Premium mit diesem Server, um Farbe und Logo bearbeiten zu können.</p><fieldset disabled style="opacity:.55"><label>Embed-Farbe</label><input value="5865F2"><label>Logo-URL</label><input value="Premium erforderlich"></fieldset>')
            + '<button type="submit">Panel speichern und veröffentlichen</button></form><form method="post" onsubmit="return confirm(\'Dieses Ticket-Panel und seine Nachricht wirklich löschen?\')">' + _csrf_field() + '<input type="hidden" name="panel_id" value="' + html.escape(str(panel_key), quote=True) + '"><input type="hidden" name="action" value="delete_panel"><button type="submit" class="secondary">🗑️ Panel und Nachricht löschen</button></form></section>'
        )
    body += f'<p><a class="button secondary" href="/guild/{gid}">← Zur Serververwaltung</a></p>'
    return _render(guild.name + " · Ticket-System", body)


@app.route("/guild/<guild_id>/applications", methods=["GET", "POST"])
def guild_applications(guild_id):
    guild, response = _authorized_guild(guild_id)
    if response:
        return response
    gid = str(guild.id)
    actor = guild.get_member(int(session.get("user_id", "0")))
    if request.method == "POST":
        if not _csrf_ok():
            abort(400, "CSRF-Prüfung fehlgeschlagen. Bitte Seite neu laden.")
        if actor is None or not (actor.guild_permissions.administrator or actor.guild_permissions.manage_guild):
            abort(403, "Du brauchst Administrator- oder Server-verwalten-Rechte.")
        def selected_id(field, allowed_ids, label, optional=False):
            value = request.form.get(field, "").strip()
            if optional and not value:
                return None
            if not value.isdigit() or int(value) not in allowed_ids:
                abort(400, label + ": Bitte eine gültige Auswahl treffen.")
            return int(value)
        channel_ids = {channel.id for channel in guild.text_channels}
        role_ids = {role.id for role in guild.roles if not role.is_default() and not role.managed}
        panel_channel_id = selected_id("panel_channel_id", channel_ids, "Panel-Kanal")
        panel_name = request.form.get("panel_name", "").strip()[:80] or request.form.get("panel_title", "").strip()[:80] or "Bewerbung"
        button_label = request.form.get("button_label", "").strip()[:80] or ("Application start" if guild.id == 1561027077984227371 else "Bewerben")
        settings_for_limit = {}
        try:
            saved_config_for_limit = json.loads(data_path("applications_config.json").read_text(encoding="utf-8"))
            settings_for_limit = saved_config_for_limit.get("guilds", {}).get(gid, {})
        except (OSError, ValueError, AttributeError):
            pass
        existing_panels = settings_for_limit.get("panels", {}) if isinstance(settings_for_limit, dict) else {}
        legacy_exists = not existing_panels and any(key in settings_for_limit for key in ("panel_channel_id", "review_channel_id", "questions"))
        panel_count = len(existing_panels) if existing_panels else (1 if legacy_exists else 0)
        premium_enabled = guild_has_feature(_guild_config(guild.id), "applications")
        review_channel_id = selected_id("review_channel_id", channel_ids, "Prüfkanal")
        log_channel_id = selected_id("log_channel_id", channel_ids, "Protokollkanal")
        review_role_id = selected_id("review_role_id", role_ids, "Prüfrolle", optional=True)
        ping_role_id = selected_id("ping_role_id", role_ids, "Ping-Rolle", optional=True)
        title = request.form.get("panel_title", "").strip()
        description = request.form.get("panel_description", "").strip()
        questions = [line.strip() for line in request.form.get("questions", "").splitlines() if line.strip()]
        if not title or len(title) > 256 or len(description) > 4000:
            abort(400, "Bitte gib einen Titel ein (max. 256 Zeichen) und halte die Description unter 4000 Zeichen.")
        if not questions or len(questions) > 10 or any(len(question) > 200 for question in questions):
            abort(400, "Bitte gib 1 bis 10 Fragen ein; jede Frage darf maximal 200 Zeichen lang sein.")
        cog = _bot.get_cog("Applications") if _bot else None
        if cog is None or not hasattr(cog, "dashboard_create_panel"):
            abort(503, "Das Bewerbungssystem ist gerade nicht verfügbar. Bitte prüfe die Railway-Logs.")
        panel_channel = guild.get_channel(panel_channel_id)
        bot_member = guild.me
        if panel_channel is not None and bot_member is not None:
            channel_permissions = panel_channel.permissions_for(bot_member)
            missing_permissions = []
            if not channel_permissions.send_messages:
                missing_permissions.append("Nachrichten senden")
            if not channel_permissions.embed_links:
                missing_permissions.append("Links einbetten")
            if missing_permissions:
                return _render(
                    "Bewerbungs-Panel nicht veröffentlicht",
                    '<section class="card"><h1>Discord-Berechtigungen fehlen</h1><p>Gib dem Bot im ausgewählten Panel-Kanal diese Berechtigungen: '
                    + html.escape(", ".join(missing_permissions))
                    + '.</p><a class="button secondary" href="/guild/' + gid + '/applications">Zurück</a></section>',
                    403,
                )
        try:
            create_panel = cog.dashboard_create_panel
            # Keep the dashboard compatible with an older Applications cog during
            # rolling/stale deployments: pass only optional arguments supported by
            # the method version currently loaded in the bot process.
            supported_parameters = inspect.signature(create_panel).parameters
            panel_kwargs = {
                name: value
                for name, value in (
                    ("review_role_id", review_role_id),
                    ("ping_role_id", ping_role_id),
                    ("panel_name", panel_name),
                    ("button_label", button_label),
                )
                if name in supported_parameters
            }
            panel_coro = create_panel(
                guild.id,
                panel_channel_id,
                review_channel_id,
                log_channel_id,
                title,
                description,
                questions,
                **panel_kwargs,
            )
            future = asyncio.run_coroutine_threadsafe(panel_coro, _bot.loop)
            result_message = future.result(timeout=60)
        except Exception as exc:
            detail = "{}: {}".format(type(exc).__name__, str(exc)[:500])
            print("[DASHBOARD APPLICATIONS] Panel creation failed: {}".format(detail), flush=True)
            return _render(
                "Bewerbungs-Panel fehlgeschlagen",
                '<section class="card"><h1>Panel konnte nicht veröffentlicht werden</h1><p>Technischer Fehler: <code>'
                + html.escape(detail)
                + '</code></p><p>Prüfe die Railway-Logs nach <code>[DASHBOARD APPLICATIONS] Panel creation failed</code>. '
                'Wenn der Fehler von Discord kommt, kontrolliere die Berechtigungen in Panel-, Prüf- und Protokollkanal.</p>'
                '<a class="button secondary" href="/guild/' + gid + '/applications">Zurück zu Bewerbungen</a></section>',
                502,
            )
        _audit_dashboard_change(guild.id, "Bewerbungs-Panel", session.get("user_id"), "Panel in Kanal {} mit {} Fragen veröffentlicht".format(panel_channel_id, len(questions)))
        return redirect(url_for("guild_applications", guild_id=gid, saved="1"))

    settings = {}
    try:
        saved_config = json.loads(data_path("applications_config.json").read_text(encoding="utf-8"))
        settings = saved_config.get("guilds", {}).get(gid, {})
    except (OSError, ValueError, AttributeError):
        pass
    def channel_options(field):
        current = str(settings.get(field) or "")
        result = '<option value="">— Kanal auswählen —</option>'
        for channel in sorted(guild.text_channels, key=lambda item: item.name.lower()):
            selected = " selected" if str(channel.id) == current else ""
            result += '<option value="{}"{}>#{}</option>'.format(channel.id, selected, html.escape(channel.name))
        return result
    def role_options(field):
        current = str(settings.get(field) or "")
        result = '<option value="">— Keine —</option>'
        for role in sorted(_guild_roles(guild), key=lambda item: item[1].lower()):
            selected = " selected" if str(role[0]) == current else ""
            result += '<option value="{}"{}>{}</option>'.format(role[0], selected, html.escape(role[1]))
        return result
    questions = settings.get("questions") or ["Wie lautet dein RP-Name?", "Wie lautet dein Roblox-Name?", "Wie alt bist du (OOC)?", "Welche Erfahrung bringst du mit?", "Warum möchtest du beitreten?"]
    panels = settings.get("panels", {}) if isinstance(settings, dict) else {}
    legacy_exists = not panels and any(key in settings for key in ("panel_channel_id", "review_channel_id", "questions"))
    panel_count = len(panels) if panels else (1 if legacy_exists else 0)
    premium_enabled = guild_has_feature(_guild_config(guild.id), "applications")
    body = '<span class="pill">IRONWOLF · BEWERBUNGEN</span><h1 style="margin-top:12px">📝 Bewerbungen verwalten</h1><p class="muted">Erstelle unbegrenzt viele getrennte Bewerbungs-Panels mit eigenen Fragen und Kanälen.</p><div class="notice">Enablede Panels: {} · {}</div>'.format(panel_count, "Premium" if premium_enabled else "Free")
    body += '<p style="margin:16px 0"><a class="button" href="#application-create-form" style="display:inline-flex;align-items:center;gap:8px;font-size:16px">＋ Weitere Bewerbung hinzufügen</a></p>'
    if request.args.get("saved") == "1":
        body += '<div class="notice">✅ Bewerbungs-Panel wurde veröffentlicht und die Einstellungen gespeichert.</div>'
    body += '<form id="application-create-form" method="post">' + _csrf_field() + '<div class="grid"><section class="card"><h2>Panel gestalten</h2><label for="panel_name">Interner Panel-Name</label><input id="panel_name" name="panel_name" maxlength="80" required value="Bewerbung"><label for="panel_title">Titel</label><input id="panel_title" name="panel_title" maxlength="256" required value="' + html.escape(str(settings.get("panel_title") or "🐺 IronWolf – Bewerbungen"), quote=True) + '"><label for="button_label">Button-Name</label><input id="button_label" name="button_label" maxlength="80" required value="Bewerben"><label for="panel_description">Description</label><textarea id="panel_description" name="panel_description" rows="5" maxlength="4000">' + html.escape(str(settings.get("panel_description") or "Du möchtest dich bewerben? Klicke auf den Button. Ich stelle dir die Fragen anschließend einzeln per DM.")) + '</textarea><label for="questions">Bewerbungsfragen (eine pro Zeile, maximal 10)</label><textarea id="questions" name="questions" rows="8" required>' + html.escape("\n".join(str(q) for q in questions)) + '</textarea></section><section class="card"><h2>Discord-Kanäle</h2><label for="panel_channel_id">Öffentlicher Panel-Kanal</label><select id="panel_channel_id" name="panel_channel_id" required>' + channel_options("panel_channel_id") + '</select><label for="review_channel_id">Privater Bewerbungs-Prüfkanal</label><select id="review_channel_id" name="review_channel_id" required>' + channel_options("review_channel_id") + '</select><label for="log_channel_id">Protokoll-Kanal</label><select id="log_channel_id" name="log_channel_id" required>' + channel_options("log_channel_id") + '</select><label for="review_role_id">Rolle, die Bewerbungen bearbeiten darf (optional)</label><select id="review_role_id" name="review_role_id">' + role_options("review_role_id") + '</select><label for="ping_role_id">Rolle bei neuen Bewerbungen anpingen (optional)</label><select id="ping_role_id" name="ping_role_id">' + role_options("ping_role_id") + '</select><p class="muted">Mit Administrator- oder Server-verwalten-Rechten kann man Bewerbungen ebenfalls bearbeiten.</p></section></div><button type="submit">💾 Speichern und Bewerbungs-Panel veröffentlichen</button> <a class="button secondary" href="/guild/' + gid + '">Zur Serverübersicht</a></form>'
    return _render(guild.name + " · Bewerbungen", body)


@app.route("/guild/<guild_id>/commands", methods=["GET", "POST"])
def guild_commands(guild_id):
    guild, response = _authorized_guild(guild_id)
    if response:
        return response
    gid = str(guild.id)
    cfg = _guild_config(guild.id)
    if request.method == "POST":
        if not _csrf_ok():
            abort(400, "CSRF-Prüfung fehlgeschlagen. Bitte Seite neu laden.")
        if request.form.get("action") != "commands":
            abort(400, "Unbekannte Aktion.")
        disabled = set(cfg.get("disabled_commands", []))
        valid_keys = {item["key"] for item in _command_records(guild.id)}
        for key in valid_keys:
            enabled = request.form.get("cmd:" + key) == "on"
            aliases = {key}
            if key.startswith("prefix:!"):
                aliases.add("prefix:." + key[len("prefix:!"):])
            for alias_key in aliases:
                if enabled:
                    disabled.discard(alias_key)
                else:
                    disabled.add(alias_key)
        cfg["disabled_commands"] = sorted(disabled)
        _save_json(data_path("config.json"), _config)
        _audit_dashboard_change(guild.id, "Befehlsrechte", session.get("user_id"), "Disablede Befehle: " + ", ".join(sorted(disabled))[:350])
        return redirect(url_for("guild_commands", guild_id=gid))
    commands = _command_records(guild.id)
    groups = {}
    for item in commands:
        groups.setdefault(item["cog"], []).append(item)
    command_html = '<span class="pill">IRONWOLF · COMMAND CENTER</span><h1 style="margin-top:12px">⌘ Befehle</h1><p class="muted">Bei neu hinzugefügten Servern sind alle Befehle standardmäßig deaktiviert. Aktiviere hier nur die Prefix- und Slash-Befehle, die auf diesem Server verfügbar sein sollen. Bereits bestehende Server behalten ihre bisherigen Einstellungen.</p><div class="section-nav"><a href="/guild/' + gid + '">← Serverübersicht</a><a href="#prefix">Prefix-Befehle</a><a href="#slash">Slash-Befehle</a></div>'
    command_html += '<div class="notice">🔎 Suche nach Befehlsnamen oder Modul. Die Liste wird sofort gefiltert; Änderungen anschließend unten speichern.</div>'
    command_html += '<label for="command-search">Befehle durchsuchen</label><input id="command-search" type="search" placeholder="z. B. !filmserver, /ticket, Moderation …" autocomplete="off" style="width:100%;box-sizing:border-box;margin:0 0 16px;padding:12px;border-radius:10px;border:1px solid var(--border);background:var(--panel);color:var(--text)">'
    command_html += '<p id="command-search-count" class="muted" aria-live="polite"></p>'
    command_html += '<form method="post">' + _csrf_field() + '<input type="hidden" name="action" value="commands"><div class="command-groups" id="command-groups">'
    for group_name, items in sorted(groups.items(), key=lambda pair: pair[0].lower()):
        command_html += '<section class="command-group" data-command-group="' + html.escape(group_name.lower(), quote=True) + '"><h3>' + html.escape(group_name) + '</h3>'
        for item in items:
            checked = " checked" if item["enabled"] else ""
            command_name = item["name"]
            premium_marker = ""
            if item["cog"] in {"SecurityTools", "EventTools"}:
                premium_marker = ' <span class="pill">PREMIUM+</span>'
            elif item["cog"] in {"TicketTools", "TicketSystem"}:
                premium_marker = ' <span class="pill">PREMIUM</span>'
            command_html += f'<div class="row command-entry" data-command-search="{html.escape((command_name + " " + item["kind"] + " " + item["cog"]).casefold(), quote=True)}"><div><strong>{html.escape(command_name)}</strong>{premium_marker}<br><small>{html.escape(item["kind"])}</small></div><div class="check"><input type="checkbox" id="cmd-{html.escape(item["key"])}" name="cmd:{html.escape(item["key"])}"{checked}><label for="cmd-{html.escape(item["key"])}">An</label></div></div>'
        command_html += '</section>'
    command_html += '</div><button type="submit">Save changes</button> <a class="button secondary" href="/guild/' + gid + '">Zurück zur Übersicht</a></form>'
    command_html += """<script>
(function(){
  const input=document.getElementById('command-search');
  const groups=document.querySelectorAll('#command-groups .command-group');
  const count=document.getElementById('command-search-count');
  if(!input)return;
  function filterCommands(){
    const query=input.value.trim().toLocaleLowerCase();
    let visible=0;
    groups.forEach(function(group){
      let groupVisible=0;
      group.querySelectorAll('.command-entry').forEach(function(row){
        const matches=!query || (row.dataset.commandSearch+' '+(group.dataset.commandGroup||'')).toLocaleLowerCase().includes(query);
        row.hidden=!matches;
        if(matches){groupVisible++;visible++;}
      });
      group.hidden=groupVisible===0;
    });
    count.textContent=query ? visible+' passende Befehle gefunden.' : visible+' Befehle insgesamt.';
  }
  input.addEventListener('input',filterCommands);
  filterCommands();
})();
</script>"""
    return _render(guild.name + " · Befehle", command_html)


@app.route("/guild/<guild_id>/actions", methods=["GET", "POST"])
def guild_actions(guild_id):
    guild, response = _authorized_guild(guild_id)
    if response:
        return response
    gid = str(guild.id)
    result_html = ""
    if request.method == "POST":
        if not _csrf_ok():
            abort(400, "CSRF-Prüfung fehlgeschlagen. Bitte Seite neu laden.")
        # Check the actual Discord member permissions as well as OAuth guild access.
        actor = guild.get_member(int(session.get("user_id", "0")))
        if actor is None or not (actor.guild_permissions.administrator or actor.guild_permissions.manage_guild):
            abort(403, "Du brauchst auf diesem Server Administrator- oder Server-verwalten-Rechte.")
        action = request.form.get("action", "").strip()
        channel_id = request.form.get("channel_id", "").strip()
        target_id = request.form.get("target_id", "").strip()
        reason = request.form.get("reason", "").strip()[:400]
        content = request.form.get("message", "").strip()
        confirm = request.form.get("confirm", "").strip()
        amount_raw = request.form.get("amount", "10")
        minutes_raw = request.form.get("minutes", "10")
        try:
            channel = guild.get_channel(int(channel_id)) if channel_id else None
            target = guild.get_member(int(target_id)) if target_id else None
        except ValueError:
            abort(400, "Ungültige Kanal- oder Mitglieder-ID.")
        async def execute_action():
            bot_member = guild.me
            if bot_member is None:
                raise PermissionError("Der Bot ist nicht als Mitglied auf diesem Server verfügbar.")
            if action == "announce":
                if not isinstance(channel, discord.TextChannel):
                    raise ValueError("Bitte wähle einen gültigen Textkanal.")
                if not channel.permissions_for(bot_member).send_messages:
                    raise PermissionError("Der Bot darf in diesem Kanal keine Nachrichten senden.")
                if not content or len(content) > 2000:
                    raise ValueError("Die Nachricht muss zwischen 1 und 2000 Zeichen lang sein.")
                if not channel.permissions_for(actor).send_messages:
                    raise PermissionError("Du darfst in diesem Kanal keine Nachrichten senden.")
                await channel.send(content, allowed_mentions=discord.AllowedMentions.none())
                return f"Nachricht wurde in #{channel.name} gesendet."
            if action == "purge":
                if not isinstance(channel, discord.TextChannel):
                    raise ValueError("Bitte wähle einen gültigen Textkanal.")
                if not actor.guild_permissions.manage_messages or not channel.permissions_for(bot_member).manage_messages:
                    raise PermissionError("Du und der Bot benötigen „Nachrichten verwalten“.")
                if confirm != "LOESCHEN":
                    raise ValueError("Zum Bestätigen musst du LOESCHEN in das Bestätigungsfeld schreiben.")
                try:
                    amount = int(amount_raw)
                except ValueError:
                    raise ValueError("Die Anzahl muss eine Zahl sein.")
                if not 1 <= amount <= 100:
                    raise ValueError("Du kannst zwischen 1 und 100 Nachrichten auf einmal löschen.")
                deleted = await channel.purge(limit=amount, reason=reason or f"Dashboard-Aktion durch {actor} ({actor.id})")
                return f"{len(deleted)} Nachrichten in #{channel.name} gelöscht."
            if action in {"timeout", "kick", "ban"}:
                if target is None:
                    raise ValueError("Mitglied nicht gefunden. Gib eine gültige Mitglieder-ID an.")
                if target.id == actor.id or target.id == guild.owner_id:
                    raise PermissionError("Du kannst dich selbst oder den Serverinhaber nicht moderieren.")
                if target.top_role >= actor.top_role and actor.id != guild.owner_id:
                    raise PermissionError("Deine höchste Rolle muss über der Zielrolle liegen.")
                if target.top_role >= bot_member.top_role:
                    raise PermissionError("Die höchste Rolle des Bots muss über der Zielrolle liegen.")
                audit_reason = reason or f"Dashboard-Aktion durch {actor} ({actor.id})"
                if action == "timeout":
                    if not actor.guild_permissions.moderate_members or not bot_member.guild_permissions.moderate_members:
                        raise PermissionError("Du und der Bot benötigen „Mitglieder moderieren“.")
                    try:
                        minutes = int(minutes_raw)
                    except ValueError:
                        raise ValueError("Die Dauer muss eine Zahl sein.")
                    if not 1 <= minutes <= 40320:
                        raise ValueError("Die Timeout-Dauer muss zwischen 1 Minute und 28 Tagen liegen.")
                    await target.timeout(discord.utils.utcnow() + __import__("datetime").timedelta(minutes=minutes), reason=audit_reason)
                    return f"{target} wurde für {minutes} Minuten timeoutet."
                if confirm != ("KICK" if action == "kick" else "BAN"):
                    raise ValueError(f"Zur Bestätigung musst du {('KICK' if action == 'kick' else 'BAN')} eingeben.")
                if action == "kick":
                    if not actor.guild_permissions.kick_members or not bot_member.guild_permissions.kick_members:
                        raise PermissionError("Du und der Bot benötigen „Mitglieder kicken“.")
                    await target.kick(reason=audit_reason)
                    return f"{target} wurde vom Server gekickt."
                if not actor.guild_permissions.ban_members or not bot_member.guild_permissions.ban_members:
                    raise PermissionError("Du und der Bot benötigen „Mitglieder bannen“.")
                await target.ban(reason=audit_reason, delete_message_seconds=0)
                return f"{target} wurde vom Server gebannt."
            raise ValueError("Unbekannte Aktion.")
        try:
            future = asyncio.run_coroutine_threadsafe(execute_action(), _bot.loop)
            result = future.result(timeout=25)
            result_html = '<div class="notice">✅ ' + html.escape(str(result)) + '</div>'
        except Exception as exc:
            detail = str(exc) or type(exc).__name__
            result_html = '<div class="notice">⚠️ ' + html.escape(detail[:500]) + '</div>'
    channels = _guild_channels(guild)
    channel_options = '<option value="">— Textkanal auswählen —</option>' + ''.join(
        f'<option value="{cid}">{html.escape(name)}</option>' for cid, name in channels
    )
    csrf = _csrf_field()
    base = f'<input type="hidden" name="guild_id" value="{gid}">{csrf}'
    announcement = (
        '<section class="card"><h2>📣 Nachricht senden</h2><p>Sendet eine Nachricht ohne automatische @everyone- oder Rollen-Erwähnungen.</p>'
        f'<form method="post">{base}<input type="hidden" name="action" value="announce">'
        f'<label>Kanal</label><select name="channel_id" required>{channel_options}</select>'
        '<label>Nachricht (max. 2000 Zeichen)</label><textarea name="message" maxlength="2000" required></textarea>'
        '<button type="submit">Nachricht senden</button></form></section>'
    )
    moderation = (
        '<section class="card"><h2>🛡️ Mitglieder moderieren</h2><p>Mitglieder-ID verwenden. Kick und Ban verlangen eine zusätzliche Bestätigung.</p>'
        f'<form method="post">{base}<input type="hidden" name="action" value="timeout">'
        '<label>Mitglieder-ID für Timeout</label><input type="text" name="target_id" inputmode="numeric" required>'
        '<label>Dauer in Minuten (1–40320)</label><input type="text" name="minutes" value="10" inputmode="numeric" required>'
        '<label>Grund (optional)</label><input type="text" name="reason" maxlength="400">'
        '<button type="submit">Timeout setzen</button></form>'
        f'<form method="post">{base}<input type="hidden" name="action" value="kick">'
        '<label>Mitglieder-ID für Kick</label><input type="text" name="target_id" inputmode="numeric" required>'
        '<label>Grund (optional)</label><input type="text" name="reason" maxlength="400">'
        '<label>Zur Bestätigung KICK eingeben</label><input type="text" name="confirm" required>'
        '<button type="submit" class="secondary">Mitglied kicken</button></form>'
        f'<form method="post">{base}<input type="hidden" name="action" value="ban">'
        '<label>Mitglieder-ID für Ban</label><input type="text" name="target_id" inputmode="numeric" required>'
        '<label>Grund (optional)</label><input type="text" name="reason" maxlength="400">'
        '<label>Zur Bestätigung BAN eingeben</label><input type="text" name="confirm" required>'
        '<button type="submit" class="secondary">Mitglied bannen</button></form></section>'
    )
    cleanup = (
        '<section class="card"><h2>🧹 Nachrichten löschen</h2><p>Unwiderruflich; es können 1–100 aktuelle Nachrichten entfernt werden.</p>'
        f'<form method="post">{base}<input type="hidden" name="action" value="purge">'
        f'<label>Kanal</label><select name="channel_id" required>{channel_options}</select>'
        '<label>Anzahl (1–100)</label><input type="text" name="amount" value="10" inputmode="numeric" required>'
        '<label>Grund (optional)</label><input type="text" name="reason" maxlength="400">'
        '<label>Zur Bestätigung LOESCHEN eingeben</label><input type="text" name="confirm" required>'
        '<button type="submit" class="secondary">Nachrichten löschen</button></form></section>'
    )
    body = (
        f'<h1>⚡ Aktionszentrale · {html.escape(guild.name)}</h1>'
        '<p class="muted">Direkte Discord-Aktionen. Berechtigungen werden beim Absenden erneut geprüft.</p>'
        + result_html + '<div class="grid"><div>' + announcement + cleanup + '</div><div>' + moderation + '</div></div>'
        + f'<p><a class="button secondary" href="/guild/{gid}">← Zur Serververwaltung</a></p>'
    )
    return _render(guild.name + " · Aktionszentrale", body)


@app.route("/create-module", methods=["POST"])
def create_module():

    login = _require_login()
    if login:
        return login
    if not _csrf_ok():
        abort(400, "CSRF-Prüfung fehlgeschlagen.")
    guild, response = _authorized_guild(request.form.get("guild_id", ""))
    if response:
        return response
    name = request.form.get("module_name", "").strip()
    description = request.form.get("description", "").strip()
    command = request.form.get("command_name", "").strip()
    import re
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,39}", name):
        abort(400, "Der Module name darf nur Buchstaben, Zahlen und Unterstriche enthalten und muss mit einem Buchstaben beginnen.")
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", command):
        abort(400, "Der Befehlsname muss klein geschrieben sein und darf Buchstaben, Zahlen und Bindestriche enthalten.")
    description = description[:120].replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
    code = f'''"""IronWolf-Modul: {description or name}

Erstellt über das IronWolf Control Center. Prüfe und passe die Datei an,
bevor du sie als Extension lädst.
"""
import discord
from discord import app_commands
from discord.ext import commands


class {name}(commands.Cog):
    """{description or name}"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="{command}", description="{(description or name).replace(chr(34), chr(39))[:100]}")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    async def example(self, interaction: discord.Interaction):
        """Beispiel-Slash-Befehl für dein neues Modul."""
        await interaction.response.send_message(
            "Dein neues IronWolf-Modul funktioniert. Passe diese Antwort an!",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog({name}(bot))
'''
    filename = MODULE_OUTPUT_DIR / f"{name}.py"
    filename.write_text(code, encoding="utf-8")
    response = make_response(code)
    response.headers["Content-Type"] = "text/x-python; charset=utf-8"
    response.headers["Content-Disposition"] = f'attachment; filename="{name}.py"'
    return response


def start_dashboard(bot, config, save_json, guild_config):
    """Start the Flask dashboard once in a background thread."""
    global _bot, _config, _save_json, _guild_config
    if getattr(bot, "_dashboard_started", False):
        return
    _bot, _config, _save_json, _guild_config = bot, config, save_json, guild_config
    try:
        import voice_support_dashboard
        voice_support_dashboard.register()
    except Exception as exc:
        print(f"[DASHBOARD] Voice support settings route could not be registered: {type(exc).__name__}: {exc}", flush=True)
    app.secret_key = _env("DASHBOARD_SECRET_KEY") or app.secret_key
    if not _env("DASHBOARD_SECRET_KEY"):
        print("[DASHBOARD] WARNING: DASHBOARD_SECRET_KEY is not set; sessions will reset on restart.", flush=True)
    port = int(os.getenv("PORT", "8080"))
    def run_dashboard():
        try:
            from waitress import serve
            serve(app, host="0.0.0.0", port=port, threads=8)
        except Exception as exc:
            print(f"[DASHBOARD] Production server failed: {type(exc).__name__}: {exc}", flush=True)

    thread = threading.Thread(
        target=run_dashboard,
        name="ironwolf-dashboard",
        daemon=True,
    )
    thread.start()
    bot._dashboard_started = True
    print(f"[DASHBOARD] Listening on 0.0.0.0:{port}", flush=True)


@app.route("/guild/<guild_id>/wolfpulse", methods=["GET"])
def guild_wolfpulse(guild_id):
    """Read-only server readiness report for the IronWolf Control Center."""
    guild, response = _authorized_guild(guild_id)
    if response:
        return response
    cfg = _guild_config(guild.id)
    me = guild.me
    checks = [
        ("Welcome channel", cfg.get("welcome_channel"), "welcome_channel"),
        ("Verifizierungskanal", cfg.get("verify_channel"), "verify_channel"),
        ("Moderations-Logkanal", cfg.get("log_channel_id"), "log_channel_id"),
        ("Security log channel", cfg.get("security_log_channel"), "security_log_channel"),
    ]
    rows = []
    ready = 0
    for label, raw_id, key in checks:
        channel = guild.get_channel(int(raw_id)) if str(raw_id or "").isdigit() else None
        if channel is not None:
            ready += 1
            status = '<span class="pill">Konfiguriert</span>'
            detail = html.escape(getattr(channel, "mention", channel.name))
        else:
            status = '<span class="pill">Offen</span>'
            detail = "Noch nicht eingerichtet"
        rows.append(
            '<div class="card" style="margin:12px 0"><strong>' + html.escape(label) +
            '</strong><div class="muted" style="margin-top:8px">' + detail +
            '</div><div style="margin-top:10px">' + status + '</div></div>'
        )
    permissions = {
        "Nachrichten senden": bool(me and me.guild_permissions.send_messages),
        "Nachrichtenverlauf lesen": bool(me and me.guild_permissions.read_message_history),
        "Kanäle verwalten": bool(me and me.guild_permissions.manage_channels),
        "Rollen verwalten": bool(me and me.guild_permissions.manage_roles),
        "Mitglieder moderieren": bool(me and me.guild_permissions.moderate_members),
    }
    permission_rows = []
    for label, enabled in permissions.items():
        pill = '<span class="pill">' + ("OK" if enabled else "Prüfen") + '</span>'
        permission_rows.append('<div class="card" style="margin:12px 0"><strong>' +
            html.escape(label) + '</strong><div style="margin-top:10px">' + pill + '</div></div>')
    missing = len(checks) - ready
    body = (
        '<section class="card"><h1>🐺 WolfPulse · Server-Check</h1>'
        '<p class="muted">Live-Prüfung der wichtigsten IronWolf-Einstellungen und Bot-Rechte. '
        'Diese Seite verändert keine Serverkonfiguration.</p>'
        '<div class="grid">'
        '<div class="card"><div class="muted">Mitglieder</div><h2>' + str(guild.member_count or len(guild.members)) + '</h2></div>'
        '<div class="card"><div class="muted">Text- und Voice-Kanäle</div><h2>' + str(len(guild.channels)) + '</h2></div>'
        '<div class="card"><div class="muted">Rollen</div><h2>' + str(len(guild.roles)) + '</h2></div>'
        '<div class="card"><div class="muted">Konfigurationschecks</div><h2>' + str(ready) + '/' + str(len(checks)) + '</h2></div>'
        '</div></section><section class="card"><h2>Konfiguration</h2>' + ''.join(rows) +
        '</section><section class="card"><h2>Bot-Berechtigungen</h2>' + ''.join(permission_rows) +
        '</section><section class="card"><h2>Empfohlene nächste Schritte</h2><p>' +
        (str(missing) + ' der vier geprüften Kanäle fehlen oder zeigen auf einen nicht verfügbaren Kanal. '
         'Richte sie im Control Center ein und prüfe anschließend erneut.' if missing else
         'Alle vier geprüften Kanäle sind eingerichtet. Prüfe zusätzlich die Rechte im jeweiligen Kanal.') +
        '</p><p><a href="/guild/' + str(guild.id) + '">Zur Server-Konfiguration ↗</a></p></section>'
    )
    return _page("WolfPulse", body)


@app.route("/guild/<guild_id>/wolfshield", methods=["GET", "POST"])
def guild_wolfshield(guild_id):
    """Configure the server's anti-raid detection and response safely."""
    guild, response = _authorized_guild(guild_id)
    if response:
        return response
    gid = str(guild.id)
    cfg = _guild_config(guild.id)

    if request.method == "POST":
        if not _csrf_ok():
            abort(400, "CSRF-Prüfung fehlgeschlagen. Bitte Seite neu laden.")
        import copy
        original_cfg = copy.deepcopy(cfg)
        try:
            cfg["anti_raid_enabled"] = request.form.get("anti_raid_enabled") == "on"
            numeric = {
                "raid_threshold": ("raid_threshold", 3, 30, 8),
                "raid_window_seconds": ("raid_window_seconds", 5, 60, 10),
                "raid_lockdown_seconds": ("raid_lockdown_seconds", 15, 600, 60),
                "raid_timeout_minutes": ("raid_timeout_minutes", 1, 60, 10),
                "raid_min_account_age_days": ("raid_min_account_age_days", 0, 365, 0),
            }
            for key, (label, minimum, maximum, default) in numeric.items():
                try:
                    value = int(request.form.get(key, str(cfg.get(key, default))).strip())
                except (TypeError, ValueError):
                    abort(400, f"{label}: Bitte eine ganze Zahl eingeben.")
                if not minimum <= value <= maximum:
                    abort(400, f"{label} muss zwischen {minimum} und {maximum} liegen.")
                cfg[key] = value
            action = request.form.get("raid_action", "timeout").strip().lower()
            if action not in {"alert", "timeout"}:
                abort(400, "Ungültige Anti-Raid-Aktion.")
            cfg["raid_action"] = action
            _save_json(data_path("config.json"), _config)
            _audit_dashboard_change(
                guild.id, "WolfShield Anti-Raid", session.get("user_id"),
                f"enabled={cfg['anti_raid_enabled']}; threshold={cfg['raid_threshold']}; "
                f"window={cfg['raid_window_seconds']}s; lockdown={cfg['raid_lockdown_seconds']}s; "
                f"action={action}; timeout={cfg['raid_timeout_minutes']}m; "
                f"min_account_age={cfg['raid_min_account_age_days']}d",
            )
        except Exception:
            cfg.clear()
            cfg.update(original_cfg)
            raise
        return redirect(url_for("guild_wolfshield", guild_id=gid, saved="1"))

    enabled = bool(cfg.get("anti_raid_enabled", False))
    action = str(cfg.get("raid_action", "timeout")).lower()
    action_options = (
        '<option value="alert"' + (' selected' if action == "alert" else '') + '>Nur alarmieren (keine automatische Moderation)</option>'
        '<option value="timeout"' + (' selected' if action == "timeout" else '') + '>Neue Beitritte während der Schutzphase timeouten</option>'
    )
    saved_notice = '<div class="card"><strong>✅ Einstellungen gespeichert.</strong> Bot und Website verwenden dieselbe Serverkonfiguration.</div>' if request.args.get("saved") == "1" else ""
    checked = " checked" if enabled else ""
    body = (
        '<section class="card"><h1>🛡️ WolfShield · Anti-Raid</h1>'
        '<p class="muted">Konfiguriere die Beitritts-Erkennung und die Reaktion des verbundenen Discord-Bots. '
        'Änderungen werden serverbezogen gespeichert und im Dashboard-Audit protokolliert.</p>'
        + saved_notice +
        '<form method="post">' + _csrf_field() +
        '<label class="setting-row"><span><strong>Anti-Raid aktivieren</strong><small>Erkennt ungewöhnlich viele neue Mitglieder innerhalb eines Zeitfensters.</small></span>'
        '<input type="checkbox" name="anti_raid_enabled" value="on"' + checked + '></label>'
        '<div class="grid">'
        '<label class="card">Beitritte bis Alarm <small>3–30 Mitglieder</small><input type="number" name="raid_threshold" min="3" max="30" required value="' + str(int(cfg.get("raid_threshold", 8) or 8)) + '"></label>'
        '<label class="card">Erkennungsfenster (Sekunden) <small>5–60 Sekunden</small><input type="number" name="raid_window_seconds" min="5" max="60" required value="' + str(int(cfg.get("raid_window_seconds", 10) or 10)) + '"></label>'
        '<label class="card">Schutzphase (Sekunden) <small>15–600 Sekunden</small><input type="number" name="raid_lockdown_seconds" min="15" max="600" required value="' + str(int(cfg.get("raid_lockdown_seconds", 60) or 60)) + '"></label>'
        '<label class="card">Timeout-Dauer (Minuten) <small>1–60 Minuten</small><input type="number" name="raid_timeout_minutes" min="1" max="60" required value="' + str(int(cfg.get("raid_timeout_minutes", 10) or 10)) + '"></label>'
        '<label class="card">Accountalter-Hinweis (Tage) <small>0 = nur protokollieren, keine Altersgrenze</small><input type="number" name="raid_min_account_age_days" min="0" max="365" required value="' + str(int(cfg.get("raid_min_account_age_days", 0) or 0)) + '"></label>'
        '<label class="card">Reaktion während der Schutzphase <small>Kann jederzeit angepasst werden</small><select name="raid_action">' + action_options + '</select></label>'
        '</div><p class="muted">Sicherheitshinweis: Das Accountalter ist nur ein Kontextsignal und löst allein keine Strafe aus. Im Modus „Timeout“ werden Nicht-Bot-Mitglieder, die während einer aktiven Schutzphase beitreten, für die eingestellte Dauer eingeschränkt. Benötigt „Mitglieder moderieren“ und eine passende Rollenposition.</p>'
        '<button type="submit">💾 WolfShield speichern</button></form></section>'
        '<section class="card"><h2>So funktioniert die Schutzphase</h2>'
        '<ol><li>IronWolf zählt Nicht-Bot-Beitritte im konfigurierten Zeitfenster.</li>'
        '<li>Wird die Schwelle erreicht, wird eine Schutzphase aktiviert und ein Sicherheitslog gesendet.</li>'
        '<li>Während der Schutzphase protokolliert der Bot Beitritte und führt – je nach gewählter Aktion – keine Moderation oder einen Timeout aus.</li>'
        '</ol><p><a href="/guild/' + gid + '/wolfpulse">WolfPulse · Berechtigungen prüfen ↗</a> · <a href="/guild/' + gid + '">Zur Server-Konfiguration ↗</a></p></section>'
    )
    return _page("WolfShield · Anti-Raid", body)


@app.route("/guild/<guild_id>/wolfchronicle", methods=["GET", "POST"])
def guild_wolfchronicle(guild_id):
    """Shared, OAuth-protected incident timeline for the bot and website."""
    guild, response = _authorized_guild(guild_id)
    if response:
        return response
    cfg = _guild_config(guild.id)

    if request.method == "POST":
        if not _csrf_ok():
            abort(400, "CSRF-Prüfung fehlgeschlagen. Bitte Seite neu laden.")
        action = request.form.get("action", "")
        if action == "create":
            try:
                incident = create_incident(
                    cfg,
                    incident_type=request.form.get("incident_type", "other"),
                    severity=request.form.get("severity", "medium"),
                    title=request.form.get("title", ""),
                    summary=request.form.get("summary", ""),
                    reporter_id=session.get("user_id", "dashboard"),
                    source="dashboard",
                )
            except ValueError as exc:
                abort(400, str(exc))
            _save_json(data_path("config.json"), _config)
            _audit_dashboard_change(guild.id, "wolfchronicle.create", session.get("user_id"), f"Vorfall {incident['id']} erstellt")
        elif action == "update":
            incident_id = request.form.get("incident_id", "")
            status = request.form.get("status", "")
            note = request.form.get("resolution_note", "")
            try:
                updated = update_incident(cfg, incident_id, status=status, resolution_note=note)
            except ValueError as exc:
                abort(400, str(exc))
            if updated is None:
                abort(404, "Dieser Vorfall wurde nicht gefunden.")
            _save_json(data_path("config.json"), _config)
            _audit_dashboard_change(guild.id, "wolfchronicle.update", session.get("user_id"), f"Vorfall {updated['id']} → {updated['status_label']}")
        else:
            abort(400, "Unbekannte Aktion.")
        return redirect(url_for("guild_wolfchronicle", guild_id=guild.id))

    incidents = list_incidents(cfg, limit=50)
    type_options = "".join(
        '<option value="' + key + '">' + html.escape(label) + '</option>'
        for key, label in INCIDENT_TYPES.items()
    )
    severity_options = "".join(
        '<option value="' + key + '"' + (' selected' if key == "medium" else '') + '>' +
        html.escape(label) + '</option>'
        for key, label in SEVERITIES.items()
    )
    status_options = "".join(
        '<option value="' + key + '">' + html.escape(label) + '</option>'
        for key, label in STATUSES.items()
    )
    cards = []
    for item in incidents:
        created = html.escape(str(item.get("created_at", "Unbekannt"))[:19].replace("T", " "))
        status = item.get("status", "open")
        item_status_options = status_options.replace(
            '<option value="' + status + '">',
            '<option value="' + status + '" selected>',
        )
        status_label = html.escape(str(item.get("status_label", STATUSES.get(status, status))))
        resolution_note = html.escape(str(item.get("resolution_note", "")))
        card = (
            '<section class="card"><div class="pill">' + html.escape(str(item.get("severity_label", "Mittel"))) +
            ' · ' + status_label + '</div><h2>' + html.escape(str(item.get("title", "Vorfall"))) + '</h2>'
            '<p class="muted">' + html.escape(str(item.get("type_label", "Sonstiger Vorfall"))) +
            ' · ID <code>' + html.escape(str(item.get("id", ""))) + '</code> · ' + created + ' UTC</p>'
            '<p>' + html.escape(str(item.get("summary", ""))) + '</p>'
        )
        if status != "resolved":
            card += (
                '<form method="post">' + _csrf_field() +
                '<input type="hidden" name="action" value="update">'
                '<input type="hidden" name="incident_id" value="' + html.escape(str(item.get("id", "")), quote=True) + '">'
                '<label>Status<select name="status">' + item_status_options + '</select></label>'
                '<label>Abschlussnotiz (optional, wird nur bei „Abgeschlossen“ gespeichert)'
                '<input type="text" name="resolution_note" maxlength="500" placeholder="z. B. Ursache behoben, Schutz geprüft"></label>'
                '<button type="submit">Status aktualisieren</button></form>'
            )
        elif resolution_note:
            card += '<p class="notice"><strong>Abschlussnotiz:</strong> ' + resolution_note + '</p>'
        card += '</section>'
        cards.append(card)
    timeline = "".join(cards) if cards else '<section class="card"><h2>Noch keine Vorfälle</h2><p>Erstelle hier einen Eintrag oder nutze im Discord Servers <code>/wolfincident</code>. Beide Wege schreiben in dieselbe serverbezogene Chronik.</p></section>'
    body = (
        '<section class="card"><h1>📚 WolfChronicle · Gemeinsames Incident-Center</h1>'
        '<p class="muted">Ein gemeinsamer Verlauf für Discord und Website. Vorfälle bleiben serverbezogen gespeichert, sind begrenzt auf 100 Einträge und können hier nachverfolgt werden. Es werden keine Nachrichten automatisch gesammelt und keine Strafen ausgeführt.</p>'
        '<form method="post">' + _csrf_field() +
        '<input type="hidden" name="action" value="create">'
        '<div class="grid"><label>Vorfalltyp<select name="incident_type">' + type_options + '</select></label>'
        '<label>Priorität<select name="severity">' + severity_options + '</select></label></div>'
        '<label>Titel<input type="text" name="title" maxlength="120" required placeholder="Kurzer, verständlicher Titel"></label>'
        '<label>Zusammenfassung<textarea name="summary" maxlength="500" required placeholder="Was ist passiert? Keine privaten Nachrichten oder unnötigen personenbezogenen Daten einfügen."></textarea></label>'
        '<button type="submit">＋ Vorfall dokumentieren</button></form></section>'
        '<section><h2>Letzte Vorfälle · ' + str(len(incidents)) + '</h2>' + timeline + '</section>'
        '<section class="card"><h2>Vom Discord aus starten</h2><p><code>/wolfincident</code> legt einen Eintrag an. <code>/wolfincidents</code> zeigt die letzten Vorfälle direkt im Server. Statusänderungen im Dashboard werden beim nächsten Aufruf auch vom Bot gelesen.</p>'
        '<p><a href="/guild/' + str(guild.id) + '/wolfmirror">WolfMirror öffnen ↗</a> · <a href="/guild/' + str(guild.id) + '/wolfpulse">WolfPulse öffnen ↗</a></p></section>'
    )
    return _page("WolfChronicle · Incident-Center", body)


@app.route("/guild/<guild_id>/wolfmirror", methods=["GET", "POST"])
def guild_wolfmirror(guild_id):
    """Run a safe, read-only security tabletop simulation for this guild."""
    guild, response = _authorized_guild(guild_id)
    if response:
        return response
    cfg = _guild_config(guild.id)
    scenario = request.form.get("scenario", "raid") if request.method == "POST" else request.args.get("scenario", "raid")
    if scenario not in SCENARIOS:
        abort(400, "Unbekanntes WolfMirror-Szenario.")
    if request.method == "POST" and not _csrf_ok():
        abort(400, "CSRF-Prüfung fehlgeschlagen. Bitte Seite neu laden.")

    me = guild.me
    permissions = me.guild_permissions if me else None
    report = simulate_scenario(
        cfg,
        {
            "moderate_members": bool(permissions and permissions.moderate_members),
            "manage_messages": bool(permissions and permissions.manage_messages),
            "view_audit_log": bool(permissions and permissions.view_audit_log),
        },
        scenario,
        {"member_count": guild.member_count or 0, "channel_count": len(guild.channels)},
    )
    controls_html = "".join(
        '<div class="setting-row"><span><strong>' + html.escape(item["name"]) + '</strong><small>' +
        html.escape(item["detail"]) + '</small></span><strong>' +
        ("✅" if item["active"] else "⚠️") + '</strong></div>'
        for item in report["controls"]
    )
    gaps_html = "".join('<li>' + html.escape(gap) + '</li>' for gap in report["gaps"])
    options_html = "".join(
        '<option value="' + key + '"' + (' selected' if key == scenario else '') + '>' +
        html.escape(value["label"]) + '</option>'
        for key, value in SCENARIOS.items()
    )
    score = int(report["score"])
    score_color = "#35c98b" if score >= 80 else ("#e6b450" if score >= 50 else "#f16c6c")
    body = (
        '<section class="card"><h1>🧪 WolfMirror · Security Digital Twin</h1>'
        '<p class="muted">Teste die Reaktion deines Servers, bevor ein echter Vorfall passiert. '
        'Website und Discord-Bot verwenden denselben Simulationskern und die gespeicherten Servereinstellungen.</p>'
        '<form method="post">' + _csrf_field() +
        '<label class="card">Szenario auswählen<select name="scenario">' + options_html + '</select></label>'
        '<button type="submit">🧪 Dry-Run simulieren</button></form>'
        '<p class="muted">Simulationen verändern weder Konfiguration noch Mitglieder, Nachrichten oder Serverobjekte.</p></section>'
        '<section class="card"><h2>' + html.escape(report["label"]) + '</h2><p>' + html.escape(report["summary"]) + '</p>'
        '<div style="display:flex;align-items:center;gap:18px;flex-wrap:wrap;margin:18px 0">'
        '<div style="width:112px;height:112px;border-radius:50%;display:grid;place-items:center;border:8px solid ' + score_color + ';font-size:25px;font-weight:800">' + str(score) + '<small style="font-size:12px">/100</small></div>'
        '<div><h3>' + html.escape(report["verdict"]) + '</h3><p class="muted">' +
        str(report["metrics"]["member_count"]) + ' Mitglieder · ' + str(report["metrics"]["channel_count"]) + ' Kanäle</p></div></div>'
        + controls_html +
        ('<h3>Verbesserungspotenzial</h3><ul>' + gaps_html + '</ul>' if gaps_html else '<p>✅ Keine Lücken in den geprüften Signalen gefunden.</p>') +
        '<p class="muted">🧪 ' + html.escape(report["disclaimer"]) + '</p></section>'
        '<section class="card"><h2>Warum WolfMirror anders ist</h2>'
        '<p>Ein kontrollierter Sicherheits-Tabletop statt blindem Auto-Ban: Du siehst vorab, welche Regeln aktiv sind und welche Bot-Berechtigungen fehlen. '
        'Der Score ist eine erklärbare Checkliste, keine wissenschaftliche Angriffswahrscheinlichkeit.</p>'
        '<p><a href="/guild/' + str(guild.id) + '/wolfshield">WolfShield konfigurieren ↗</a> · '
        '<a href="/guild/' + str(guild.id) + '/wolfpulse">WolfPulse prüfen ↗</a></p></section>'
    )
    return _page("WolfMirror · Security Digital Twin", body)
