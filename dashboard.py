"""IronWolf Control Center: Discord-OAuth protected web dashboard.

The dashboard only edits allow-listed server settings and command toggles.
New module files are generated as downloadable templates and are never executed
automatically by the running bot.
"""
import hmac
import html
import os
import secrets
import threading
from pathlib import Path
from urllib.parse import urlencode

import discord
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
        '<nav><a href="/">🐺 IronWolf Control Center</a><span></span>'
        + ('<a href="/logout">Abmelden</a>' if logged_in else '<a href="/login">Mit Discord anmelden</a>')
        + '</nav>'
    )
    return f"""<!doctype html>
<html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)} · IronWolf</title>
<style>
:root{{--bg:#10131b;--panel:#1a2030;--text:#f4f6fb;--muted:#aeb8ca;--accent:#8b9dff;--border:#30394d;}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:16px/1.5 system-ui,sans-serif}}
nav{{display:flex;gap:20px;align-items:center;padding:16px max(20px,calc((100vw - 1100px)/2));border-bottom:1px solid var(--border);background:#141925}}
nav span{{flex:1}}a{{color:var(--accent)}}main{{max-width:1100px;margin:28px auto;padding:0 18px}}
h1,h2{{line-height:1.2}}h1{{font-size:clamp(26px,4vw,38px)}}h2{{font-size:21px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px}}
.card{{background:var(--panel);border:1px solid var(--border);border-radius:14px;padding:18px;margin:14px 0}}
label{{display:block;font-weight:650;margin:12px 0 5px}}input[type=text],input[type=password],select,textarea{{width:100%;padding:10px 12px;border:1px solid var(--border);border-radius:8px;background:#0e121b;color:var(--text)}}
textarea{{min-height:95px;resize:vertical}}button,.button{{display:inline-block;border:0;border-radius:8px;padding:10px 14px;background:var(--accent);color:#10131b;font-weight:750;cursor:pointer;text-decoration:none;margin-top:12px}}
button.secondary,.button.secondary{{background:#30394d;color:var(--text)}}.muted{{color:var(--muted)}}.pill{{display:inline-block;padding:3px 8px;border-radius:20px;background:#30394d;font-size:13px}}
.row{{display:flex;gap:12px;align-items:center;justify-content:space-between;border-bottom:1px solid var(--border);padding:10px 0}}.row:last-child{{border:0}}
.check{{display:flex;gap:10px;align-items:center}}.check label{{margin:0;font-weight:500}}.check input{{width:18px;height:18px}}
.notice{{padding:12px;border-radius:8px;background:#252d40;margin:14px 0}}code{{overflow-wrap:anywhere}}small{{color:var(--muted)}}
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
        records.append({"key": key, "name": "!" + command.name, "kind": "Prefix", "enabled": key not in disabled, "cog": command.cog_name or "Allgemein"})
    for command in _bot.tree.walk_commands():
        if isinstance(command, app_commands.Group):
            continue
        key = f"slash:/{command.qualified_name}"
        if key in seen:
            continue
        seen.add(key)
        records.append({"key": key, "name": "/" + command.qualified_name, "kind": "Slash", "enabled": key not in disabled, "cog": getattr(command, "binding", None).__class__.__name__ if getattr(command, "binding", None) else "Allgemein"})
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
        body = """<section class="card"><h1>🐺 IronWolf Control Center</h1>
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
        cards.append(f'<a class="card" style="display:block;text-decoration:none" href="/guild/{gid}"><h2>{html.escape(item.get("name","Server"))}</h2><span class="pill">Bot verbunden</span><p class="muted">Einstellungen, Befehle und Module öffnen →</p></a>')
    body = '<h1>Deine IronWolf-Server</h1><p class="muted">Es werden nur Server angezeigt, auf denen du verwalten darfst und IronWolf verbunden ist.</p>'
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
    settings_html = '<section class="card"><h2>⚙️ Server-Einstellungen</h2><form method="post">' + _csrf_field() + '<input type="hidden" name="action" value="settings">'
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
    command_html = '<section class="card"><h2>🧩 Befehle aktivieren/deaktivieren</h2><p class="muted">Deaktivierte Befehle werden serverbezogen blockiert. Der Bot-Code wird dabei nicht verändert.</p><form method="post">' + _csrf_field() + '<input type="hidden" name="action" value="commands">'
    current_cog = None
    for item in commands:
        if item["cog"] != current_cog:
            current_cog = item["cog"]
            command_html += f'<h3>{html.escape(current_cog)}</h3>'
        checked = " checked" if item["enabled"] else ""
        command_html += f'<div class="row"><div><strong>{html.escape(item["name"])}</strong><br><small>{html.escape(item["kind"])}</small></div><div class="check"><input type="checkbox" id="cmd-{html.escape(item["key"])}" name="cmd:{html.escape(item["key"])}"{checked}><label for="cmd-{html.escape(item["key"])}">Aktiv</label></div></div>'
    command_html += '<button type="submit">Befehle speichern</button></form></section>'
    module_records = _module_records(guild.id)
    modules_html = '<section class="card"><h2>🧩 Module aktivieren/deaktivieren</h2><p class="muted">Gilt nur für diesen Server. Ein deaktiviertes Modul blockiert seine Cog-Befehle; andere Server bleiben unverändert.</p><form method="post">' + _csrf_field() + '<input type="hidden" name="action" value="modules">'
    for item in module_records:
        checked = " checked" if item["enabled"] else ""
        modules_html += f'<div class="row"><strong>{html.escape(item["name"])}</strong><div class="check"><input type="checkbox" id="module-{html.escape(item["name"])}" name="module:{html.escape(item["name"])}"{checked}><label for="module-{html.escape(item["name"])}">Aktiv</label></div></div>'
    if not module_records:
        modules_html += '<p class="muted">Aktuell sind keine Cog-Module registriert.</p>'
    modules_html += '<button type="submit">Module speichern</button></form></section>'
    module_form = '<section class="card"><h2>🛠️ Modul-Vorlage erstellen</h2><p class="muted">Erstellt eine herunterladbare, sichere discord.py-Cog-Vorlage. Die Datei wird nicht automatisch geladen.</p><form method="post" action="/create-module">' + _csrf_field() + f'<input type="hidden" name="guild_id" value="{gid}"><label for="module_name">Modulname</label><input id="module_name" name="module_name" type="text" required maxlength="40" pattern="[A-Za-z][A-Za-z0-9_]*" placeholder="mein_modul"><label for="description">Beschreibung</label><input id="description" name="description" type="text" maxlength="120" placeholder="Was macht das Modul?"><label for="command_name">Beispiel-Befehl</label><input id="command_name" name="command_name" type="text" maxlength="32" pattern="[a-z][a-z0-9-]*" value="mein-modul" required><button type="submit">Vorlage erstellen</button></form></section>'
    body = f'<h1>🐺 {html.escape(guild.name)}</h1><p class="muted">Server-ID: {guild.id}</p><div class="grid"><div>{settings_html}</div><div>{modules_html}{command_html}{module_form}</div></div>'
    return _render(guild.name + " · Control Center", body)


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
