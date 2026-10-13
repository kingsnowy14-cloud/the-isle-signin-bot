"""IronWolf Control Center: Discord-OAuth protected web dashboard.

The dashboard only edits allow-listed server settings and command toggles.
New module files are generated as downloadable templates and are never executed
automatically by the running bot.
"""
import hmac
import asyncio
import html
import os
import secrets
import threading
from pathlib import Path
from urllib.parse import urlencode

import discord
from discord import app_commands
import requests
from flask import Flask, abort, redirect, request, session, url_for, make_response
from storage import data_path

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
    MAX_CONTENT_LENGTH=32 * 1024,
)

_bot = None
_config = None
_save_json = None
_guild_config = None
_oauth_state = None

TEXT_SETTINGS = {
    "welcome_message": {
        "label": "Willkommensnachricht",
        "help": "Platzhalter: {mention}, {server}, {count}",
        "max_length": 1000,
    },
}
BOOL_SETTINGS = {
    "welcome_bots_enabled": "Willkommensnachrichten für Bots",
    "automod_enabled": "AutoMod",
    "anti_nuke_enabled": "Anti-Nuke-Schutz",
    "anti_raid_enabled": "Anti-Raid-Schutz",
}
CHANNEL_SETTINGS = {
    "welcome_channel": "Willkommenskanal",
    "security_log_channel": "Sicherheits-Logkanal",
    "honeypot_channel": "Honeypot-Kanal",
    "verify_channel": "Verifizierungskanal",
    "registration_channel": "Anmeldekanal",
    "ticket_log_channel": "Ticket-Logkanal",
}
ROLE_SETTINGS = {
    "support_role": "Support-Rolle",
    "verified_role": "Verifizierte Rolle",
    "unverified_role": "Unverifizierte Rolle",
    "trusted_security_role": "Vertrauenswürdige Sicherheitsrolle",
}


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
    nav = (
        '<nav><a href="/">🐺 IronWolf <span class="muted">/ Control Center</span></a><span></span>'
        + ('<a href="/logout">Abmelden</a>' if logged_in else '<a href="/login">Mit Discord anmelden</a>')
        + '</nav>'
    )
    return f"""<!doctype html>
<html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)} · IronWolf</title>
<style>
:root{{--bg:#090c12;--panel:#111823;--panel2:#151f2c;--text:#edf3fa;--muted:#91a0b3;--accent:#d6a64f;--accent2:#8da8c2;--border:#293646;--danger:#c65d5d;--shadow:0 18px 46px rgba(0,0,0,.28);}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth;scroll-padding-top:92px}}
body{{margin:0;background:radial-gradient(ellipse at 10% -12%,rgba(214,166,79,.13),transparent 34%),radial-gradient(ellipse at 100% 12%,rgba(91,125,158,.14),transparent 32%),linear-gradient(180deg,#0b1018 0%,var(--bg) 48%,#080b10 100%);color:var(--text);font:15px/1.65 Inter,ui-sans-serif,system-ui,-apple-system,sans-serif;min-height:100vh}}
nav{{position:sticky;top:0;z-index:5;display:flex;gap:18px;align-items:center;padding:13px max(20px,calc((100vw - 1240px)/2));border-bottom:1px solid rgba(214,166,79,.22);background:rgba(9,12,18,.9);backdrop-filter:blur(18px)}}
nav a:first-child{{font-weight:900;font-size:18px;letter-spacing:.2px;text-decoration:none;color:var(--text)}}nav a:first-child::first-letter{{color:var(--accent)}}nav span{{flex:1}}nav a:last-child{{padding:7px 13px;border:1px solid var(--border);border-radius:10px;text-decoration:none;font-weight:700;background:rgba(255,255,255,.025)}}nav a:last-child:hover{{border-color:var(--accent);color:var(--accent)}}a{{color:var(--accent)}}
main{{max-width:1240px;margin:34px auto;padding:0 24px 44px}}main>p:last-child{{border-top:1px solid var(--border);padding-top:18px;margin-top:34px}}h1,h2,h3{{line-height:1.22;letter-spacing:-.35px}}h1{{font-size:clamp(28px,4vw,42px);margin:0 0 10px;font-weight:900;letter-spacing:-.8px}}h2{{font-size:20px;margin:0 0 12px;font-weight:800}}h3{{font-size:15px;margin:22px 0 8px;color:var(--accent);text-transform:uppercase;letter-spacing:.8px}}main>p.muted{{margin-top:0;margin-bottom:24px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,340px),1fr));gap:20px;align-items:start}}.grid>.card,.grid>div>.card{{margin:0 0 18px}}
.card{{background:linear-gradient(145deg,rgba(20,29,42,.97),rgba(14,20,30,.98));border:1px solid var(--border);border-radius:16px;padding:23px;margin:18px 0;box-shadow:var(--shadow);min-width:0;position:relative;overflow:hidden}}.card::before{{content:"";position:absolute;inset:0 0 auto;height:2px;background:linear-gradient(90deg,var(--accent),rgba(141,168,194,.2),transparent);opacity:.8}}.card h1{{margin-bottom:14px}}.card p{{color:var(--muted)}}.card ul{{padding-left:22px;color:var(--muted)}}.card li{{padding:3px 0}}
.grid>a.card{{transition:transform .18s ease,border-color .18s ease,box-shadow .18s ease}}.grid>a.card:hover{{transform:translateY(-3px);border-color:rgba(214,166,79,.6);box-shadow:0 22px 55px rgba(0,0,0,.36)}}
label{{display:block;font-weight:750;margin:14px 0 6px;color:#dce5ef}}input[type=text],input[type=password],select,textarea{{width:100%;padding:11px 13px;border:1px solid #354457;border-radius:10px;background:#0a1018;color:var(--text);font:inherit;outline:none;transition:border-color .15s,box-shadow .15s}}input:focus,select:focus,textarea:focus{{border-color:var(--accent);box-shadow:0 0 0 3px rgba(214,166,79,.13)}}textarea{{min-height:100px;resize:vertical}}input::placeholder,textarea::placeholder{{color:#718096}}
button,.button{{display:inline-flex;align-items:center;justify-content:center;gap:8px;border:1px solid transparent;border-radius:10px;padding:10px 15px;background:linear-gradient(120deg,#e0b65e,#c9953c);color:#17130b;font:inherit;font-weight:850;cursor:pointer;text-decoration:none;margin-top:14px;transition:transform .15s,filter .15s}}button:hover,.button:hover{{transform:translateY(-1px);filter:brightness(1.07)}}button.secondary,.button.secondary{{background:#202c3b;border-color:#3a4a5e;color:var(--text)}}button.secondary:hover,.button.secondary:hover{{border-color:var(--accent)}}button:focus-visible,.button:focus-visible,a:focus-visible{{outline:2px solid var(--accent);outline-offset:3px}}
.muted,small{{color:var(--muted)}}.pill{{display:inline-flex;align-items:center;gap:6px;padding:4px 10px;border-radius:999px;background:rgba(214,166,79,.1);border:1px solid rgba(214,166,79,.3);color:#e8c477;font-size:11px;font-weight:850;letter-spacing:1px}}
.row{{display:flex;gap:14px;align-items:center;justify-content:space-between;border-bottom:1px solid rgba(53,68,87,.65);padding:12px 0}}.row:last-child{{border:0}}.row strong{{overflow-wrap:anywhere}}.check{{display:flex;gap:9px;align-items:center;flex-shrink:0}}.check label{{margin:0;font-weight:650;color:var(--muted)}}.check input{{width:18px;height:18px;accent-color:var(--accent);cursor:pointer}}
.notice{{padding:15px 17px;border-radius:12px;background:rgba(141,168,194,.08);border:1px solid rgba(141,168,194,.23);margin:18px 0;color:#dbe5ef}}code{{overflow-wrap:anywhere;background:#0a1018;border:1px solid var(--border);border-radius:6px;padding:2px 5px;font-size:.92em}}form{{margin-top:10px}}form button{{width:auto}}.card form>button{{margin-top:18px}}select{{cursor:pointer}}
.section-nav{{display:flex;flex-wrap:wrap;gap:8px;margin:18px 0 22px;padding:10px;background:rgba(17,24,35,.72);border:1px solid var(--border);border-radius:13px}}.section-nav a{{display:inline-flex;padding:7px 11px;border-radius:8px;text-decoration:none;font-size:13px;font-weight:750;background:rgba(255,255,255,.025);border:1px solid transparent}}.section-nav a:hover{{border-color:rgba(214,166,79,.45);background:rgba(214,166,79,.07)}}section[id]{{scroll-margin-top:90px}}
@media(max-width:650px){{nav{{padding:12px 15px}}nav a:first-child{{font-size:15px}}main{{margin:24px auto;padding:0 14px 30px}}.card{{padding:18px;border-radius:13px}}.row{{align-items:flex-start}}.section-nav{{gap:6px}}.section-nav a{{font-size:12px;padding:7px 9px}}button,.button{{max-width:100%}}}}
.command-groups{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,280px),1fr));gap:12px;align-items:start}}.command-group{{border:1px solid var(--border);background:rgba(8,13,20,.42);border-radius:12px;padding:12px;min-width:0}}.command-group h3{{margin:2px 0 8px;padding-bottom:9px;border-bottom:1px solid var(--border)}}.command-group .row{{gap:8px;padding:9px 0}}.command-group .row strong{{font-size:13px;overflow-wrap:anywhere}}.command-group .check label{{font-size:12px}}@media(prefers-reduced-motion:reduce){{*,*::before,*::after{{scroll-behavior:auto!important;transition:none!important}}}}
</style></head><body>{nav}<main>{body}<p class="muted"><small>IronWolf Control Center · Änderungen gelten serverbezogen. Neue Python-Module werden nicht automatisch ausgeführt.</small></p></main></body></html>"""


def _render(title, body, status=200):
    return make_response(_page(title, body), status)


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
    guilds = _api_get("/users/@me/guilds")
    return guilds if isinstance(guilds, list) else []


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
        abort(403)
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
    for command in _bot.commands:
        if command.hidden or command.name == "help":
            continue
        key = f"prefix:!{command.name}"
        if key in seen:
            continue
        seen.add(key)
        cog = command.cog
        module_name = cog.__class__.__name__ if cog is not None else None
        records.append({"key": key, "name": "!" + command.name, "kind": "Prefix", "enabled": key not in disabled and module_name not in disabled_modules, "cog": command.cog_name or "Allgemein"})
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


def _guild_channels(guild):
    return [(channel.id, channel.name) for channel in guild.text_channels]


def _guild_roles(guild):
    return [(role.id, role.name) for role in guild.roles if not role.is_default() and not role.managed]


@app.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'"
    return response


@app.route("/")
def index():
    login = _require_login()
    if login:
        body = """<section class="card hero"><span class="pill">CONTROL CENTER</span><h1 style="margin-top:16px">Dein Server. Deine Regeln.</h1>
<p>Verwalte deine IronWolf-Server an einem Ort: Einstellungen, Befehle und Modulvorlagen.</p>
<div class="notice">🔐 Die Anmeldung erfolgt über Discord. Du kannst nur Server verwalten, auf denen du „Server verwalten“ oder Administrator-Rechte besitzt und auf denen der Bot online ist.</div>
<a class="button" href="/login">Mit Discord anmelden</a>
<h2>Was du hier machen kannst</h2><ul><li>Serverbezogene Einstellungen mit Formularen ändern</li><li>Prefix- und Slash-Befehle einzeln aktivieren/deaktivieren</li><li>Neue Python-Modulvorlagen erstellen und herunterladen</li></ul></section>"""
        if not _oauth_ready():
            body += '<div class="card"><h2>Einmalige Einrichtung nötig</h2><p>Setze in Railway die Variablen <code>DISCORD_CLIENT_ID</code>, <code>DISCORD_CLIENT_SECRET</code>, <code>DASHBOARD_REDIRECT_URI</code> und <code>DASHBOARD_SECRET_KEY</code>. Als Redirect URI muss exakt die öffentliche URL plus <code>/callback</code> in der Discord Developer Portal OAuth2-Einstellung stehen.</p></div>'
        return _render("Control Center", body)
    try:
        guilds = _user_guilds()
    except (requests.RequestException, ValueError, KeyError):
        session.clear()
        return _render("Discord-Anmeldung", '<div class="card"><h1>Anmeldung abgelaufen</h1><p>Bitte melde dich erneut an.</p><a class="button" href="/login">Erneut anmelden</a></div>', 401)
    cards = []
    for item in guilds:
        if not (int(item.get("permissions", "0")) & (MANAGE_GUILD | ADMINISTRATOR)):
            continue
        gid = str(item.get("id"))
        bot_guild = _bot.get_guild(int(gid)) if _bot else None
        if not bot_guild:
            continue
        cards.append(f'<a class="card server-card" style="display:block;text-decoration:none" href="/guild/{gid}"><span class="pill">● BOT VERBUNDEN</span><h2 style="margin-top:14px">{html.escape(item.get("name","Server"))}</h2><p class="muted">Einstellungen, Befehle und Module verwalten</p><strong>Server öffnen ↗</strong></a>')
    body = '<span class="pill">ÜBERSICHT</span><h1 style="margin-top:14px">Deine IronWolf-Server</h1><p class="muted">Wähle einen Server, um Einstellungen, Befehle und Module an einem Ort zu verwalten.</p>'
    body += '<div class="grid">' + ("".join(cards) or '<div class="card">Keine passenden Server gefunden. Prüfe deine Discord-Rechte und ob IronWolf auf dem Server ist.</div>') + '</div>'
    return _render("Deine Server", body)


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


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


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
            return redirect(url_for("guild_page", guild_id=gid))
        if action == "settings":
            for key, meta in TEXT_SETTINGS.items():
                if key in request.form:
                    value = request.form.get(key, "").strip()
                    if len(value) > meta["max_length"]:
                        abort(400, f"{meta['label']} ist zu lang.")
                    cfg[key] = value
            for key in BOOL_SETTINGS:
                cfg[key] = request.form.get(key) == "on"
            channel_ids = {str(item.id) for item in guild.text_channels}
            role_ids = {str(item.id) for item in guild.roles}
            for key in CHANNEL_SETTINGS:
                raw = request.form.get(key, "").strip()
                if raw and raw not in channel_ids:
                    abort(400, "Ungültiger Kanal ausgewählt.")
                cfg[key] = int(raw) if raw else None
            for key in ROLE_SETTINGS:
                raw = request.form.get(key, "").strip()
                if raw and raw not in role_ids:
                    abort(400, "Ungültige Rolle ausgewählt.")
                cfg[key] = int(raw) if raw else None
            _save_json(data_path("config.json"), _config)
        elif action == "commands":
            disabled = set(cfg.get("disabled_commands", []))
            valid_keys = {item["key"] for item in _command_records(guild.id)}
            for key in valid_keys:
                if request.form.get("cmd:" + key) == "on":
                    disabled.discard(key)
                else:
                    disabled.add(key)
            cfg["disabled_commands"] = sorted(disabled)
            _save_json(data_path("config.json"), _config)
        else:
            abort(400, "Unbekannte Aktion.")
        return redirect(url_for("guild_page", guild_id=gid))
    channels = _guild_channels(guild)
    roles = _guild_roles(guild)
    def options(items, current):
        out = ['<option value="">— Nicht festgelegt —</option>']
        for item_id, name in items:
            selected = " selected" if str(item_id) == str(current or "") else ""
            out.append(f'<option value="{item_id}"{selected}>{html.escape(name)}</option>')
        return "".join(out)
    settings_html = '<section class="card" id="settings"><h2>⚙️ Server-Einstellungen</h2><form method="post">' + _csrf_field() + '<input type="hidden" name="action" value="settings">'
    for key, label in TEXT_SETTINGS.items():
        settings_html += f'<label for="{key}">{label["label"]}</label><small>{label["help"]}</small><textarea id="{key}" name="{key}" maxlength="{label["max_length"]}">{html.escape(str(cfg.get(key, "")))}</textarea>'
    for key, label in BOOL_SETTINGS.items():
        checked = " checked" if cfg.get(key, False) else ""
        settings_html += f'<div class="check"><input type="checkbox" id="{key}" name="{key}"{checked}><label for="{key}">{html.escape(label)}</label></div>'
    for key, label in CHANNEL_SETTINGS.items():
        settings_html += f'<label for="{key}">{html.escape(label)}</label><select id="{key}" name="{key}">{options(channels, cfg.get(key))}</select>'
    for key, label in ROLE_SETTINGS.items():
        settings_html += f'<label for="{key}">{html.escape(label)}</label><select id="{key}" name="{key}">{options(roles, cfg.get(key))}</select>'
    settings_html += '<button type="submit">Einstellungen speichern</button></form></section>'
    commands = _command_records(guild.id)
    command_html = '<section class="card" id="commands"><h2>⌘ Befehle</h2><p class="muted">Nach Modul gruppiert. Änderungen gelten nur auf diesem Server.</p><form method="post">' + _csrf_field() + '<input type="hidden" name="action" value="commands"><div class="command-groups">'
    current_cog = None
    group_open = False
    for item in commands:
        if item["cog"] != current_cog:
            if group_open:
                command_html += '</div>'
            current_cog = item["cog"]
            command_html += '<div class="command-group"><h3>' + html.escape(current_cog) + '</h3>'
            group_open = True
        checked = " checked" if item["enabled"] else ""
        command_html += f'<div class="row"><div><strong>{html.escape(item["name"])}</strong><br><small>{html.escape(item["kind"])}</small></div><div class="check"><input type="checkbox" id="cmd-{html.escape(item["key"])}" name="cmd:{html.escape(item["key"])}"{checked}><label for="cmd-{html.escape(item["key"])}">An</label></div></div>'
    if group_open:
        command_html += '</div>'
    command_html += '</div><button type="submit">Befehle speichern</button></form></section>'

    module_records = _module_records(guild.id)
    modules_html = '<section class="card" id="modules"><h2>🧩 Module aktivieren/deaktivieren</h2><p class="muted">Gilt nur für diesen Server. Ein deaktiviertes Modul blockiert seine Cog-Befehle; andere Server bleiben unverändert.</p><form method="post">' + _csrf_field() + '<input type="hidden" name="action" value="modules">'
    for item in module_records:
        checked = " checked" if item["enabled"] else ""
        modules_html += f'<div class="row"><strong>{html.escape(item["name"])}</strong><div class="check"><input type="checkbox" id="module-{html.escape(item["name"])}" name="module:{html.escape(item["name"])}"{checked}><label for="module-{html.escape(item["name"])}">Aktiv</label></div></div>'
    if not module_records:
        modules_html += '<p class="muted">Aktuell sind keine Cog-Module registriert.</p>'
    modules_html += '<button type="submit">Module speichern</button></form></section>'
    module_form = '<section class="card" id="module-template"><h2>🛠️ Modul-Vorlage erstellen</h2><p class="muted">Erstellt eine herunterladbare, sichere discord.py-Cog-Vorlage. Die Datei wird nicht automatisch geladen.</p><form method="post" action="/create-module">' + _csrf_field() + f'<input type="hidden" name="guild_id" value="{gid}"><label for="module_name">Modulname</label><input id="module_name" name="module_name" type="text" required maxlength="40" pattern="[A-Za-z][A-Za-z0-9_]*" placeholder="mein_modul"><label for="description">Beschreibung</label><input id="description" name="description" type="text" maxlength="120" placeholder="Was macht das Modul?"><label for="command_name">Beispiel-Befehl</label><input id="command_name" name="command_name" type="text" maxlength="32" pattern="[a-z][a-z0-9-]*" value="mein-modul" required><button type="submit">Vorlage erstellen</button></form></section>'
    body = f'<span class="pill">IRONWOLF · SERVER CONTROL</span><h1 style="margin-top:12px">🐺 {html.escape(guild.name)}</h1><p class="muted">Server-ID: {guild.id} · Verwalte Einstellungen, Module und Befehle zentral.</p><div class="notice"><strong>⚡ Schnellzugriff</strong><p style="margin:4px 0 10px">Aktionen direkt auf Discord ausführen oder gezielt zu einem Verwaltungsbereich springen.</p><a class="button" href="/guild/{gid}/actions">Aktionszentrale öffnen →</a></div><div class="section-nav"><a href="#settings">⚙️ Einstellungen</a><a href="#modules">🧩 Module</a><a href="#commands">⌘ Befehle</a><a href="#module-template">＋ Modul-Vorlage</a></div><div class="grid"><div>{settings_html}</div><div>{modules_html}{command_html}{module_form}</div></div>'
    return _render(guild.name + " · Control Center", body)


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
        abort(400, "Der Modulname darf nur Buchstaben, Zahlen und Unterstriche enthalten und muss mit einem Buchstaben beginnen.")
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
    app.secret_key = _env("DASHBOARD_SECRET_KEY") or app.secret_key
    if not _env("DASHBOARD_SECRET_KEY"):
        print("[DASHBOARD] WARNING: DASHBOARD_SECRET_KEY is not set; sessions will reset on restart.", flush=True)
    port = int(os.getenv("PORT", "8080"))
    thread = threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False, threaded=True),
        name="ironwolf-dashboard",
        daemon=True,
    )
    thread.start()
    bot._dashboard_started = True
    print(f"[DASHBOARD] Listening on 0.0.0.0:{port}", flush=True)
